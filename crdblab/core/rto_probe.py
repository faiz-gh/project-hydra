"""High-frequency availability probe: how long could the database not serve a write?

The RPO audit writer issues one write at a time, so its resolution is bounded
by the quorum write cost (~70 ms). This probe keeps several canary writes in
flight on separate connections, so the gap between observations is roughly the
write cost divided by the worker count (~21-29 ms from the client node).

Design points:

* It runs on its own threads, connections and table, and cannot fail the
  workload. Its extra write rate is reported as ``achieved_rate_per_s``.
* ``resolution_s`` is measured from the observed gaps, not taken from the
  configured 2 ms dispatch interval.
* A write that blocks through a failover and then commits is the measurement,
  so ``statement_timeout`` is generous (5 s).
* Every attempt uses a fresh sequence number and is never retried.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .recorder import PROBE_OUTCOMES, utcnow_us

#: Dispatch cadence. The achieved rate is lower and reported separately.
DEFAULT_INTERVAL_S = 0.002

#: Concurrent in-flight writes. More workers give finer resolution but add load
#: and connections; check ``resolution_s`` per run.
DEFAULT_WORKERS = 8

#: Server-side budget per canary write: a hang detector, not a latency budget.
DEFAULT_STATEMENT_TIMEOUT_MS = 5_000

#: A connection that cannot be made is itself an observation, so keep this short.
DEFAULT_CONNECT_TIMEOUT_S = 2.0

#: Dedicated table, so its outage cannot be confused with the workload's or audit's.
DEFAULT_TABLE = "rto_canary"

CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS {table} ("
    "seq_id INT8 PRIMARY KEY, "
    "written_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)


@dataclass(frozen=True)
class ProbeAttempt:
    """One canary write as the client saw it.

    Offsets are seconds from the caller-supplied epoch, shared with
    ``events.json`` and ``metrics.csv``'s ``wall_offset_s``.
    """

    seq_id: int
    dispatch_offset_s: float
    complete_offset_s: float
    outcome: str
    worker: int
    detail: str = ""
    ts_utc: str = ""

    @property
    def duration_ms(self) -> float:
        return (self.complete_offset_s - self.dispatch_offset_s) * 1000.0

    @property
    def served(self) -> bool:
        return self.outcome == "ok"

    def to_row(self) -> dict[str, Any]:
        """A row under :data:`crdblab.core.recorder.PROBE_COLUMNS`."""
        return {
            "ts_utc": self.ts_utc,
            "seq_id": self.seq_id,
            "dispatch_offset_s": round(self.dispatch_offset_s, 6),
            "complete_offset_s": round(self.complete_offset_s, 6),
            "duration_ms": round(self.duration_ms, 3),
            "outcome": self.outcome,
            "worker": self.worker,
            "detail": self.detail,
        }


def classify(exc: BaseException) -> tuple[str, str]:
    """Map a driver exception to a :data:`PROBE_OUTCOMES` member and a detail.

    ``timeout`` (statement accepted, never answered) is the outage signature and
    is kept apart from ``conn_error`` (connection lost or refused).
    """
    name = type(exc).__name__
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else name
    detail = f"{name}: {text}"[:200]
    lowered = f"{name} {text}".lower()
    if "timeout" in lowered or "canceling statement" in lowered:
        return "timeout", detail
    # Connection-level errors vs. a reachable database rejecting the statement
    # (a probe bug, not an outage).
    if "operational" in lowered or "interface" in lowered or "connection" in lowered:
        return "conn_error", detail
    return "refused", detail


class _EventLog:
    """Append-only JSON-lines log of connection lifecycle events.

    Flushed on every write so it survives a run killed mid-fault. Only the
    edges are logged (failures, connects, disconnects), not every success.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._fh: TextIO | None = None
        self._lock = threading.Lock()

    def open(self) -> None:
        if self._path is not None:
            self._fh = open(self._path, "a", buffering=1)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def write(self, event: str, offset_s: float, **fields: Any) -> None:
        if self._fh is None:
            return
        record = {
            "ts_utc": utcnow_us(),
            "offset_s": round(offset_s, 6),
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=str)
        with self._lock:
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()


class RtoProbe:
    """A pool of canary writers on a background path, used as a context manager.

    Worker exceptions become classified observations; anything that stops the
    probe entirely is stored in :attr:`error` instead of raised.
    """

    def __init__(
        self,
        dsn: str,
        *,
        table: str = DEFAULT_TABLE,
        interval_s: float = DEFAULT_INTERVAL_S,
        workers: int = DEFAULT_WORKERS,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        log_path: Path | None = None,
        epoch_monotonic: float | None = None,
        emit: TextIO | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("the probe needs at least one worker")
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.dsn = dsn
        self.table = table
        self.interval_s = float(interval_s)
        self.workers = int(workers)
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.connect_timeout_s = float(connect_timeout_s)
        self.epoch_monotonic = (
            time.monotonic() if epoch_monotonic is None else float(epoch_monotonic)
        )
        self.epoch_utc = utcnow_us()

        #: Optional stream that receives each attempt as a JSON line as it
        #: completes; this is how the remote agent reports back over SSH.
        self._emit = emit
        self._emit_lock = threading.Lock()

        self._log = _EventLog(log_path)
        self._stop = threading.Event()
        self._queue: queue.Queue[tuple[int, float]] = queue.Queue(maxsize=self.workers)
        #: One permit per worker, held for a whole attempt, so a job is only
        #: dispatched (and timestamped) when a worker is actually free.
        self._idle = threading.Semaphore(self.workers)
        self._threads: list[threading.Thread] = []
        self._seq_lock = threading.Lock()
        self._seq = 0
        self._results_lock = threading.Lock()

        #: Every attempt, in completion order.
        self.attempts: list[ProbeAttempt] = []
        #: Ticks that found no free worker.
        self.dispatch_saturation = 0
        #: Ticks skipped to keep dispatches spread out; see :meth:`_spacing`.
        self.ticks_spaced_out = 0
        self.ticks = 0
        #: Rolling window of served-write latencies, used to space dispatches.
        self._recent_latencies: deque[float] = deque(maxlen=32)
        self._median_latency: float | None = None
        #: A fatal, probe-wide failure. Not an outage; a broken probe.
        self.error: str | None = None

    def offset(self) -> float:
        return time.monotonic() - self.epoch_monotonic

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _record(self, attempt: ProbeAttempt) -> None:
        with self._results_lock:
            self.attempts.append(attempt)
        if self._emit is not None:
            # Serialised so concurrent workers never emit a torn line.
            line = json.dumps(attempt.to_row(), separators=(",", ":"))
            with self._emit_lock:
                self._emit.write(line + "\n")
                self._emit.flush()

    def _note_latency(self, seconds: float) -> None:
        """Fold a served write's latency into the dispatch-spacing estimate.

        Failed writes are excluded so fast refusals cannot make the probe fire
        harder at an unhealthy cluster.
        """
        with self._results_lock:
            self._recent_latencies.append(seconds)
            ordered = sorted(self._recent_latencies)
            self._median_latency = ordered[len(ordered) // 2]

    def _connect(self, worker: int):
        import psycopg

        conn = psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=self.connect_timeout_s
        )
        # Set per connection so it survives reconnects.
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{self.statement_timeout_ms}ms'")
        return conn

    def _worker(self, worker: int) -> None:
        conn = None
        connected_once = False
        while not self._stop.is_set():
            try:
                seq, dispatched = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            outcome, detail = "ok", ""
            try:
                if conn is None or conn.closed:
                    conn = self._connect(worker)
                    self._log.write(
                        "reconnect" if connected_once else "connect",
                        self.offset(),
                        worker=worker,
                    )
                    connected_once = True
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {self.table} (seq_id) VALUES (%s)", (seq,)
                    )
            except BaseException as exc:
                outcome, detail = classify(exc)
                self._log.write(
                    "attempt_failed",
                    self.offset(),
                    worker=worker,
                    seq_id=seq,
                    outcome=outcome,
                    detail=detail,
                    waited_ms=round((time.monotonic() - dispatched) * 1000.0, 3),
                )
                if conn is not None:
                    try:
                        conn.close()
                    except BaseException:
                        pass
                conn = None

            completed = time.monotonic()
            if outcome == "ok":
                self._note_latency(completed - dispatched)
            self._record(
                ProbeAttempt(
                    seq_id=seq,
                    dispatch_offset_s=dispatched - self.epoch_monotonic,
                    complete_offset_s=completed - self.epoch_monotonic,
                    outcome=outcome,
                    worker=worker,
                    detail=detail,
                    ts_utc=utcnow_us(),
                )
            )
            self._queue.task_done()
            self._idle.release()

        if conn is not None:
            try:
                conn.close()
            except BaseException:
                pass
            self._log.write("disconnect", self.offset(), worker=worker)

    def _spacing(self) -> float:
        """Minimum interval between dispatches: median write latency / workers.

        Without it the workers phase-lock: they all finish together, get
        re-dispatched together, and return in bursts with long blind gaps between.
        Until latency is known, ``interval_s`` alone applies.
        """
        latency = self._median_latency
        if latency is None:
            return self.interval_s
        return max(self.interval_s, latency / self.workers)

    def _dispatcher(self) -> None:
        """Tick on absolute deadlines (``epoch + n * interval``) so cadence cannot drift.

        A tick dispatches only if a worker is free and :meth:`_spacing` allows it.
        """
        tick = 0
        last_dispatch = 0.0
        while not self._stop.is_set():
            tick += 1
            deadline = self.epoch_monotonic + tick * self.interval_s
            delay = deadline - time.monotonic()
            if delay > 0:
                if self._stop.wait(delay):
                    return
            elif -delay > self.interval_s:
                # Behind by more than a tick: skip forward instead of bursting.
                tick = int((time.monotonic() - self.epoch_monotonic) / self.interval_s)
                continue
            self.ticks += 1
            now = time.monotonic()
            if now - last_dispatch < self._spacing():
                self.ticks_spaced_out += 1
                continue
            if not self._idle.acquire(blocking=False):
                self.dispatch_saturation += 1
                continue
            try:
                self._queue.put_nowait((self._next_seq(), now))
                last_dispatch = now
            except queue.Full:  # pragma: no cover - the semaphore bounds this
                self.dispatch_saturation += 1
                self._idle.release()

    def start(self) -> RtoProbe:
        self._log.open()
        self._log.write(
            "probe_start",
            0.0,
            epoch_utc=self.epoch_utc,
            table=self.table,
            interval_s=self.interval_s,
            workers=self.workers,
            statement_timeout_ms=self.statement_timeout_ms,
            connect_timeout_s=self.connect_timeout_s,
        )
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._worker, args=(index,), daemon=True,
                name=f"rto-probe-{index}",
            )
            thread.start()
            self._threads.append(thread)
        dispatcher = threading.Thread(
            target=self._dispatcher, daemon=True, name="rto-probe-dispatch"
        )
        dispatcher.start()
        self._threads.append(dispatcher)
        return self

    def stop(self, timeout_s: float = 15.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout_s)
        self._threads.clear()
        summary = self.summary()
        self._log.write("probe_stop", self.offset(), **summary)
        self._log.close()

    def __enter__(self) -> RtoProbe:
        try:
            return self.start()
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return self

    def __exit__(self, *exc) -> None:
        try:
            self.stop()
        except BaseException as stop_exc:
            self.error = self.error or f"{type(stop_exc).__name__}: {stop_exc}"

    def rows(self) -> Iterable[dict[str, Any]]:
        """Attempts as rows under :data:`PROBE_COLUMNS`, in completion order."""
        return (attempt.to_row() for attempt in self.attempts)

    def summary(self) -> dict[str, Any]:
        return summarise(
            self.attempts,
            ticks=self.ticks,
            saturated=self.dispatch_saturation,
            spaced_out=self.ticks_spaced_out,
            interval_s=self.interval_s,
            workers=self.workers,
        )

    def rto(
        self, fault_offset_s: float, observation_end_s: float | None = None
    ) -> dict[str, Any]:
        return measure_rto(self.attempts, fault_offset_s, observation_end_s)


# Analysis is at module scope so resilience.py can re-derive figures from a
# recorded CSV without a live probe.


def _when(offset_s: float) -> str:
    """Phrase an offset from the fault; the last write before an outage can
    slightly predate the fault."""
    if offset_s < -0.05:
        return f"{-offset_s:.1f}s before the fault was injected"
    if offset_s < 0.05:
        return "as the fault was injected"
    return f"{offset_s:.1f}s after the fault"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _quantile(values: list[float], q: float) -> float | None:
    """Nearest-rank quantile, so every value returned was actually observed."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * len(ordered) + 0.5)) - 1))
    return ordered[index]


def resolution_of(gaps: list[float]) -> float | None:
    """The interval an RTO may be quoted to: the 95th percentile of the gaps.

    The median is misleading for a bursty, bimodal gap distribution, and the
    maximum is a single scheduling accident. ``summarise`` reports both anyway.
    """
    return _quantile(gaps, 0.95)


def summarise(
    attempts: list[ProbeAttempt],
    *,
    ticks: int = 0,
    saturated: int = 0,
    spaced_out: int = 0,
    interval_s: float = DEFAULT_INTERVAL_S,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """What the probe achieved, as distinct from what it was configured to do."""
    served = sorted(a.complete_offset_s for a in served_attempts(attempts))
    gaps = [b - a for a, b in zip(served, served[1:])]
    resolution = resolution_of(gaps)
    outcomes: dict[str, int] = {name: 0 for name in PROBE_OUTCOMES}
    for attempt in attempts:
        outcomes[attempt.outcome] = outcomes.get(attempt.outcome, 0) + 1
    span = (
        max(a.complete_offset_s for a in attempts) - min(a.dispatch_offset_s for a in attempts)
        if attempts
        else 0.0
    )
    latencies = [a.duration_ms for a in served_attempts(attempts)]
    return {
        "attempts": len(attempts),
        "outcomes": outcomes,
        "dispatch_interval_s": interval_s,
        "workers": workers,
        "ticks": ticks,
        "dispatch_saturation": saturated,
        "dispatch_saturation_pct": round(100.0 * saturated / ticks, 2) if ticks else None,
        "ticks_spaced_out": spaced_out,
        "achieved_rate_per_s": round(len(attempts) / span, 2) if span > 0 else None,
        "served_rate_per_s": round(len(served) / span, 2) if span > 0 else None,
        "resolution_s": round(resolution, 6) if resolution else None,
        "gap_p50_s": round(_median(gaps), 6) if gaps else None,
        "gap_max_s": round(max(gaps), 6) if gaps else None,
        "median_write_ms": round(_median(latencies), 3) if latencies else None,
        "span_s": round(span, 3),
        "note": (
            "resolution_s is the 95th percentile of the gap between served writes "
            "-- the tail, not the median, because the tail is what the probe might "
            "have been waiting through when the database recovered. It is bounded "
            "by the cost of a write divided by the worker count, not by "
            "dispatch_interval_s. Compare it with gap_p50_s: if they differ by "
            "orders of magnitude the pool was completing in bursts and the "
            "effective sampling is the coarser of the two"
        ),
    }


def served_attempts(attempts: list[ProbeAttempt]) -> list[ProbeAttempt]:
    return [a for a in attempts if a.served]


def outage_windows(
    attempts: list[ProbeAttempt], min_gap_s: float = 0.0
) -> list[dict[str, float]]:
    """Intervals between consecutive served writes, longest first.

    Each window carries both edges: the true recovery lies somewhere inside it.
    """
    served = sorted(served_attempts(attempts), key=lambda a: a.complete_offset_s)
    windows = []
    for previous, current in zip(served, served[1:]):
        gap = current.complete_offset_s - previous.complete_offset_s
        if gap >= min_gap_s:
            windows.append(
                {
                    "from_s": round(previous.complete_offset_s, 6),
                    "to_s": round(current.complete_offset_s, 6),
                    "duration_s": round(gap, 6),
                    # An in-flight write dates the recovery more tightly.
                    "closed_by_in_flight_write": current.dispatch_offset_s
                    <= previous.complete_offset_s,
                }
            )
    windows.sort(key=lambda w: w["duration_s"], reverse=True)
    return windows


def tail_attribution(
    pre_gaps: list[float],
    post_gaps: list[float],
) -> dict[str, Any]:
    """Is the post-fault gap tail heavier than the pre-fault one, or just longer?

    The post-fault window is usually longer, so its maximum gap is larger by
    chance alone. This compares *rates* of gaps over the healthy 95th percentile
    instead, and returns the evidence as well as the verdict.
    """
    if len(pre_gaps) < 20 or not post_gaps:
        return {
            "testable": False,
            "detail": (
                f"only {len(pre_gaps)} pre-fault gap(s); too few to characterise "
                "the healthy tail, so an observed outage cannot be distinguished "
                "from it either way"
            ),
        }

    reference = _quantile(pre_gaps, 0.95) or 0.0
    pre_over = sum(1 for g in pre_gaps if g > reference)
    post_over = sum(1 for g in post_gaps if g > reference)
    pre_rate = pre_over / len(pre_gaps)
    post_rate = post_over / len(post_gaps)
    expected = pre_rate * len(post_gaps)
    ratio = (post_rate / pre_rate) if pre_rate > 0 else None

    return {
        "testable": True,
        "reference_s": round(reference, 6),
        "pre_fault_gaps": len(pre_gaps),
        "post_fault_gaps": len(post_gaps),
        "pre_fault_exceedances": pre_over,
        "post_fault_exceedances": post_over,
        "expected_post_fault_exceedances": round(expected, 1),
        "exceedance_rate_ratio": round(ratio, 2) if ratio is not None else None,
        # Deliberately loose: a tight bar would reject real events on a jittery link.
        "heavier_after_fault": bool(ratio is not None and ratio >= 1.5),
    }


#: How far (in sampling periods) the last observation may fall short of the run's
#: end before the probe counts as having stopped observing.
COVERAGE_SLACK_PERIODS = 20.0


def measure_rto(
    attempts: list[ProbeAttempt],
    fault_offset_s: float,
    observation_end_s: float | None = None,
) -> dict[str, Any]:
    """How long the database could not serve a write after the fault.

    A cluster keeps serving for a few seconds before it notices a lost member,
    so "fault to next served write" is wrong. Instead the outage is the largest
    post-fault gap in served writes that exceeds the noise floor (the longest
    gap that closed before the fault, plus one sampling period).

    Key results:

    ``rto_s``
        Fault to service restored, or ``None`` when no gap cleared the floor.
    ``outage``
        The gap itself, with both edges.
    ``detection_lag_s``
        Fault to the first blocked or failed attempt, reported separately.
    ``next_write_after_fault_s``
        Fault to the next served write, comparable with the audit log's figure.
    ``observed_outage_s``
        The gap between two probe observations, where link delay cancels out.

    An outage still open when the probe stopped is ``truncated``. If
    ``observation_end_s`` is given and the probe's last attempt falls well
    short of it, ``coverage_truncated`` is set: the probe stopped observing
    (e.g. blocked on a black-holed socket), so absence of an outage means nothing.
    """
    served = sorted(served_attempts(attempts), key=lambda a: a.complete_offset_s)
    gaps = [
        (a, b, b.complete_offset_s - a.complete_offset_s)
        for a, b in zip(served, served[1:])
    ]
    # Resolution comes from pre-fault gaps only, so a long outage cannot raise the
    # threshold that should detect it.
    healthy_gaps = [gap for _, b, gap in gaps if b.complete_offset_s < fault_offset_s]
    resolution = resolution_of(healthy_gaps) or resolution_of([g for _, _, g in gaps])

    failures_after = sorted(
        (a for a in attempts if not a.served and a.complete_offset_s >= fault_offset_s),
        key=lambda a: a.complete_offset_s,
    )
    detection_lag = (
        round(failures_after[0].complete_offset_s - fault_offset_s, 6)
        if failures_after
        else None
    )

    after_fault = [a for a in served if a.complete_offset_s >= fault_offset_s]
    next_after = (
        round(after_fault[0].complete_offset_s - fault_offset_s, 6)
        if after_fault
        else None
    )

    # Coverage is judged on the last attempt of any outcome, not the last success.
    last_offset = max((a.complete_offset_s for a in attempts), default=None)
    coverage_gap = (
        observation_end_s - last_offset
        if (observation_end_s is not None and last_offset is not None)
        else None
    )
    coverage_truncated = (
        coverage_gap is not None
        and coverage_gap > (resolution or 0.0) * COVERAGE_SLACK_PERIODS
    )

    base: dict[str, Any] = {
        "resolution_s": round(resolution, 6) if resolution else None,
        "detection_lag_s": detection_lag,
        "next_write_after_fault_s": next_after,
        "served_after_fault": len(after_fault),
        "served_before_fault": len(served) - len(after_fault),
        "last_observation_offset_s": (
            round(last_offset, 6) if last_offset is not None else None
        ),
        "observation_end_offset_s": observation_end_s,
        "coverage_gap_s": round(coverage_gap, 3) if coverage_gap is not None else None,
        "coverage_truncated": coverage_truncated if coverage_gap is not None else None,
    }

    if len(served) < 2:
        return {
            **base,
            "rto_s": None,
            "measurable": False,
            "outage": None,
            "truncated": False,
            "detail": (
                "fewer than two canary writes were served in the whole run, so "
                "there is no interval between observations to measure an outage "
                "against"
            ),
        }

    # Noise floor: longest gap that closed before the fault, plus one sampling
    # period so jitter cannot manufacture an outage.
    healthy = healthy_gaps
    period = resolution or 0.0
    if healthy:
        floor = max(healthy) + period
        floor_source = (
            "longest gap between served writes that closed before the fault, plus "
            "one median sampling period"
        )
    else:
        floor = 2 * period
        floor_source = (
            "twice the median gap over the whole run; no gap between served "
            "writes closed before the fault, so there is nothing to characterise "
            "the healthy cadence with"
        )
    base["noise_floor_s"] = round(floor, 6)
    base["noise_floor_source"] = floor_source

    # Take the largest qualifying gap, not the first: post-fault jitter often
    # clears the floor before the real outage begins.
    qualifying = [
        (a, b, gap)
        for a, b, gap in gaps
        if b.complete_offset_s >= fault_offset_s and gap > floor
    ]
    outage = max(qualifying, key=lambda t: t[2]) if qualifying else None

    # A gap still open at the end has no closing observation; check it separately.
    last_served = served[-1]
    last_attempt = max(attempts, key=lambda a: a.complete_offset_s)
    open_gap = last_attempt.complete_offset_s - last_served.complete_offset_s
    if outage is None and last_served.complete_offset_s >= fault_offset_s and open_gap > floor:
        return {
            **base,
            "rto_s": None,
            "measurable": False,
            "truncated": True,
            "outage": {
                "started_s": round(last_served.complete_offset_s, 6),
                "ended_s": None,
                "duration_s": None,
                "at_least_s": round(open_gap, 6),
            },
            "detail": (
                f"writes stopped being served {last_served.complete_offset_s:.3f}s "
                f"into the run and had not resumed {open_gap:.3f}s later when the "
                "probe stopped. The recovery, if any, happened outside the "
                "observation window and this is not a measurement of it"
            ),
        }

    if outage is None and coverage_truncated:
        # Distinct from `below_resolution`: here the instrument was absent.
        return {
            **base,
            "rto_s": None,
            "measurable": False,
            "outage": None,
            "truncated": False,
            "below_resolution": False,
            "quotable_value_s": None,
            "claim": (
                f"the probe stopped observing {last_offset:.1f}s into the run, "
                f"{coverage_gap:.1f}s before it ended; any outage after that "
                "point is UNMEASURED, not absent"
            ),
            "detail": (
                "no gap between served writes cleared the detection floor, but "
                "the probe's own observations stop well short of the end of the "
                "run, so that says nothing about the cluster. The usual cause is "
                "workers blocked on connections a partition black-holed: a "
                "server-side statement_timeout cannot arrive when packets "
                "cannot, so the attempts neither complete nor fail and the "
                "series simply ends. Check the outcome counts -- all `ok` with "
                "no timeouts or conn_errors is the signature"
            ),
        }

    if outage is None:
        return {
            **base,
            "rto_s": None,
            "measurable": True,
            "outage": None,
            "truncated": False,
            "below_resolution": True,
            "quotable_value_s": None,
            "claim": (
                "no interruption in served writes was detectable after the fault"
                + (
                    f"; any outage was shorter than the {floor * 1000:.0f} ms "
                    f"detection threshold (longest healthy gap plus one "
                    f"{(resolution or 0) * 1000:.0f} ms sampling period)"
                    if floor
                    else ""
                )
            ),
            "detail": (
                "every gap between served writes after the fault was within the "
                "range the probe saw while the system was healthy. That is a "
                "result -- the outage, if any, was shorter than this probe can "
                "resolve -- and not a recovery time of zero"
            ),
        }

    before, after, duration = outage
    base["qualifying_gaps_s"] = sorted(
        (round(g, 6) for _, _, g in qualifying), reverse=True
    )[:10]
    base["qualifying_gap_count"] = len(qualifying)
    rto = after.complete_offset_s - fault_offset_s
    # Fraction of the gap the closing write was in flight for: overlap of its
    # flight window with the gap (it may have been dispatched before the gap opened).
    overlap_start = max(after.dispatch_offset_s, before.complete_offset_s)
    overlap = max(0.0, after.complete_offset_s - overlap_start)
    attribution = tail_attribution(
        healthy_gaps,
        [gap for _, b, gap in gaps if b.complete_offset_s >= fault_offset_s],
    )
    # A gap that clears the floor but without a heavier tail is not offered as an RTO.
    attributable = attribution.get("heavier_after_fault", True)
    started_after = before.complete_offset_s - fault_offset_s
    in_flight_fraction = min(1.0, overlap / duration) if duration else 0.0
    return {
        **base,
        "rto_s": round(rto, 6),
        "rto_ms": round(rto * 1000.0, 3),
        "measurable": True,
        "truncated": False,
        "below_resolution": False,
        "outage": {
            "started_s": round(before.complete_offset_s, 6),
            "ended_s": round(after.complete_offset_s, 6),
            "duration_s": round(duration, 6),
            "started_after_fault_s": round(
                before.complete_offset_s - fault_offset_s, 6
            ),
        },
        # Both edges are probe observations, so link delay cancels (unlike `rto_s`).
        "observed_outage_s": round(duration, 6),
        "closed_by_in_flight_write": in_flight_fraction >= 0.5,
        "in_flight_fraction": round(in_flight_fraction, 4),
        "attribution": attribution,
        "fault_attributable": attributable,
        "quotable_value_s": round(rto, 6) if attributable else None,
        "claim": (
            (
                f"writes stopped being served {_when(started_after)} and resumed "
                f"{rto * 1000:.0f} ms after the fault, an observed outage of "
                f"{duration * 1000:.0f} ms"
                + (
                    f", measured at {resolution * 1000:.1f} ms resolution"
                    if resolution
                    else ""
                )
            )
            if attributable
            else (
                f"a {duration * 1000:.0f} ms gap in served writes occurred "
                f"{_when(started_after)}, but it is NOT distinguishable from this "
                "probe's own tail: gaps over the healthy 95th percentile occurred "
                f"{attribution.get('post_fault_exceedances')} times after the fault "
                f"against {attribution.get('expected_post_fault_exceedances')} "
                "expected from the pre-fault rate. Do not quote it as a recovery "
                "time -- the post-fault window is simply longer, so its maximum is "
                "larger for that reason alone"
            )
        ),
    }


def attempts_from_rows(rows: Iterable[dict[str, Any]]) -> list[ProbeAttempt]:
    """Rebuild attempts from a recorded ``rto_probe.csv`` for re-analysis."""
    out = []
    for row in rows:
        out.append(
            ProbeAttempt(
                seq_id=int(row["seq_id"]),
                dispatch_offset_s=float(row["dispatch_offset_s"]),
                complete_offset_s=float(row["complete_offset_s"]),
                outcome=str(row["outcome"]),
                worker=int(row["worker"]),
                detail=str(row.get("detail") or ""),
                ts_utc=str(row.get("ts_utc") or ""),
            )
        )
    return out


# Agent mode: launched on the client node by remote_probe.py. Attempts go to
# stdout as JSON lines; diagnostics go to stderr.

#: Key marking the agent's start/stop lines (vs. attempt rows).
AGENT_RESULT_KEY = "__agent__"


def _agent_main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python3 -m crdblab.core.rto_probe",
        description=(
            "Run the high-frequency RTO probe here and report attempts as JSON "
            "lines on stdout. Intended to be launched over SSH by "
            "crdblab.phases.p4_chaos, not by hand."
        ),
    )
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--statement-timeout-ms", type=int, default=DEFAULT_STATEMENT_TIMEOUT_MS
    )
    parser.add_argument(
        "--connect-timeout-s", type=float, default=DEFAULT_CONNECT_TIMEOUT_S
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        required=True,
        help=(
            "hard upper bound on the probe's life, so an abandoned agent "
            "cannot outlive the run"
        ),
    )
    args = parser.parse_args(argv)

    probe = RtoProbe(
        args.dsn,
        table=args.table,
        interval_s=args.interval_s,
        workers=args.workers,
        statement_timeout_ms=args.statement_timeout_ms,
        connect_timeout_s=args.connect_timeout_s,
        emit=sys.stdout,
    )
    # Announce the epoch first so the reader can rebase every later offset.
    sys.stdout.write(
        json.dumps(
            {
                AGENT_RESULT_KEY: "start",
                "epoch_utc": probe.epoch_utc,
                "table": probe.table,
                "workers": probe.workers,
                "interval_s": probe.interval_s,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()

    deadline = time.monotonic() + args.duration_s
    with probe:
        if probe.error is None:
            try:
                while time.monotonic() < deadline:
                    time.sleep(min(0.25, max(deadline - time.monotonic(), 0.0)))
            except KeyboardInterrupt:
                pass

    sys.stdout.write(
        json.dumps(
            {
                AGENT_RESULT_KEY: "stop",
                "epoch_utc": probe.epoch_utc,
                "error": probe.error,
                "summary": probe.summary(),
            },
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_agent_main())

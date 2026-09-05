"""High-frequency availability probe, for measuring RTO in milliseconds.

The question this exists to answer is narrow: **for how long was the database
unable to serve a write?** Nothing else in the harness answers it at a useful
resolution.

*Performance RTO* -- throughput back to a fraction of baseline, from
``metrics.csv`` -- is sampled once a second by the generator and is a statement
about the workload recovering, not about the database being available. It is also
undefined whenever the cluster settles into a new stable state below the
threshold, which on this topology it does whenever a member of the fast triangle
is down.

*Availability RTO* as measured by :class:`crdblab.phases.p4_chaos.AuditWriter` is
the right quantity, but that client exists to establish RPO and is paced for it:
one write at a time, at ``audit_interval_s``, on a single connection. Because a
committed write on this topology costs a quorum round trip -- 69-73 ms from the
gateway across the recorded Phase I matrices -- its *achieved* cadence is around fourteen attempts a second no matter
what the profile asks for, and its own docstring says so. An RTO derived from it
cannot be quoted below ~70 ms, and ``resilience.availability`` correctly refuses
to quote one that is.

This probe raises the resolution by making the attempts concurrent rather than by
making them faster, which is the only thing that can work: a single client cannot
observe a 70 ms round trip more often than every 70 ms. A small pool of workers
holds several writes in flight at once, so the interval between *observations* is
the write cost divided by the pool size rather than the write cost itself.

Four properties of the implementation are load-bearing.

**It is on a background path, and takes nothing from the workload's.** It runs in
this process, in its own threads, over its own connections, and touches its own
table. It never reads the generator's stream, is never read by it, and its
failures cannot fail a benchmark tier -- a probe that could would be a new way to
lose a sweep. Its cost is not free and is not pretended to be: it is a measured
number of extra writes per second against the same cluster, reported in
``summary()`` as ``achieved_rate_per_s`` so that it can be set against the
workload's own write rate and judged rather than assumed negligible.

**It writes from the workstation, and that has to be stated because it sets the
arithmetic.** Like the RPO audit writer, it connects directly rather than being
shipped to the gateway, and the workstation is 376 ms round trip from the gateway
(the measurement behind ``preflight.CONTROL_TIMEOUT_S``). Each canary write
therefore costs that link plus the ~70 ms quorum, near 450 ms, and *not* the
~70 ms a client on the gateway would pay. Three consequences, none of which are worked around silently:

* Resolution is the write cost over the worker count, and it is measured per
  run rather than asserted. Two 60 s runs against the live cluster on
  2026-09-05, with the median write at 369-375 ms: eight workers gave a p95 gap
  of 125 ms and a p50 of 47 ms (= 369/8, as the arithmetic predicts);
  twenty-four gave a p95 of 64 ms. The returns are sub-linear because the tail
  is jitter on the link rather than the pool being short of workers.
* The load the probe puts on the cluster is *lower* than a naive reading
  suggests, because concurrency here buys observations against a link rather than
  work against the database: eight workers is 18 writes a second, roughly 5% of
  the ~371 writes/s a Phase IV tier at C=100 issues, and twenty-four is 43/s or
  ~12%. Concurrency is therefore the cheap axis on this testbed and the interval
  is not.
* **The link, not the design, is what caps the resolution.** A client on the
  gateway would pay ~70 ms a write instead of ~370 ms, so the same eight workers
  would resolve to single-digit milliseconds. Running the probe from here is a
  deliberate choice -- it matches the RPO audit writer, keeps the log on the
  machine that will analyse it, and survives the node under test going away --
  but it means a run's resolution is a property of where you are sitting. That is
  why every figure is reported with the resolution that produced it.
* Every timestamp carries about half that round trip as a *systematic* offset:
  the write commits, and the client learns about it 188 ms later. The offset is
  the same on both edges of an outage, so it cancels in ``observed_outage_s`` --
  which is why that quantity is reported beside ``rto_s`` rather than being
  treated as a footnote. ``rto_s`` measures from a fault timestamped by a
  different mechanism and does not enjoy the cancellation.

**The dispatch cadence and the achieved cadence are different numbers, and both
are recorded.** The dispatcher ticks on absolute monotonic deadlines at
``interval_s`` -- 2 ms by default, and it does not accumulate drift because each
deadline is computed from the epoch rather than by adding to the last one. But a
tick only becomes an attempt if a worker is free, and with writes costing ~450 ms
from the workstation and eight workers, all but about one tick in fifty finds
none. Reporting the configured 2 ms as the
resolution of an RTO would be false precision of exactly the kind
``docs/defects.md`` is a catalogue of, so what :func:`RtoProbe.summary` reports as
``resolution_s`` is the *observed* median gap between completed observations, and
``dispatch_saturation`` says how often a tick was dropped.

**A blocked write is the measurement, not a failed one.** During a lease transfer
an ``INSERT`` does not fail; it waits, and then commits the moment the range is
served again. Its completion timestamp is therefore a direct observation of the
instant service resumed, accurate to the process that observed it rather than to
the polling interval. This is why ``statement_timeout`` defaults to five seconds
rather than to something tight: a short timeout would abort exactly the write
whose return would have timed the recovery, and replace a millisecond-accurate
edge with a poll at the timeout period. The probe wants writes in flight *through*
the outage.

**Every attempt takes a fresh sequence number, and none is ever retried.** This is
the same discipline as the RPO audit writer and is here for the same reason: the
legacy audit client retried a ``seq_id`` after a failure and livelocked against
its own duplicate key precisely when the interesting thing was happening. A
retried number would also silently make one observation look like several.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TextIO

from .recorder import PROBE_OUTCOMES, utcnow_us

#: Default dispatch cadence. Sub-5 ms as specified; see the module docstring for
#: why the achieved rate is lower and why both numbers are reported.
DEFAULT_INTERVAL_S = 0.002

#: Concurrent in-flight writes. The gap between observations is roughly the write
#: cost over the pool size, and from the workstation that cost is ~370 ms measured
#: (376 ms of link dominating a ~70 ms quorum).
#:
#: Measured against the live cluster rather than reasoned about, because the first
#: version of this constant was reasoned about and was wrong by a factor of two:
#:
#:     workers   p95 gap   p50 gap   writes/s   share of a C=100 tier's writes
#:           8    125 ms     47 ms       18.1                             ~5%
#:          24     64 ms     23 ms       43.2                            ~12%
#:
#: Eight rather than more because the returns are sub-linear while the cost is
#: linear -- tripling the pool bought less than double the resolution and more
#: than double the load -- and because every worker is a connection the gateway
#: holds open through a fault; a probe that contributes to the outage it is
#: measuring is worthless. Eight rather than fewer because 18 writes a second is
#: small enough to argue is not what moved the throughput series, and the argument
#: is checkable: ``achieved_rate_per_s`` sits in the same events.json as the run's
#: own throughput.
#:
#: Raise it when the outage being timed is short enough that 125 ms matters, and
#: read ``resolution_s`` from the run afterwards rather than assuming the table
#: above still holds -- it is a property of the link on the day.
DEFAULT_WORKERS = 8

#: Server-side budget for one canary write. Generous on purpose: an ``INSERT``
#: that blocks through a lease transfer and then commits is the most precise
#: observation of recovery available, and a tight timeout would abort it. It is a
#: hang detector, not a latency budget.
DEFAULT_STATEMENT_TIMEOUT_MS = 5_000

#: Budget for opening a connection. Short, because a connection that cannot be
#: made is itself an observation and the worker should record it and try again
#: rather than sit on it.
DEFAULT_CONNECT_TIMEOUT_S = 2.0

#: Table the canary rows go to. Dedicated: it must not share a range, a schema or
#: a lease with either the workload's ``usertable`` or the RPO audit's
#: ``rpo_audit``, or an outage of one would be indistinguishable from an outage of
#: the other.
DEFAULT_TABLE = "rto_canary"

CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS {table} ("
    "seq_id INT8 PRIMARY KEY, "
    "written_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)


@dataclass(frozen=True)
class ProbeAttempt:
    """One canary write, as the client saw it.

    Offsets are seconds on the harness's monotonic clock from the probe's epoch,
    which the caller supplies so that they share an origin with ``events.json``
    and with ``wall_offset_s`` in ``metrics.csv``. Comparing an offset here with
    one from another clock is the error ``wall_offset_s`` was added to prevent
    (D5), so the epoch is passed in rather than taken here.
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

    The classification is by exception *type name* rather than by matching the
    message, because messages are not part of psycopg's interface and change
    between releases, whereas the class hierarchy is documented. It is also
    deliberately coarse: the probe needs to know whether the database served the
    write, failed to answer, or answered with a rejection, and inventing finer
    categories here would mean asserting things about the failure that the client
    is not in a position to know.

    A ``timeout`` is separated from a general connection error because it is the
    signature of the outage this probe measures: during a lease transfer the
    statement is accepted and simply not answered. A connection that is refused
    outright is a different event -- the process is gone -- and conflating them
    would hide which of the two a run actually saw.
    """
    name = type(exc).__name__
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else name
    detail = f"{name}: {text}"[:200]
    lowered = f"{name} {text}".lower()
    if "timeout" in lowered or "canceling statement" in lowered:
        return "timeout", detail
    # psycopg raises OperationalError for connection-level trouble and
    # InterfaceError for a connection used after it broke. Everything else --
    # ProgrammingError, IntegrityError, DataError -- means a reachable database
    # rejected the statement, which is a fault in the probe rather than an outage.
    if "operational" in lowered or "interface" in lowered or "connection" in lowered:
        return "conn_error", detail
    return "refused", detail


class _EventLog:
    """Append-only JSON-lines log of connection lifecycle events.

    Flushed on every write. The point of this file is to survive the run: a chaos
    run that is interrupted while the fault is in place -- which happens, and is
    the case whose timings matter most -- leaves the CSV unwritten because that is
    assembled at the end, but leaves this complete up to the last event.

    Successful writes are *not* logged here. There are tens of thousands of them
    and they are in the CSV; what this file carries is the edges -- every failure,
    every connection opened or lost, and every first success after a failure --
    which is what a downtime calculation actually reads.
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
    """A pool of canary writers on a background path.

    Use as a context manager; the pool starts on ``__enter__`` and is stopped and
    joined on ``__exit__``. It never raises out of the workers: a probe that could
    abort the run it is observing would be a new failure mode for the sweep, so
    every exception becomes a classified observation instead. Anything that stops
    the probe entirely -- a missing driver, a table that cannot be created -- is
    recorded in :attr:`error` and left for the caller to report.
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

        self._log = _EventLog(log_path)
        self._stop = threading.Event()
        self._queue: queue.Queue[tuple[int, float]] = queue.Queue(maxsize=self.workers)
        #: One permit per worker, released when a worker finishes an attempt. The
        #: queue's own ``maxsize`` is not sufficient: a slot frees as soon as a
        #: worker *takes* a job, so a bounded queue alone would let the dispatcher
        #: keep enqueuing while every thread was mid-write, and those jobs would
        #: then be recorded with a dispatch timestamp from long before anything
        #: attempted them. Since a dispatch offset is what the leading edge of an
        #: outage is measured against, that queueing delay would be indis-
        #: tinguishable from the database being slow. The permit is held for the
        #: whole attempt, so a job is enqueued only when a thread is genuinely free.
        self._idle = threading.Semaphore(self.workers)
        self._threads: list[threading.Thread] = []
        self._seq_lock = threading.Lock()
        self._seq = 0
        self._results_lock = threading.Lock()

        #: Every attempt, in completion order. Ordered by completion rather than
        #: by sequence because that is the order the observations were made in,
        #: and with several writes in flight the two differ.
        self.attempts: list[ProbeAttempt] = []
        #: Ticks that found no free worker. See the module docstring.
        self.dispatch_saturation = 0
        #: Ticks skipped because firing would have bunched against the previous
        #: dispatch rather than spreading the observations. See :meth:`_spacing`.
        self.ticks_spaced_out = 0
        self.ticks = 0
        #: Rolling median of recent served-write latencies, in seconds, used to
        #: space dispatches. Kept as a small window rather than a run-long mean
        #: so the spacing follows the link if it changes -- and it does change:
        #: during an outage writes block, and the pool should not keep firing at
        #: the healthy rate into a database that is not answering.
        self._recent_latencies: deque[float] = deque(maxlen=32)
        self._median_latency: float | None = None
        #: A fatal, probe-wide failure. Not an outage; a broken probe.
        self.error: str | None = None

    # --- clock ------------------------------------------------------------

    def offset(self) -> float:
        return time.monotonic() - self.epoch_monotonic

    # --- lifecycle --------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _record(self, attempt: ProbeAttempt) -> None:
        with self._results_lock:
            self.attempts.append(attempt)

    def _note_latency(self, seconds: float) -> None:
        """Fold a served write into the estimate that spaces dispatches.

        Only served writes count. A write that failed fast tells us nothing about
        how long the database takes to answer, and letting a burst of instant
        connection refusals collapse the estimate would make the probe hammer the
        cluster hardest at exactly the moment it is unwell.
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
        # Server-side, so a statement the client has given up on does not keep
        # holding a range on the server. Set per connection rather than in the
        # DSN so it survives a reconnect without depending on how the caller
        # spelled the connection string.
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
            except BaseException as exc:  # noqa: BLE001 - classification is the point
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
                    except BaseException:  # noqa: BLE001
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
            except BaseException:  # noqa: BLE001
                pass
            self._log.write("disconnect", self.offset(), worker=worker)

    def _spacing(self) -> float:
        """Minimum interval between dispatches, adapted to the observed latency.

        Without this the pool **phase-locks** and the probe silently loses almost
        all of its resolution. Measured against the live testbed before this was
        added: eight workers, a 2 ms dispatch interval and a 368 ms write. All
        eight start within 16 ms of each other, all eight therefore finish within
        16 ms of each other, all eight permits are released together, and the
        dispatcher -- which is free to fire the moment a permit exists -- issues
        the next eight 2 ms apart. The phase relationship is then preserved
        forever. What comes back is not one observation every 46 ms but a burst of
        eight inside 16 ms, once per round trip, with a 350 ms hole between
        bursts: p50 gap 0.22 ms, p90 gap 342 ms, worst 918 ms. An outage of a
        third of a second could begin and end inside one of those holes and be
        recorded as nothing at all.

        The fix is to space dispatches by the round trip divided by the pool size,
        which is the interval the pool can actually sustain and the one the module
        docstring claims. Eight workers against a 368 ms write is a dispatch every
        46 ms, and the workers spread out and stay spread.

        It is derived from the run's own observations rather than configured. The
        write cost here is a property of the link on the day -- 368 ms from this
        workstation, ~69 ms from a client on the gateway -- so a constant would be
        wrong for one of them and unmaintainable for both. Until enough writes
        have completed to estimate it, dispatch is governed by ``interval_s``
        alone, which fills the pool quickly at the start of a run.
        """
        latency = self._median_latency
        if latency is None:
            return self.interval_s
        return max(self.interval_s, latency / self.workers)

    def _dispatcher(self) -> None:
        """Tick on absolute deadlines so the cadence cannot drift.

        Each deadline is ``epoch + n * interval`` rather than ``now + interval``.
        The difference matters over a three-minute run at 2 ms: adding to the last
        wake-up accumulates every scheduling delay, and the recorded cadence would
        then be a property of the machine's load rather than of the configuration
        -- which is D4's shape, a clock that ran at the wrong rate because it was
        derived from work done instead of from time passing.

        The tick is the *upper bound* on the dispatch rate. What actually gates a
        dispatch is a free worker and :meth:`_spacing`; see there for why the
        second condition is not optional.
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
                # Behind by more than a whole tick: skip forward rather than
                # firing a burst to catch up, which would misreport the cadence.
                tick = int((time.monotonic() - self.epoch_monotonic) / self.interval_s)
                continue
            self.ticks += 1
            now = time.monotonic()
            if now - last_dispatch < self._spacing():
                # Too soon after the previous dispatch. Not counted as saturation:
                # saturation means the pool was busy, and this means the pool was
                # free but firing now would bunch this observation against the last
                # one instead of spreading it.
                self.ticks_spaced_out += 1
                continue
            if not self._idle.acquire(blocking=False):
                # Every worker is mid-write. Expected and frequent; it is the
                # normal state when the write cost exceeds the tick interval, and
                # it is counted rather than waited on so that the achieved cadence
                # stays an observation.
                self.dispatch_saturation += 1
                continue
            try:
                self._queue.put_nowait((self._next_seq(), now))
                last_dispatch = now
            except queue.Full:  # pragma: no cover - the semaphore bounds this
                self.dispatch_saturation += 1
                self._idle.release()

    def start(self) -> "RtoProbe":
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

    def __enter__(self) -> "RtoProbe":
        try:
            return self.start()
        except BaseException as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return self

    def __exit__(self, *exc) -> None:
        try:
            self.stop()
        except BaseException as stop_exc:  # noqa: BLE001
            self.error = self.error or f"{type(stop_exc).__name__}: {stop_exc}"

    # --- derived quantities ----------------------------------------------

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

    def rto(self, fault_offset_s: float) -> dict[str, Any]:
        return measure_rto(self.attempts, fault_offset_s)


# --- analysis, as free functions so the same code reads a recorded CSV -------
#
# Kept out of the class deliberately. `resilience.py` re-derives every published
# figure from the run directory rather than trusting what the phase recorded at
# measurement time, and it can only do that if the derivation is reachable
# without a live probe object. This is the same reason `find_recovery` and
# `availability_rto` live at module scope in `p4_chaos`.


def _when(offset_s: float) -> str:
    """Phrase an offset from the fault that may legitimately be slightly negative.

    The last write served before an outage can predate the fault by up to one
    sampling gap, because the fault lands between two observations. Printing that
    as "-0.0s after the fault" reads as a defect in the measurement rather than as
    the resolution limit it actually is.
    """
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
    """Nearest-rank quantile. No interpolation: every value returned is one that
    was actually observed, which is the property that lets it be quoted."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * len(ordered) + 0.5)) - 1))
    return ordered[index]


def resolution_of(gaps: list[float]) -> float | None:
    """The interval an RTO from these observations may be quoted to.

    **Not the median gap.** The distribution of intervals between observations is
    strongly bimodal for a concurrent probe, and the median describes the wrong
    mode. Measured against the live testbed with eight workers before the
    dispatcher was taught to stagger them: p50 0.22 ms, p90 342 ms, worst 918 ms
    -- 65% of the gaps under a millisecond because the pool completed in bursts,
    and a third of a second of dead air between the bursts. The median said
    0.2 ms. An outage of 300 ms could have begun and ended in one of those holes
    and been recorded as no interruption at all.

    What bounds the claim is the long tail, because that is what the probe might
    have been in the middle of when the database came back. The 95th percentile
    is used rather than the maximum: the maximum of a few thousand samples is a
    single scheduling accident and would move the reported precision of every run
    by whatever the worst hiccup of that run happened to be, while p95 is stable
    and still describes the tail. The maximum is reported separately, because a
    p95 of 50 ms next to a worst of 900 ms is a fact about the run that a reader
    should see rather than a number to average away.

    Staggering the dispatches (see :meth:`RtoProbe._spacing`) is what makes this
    number small; reporting it honestly is what stops it from being asserted when
    it is not.
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
    """What the probe achieved, as distinct from what it was asked for.

    ``resolution_s`` is the number an RTO from this probe may be quoted to. It is
    the observed median gap between *served* writes, not ``interval_s``: the
    configured cadence is an upper bound on the sampling rate that the cost of a
    quorum write makes unreachable, and reporting it as the resolution would
    assert a precision the observations never had.
    """
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
        # The figure to quote against. See resolution_of: it is the tail of the
        # gap distribution, not its middle.
        "resolution_s": round(resolution, 6) if resolution else None,
        # Both modes of the distribution, so a bimodal one is visible as bimodal
        # rather than collapsing into a single flattering number.
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

    An "outage" here is defined by observation and nothing else: a gap between
    two writes the database served. It is bounded above by the truth -- the
    database may have recovered at any point between the two -- and that is why
    the returned window carries both edges rather than a single duration to be
    quoted as if it were the outage itself.
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
                    # A write already in flight when service resumed dates the
                    # recovery more tightly than one dispatched afterwards: it was
                    # waiting, so it returned as soon as the range was served.
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
    """Is the post-fault tail heavier than the pre-fault one, or merely longer?

    This exists because of a specific false positive on real data. In the
    ``dead`` run of 2026-09-05 the probe reported an 869 ms outage 40 s after the
    fault, having cleared a noise floor of 862 ms. The floor was the largest
    healthy gap (638 ms) plus one sampling period, and 869 ms duly exceeded it --
    but the pre-fault window held 711 observations and the post-fault window
    1462. Drawing twice as many samples from the same heavy-tailed link
    distribution produces a larger maximum on its own, with nothing having gone
    wrong at all. The rate of gaps over 500 ms was 2/711 before and 5/1462 after:
    identical to within counting noise.

    Comparing a maximum against a maximum is therefore the wrong test whenever
    the two windows differ in length, which they always do -- the fault is
    injected a third of the way into the run by design. The comparison that does
    hold is between *rates*: pick a threshold from the healthy distribution and
    ask how often it is exceeded per observation on each side. If the post-fault
    rate is not meaningfully higher, the longest post-fault gap is a draw from the
    same distribution and attributing it to the fault would be inventing a
    failover event out of the probe's own jitter.

    The threshold is the healthy 95th percentile rather than a constant: it is
    scale-free, so this works identically for a probe on the gateway paying 70 ms
    a write and one on a workstation paying 370 ms.

    Returns the evidence rather than a verdict alone, so a marginal call can be
    inspected instead of trusted.
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
    # Expected count after the fault if nothing changed, from the healthy rate.
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
        # 1.5x is a deliberately loose bar. The quantity being separated -- "the
        # tail got heavier" from "the window got longer" -- is coarse, and a tight
        # threshold would reject real events on a link that jitters. A check that
        # rejects sound data gets disabled, which is how check_littles_law came to
        # be corrected.
        "heavier_after_fault": bool(ratio is not None and ratio >= 1.5),
    }


def measure_rto(
    attempts: list[ProbeAttempt], fault_offset_s: float
) -> dict[str, Any]:
    """How long the database could not serve a write, and when that began.

    The naive derivation -- fault to the next write served afterwards -- is
    wrong on this testbed and is not what this returns. A five-voter cluster
    losing one member keeps committing for as long as it takes to notice, which
    is ~6 s here for liveness alone. The next write after the fault therefore
    usually succeeds, and a figure built on it would report an RTO of one
    sampling gap while the actual interruption had not started yet.

    What is measured instead is the **first gap in served writes that is longer
    than anything the probe saw while the system was healthy**, and the RTO is
    from the fault to the end of that gap. The comparison is against the run's own
    pre-fault gaps rather than a fixed threshold: how often this probe manages to
    observe the database is a property of the link, the pool size and the day, and
    a constant here would be a threshold that fires on a slow link and misses on a
    fast one. Where there are too few pre-fault observations to characterise that,
    twice the median gap is used and the fallback is named in the output.

    The keys, and what each may be used for:

    ``rto_s``
        Fault to service restored. The RTO to quote -- and ``None``, with a claim
        saying so, when no gap exceeded the noise floor. "No interruption was
        detectable at this resolution" is a result; a number smaller than the
        sampling gap is not.
    ``outage``
        The gap itself, with both edges, so the claim can be checked against the
        series and drawn on a timeline.
    ``detection_lag_s``
        Fault to the first blocked or failed attempt. The cluster's detection
        time, reported separately because folding it into the RTO would credit
        the recovery with the interval before anything was wrong. The legacy
        pipeline's 6.0 s and 5.2 s "RTOs" were an artefact of exactly this
        conflation.
    ``next_write_after_fault_s``
        Fault to the next served write, whatever it was. Kept because it is the
        quantity the RPO audit log's ``availability_rto`` reports, and the two
        artefacts should be comparable term for term rather than only in spirit.

    A run that ended while the outage was still open reports ``rto_s`` of ``None``
    and ``truncated``, never the time remaining: the probe cannot see a recovery
    that happened after it stopped, and reporting the truncation as a measurement
    would put a floor into the figure that is an artefact of the run's duration.
    """
    served = sorted(served_attempts(attempts), key=lambda a: a.complete_offset_s)
    gaps = [
        (a, b, b.complete_offset_s - a.complete_offset_s)
        for a, b in zip(served, served[1:])
    ]
    # Sampling resolution is characterised from the gaps that closed *before* the
    # fault, not from the whole run. The whole-run tail includes the outage gap
    # itself, so using it here would let a long outage raise the very threshold
    # that detects it -- the longer the interruption, the less detectable. The
    # same circularity in a different form as judging a gap healthy by when it
    # opened, which the noise floor below also avoids.
    #
    # summarise() has no fault to partition on and legitimately reports the
    # whole-run figure; that one describes the instrument over the run, this one
    # describes it while the system was working.
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

    base: dict[str, Any] = {
        "resolution_s": round(resolution, 6) if resolution else None,
        "detection_lag_s": detection_lag,
        "next_write_after_fault_s": next_after,
        "served_after_fault": len(after_fault),
        "served_before_fault": len(served) - len(after_fault),
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

    # The noise floor: the longest gap seen while the system was demonstrably
    # healthy, plus one sampling period. Anything at or below that is
    # indistinguishable from the probe's ordinary sampling.
    #
    # A gap counts as healthy only if it *closed* before the fault. Judging by
    # when it opened would admit the gap that spans the fault -- the outage
    # itself -- into the evidence for what healthy looks like, and the floor would
    # then be raised to exactly the interval it exists to detect.
    #
    # The added sampling period is not a fudge factor. Two observations that
    # differ by less than the interval between observations differ by less than
    # the instrument can resolve, and without it ordinary scheduling jitter -- or
    # simple floating-point noise in a series of round numbers -- puts a gap a
    # microsecond over the previous maximum and manufactures an outage from it.
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

    outage = next(
        (
            (a, b, gap)
            for a, b, gap in gaps
            if b.complete_offset_s >= fault_offset_s and gap > floor
        ),
        None,
    )

    # A gap that is still open when the probe stops does not appear in `gaps` at
    # all -- there is no closing observation -- so it is looked for separately.
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

    if outage is None:
        return {
            **base,
            "rto_s": None,
            "measurable": True,
            "outage": None,
            "truncated": False,
            "below_resolution": True,
            "quotable_value_s": None,
            # `floor` is the detection threshold, not the sampling resolution:
            # it is the longest healthy gap plus one sampling period, so it is
            # always the larger of the two. Calling it "resolution" here would
            # quote a number ~3x the instrument's actual precision and would
            # disagree with the `resolution_s` in the same dict.
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
    rto = after.complete_offset_s - fault_offset_s
    # The write that closes the gap can be dispatched before the gap even
    # opens -- workers run concurrently, so `after` need not wait for `before`
    # to complete before starting. Its own flight time (dispatch to complete)
    # can then exceed the gap's duration, and the fraction this is meant to
    # report -- "how much of the gap was this write already in flight for" --
    # is the OVERLAP between its flight window and the gap window, not its
    # flight time on its own. Dividing flight time by gap duration without
    # that clamp gives values above 1 whenever the write started earlier than
    # `before` finished, which is common with several workers in flight: found
    # on the retained 2026-09-05 dead-fault run, seq_id 1169 (dispatched
    # 99.762s, before `before`'s own completion at 100.220s) read back as
    # in_flight_fraction = 1.526 -- a fraction greater than one, which is not a
    # fraction of anything.
    overlap_start = max(after.dispatch_offset_s, before.complete_offset_s)
    overlap = max(0.0, after.complete_offset_s - overlap_start)
    attribution = tail_attribution(
        healthy_gaps,
        [gap for _, b, gap in gaps if b.complete_offset_s >= fault_offset_s],
    )
    # A gap can clear the noise floor and still be the healthy distribution
    # showing its tail over a longer window. When the exceedance rate has not
    # risen, the interval is reported with its evidence and explicitly not
    # offered as a recovery time.
    attributable = attribution.get("heavier_after_fault", True)
    # The last write served before the outage may predate the fault by up to one
    # sampling gap -- the fault lands between two observations -- so this is
    # routinely a small negative number and must not be printed as "-0.0s after".
    started_after = before.complete_offset_s - fault_offset_s
    # A write that spent most of the gap in flight returns the instant the range
    # is served again, so it dates the recovery to itself. One dispatched after
    # the fact only dates it to the next poll. The fraction is reported rather
    # than only the verdict, because it is the difference between an observation
    # and an upper bound.
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
        # Both edges are the probe's own observations, so the systematic delay
        # between a write committing and the client learning of it appears in
        # both and cancels here. It does not cancel in `rto_s`, whose other edge
        # is the injector's timestamp.
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
    """Rebuild attempts from a recorded ``rto_probe.csv``.

    So the analysis layer re-derives an RTO from the observations on disk rather
    than reading back the summary the phase wrote at measurement time. A figure
    whose underlying observations cannot be recomputed cannot be disputed.
    """
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

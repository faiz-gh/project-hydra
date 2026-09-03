"""Phase IV: fault injection, and the measurement of RTO and RPO.

A steady-state workload is driven against the gateway while a fault is injected
into a different node at a fixed offset. Two quantities are measured:

* **RTO**, the interval from the fault to the point at which throughput has
  returned to a stated fraction of its pre-fault level and stayed there.
* **RPO**, the set of writes the client was told had committed but which are not
  present afterwards.

Three properties of this implementation are corrections of specific defects in
the version it replaces, and each is load-bearing rather than stylistic.

**Injection is scheduled on a monotonic clock, by a timer thread that never
looks at the sample stream.** The legacy runner counted parsed lines and treated
each as a second; because the generator emits one line per operation type, its
clock ran at twice wall-clock and a fault intended for t=60 s was injected at
34.5 s (D4). Decoupling the schedule from the data entirely is the only way to
make that class of error impossible rather than merely unlikely.

**Recovery is the start of a sustained window, not the end of a guard.** The
legacy condition could not declare recovery until ten of its (double-speed)
seconds had passed, so the reported RTOs of 6.0 s and 5.2 s are that guard
rather than measurements of anything. Here throughput must hold at or above the
threshold for ``recovery_hold_s`` consecutive seconds, and the reported
timestamp is the *first* sample of that window: the hold qualifies the
recovery, it does not postpone it.

**The audit writer never retries a sequence number.** The legacy writer retried
the same ``seq_id`` after any exception, which livelocks precisely in the case
RPO exists to measure: a write that commits but whose acknowledgement is lost to
the partition will fail forever against its own duplicate key, truncating the
audit series at the interesting moment. Here every attempt takes a fresh number
and its outcome is classified as acknowledged, ambiguous or refused. Only writes
the client was *told* had committed can constitute data loss; a gap in the table
alone establishes nothing.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..config import Profile, Settings
from ..core import preflight, ssh
from ..core.recorder import (
    AUDIT_COLUMNS,
    COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
)
from ..core.workload import PERIODIC, Sample, WorkloadParser, group_timed_ticks
from ..topology import Node, Topology

#: Fault modes. ``dead`` removes the process outright; ``recover`` severs the
#: node's overlay network for a period and then restores it, which exercises the
#: heal path rather than only the detection path.
MODES = ("dead", "recover")

_PAYLOADS = {
    "dead": "killall -9 cockroach",
    # Detached so the SSH session can return before the network drops beneath it.
    "recover": "nohup bash -c 'tailscale down && sleep 45 && tailscale up' >/dev/null 2>&1 &",
}


@dataclass
class AuditResult:
    acknowledged: int
    ambiguous: int
    refused: int
    present: int
    lost: list[int]
    ambiguous_committed: int
    first_ack_utc: str | None
    last_ack_utc: str | None

    @property
    def rpo_violations(self) -> int:
        return len(self.lost)

    def to_dict(self) -> dict[str, Any]:
        return {
            "acknowledged": self.acknowledged,
            "ambiguous": self.ambiguous,
            "refused": self.refused,
            "present_in_table": self.present,
            "rpo_violations": self.rpo_violations,
            "lost_seq_ids": self.lost[:50],
            "ambiguous_but_committed": self.ambiguous_committed,
            "first_acknowledged_utc": self.first_ack_utc,
            "last_acknowledged_utc": self.last_ack_utc,
        }


class AuditWriter:
    """Writes a monotonic sequence continuously, recording the client's view.

    The distinction between *acknowledged*, *ambiguous* and *refused* is the
    whole of an honest RPO measurement. A refused write was never promised and
    its absence is not data loss. An ambiguous write -- one whose connection
    failed after the statement was sent -- may or may not have committed, and
    collapsing it into either category would either invent data loss or conceal
    it. Only an acknowledged write that is subsequently absent is a violation.
    """

    def __init__(self, dsn: str, interval_s: float) -> None:
        self._dsn = dsn
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.acknowledged: set[int] = set()
        self.ambiguous: set[int] = set()
        self.refused: set[int] = set()
        self.first_ack_utc: str | None = None
        self.last_ack_utc: str | None = None
        self.error: str | None = None
        #: (monotonic, seq, outcome) for every attempt, in order. Availability
        #: RTO is derived from this rather than from the workload stream: the
        #: question "when did writes start succeeding again" is about the
        #: database accepting writes, not about the generator's throughput
        #: recovering, and conflating the two is what makes a reported RTO
        #: unfalsifiable.
        self.attempts: list[tuple[float, int, str]] = []

    def _loop(self) -> None:
        import psycopg

        seq = 0
        conn = None
        while not self._stop.is_set():
            seq += 1
            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO rpo_audit (seq_id) VALUES (%s)", (seq,))
                self.acknowledged.add(seq)
                self.attempts.append((time.monotonic(), seq, "ack"))
                stamp = utcnow()
                if self.first_ack_utc is None:
                    self.first_ack_utc = stamp
                self.last_ack_utc = stamp
            except Exception as exc:  # noqa: BLE001 - classification is the point
                # A connection-level failure leaves the outcome genuinely
                # unknown; anything else means the statement was rejected and
                # certainly did not commit.
                name = type(exc).__name__
                if "Operational" in name or "Interface" in name or "Connection" in name:
                    self.ambiguous.add(seq)
                    self.attempts.append((time.monotonic(), seq, "ambiguous"))
                else:
                    self.refused.add(seq)
                    self.attempts.append((time.monotonic(), seq, "refused"))
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn = None
            # Never retry `seq`: the next attempt takes a fresh number. Retrying
            # is what livelocked the legacy writer against its own duplicate key.
            self._stop.wait(self._interval)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def __enter__(self) -> "AuditWriter":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def collect(self, dsn: str) -> AuditResult:
        """Compare the client's record against what the database actually holds."""
        import psycopg

        with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT seq_id FROM rpo_audit")
                present = {row[0] for row in cur.fetchall()}
        return AuditResult(
            acknowledged=len(self.acknowledged),
            ambiguous=len(self.ambiguous),
            refused=len(self.refused),
            present=len(present),
            lost=sorted(self.acknowledged - present),
            ambiguous_committed=len(self.ambiguous & present),
            first_ack_utc=self.first_ack_utc,
            last_ack_utc=self.last_ack_utc,
        )


def inject_fault(node: Node, mode: str) -> dict[str, Any]:
    """Apply the fault and return when it was applied.

    The timestamp is taken before the call and reported even when the transport
    fails, because a ``dead`` injection frequently kills the connection it
    arrived on: an SSH error here is evidence the fault landed, not that it
    did not.
    """
    if mode not in _PAYLOADS:
        raise ValueError(f"unknown chaos mode {mode!r}; expected one of {MODES}")
    at_utc = utcnow()
    at_monotonic = time.monotonic()
    try:
        result = ssh.run(node, _PAYLOADS[mode], timeout=10)
        detail = f"rc={result.returncode}"
    except Exception as exc:  # noqa: BLE001
        detail = f"transport error after dispatch: {type(exc).__name__}"
    return {
        "target": node.name,
        "host": node.host,
        "mode": mode,
        "at_utc": at_utc,
        "at_monotonic": at_monotonic,
        "detail": detail,
    }


def availability_rto(
    attempts: list[tuple[float, int, str]],
    fault_monotonic: float,
) -> dict[str, Any]:
    """Time from the fault until the database accepted a write again.

    This is the quantity an RTO claim usually denotes in a failover study, and it
    is distinct from the throughput-based figure :func:`find_recovery` produces.
    The two answer different questions and can differ by orders of magnitude:
    after a `dead` fault on a fast-triangle member the cluster accepts writes
    again within seconds, while its *throughput* never returns to the stated
    fraction of baseline at all, because the surviving quorum is intercontinental.
    Reporting only the latter would describe such a cluster as never having
    recovered, which is false; reporting only the former would conceal that it is
    permanently degraded. Both are recorded.

    Resolution is bounded below by the audit cadence, which is itself bounded by
    the cost of a quorum write -- on this topology roughly 70 ms, not the nominal
    ``audit_interval_s``. A figure from this function should not be quoted to a
    precision finer than the observed inter-attempt gap, which is returned
    alongside it so the claim can be qualified honestly.
    """
    acked = [t for t, _, outcome in attempts if outcome == "ack"]
    before = [t for t in acked if t < fault_monotonic]
    after = [t for t in acked if t >= fault_monotonic]

    gaps = [b - a for a, b in zip(acked, acked[1:])] if len(acked) > 1 else []
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else None

    if not after:
        return {
            "availability_rto_s": None,
            "detail": "no write was acknowledged after the fault",
            "writes_acknowledged_after_fault": 0,
            "resolution_s": round(typical_gap, 4) if typical_gap else None,
        }

    first_after = min(after)
    last_before = max(before) if before else None
    return {
        "availability_rto_s": round(first_after - fault_monotonic, 3),
        # The observed outage in the write stream. Distinct from the RTO above:
        # the last pre-fault success may predate the fault by up to one cadence.
        "write_gap_s": round(first_after - last_before, 3) if last_before else None,
        "writes_acknowledged_after_fault": len(after),
        # Anything below this is indistinguishable from no interruption at all.
        "resolution_s": round(typical_gap, 4) if typical_gap else None,
    }


def clock_offsets(observed_at: dict[float, float]) -> dict[str, Any]:
    """Summarise the offset between the generator's clock and the harness's.

    ``observed_at`` maps the generator's ``elapsed`` value for an interval to the
    harness-clock offset at which that interval was observed. Their difference is
    how long after the run's epoch the generator's own zero fell: the cost of
    opening the SSH session and starting the process, about 5.4 s on this
    testbed.

    It is reported with its spread rather than as a single number. A constant
    offset means the two clocks run at the same rate and differ only in origin,
    which is what licenses converting between them; a drifting one would mean
    they do not, and that a single conversion factor is not available. Stating
    the spread lets a reader see which case holds instead of trusting that it is
    the first. The legacy pipeline's failure mode was precisely a clock that ran
    at the wrong *rate* (D4), so rate agreement is not something to assume.
    """
    if not observed_at:
        return {"method": "unmeasured", "detail": "no interval was observed live"}
    offsets = sorted(wall - elapsed for elapsed, wall in observed_at.items())
    median = offsets[len(offsets) // 2]
    return {
        "method": "measured_per_tick",
        "generator_start_offset_s": round(median, 3),
        "min_s": round(offsets[0], 3),
        "max_s": round(offsets[-1], 3),
        "spread_s": round(offsets[-1] - offsets[0], 3),
        "samples": len(offsets),
        "note": (
            "elapsed_s + generator_start_offset_s = wall_offset_s. Offsets in "
            "events.json are on the harness clock; add this to any figure axis "
            "taken from the generator's elapsed_s before drawing the two together."
        ),
    }


def find_recovery(
    ticks: list[tuple[float, float]],
    fault_at_s: float,
    baseline_tps: float,
    threshold: float,
    hold_s: float,
) -> float | None:
    """First offset after the fault at which throughput holds above the threshold.

    ``ticks`` is a sequence of ``(offset_s, total_tps)`` measured from the start
    of the run. The returned offset is the beginning of the qualifying window,
    not its end: requiring the level to be sustained is a statement about
    confidence in the recovery, and should not inflate the interval attributed
    to it.
    """
    floor = baseline_tps * threshold
    post = [(t, v) for t, v in ticks if t >= fault_at_s]
    for index, (start_t, _) in enumerate(post):
        window = [v for t, v in post[index:] if t < start_t + hold_s]
        if not window or (post[-1][0] - start_t) < hold_s - 1e-9:
            break  # not enough remaining samples to establish the hold
        if all(v >= floor for v in window):
            return start_t
    return None


def run(
    settings: Settings,
    profile: Profile,
    mode: str,
    database: str = "ycsb",
    audit_database: str = "bench",
) -> tuple[RunDirectory, dict[str, Any]]:
    """Drive a steady-state workload, inject a fault, and measure RTO and RPO."""
    spec = profile.workload
    chaos = profile.chaos
    topo = settings.topology
    gateway = topo.gateway
    target = topo.get(chaos.target)

    if target.host == gateway.host:
        raise ValueError(
            f"chaos target {target.name!r} is the gateway the workload is driven "
            "from; the fault would remove the measurement apparatus along with "
            "the node under test"
        )

    report = preflight.PreflightReport()
    preflight.check_clock_offset(report, [gateway, target])
    preflight.check_leaseholder_placement(report, gateway, database, gateway.region)
    report.raise_if_failed()

    workload_dsn = f"postgresql://root@{gateway.host}:26257/{database}?sslmode=disable"
    audit_dsn = f"postgresql://root@{gateway.host}:26257/{audit_database}?sslmode=disable"

    ssh.run(
        gateway,
        f"cockroach sql --insecure --host={gateway.host}:26257 --database={audit_database} "
        '-e "DROP TABLE IF EXISTS rpo_audit; '
        'CREATE TABLE rpo_audit (seq_id INT8 PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now());"',
        timeout=60,
    )

    run_dir = RunDirectory(settings.runs_dir, new_run_id(f"p4-chaos-{mode}"))
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase="p4_chaos",
        profile=profile.to_dict(),
        # Filled once the run's monotonic zero is taken, below.
        clock_epoch_utc=None,
        topology=[
            {"name": gateway.name, "host": gateway.host, "role": "generator, audit endpoint"},
            {"name": target.name, "host": target.host, "role": f"chaos target ({mode})"},
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
    )
    server = preflight.capture_server_config(gateway)
    manifest.cockroach_version = server.get("version")
    manifest.note(f"server: {server['start_command']}")
    manifest.note(f"host: {preflight.format_hardware(server['hardware'])}")

    generator = (
        f"cockroach workload run {spec.generator} "
        f"--workload={spec.ycsb_workload} --seed={spec.seed} "
        f"--insert-count={spec.insert_count} "
        f"--request-distribution={spec.request_distribution} "
        f"--read-freq={spec.read_freq} --update-freq={spec.update_freq} "
        f"--concurrency={chaos.concurrency} --duration={chaos.duration_s}s "
        f"--display-every={spec.display_every_s}s '{workload_dsn}'"
    )
    manifest.generator_command = generator

    events: dict[str, Any] = {"mode": mode, "target": target.name}
    series: list[tuple[float, float]] = []
    injected: dict[str, Any] = {}
    first_error_at: float | None = None

    def timer(t_zero: float) -> None:
        """Inject at a wall-clock offset, independent of the sample stream (D4)."""
        deadline = t_zero + chaos.inject_at_s
        while (remaining := deadline - time.monotonic()) > 0:
            if stop_timer.wait(min(remaining, 0.25)):
                return
        injected.update(inject_fault(target, mode))
        injected["at_offset_s"] = round(injected["at_monotonic"] - t_zero, 3)
        print(
            f"  [{injected['at_offset_s']:6.1f}s] fault injected on {target.host} "
            f"({mode}); {injected['detail']}"
        )

    stop_timer = threading.Event()
    parser = WorkloadParser(strict=True)
    samples: list[Sample] = []
    #: elapsed_s -> harness-clock offset at which that interval was observed.
    #: Kept so the metrics table can carry both clocks and the offset between
    #: them becomes a recorded observation rather than an inference.
    observed_at: dict[float, float] = {}
    raw_path = run_dir.raw(f"chaos_{mode}.txt")

    print(f"  running {chaos.duration_s}s at C={chaos.concurrency}, injecting at {chaos.inject_at_s}s")
    with AuditWriter(audit_dsn, chaos.audit_interval_s) as audit:
        t_zero = time.monotonic()
        events["t_start_utc"] = utcnow()
        manifest.clock_epoch_utc = events["t_start_utc"]
        timer_thread = threading.Thread(target=timer, args=(t_zero,), daemon=True)
        timer_thread.start()

        with open(raw_path, "w") as tee:
            with ssh.StreamingRemote(gateway, ssh.force_tty(generator), tee=tee) as stream:
                def feed() -> Iterator[tuple[float, Sample]]:
                    for line in stream:
                        sample = parser.feed(line)
                        if sample is not None:
                            samples.append(sample)
                            yield time.monotonic(), sample

                for arrived, tick in group_timed_ticks(feed()):
                    offset = arrived - t_zero
                    observed_at[tick.elapsed_s] = offset
                    series.append((offset, tick.total_tps))
                    if tick.errors_cum > 0 and first_error_at is None:
                        first_error_at = offset
                        events["t_first_error_offset_s"] = round(offset, 3)
                    if int(tick.elapsed_s) % 15 == 0:
                        print(f"  [{offset:6.1f}s] tps={tick.total_tps:8.1f} errors={tick.errors_cum}")

        stop_timer.set()
        timer_thread.join(timeout=5)

    audit_result = audit.collect(audit_dsn)

    fault_offset = injected.get("at_offset_s")
    pre = [v for t, v in series if fault_offset is not None and t < fault_offset]
    baseline_tps = sum(pre[-20:]) / len(pre[-20:]) if pre else 0.0
    recovered_at = (
        find_recovery(series, fault_offset, baseline_tps, chaos.recovery_threshold, chaos.recovery_hold_s)
        if fault_offset is not None and baseline_tps > 0
        else None
    )

    avail = (
        availability_rto(audit.attempts, injected["at_monotonic"])
        if injected
        else {"availability_rto_s": None, "detail": "fault was never injected"}
    )

    with MetricsWriter(run_dir.audit_csv, AUDIT_COLUMNS) as audit_log:
        for at_monotonic, seq, outcome in audit.attempts:
            audit_log.write(
                {
                    "wall_offset_s": round(at_monotonic - t_zero, 4),
                    "seq_id": seq,
                    "outcome": outcome,
                }
            )

    events.update(
        {
            "clock": clock_offsets(observed_at),
            "injected": injected,
            "availability": avail,
            "baseline_tps": round(baseline_tps, 2),
            "recovery_threshold": chaos.recovery_threshold,
            "recovery_floor_tps": round(baseline_tps * chaos.recovery_threshold, 2),
            "recovery_hold_s": chaos.recovery_hold_s,
            "t_recovered_offset_s": round(recovered_at, 3) if recovered_at is not None else None,
            # Performance RTO: throughput back to `recovery_threshold` of
            # baseline and held. Undefined while a fast-triangle member is down,
            # since the surviving quorum is slower than the threshold allows.
            "performance_rto_s": round(recovered_at - fault_offset, 3)
            if recovered_at is not None and fault_offset is not None
            else None,
            "rpo": audit_result.to_dict(),
            "t_end_utc": utcnow(),
        }
    )

    stamp = utcnow()
    with MetricsWriter(run_dir.metrics_csv, COLUMNS) as writer:
        for sample in samples:
            if sample.kind != PERIODIC:
                continue
            writer.write(
                {
                    "ts_utc": stamp,
                    "elapsed_s": sample.elapsed_s,
                    # Empty, never 0.0, if this interval was somehow not observed
                    # live: an unmeasured offset must not be indistinguishable
                    # from an offset measured as zero (D5).
                    "wall_offset_s": round(observed_at[sample.elapsed_s], 3)
                    if sample.elapsed_s in observed_at
                    else "",
                    "concurrency": chaos.concurrency,
                    "repetition": 1,
                    "op": sample.op,
                    "tps": sample.tps,
                    "tps_cum": sample.values.get("tps_cum", ""),
                    "errors_cum": sample.errors_cum,
                    "p50_ms": sample.latency_ms("p50"),
                    "p95_ms": sample.latency_ms("p95"),
                    "p99_ms": sample.latency_ms("p99"),
                    "pmax_ms": sample.latency_ms("pmax"),
                    "gateway_cpu_pct": "",
                    "gateway_disk_iops": "",
                    "gateway_rss_bytes": "",
                }
            )

    manifest.finished_utc = utcnow()
    manifest.validation = {"preflight": report.to_dict()}
    run_dir.write_manifest(manifest)
    run_dir.write_events(events)
    run_dir.write_preflight(report.to_dict())
    return run_dir, events

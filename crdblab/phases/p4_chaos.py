"""Phases III-IV: fault injection, and the measurement of RTO and RPO.

Phase III is ``recover`` (a self-healing network partition); Phase IV is
``dead`` (the database process is killed). A steady-state workload runs from
the client node while the fault is injected into the node leading the write
path. Three independent clients observe it:

* the **generator**: throughput per second, giving the performance RTO;
* the **audit writer**: a serial sequence of writes, each classified as
  acknowledged, ambiguous or refused, giving the RPO and an availability RTO;
* the **RTO probe** (on the client node): several concurrent canary writes,
  giving a finer-grained availability RTO.

The fault is scheduled on a monotonic clock by a timer thread, measured from
the generator's first sample. Recovery is the *start* of a window in which
throughput holds above the threshold for ``recovery_hold_s``.
"""

from __future__ import annotations

import shlex
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Profile, Settings, pg_direct_dsn, pg_generator_dsn
from ..core import preflight, ssh
from ..core.hardware_metrics import HardwareMetricsSampler
from ..core.recorder import (
    AUDIT_COLUMNS,
    COLUMNS,
    PROBE_COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
    utcnow_us,
)
from ..core.remote_probe import RemoteRtoProbe, check_agent_prerequisites
from ..core.rto_probe import CREATE_TABLE_SQL
from ..core.workload import PERIODIC, Sample, WorkloadParser, group_timed_ticks
from ..topology import CLIENT_NODE, Node, Topology

#: ``dead`` kills the process; ``recover`` partitions the node then heals it.
MODES = ("dead", "recover")

#: Checkout root; the probe agent's source is copied from here each run.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]

#: Dead-man switch added to the agent's lifetime. The harness normally stops it;
#: this only stops an orphaned agent. Generous because generator setup can be slow.
PROBE_OVERRUN_S = 600.0

SUDO = ssh.SUDO


#: systemd drop-in that sets ``Restart=no`` so a killed Patroni stays dead instead
#: of being restarted in ~100 ms. Removed again by :func:`restore_target`.
PG_RESTART_OVERRIDE = "/etc/systemd/system/patroni.service.d/99-crdblab-chaos.conf"

#: How long the ``recover`` partition lasts; also used by :func:`generator_duration_s`.
RECOVER_HEAL_DELAY_S = 45


_PG_DEAD_PAYLOAD = (
    f"{SUDO} mkdir -p {PG_RESTART_OVERRIDE.rsplit('/', 1)[0]} && "
    f"printf '[Service]\\nRestart=no\\n' | {SUDO} tee {PG_RESTART_OVERRIDE} >/dev/null && "
    f"{SUDO} systemctl daemon-reload && "
    f"{SUDO} systemctl kill --kill-who=all --signal=SIGKILL patroni.service"
)


def get_payload(mode: str, engine: str) -> str:
    """The shell command that injects the fault.

    PostgreSQL's ``dead`` fault disables systemd restarts, then kills the whole
    unit's cgroup (Patroni runs as ``python3``, so ``killall`` would miss it).
    """
    if mode == "dead":
        if engine == "postgresql":
            return _PG_DEAD_PAYLOAD
        return f"{SUDO} killall -9 cockroach"
    elif mode == "recover":
        # `tailscale down` cuts this SSH session, so the heal must be detached.
        return (
            f"{SUDO} nohup setsid bash -c "
            f"'tailscale down && sleep {RECOVER_HEAL_DELAY_S} && tailscale up' "
            f">/dev/null 2>&1 &"
        )
    raise ValueError(f"Unknown mode: {mode}")


def preflight_payload(mode: str, engine: str) -> str:
    """A harmless command needing the same privileges as the fault.

    ``recover``'s payload is backgrounded, so its exit status cannot show the
    fault landed; this checks permission before the run instead.
    """
    if mode == "dead":
        if engine == "postgresql":
            # Same rights as the payload (sudo systemctl, writable unit dir), no change.
            return (
                f"{SUDO} systemctl show patroni.service --property=MainPID && "
                f"{SUDO} test -w /etc/systemd/system"
            )
        return f"{SUDO} killall -0 cockroach"
    elif mode == "recover":
        return f"{SUDO} tailscale status --json >/dev/null"
    raise ValueError(f"Unknown mode: {mode}")


#: Re-exported from ``core.preflight``, which owns primary resolution.
PATRONI_PRIMARY_PORT = preflight.PATRONI_PRIMARY_PORT
PATRONI_PRIMARY_TIMEOUT_S = preflight.PATRONI_PRIMARY_TIMEOUT_S
resolve_patroni_primary = preflight.resolve_patroni_primary


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

    Each write is *acknowledged*, *ambiguous* (connection failed after sending;
    may or may not have committed) or *refused*. Only an acknowledged write
    later missing from the table is data loss. Sequence numbers are never retried.
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
        #: (monotonic, seq, outcome) for every attempt; the availability RTO
        #: is derived from these.
        self.attempts: list[tuple[float, int, str]] = []
        #: When the writer stopped, to tell a quiet cluster from a stalled writer.
        self.stopped_at: float | None = None
        #: Start of the most recent attempt (advances past the last ack while blocked).
        self.last_attempt_started: float | None = None

    def _loop(self) -> None:
        import psycopg

        seq = 0
        conn = None
        while not self._stop.is_set():
            seq += 1
            self.last_attempt_started = time.monotonic()
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
            except Exception as exc:
                # Connection-level failure: outcome unknown. Otherwise: rejected.
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
            # Never retry `seq`; the next attempt takes a fresh number.
            self._stop.wait(self._interval)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def __enter__(self) -> AuditWriter:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        # After the bounded join; a writer still wedged just leaves a coverage gap.
        self.stopped_at = time.monotonic()

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


def check_fault_authorisation(
    report: preflight.PreflightReport,
    node: Node,
    mode: str,
    engine: str,
) -> None:
    """Pre-flight: the fault must be permitted on ``node``.

    A denied injection still yields a full run that measures an undisturbed
    cluster and looks like excellent resilience, so it is caught up front.
    """
    probe = preflight_payload(mode, engine)
    try:
        result = ssh.run(node, probe, timeout=preflight.CONTROL_TIMEOUT_S)
    except Exception as exc:
        report.add(
            "fault_authorisation",
            False,
            f"{node.name}: could not verify the {mode!r} fault would be "
            f"permitted ({type(exc).__name__})",
            node=node.name,
            mode=mode,
            probe=probe,
        )
        return

    stderr = (result.stderr or "").strip()
    passed = result.returncode == 0
    if passed:
        detail = f"{node.name}: {mode!r} fault is permitted as {node.user}"
    else:
        detail = (
            f"{node.name}: the {mode!r} fault would NOT land -- {probe!r} exited "
            f"{result.returncode}"
            + (f" ({stderr.splitlines()[0]})" if stderr else "")
            + f". The SSH user is {node.user!r} and the target process runs as "
            "root; without passwordless sudo the injection is silently refused "
            "and the run measures an undisturbed cluster"
        )
    report.add(
        "fault_authorisation",
        passed,
        detail,
        node=node.name,
        mode=mode,
        probe=probe,
        returncode=result.returncode,
        stderr=stderr,
    )


def generator_duration_s(chaos: Any, mode: str = "dead") -> int:
    """How long the generator must run to leave a usable post-fault series.

    ``inject_at_s`` counts from the generator's first sample, so the run is
    extended to ``inject_at_s + min_post_fault_s`` (plus the heal delay in
    ``recover`` mode, so the demoted node is back before the next phase).
    Never shortened.
    """
    settle_s = RECOVER_HEAL_DELAY_S if mode == "recover" else 0
    return max(
        chaos.duration_s,
        chaos.inject_at_s + settle_s + chaos.min_post_fault_s,
    )


def restore_target(
    node: Node,
    topo: Topology,
    engine: str,
    timeout_s: float = 120.0,
    poll_interval_s: float = 5.0,
) -> dict[str, Any]:
    """Bring the ``dead`` fault target back and confirm it rejoined.

    Called only after every artefact is written, and recorded as its own
    event, so the repair cannot affect the measurement. Uses sudo (the store is
    root-owned), polls liveness from a surviving node, and redirects the
    remote output so ``--background`` does not hold the SSH session open.
    """
    survivors = [n for n in topo.nodes if n.name != node.name]
    if not survivors:
        return {"attempted": False, "detail": "no surviving node to rejoin or query"}
    witness = survivors[0]
    join = ",".join(f"{n.host}:{n.sql_port}" for n in survivors)

    if engine == "postgresql":
        payload = (
            f"{SUDO} rm -f {PG_RESTART_OVERRIDE} && "
            f"{SUDO} systemctl daemon-reload && "
            f"{SUDO} systemctl start patroni"
        )
    else:
        payload = (
            f"TS_IP=$(tailscale ip -4); {SUDO} cockroach start --insecure "
            "--store=/var/lib/cockroach "
            "--listen-addr=$TS_IP:26257 --advertise-addr=$TS_IP:26257 "
            f"--locality={node.locality} "
            # Must match the peers' memory flags, or the caches differ.
            "--cache=0.25 --max-sql-memory=0.25 "
            f"--join={join} --background </dev/null >/dev/null 2>&1"
        )

    started = time.monotonic()
    try:
        result = ssh.run(node, payload, timeout=60)
        launched = result.returncode == 0
        detail = f"rc={result.returncode}"
        stderr = (result.stderr or "").strip()
        if stderr:
            detail = f"{detail}: {stderr.splitlines()[0]}"
    except Exception as exc:
        launched = False
        detail = f"{type(exc).__name__}: {exc}"

    expected = len(topo.nodes)
    live = 0
    while time.monotonic() - started < timeout_s:
        time.sleep(poll_interval_s)
        try:
            status = ssh.run(
                witness,
                f"cockroach node status --insecure --host={witness.host}:{witness.sql_port} "
                "--format=csv 2>/dev/null | tail -n +2 | wc -l"
                if engine != "postgresql"
                else f"curl -s -o /dev/null -w '%{{http_code}}' http://{node.host}:8008/health",
                timeout=60,
            )
            if engine == "postgresql":
                live = expected if status.stdout.strip() == "200" else 0
            else:
                live = int((status.stdout or "0").strip() or 0)
        except Exception:
            live = 0
        if live >= expected:
            break

    rejoined = live >= expected
    waited = round(time.monotonic() - started, 1)
    print(
        f"  restore {node.host}: {'rejoined' if rejoined else 'DID NOT REJOIN'} "
        f"({live}/{expected} live after {waited:.0f}s); {detail}",
        flush=True,
    )
    return {
        "attempted": True,
        "launched": launched,
        "rejoined": rejoined,
        "nodes_live": live,
        "nodes_expected": expected,
        "waited_s": waited,
        "witness": witness.name,
        "detail": detail,
        "at_utc": utcnow(),
    }


def inject_fault(node: Node, mode: str, engine: str) -> dict[str, Any]:
    """Apply the fault and return when it was applied.

    The timestamp is taken before the call. A transport error is not failure:
    a ``dead`` fault often kills the connection it arrived on.
    """
    at_utc = utcnow()
    at_monotonic = time.monotonic()
    # None: transport died (for ``dead``, likely success); else the exit status.
    landed: bool | None = None
    stderr = ""
    try:
        result = ssh.run(node, get_payload(mode, engine), timeout=10)
        detail = f"rc={result.returncode}"
        stderr = (result.stderr or "").strip()
        landed = result.returncode == 0
        if stderr:
            detail = f"{detail}: {stderr.splitlines()[0]}"
    except Exception as exc:
        detail = f"transport error after dispatch: {type(exc).__name__}"
    return {
        "target": node.name,
        "host": node.host,
        "mode": mode,
        "at_utc": at_utc,
        "at_monotonic": at_monotonic,
        "detail": detail,
        "landed": landed,
        "stderr": stderr,
    }


#: How far (in write cadences) the last acknowledgement may fall short of the
#: run's end before the audit writer counts as having stopped observing.
COVERAGE_SLACK_CADENCES = 10.0


def availability_rto(
    attempts: list[tuple[float, int, str]],
    fault_monotonic: float,
    observation_end: float | None = None,
) -> dict[str, Any]:
    """Time from the fault until the database accepted a write again.

    Distinct from the throughput-based :func:`find_recovery`: a cluster can
    accept writes again within seconds but never regain its old throughput.
    The outage is the largest gap between acknowledged writes that closes after
    the fault and exceeds the healthy noise floor. Resolution is bounded by the
    audit cadence (~one quorum write) and is returned alongside.

    With ``observation_end``, coverage is checked: if acknowledgements stop well
    before the run ends, no RTO is stated (unmeasured, not zero).
    """
    acked = [t for t, _, outcome in attempts if outcome == "ack"]
    before = [t for t in acked if t < fault_monotonic]
    after = [t for t in acked if t >= fault_monotonic]

    gaps = [b - a for a, b in zip(acked, acked[1:])] if len(acked) > 1 else []
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else None

    # Coverage is judged on the last acknowledgement: a writer blocked on a
    # black-holed socket keeps "attempting" while observing nothing.
    last_attempt = max((t for t, _, _ in attempts), default=None)
    last_acked = max(acked, default=None)
    coverage: dict[str, Any] = {
        "last_ack_offset_s": (
            round(last_acked - fault_monotonic, 3) if last_acked is not None else None
        ),
        "last_attempt_offset_s": (
            round(last_attempt - fault_monotonic, 3) if last_attempt is not None else None
        ),
        "observation_end_offset_s": (
            round(observation_end - fault_monotonic, 3)
            if observation_end is not None
            else None
        ),
    }
    coverage_gap = (
        (observation_end - last_acked)
        if (observation_end is not None and last_acked is not None)
        else None
    )
    slack = (typical_gap or 0.0) * COVERAGE_SLACK_CADENCES
    truncated = coverage_gap is not None and coverage_gap > slack
    coverage["coverage_gap_s"] = round(coverage_gap, 3) if coverage_gap is not None else None
    coverage["coverage_truncated"] = truncated if coverage_gap is not None else None

    if not after:
        return {
            "availability_rto_s": None,
            "detail": "no write was acknowledged after the fault",
            "writes_acknowledged_after_fault": 0,
            "resolution_s": round(typical_gap, 4) if typical_gap else None,
            **coverage,
        }

    first_after = min(after)
    last_before = max(before) if before else None

    # Largest qualifying gap, not the first write after the fault: a partition
    # may take seconds to bite. The floor uses only pre-fault gaps.
    acked_gaps = [(a, b, b - a) for a, b in zip(acked, acked[1:])]
    healthy = [gap for _, b, gap in acked_gaps if b < fault_monotonic]
    floor = (max(healthy) + typical_gap) if (healthy and typical_gap) else None

    outage = None
    if floor is not None:
        qualifying = [
            (a, b, gap)
            for a, b, gap in acked_gaps
            if b >= fault_monotonic and gap > floor
        ]
        if qualifying:
            outage = max(qualifying, key=lambda t: t[2])

    if outage is not None:
        _, gap_end, gap_len = outage
        rto = gap_end - fault_monotonic
        write_gap = gap_len
    elif truncated:
        # The acknowledgements stop early and no gap closed: length unmeasured.
        return {
            "availability_rto_s": None,
            "detail": (
                "the last acknowledged write was "
                f"{coverage['last_ack_offset_s']}s after the fault and none "
                f"followed for the remaining {coverage['coverage_gap_s']}s of "
                "the run, so the interruption never closed while it was being "
                "observed; its length is unmeasured, not zero"
            ),
            "write_gap_s": None,
            "writes_acknowledged_after_fault": len(after),
            "resolution_s": round(typical_gap, 4) if typical_gap else None,
            "detection_floor_s": round(floor, 4) if floor is not None else None,
            "outage_observed": None,
            **coverage,
        }
    else:
        # No detectable interruption: report the interval to the next ack.
        rto = first_after - fault_monotonic
        write_gap = (first_after - last_before) if last_before else None

    return {
        "availability_rto_s": round(rto, 3),
        # Outage in the write stream; the last pre-fault ack may predate the fault.
        "write_gap_s": round(write_gap, 3) if write_gap is not None else None,
        "writes_acknowledged_after_fault": len(after),
        "resolution_s": round(typical_gap, 4) if typical_gap else None,
        "detection_floor_s": round(floor, 4) if floor is not None else None,
        "outage_observed": outage is not None,
        **coverage,
    }


def clock_offsets(observed_at: dict[float, float]) -> dict[str, Any]:
    """Summarise the offset between the generator's clock and the harness's.

    ``observed_at`` maps generator ``elapsed`` to harness-clock offset. Their
    difference (~5 s of SSH and process startup) is reported with its spread; a
    small spread shows the clocks run at the same rate.
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

    ``ticks`` is ``(offset_s, total_tps)``. Returns the *start* of the first
    window of ``hold_s`` seconds at or above ``baseline_tps * threshold``.
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


@contextmanager
def _optional(resource):
    """Enter ``resource`` if it is not ``None``."""
    if resource is None:
        yield None
        return
    with resource as entered:
        yield entered


def run(
    settings: Settings,
    profile: Profile,
    mode: str,
    database: str = "ycsb",
    audit_database: str = "bench",
    engine: str = "cockroachdb",
) -> tuple[RunDirectory, dict[str, Any]]:
    """Drive a steady-state workload, inject a fault, and measure RTO and RPO."""
    spec = profile.workload
    chaos = profile.chaos
    topo = settings.topology
    gateway = CLIENT_NODE
    report = preflight.PreflightReport()
    if engine == "postgresql":
        # Switch the primary back to the gateway first so the fault hits the same
        # node on both engines; the window waits for the candidate to be eligible.
        placement = preflight.check_patroni_primary_placement(
            report, topo, settle_timeout_s=chaos.leaseholder_settle_s
        )
        # Resolved live; a failed switchover has already failed the report.
        fault_target = resolve_patroni_primary(topo)
        if placement.passed and fault_target.name != chaos.target:
            print(
                f"  note: profile names {chaos.target!r} as chaos.target, but "
                f"{fault_target.name!r} is the Patroni primary right now; "
                "faulting the actual primary"
            )
    else:
        fault_target = topo.get(chaos.target)

    if fault_target.host == gateway.host:
        raise ValueError(
            f"chaos target {fault_target.name!r} is the gateway the workload is driven "
            "from; the fault would remove the measurement apparatus along with "
            "the node under test"
        )

    preflight.check_clock_offset(report, [gateway, fault_target])
    check_fault_authorisation(report, fault_target, mode, engine)
    if chaos.probe_enabled:
        # A probe failing at its first write would look like a total outage.
        ok, detail = check_agent_prerequisites(gateway)
        report.add("probe_agent_ready", ok, detail, node=gateway.name)
    if engine == "cockroachdb":
        preflight.check_leaseholder_placement(
            report,
            topo.gateway,
            database,
            topo.gateway.region,
            # Chaos runs may follow a fault the harness itself just injected.
            settle_timeout_s=chaos.leaseholder_settle_s,
        )
    else:
        report.add(
            "patroni_primary_resolved",
            True,
            f"resolved {fault_target.name} ({fault_target.host}) as the current "
            "Patroni primary via its REST API immediately before scheduling "
            "the fault",
            target=fault_target.name,
        )
    report.raise_if_failed()

    if engine == "postgresql":
        # Generator via HAProxy (one URL); audit writer and probe connect directly
        # (multi-host DSN) so a proxy hiccup cannot pose as an outage.
        workload_uri = pg_generator_dsn(database, settings.pg_password)
        audit_dsn = pg_direct_dsn(topo, audit_database, settings.pg_password)
    else:
        # One URL on a non-target node (multi-URL dials serially and slowly).
        # Audit writer and probe use a multi-host DSN over every node.
        admin_node = next((n for n in topo.nodes if n.name != fault_target.name), topo.gateway)
        workload_uri = (
            f"postgresql://root@{admin_node.host}:{admin_node.sql_port}/"
            f"{database}?sslmode=disable"
        )
        hosts_ports = ",".join(f"{node.host}:{node.sql_port}" for node in topo.nodes)
        audit_dsn = f"postgresql://root@{hosts_ports}/{audit_database}?sslmode=disable"

    # Separate tables so the RPO sequence and RTO canary are independent readings.
    canary_ddl = (
        f"DROP TABLE IF EXISTS {chaos.probe_table}; "
        + CREATE_TABLE_SQL.format(table=chaos.probe_table)
        + ";"
    )
    if engine == "postgresql":
        ssh.run(
            gateway,
            f"PGPASSWORD={shlex.quote(settings.pg_password)} "
            f"psql -h 127.0.0.1 -p 5000 -U root -d {audit_database} "
            '-c "DROP TABLE IF EXISTS rpo_audit; '
            'CREATE TABLE rpo_audit (seq_id INT8 PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now()); '
            f'{canary_ddl}"',
            timeout=60,
        )
    else:
        ssh.run(
            gateway,
            f"cockroach sql --insecure --host={admin_node.host}:{admin_node.sql_port} --database={audit_database} "
            '-e "DROP TABLE IF EXISTS rpo_audit; '
            'CREATE TABLE rpo_audit (seq_id INT8 PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now()); '
            f'{canary_ddl}"',
            timeout=60,
        )

    run_dir = RunDirectory(settings.runs_dir, new_run_id(f"p4-chaos-{mode}"))
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase="p4_chaos",
        engine=engine,
        profile=profile.to_dict(),
        clock_epoch_utc=None,
        topology=[
            {"name": gateway.name, "host": gateway.host, "role": "generator, audit endpoint"},
            {"name": fault_target.name, "host": fault_target.host, "role": f"chaos target ({mode})"},
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
    )
    # Server flags and hardware, read by `validation.check_run_comparability`.
    server = preflight.capture_server_config(topo.gateway, engine=engine)
    manifest.server_version = server.get("version")
    if engine == "cockroachdb":
        manifest.cockroach_version = server.get("version")
    else:
        manifest.note("engine: postgresql (patroni HA)")
    manifest.note(f"server: {server.get('start_command', '')}")
    manifest.note(f"host: {preflight.format_hardware(server.get('hardware', {}))}")
    if server.get("memory"):
        manifest.note(f"pg memory: {preflight.format_pg_memory(server['memory'])}")
    payload = get_payload(mode, engine)
    manifest.note(f"fault scheduled for {profile.chaos.inject_at_s}s: {payload}")
    manifest.note(
        f"rto probe: {'enabled' if chaos.probe_enabled else 'DISABLED'}, "
        f"{chaos.probe_workers} worker(s) at {chaos.probe_interval_s * 1000:.0f} ms "
        f"dispatch into {audit_database}.{chaos.probe_table}"
    )

    run_duration_s = generator_duration_s(chaos, mode)
    if run_duration_s > chaos.duration_s:
        after = (
            f"the partition heals at {chaos.inject_at_s + RECOVER_HEAL_DELAY_S}s"
            if mode == "recover"
            else f"a fault at {chaos.inject_at_s}s"
        )
        manifest.note(
            f"generator run extended from {chaos.duration_s}s to {run_duration_s}s "
            f"to keep {chaos.min_post_fault_s}s of observation after {after}"
        )

    generator = (
        f"cockroach workload run {spec.generator} "
        f"--workload={spec.ycsb_workload} --seed={spec.seed} "
        f"--insert-count={spec.insert_count} "
        f"--request-distribution={spec.request_distribution} "
        f"--read-freq={spec.read_freq} --update-freq={spec.update_freq} "
        f"--concurrency={chaos.concurrency} --duration={run_duration_s}s "
        # Without this the generator exits at the fault. Not used for bench runs,
        # where an error should fail the run.
        f"--tolerate-errors "
        f"--display-every={spec.display_every_s}s '{workload_uri}'"
    )
    manifest.generator_command = generator

    events: dict[str, Any] = {"mode": mode, "target": fault_target.name}
    series: list[tuple[float, float]] = []
    injected: dict[str, Any] = {}
    first_error_at: float | None = None

    def timer(t_zero: float) -> None:
        """Inject the fault ``inject_at_s`` seconds after the generator's first sample.

        Timed on the monotonic clock, never by counting samples. Anchoring on the
        first sample means connection setup cannot eat into the pre-fault baseline.
        """
        # Bounded, so a generator that never produces a sample fails loudly.
        setup_budget_s = run_duration_s
        if not first_sample_seen.wait(timeout=setup_budget_s):
            print(
                f"  ERROR: the generator produced no sample within "
                f"{setup_budget_s:.0f}s; the fault was NOT injected and this run "
                "measures nothing",
                flush=True,
            )
            injected.update(
                {
                    "target": fault_target.name,
                    "host": fault_target.host,
                    "mode": mode,
                    "at_utc": None,
                    "at_monotonic": None,
                    "detail": "not injected: generator never reached steady state",
                    "landed": False,
                    "stderr": "",
                }
            )
            return
        if stop_timer.is_set():
            return

        origin = steady_state_at[0]
        deadline = origin + chaos.inject_at_s
        while (remaining := deadline - time.monotonic()) > 0:
            if stop_timer.wait(min(remaining, 0.25)):
                return
        injected.update(inject_fault(fault_target, mode, engine))
        injected["at_offset_s"] = round(injected["at_monotonic"] - t_zero, 3)
        # Steady state that preceded the fault (what `inject_at_s` promises).
        injected["at_steady_state_offset_s"] = round(
            injected["at_monotonic"] - origin, 3
        )
        print(
            f"  [{injected['at_offset_s']:6.1f}s] fault injected on {fault_target.host} "
            f"({mode}); {injected['detail']} "
            f"({injected['at_steady_state_offset_s']:.1f}s into steady state)",
            flush=True,
        )

    stop_timer = threading.Event()
    # Set on the generator's first interval: the injection timer's origin.
    first_sample_seen = threading.Event()
    steady_state_at: list[float] = [0.0]
    parser = WorkloadParser(strict=True)
    samples: list[Sample] = []
    # elapsed_s -> harness-clock offset when that interval was observed.
    observed_at: dict[float, float] = {}
    raw_path = run_dir.raw(f"chaos_{mode}.txt")

    print(
        f"  running {run_duration_s}s at C={chaos.concurrency}, injecting at "
        f"{chaos.inject_at_s}s",
        flush=True,
    )

    # One epoch (monotonic + UTC, taken together) for every file in the run;
    # the remote probe's offsets are rebased onto it.
    t_zero = time.monotonic()
    t_zero_utc = utcnow_us()
    probe = (
        RemoteRtoProbe(
            gateway,
            audit_dsn,
            package_root=PACKAGE_ROOT,
            duration_s=run_duration_s + PROBE_OVERRUN_S,
            table=chaos.probe_table,
            interval_s=chaos.probe_interval_s,
            workers=chaos.probe_workers,
            statement_timeout_ms=chaos.probe_statement_timeout_ms,
            connect_timeout_s=chaos.probe_connect_timeout_s,
            epoch_monotonic=t_zero,
            epoch_utc=t_zero_utc,
            log_path=run_dir.probe_log,
        )
        if chaos.probe_enabled
        else None
    )

    # Every node, including the client, from t_zero so the pre-fault baseline is captured.
    hw_sampler = (
        HardwareMetricsSampler(
            list(topo.nodes) + [gateway],
            t_zero,
            interval_s=profile.hardware_metrics.sample_interval_s,
        )
        if profile.hardware_metrics.enabled
        else None
    )

    with _optional(hw_sampler):
        with _optional(probe):
            with AuditWriter(audit_dsn, chaos.audit_interval_s) as audit:
                events["t_start_utc"] = t_zero_utc
                manifest.clock_epoch_utc = t_zero_utc
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
                            if not first_sample_seen.is_set():
                                steady_state_at[0] = arrived
                                first_sample_seen.set()
                            observed_at[tick.elapsed_s] = offset
                            series.append((offset, tick.total_tps))
                            if tick.errors_cum > 0 and first_error_at is None:
                                first_error_at = offset
                                events["t_first_error_offset_s"] = round(offset, 3)
                            if int(tick.elapsed_s) % 15 == 0:
                                print(
                                    f"  [{offset:6.1f}s] tps={tick.total_tps:8.1f} "
                                    f"errors={tick.errors_cum}",
                                    flush=True,
                                )

                stop_timer.set()
                first_sample_seen.set()
                timer_thread.join(timeout=5)

    if hw_sampler is not None:
        hw_sampler.write(run_dir.hardware_metrics_csv)
        failed = {n: c for n, c in hw_sampler.scrape_failures.items() if c}
        if failed:
            manifest.note(f"hardware metrics: scrape failures {failed}")

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
        availability_rto(
            audit.attempts,
            injected["at_monotonic"],
            observation_end=audit.stopped_at or audit.last_attempt_started,
        )
        if injected.get("at_monotonic") is not None
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

    probe_summary: dict[str, Any] = {"enabled": probe is not None}
    if probe is not None:
        # Completion order, which differs from seq_id order with several in flight.
        attempts_in_order = sorted(probe.attempts, key=lambda a: a.complete_offset_s)
        with MetricsWriter(run_dir.probe_csv, PROBE_COLUMNS) as probe_log:
            for attempt in attempts_in_order:
                probe_log.write(attempt.to_row())
        probe_summary.update(probe.summary())
        probe_summary["error"] = probe.error
        probe_summary["log"] = run_dir.probe_log.name
        probe_summary["attempts_csv"] = run_dir.probe_csv.name
        probe_observation_end = (
            (audit.stopped_at - t_zero) if audit.stopped_at is not None else None
        )
        probe_summary["rto"] = (
            probe.rto(injected["at_offset_s"], probe_observation_end)
            if injected.get("at_offset_s") is not None
            else {"measurable": False, "detail": "fault was never injected"}
        )

    clock = clock_offsets(observed_at)
    fault_offset_s = injected.get("at_offset_s")
    generator_start_s = clock.get("generator_start_offset_s")
    if fault_offset_s is not None and generator_start_s is not None and generator_start_s >= fault_offset_s:
        # The fault preceded any operation, so there is no baseline to recover to.
        print(
            f"  WARNING: the generator's first sample arrived at {generator_start_s:.1f}s, "
            f"*after* the fault was injected at {fault_offset_s:.1f}s. The throughput-based "
            "RTO/degradation-profile in this run is not measuring recovery from steady "
            "state and should not be trusted -- investigate why connection setup took "
            "this long before using this run's figures."
        )
        manifest.note(
            f"fault injected at {fault_offset_s:.1f}s but the generator's first sample "
            f"did not arrive until {generator_start_s:.1f}s; throughput-based recovery "
            "figures from this run are not measurements of recovery from steady state"
        )

    if injected.get("landed") is False:
        # A clean non-zero exit proves the fault did not land.
        print(
            f"  ERROR: the {mode!r} fault on {fault_target.host} did not land "
            f"({injected['detail']}). Every RTO/RPO figure in this run describes "
            "an UNDISTURBED cluster and must not be quoted as a resilience "
            "result.",
            flush=True,
        )
        manifest.note(
            f"FAULT DID NOT LAND: {injected['detail']}. The target kept serving "
            "throughout; RTO/RPO figures from this run measure an undisturbed "
            "cluster and are not resilience results"
        )

    events.update(
        {
            "fault_landed": injected.get("landed"),
            "clock": clock,
            "injected": injected,
            "availability": avail,
            "probe": probe_summary,
            "baseline_tps": round(baseline_tps, 2),
            "recovery_threshold": chaos.recovery_threshold,
            "recovery_floor_tps": round(baseline_tps * chaos.recovery_threshold, 2),
            "recovery_hold_s": chaos.recovery_hold_s,
            "t_recovered_offset_s": round(recovered_at, 3) if recovered_at is not None else None,
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
                    # Empty, never 0.0, if not observed live.
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

    # Measurement done. Restart a `dead` target so the testbed is usable again.
    if mode == "dead" and injected.get("landed") is not False:
        print(f"  restoring {fault_target.host} after the measurement", flush=True)
        events["restore"] = restore_target(fault_target, topo, engine)
        if not events["restore"].get("rejoined"):
            manifest.note(
                f"{fault_target.host} did not rejoin after the run "
                f"({events['restore'].get('detail')}); restart it before measuring again"
            )
    else:
        events["restore"] = {
            "attempted": False,
            "detail": (
                f"recover mode heals its own fault after {RECOVER_HEAL_DELAY_S}s"
                if mode == "recover"
                else "the fault did not land, so there is nothing to restore"
            ),
        }

    manifest.finished_utc = utcnow()
    manifest.validation = {"preflight": report.to_dict()}
    run_dir.write_manifest(manifest)
    run_dir.write_events(events)
    run_dir.write_preflight(report.to_dict())
    return run_dir, events

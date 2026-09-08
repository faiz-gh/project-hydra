"""Phases III-IV: fault injection, and the measurement of RTO and RPO.

Phase III is the `recover` fault (a healable network partition); Phase IV is
`dead` (the process is killed outright and stays down). Both modes share this
one module because the sweep, the injection scheduling, and the RTO/RPO
measurement are identical between them -- only the fault payload
(:func:`get_payload`) and its recoverability differ.

A steady-state workload is driven from the dedicated client node against the
cluster while a fault is injected into the primary at a fixed offset. Two
quantities are measured:

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

**RTO is measured by a third client, not by either of the first two.** The
generator answers "when did throughput come back" at one sample a second; the
audit writer answers "what did the client lose" at the pace of one serialised
quorum write, ~14 a second. Neither can time a recovery to better than about a
tenth of a second, and the audit writer's own docstring says so.
:class:`crdblab.core.rto_probe.RtoProbe` runs alongside both, on its own threads,
its own connections and its own table, holding several canary writes in flight so
that the interval between observations is the write cost *divided by* the pool
size. It is additive in every direction: the RPO series is untouched and paced
exactly as its recorded runs were, ``audit.csv`` still carries the availability
figure derived from it, and a probe that fails is recorded as a failed probe
rather than as a failed run.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..config import Profile, Settings
from ..core import preflight, ssh
from ..core.recorder import (
    AUDIT_COLUMNS,
    COLUMNS,
    PROBE_COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
)
from ..core.rto_probe import CREATE_TABLE_SQL, RtoProbe
from ..core.workload import PERIODIC, Sample, WorkloadParser, group_timed_ticks
from ..topology import CLIENT_NODE, Node, Topology

#: Fault modes. ``dead`` removes the process outright; ``recover`` severs the
#: node's overlay network for a period and then restores it, which exercises the
#: heal path rather than only the detection path.
MODES = ("dead", "recover")

#: Every fault payload is privileged, and on most of this testbed the SSH user
#: is *not* root: ``crdb-gcp-1`` and the Azure nodes are reached as ``ubuntu``
#: while ``cockroach``/``patroni`` run as root and ``tailscale down`` needs the
#: daemon socket. Without this prefix ``killall -9 cockroach`` returns
#: ``Operation not permitted`` (rc=1) and ``tailscale down`` returns
#: ``Access denied`` -- in both cases the node under test carries on serving and
#: the run silently measures a fault that never happened. That is exactly what
#: the 2026-09-07/08 chaos runs recorded (``"detail": "rc=1"``, and the target's
#: ``cockroach`` pid unchanged across the whole run). ``-n`` keeps it
#: non-interactive: if passwordless sudo is not available we want a hard,
#: immediate failure rather than a hung prompt eating the injection window.
SUDO = "sudo -n"


def get_payload(mode: str, engine: str) -> str:
    if mode == "dead":
        procs = "patroni postgres" if engine == "postgresql" else "cockroach"
        return f"{SUDO} killall -9 {procs}"
    elif mode == "recover":
        # The partition has to outlive the SSH connection that delivered it --
        # `tailscale down` severs the overlay this very session is riding on --
        # so the heal half must be detached. `setsid` + `nohup` keep it alive
        # once sshd tears the session down.
        return (
            f"{SUDO} nohup setsid bash -c "
            f"'tailscale down && sleep 45 && tailscale up' >/dev/null 2>&1 &"
        )
    raise ValueError(f"Unknown mode: {mode}")


def preflight_payload(mode: str, engine: str) -> str:
    """A privilege probe with the same authorisation requirements as the fault.

    ``recover``'s real payload is backgrounded, so its exit status reports only
    that the shell forked -- it is *structurally* incapable of telling us the
    fault landed (this is why a denied ``tailscale down`` went unnoticed for
    three runs). The only way to know the injection will be permitted is to ask
    before the measurement starts, with a command that needs the same rights but
    changes nothing. ``killall -0`` signals nothing and still returns
    ``Operation not permitted`` when it may not signal; ``tailscale status``
    needs the same daemon access ``tailscale down`` does.
    """
    if mode == "dead":
        procs = "patroni postgres" if engine == "postgresql" else "cockroach"
        return f"{SUDO} killall -0 {procs}"
    elif mode == "recover":
        return f"{SUDO} tailscale status --json >/dev/null"
    raise ValueError(f"Unknown mode: {mode}")


#: Patroni's own REST endpoint, port 8008, answers 200 on the primary and a
#: non-2xx status everywhere else -- the same check
#: ``terraform/scripts/bootstrap-client.tftpl`` configures HAProxy's
#: ``patroni_primary`` backend to poll. Unlike CockroachDB, nothing in
#: ``bootstrap-patroni.tftpl`` biases which node wins Patroni's etcd-based
#: leader election, so a profile's static ``chaos.target`` cannot be trusted to
#: name the primary the way it can for CockroachDB (where
#: ``preflight.check_leaseholder_placement`` asserts the gateway holds the
#: lease). The primary is therefore resolved here, live, immediately before the
#: fault is scheduled, rather than assumed from configuration.
PATRONI_PRIMARY_PORT = 8008
PATRONI_PRIMARY_TIMEOUT_S = 3.0


def resolve_patroni_primary(topo: Topology, timeout_s: float = PATRONI_PRIMARY_TIMEOUT_S) -> Node:
    """Query every cluster member's Patroni REST API and return the primary.

    Raises if zero or more than one node claims to be primary: zero means the
    cluster has no leader right now (mid-failover, or Patroni is down), and more
    than one means a split-brain the harness must not paper over by picking
    one arbitrarily.
    """
    import urllib.error
    import urllib.request

    primaries: list[Node] = []
    unreachable: list[str] = []
    for node in topo.nodes:
        url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/primary"
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                if response.status == 200:
                    primaries.append(node)
        except (urllib.error.URLError, OSError) as exc:
            unreachable.append(f"{node.name} ({exc})")

    if len(primaries) == 1:
        return primaries[0]
    if not primaries:
        raise ValueError(
            "no cluster member's Patroni REST API (port "
            f"{PATRONI_PRIMARY_PORT}) reports itself primary; the cluster may be "
            f"mid-failover or unreachable. Unreachable: {unreachable or 'none'}"
        )
    raise ValueError(
        "more than one cluster member's Patroni REST API reports itself "
        f"primary ({', '.join(n.name for n in primaries)}); this is a "
        "split-brain and the harness refuses to guess which one to fault"
    )


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


def check_fault_authorisation(
    report: preflight.PreflightReport,
    node: Node,
    mode: str,
    engine: str,
) -> None:
    """The fault must be *permitted* before the run is worth starting.

    This is a pre-flight check in the strict sense the README means: it asks
    "was the system fit to be measured?" rather than "what did we measure?" A
    chaos run whose injection is denied still produces a full run directory --
    metrics, audit, probe, an RTO of "no interruption detectable" -- and every
    one of those numbers is a measurement of an undisturbed cluster. It reads as
    a flatteringly good resilience result, which is the project's most dangerous
    failure mode, so it has to be caught before the measurement, not after.

    The probe changes nothing on the target: ``killall -0`` sends no signal and
    ``tailscale status`` only reads. Both fail the same way the real payload
    would if the SSH user cannot act as root.
    """
    probe = preflight_payload(mode, engine)
    try:
        result = ssh.run(node, probe, timeout=preflight.CONTROL_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
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


def inject_fault(node: Node, mode: str, engine: str) -> dict[str, Any]:
    """Apply the fault and return when it was applied.

    The timestamp is taken before the call and reported even when the transport
    fails, because a ``dead`` injection frequently kills the connection it
    arrived on: an SSH error here is evidence the fault landed, not that it
    did not.
    """
    at_utc = utcnow()
    at_monotonic = time.monotonic()
    #: ``None`` means "the transport died, which for a ``dead`` fault is
    #: evidence of success"; ``True``/``False`` mean the command actually
    #: reported an exit status and we know which.
    landed: bool | None = None
    stderr = ""
    try:
        result = ssh.run(node, get_payload(mode, engine), timeout=10)
        detail = f"rc={result.returncode}"
        stderr = (result.stderr or "").strip()
        landed = result.returncode == 0
        if stderr:
            detail = f"{detail}: {stderr.splitlines()[0]}"
    except Exception as exc:  # noqa: BLE001
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


@contextmanager
def _optional(resource):
    """Enter ``resource`` if there is one, and do nothing if there is not.

    So that disabling the probe changes one flag rather than duplicating the
    body of the run under an ``if``. Two copies of a measurement loop is how the
    runner and the evaluator came to disagree about the same run (D5).
    """
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
    if engine == "postgresql":
        # chaos.target names the intended primary for CockroachDB, where it is
        # pinned by lease_preferences and checked below -- but nothing pins
        # Patroni's leader, so the profile's static value cannot be trusted
        # here. Resolve who actually holds the lease live instead.
        fault_target = resolve_patroni_primary(topo)
        if fault_target.name != chaos.target:
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

    report = preflight.PreflightReport()
    preflight.check_clock_offset(report, [gateway, fault_target])
    check_fault_authorisation(report, fault_target, mode, engine)
    if engine == "cockroachdb":
        preflight.check_leaseholder_placement(report, topo.gateway, database, topo.gateway.region)
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
        workload_uri = f"postgresql://root@127.0.0.1:5000/{database}?sslmode=disable"
        audit_dsn = f"postgresql://root@127.0.0.1:5000/{audit_database}?sslmode=disable"
    else:
        # A single connection, not one per cluster member: `cockroach workload
        # run`, given more than one URL, dials its --concurrency connections
        # *serially* against the list rather than in parallel -- ~2.65s each,
        # measured on this topology, turning a sub-second connect into minutes
        # at any real concurrency. That delay once landed *after* this run's
        # fault-injection timer had already fired, so a chaos run's recorded
        # RTO measured a fault injected mid-connection-setup, before the
        # generator had sent a single operation. The single node chosen here
        # is never the fault target, so the generator does not need multi-host
        # tolerance to begin with -- it was never connected to the node that
        # dies. The audit/probe connections below are unaffected: they are
        # single psycopg connections each (or a small worker pool), not
        # --concurrency many, and libpq's own multi-host fallback is a
        # different, lighter-weight code path than the Go workload tool's.
        admin_node = next((n for n in topo.nodes if n.name != fault_target.name), topo.gateway)
        workload_uri = (
            f"postgresql://root@{admin_node.host}:{admin_node.sql_port}/"
            f"{database}?sslmode=disable"
        )
        hosts_ports = ",".join(f"{node.host}:{node.sql_port}" for node in topo.nodes)
        audit_dsn = f"postgresql://root@{hosts_ports}/{audit_database}?sslmode=disable"

    # Both tables are dropped and recreated, and they are two tables rather than
    # one. Sharing would put the RPO sequence and the RTO canary in the same
    # range under the same lease, so an outage of that one range would appear in
    # both series and the two measurements would stop being independent readings.
    canary_ddl = (
        f"DROP TABLE IF EXISTS {chaos.probe_table}; "
        + CREATE_TABLE_SQL.format(table=chaos.probe_table)
        + ";"
    )
    if engine == "postgresql":
        ssh.run(
            gateway,
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
        profile=profile.to_dict(),
        # Filled once the run's monotonic zero is taken, below.
        clock_epoch_utc=None,
        topology=[
            {"name": gateway.name, "host": gateway.host, "role": "generator, audit endpoint"},
            {"name": fault_target.name, "host": fault_target.host, "role": f"chaos target ({mode})"},
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
    )
    server = {}
    if engine == "cockroachdb":
        server = preflight.capture_server_config(topo.gateway)
        manifest.cockroach_version = server.get("version")
        manifest.note(f"server: {server.get('start_command', '')}")
        manifest.note(f"host: {preflight.format_hardware(server.get('hardware', {}))}")
    else:
        manifest.note(f"engine: postgresql (patroni HA)")
    payload = get_payload(mode, engine)
    manifest.note(f"fault scheduled for {profile.chaos.inject_at_s}s: {payload}")
    manifest.note(
        f"rto probe: {'enabled' if chaos.probe_enabled else 'DISABLED'}, "
        f"{chaos.probe_workers} worker(s) at {chaos.probe_interval_s * 1000:.0f} ms "
        f"dispatch into {audit_database}.{chaos.probe_table}"
    )

    generator = (
        f"cockroach workload run {spec.generator} "
        f"--workload={spec.ycsb_workload} --seed={spec.seed} "
        f"--insert-count={spec.insert_count} "
        f"--request-distribution={spec.request_distribution} "
        f"--read-freq={spec.read_freq} --update-freq={spec.update_freq} "
        f"--concurrency={chaos.concurrency} --duration={chaos.duration_s}s "
        f"--display-every={spec.display_every_s}s '{workload_uri}'"
    )
    manifest.generator_command = generator

    events: dict[str, Any] = {"mode": mode, "target": fault_target.name}
    series: list[tuple[float, float]] = []
    injected: dict[str, Any] = {}
    first_error_at: float | None = None

    def timer(t_zero: float) -> None:
        """Inject ``inject_at_s`` wall-clock seconds into *steady state*.

        The offset is still measured on a monotonic clock and never by counting
        samples -- that is D4, and it stays. What changed is only the *origin*.
        It used to be ``t_zero``, the moment the harness started; but ``t_zero``
        precedes the generator's connection-setup phase, and
        ``cockroach workload run`` spends anywhere from 0.2 s to 4m28s in
        ``creating load generator`` on this topology depending on how far the
        target is from the client node. When setup outruns ``inject_at_s`` the
        fault fires before the first sample exists, so there are no pre-fault
        intervals, ``baseline_tps`` is 0, the recovery floor is 0 and
        ``performance_rto_s`` comes back ``null`` -- which is exactly what the
        2026-09-07 and 2026-09-08 chaos runs recorded (setup 65 s and 268 s
        against a 60 s ``inject_at_s``).

        Anchoring to the first observed sample makes ``inject_at_s`` mean what
        the profile says it means: seconds of measured steady state before the
        fault. Counting samples would be D4; waiting for the stream to *begin*
        and then timing on the monotonic clock is not -- the schedule still
        cannot be distorted by the generator's line rate, only by when it
        started emitting at all, which is the thing we actually want to be
        relative to.
        """
        # Bounded so a generator that never produces a sample fails the run
        # loudly instead of hanging until the workload's own duration expires
        # with no fault injected at all.
        setup_budget_s = chaos.duration_s
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
        # Offset from the generator's first sample, i.e. how much steady state
        # actually preceded the fault. This is the number `inject_at_s` promises
        # and the one `baseline_tps` is computed over.
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
    #: Set the instant the generator's first interval arrives; the injection
    #: timer's origin. A one-element list because the timer thread reads it and
    #: the parser loop writes it.
    first_sample_seen = threading.Event()
    steady_state_at: list[float] = [0.0]
    parser = WorkloadParser(strict=True)
    samples: list[Sample] = []
    #: elapsed_s -> harness-clock offset at which that interval was observed.
    #: Kept so the metrics table can carry both clocks and the offset between
    #: them becomes a recorded observation rather than an inference.
    observed_at: dict[float, float] = {}
    raw_path = run_dir.raw(f"chaos_{mode}.txt")

    print(
        f"  running {chaos.duration_s}s at C={chaos.concurrency}, injecting at "
        f"{chaos.inject_at_s}s",
        flush=True,
    )

    # The probe's epoch is taken *before* it starts and is then handed to it, so
    # every offset it records shares an origin with events.json, audit.csv and
    # metrics.csv's wall_offset_s. Letting it take its own zero would put a fourth
    # clock in the run directory whose offset to the others nobody measured --
    # which is D5 exactly, and is why the epoch is a parameter and not a default.
    t_zero = time.monotonic()
    probe = (
        RtoProbe(
            audit_dsn,
            table=chaos.probe_table,
            interval_s=chaos.probe_interval_s,
            workers=chaos.probe_workers,
            statement_timeout_ms=chaos.probe_statement_timeout_ms,
            connect_timeout_s=chaos.probe_connect_timeout_s,
            log_path=run_dir.probe_log,
            epoch_monotonic=t_zero,
        )
        if chaos.probe_enabled
        else None
    )

    with _optional(probe):
        with AuditWriter(audit_dsn, chaos.audit_interval_s) as audit:
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
                        if not first_sample_seen.is_set():
                            # Steady state has begun; the injection timer starts
                            # counting from here, not from the harness's epoch.
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
            # Also release the timer if it is still blocked waiting for a first
            # sample that is never going to arrive, so the join below cannot
            # hang for the whole setup budget.
            first_sample_seen.set()
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
        # Ordered by completion, which is the order the observations were made
        # in. With several writes in flight that is not the order of seq_id, and
        # sorting by seq_id here would silently reorder the outage edges.
        attempts_in_order = sorted(probe.attempts, key=lambda a: a.complete_offset_s)
        with MetricsWriter(run_dir.probe_csv, PROBE_COLUMNS) as probe_log:
            for attempt in attempts_in_order:
                probe_log.write(attempt.to_row())
        probe_summary.update(probe.summary())
        probe_summary["error"] = probe.error
        probe_summary["log"] = run_dir.probe_log.name
        probe_summary["attempts_csv"] = run_dir.probe_csv.name
        probe_summary["rto"] = (
            probe.rto(injected["at_offset_s"])
            if injected.get("at_offset_s") is not None
            else {"measurable": False, "detail": "fault was never injected"}
        )

    clock = clock_offsets(observed_at)
    fault_offset_s = injected.get("at_offset_s")
    generator_start_s = clock.get("generator_start_offset_s")
    if fault_offset_s is not None and generator_start_s is not None and generator_start_s >= fault_offset_s:
        # This is not a warning about a slow generator; it means the RTO/RPO
        # figures below describe a fault injected before the generator ever
        # sent an operation, which happened for real once already (9 min of
        # serialised multi-host connection setup against a 60 s inject_at_s,
        # 2026-09-07). The throughput-based recovery detection has no pre-fault
        # baseline to fall from in that case, and any number it reports is not
        # a measurement of recovery.
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
        # The fault reported a clean non-zero exit: the command ran and refused.
        # Unlike a transport error (which for `dead` is evidence the fault
        # landed), this is positive proof it did not, so every resilience figure
        # below describes an undisturbed cluster. Recorded on the manifest so a
        # run like this cannot be mistaken for a good one after the fact -- the
        # 2026-09-08 `dead` run recorded `rc=1` and was otherwise
        # indistinguishable from a successful measurement.
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
            # A second, finer reading of the same quantity, from an independent
            # client. Recorded beside `availability` rather than replacing it:
            # the two are measured at different resolutions by different code,
            # and a disagreement between them is information, not noise.
            "probe": probe_summary,
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

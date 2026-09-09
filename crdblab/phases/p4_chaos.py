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
size. It runs on the *client node* rather than in this process
(:class:`crdblab.core.remote_probe.RemoteRtoProbe`): measured from the operator's
workstation a canary write cost 332 ms and the pool achieved 21 writes a second,
so the probe resolved 64 ms; from ``crdb-client-1`` the same code costs 123 ms
and achieves 59 a second, resolving 21 ms, and what remains is the cluster's own
cross-region quorum cost rather than the operator's uplink. It is additive in every direction: the RPO series is untouched and paced
exactly as its recorded runs were, ``audit.csv`` still carries the availability
figure derived from it, and a probe that fails is recorded as a failed probe
rather than as a failed run.
"""

from __future__ import annotations

import json
import shlex
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

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

#: Fault modes. ``dead`` removes the process outright; ``recover`` severs the
#: node's overlay network for a period and then restores it, which exercises the
#: heal path rather than only the detection path.
MODES = ("dead", "recover")

#: Root of this checkout, from which the probe agent's source files are copied
#: to the client node. Derived from this module's location so the agent is
#: always the revision the manifest records, never whatever happens to be
#: installed on the remote host.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]

#: The probe agent's dead-man switch, added to `chaos.duration_s`.
#:
#: It is NOT how long the probe is meant to run. The harness stops the agent by
#: closing the SSH channel when the measurement ends, and that is the normal
#: path; this bound only guarantees that an agent whose harness died cannot keep
#: writing to the cluster indefinitely. It is therefore generous rather than
#: tight: the probe starts at the run's epoch but the generator does not reach
#: steady state until it has finished connecting, which has taken as long as
#: 268 s on this topology, so a bound close to `duration_s` would kill the probe
#: before the workload it is observing had finished.
PROBE_OVERRUN_S = 600.0

#: Every fault payload is privileged; see ``core.ssh.SUDO`` for why, and for the
#: chaos runs that recorded a fault which never landed without it. Defined in
#: ``core.ssh`` because ``core.preflight`` needs it too, and re-exported here
#: because this module is where the fault payloads that use it live.
SUDO = ssh.SUDO


#: PostgreSQL's process death has to be arranged around systemd; CockroachDB's
#: does not. `cockroach` is started by cloud-init with `--background` and has no
#: unit, so `killall -9 cockroach` is simply the end of it. Patroni is a
#: packaged systemd service with `Restart=on-failure`, so a SIGKILL is a
#: *failure* by systemd's definition and the unit comes back within
#: `RestartSec` -- roughly 100 ms. A `dead` run against PostgreSQL would then
#: measure systemd's restart loop rather than the cluster's failover, and would
#: do so while reporting an RTO far better than CockroachDB's, on a fault that
#: was never comparable in the first place.
#:
#: `Restart=no` is installed first, as a **drop-in file** plus a
#: `daemon-reload`, and the kill is then delivered through `systemctl kill
#: --kill-who=all`, not `killall`.
#:
#: The drop-in is not stylistic. `systemctl set-property patroni.service
#: Restart=no` -- the obvious one-liner, and what this did first -- fails with
#: "Cannot set property Restart, or unknown property": `set-property` only
#: accepts properties that are settable on a *running* unit, which is
#: essentially the cgroup resource knobs, and `Restart=` is not one. Measured
#: against crdb-azure-1 on 2026-09-08: the command returned rc=1, the `&&`
#: short-circuited, and the primary went on serving. (The harness reported the
#: fault as not landed, which is what that check is for.) A drop-in under
#: /etc/systemd/system/ is the supported way, and `restore_target` deletes it.
#:
#: `systemctl kill` rather than `killall` matters twice over: nothing has to
#: guess the process name -- Patroni runs as `/usr/bin/python3 /usr/bin/patroni`,
#: so its `comm` is `python3` and `killall -9 patroni` finds nothing at all --
#: and a `pkill -f patroni` written to work around that matches the very SSH
#: command carrying it. Signalling the unit's cgroup has neither problem, and
#: takes postgres down with it since Patroni starts the postmaster as its child.
PG_RESTART_OVERRIDE = "/etc/systemd/system/patroni.service.d/99-crdblab-chaos.conf"

#: How long the ``recover`` partition lasts before it heals itself. This is not
#: a free parameter of the payload: it is the length of the outage the run has
#: to observe, so :func:`generator_duration_s` reads it too, and the two must
#: never be able to drift apart. Named for that reason rather than written twice.
RECOVER_HEAL_DELAY_S = 45


_PG_DEAD_PAYLOAD = (
    f"{SUDO} mkdir -p {PG_RESTART_OVERRIDE.rsplit('/', 1)[0]} && "
    f"printf '[Service]\\nRestart=no\\n' | {SUDO} tee {PG_RESTART_OVERRIDE} >/dev/null && "
    f"{SUDO} systemctl daemon-reload && "
    f"{SUDO} systemctl kill --kill-who=all --signal=SIGKILL patroni.service"
)


def get_payload(mode: str, engine: str) -> str:
    if mode == "dead":
        if engine == "postgresql":
            return _PG_DEAD_PAYLOAD
        return f"{SUDO} killall -9 cockroach"
    elif mode == "recover":
        # The partition has to outlive the SSH connection that delivered it --
        # `tailscale down` severs the overlay this very session is riding on --
        # so the heal half must be detached. `setsid` + `nohup` keep it alive
        # once sshd tears the session down.
        return (
            f"{SUDO} nohup setsid bash -c "
            f"'tailscale down && sleep {RECOVER_HEAL_DELAY_S} && tailscale up' "
            f">/dev/null 2>&1 &"
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
        if engine == "postgresql":
            # The same two rights the payload needs -- passwordless sudo over
            # systemctl, and write access to the unit directory the drop-in
            # goes in -- while changing nothing. A unit that does not exist, a
            # sudo that prompts, or a read-only /etc all fail here rather than
            # during the fault, which is the whole point of asking first.
            return (
                f"{SUDO} systemctl show patroni.service --property=MainPID && "
                f"{SUDO} test -w /etc/systemd/system"
            )
        return f"{SUDO} killall -0 cockroach"
    elif mode == "recover":
        return f"{SUDO} tailscale status --json >/dev/null"
    raise ValueError(f"Unknown mode: {mode}")


#: Patroni primary resolution lives in ``core.preflight`` because the pre-flight
#: gate needs it too, and ``core`` may not import ``phases``. Re-exported here
#: because this is where it was defined and where callers (and tests) look for
#: it -- there is exactly one implementation, not a copy on each side.
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
        #: Monotonic instant the observation window closed, set when the writer
        #: is stopped. :func:`availability_rto` compares the last attempt
        #: against it to tell "the cluster was quiet" from "this instrument
        #: stopped watching" -- two states that were indistinguishable in the
        #: artefact until 2026-09-09, when the second was reported as the first.
        self.stopped_at: float | None = None
        #: Monotonic instant the most recent attempt was *started*. A writer
        #: blocked inside `cur.execute` on a black-holed socket keeps this
        #: advancing past its last acknowledgement by the length of the block,
        #: which is how a stuck write is told from an abandoned one.
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
        # Taken after the join so it is genuinely the end of the window. The
        # join is bounded, so a thread still wedged on a black-holed socket does
        # not hold the run open -- it simply leaves a coverage gap, which is now
        # recorded rather than silently read as health.
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


def generator_duration_s(chaos: Any, mode: str = "dead") -> int:
    """How long the generator must run to leave a usable post-fault series.

    ``duration_s`` alone does not guarantee one. ``inject_at_s`` is measured
    from the generator's *first sample*, not from the harness epoch, so a
    profile that moves the injection later without lengthening the run silently
    shrinks the interval the run exists to observe -- and if it shrinks to
    nothing there is no recovery to find, only a collapse.

    **``recover`` mode needs a longer run than that arithmetic gives, and did
    not get one until 2026-09-09.** The run of that date
    (experiment-20260909T031334Z.log) was 45 s with the fault at 15 s: both
    independent instruments were still inside the outage when observation
    ended, and the harness correctly reported the RTO as UNMEASURED. The
    following run, at the length this function now returns, measured it at
    **37.6 s** after the fault -- so the old window was short by roughly eight
    seconds and no amount of instrument correctness could have recovered it.

    The bound is ``inject_at_s + RECOVER_HEAL_DELAY_S + min_post_fault_s``, and
    it is worth being precise about *why*, because the obvious reason is wrong.
    Recovery here is **not** gated on the partition lifting. The fault isolates
    one node; the other four keep quorum and elect a new primary, so writes
    resume at failover -- measured at 64.1 s on
    ``20260909T040914Z_p4-chaos-recover``, which is **7.4 s before** that run's
    partition healed at 71.5 s. What the heal delay actually buys is a
    conservative upper bound (failover has consistently been faster than it) and
    a second thing the next phase depends on: the demoted node is back on the
    network, and can therefore finish rewinding or re-cloning, in time for Phase
    IV's switchover to have a candidate at all. ``min_post_fault_s`` then covers
    the settling *after* whichever of the two happened last.

    Extended, never shortened: a profile asking for longer than the minimum
    keeps what it asked for.
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

    Called only after every observation is collected and written. That ordering
    is the whole safety argument: restarting the node is a change to the system
    under test, so it must not be able to move any number in the run directory.
    It is reported as its own event rather than folded into the fault's, because
    "the node came back" is not part of the measurement -- the run measured what
    happened while it was down.

    ``recover`` mode does not come through here: its payload heals itself after
    45 s, and restarting a node that was never stopped would be a second fault.

    Two things this has to get right, both learned the hard way on 2026-09-08:

    * **It needs ``sudo``.** ``/var/lib/cockroach`` is root-owned and the SSH
      user on the GCP and Azure nodes is ``ubuntu``, so an unprivileged
      ``cockroach start`` cannot open the store -- the same mistake that made
      ``killall -9 cockroach`` a no-op for three runs.
    * **Liveness must be read from a survivor.** ``run-experiment.sh`` polled
      ``cockroach node status`` on the gateway, which for ``chaos.target:
      gcp-1`` *is* the node that was just killed, so it always reported "0
      live" and always warned that the node had not rejoined -- even when it
      had.

    The remote redirections are not decoration either. ``--background`` forks
    and returns, but the forked process inherits the SSH channel's stdout and
    stderr, and ssh will not close a session while any process holds those
    pipes -- so without ``</dev/null >/dev/null 2>&1`` on the *remote* side the
    call blocks until the database exits. That cost a completed sweep ~50
    minutes on 2026-09-05.
    """
    survivors = [n for n in topo.nodes if n.name != node.name]
    if not survivors:
        return {"attempted": False, "detail": "no surviving node to rejoin or query"}
    witness = survivors[0]
    join = ",".join(f"{n.host}:{n.sql_port}" for n in survivors)

    if engine == "postgresql":
        # The fault installed a drop-in disabling systemd's restart so the kill
        # could not be undone; removing it is part of restoring the node, not an
        # extra courtesy -- left behind, the next `dead` run would measure a node
        # systemd had stopped supervising. `rm -f` and the reload are
        # unconditional so a re-run of the restore is harmless.
        payload = (
            f"sudo -n rm -f {PG_RESTART_OVERRIDE} && "
            "sudo -n systemctl daemon-reload && "
            "sudo -n systemctl start patroni"
        )
    else:
        payload = (
            "TS_IP=$(tailscale ip -4); sudo -n cockroach start --insecure "
            "--store=/var/lib/cockroach "
            "--listen-addr=$TS_IP:26257 --advertise-addr=$TS_IP:26257 "
            f"--locality={node.locality} "
            # Not optional: the default cache is 128 MiB, and starting the
            # restored node with different memory limits than its peers
            # reintroduces the block-cache asymmetry of D9 on the next run.
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
    except Exception as exc:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
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


#: How far short of the run's end an instrument's last observation may fall
#: before its silence is treated as loss of coverage rather than as a quiet
#: cluster, as a multiple of the observed inter-attempt cadence.
#:
#: A margin is needed because the last attempt legitimately precedes the end of
#: the window by up to one cadence, and the writer is stopped between attempts.
#: Ten cadences is far outside that and far inside the failure this catches: on
#: 2026-09-09 the audit writer went quiet 25 s before the run ended, against a
#: 0.39 s cadence -- roughly sixty-four cadences.
COVERAGE_SLACK_CADENCES = 10.0


def availability_rto(
    attempts: list[tuple[float, int, str]],
    fault_monotonic: float,
    observation_end: float | None = None,
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

    ``observation_end`` is when the measurement window closed, on the same clock
    as ``attempts``. It exists because **an instrument that stopped observing
    must not be reported as an instrument that observed nothing wrong.** Given
    it, this function checks that the attempt stream actually reaches the end of
    the run and refuses to state an RTO when it does not; given ``None`` -- a
    run recorded before the field existed -- the check is skipped rather than
    guessed at, so old runs read exactly as they did before.
    """
    acked = [t for t, _, outcome in attempts if outcome == "ack"]
    before = [t for t in acked if t < fault_monotonic]
    after = [t for t in acked if t >= fault_monotonic]

    gaps = [b - a for a, b in zip(acked, acked[1:])] if len(acked) > 1 else []
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else None

    # How much of the run this instrument actually watched.
    #
    # Judged on the last *acknowledgement*, not the last attempt, and that is
    # the load-bearing choice. A writer blocked inside a single `cur.execute` on
    # a black-holed socket has not stopped trying -- its next attempt eventually
    # resolves, minutes later, as `ambiguous` -- so "did it make an attempt
    # recently" answers yes across a window in which it observed nothing at all.
    # On 2026-09-09 the last acknowledgement landed 3.7 s after the fault and
    # the next attempt resolved 48 s later, after the run had already ended: one
    # observation in fifty seconds, from an instrument nominally sampling at
    # 2.5/s. Acknowledgements are what this function measures gaps between, so
    # they are what its coverage has to be measured in.
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

    # The interruption is the LARGEST gap in the acknowledged-write stream that
    # closes at or after the fault -- not the interval to the first write that
    # happened to be acknowledged after it. Those are the same number only when
    # the fault takes effect the instant the injection command returns, and it
    # frequently does not: `tailscale down` exits 0 while established flows keep
    # working for seconds afterwards. During that tail the audit writer is still
    # being served, so `first_after` is a few milliseconds and the function
    # concludes the database never stopped accepting writes.
    #
    # Measured on runs/20260908T232245Z_p4-chaos-recover: this reported an
    # availability RTO of 0.039 s across an interruption the independent RTO
    # probe puts at 68.96 s, because a write was acknowledged 39 ms after the
    # injection returned and the real outage did not open until ~3.5 s later.
    # The same defect, in the same run, as the probe's own first-gap selection
    # (see rto_probe.measure_rto) -- and it fails the same flattering way, so
    # the two artefacts corroborated each other's understatement instead of
    # catching it.
    #
    # The floor is characterised from gaps that closed *before* the fault, for
    # the same reason it is there: using the whole run would let the outage
    # raise the very threshold meant to detect it.
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
        gap_start, gap_end, gap_len = outage
        rto = gap_end - fault_monotonic
        write_gap = gap_len
    elif truncated:
        # No *closed* gap cleared the floor, but the acknowledgement stream
        # stops well before the end of the run: whatever gap opened at the last
        # acknowledgement never closed while anyone was watching. That is the
        # audit writer's counterpart of `measure_rto`'s `truncated` case, and it
        # is the branch that was missing on 2026-09-09. Falling through to the
        # `else` below reported the interval to the next acknowledged write --
        # 0.082 s -- for a fault the generator recorded as two consecutive ticks
        # of zero throughput. The last acknowledgement came 3.7 s after the
        # fault over a connection that was already black-holed, the writer then
        # blocked on one INSERT for 48 s, and nothing observed the intervening
        # 25 s of run at all.
        #
        # Whether the database stopped serving or the client stopped asking is
        # not decidable from this stream, and the honest report says so rather
        # than picking the flattering reading.
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
        # No gap distinguishable from the healthy cadence, over a window that
        # did reach the end of the run: the write stream was not observably
        # interrupted, and the honest figure is the interval to the next
        # acknowledged write.
        rto = first_after - fault_monotonic
        write_gap = (first_after - last_before) if last_before else None

    return {
        "availability_rto_s": round(rto, 3),
        # The observed outage in the write stream. Distinct from the RTO above:
        # the last pre-fault success may predate the fault by up to one cadence.
        "write_gap_s": round(write_gap, 3) if write_gap is not None else None,
        "writes_acknowledged_after_fault": len(after),
        # Anything below this is indistinguishable from no interruption at all.
        "resolution_s": round(typical_gap, 4) if typical_gap else None,
        "detection_floor_s": round(floor, 4) if floor is not None else None,
        "outage_observed": outage is not None,
        **coverage,
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
    report = preflight.PreflightReport()
    if engine == "postgresql":
        # Put the primary back on the designated node *before* choosing the
        # target, so that the fault lands on the same node CockroachDB's fault
        # lands on. Patroni never fails back on its own, so after Phase III the
        # primary is wherever that failover left it; without this repair Phase
        # IV would fault a different node than Phase III did, and a different
        # node than either CockroachDB phase did.
        # The window is for the *candidate* to become eligible, not for the
        # primary to drift back: after Phase III's partition the expected node
        # has a diverged timeline and has to be rewound or re-cloned before
        # Patroni will hand the leadership back to it. See the check's
        # docstring.
        placement = preflight.check_patroni_primary_placement(
            report, topo, settle_timeout_s=chaos.leaseholder_settle_s
        )
        # Still resolved live rather than assumed. The check above is what makes
        # the answer predictable; this is what makes it *true*. If the
        # switchover did not take, the check has already failed the report and
        # raise_if_failed() below stops the run -- this never silently faults
        # the wrong node.
        fault_target = resolve_patroni_primary(topo)
        # Only worth saying when the run is going to proceed. Printed
        # unconditionally it announced "faulting the actual primary" on the line
        # immediately before the placement failure aborted the sweep
        # (experiment-20260909T011615Z.log), which reads as a contradiction.
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
        # The clock check above already covers `gateway` (the client node), which
        # is what makes the agent's offsets convertible; this one asserts the
        # node can actually run the agent. Both are pre-flight because a probe
        # that fails at its first write looks like a total outage from the first
        # sample onward -- a flattering failure, and the kind this gate exists
        # to catch before it reaches a figure.
        ok, detail = check_agent_prerequisites(gateway)
        report.add("probe_agent_ready", ok, detail, node=gateway.name)
    if engine == "cockroachdb":
        preflight.check_leaseholder_placement(
            report,
            topo.gateway,
            database,
            topo.gateway.region,
            # Only the chaos phases pass a settle window: they are the only ones
            # that can run against a cluster still recovering from a fault the
            # harness itself injected moments earlier.
            settle_timeout_s=chaos.leaseholder_settle_s,
        )
    else:
        # The PostgreSQL counterpart, check_patroni_primary_placement, already
        # ran above -- it has to, because its repair decides which node becomes
        # the fault target. This records only what that target resolved to, and
        # deliberately no longer asserts a hardcoded True: the assertion is the
        # placement check's, and it can fail.
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
        # Credentialed, unlike the CockroachDB branch: Patroni's pg_hba is
        # `host all all 0.0.0.0/0 md5`, so every one of these -- generator,
        # audit writer, probe agent -- is refused without a password. All three
        # go through the client node's HAProxy on :5000, which is the only
        # endpoint that follows the leader through a failover.
        # The generator goes through HAProxy because it must be given exactly
        # one URL; the audit writer and the RTO probe do not, and must not --
        # they are the two clients whose whole job is to observe the cluster
        # through the fault, and routing them through one proxy on the client
        # node would make a hiccup there indistinguishable from an outage,
        # drop their in-flight connections at every failover
        # (`on-marked-down shutdown-sessions`), and give two deliberately
        # independent measurements a shared single point of failure. libpq
        # resolves the primary for them instead, which is exactly what the
        # CockroachDB branch below does with its own multi-host DSN.
        workload_uri = pg_generator_dsn(database, settings.pg_password)
        audit_dsn = pg_direct_dsn(topo, audit_database, settings.pg_password)
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
        # Filled once the run's monotonic zero is taken, below.
        clock_epoch_utc=None,
        topology=[
            {"name": gateway.name, "host": gateway.host, "role": "generator, audit endpoint"},
            {"name": fault_target.name, "host": fault_target.host, "role": f"chaos target ({mode})"},
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
    )
    # Capture server configuration for BOTH engines. It was CockroachDB-only,
    # which left every PostgreSQL run with no `server:` or `host:` note -- and
    # those two notes are exactly what `validation.check_run_comparability`
    # reads. The cross-engine comparison, the one thing this project exists to
    # produce, was therefore always drawn between a run whose machine was
    # recorded and one whose machine was not. The server's flags and version
    # are *expected* to differ across engines; the hardware is not, and it is
    # the half that D9 and the unexplained 22% shift of 2026-09-02 turned on.
    server = preflight.capture_server_config(topo.gateway, engine=engine)
    manifest.server_version = server.get("version")
    if engine == "cockroachdb":
        manifest.cockroach_version = server.get("version")
    else:
        manifest.note("engine: postgresql (patroni HA)")
    manifest.note(f"server: {server.get('start_command', '')}")
    manifest.note(f"host: {preflight.format_hardware(server.get('hardware', {}))}")
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
        # Without this the generator EXITS on its first failed statement, which
        # during a chaos run is the fault itself: the 2026-09-08 `dead` run
        # aborted 8s after injection with "result is ambiguous ... connection
        # refused (SQLSTATE 40003)", leaving three zero-throughput samples and
        # then nothing. Recovery is unobservable if the observer dies with the
        # cluster, so `performance_rto_s` could never be anything but null.
        #
        # Deliberately NOT set on the bench sweep. Nothing is supposed to fault
        # during a benchmark, so there an error must fail the run loudly rather
        # than be absorbed into a throughput average.
        f"--tolerate-errors "
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
        f"  running {run_duration_s}s at C={chaos.concurrency}, injecting at "
        f"{chaos.inject_at_s}s",
        flush=True,
    )

    # The probe's epoch is taken *before* it starts and is then handed to it, so
    # every offset it records shares an origin with events.json, audit.csv and
    # metrics.csv's wall_offset_s. Letting it take its own zero would put a fourth
    # clock in the run directory whose offset to the others nobody measured --
    # which is D5 exactly, and is why the epoch is a parameter and not a default.
    #
    # The UTC stamp is taken in the same breath as the monotonic one because the
    # probe now runs on the client node: a monotonic clock is meaningless across
    # machines, so the pair is what lets the agent's offsets be rebased onto this
    # run's timeline. Any delay between these two calls would be a systematic
    # error in that conversion, so they are adjacent and nothing sits between.
    t_zero = time.monotonic()
    t_zero_utc = utcnow_us()
    probe = (
        RemoteRtoProbe(
            gateway,
            audit_dsn,
            package_root=PACKAGE_ROOT,
            # A dead-man switch, not the intended lifetime: the probe is stopped
            # by the harness when the measurement ends. Generous on purpose --
            # see PROBE_OVERRUN_S.
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

    # All 5 cluster nodes plus the client node the generator/audit/probe run
    # from -- node_exporter is OS-level, polled the same way regardless of
    # which node ends up being the fault target. Started alongside the probe
    # and audit writer, on the same t_zero, so pre-fault baseline utilisation
    # is captured too.
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
                # The same instant `t_zero` was taken at, not a fresh one: this is
                # the origin the probe agent's offsets were rebased onto, and a
                # second, later stamp here would silently shift every figure drawn
                # against it.
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
        # The probe's offsets are on the run's clock (rebased by the epoch
        # skew), so the audit writer's stop instant converts directly and is the
        # same "when did the window close" both instruments are judged against.
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

    # Everything above is the measurement; everything below changes the system
    # again. `dead` leaves the target down by design -- the fault is real -- but
    # leaving it down also leaves the testbed unfit for the next run, and the
    # operator had to remember to restart it by hand. Done here, after every
    # artefact is derived and with the restart recorded as its own event, so a
    # reader can always tell what was measured from what was repaired.
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
                "recover mode heals its own fault after 45s"
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

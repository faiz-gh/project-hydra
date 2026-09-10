"""Assertions made before a measurement is trusted.

The validation layer (``analysis/validation.py``) inspects a recorded run for
internal consistency. It cannot detect a run whose numbers are arithmetically
sound but semantically empty, and two defects on record are of exactly that
kind: under D7 the cluster was correctly measured while misconfigured, and under
D8 the generator correctly measured operations that touched no data. Little's law
holds in both cases to better than one percent.

The checks here therefore address a different question. Validation asks whether
the recorded numbers are consistent with each other; pre-flight asks whether the
system was in a state worth measuring, and it asks *before* the measurement
rather than after, so that a bad run is never recorded in the first place.

Each check is derived from a physical invariant or a directly observable fact
rather than a threshold chosen by eye, and each returns its observed value so the
run manifest records what was actually seen and not merely that something passed.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..topology import Node, Topology
from . import ssh

#: CockroachDB refuses to run with a clock offset beyond half its
#: ``--max-offset`` (500 ms by default), and its hybrid-logical clock guarantees
#: degrade well before that. The threshold is the database's, not ours.
MAX_CLOCK_OFFSET_S = 0.25

#: A read or update that matches no row does no work; anything below this is a
#: broken keyspace alignment, not a slow cluster (D8).
MIN_ROW_MATCH_RATE = 0.99

#: Timeout for pre-flight *control-plane* SSH commands, in seconds.
#:
#: These commands are trivial -- read a clock, list zone configuration, read a
#: counter -- so their wall cost is dominated by SSH session setup across the
#: WAN, which is a property of the link on the day and not a constant. Measured
#: from the workstation to the gateway on 2026-09-02: 376 ms round trip,
#: and 6.2-6.9 s for a complete `ssh ... chronyc tracking`. The previous 20 s
#: budget on the clock check was about three times that, and a transient spike
#: duly exceeded it and refused an entire Phase II sweep.
#:
#: This value is therefore a *hang detector*, not a latency budget: it exists so
#: a wedged session cannot stall a sweep indefinitely, and nothing is weakened by
#: making it generous. It bounds no measurement -- every quantity that reaches a
#: figure is timed on the node by the generator or by the harness's own monotonic
#: clock, never by how long an SSH control command took.
CONTROL_TIMEOUT_S = 60


class PreflightError(RuntimeError):
    """Raised when the testbed is not in a state worth measuring."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str, **observed: Any) -> Check:
        check = Check(name, passed, detail, observed)
        self.checks.append(check)
        return check

    def raise_if_failed(self) -> None:
        failures = [c for c in self.checks if not c.passed]
        if failures:
            lines = "\n".join(f"  - {c.name}: {c.detail}" for c in failures)
            raise PreflightError(
                f"{len(failures)} pre-flight check(s) failed; refusing to "
                f"measure:\n{lines}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    **c.observed,
                }
                for c in self.checks
            ],
        }


# --- server configuration -------------------------------------------------

#: How to read the server's own argv and version, per engine. The hardware
#: block below is identical for both and is the half that matters most for
#: comparability: a cross-engine comparison is *supposed* to differ in server
#: version and flags, but it is not supposed to differ in the machine.
_SERVER_PROBES = {
    "cockroachdb": (
        "pgrep -a cockroach | head -1",
        "cockroach version --build-tag 2>/dev/null",
    ),
    # The postmaster is found by the path Patroni launches it with, because
    # the process is named `postgres` and so is every backend it forks --
    # `pgrep -a postgres | head -1` can return a backend's argv instead of the
    # server's. The `[p]` is not a typo and not decoration: `pgrep -f` searches
    # full command lines, including the one carrying this very probe, so the
    # plain pattern matches the ssh command itself and reports it as the server
    # (observed on crdb-gcp-1: `start_command` came back as this shell's own
    # `bash -c pgrep ...`). A bracket class matches the postmaster and not the
    # literal text of the pattern.
    #
    # The version comes from the binary rather than from a SQL
    # `SHOW server_version`, so capturing it needs no credentials and works on
    # a node that is not currently the primary.
    "postgresql": (
        "pgrep -a -f bin/[p]ostgres | head -1",
        # The `[p]` is repeated here for the same reason, and this is where it
        # was actually needed: the whole probe -- argv, version and hardware --
        # travels as one command line, so the literal `bin/postgres` inside
        # *this* glob was what `pgrep -f` kept matching, even after the pattern
        # above was bracketed. As a shell glob `[p]ostgres` still expands to the
        # same binary.
        (
            "for b in /usr/lib/postgresql/*/bin/[p]ostgres; do $b --version; done "
            "2>/dev/null | tail -1"
        ),
    ),
}


def capture_server_config(node: Node, engine: str = "cockroachdb") -> dict[str, Any]:
    """Record how the database server was actually started on ``node``.

    The run manifest previously described the client side in full -- profile,
    generator command, topology -- and the server side not at all. That gap hid a
    material confound: the Phase II baseline was started with ``--cache=0.25``
    while every cluster member took the 128 MiB default, roughly a fifteen-fold
    difference in block cache against a 205 MB working set. Both were healthy,
    both measured cleanly, and the Phase II/III comparison attributed the
    difference to Raft replication.

    Capturing the process arguments makes that class of asymmetry visible in the
    artefact rather than discoverable only by someone thinking to look. It is
    deliberately the raw argument list rather than a parsed subset: the next
    confound will involve a flag this function's author did not think to parse.

    That prediction came true one level down, and the hardware capture below is
    the response. Between the sweeps of 2026-09-02 the Phase II baseline fell
    from 3,505 to ~2,600 ops/s -- 22% -- with the profile, seed, generator,
    server version and every recorded server flag byte-identical across the two
    runs. Nothing in either manifest describes the machine the server ran on, so
    a redeployment onto a different instance type is indistinguishable in the
    artefact from a genuine regression. The argument list answered "how was the
    process started"; it could not answer "on what". Both questions have to be
    recorded for a throughput number measured weeks apart to mean anything.

    ``nproc``, the CPU model and ``MemTotal`` are read rather than the cloud
    provider's machine-type metadata, because the four providers in this topology
    expose that through four different endpoints while these three files exist on
    all of them -- and because it is the core count, clock and memory that bound
    the measurement, not the label the provider gives the bundle. Note that
    ``--cache`` and ``--max-sql-memory`` are *fractions*, so a change in
    ``MemTotal`` silently changes the absolute cache size even when the flags do
    not move: the flags can match exactly while the caches differ, which is D9
    reappearing in a form the flag comparison alone cannot see.
    """
    argv_cmd, version_cmd = _SERVER_PROBES.get(engine, _SERVER_PROBES["cockroachdb"])
    result = ssh.run(
        node,
        f"{argv_cmd}; echo '---'; "
        f"{version_cmd}; echo '---'; "
        "nproc; grep -m1 '^model name' /proc/cpuinfo | cut -d: -f2-; "
        "grep '^MemTotal' /proc/meminfo",
        timeout=CONTROL_TIMEOUT_S,
    )
    argv, _, rest = result.stdout.partition("---")
    version, _, hardware = rest.partition("---")
    return {
        "host": node.host,
        "engine": engine,
        "start_command": argv.strip(),
        "version": version.strip() or None,
        "hardware": parse_hardware(hardware),
    }


def parse_hardware(block: str) -> dict[str, Any]:
    """Interpret the ``nproc`` / ``model name`` / ``MemTotal`` block.

    Missing fields are recorded as ``None`` rather than as a default. A CPU count
    defaulted to some plausible number is worse than an absent one, because it
    would compare equal to a real reading and so make two unlike machines look
    alike -- the failure mode that kept ``ram_pct`` at a constant 0.0 for an
    entire dissertation's worth of runs (D5).
    """
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    out: dict[str, Any] = {"cpus": None, "cpu_model": None, "mem_total_kb": None}
    for line in lines:
        if line.isdigit():
            out["cpus"] = int(line)
        elif line.startswith("MemTotal"):
            digits = re.sub(r"[^0-9]", "", line)
            out["mem_total_kb"] = int(digits) if digits else None
        else:
            out["cpu_model"] = " ".join(line.split())
    return out


def format_hardware(hardware: dict[str, Any]) -> str:
    """Render a hardware capture as one manifest note line."""
    return (
        f"cpus={hardware.get('cpus')} "
        f"mem_total_kb={hardware.get('mem_total_kb')} "
        f"cpu_model={hardware.get('cpu_model')}"
    )


# --- clock ----------------------------------------------------------------

_SYSTEM_TIME_RE = re.compile(r"System time\s*:\s*([0-9.]+)\s+seconds")


def check_clock_offset(report: PreflightReport, nodes: Iterable[Node]) -> None:
    """Every node's NTP offset must be small relative to CockroachDB's tolerance.

    A cluster whose clocks have drifted will either refuse to serve or will
    produce commit timestamps that make an RPO measurement meaningless, since RPO
    is derived by comparing timestamps written on different nodes.
    """
    for node in nodes:
        result = ssh.run(node, "chronyc tracking", timeout=CONTROL_TIMEOUT_S)
        match = _SYSTEM_TIME_RE.search(result.stdout)
        if match is None:
            report.add(
                "clock_offset",
                False,
                f"{node.name}: could not read chronyc tracking output",
                node=node.name,
            )
            continue
        offset = float(match.group(1))
        report.add(
            "clock_offset",
            offset < MAX_CLOCK_OFFSET_S,
            f"{node.name}: NTP offset {offset * 1000:.2f} ms "
            f"(limit {MAX_CLOCK_OFFSET_S * 1000:.0f} ms)",
            node=node.name,
            offset_s=offset,
        )


# --- leaseholder placement ------------------------------------------------

def check_leaseholder_placement(
    report: PreflightReport,
    gateway: Node,
    database: str,
    expected_region: str,
    settle_timeout_s: float = 0.0,
    poll_interval_s: float = 10.0,
) -> None:
    """The workload's own ranges must be led from where the generator runs.

    ``settle_timeout_s`` allows the placement a bounded window to *become*
    correct before the check is failed, and defaults to 0 -- an immediate,
    single reading -- so every existing caller behaves exactly as before. It is
    not a loosening of the assertion: the condition that must hold is unchanged
    and still has to hold before anything is measured. What it accommodates is
    that lease placement is restored asynchronously by the replication queue,
    so immediately after a chaos run the answer is legitimately "not yet"
    rather than "no".

    Phase III demonstrated this: partitioning ``gcp-1`` moved both ``ycsb``
    leaseholders to Linode, and Phase IV -- which starts as soon as Phase III
    returns -- read that placement and refused to measure. It was right to
    refuse; ~75s of post-heal time was not enough for
    ``lease_preferences`` to pull the leases back, and faulting a node that
    holds no leases measures nothing. Polling turns a run that aborts into one
    that waits for the cluster it just perturbed, and still aborts if the
    cluster does not recover its declared placement.

    Scoped to ``database`` deliberately. A cluster-wide count is not a usable
    signal: system ranges are governed by their own zone configurations and are
    spread across every node by design, so on a healthy testbed the cluster-wide
    distribution shows leaseholders in every region -- 14 in eastasia and 13 in
    centralindia when this check was written, against 60 ranges total. The legacy
    ``wan_baseline.py`` printed exactly that figure and flagged any Azure lease as
    an error, which would have fired on every correctly configured run. Only the
    user data governed by the ``default`` zone configuration is informative here.

    This is D7's detector: an arbitrarily placed lease cost a factor of 12.3 in
    throughput and 110 in read latency, while the cluster reported full health.
    """
    deadline = time.monotonic() + max(settle_timeout_s, 0.0)
    waited_s = 0.0
    started = time.monotonic()
    while True:
        passed, detail, observed = _read_leaseholder_placement(
            gateway, database, expected_region
        )
        waited_s = time.monotonic() - started
        if passed or time.monotonic() >= deadline:
            break
        print(
            f"  waiting for {database} leaseholders to return to "
            f"{expected_region!r}: {detail} "
            f"({waited_s:.0f}s of {settle_timeout_s:.0f}s)",
            flush=True,
        )
        time.sleep(min(poll_interval_s, max(deadline - time.monotonic(), 0.0)))

    if settle_timeout_s > 0 and waited_s >= poll_interval_s:
        detail = f"{detail} (after waiting {waited_s:.0f}s for placement to settle)"
    report.add(
        "leaseholder_placement",
        passed,
        detail,
        database=database,
        expected_region=expected_region,
        waited_s=round(waited_s, 1),
        **observed,
    )


def _read_leaseholder_placement(
    gateway: Node, database: str, expected_region: str
) -> tuple[bool, str, dict[str, Any]]:
    """One reading of where ``database``'s leaseholders currently are.

    Returns ``(passed, detail, observed)`` rather than writing to a report, so
    the caller can take several readings and record only the last.
    """
    query = (
        f"SELECT lease_holder_locality, count(*) FROM "
        f"[SHOW RANGES FROM DATABASE {database} WITH DETAILS] "
        f"GROUP BY 1 ORDER BY 2 DESC"
    )
    # Resolved via the gateway's OWN `tailscale ip -4`, not `gateway.host`.
    # This command runs ON the gateway (it is the ssh target), asking it about
    # itself -- and cockroach binds only its Tailscale IPv4 address, while a
    # bare hostname resolved by the gateway's own OS can answer with something
    # else entirely: on GCP, the project's internal DNS search domain is
    # consulted ahead of the tailnet's own and returns the node's internal
    # RFC1918 address, which nothing listens on. Observed on
    # experiment-20260909T205041Z.log against a cluster verified fully live at
    # the time: "dial tcp 10.5.0.2:26257: connect: connection refused" for a
    # node whose Tailscale address (100.79.193.22) answered node status with
    # all 5 nodes live in the same second. Same idiom the dead-mode restore
    # payload already uses (`p4_chaos.py`'s `TS_IP=$(tailscale ip -4)`).
    result = ssh.run(
        gateway,
        f"TS_IP=$(tailscale ip -4); cockroach sql --insecure --host=$TS_IP:26257 "
        f"--format=csv -e \"{query};\"",
        timeout=60,
    )
    if result.returncode != 0:
        return (
            False,
            (
                f"could not read leaseholders for database {database!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            ),
            {},
        )

    distribution: dict[str, int] = {}
    for line in result.stdout.strip().splitlines()[1:]:
        if not line.strip():
            continue
        # The locality itself contains commas, so split from the right.
        locality, _, count = line.replace('"', "").rpartition(",")
        if locality:
            distribution[locality] = int(count)

    if not distribution:
        return (
            False,
            f"database {database!r} has no ranges; has the working set been loaded?",
            {},
        )

    total = sum(distribution.values())
    local = sum(n for loc, n in distribution.items() if expected_region in loc)
    return (
        local == total,
        (
            f"{local}/{total} {database} leaseholders in {expected_region!r}"
            + ("" if local == total else f"; distribution {distribution}")
        ),
        {"distribution": distribution},
    )


# --- Patroni primary placement (the PostgreSQL counterpart) ----------------

#: Patroni's own REST endpoint, port 8008, answers 200 on the primary and a
#: non-2xx status everywhere else -- the same check
#: ``terraform/scripts/bootstrap-client.tftpl`` configures HAProxy's
#: ``patroni_primary`` backend to poll.
PATRONI_PRIMARY_PORT = 8008
PATRONI_PRIMARY_TIMEOUT_S = 3.0

#: How long a ``patronictl switchover`` is given to complete. A switchover is a
#: controlled handover -- the old primary is demoted only once the candidate has
#: caught up -- so it is bounded by replication lag, not by a failure detector's
#: timeout.
PATRONI_SWITCHOVER_TIMEOUT_S = 120

#: How often the candidate's eligibility is re-read while waiting for it. Well
#: under Patroni's own ``loop_wait`` of 10 s, so the wait ends promptly once the
#: node flips rather than on the next multiple of a coarse interval.
PATRONI_CANDIDATE_POLL_S = 5.0


def patroni_member_state(
    node: Node, timeout_s: float = PATRONI_PRIMARY_TIMEOUT_S
) -> dict[str, Any]:
    """Read one member's ``/patroni`` document, or ``{}`` if it cannot be read.

    This is the same data ``patronictl list`` renders -- ``role``, ``timeline``,
    ``replication_state``, ``xlog`` -- and it is the only place the harness can
    see the two facts that ``/replica`` does not expose: whether the member is
    actually attached to the leader's replication stream, and which timeline it
    is on. Read as a separate function so both the candidate check and its
    failure message can use it.
    """
    import urllib.error
    import urllib.request

    url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/patroni"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        # HTTPError is a subclass of URLError and carries a body, so a member
        # answering 503 is still readable -- which matters, because 503 is
        # exactly the state whose reason we want to report.
        body = exc.read() if hasattr(exc, "read") else None
        if not body:
            return {}
    try:
        state = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return state if isinstance(state, dict) else {}


def patroni_candidate_ready(
    node: Node,
    timeout_s: float = PATRONI_PRIMARY_TIMEOUT_S,
    leader_timeline: int | None = None,
) -> tuple[bool, str]:
    """Is ``node`` a replica Patroni would actually accept as a candidate?

    Two gates, and the second one exists because the first is not sufficient.

    ``/replica`` answers 200 for a member that is up, in recovery, not tagged
    ``noloadbalance``, and within ``maximum_lag_on_failover``. This code once
    stopped there, on the stated theory that 200 was "exactly the condition
    ``patronictl switchover --candidate`` tests". **That was wrong**, and the
    run of 2026-09-09 (experiment-20260909T031334Z.log) is the counterexample:
    after Phase III's partition ``gcp-1`` came back up and answered ``/replica``
    200, this check declared "running replica, lag within bounds", and the
    switchover it then asked for failed with ``503, Switchover failed``. The
    reason is visible in the cluster table the failure printed -- ``gcp-1`` was
    ``Role: Replica`` (not ``Quorum Standby``) on **timeline 1** with
    ``Receive LSN: unknown``, while the leader and the other three members were
    streaming on **timeline 2**. It was a replica that was up and not lagging
    because it was not connected to anything at all, and ``/replica`` cannot
    tell that case from a healthy one: it reports lag against a position the
    member has not been able to advance.

    So the second gate reads ``/patroni`` and requires the member to be
    ``replication_state: streaming`` and, when the leader's timeline is known,
    to be on that same timeline. Both are the observable form of the thing the
    switchover actually needs -- a candidate that already holds the current
    history and is receiving the rest of it. A node mid-rewind or mid-re-clone
    fails this and is *waited* for, which is what ``settle_timeout_s`` is for;
    before this fix the wait ended early on a node that would never have been
    accepted, and the phase failed on a condition it had been given 300 s to
    clear.

    ``leader_timeline`` is optional and the check degrades safely without it:
    the streaming requirement alone catches the observed failure. It is passed
    when the primary's own document could be read, because a member can be
    streaming from a leader and still be behind a timeline switch.

    **Even that was not enough.** Both gates passed on
    ``experiment-20260909T043036Z.log`` -- gcp-1 streaming on the leader's
    timeline 6, zero lag on both Receive and Replay LSN -- and the switchover
    still answered ``503, Switchover failed``. The cluster table the failure
    printed named the reason: gcp-1 was ``Role: Replica``, not ``Quorum
    Standby``, while every other survivor was. In ``synchronous_mode: quorum``
    Patroni refuses a switchover candidate that is not currently one of the
    nodes named in ``synchronous_standby_names``, and that membership is
    decided by the leader on its own ``loop_wait`` cadence -- a node can start
    streaming on the right timeline one poll before the leader admits it to the
    synchronous set, which is exactly the gap this run's candidate wait ended
    inside of. The third gate is ``GET :8008/quorum``, which Patroni documents
    as answering 200 only when "this node is listed as a quorum node in
    synchronous_standby_names on the primary" -- the same fact ``patronictl
    list`` renders as the Role column, read the same bespoke-endpoint way
    ``/replica`` already is.

    Returns the verdict and a human-readable reason, because the reason is what
    goes in the pre-flight report when the wait times out: "still taking its
    basebackup" and "the process is dead" are the same boolean and very
    different situations.
    """
    import urllib.error
    import urllib.request

    url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/replica"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            if response.status != 200:
                return False, f"/replica answered {response.status}"
    except urllib.error.HTTPError as exc:
        # 503 is the normal answer while a member is coming up -- taking its
        # pg_basebackup, replaying WAL, catching up -- and after a diverged
        # rejoin that is minutes, not seconds. It is not a fault.
        return False, f"/replica answered {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"/replica unreachable ({exc})"

    state = patroni_member_state(node, timeout_s=timeout_s)
    if not state:
        return False, "/replica answered 200 but /patroni could not be read"

    replication_state = state.get("replication_state")
    timeline = state.get("timeline")
    if replication_state != "streaming":
        return False, (
            f"/replica answered 200 but the member is not streaming "
            f"(replication_state={replication_state!r}, timeline={timeline!r}); "
            f"it is up and not lagging because it is not attached to the leader"
        )
    if leader_timeline is not None and timeline != leader_timeline:
        return False, (
            f"streaming but on timeline {timeline!r}, not the leader's "
            f"{leader_timeline!r}"
        )

    quorum_url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/quorum"
    try:
        with urllib.request.urlopen(quorum_url, timeout=timeout_s) as response:
            is_quorum_member = response.status == 200
    except urllib.error.HTTPError:
        is_quorum_member = False
    except (urllib.error.URLError, OSError) as exc:
        return False, (
            f"streaming on timeline {timeline!r} but /quorum unreachable ({exc})"
        )
    if not is_quorum_member:
        return False, (
            f"streaming on timeline {timeline!r} but not yet in "
            f"synchronous_standby_names (/quorum did not answer 200); "
            f"a switchover would be refused"
        )

    return True, f"streaming on timeline {timeline!r}, lag within bounds, quorum member"


def resolve_patroni_primary(topo: Topology, timeout_s: float = PATRONI_PRIMARY_TIMEOUT_S) -> Node:
    """Query every cluster member's Patroni REST API and return the primary.

    Raises if zero or more than one node claims to be primary: zero means the
    cluster has no leader right now (mid-failover, or Patroni is down), and more
    than one means a split-brain the harness must not paper over by picking
    one arbitrarily.

    This is the single source of truth for which node is primary. Nothing in
    the harness infers it from configuration -- ``bootstrap-patroni.tftpl``
    pins the leader and :func:`check_patroni_primary_placement` asserts it, but
    both are verified against this live reading rather than assumed.
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


def check_patroni_primary_placement(
    report: PreflightReport,
    topology: Topology,
    expected: Node | None = None,
    repair: bool = True,
    settle_timeout_s: float = 0.0,
) -> Check:
    """The PostgreSQL primary must be where the CockroachDB leaseholder is.

    This is :func:`check_leaseholder_placement`'s counterpart, and it exists for
    the same reason: the workload is driven from ``CLIENT_NODE`` (GCP
    us-east1), so where the write path is led from is a property of the
    *deployment*, not of the engine, and letting it differ between the two arms
    puts cloud geography into the comparison. Measured on this testbed
    2026-09-09, an unpinned Patroni election put the primary on ``crdb-azure-2``
    (Azure eastasia, 199 ms from the client): a fresh connection cost 1.02 s
    against 0.05 s to ``crdb-gcp-1``, a 20x penalty on every connection the
    generator opens, none of which is attributable to PostgreSQL.

    Where the two checks differ is in what restores the condition.
    CockroachDB's ``lease_preferences`` pulls the lease back on its own, so its
    check only has to *wait*. Patroni has no equivalent: ``failover_priority``
    biases who wins an election but never triggers one, so after a chaos run
    the primary simply stays where the failover put it, forever. Waiting for
    the primary to come back would be waiting for something that cannot happen,
    and the repair therefore has to be explicit -- ``patronictl switchover``,
    not a settle window.

    ``settle_timeout_s`` does not weaken that. It waits for a different thing:
    the **candidate** becoming eligible, after which the explicit switchover
    still runs. A switchover to a node Patroni will not accept fails outright
    -- "no good candidates have been found" -- and after a ``recover`` fault
    against the primary the expected node is exactly such a node for a while.
    Its timeline diverged, so it must either be rewound or (with
    ``remove_data_directory_on_diverged_timelines``, which
    ``bootstrap-patroni.tftpl`` sets for this reason) re-cloned from the leader,
    and at thesis scale re-cloning is ~6 GB across a WAN link. Phase IV starts
    the instant Phase III returns, so with no wait the repair asks for a
    handover to a member that is still taking its basebackup and the sweep
    aborts on a condition that would have cleared itself in minutes. Observed
    2026-09-09 (experiment-20260909T011615Z.log) in its permanent form, before
    the template could fall back to a re-clone at all.

    The default is 0 -- one reading, fail fast -- so ``bench`` and ``net probe``
    are unchanged; only the chaos phases pass a window, from
    ``chaos.leaseholder_settle_s``. This mirrors
    :func:`check_leaseholder_placement` exactly. The condition that must hold is
    not loosened by any of it: an ineligible candidate still fails the check
    when the window expires, and the reason Patroni last gave is reported
    rather than a bare timeout.

    What counts as eligible is :func:`patroni_candidate_ready`'s business, and
    it is stricter than it was: a ``/replica`` 200 alone let this wait end on a
    node that was up, unlagged and not connected to anything, after which the
    switchover failed outright (2026-09-09 -- see that function). Streaming on
    the leader's timeline was not sufficient either -- a later run the same day
    showed the candidate can hold both and still not be in Patroni's
    synchronous set yet, and the switchover fails just the same. The candidate
    must now be streaming on the leader's timeline *and* answer its own
    ``/quorum`` endpoint 200, which is why the leader's timeline is read here,
    once, before the wait begins.

    The switchover is a controlled handover, not a fault: Patroni demotes the
    old primary only once the candidate has caught up, so it does not lose
    writes. It runs between phases and never inside a measurement window, and
    it is recorded in the report so a reader can separate what was measured
    from what was repaired.
    """
    expected = expected or topology.gateway

    try:
        primary = resolve_patroni_primary(topology)
    except ValueError as exc:
        return report.add(
            "patroni_primary_placement", False, str(exc), expected=expected.name
        )

    if primary.host == expected.host:
        return report.add(
            "patroni_primary_placement",
            True,
            f"patroni primary is {primary.name}, as required",
            expected=expected.name,
            observed=primary.name,
            repaired=False,
        )

    if not repair:
        return report.add(
            "patroni_primary_placement",
            False,
            f"patroni primary is {primary.name}, expected {expected.name}",
            expected=expected.name,
            observed=primary.name,
            repaired=False,
        )

    print(
        f"  patroni primary is {primary.name}, not {expected.name}; "
        f"switching over (Patroni does not fail back on its own)",
        flush=True,
    )

    # Wait for the candidate to become eligible before asking for the handover.
    # See the docstring: this waits for the candidate, never for the primary,
    # and the switchover below is still what does the repair.
    #
    # The leader's timeline is read once here rather than per poll: it is what
    # the candidate has to converge *onto*, and it does not move while the
    # current leader keeps the lock. None if it could not be read, which
    # degrades the check to its streaming half rather than failing the phase on
    # a missing field.
    leader_timeline = patroni_member_state(primary).get("timeline")
    ready, reason = patroni_candidate_ready(expected, leader_timeline=leader_timeline)
    if not ready and settle_timeout_s > 0:
        print(
            f"  {expected.name} is not yet a switchover candidate ({reason}); "
            f"waiting up to {settle_timeout_s:.0f}s",
            flush=True,
        )
        deadline = time.monotonic() + settle_timeout_s
        while not ready and time.monotonic() < deadline:
            time.sleep(PATRONI_CANDIDATE_POLL_S)
            ready, reason = patroni_candidate_ready(
                expected, leader_timeline=leader_timeline
            )
        if ready:
            print(f"  {expected.name} is a candidate now: {reason}", flush=True)

    if not ready:
        return report.add(
            "patroni_primary_placement",
            False,
            f"patroni primary is {primary.name}, and {expected.name} is not a "
            f"switchover candidate ({reason})"
            + (
                f" after waiting {settle_timeout_s:.0f}s"
                if settle_timeout_s > 0
                else ""
            ),
            expected=expected.name,
            observed=primary.name,
            repaired=False,
        )

    # Run from the current primary: it is by definition reachable and holds the
    # leader lock. --force skips the interactive confirmation; the candidate is
    # named explicitly so Patroni cannot pick a different one.
    #
    # ``node.host``, NOT ``node.name``. Patroni identifies its members by the
    # ``name:`` in its own config, which ``bootstrap-patroni.tftpl`` sets to the
    # node's *hostname* (``crdb-gcp-1``) -- while this harness's ``Node.name``
    # is the short label (``gcp-1``) that ``profiles/*.yaml`` uses for
    # ``chaos.target``. The two differ on every node in the topology, so naming
    # members by ``.name`` addresses members that do not exist. Observed
    # 2026-09-08 (experiment-20260908T225939Z.log): the Phase IV repair ran
    # ``switchover --leader linode-2 --candidate gcp-1`` and Patroni answered
    # "Member linode-2 is not the leader of cluster postgres-cluster", the
    # placement check failed, and Phase IV never ran -- on a cluster that was
    # healthy and where the switchover it was asking for was entirely possible.
    result = ssh.run(
        primary,
        f"{ssh.SUDO} patronictl -c /etc/patroni/config.yml switchover "
        f"--leader {primary.host} --candidate {expected.host} --force",
        timeout=PATRONI_SWITCHOVER_TIMEOUT_S,
    )

    try:
        now = resolve_patroni_primary(topology)
    except ValueError as exc:
        return report.add(
            "patroni_primary_placement",
            False,
            f"switchover to {expected.name} left no single primary: {exc}",
            expected=expected.name,
            observed=primary.name,
            repaired=True,
        )

    passed = now.host == expected.host
    detail = (
        f"switched over from {primary.name} to {now.name}"
        if passed
        else (
            f"switchover to {expected.name} did not take; primary is {now.name}. "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    )
    return report.add(
        "patroni_primary_placement",
        passed,
        detail,
        expected=expected.name,
        observed=now.name,
        repaired=True,
    )


# --- row match (D8) -------------------------------------------------------

#: ``crdb_internal`` is gated behind a session variable on the redeployed
#: testbed: any query against it returns SQLSTATE 42501, "Access to crdb_internal
#: and system is restricted", with a hint naming this variable. The gate is not a
#: version change -- both deployments report v26.3.0 -- so it is a property of
#: the cluster's configuration and must be assumed to vary between deploys rather
#: than pinned to a release.
#:
#: It is prefixed to the statement-statistics query rather than worked around,
#: because that query is D8's only direct detector: it is the one check that can
#: tell a workload doing real work from one whose every operation matches zero
#: rows and therefore reports twenty times the throughput at a twenty-fifth of
#: the latency. Losing it silently would be far worse than losing it loudly, and
#: losing it at all is not acceptable before a sweep.
#:
#: The gate is opened only for this read-only introspection query, and only for
#: the duration of that one ``cockroach sql`` invocation. It is never set for the
#: workload's own connections.
_ALLOW_INTERNALS = "SET allow_unsafe_internals = true;"

_STATS_QUERY = (
    "SELECT coalesce(sum((statistics->'statistics'->>'cnt')::float), 0), "
    "coalesce(sum((statistics->'statistics'->>'cnt')::float * "
    "(statistics->'statistics'->'rowsRead'->>'mean')::float), 0) "
    "FROM crdb_internal.statement_statistics "
    "WHERE metadata->>'query' ILIKE '{pattern}'"
)


@dataclass
class RowMatchProbe:
    """Measures what fraction of the workload's statements touched a row.

    This is D8's direct detector. The generator seeds its key sequence from a
    value that changes on every invocation by default, so a table loaded by one
    process is addressed by a different keyspace than the next process queries and
    every lookup matches nothing. The run still completes, reports no errors, and
    returns roughly twenty times the throughput at a twenty-fifth of the latency,
    because an operation that matches no row does no work. It looks like the best
    result the testbed has ever produced.

    Counters are read by *differencing* across the measurement window rather than
    absolutely: ``crdb_internal.reset_sql_stats()`` does not clear them on
    v26.3.0, so an absolute read returns a running mean over the whole session.
    That subtlety produced three misleading readings during the original
    diagnosis before the execution count was compared against the run's own
    reported operation count and the discrepancy became obvious.
    """

    gateway: Node
    table: str
    _before: tuple[float, float] | None = field(default=None, init=False, repr=False)

    def _sample(self) -> tuple[float, float]:
        query = _STATS_QUERY.format(pattern=f"%{self.table}%WHERE%")
        # See the matching comment in `_read_leaseholder_placement`: resolved
        # via the gateway's own `tailscale ip -4` rather than `gateway.host`,
        # since this command runs ON the gateway and a bare hostname resolved
        # there can answer with an address cockroach never bound.
        result = ssh.run(
            self.gateway,
            f"TS_IP=$(tailscale ip -4); cockroach sql --insecure --host=$TS_IP:26257 "
            f"--format=csv -e \"{_ALLOW_INTERNALS} {query};\"",
            timeout=60,
        )
        if result.returncode != 0:
            raise PreflightError(
                "could not read statement statistics, so the row-match rate cannot "
                "be asserted and this run must not be trusted (D8): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        # The SET above emits its own acknowledgement line before the result set,
        # so the values are the last line rather than the second. Parsed by
        # position within a two-column projection this module wrote itself, not
        # from an external tool's layout.
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        count, rows = lines[-1].split(",")
        return float(count), float(rows)

    def start(self) -> None:
        self._before = self._sample()

    def finish(
        self, report: PreflightReport, corroborated: bool = False
    ) -> float | None:
        """Assert the row-match rate for the tier just measured.

        ``corroborated`` says whether an *independent* detector has already
        confirmed that this tier's operations touched data -- in practice, that
        the observed write median cleared the quorum floor. It is consulted only
        when the statistics view was flushed out from under the window and there
        is no evidence left to assert on; it never relaxes an assertion that
        could be made. See the flush branch below for why the corroboration is
        admissible and what it does not cover.
        """
        if self._before is None:
            raise PreflightError("RowMatchProbe.finish called before start")
        c0, r0 = self._before
        c1, r1 = self._sample()
        executions = c1 - c0
        matched = r1 - r0
        window = "interval"

        if executions <= 0 and c1 > 0:
            # The counters were reset underneath the window. CockroachDB flushes
            # in-memory statement statistics to disk every ``sql.stats.flush.interval``
            # (10 minutes by default), which zeroes the view this probe reads, so a
            # tier straddling a flush boundary differences a large "before" against a
            # small "after" and yields a non-positive delta. On a fifteen-minute
            # sweep exactly one tier hits this: observed 2026-09-02, where eleven of
            # twelve tiers matched at 1.0000 and the twelfth reported no statements
            # at all while sustaining 2,431 ops/s.
            #
            # The absolute counters are still usable, and are not a weaker test. A
            # reset detected here must have occurred *after* ``start()``, so
            # everything accumulated since belongs to this tier: the rate is measured
            # over a shorter window, not over the wrong work. Falling back is
            # therefore a narrowing of the sample, which is recorded, rather than a
            # relaxation of the assertion -- and the assertion is the only detector
            # of D8 that Phase II has, since an unreplicated baseline has no quorum
            # floor to check a write latency against.
            executions, matched = c1, r1
            window = "post-flush partial"

        if executions <= 0 and c0 > 0:
            # Statements existed at start() and none exist now, so the view was
            # flushed *after* this tier's workload stopped and before this
            # sample: the flush moved the evidence rather than the tier failing
            # to produce it. Observed 2026-09-03 on a 21-tier Phase II sweep,
            # where twenty tiers matched at >= 0.9999 and the twenty-first --
            # C=10 rep 3, which had itself just sustained 611.7 ops/s for 55
            # intervals -- reported nothing. The branch above recovers the case
            # where the flush lands mid-tier and some statements accumulate
            # after it; this one cannot, because the workload has already ended.
            #
            # Raising sql.stats.flush.interval for the duration of a sweep would
            # remove the race, and is rejected: a flush writes to
            # system.statement_statistics, which is background I/O on a 2 vCPU
            # host carrying a saturated workload, so suppressing it would change
            # the throughput being measured and make runs before and after the
            # change incomparable. The measurement is not adjusted to suit its
            # instrumentation.
            #
            # What the quorum floor can and cannot stand in for. Under D8 an
            # update matching no rows commits an empty transaction -- there is
            # nothing to replicate -- and returned 3.1 ms. A write median above
            # the floor is therefore positive evidence that the updates in this
            # tier performed real cross-region quorum writes, and it rules out
            # the seed mismatch that D8 names, which breaks reads and updates
            # together. It does *not* independently confirm the 80% of the mix
            # that is reads, so the check is recorded as corroborated rather
            # than as measured, and the run carries that distinction.
            detail = (
                f"the statement-statistics view was flushed after this tier "
                f"ended, so its row-match evidence is unrecoverable"
            )
            if corroborated:
                report.add(
                    "row_match",
                    True,
                    detail
                    + "; the tier's write median cleared the quorum floor, which "
                    "is independent evidence that its updates touched rows "
                    "(an update matching nothing commits an empty transaction "
                    "and returns in ~3 ms, D8). Reads are not independently "
                    "corroborated",
                    table=self.table,
                    window="flushed; corroborated by quorum floor",
                )
                # None, not 0.0 and not NaN: the rate was not measured for this
                # tier. NaN would also serialise into the manifest as a bare NaN
                # token, which is not valid JSON.
                return None
            report.add(
                "row_match",
                False,
                detail
                + " and no independent detector covers this tier, so it cannot "
                "be shown that the workload touched data (D8). "
                "An unreplicated system has no quorum floor to corroborate "
                "against, which is why this is fatal rather than downgraded",
                table=self.table,
                window="flushed; uncorroborated",
            )
            return 0.0

        if executions <= 0:
            report.add(
                "row_match",
                False,
                f"no statements against {self.table!r} were recorded during the "
                "window; the workload may not have run at all",
                table=self.table,
            )
            return 0.0
        rate = matched / executions
        report.add(
            "row_match",
            rate >= MIN_ROW_MATCH_RATE,
            f"{matched:.0f}/{executions:.0f} operations matched a row "
            f"(rate {rate:.4f}, minimum {MIN_ROW_MATCH_RATE})"
            + ("" if window == "interval" else
               "; measured over a partial window because the statistics view was "
               "flushed mid-tier")
            + ("" if rate >= MIN_ROW_MATCH_RATE else "; check that the generator "
               "seed and insert-count match the values the table was loaded with"),
            table=self.table,
            executions=executions,
            matched=matched,
            match_rate=round(rate, 6),
            window=window,
        )
        return rate


#: PostgreSQL's equivalent of the statement-statistics view CockroachDB
#: exposes. ``pg_stat_user_tables`` counts, per table and per node, how many
#: scans ran against it and how many live rows those scans actually fetched --
#: which is exactly the ratio D8 destroys. A workload whose seed does not match
#: the loaded keyspace still scans on every operation and fetches nothing, so
#: the rate goes to zero while throughput goes *up*.
#:
#: An UPDATE's index scan increments ``idx_scan``/``idx_tup_fetch`` like a read
#: does, so both operation types are covered without counting either twice --
#: ``n_tup_upd`` is deliberately not added in, since the row it reports was
#: already counted when the update found it.
#:
#: ``seq_tup_read`` is deliberately **not** part of the matched count, and this
#: is the difference between a working detector and a decorative one. It counts
#: rows *read* by sequential scans, not rows matched: measured on this testbed,
#: twenty sequential scans matching nothing at all reported ``seq_tup_read``
#: 100,000 against a 5,000-row table, so folding it in yielded a "match rate"
#: of 5000 for a workload that touched no data -- comfortably past the 0.99
#: minimum, on precisely the failure this check exists to catch. Only the index
#: counters distinguish "found a row" from "looked at a row", and this
#: workload's operations are primary-key lookups, so index scans are what it
#: should be producing.
#:
#: ``seq_scan`` is therefore read as a *separate* signal rather than as part of
#: the rate: a sequential scan of the workload's table means the plan is not
#: the one the measurement assumes, which is its own defect.
_PG_STATS_QUERY = (
    "SELECT coalesce(idx_scan, 0), coalesce(idx_tup_fetch, 0), coalesce(seq_scan, 0) "
    "FROM pg_stat_user_tables WHERE relname = '{table}'"
)


@dataclass
class PostgresRowMatchProbe:
    """D8's detector for PostgreSQL, with :class:`RowMatchProbe`'s semantics.

    Until this existed the check was CockroachDB-only, so the single most
    dangerous failure this project has on record -- a workload that addresses an
    empty keyspace, reports roughly twenty times the throughput at a
    twenty-fifth of the latency, and looks like an excellent result -- had no
    detector at all on the PostgreSQL side of the comparison. A defect that
    fails flatteringly needs its detector on both arms or the comparison is
    exactly as trustworthy as the arm without one.

    Counters are differenced across the tier rather than read absolutely, for
    the same reason as the CockroachDB probe. Unlike CockroachDB's, they are not
    flushed on a timer, so the "post-flush partial" recovery that probe needs
    has no counterpart here; a reset means the server restarted, which is not
    something to silently absorb during a benchmark.

    The statistics are per-node and only the primary serves this workload, so
    the DSN must be one that resolves to the primary -- ``pg_direct_dsn``'s
    ``target_session_attrs=read-write`` -- rather than to whichever replica a
    round-robin happened to pick.
    """

    exec_node: Node
    dsn: str
    table: str
    password: str = ""
    _before: tuple[float, float] | None = field(default=None, init=False, repr=False)

    def _sample(self) -> tuple[float, float, float]:
        query = _PG_STATS_QUERY.format(table=self.table)
        result = ssh.run(
            self.exec_node,
            f"PGPASSWORD={shlex.quote(self.password)} "
            f"psql {shlex.quote(self.dsn)} -tAF, -c {shlex.quote(query)}",
            timeout=60,
        )
        if result.returncode != 0:
            raise PreflightError(
                "could not read pg_stat_user_tables, so the row-match rate cannot "
                "be asserted and this run must not be trusted (D8): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            # No row at all means the table does not exist in this database --
            # the working set was never loaded, or was loaded somewhere else.
            raise PreflightError(
                f"pg_stat_user_tables has no row for {self.table!r}: the table does "
                "not exist on the primary, so the workload cannot have touched it (D8)"
            )
        scans, rows, seq = lines[-1].split(",")
        return float(scans), float(rows), float(seq)

    def start(self) -> None:
        self._before = self._sample()

    def finish(
        self, report: PreflightReport, corroborated: bool = False
    ) -> float | None:
        """Assert the row-match rate for the tier just measured.

        ``corroborated`` is accepted for interface parity with
        :class:`RowMatchProbe` and is consulted for the same purpose: when the
        counters went backwards there is no evidence left to assert on, and an
        independent detector -- the write median clearing the quorum floor -- is
        the only thing that can speak for the tier.
        """
        if self._before is None:
            raise PreflightError("PostgresRowMatchProbe.finish called before start")
        s0, r0, q0 = self._before
        s1, r1, q1 = self._sample()
        scans = s1 - s0
        matched = r1 - r0
        seq_scans = q1 - q0

        if seq_scans > 0:
            # Not folded into the rate, and not ignored either. This workload
            # addresses rows by primary key; a sequential scan of its table
            # means the plan is not the one being measured -- a dropped index,
            # a rewritten predicate, or a table that is not what it should be --
            # and every such scan reads the whole table, which is not the
            # operation whose latency is being reported.
            report.add(
                "row_match", False,
                f"{seq_scans:.0f} sequential scan(s) of {self.table!r} during the "
                "tier; this workload should address rows by primary key, so the "
                "operations measured are not the operations intended",
                table=self.table, sequential_scans=seq_scans,
            )
            return 0.0

        if scans < 0 or matched < 0:
            detail = (
                f"pg_stat_user_tables counters for {self.table!r} went backwards "
                "during the tier, which means the server restarted or the "
                "statistics were reset"
            )
            if corroborated:
                report.add(
                    "row_match", True,
                    detail + "; the write median cleared the quorum floor, which "
                    "independently shows the operations reached data",
                    table=self.table, window="reset; corroborated by quorum floor",
                )
                return None
            report.add(
                "row_match", False,
                detail + " and no independent detector covers this tier, so it "
                "cannot be shown that the workload touched data (D8)",
                table=self.table, window="reset; uncorroborated",
            )
            return 0.0

        if scans <= 0:
            report.add(
                "row_match", False,
                f"no index scans of {self.table!r} were recorded during the "
                "window; the workload may not have run at all",
                table=self.table,
            )
            return 0.0

        rate = matched / scans
        report.add(
            "row_match",
            rate >= MIN_ROW_MATCH_RATE,
            f"{matched:.0f}/{scans:.0f} index scans fetched a row "
            f"(rate {rate:.4f}, minimum {MIN_ROW_MATCH_RATE})"
            + ("" if rate >= MIN_ROW_MATCH_RATE else "; check that the generator "
               "seed and insert-count match the values the table was loaded with"),
            table=self.table,
            executions=scans,
            matched=matched,
            match_rate=round(rate, 6),
            window="interval",
        )
        return rate


def row_match_probe(
    engine: str,
    *,
    gateway: Node,
    table: str,
    exec_node: Node | None = None,
    dsn: str | None = None,
    password: str = "",
):
    """The D8 detector for ``engine``. Both arms of the comparison have one."""
    if engine == "postgresql":
        if exec_node is None or dsn is None:
            raise PreflightError(
                "a PostgreSQL row-match probe needs a client node to run from and a "
                "DSN that resolves to the primary"
            )
        return PostgresRowMatchProbe(exec_node, dsn, table, password)
    return RowMatchProbe(gateway, table)


# --- write latency floor (D8) ---------------------------------------------

def quorum_floor_ms(rtts_ms: dict[str, float], voters: int) -> float:
    """Round trip to the follower whose acknowledgement completes quorum.

    A write commits when a majority of voting replicas has acknowledged it. With
    ``voters`` replicas the leader needs ``voters // 2`` follower acknowledgements
    in addition to its own, so the binding constraint is the round trip to the
    slowest of the *fastest* ``voters // 2`` followers. Nothing committed can be
    faster than this, which makes it an invariant rather than a heuristic: a
    reported write latency below this floor is not a good result but an
    impossible one, and in practice means the writes matched no rows.

    On the reference testbed the followers sit at 24.7, 70.6, 191.3 and 200.5 ms,
    so with five voters the floor is 70.6 ms -- matched by three independent
    measurements (kv inserts 75.5 ms, ycsb inserts 79.7 ms, ycsb updates 75.5 ms).
    """
    if voters < 3:
        raise ValueError(f"a quorum requires at least 3 voters, got {voters}")
    needed = voters // 2
    ordered = sorted(rtts_ms.values())
    if len(ordered) < needed:
        raise ValueError(
            f"{voters} voters need {needed} follower RTTs, only "
            f"{len(ordered)} were measured"
        )
    return ordered[needed - 1]


def check_write_latency_floor(
    report: PreflightReport,
    observed_write_p50_ms: float,
    floor_ms: float,
    tolerance: float = 0.9,
) -> bool:
    """Assert the observed write latency is physically achievable.

    ``tolerance`` allows a small margin below the measured floor for scheduling
    jitter and for the difference between an ICMP round trip and a Raft
    acknowledgement; it is not licence for a value that is a different order of
    magnitude.
    """
    limit = floor_ms * tolerance
    report.add(
        "write_latency_floor",
        observed_write_p50_ms >= limit,
        f"write p50 {observed_write_p50_ms:.1f} ms against a quorum floor of "
        f"{floor_ms:.1f} ms"
        + ("" if observed_write_p50_ms >= limit else
           "; a committed write cannot outrun quorum, so these writes are "
           "probably matching no rows (D8)"),
        observed_write_p50_ms=round(observed_write_p50_ms, 3),
        quorum_floor_ms=round(floor_ms, 3),
    )
    return observed_write_p50_ms >= limit


def gateway_rtts(network_csv, gateway_host: str) -> dict[str, float]:
    """Mean RTT from ``gateway_host`` to every other node in a Phase I matrix."""
    import csv as _csv

    with open(network_csv, newline="") as fh:
        return {
            row["destination"]: float(row["rtt_mean_ms"])
            for row in _csv.DictReader(fh)
            if row["source"] == gateway_host and row["rtt_mean_ms"] not in ("", "None")
        }

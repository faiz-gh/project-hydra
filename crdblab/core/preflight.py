"""Checks that the testbed is fit to be measured, run before each measurement.

``analysis/validation.py`` asks whether recorded numbers are consistent with
each other. Pre-flight asks whether the system was in a state worth measuring,
which catches runs that are arithmetically sound but meaningless (a misplaced
leaseholder, a workload whose lookups match no rows). Each check records the
value it observed.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..topology import Node, Topology
from . import ssh

#: Half of CockroachDB's default ``--max-offset`` (500 ms).
MAX_CLOCK_OFFSET_S = 0.25

#: Below this, the generator's keyspace does not match the loaded data.
MIN_ROW_MATCH_RATE = 0.99

#: Hang detector for control-plane SSH commands (not a latency budget; WAN SSH
#: setup alone can take several seconds).
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

#: Shell commands reading the server's argv and version, per engine.
_SERVER_PROBES = {
    "cockroachdb": (
        "pgrep -a cockroach | head -1",
        "cockroach version --build-tag 2>/dev/null",
    ),
    # Match the postmaster by path; `[p]` stops `pgrep -f` matching this probe's
    # own command line. Version comes from the binary, so no credentials needed.
    "postgresql": (
        "pgrep -a -f bin/[p]ostgres | head -1",
        (
            "for b in /usr/lib/postgresql/*/bin/[p]ostgres; do $b --version; done "
            "2>/dev/null | tail -1"
        ),
    ),
}


def capture_server_config(node: Node, engine: str = "cockroachdb") -> dict[str, Any]:
    """Record how the database server was started on ``node``, and on what hardware.

    Captures the raw server argv, version, CPU count/model and ``MemTotal`` so
    comparability checks can spot differences in flags or machines. Cache
    flags are fractions of RAM, so memory matters even when flags match. For
    PostgreSQL, ``memory`` also carries ``shared_buffers``/``effective_cache_size``.
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
        "memory": capture_pg_memory_config(node) if engine == "postgresql" else None,
    }


def parse_hardware(block: str) -> dict[str, Any]:
    """Interpret the ``nproc`` / ``model name`` / ``MemTotal`` block.

    Missing fields are ``None``, never a plausible default that could compare equal.
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


#: Patroni sets these in postgresql.conf, not argv, so they are read via SQL.
_PG_MEMORY_QUERY = (
    "SELECT pg_size_bytes(current_setting('shared_buffers')), "
    "pg_size_bytes(current_setting('effective_cache_size'))"
)


def capture_pg_memory_config(node: Node) -> dict[str, int] | None:
    """PostgreSQL's ``shared_buffers``/``effective_cache_size`` in kB, or ``None``.

    The counterpart of CockroachDB's ``--cache`` for cross-engine comparability.
    Read over the local socket as ``postgres``; any failure returns ``None``.
    """
    result = ssh.run(
        node,
        f"{ssh.SUDO} -u postgres psql -tAF, -c {shlex.quote(_PG_MEMORY_QUERY)}",
        timeout=CONTROL_TIMEOUT_S,
    )
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = line.split(",")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        return None
    shared_buffers_bytes, effective_cache_bytes = (int(p.strip()) for p in parts)
    return {
        "shared_buffers_kb": shared_buffers_bytes // 1024,
        "effective_cache_size_kb": effective_cache_bytes // 1024,
    }


def format_pg_memory(memory: dict[str, Any]) -> str:
    """Render a PostgreSQL memory capture as one manifest note line."""
    return (
        f"shared_buffers_kb={memory.get('shared_buffers_kb')} "
        f"effective_cache_size_kb={memory.get('effective_cache_size_kb')}"
    )


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
    """Every node's NTP offset must be below :data:`MAX_CLOCK_OFFSET_S`.

    Drifted clocks break CockroachDB and make cross-node timings meaningless.
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
    """All of ``database``'s leaseholders must be in ``expected_region``.

    Scoped to the workload database because system ranges are spread across
    regions by design. ``settle_timeout_s`` lets the placement recover after a
    previous chaos run (the replication queue restores leases asynchronously);
    the condition itself is never relaxed. A misplaced lease slows every
    operation while the cluster still reports healthy.
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
    """One reading of ``database``'s leaseholder placement: ``(passed, detail, observed)``."""
    query = (
        f"SELECT lease_holder_locality, count(*) FROM "
        f"[SHOW RANGES FROM DATABASE {database} WITH DETAILS] "
        f"GROUP BY 1 ORDER BY 2 DESC"
    )
    # Runs on the gateway, whose OS may resolve its own hostname to an internal
    # address cockroach does not listen on; use the Tailscale IP.
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

#: Patroni REST API; ``/primary`` answers 200 only on the leader.
PATRONI_PRIMARY_PORT = 8008
PATRONI_PRIMARY_TIMEOUT_S = 3.0

#: A switchover is a controlled handover bounded by replication lag.
PATRONI_SWITCHOVER_TIMEOUT_S = 120

#: Candidate re-check interval, under Patroni's 10 s ``loop_wait``.
PATRONI_CANDIDATE_POLL_S = 5.0


def patroni_member_state(
    node: Node, timeout_s: float = PATRONI_PRIMARY_TIMEOUT_S
) -> dict[str, Any]:
    """Read one member's ``/patroni`` document (role, timeline, replication state), or ``{}``."""
    import urllib.error
    import urllib.request

    url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/patroni"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        # HTTPError carries a body, so a 503 member is still readable.
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
    """Would Patroni accept ``node`` as a switchover candidate? Returns ``(ready, reason)``.

    Three gates, all required:

    1. ``/replica`` answers 200 (up, in recovery, lag within bounds).
    2. ``/patroni`` shows ``replication_state: streaming``, on the leader's
       timeline when known. A detached replica can pass gate 1 with no lag.
    3. ``/quorum`` answers 200: in ``synchronous_mode: quorum`` Patroni refuses
       a candidate not yet in ``synchronous_standby_names``.

    The reason explains a timeout (e.g. still re-cloning vs. process down).
    """
    import urllib.error
    import urllib.request

    url = f"http://{node.host}:{PATRONI_PRIMARY_PORT}/replica"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            if response.status != 200:
                return False, f"/replica answered {response.status}"
    except urllib.error.HTTPError as exc:
        # 503 is normal while a member catches up or re-clones.
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
    """Return the node whose Patroni ``/primary`` answers 200.

    Raises if none or more than one does (mid-failover or split-brain). This
    live reading is the only source of truth for which node is primary.
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
    """The Patroni primary must be on ``expected`` (the gateway by default).

    Keeps the write path led from the same place on both engines. Patroni never
    fails back on its own, so if the primary is elsewhere and ``repair`` is set
    this runs ``patronictl switchover``. ``settle_timeout_s`` waits for the
    *candidate* to become eligible first (after a partition it may need to
    rewind or re-clone); it never waits for the primary to move by itself.
    The switchover is recorded in the report as a repair.
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

    # The leader's timeline is read once: the candidate must converge onto it.
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

    # Run on the current primary. Patroni names members by hostname
    # (``node.host``), not by the harness's short ``node.name``.
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


# --- row match ---

#: ``crdb_internal`` may be gated behind this session variable; it is set only for
#: this read-only statistics query, never for workload connections.
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
    """Measures what fraction of the workload's statements touched a row (CockroachDB).

    If the generator's seed differs from the one used at load time, every
    lookup matches nothing and the run reports far higher throughput at far
    lower latency. Counters from ``crdb_internal.statement_statistics`` are
    differenced across the tier window.
    """

    gateway: Node
    table: str
    _before: tuple[float, float] | None = field(default=None, init=False, repr=False)

    def _sample(self) -> tuple[float, float]:
        query = _STATS_QUERY.format(pattern=f"%{self.table}%WHERE%")
        # Tailscale IP, as in `_read_leaseholder_placement`.
        result = ssh.run(
            self.gateway,
            f"TS_IP=$(tailscale ip -4); cockroach sql --insecure --host=$TS_IP:26257 "
            f"--format=csv -e \"{_ALLOW_INTERNALS} {query};\"",
            timeout=60,
        )
        if result.returncode != 0:
            raise PreflightError(
                "could not read statement statistics, so the row-match rate cannot "
                "be asserted and this run must not be trusted: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        # The SET prints its own line first, so the values are on the last line.
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        count, rows = lines[-1].split(",")
        return float(count), float(rows)

    def start(self) -> None:
        self._before = self._sample()

    def finish(
        self, report: PreflightReport, corroborated: bool = False
    ) -> float | None:
        """Assert the row-match rate for the tier just measured.

        ``corroborated`` (the write median cleared the quorum floor) is only
        consulted when a statistics flush destroyed the tier's evidence.
        """
        if self._before is None:
            raise PreflightError("RowMatchProbe.finish called before start")
        c0, r0 = self._before
        c1, r1 = self._sample()
        executions = c1 - c0
        matched = r1 - r0
        window = "interval"

        if executions <= 0 and c1 > 0:
            # A stats flush (every 10 min) reset the counters mid-tier; everything
            # since belongs to this tier, so use the absolute values.
            executions, matched = c1, r1
            window = "post-flush partial"

        if executions <= 0 and c0 > 0:
            # Flushed after the tier ended: evidence is gone. Accept only if the quorum
            # floor check independently shows the updates did real work.
            detail = (
                "the statement-statistics view was flushed after this tier "
                "ended, so its row-match evidence is unrecoverable"
            )
            if corroborated:
                report.add(
                    "row_match",
                    True,
                    detail
                    + "; the tier's write median cleared the quorum floor, which "
                    "is independent evidence that its updates touched rows "
                    "(an update matching nothing commits an empty transaction "
                    "and returns in ~3 ms). Reads are not independently "
                    "corroborated",
                    table=self.table,
                    window="flushed; corroborated by quorum floor",
                )
                # Not measured: None (NaN would not be valid JSON).
                return None
            report.add(
                "row_match",
                False,
                detail
                + " and no independent detector covers this tier, so it cannot "
                "be shown that the workload touched data. "
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


#: PostgreSQL row-match counters: ``idx_tup_fetch / idx_scan`` is the match rate.
#: ``seq_tup_read`` counts rows read, not matched, so a ``seq_scan`` is flagged instead.
_PG_STATS_QUERY = (
    "SELECT coalesce(idx_scan, 0), coalesce(idx_tup_fetch, 0), coalesce(seq_scan, 0) "
    "FROM pg_stat_user_tables WHERE relname = '{table}'"
)


@dataclass
class PostgresRowMatchProbe:
    """PostgreSQL counterpart of :class:`RowMatchProbe`.

    Reads ``pg_stat_user_tables`` on the primary (the DSN must resolve to it)
    and differences it across the tier. These counters are not flushed on a
    timer, so a reset means the server restarted.
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
                "be asserted and this run must not be trusted: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            # No row: the table does not exist in this database.
            raise PreflightError(
                f"pg_stat_user_tables has no row for {self.table!r}: the table does "
                "not exist on the primary, so the workload cannot have touched it"
            )
        scans, rows, seq = lines[-1].split(",")
        return float(scans), float(rows), float(seq)

    def start(self) -> None:
        self._before = self._sample()

    def finish(
        self, report: PreflightReport, corroborated: bool = False
    ) -> float | None:
        """Assert the row-match rate for the tier just measured.

        ``corroborated`` is consulted only if the counters went backwards.
        """
        if self._before is None:
            raise PreflightError("PostgresRowMatchProbe.finish called before start")
        s0, r0, q0 = self._before
        s1, r1, q1 = self._sample()
        scans = s1 - s0
        matched = r1 - r0
        seq_scans = q1 - q0

        if seq_scans > 0:
            # Primary-key lookups should never sequentially scan the table.
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
                "cannot be shown that the workload touched data",
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
    """The row-match probe for ``engine``."""
    if engine == "postgresql":
        if exec_node is None or dsn is None:
            raise PreflightError(
                "a PostgreSQL row-match probe needs a client node to run from and a "
                "DSN that resolves to the primary"
            )
        return PostgresRowMatchProbe(exec_node, dsn, table, password)
    return RowMatchProbe(gateway, table)


# --- write latency floor ---------------------------------------------

def quorum_floor_ms(rtts_ms: dict[str, float], voters: int) -> float:
    """Round trip to the follower whose acknowledgement completes quorum.

    The leader needs ``voters // 2`` follower acks, so the floor is the RTT to
    the slowest of the fastest ``voters // 2`` followers. No committed write can
    be faster; a lower write latency means the writes matched no rows.
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

    ``tolerance`` allows a small margin for jitter and ICMP-vs-Raft differences.
    """
    limit = floor_ms * tolerance
    report.add(
        "write_latency_floor",
        observed_write_p50_ms >= limit,
        f"write p50 {observed_write_p50_ms:.1f} ms against a quorum floor of "
        f"{floor_ms:.1f} ms"
        + ("" if observed_write_p50_ms >= limit else
           "; a committed write cannot outrun quorum, so these writes are "
           "probably matching no rows"),
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

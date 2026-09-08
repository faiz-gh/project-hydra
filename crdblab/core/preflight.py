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

def capture_server_config(node: Node) -> dict[str, Any]:
    """Record how CockroachDB was actually started on ``node``.

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
    result = ssh.run(
        node,
        "pgrep -a cockroach | head -1; echo '---'; "
        "cockroach version --build-tag 2>/dev/null; echo '---'; "
        "nproc; grep -m1 '^model name' /proc/cpuinfo | cut -d: -f2-; "
        "grep '^MemTotal' /proc/meminfo",
        timeout=CONTROL_TIMEOUT_S,
    )
    argv, _, rest = result.stdout.partition("---")
    version, _, hardware = rest.partition("---")
    return {
        "host": node.host,
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
    result = ssh.run(
        gateway,
        f"cockroach sql --insecure --host={gateway.host}:26257 "
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
        result = ssh.run(
            self.gateway,
            f"cockroach sql --insecure --host={self.gateway.host}:26257 "
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

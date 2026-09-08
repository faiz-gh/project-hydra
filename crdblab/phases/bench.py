"""Benchmark: steady-state throughput and latency under load.

Measures the same five-node cluster twice, once per engine: CockroachDB and
PostgreSQL under Patroni for HA. The generator is executed from the dedicated
client node (:data:`crdblab.topology.CLIENT_NODE`) rather than from a node that
is itself part of the system under test, so that the difference between a
CockroachDB run and a PostgreSQL run isolates the engine rather than also
moving where the generator runs. A single :class:`Target` carries the engine
selector and the connection information for both: CockroachDB is driven
against a single gateway connection string (:attr:`Target.db_uri`), while
PostgreSQL is driven through the HAProxy endpoint on the client node itself
(``127.0.0.1:5000``), which is what fronts the Patroni cluster's current
leader. The sweep, the parsing, the sampling and the recording are identical
for both engines and are implemented once here.

``Target`` briefly carried one connection string *per cluster member* instead,
on the theory that the client pool should survive a single node's loss. It
does not: ``cockroach workload run``, given more than one URL, dials its
``--concurrency`` connections *serially* against the list rather than in
parallel, at roughly 2.65 s per connection measured on this topology --
0.83 s for the whole pool at C=200 with one URL, 9m00s with five. Nothing
faults during a benchmark sweep, so the resilience a multi-host string would
buy is not needed here in the first place; it only added minutes of pure
connection-setup time to every tier at any real concurrency.

Design points that are corrections of specific observed failures:

* Output is parsed by :class:`~crdblab.core.workload.WorkloadParser` in strict
  mode. Throughput is summed across operation types and latency distributions are
  kept separate (D1, D2, D3).
* Tier order is shuffled to decorrelate thermal and background drift from the
  concurrency axis, and the realised order is recorded in the manifest, because a
  randomisation that is not written down is not reproducible.
* Every scheduling decision uses :func:`time.monotonic` (D4).
* Each tier is bracketed by a :class:`~crdblab.core.preflight.RowMatchProbe`, and
  the observed write latency is checked against the quorum floor derived from
  Phase I. Both exist because a workload that matches no rows reports roughly
  twenty times the throughput at a twenty-fifth of the latency and is otherwise
  indistinguishable from an excellent result (D8). Both checks are
  CockroachDB-specific and are skipped for a PostgreSQL target.
"""

from __future__ import annotations

import random
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..config import Profile, Settings
from ..core import preflight, ssh
from ..core.recorder import (
    COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
)
from ..core.workload import PERIODIC, SUMMARY, WorkloadParser, group_timed_ticks
from ..topology import CLIENT_NODE, Node, Topology

_METRIC_RE = re.compile(r"^(?P<name>[a-z_]+)\{[^}]*\}\s+(?P<value>[0-9.e+-]+)$")

#: Host counters scraped from the node's Prometheus endpoint. Names verified
#: against v26.3.0; they carry a ``{node_id="..."}`` label, which the legacy
#: prefix match happened to tolerate but which is matched explicitly here.
_CPU_METRIC = "sys_cpu_combined_percent_normalized"
_DISK_READ_METRIC = "sys_host_disk_read_count"
_DISK_WRITE_METRIC = "sys_host_disk_write_count"
_RSS_METRIC = "sys_rss"


@dataclass
class Target:
    """What is under test, and where the generator runs.

    ``voters`` is the replication factor of the ranges being written. It is 1 for
    the single-node baseline, where there is no quorum and hence no latency floor
    to assert against.
    """

    name: str
    phase: str
    exec_node: Node
    database: str
    voters: int
    engine: str
    nodes: tuple[Node, ...] = ()

    @property
    def db_uri(self) -> str:
        """The single connection string the generator dials.

        One URL, not one per cluster member -- see the module docstring for
        why a multi-host string is actively harmful here, not merely
        unnecessary.
        """
        if self.engine == "postgresql":
            return f"postgresql://root@127.0.0.1:5000/{self.database}?sslmode=disable"
        gateway = next((n for n in self.nodes if n.gateway), None)
        host = gateway.host if gateway else "crdb-gcp-1"
        port = gateway.sql_port if gateway else 26257
        return f"postgresql://root@{host}:{port}/{self.database}?sslmode=disable"

    @property
    def metrics_url(self) -> str:
        return ""


@dataclass
class HostSample:
    cpu_pct: float = float("nan")
    disk_iops: float = float("nan")
    rss_bytes: float = float("nan")


class HostSampler:
    """Polls the target's Prometheus endpoint once a second in the background.

    Disk activity is exposed as a monotonic counter, so an interval rate is
    obtained by differencing consecutive scrapes; the first scrape therefore
    yields no rate and is reported as ``nan`` rather than as zero. Distinguishing
    "not yet measured" from "measured as zero" matters here because the legacy
    exports recorded an unmeasured quantity as a constant and nobody noticed for
    an entire dissertation (D5).
    """

    def __init__(self, url: str, interval_s: float = 1.0) -> None:
        self._url = url
        self._interval = interval_s
        self._sample = HostSample()
        self._prev_disk_total: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failures = 0

    def _scrape(self) -> None:
        with urllib.request.urlopen(self._url, timeout=2.0) as response:
            body = response.read().decode("utf-8", "replace")
        values: dict[str, float] = {}
        for line in body.splitlines():
            match = _METRIC_RE.match(line)
            if match and match.group("name") in (
                _CPU_METRIC,
                _DISK_READ_METRIC,
                _DISK_WRITE_METRIC,
                _RSS_METRIC,
            ):
                values[match.group("name")] = float(match.group("value"))

        sample = HostSample()
        if _CPU_METRIC in values:
            sample.cpu_pct = values[_CPU_METRIC] * 100.0
        if _RSS_METRIC in values:
            sample.rss_bytes = values[_RSS_METRIC]
        if _DISK_READ_METRIC in values and _DISK_WRITE_METRIC in values:
            total = values[_DISK_READ_METRIC] + values[_DISK_WRITE_METRIC]
            if self._prev_disk_total is not None:
                sample.disk_iops = (total - self._prev_disk_total) / self._interval
            self._prev_disk_total = total
        self._sample = sample

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._scrape()
            except Exception:
                self.failures += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))

    def __enter__(self) -> "HostSampler":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def current(self) -> HostSample:
        return self._sample


def tier_order(profile: Profile) -> list[tuple[int, int]]:
    """Realised (concurrency, repetition) sequence for the sweep.

    Shuffled when the profile asks for it, using a generator seeded from the
    profile so the order is a function of declared parameters rather than of the
    wall clock. The returned sequence is written into the manifest verbatim.
    """
    spec = profile.workload
    plan = [(c, r) for r in range(1, spec.repetitions + 1) for c in spec.concurrencies]
    if spec.randomise_tier_order:
        random.Random(spec.seed).shuffle(plan)
    return plan


def _run_tier(
    target: Target,
    profile: Profile,
    concurrency: int,
    repetition: int,
    raw_path: Path,
    writer: MetricsWriter,
    sampler: HostSampler,
    manifest: Manifest,
    t_zero: float,
    tier_index: int = 0,
    tier_total: int = 0,
) -> dict[str, Any]:
    """Execute one tier and record its per-interval samples.

    Returns the tier's summary statistics for the post-hoc physical checks.
    """
    spec = profile.workload
    generator = (
        f"cockroach workload run {spec.generator} "
        f"--workload={spec.ycsb_workload} "
        f"--seed={spec.seed} "
        f"--insert-count={spec.insert_count} "
        f"--request-distribution={spec.request_distribution} "
        f"--read-freq={spec.read_freq} --update-freq={spec.update_freq} "
        f"--concurrency={concurrency} "
        f"--duration={spec.duration_s}s "
        f"--display-every={spec.display_every_s}s "
        f"'{target.db_uri}'"
    )
    remote = ssh.force_tty(generator)
    manifest.generator_command = generator

    print(
        f"  tier {tier_index}/{tier_total}: C={concurrency}, rep={repetition}, "
        f"{spec.duration_s}s",
        flush=True,
    )

    # This tier's own origin, distinct from the sweep-wide ``t_zero``. Connection
    # setup is a property of *this* tier -- the SSH session, the process start
    # and the generator's own dialing -- so measuring it from the sweep epoch
    # reports everything that has happened since the sweep began instead. On a
    # 12-tier thesis sweep that grew by one tier's duration plus cooldown on
    # every iteration (18.8s to 1042.5s, ~92s per step), firing the warning
    # below on all twelve, against a true setup cost of 0.2-4.8s.
    #
    # ``t_zero`` stays the origin for ``wall_offset_s`` and
    # ``generator_start_offset_s``: those exist to make ticks from different
    # tiers orderable against each other and against the cooldowns between
    # them, which needs one epoch for the whole sweep.
    tier_start = time.monotonic()

    parser = WorkloadParser(strict=True)
    samples = []
    # Each sample is stamped with the harness's own clock as it is read, so the
    # generator's ``elapsed`` accounting and the run's wall-clock timeline can be
    # placed on one axis afterwards rather than assumed to coincide. They do not:
    # the generator's origin is later than the run's by the SSH and process
    # startup cost.
    arrivals: list[tuple[float, Any]] = []
    with open(raw_path, "w") as tee:
        with ssh.StreamingRemote(target.exec_node, remote, tee=tee) as stream:
            for line in stream:
                sample = parser.feed(line)
                if sample is not None:
                    samples.append(sample)
                    arrivals.append((time.monotonic(), sample))
                    # A one-line-per-second summary, not the raw stream: the
                    # raw text (every operation type's own row) is already
                    # kept verbatim in raw_path, and echoing all of it here
                    # duplicates that rather than helping anyone watch a tier
                    # progress live. `flush=True` matters as much as what is
                    # printed -- run-experiment.sh pipes this through `tee`,
                    # which makes Python's stdout fully buffered instead of
                    # line-buffered (it is no longer a TTY), so without an
                    # explicit flush every print below queues up and appears
                    # all at once when the buffer fills or the process exits,
                    # which looks exactly like "no logs until it's done".
                    if sample.kind == PERIODIC and int(sample.elapsed_s) % 15 == 0:
                        total_tps = sum(
                            s.tps for s in samples
                            if s.kind == PERIODIC and s.elapsed_s == sample.elapsed_s
                        )
                        print(
                            f"    [{sample.elapsed_s:6.1f}s] tps={total_tps:8.1f}",
                            flush=True,
                        )

    timed = list(group_timed_ticks(arrivals))
    started_at = timed[0][0] - timed[0][1].elapsed_s if timed else None
    kept = 0
    per_op_p50: dict[str, list[float]] = {}
    throughputs: list[float] = []

    for arrived, tick in timed:
        # Discard the ramp-up window: connection establishment and cache warming
        # are not steady state, and averaging them in depresses every tier
        # equally but not identically.
        if tick.elapsed_s <= spec.warmup_s:
            continue
        kept += 1
        host = sampler.current
        throughputs.append(tick.total_tps)
        for op, sample in tick.by_op.items():
            per_op_p50.setdefault(op, []).append(sample.latency_ms("p50"))
            writer.write(
                {
                    "ts_utc": utcnow(),
                    "elapsed_s": tick.elapsed_s,
                    "wall_offset_s": round(arrived - t_zero, 3),
                    "concurrency": concurrency,
                    "repetition": repetition,
                    "op": op,
                    "tps": sample.tps,
                    "tps_cum": sample.values.get("tps_cum", ""),
                    "errors_cum": sample.errors_cum,
                    "p50_ms": sample.latency_ms("p50"),
                    "p95_ms": sample.latency_ms("p95"),
                    "p99_ms": sample.latency_ms("p99"),
                    "pmax_ms": sample.latency_ms("pmax"),
                    "gateway_cpu_pct": round(host.cpu_pct, 3),
                    "gateway_disk_iops": round(host.disk_iops, 1),
                    "gateway_rss_bytes": int(host.rss_bytes)
                    if host.rss_bytes == host.rss_bytes
                    else "",
                }
            )

    summary_rows = {s.op: s.values for s in samples if s.kind == SUMMARY}
    expected = spec.duration_s - spec.warmup_s
    if kept < expected * 0.9:
        manifest.note(
            f"C={concurrency} rep={repetition}: only {kept} steady-state ticks, "
            f"expected about {expected}"
        )
    setup_s = (started_at - tier_start) if started_at is not None else None
    mean_tps = round(sum(throughputs) / len(throughputs), 1) if throughputs else None
    print(
        f"  tier {tier_index}/{tier_total} done: {kept} ticks kept, "
        f"mean {mean_tps or 0:.1f} ops/s"
        + (f", setup {setup_s:.1f}s" if setup_s is not None else ""),
        flush=True,
    )
    # A connection-setup delay this size means the generator was still dialing
    # in when this ran -- for a bench sweep that only wastes wall time, but the
    # same delay silently desynchronised a chaos run's fault-injection timer
    # from its steady state once already (9 min of dialing against a 60 s
    # inject_at_s). Surfaced here so a future regression like it is loud.
    if setup_s is not None and setup_s > 10.0:
        print(
            f"  WARNING: tier {tier_index}/{tier_total} took {setup_s:.1f}s just to "
            "establish connections -- investigate before trusting timings that "
            "assume the generator started promptly",
            flush=True,
        )
    return {
        "concurrency": concurrency,
        "repetition": repetition,
        "ticks_recorded": kept,
        # Offset between the two clocks for this tier: how long after the phase's
        # epoch the generator's own ``elapsed`` zero fell. Recorded per tier
        # because it is the SSH and process startup cost, which varies.
        "generator_start_offset_s": round(started_at - t_zero, 3)
        if started_at is not None
        else None,
        # How long this tier spent getting the generator to its first sample,
        # measured from the tier's own start. Distinct from the field above,
        # which shares the sweep's epoch; conflating the two is what produced
        # the runaway setup figures described at ``tier_start``.
        "connection_setup_s": round(setup_s, 3) if setup_s is not None else None,
        "mean_total_tps": round(sum(throughputs) / len(throughputs), 2)
        if throughputs
        else None,
        "mean_p50_ms": {
            op: round(sum(v) / len(v), 3) for op, v in per_op_p50.items() if v
        },
        "generator_totals": {op: v.get("ops_total") for op, v in summary_rows.items()},
    }


def run(
    settings: Settings,
    profile: Profile,
    target: Target,
    network_run: Path | None = None,
    skip_checks: bool = False,
) -> tuple[RunDirectory, dict[str, Any]]:
    """Execute a full concurrency sweep against ``target``."""
    spec = profile.workload
    report = preflight.PreflightReport()

    # Pre-flight runs before any measurement, not after: the point is to refuse
    # to spend half an hour producing a run that will have to be discarded.
    quorum_floor: float | None = None
    if not skip_checks and target.engine == "cockroachdb":
        preflight.check_clock_offset(report, [target.exec_node])
        if target.voters > 1:
            preflight.check_leaseholder_placement(
                report, settings.topology.gateway, target.database, settings.topology.gateway.region
            )
            if network_run is None:
                report.add(
                    "quorum_floor_available",
                    False,
                    "no Phase I run supplied; run `crdblab net probe` first so the "
                    "write-latency floor can be asserted",
                )
            else:
                rtts = preflight.gateway_rtts(network_run, settings.topology.gateway.host)
                quorum_floor = preflight.quorum_floor_ms(rtts, target.voters)
                report.add(
                    "quorum_floor_available",
                    True,
                    f"quorum floor {quorum_floor:.1f} ms from {network_run}",
                    quorum_floor_ms=round(quorum_floor, 3),
                )
        report.raise_if_failed()

    run_dir = RunDirectory(settings.runs_dir, new_run_id(target.phase))
    plan = tier_order(profile)
    # One monotonic epoch for the whole sweep. Every ``wall_offset_s`` in the
    # metrics table is measured from here, so ticks from different tiers remain
    # orderable against each other and against the cooldowns between them.
    t_zero = time.monotonic()
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase=target.phase,
        clock_epoch_utc=utcnow(),
        profile=profile.to_dict(),
        topology=[
            {
                "name": target.exec_node.name,
                "host": target.exec_node.host,
                "region": target.exec_node.region,
                "locality": target.exec_node.locality,
                "role": "generator host and connection endpoint",
            }
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
    )
    manifest.note(f"target={target.name} database={target.database} voters={target.voters}")
    manifest.note(f"tier order: {plan}")

    # Capture server configuration
    server = {}
    if target.engine == "cockroachdb":
        server = preflight.capture_server_config(settings.topology.gateway)
        manifest.cockroach_version = server.get("version")
        manifest.note(f"server: {server.get('start_command', '')}")
        manifest.note(f"host: {preflight.format_hardware(server.get('hardware', {}))}")
    else:
        manifest.note(f"engine: postgresql (patroni HA)")

    tiers: list[dict[str, Any]] = []
    with HostSampler(target.metrics_url) as sampler:
        with MetricsWriter(run_dir.metrics_csv, COLUMNS) as writer:
            for index, (concurrency, repetition) in enumerate(plan):
                probe = preflight.RowMatchProbe(settings.topology.gateway, "usertable")
                if not skip_checks and target.engine == "cockroachdb":
                    probe.start()

                raw_path = run_dir.raw(f"c{concurrency}_rep{repetition}.txt")
                tier = _run_tier(
                    target, profile, concurrency, repetition,
                    raw_path, writer, sampler, manifest, t_zero,
                    tier_index=index + 1, tier_total=len(plan),
                )

                if not skip_checks and target.engine == "cockroachdb":
                    floor_ok = False
                    if quorum_floor is not None:
                        write_p50 = tier["mean_p50_ms"].get("update")
                        if write_p50 is not None:
                            floor_ok = preflight.check_write_latency_floor(
                                report, write_p50, quorum_floor
                            )
                    tier["row_match_rate"] = probe.finish(
                        report, corroborated=floor_ok
                    )
                tiers.append(tier)

                if index < len(plan) - 1 and spec.cooldown_s:
                    # Monotonic, so a clock adjustment mid-sweep cannot shorten or
                    # extend the interval that lets range rebalancing quiesce.
                    deadline = time.monotonic() + spec.cooldown_s
                    while (remaining := deadline - time.monotonic()) > 0:
                        time.sleep(min(remaining, 0.5))

    if sampler.failures:
        manifest.note(f"host metric scrape failed {sampler.failures} time(s)")
    manifest.finished_utc = utcnow()
    manifest.validation = {"preflight": report.to_dict()}
    run_dir.write_manifest(manifest)
    run_dir.write_preflight({**report.to_dict(), "tiers": tiers})
    return run_dir, {"tiers": tiers, "preflight": report}


def cluster_target(settings: Settings, database: str = "ycsb", engine: str = "cockroachdb") -> Target:
    """The five-node cluster, driven from the dedicated client node."""
    return Target(
        name="cluster",
        phase="bench_cluster",
        exec_node=CLIENT_NODE,
        database=database,
        voters=len(settings.topology),
        engine=engine,
        nodes=settings.topology.nodes,
    )

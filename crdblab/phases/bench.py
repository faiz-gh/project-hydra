"""Phase II: steady-state throughput and latency, swept over concurrency tiers.

The generator runs on the dedicated client node against one connection string
(``cockroach workload`` dials multiple URLs serially, which is very slow):
the gateway for CockroachDB, the client node's pgbouncer/HAProxy for PostgreSQL.
The sweep is otherwise identical for both engines.

* Output is parsed by the strict, header-bound
  :class:`~crdblab.core.workload.WorkloadParser`.
* Tier order is shuffled with a profile-seeded RNG and recorded in the manifest.
* Scheduling uses :func:`time.monotonic`.
* Each tier is bracketed by a row-match probe, and its write median is checked
  against the Phase I quorum floor, so a workload touching no rows is caught.
"""

from __future__ import annotations

import random
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Profile, Settings, pg_direct_dsn, pg_generator_dsn
from ..core import preflight, ssh
from ..core.hardware_metrics import HardwareMetricsSampler
from ..core.recorder import (
    COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
)
from ..core.workload import PERIODIC, SUMMARY, WorkloadParser, group_timed_ticks
from ..topology import CLIENT_NODE, Node


@dataclass
class Target:
    """What is under test, and where the generator runs.

    ``voters`` is the replication factor, which sets the quorum floor.
    """

    name: str
    phase: str
    exec_node: Node
    database: str
    voters: int
    engine: str
    nodes: tuple[Node, ...] = ()
    #: PostgreSQL ``root`` password (CockroachDB runs ``--insecure``).
    password: str | None = None

    @property
    def db_uri(self) -> str:
        """The single connection string the generator dials."""
        if self.engine == "postgresql":
            return pg_generator_dsn(self.database, self.password or "")
        gateway = next((n for n in self.nodes if n.gateway), None)
        host = gateway.host if gateway else "crdb-gcp-1"
        port = gateway.sql_port if gateway else 26257
        return f"postgresql://root@{host}:{port}/{self.database}?sslmode=disable"

def tier_order(profile: Profile) -> list[tuple[int, int]]:
    """Realised (concurrency, repetition) sequence for the sweep.

    Shuffled with an RNG seeded from the profile, so the order is reproducible.
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
    manifest: Manifest,
    t_zero: float,
    tier_index: int = 0,
    tier_total: int = 0,
) -> dict[str, Any]:
    """Execute one tier, record its per-interval samples, and return its summary."""
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

    # Connection setup is measured from this tier's start; ``t_zero`` stays the
    # sweep-wide origin for ``wall_offset_s``.
    tier_start = time.monotonic()

    parser = WorkloadParser(strict=True)
    samples = []
    # Harness clock at arrival, so generator and harness timelines can be aligned.
    arrivals: list[tuple[float, Any]] = []
    with open(raw_path, "w") as tee:
        with ssh.StreamingRemote(target.exec_node, remote, tee=tee) as stream:
            for line in stream:
                sample = parser.feed(line)
                if sample is not None:
                    samples.append(sample)
                    arrivals.append((time.monotonic(), sample))
                    # Brief progress every 15 s; flush because stdout is teed to a log.
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
        if tick.elapsed_s <= spec.warmup_s:
            continue
        kept += 1
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
                    "gateway_cpu_pct": "",
                    "gateway_disk_iops": "",
                    "gateway_rss_bytes": "",
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
    # Slow setup means the generator was still dialing; make it loud.
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
        # When the generator's ``elapsed`` zero fell, on the sweep clock.
        "generator_start_offset_s": round(started_at - t_zero, 3)
        if started_at is not None
        else None,
        # Time from this tier's start to the generator's first sample.
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

    quorum_floor: float | None = None

    def _resolve_quorum_floor() -> None:
        """Derive the quorum floor from Phase I. Applies to both engines:
        Patroni's ``ANY 2`` synchronous standbys match a 3-of-5 Raft quorum."""
        nonlocal quorum_floor
        if network_run is None:
            report.add(
                "quorum_floor_available",
                False,
                "no Phase I run supplied; run `crdblab net probe` first so the "
                "write-latency floor can be asserted",
            )
            return
        rtts = preflight.gateway_rtts(network_run, settings.topology.gateway.host)
        quorum_floor = preflight.quorum_floor_ms(rtts, target.voters)
        report.add(
            "quorum_floor_available",
            True,
            f"quorum floor {quorum_floor:.1f} ms from {network_run}",
            quorum_floor_ms=round(quorum_floor, 3),
        )

    if not skip_checks and target.engine == "cockroachdb":
        preflight.check_clock_offset(report, [target.exec_node])
        if target.voters > 1:
            preflight.check_leaseholder_placement(
                report, settings.topology.gateway, target.database, settings.topology.gateway.region
            )
            _resolve_quorum_floor()
        report.raise_if_failed()
    elif not skip_checks and target.engine == "postgresql":
        # The primary must be on the gateway, as the leaseholder is for CockroachDB.
        preflight.check_clock_offset(report, [target.exec_node])
        preflight.check_patroni_primary_placement(report, settings.topology)
        if target.voters > 1:
            _resolve_quorum_floor()
        report.raise_if_failed()

    run_dir = RunDirectory(settings.runs_dir, new_run_id(target.phase))
    plan = tier_order(profile)
    # One monotonic epoch for the whole sweep; every ``wall_offset_s`` uses it.
    t_zero = time.monotonic()
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase=target.phase,
        engine=target.engine,
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

    # Server flags and hardware, read by `validation.check_run_comparability`.
    server = preflight.capture_server_config(settings.topology.gateway, engine=target.engine)
    manifest.server_version = server.get("version")
    if target.engine == "cockroachdb":
        manifest.cockroach_version = server.get("version")
    else:
        manifest.note("engine: postgresql (patroni HA)")
    manifest.note(f"server: {server.get('start_command', '')}")
    manifest.note(f"host: {preflight.format_hardware(server.get('hardware', {}))}")
    if server.get("memory"):
        manifest.note(f"pg memory: {preflight.format_pg_memory(server['memory'])}")

    all_nodes = list(target.nodes) + [target.exec_node]
    hw_cm = (
        HardwareMetricsSampler(
            all_nodes, t_zero, interval_s=profile.hardware_metrics.sample_interval_s
        )
        if profile.hardware_metrics.enabled
        else nullcontext()
    )

    tiers: list[dict[str, Any]] = []
    with hw_cm as hw_sampler:
        with MetricsWriter(run_dir.metrics_csv, COLUMNS) as writer:
            for index, (concurrency, repetition) in enumerate(plan):
                # Row match and the quorum floor catch a workload touching no rows.
                probe = preflight.row_match_probe(
                    target.engine,
                    gateway=settings.topology.gateway,
                    table="usertable",
                    exec_node=target.exec_node,
                    dsn=pg_direct_dsn(settings.topology, target.database, settings.pg_password),
                    password=settings.pg_password,
                )
                if not skip_checks:
                    probe.start()

                raw_path = run_dir.raw(f"c{concurrency}_rep{repetition}.txt")
                tier = _run_tier(
                    target, profile, concurrency, repetition,
                    raw_path, writer, manifest, t_zero,
                    tier_index=index + 1, tier_total=len(plan),
                )

                if not skip_checks:
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
                    # Cooldown lets range rebalancing quiesce between tiers.
                    deadline = time.monotonic() + spec.cooldown_s
                    while (remaining := deadline - time.monotonic()) > 0:
                        time.sleep(min(remaining, 0.5))

    if hw_sampler is not None:
        hw_sampler.write(run_dir.hardware_metrics_csv)
        failed = {n: c for n, c in hw_sampler.scrape_failures.items() if c}
        if failed:
            manifest.note(f"hardware metrics: scrape failures {failed}")
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
        password=settings.pg_password if engine == "postgresql" else None,
    )

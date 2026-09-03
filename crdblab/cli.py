"""Command-line entry point.

The standard library's ``argparse`` is used in preference to a third-party CLI
framework to keep the dependency surface of the measurement path as small as
possible: every additional runtime dependency is one more thing whose version
must be pinned and reported for the results to be reproducible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .config import Profile, Settings, load_env_file
from .core import ssh
from .core.workload import PERIODIC, SUMMARY, WorkloadParser, group_ticks


def _generator_flags(args: argparse.Namespace) -> str:
    """Assemble the generator-specific portion of the workload invocation.

    The two generators express their read/write mix incompatibly.

    ``--seed`` is the load-bearing argument here and is passed unconditionally
    (defect D8). The generator derives its keys from a pseudo-random sequence
    whose seed *changes on every invocation by default*, so a table populated by
    ``workload init`` is addressed by a different keyspace than a subsequent
    ``workload run`` consults. Every point lookup then matches nothing. This
    fails silently and, worse, fails *fast*: unmatched reads and updates return
    in ~3 ms against the ~75 ms a genuine quorum write costs on this topology,
    so the corrupted configuration looks like a spectacularly good result rather
    than a broken one. Measured against v26.3.0, matching the seed between load
    and run moves the row-match rate from 0.0000 to 1.0000 and update latency
    from 3.1 ms to 75.5 ms, the latter agreeing with the 70.6 ms
    second-fastest-follower RTT that bounds Raft quorum.

    ``CUSTOM`` with an explicit read/update split preserves the 80/20 mix of the
    original design, so corrected results remain comparable with the legacy
    figures reproduced in the error case study. ``request_distribution`` is
    pinned to ``uniform`` because ``CUSTOM`` defaults to zipfian, which would
    concentrate accesses on a hot subset and is not what ``kv``'s uniformly
    scattered keys did.
    """
    if args.generator == "ycsb":
        flags = (
            f"--workload={args.workload} "
            f"--seed={args.seed} "
            f"--insert-count={args.insert_count} "
            f"--request-distribution={args.request_distribution} "
        )
        if args.workload.upper() == "CUSTOM":
            flags += f"--read-freq={args.read_freq} --update-freq={args.update_freq} "
        return flags
    if args.generator == "kv":
        return (
            f"--read-percent={args.read_percent} "
            f"--seed={args.seed} "
            f"--cycle-length={args.cycle_length} "
        )
    raise SystemExit(f"unsupported generator {args.generator!r}; expected 'ycsb' or 'kv'")


def _cmd_capture(args: argparse.Namespace) -> int:
    """Capture raw generator output verbatim and report the column layout.

    This must be run once against the provisioned testbed before any
    measurement sweep. It pins the exact column layout emitted by the
    CockroachDB version in use, which is the fact the legacy tooling assumed
    rather than verified.
    """
    settings = Settings.from_env()
    node = settings.topology.get(args.node)
    db_uri = settings.require_db_uri()

    remote = (
        f"cockroach workload run {args.generator} "
        f"{_generator_flags(args)}"
        f"--concurrency={args.concurrency} "
        f"--duration={args.duration}s "
        f"--display-every=1s '{db_uri}'"
    )
    if args.pty:
        remote = ssh.force_tty(remote)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parser = WorkloadParser(strict=False)
    samples = []
    with open(out_path, "w") as tee:
        with ssh.StreamingRemote(node, remote, tee=tee) as stream:
            for line in stream:
                sample = parser.feed(line)
                if sample is not None:
                    samples.append(sample)

    periodic = [s for s in samples if s.kind == PERIODIC]
    summary = [s for s in samples if s.kind == SUMMARY]
    ticks = list(group_ticks(samples))
    ops = sorted({s.op for s in periodic})

    print(f"raw output written to {out_path}")
    print(f"periodic samples: {len(periodic)}  summary rows: {len(summary)}  ticks: {len(ticks)}")
    print(f"operation types reported per interval: {ops or ['(none)']}")
    if periodic:
        print(f"latency columns bound: {sorted(k for k in periodic[0].values if k.endswith('_ms'))}")
    if len(ticks) < args.duration * 0.9:
        print(
            f"WARNING: only {len(ticks)} ticks for a {args.duration}s run. The generator is "
            "probably suppressing per-interval output because stdout is a pipe; re-run with --pty.",
            file=sys.stderr,
        )
    if parser.unparsed:
        print(f"note: {len(parser.unparsed)} non-sample line(s) ignored (generator chatter)")
    return 0


def _cmd_net_probe(args: argparse.Namespace) -> int:
    """Phase I: characterise the substrate and derive the quorum floor.

    Run this before any benchmark. Its round-trip matrix is what makes the
    write-latency floor check possible, and that check is the only assertion in
    the project capable of detecting a workload that reports excellent numbers
    while touching no data.
    """
    from .core import preflight
    from .phases import p1_network

    settings = Settings.from_env()
    profile = Profile.load(args.profile)
    run_dir, probes = p1_network.run(settings, profile)

    print(f"run: {run_dir.path}")
    failed = [p for p in probes if p.error]
    for p in failed:
        print(f"  ! {p.node}: {p.error}", file=sys.stderr)

    ok = [p for p in probes if not p.error]
    if ok:
        print(f"\nMTU: " + ", ".join(f"{p.node}={p.mtu}" for p in ok))
        print("\nmean RTT (ms), source -> destination:")
        for p in ok:
            cells = ", ".join(
                f"{d.split('crdb-')[-1]}={s.rtt_mean_ms}"
                for d, s in sorted(p.links.items())
                if s.rtt_mean_ms is not None
            )
            print(f"  {p.node:<16} {cells}")

    summary = p1_network.summarise(probes, settings.topology)
    if summary:
        print(
            f"\nquorum floor: {summary['quorum_floor_ms']} ms "
            f"({summary['voters']} voters, leader + {summary['voters'] // 2} "
            f"follower acks). No committed write can be faster than this."
        )

    report = preflight.PreflightReport()
    if args.checks:
        preflight.check_clock_offset(report, settings.topology)
        preflight.check_leaseholder_placement(
            report,
            settings.topology.gateway,
            args.database,
            settings.topology.gateway.region,
        )
        print()
        for check in report.checks:
            print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")

    run_dir.write_preflight({**report.to_dict(), "derived": summary})
    if failed:
        return 1
    return 0 if report.ok else 1


def _latest_network_run(runs_dir: Path) -> Path | None:
    """Most recent Phase I matrix, which supplies the quorum floor."""
    candidates = sorted(runs_dir.glob("*_p1-network/network.csv"))
    return candidates[-1] if candidates else None


def _cmd_bench(args: argparse.Namespace) -> int:
    from .phases import bench

    settings = Settings.from_env()
    profile = Profile.load(args.profile)
    target = (
        bench.single_target(settings, args.database)
        if args.scope == "single"
        else bench.cluster_target(settings, args.database)
    )

    network_run = Path(args.network_run) if args.network_run else _latest_network_run(
        settings.runs_dir
    )
    plan = bench.tier_order(profile)
    spec = profile.workload
    estimate = len(plan) * (spec.duration_s + spec.cooldown_s) / 60.0
    print(
        f"{target.phase}: {len(plan)} tiers "
        f"({len(spec.concurrencies)} concurrencies x {spec.repetitions} reps), "
        f"~{estimate:.0f} min"
    )
    print(f"  target   {target.exec_node.host} ({target.voters} voter(s))")
    print(f"  uri      {target.db_uri}")
    if network_run:
        print(f"  phase I  {network_run}")

    try:
        run_dir, result = bench.run(
            settings, profile, target,
            network_run=network_run,
            skip_checks=not args.checks,
        )
    except Exception as exc:
        print(f"\nrefusing to measure: {exc}", file=sys.stderr)
        return 1

    print(f"\nrun: {run_dir.path}")
    print(f"{'C':>5} {'rep':>4} {'ticks':>6} {'tps':>10}  latency p50 (ms)")
    for tier in sorted(result["tiers"], key=lambda t: (t["concurrency"], t["repetition"])):
        lat = "  ".join(f"{op}={v}" for op, v in sorted(tier["mean_p50_ms"].items()))
        print(
            f"{tier['concurrency']:>5} {tier['repetition']:>4} "
            f"{tier['ticks_recorded']:>6} {tier['mean_total_tps'] or 0:>10.1f}  {lat}"
        )

    report = result["preflight"]
    failed = [c for c in report.checks if not c.passed]
    if failed:
        print("\npre-flight failures:", file=sys.stderr)
        for check in failed:
            print(f"  [FAIL] {check.name}: {check.detail}", file=sys.stderr)
        print(
            "\nThis run must not be used for figures. See docs/defects.md.",
            file=sys.stderr,
        )
        return 1
    print(f"\nall {len(report.checks)} pre-flight checks passed")
    print(f"next: crdblab validate {run_dir.path}")
    return 0


def _cmd_chaos(args: argparse.Namespace) -> int:
    from .phases import p4_chaos

    settings = Settings.from_env()
    profile = Profile.load(args.profile)
    print(f"p4_chaos ({args.mode}): target={profile.chaos.target}")
    try:
        run_dir, events = p4_chaos.run(settings, profile, args.mode, database=args.database)
    except Exception as exc:
        print(f"\nrefusing to measure: {exc}", file=sys.stderr)
        return 1

    print(f"\nrun: {run_dir.path}")
    print(f"  baseline          {events['baseline_tps']:.1f} tps")
    print(
        f"  recovery floor    {events['recovery_floor_tps']:.1f} tps "
        f"({events['recovery_threshold']:.0%} held for {events['recovery_hold_s']}s)"
    )
    # Two different quantities, both legitimately called RTO. Availability is the
    # headline for a failover claim; performance qualifies it, and is undefined
    # while a fast-triangle member is down because the surviving quorum is
    # intercontinental. Reporting either alone misleads.
    avail = events.get("availability", {})
    a_rto = avail.get("availability_rto_s")
    res = avail.get("resolution_s")
    print(
        f"  RTO availability  "
        + (f"{a_rto:.2f} s" if a_rto is not None else avail.get("detail", "not measured"))
        + (f"   (resolution ~{res:.2f} s)" if res else "")
    )
    if avail.get("write_gap_s") is not None:
        print(f"     write outage   {avail['write_gap_s']:.2f} s between acknowledged writes")
    p_rto = events["performance_rto_s"]
    print(
        f"  RTO performance   "
        + (
            f"{p_rto:.1f} s"
            if p_rto is not None
            else f"not regained within the run (needs >={events['recovery_floor_tps']:.0f} tps)"
        )
    )
    rpo = events["rpo"]
    print(
        f"  RPO               {rpo['rpo_violations']} acknowledged write(s) lost "
        f"of {rpo['acknowledged']} acknowledged"
    )
    print(
        f"     ambiguous {rpo['ambiguous']} (of which {rpo['ambiguous_but_committed']} "
        f"did commit), refused {rpo['refused']}"
    )
    if rpo["rpo_violations"]:
        print(
            "\n  A write the client was told had committed is absent. For a "
            "quorum-replicated database this should not occur; investigate before "
            "reporting it.",
            file=sys.stderr,
        )
    print(f"\nevents: {run_dir.events_json}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Stage 5 analysis. Every path here loads through the canonical loader,
    which refuses a run that has no manifest or does not pass validation."""
    from .analysis import raft_overhead, resilience, steady_state
    from .analysis.loader import RunLoadError, load_run

    settings = Settings.from_env()

    def emit(payload: dict) -> None:
        print(json.dumps(payload, indent=2, default=str))

    try:
        if args.analysis == "steady-state":
            run = load_run(args.run, settings.runs_dir)
            summary = steady_state.summarise(run)
            if args.json:
                emit(summary)
                return 0
            print(f"{run.run_id}  ({run.phase}, schema {run.schema_version})")
            window = summary["window"]
            print(
                f"  warmup: declared {window['declared_warmup_s']:.0f}s, first "
                f"interval at {window['first_interval_s']:.0f}s "
                f"({'already trimmed at write time' if window['already_trimmed'] else 'not trimmed'})"
            )
            print("\nthroughput, summed across operation types:")
            print(steady_state.per_tier(run).to_string(index=False))
            print("\nlatency, per operation type, never pooled:")
            print(steady_state.latency_by_op(run).to_string(index=False))
            return 0

        if args.analysis == "raft-overhead":
            baseline = load_run(args.baseline, settings.runs_dir)
            cluster = load_run(args.cluster, settings.runs_dir)
            try:
                result = raft_overhead.compare(
                    baseline,
                    cluster,
                    args.op,
                    accept_hardware_difference=args.accept_hardware_difference,
                )
            except raft_overhead.NotComparable as exc:
                print(f"refusing to compare: {exc}", file=sys.stderr)
                print(
                    "\nThese two runs differ in more than replication, so their "
                    "difference is not replication cost (see docs/defects.md, D9).",
                    file=sys.stderr,
                )
                return 1
            if args.json:
                emit(result)
                return 0

            print(f"phase II  {baseline.run_id}")
            print(f"phase III {cluster.run_id}")
            print(f"\nthroughput-latency curve ({args.op}):")
            print(raft_overhead.curves(baseline, cluster, args.op).to_string(index=False))
            for phase, sat in result["saturation"].items():
                print(f"  {phase}: {sat['detail']}")

            matched = result["matched_throughput"]
            print("\nat matched throughput:")
            if not matched["comparable"]:
                print(f"  NOT AVAILABLE. {matched['reason']}.")
                print(f"  remedy: {matched['remedy']}")
            else:
                best = matched.get("least_confounded") or {}
                for point in matched["points"]:
                    util = ""
                    if point.get("utilisation_gap") is not None:
                        util = (
                            f"  [util {point['phase_ii_utilisation']:.2f} vs "
                            f"{point['phase_iii_utilisation']:.2f}, "
                            f"gap {point['utilisation_gap']:.2f}]"
                        )
                    # The utilisation gap is printed against every point, and the
                    # narrowest is named, because matching throughput does not
                    # match utilisation: at the cluster's peak it is at 100% of
                    # its capacity while the baseline is at 72% of its, so most of
                    # the ratio there is the cluster's own queueing rather than
                    # replication. Printing the four ratios undifferentiated
                    # invites the largest one to be quoted.
                    mark = " <-- least confounded" if point is best or (
                        best and point["throughput_tps"] == best.get("throughput_tps")
                    ) else ""
                    print(
                        f"  {point['throughput_tps']:>8.0f} ops/s: "
                        f"phase II {point['phase_ii_latency_ms']:.2f} ms, "
                        f"phase III {point['phase_iii_latency_ms']:.2f} ms "
                        f"({point['overhead_x']:.2f}x){util}{mark}"
                    )
                if best:
                    print(
                        f"  quote the least-confounded point "
                        f"({best['overhead_x']:.2f}x at {best['throughput_tps']:.0f} ops/s), "
                        f"not the largest: the ratio inflates with the utilisation gap"
                    )
                print(f"  caveat: {matched['caveat']}")

            util = result.get("matched_utilisation", {})
            print("\nat matched utilisation (the phases are at DIFFERENT throughputs here):")
            if not util.get("comparable"):
                print(f"  NOT AVAILABLE. {util.get('reason')}")
            else:
                print(
                    f"  capacity: phase II {util['phase_ii_peak_tps']:.0f} ops/s, "
                    f"phase III {util['phase_iii_peak_tps']:.0f} ops/s"
                )
                for point in util["points"]:
                    print(
                        f"  {point['utilisation']:>5.0%} of capacity: "
                        f"phase II {point['phase_ii_latency_ms']:.2f} ms "
                        f"@{point['phase_ii_tps']:.0f} ops/s, "
                        f"phase III {point['phase_iii_latency_ms']:.2f} ms "
                        f"@{point['phase_iii_tps']:.0f} ops/s "
                        f"({point['overhead_x']:.2f}x)"
                    )
                print(f"  caveat: {util['caveat']}")

            light = result["lightest_load_write_latency"]
            if light.get("ratio_x"):
                print(
                    f"\nlightest-load write median: "
                    f"phase II {light['phase_ii']['p50_ms']:.2f} ms at "
                    f"{light['phase_ii']['offered_load_tps']:.0f} ops/s, "
                    f"phase III {light['phase_iii']['p50_ms']:.2f} ms at "
                    f"{light['phase_iii']['offered_load_tps']:.0f} ops/s "
                    f"({light['ratio_x']:.2f}x)"
                )
                print(f"  caveat: {light['caveat']}")

            invalid = result["same_concurrency_delta"]
            print(f"\nsame-concurrency delta -- NOT A RESULT ({invalid['use']}):")
            print(pd.DataFrame(invalid["rows"]).to_string(index=False))
            print(f"  why not: {invalid['reason']}")
            return 0

        run = load_run(args.run, settings.runs_dir)
        network = Path(args.network_run) if args.network_run else _latest_network_run(
            settings.runs_dir
        )
        summary = resilience.summarise(run, network, settings.topology)
        if args.json:
            emit(summary)
            return 0

        print(f"{run.run_id}  (mode {summary['mode']}, target {summary['target']})")
        clock = summary["clock_alignment"]
        print(f"\nclock alignment: {clock['method']}")
        print(f"  {clock['detail']}")

        avail = summary["availability_rto"]
        print("\nRTO, availability:")
        print(f"  {avail.get('claim', avail.get('detail'))}")
        if avail.get("caveat"):
            print(f"  caveat: {avail['caveat']}")

        perf = summary["performance_rto"]
        print("\nRTO, performance:")
        print(f"  {perf.get('claim', perf.get('detail'))}")
        if perf.get("post_fault_state", {}).get("mean_tps"):
            state = perf["post_fault_state"]
            print(
                f"  settled at {state['mean_tps']:.0f} ops/s "
                f"(sd {state['sd_tps']:.0f}, {state['fraction_of_baseline']:.0%} of "
                f"baseline) over {state['intervals']} intervals"
            )

        geom = summary["quorum_geometry"]
        if geom.get("available"):
            print("\nquorum geometry:")
            print(f"  {geom['detail']}")
            print(f"  {geom['consequence']}")

        rpo = summary["rpo"]
        print("\nRPO:")
        print(f"  {rpo.get('claim', rpo.get('detail'))}")
        if rpo.get("interpretation"):
            print(f"  {rpo['interpretation']}")
        return 0
    except RunLoadError as exc:
        print(f"refusing to analyse: {exc}", file=sys.stderr)
        return 1


def _latest_run(runs_dir: Path, suffix: str) -> str | None:
    """Most recent run directory of a given phase."""
    candidates = sorted(runs_dir.glob(f"*_{suffix}"))
    return candidates[-1].name if candidates else None


def _cmd_report(args: argparse.Namespace) -> int:
    """Render the dissertation figures from validated runs.

    Inputs default to the most recent run of each phase. Every figure resolves
    through the analysis loader, so a run that has no manifest or does not
    validate cannot reach a figure at all.
    """
    from .analysis.loader import RunLoadError, load_network_run, load_run
    from .report import figures

    settings = Settings.from_env()
    runs = settings.runs_dir
    out_dir = Path(args.out)

    picks = {
        "network": args.network or _latest_run(runs, "p1-network"),
        "baseline": args.baseline or _latest_run(runs, "p2_baseline"),
        "cluster": args.cluster or _latest_run(runs, "p3_cluster"),
        "chaos": args.chaos or _latest_run(runs, "p4-chaos-recover"),
    }
    for role, run_id in picks.items():
        print(f"  {role:9} {run_id or '(none found)'}")

    try:
        written = figures.render_all(
            out_dir,
            network=load_network_run(picks["network"], runs) if picks["network"] else None,
            baseline=load_run(picks["baseline"], runs) if picks["baseline"] else None,
            cluster=load_run(picks["cluster"], runs) if picks["cluster"] else None,
            chaos=load_run(picks["chaos"], runs) if picks["chaos"] else None,
        )
    except RunLoadError as exc:
        print(f"\nrefusing to draw: {exc}", file=sys.stderr)
        return 1

    print()
    for path in written:
        print(f"  wrote {path}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    import pandas as pd

    from .analysis.validation import validate

    path = Path(args.run)
    if path.is_dir():
        # A Phase I run records network.csv under a different schema -- network
        # data shares no dimensions with a workload sample -- so it has no
        # metrics.csv and nothing here to check. Say so in a sentence; a
        # traceback at this point reads as a broken harness rather than as the
        # wrong command, and this is a documented step in instructions.md.
        if not (path / "metrics.csv").exists():
            if (path / "network.csv").exists():
                print(
                    f"{path.name} is a Phase I network run: it records no workload "
                    "samples, so there is nothing for `validate` to check. Its "
                    "assertions are in preflight.json."
                )
                return 0
            print(f"{path} contains no metrics.csv; is it a run directory?")
            return 2
        path = path / "metrics.csv"
    if not path.exists():
        print(f"{path} does not exist")
        return 2
    df = pd.read_csv(path)
    profile_ceiling = args.tps_ceiling
    report = validate(df, tps_ceiling=profile_ceiling)

    for finding in report.findings:
        print(f"[{finding.severity.upper():7}] {finding.check}: {finding.message}")
    if report.ok:
        print("PASS: no consistency errors detected")
    else:
        print("FAIL: run is not internally consistent and must not be used for figures")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = Profile.load(args.name)
    print(json.dumps(profile.to_dict(), indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crdblab",
        description="Multi-cloud CockroachDB benchmarking and chaos testbed harness",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser(
        "capture",
        help="capture raw generator output and report its column layout",
    )
    cap.add_argument("--node", default="linode-1", help="node on which to run the generator")
    cap.add_argument("--generator", default="ycsb", choices=("ycsb", "kv"))
    cap.add_argument("--concurrency", type=int, default=10)
    cap.add_argument("--duration", type=int, default=15)
    # ycsb
    cap.add_argument(
        "--workload",
        default="CUSTOM",
        help="ycsb workload type A-F or CUSTOM (default: CUSTOM, to hold the "
        "80/20 read/update mix of the original design)",
    )
    cap.add_argument("--read-freq", type=float, default=0.8)
    cap.add_argument("--update-freq", type=float, default=0.2)
    cap.add_argument(
        "--seed",
        type=int,
        default=42,
        help="generator key seed; MUST match the seed the table was loaded with, "
        "or every lookup silently matches nothing (see docs/defects.md, D8)",
    )
    cap.add_argument(
        "--insert-count",
        type=int,
        default=125_000,
        help="size of the loaded keyspace; must match the value used at load time",
    )
    cap.add_argument(
        "--request-distribution",
        default="uniform",
        choices=("uniform", "zipfian", "latest"),
        help="ycsb key distribution; uniform matches kv's scattered keys, whereas "
        "the CUSTOM default of zipfian would concentrate on a hot subset",
    )
    # kv (retained for comparison against the legacy configuration only)
    cap.add_argument("--read-percent", type=int, default=80)
    cap.add_argument(
        "--cycle-length",
        type=int,
        default=1_000_000,
        help="kv only; note that kv reads cannot reach pre-loaded rows regardless "
        "of this value (see docs/defects.md, D8)",
    )
    cap.add_argument("--pty", action="store_true", help="allocate a pseudo-terminal")
    cap.add_argument("--output", default="tests/fixtures/workload/captured.txt")
    cap.set_defaults(func=_cmd_capture)

    net = sub.add_parser("net", help="Phase I: network substrate characterisation")
    net_sub = net.add_subparsers(dest="net_command", required=True)
    probe = net_sub.add_parser(
        "probe", help="measure the all-pairs RTT matrix and derive the quorum floor"
    )
    probe.add_argument("--profile", default="thesis")
    probe.add_argument(
        "--database",
        default="ycsb",
        help="database whose leaseholder placement to check; must be the one the "
        "workload targets, since cluster-wide counts include system ranges",
    )
    probe.add_argument(
        "--no-checks",
        dest="checks",
        action="store_false",
        help="record the matrix without asserting clock offset and leaseholder placement",
    )
    probe.set_defaults(func=_cmd_net_probe)

    ben = sub.add_parser(
        "bench", help="Phases II and III: steady-state throughput and latency"
    )
    ben.add_argument(
        "scope",
        choices=("single", "cluster"),
        help="single: unreplicated baseline on the local node. "
        "cluster: five-node cluster driven from its gateway.",
    )
    ben.add_argument("--profile", default="thesis")
    ben.add_argument("--database", default="ycsb")
    ben.add_argument(
        "--network-run",
        help="network.csv from a Phase I run, supplying the quorum floor; "
        "defaults to the most recent one in the runs directory",
    )
    ben.add_argument(
        "--no-checks",
        dest="checks",
        action="store_false",
        help="skip pre-flight and the per-tier row-match probe. Produces a run "
        "that must not be used for figures; for harness debugging only.",
    )
    ben.set_defaults(func=_cmd_bench)

    cha = sub.add_parser("chaos", help="Phase IV: fault injection, RTO and RPO")
    cha_sub = cha.add_subparsers(dest="chaos_command", required=True)
    cha_run = cha_sub.add_parser("run", help="inject a fault into a steady-state run")
    cha_run.add_argument(
        "--mode",
        required=True,
        choices=("dead", "recover"),
        help="dead: kill the process. recover: sever and restore the overlay network.",
    )
    cha_run.add_argument("--profile", default="thesis")
    cha_run.add_argument("--database", default="ycsb")
    cha_run.set_defaults(func=_cmd_chaos)

    ana = sub.add_parser(
        "analyze",
        help="Stage 5: steady-state, Raft overhead and resilience analysis",
    )
    ana_sub = ana.add_subparsers(dest="analysis", required=True)

    ss = ana_sub.add_parser(
        "steady-state", help="Phase II or III throughput and latency by tier"
    )
    ss.add_argument("run", help="run id or directory")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=_cmd_analyze, analysis="steady-state")

    ro = ana_sub.add_parser(
        "raft-overhead",
        help="replication cost: phase III against phase II, at matched throughput",
    )
    ro.add_argument("--baseline", required=True, help="phase II run id or directory")
    ro.add_argument("--cluster", required=True, help="phase III run id or directory")
    ro.add_argument(
        "--op",
        default="update",
        help="operation type to compare; writes are the path replication affects",
    )
    ro.add_argument(
        "--accept-hardware-difference",
        action="store_true",
        help="proceed even though the two runs were measured on different CPU "
        "models or memory sizes. Use only when that difference is a stated "
        "limitation of the study rather than a mistake; it is recorded as a "
        "warning in the output. Latency ratios on a network-bound path are "
        "least affected by it, absolute throughput most.",
    )
    ro.add_argument("--json", action="store_true")
    ro.set_defaults(func=_cmd_analyze, analysis="raft-overhead")

    rs = ana_sub.add_parser("resilience", help="Phase IV RTO and RPO with their limits")
    rs.add_argument("run", help="chaos run id or directory")
    rs.add_argument(
        "--network-run",
        help="network.csv from a Phase I run, supplying the quorum geometry that "
        "explains an undefined performance RTO; defaults to the most recent",
    )
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=_cmd_analyze, analysis="resilience")

    rep = sub.add_parser("report", help="render dissertation figures from validated runs")
    rep_sub = rep.add_subparsers(dest="report_command", required=True)
    figs = rep_sub.add_parser("figures", help="render every figure whose inputs exist")
    figs.add_argument("--out", default="figures", help="output directory")
    figs.add_argument("--network", help="Phase I run id (default: most recent)")
    figs.add_argument("--baseline", help="Phase II run id (default: most recent)")
    figs.add_argument("--cluster", help="Phase III run id (default: most recent)")
    figs.add_argument("--chaos", help="Phase IV run id (default: most recent recover run)")
    figs.set_defaults(func=_cmd_report)

    val = sub.add_parser("validate", help="check a run for internal consistency")
    val.add_argument("run", help="run directory or metrics.csv path")
    val.add_argument("--tps-ceiling", type=float, default=20_000.0)
    val.add_argument("--json", action="store_true")
    val.set_defaults(func=_cmd_validate)

    prof = sub.add_parser("profile", help="print a resolved experiment profile")
    prof.add_argument("name", default="thesis", nargs="?")
    prof.set_defaults(func=_cmd_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

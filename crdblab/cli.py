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
import time
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
    target = bench.cluster_target(settings, args.database, args.engine)

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
        print("\nThis run must not be used for figures.", file=sys.stderr)
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
        run_dir, events = p4_chaos.run(settings, profile, args.mode, database=args.database, engine=args.engine)
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

    # The probe measures the same thing as `RTO availability` above, from a
    # separate client at a finer resolution. Both are printed, and printed
    # adjacently, because the useful thing about a second measurement is whether
    # it agrees with the first.
    probe = events.get("probe") or {}
    if not probe.get("enabled"):
        print("  RTO probe         disabled for this run (chaos.probe_enabled)")
    elif probe.get("error"):
        print(f"  RTO probe         FAILED: {probe['error']}", file=sys.stderr)
    else:
        p_rto = probe.get("rto") or {}
        resolution = p_rto.get("resolution_s")
        print(
            "  RTO probe         "
            + (p_rto.get("claim") or p_rto.get("detail", "not measured"))
        )
        if p_rto.get("observed_outage_s") is not None:
            if p_rto.get("fault_attributable", True):
                print(
                    f"     write outage   {p_rto['observed_outage_s'] * 1000:.0f} ms between "
                    "served canary writes (the offset-cancelling figure; prefer it)"
                )
            else:
                # The gap exists but failed the exceedance-rate test: it is not
                # distinguishable from this probe's own tail over a longer
                # window. The claim string above already says so; this is the
                # supporting evidence for it, not a second number to quote.
                attribution = p_rto.get("attribution", {})
                print(
                    f"     (unattributed gap {p_rto['observed_outage_s'] * 1000:.0f} ms; "
                    f"{attribution.get('post_fault_exceedances')} large gaps observed "
                    f"after the fault vs {attribution.get('expected_post_fault_exceedances')} "
                    "expected from the pre-fault rate -- not a recovery time)"
                )
        if p_rto.get("detection_lag_s") is not None:
            print(
                f"     detection      {p_rto['detection_lag_s'] * 1000:.0f} ms from the "
                "fault to the first blocked or failed write"
            )
        print(
            f"     sampling       {probe.get('attempts', 0)} attempts, "
            f"{probe.get('achieved_rate_per_s')}/s achieved of "
            f"{1 / probe['dispatch_interval_s']:.0f}/s dispatched"
            if probe.get("dispatch_interval_s")
            else f"     sampling       {probe.get('attempts', 0)} attempts"
        )
        if resolution:
            print(
                f"     resolution     {resolution * 1000:.1f} ms; an interval shorter "
                "than this is indistinguishable from no interruption"
            )
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
    from .analysis import engine_comparison, resilience, steady_state
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

        if args.analysis == "engine-comparison":
            crdb = load_run(args.crdb, settings.runs_dir)
            pg = load_run(args.pg, settings.runs_dir)
            try:
                result = engine_comparison.compare(
                    crdb,
                    pg,
                    args.op,
                    accept_hardware_difference=args.accept_hardware_difference,
                )
            except engine_comparison.NotComparable as exc:
                print(f"refusing to compare: {exc}", file=sys.stderr)
                print(
                    "\nThese two runs differ in more than the engine under test, "
                    "so their difference is not replication cost.",
                    file=sys.stderr,
                )
                return 1
            if args.json:
                emit(result)
                return 0

            print(f"CockroachDB {crdb.run_id}")
            print(f"PostgreSQL  {pg.run_id}")
            print(f"\nthroughput-latency curve ({args.op}):")
            print(engine_comparison.curves(crdb, pg, args.op).to_string(index=False))
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
                    # match utilisation: at one engine's peak it is at 100% of
                    # its capacity while the other is at 72% of its, so most of
                    # the ratio there is queueing rather than replication cost.
                    # Printing the four ratios undifferentiated invites the
                    # largest one to be quoted.
                    mark = " <-- least confounded" if point is best or (
                        best and point["throughput_tps"] == best.get("throughput_tps")
                    ) else ""
                    print(
                        f"  {point['throughput_tps']:>8.0f} ops/s: "
                        f"CockroachDB {point['phase_ii_latency_ms']:.2f} ms, "
                        f"PostgreSQL {point['phase_iii_latency_ms']:.2f} ms "
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
            print("\nat matched utilisation (the engines are at DIFFERENT throughputs here):")
            if not util.get("comparable"):
                print(f"  NOT AVAILABLE. {util.get('reason')}")
            else:
                print(
                    f"  capacity: CockroachDB {util['phase_ii_peak_tps']:.0f} ops/s, "
                    f"PostgreSQL {util['phase_iii_peak_tps']:.0f} ops/s"
                )
                for point in util["points"]:
                    print(
                        f"  {point['utilisation']:>5.0%} of capacity: "
                        f"CockroachDB {point['phase_ii_latency_ms']:.2f} ms "
                        f"@{point['phase_ii_tps']:.0f} ops/s, "
                        f"PostgreSQL {point['phase_iii_latency_ms']:.2f} ms "
                        f"@{point['phase_iii_tps']:.0f} ops/s "
                        f"({point['overhead_x']:.2f}x)"
                    )
                print(f"  caveat: {util['caveat']}")

            light = result["lightest_load_write_latency"]
            if light.get("ratio_x"):
                print(
                    f"\nlightest-load write median: "
                    f"CockroachDB {light['phase_ii']['p50_ms']:.2f} ms at "
                    f"{light['phase_ii']['offered_load_tps']:.0f} ops/s, "
                    f"PostgreSQL {light['phase_iii']['p50_ms']:.2f} ms at "
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
        if summary.get("fault_landed") is False:
            print(
                "\n*** THE FAULT DID NOT LAND ***\n"
                f"  the {summary['mode']!r} injection on {summary['target']} was "
                "refused by the target; it kept serving for the whole run.\n"
                "  Every figure below therefore describes an undisturbed cluster "
                "and is not a resilience result.\n"
                "  See events.json -> injected.detail, and the manifest note."
            )
        clock = summary["clock_alignment"]
        print(f"\nclock alignment: {clock['method']}")
        print(f"  {clock['detail']}")

        avail = summary["availability_rto"]
        print("\nRTO, availability:")
        print(f"  {avail.get('claim', avail.get('detail'))}")
        if avail.get("caveat"):
            print(f"  caveat: {avail['caveat']}")

        probe = summary.get("probe_rto") or {}
        if probe.get("available"):
            print("\nRTO, availability (high-frequency probe):")
            print(f"  {probe.get('claim', probe.get('detail'))}")
            if probe.get("observed_outage_s") is not None:
                if probe.get("fault_attributable", True):
                    print(
                        f"  {probe['observed_outage_s'] * 1000:.0f} ms between served canary "
                        "writes. Prefer this figure: it is the interval between two "
                        "of the probe's own observations, so whatever each timestamp "
                        "carries in link cost cancels between them -- which it does "
                        "not when a probe timestamp is differenced against the fault's"
                    )
                else:
                    attribution = probe.get("attribution", {})
                    print(
                        f"  gap of {probe['observed_outage_s'] * 1000:.0f} ms observed, but "
                        "NOT attributed to the fault: "
                        f"{attribution.get('post_fault_exceedances')} gaps over the healthy "
                        f"95th percentile occurred afterward against "
                        f"{attribution.get('expected_post_fault_exceedances')} expected from "
                        "the pre-fault rate (ratio "
                        f"{attribution.get('exceedance_rate_ratio')}x). A longer post-fault "
                        "window draws a larger maximum from the same tail on its own; do "
                        "not quote this as a recovery time"
                    )
            if probe.get("detection_lag_s") is not None:
                print(
                    f"  {probe['detection_lag_s'] * 1000:.0f} ms from the fault to the "
                    "first blocked or failed write, which is detection and not recovery"
                )
            if probe.get("resolution_s"):
                print(f"  resolution {probe['resolution_s'] * 1000:.1f} ms")
        elif probe.get("detail"):
            print(f"\nRTO, availability (high-frequency probe):\n  {probe['detail']}")

        perf = summary["performance_rto"]
        print("\nRTO, performance:")
        print(f"  {perf.get('claim', perf.get('detail'))}")
        if perf.get("post_fault_state", {}).get("mean_tps"):
            state = perf["post_fault_state"]
            frac = state.get('fraction_of_baseline')
            frac_str = f", {frac:.0%} of baseline" if frac is not None else ""
            print(
                f"  settled at {state['mean_tps']:.0f} ops/s "
                f"(sd {state['sd_tps']:.0f}{frac_str}) over {state['intervals']} intervals"
            )

        wlat = summary.get("write_latency_recovery") or {}
        if wlat.get("available"):
            print(f"\nRTO, write-path latency ({wlat.get('op', 'update')}):")
            print(f"  {wlat.get('claim', wlat.get('detail'))}")
            if wlat.get("classification") == "structural_latency_shift":
                print(
                    "  a throughput-based recovery figure recovering alongside this "
                    "is not a contradiction -- see quorum geometry below"
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
        "cluster": args.cluster or _latest_run(runs, "bench_cluster"),
    }
    # One Phase III/IV figure per fault class, so the default is the most recent run
    # of *each* class. Defaulting to the recover run alone left the dead-fault
    # timeline unreachable without an explicit argument, and the figure of it in
    # ``figures/`` therefore had no invocation that reproduced it.
    chaos_picks = args.chaos or [
        run_id
        for run_id in (
            _latest_run(runs, "p4-chaos-recover"),
            _latest_run(runs, "p4-chaos-dead"),
        )
        if run_id
    ]
    for role, run_id in picks.items():
        print(f"  {role:9} {run_id or '(none found)'}")
    print(f"  {'chaos':9} {'  '.join(chaos_picks) or '(none found)'}")

    try:
        written = figures.render_all(
            out_dir,
            network=load_network_run(picks["network"], runs) if picks["network"] else None,
            cluster=load_run(picks["cluster"], runs) if picks["cluster"] else None,
            chaos=[load_run(run_id, runs) for run_id in chaos_picks],
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

    from .analysis.validation import validate, validate_probe

    path = Path(args.run)
    probe_dir = path if path.is_dir() else path.parent
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
            # A standalone `crdblab probe rto` run has no generator behind it and
            # so no workload table. It is still a measurement with a manifest and
            # a schema, so it validates -- under its own checks rather than under
            # the workload ones, which have nothing to say about it.
            if (path / "rto_probe.csv").exists():
                report = validate_probe(pd.read_csv(path / "rto_probe.csv"))
                for finding in report.findings:
                    print(f"[{finding.severity.upper():7}] {finding.check}: {finding.message}")
                print(
                    "PASS: probe log is internally consistent"
                    if report.ok
                    else "FAIL: probe log is not internally consistent and must "
                    "not be used for figures"
                )
                if args.json:
                    print(json.dumps(report.to_dict(), indent=2))
                return 0 if report.ok else 1
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

    # A Phase III/IV run may also carry a probe log, under its own schema. It is
    # checked here rather than in a separate command so that "the run validates"
    # keeps meaning "everything this run recorded validates" -- a second gate
    # nobody remembers to run is not a gate.
    probe_report = None
    probe_csv = probe_dir / "rto_probe.csv" if probe_dir else None
    if probe_csv is not None and probe_csv.exists():
        probe_report = validate_probe(pd.read_csv(probe_csv))
        for finding in probe_report.findings:
            print(f"[{finding.severity.upper():7}] {finding.check}: {finding.message}")

    ok = report.ok and (probe_report is None or probe_report.ok)
    if ok:
        print(
            "PASS: no consistency errors detected"
            + (" (workload and probe logs)" if probe_report else "")
        )
    else:
        print("FAIL: run is not internally consistent and must not be used for figures")
    if args.json:
        payload = report.to_dict()
        if probe_report is not None:
            payload["probe"] = probe_report.to_dict()
        print(json.dumps(payload, indent=2))
    return 0 if ok else 1


def _cmd_probe(args: argparse.Namespace) -> int:
    """Run the RTO probe on its own, with no workload generator anywhere.

    The probe is designed to run *beside* a Phase III or IV chaos run and does so by
    default, but it is genuinely independent of the generator and this is the
    command that demonstrates it. Two uses:

    * Verifying that the probe reaches the cluster, and reading its achieved rate
      and resolution against the live link, before committing a chaos run to
      them. The numbers in its docstring are from one testbed on one day.
    * Measuring an outage caused by something other than this harness -- a manual
      restart, a provider event, a change being rolled out -- where there is no
      benchmark to attach to and the question is only how long writes stopped.

    It produces a normal run directory: manifest, probe log, attempt CSV. A
    measurement without a manifest is not usable later, and there is no reason
    for this one to be the exception.
    """
    from .core import ssh
    from .core.recorder import PROBE_COLUMNS, Manifest, MetricsWriter, RunDirectory, new_run_id, utcnow
    from .core.rto_probe import CREATE_TABLE_SQL, RtoProbe

    settings = Settings.from_env()
    profile = Profile.load(args.profile)
    chaos = profile.chaos
    gateway = settings.topology.gateway
    dsn = f"postgresql://root@{gateway.host}:26257/{args.database}?sslmode=disable"

    workers = args.workers if args.workers is not None else chaos.probe_workers
    interval = args.interval if args.interval is not None else chaos.probe_interval_s

    if not args.keep_table:
        ssh.run(
            gateway,
            f"cockroach sql --insecure --host={gateway.host}:26257 "
            f"--database={args.database} "
            f'-e "DROP TABLE IF EXISTS {chaos.probe_table}; '
            f'{CREATE_TABLE_SQL.format(table=chaos.probe_table)};"',
            timeout=60,
        )

    run_dir = RunDirectory(settings.runs_dir, new_run_id("p4-probe"))
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase="p4_probe",
        profile=profile.to_dict(),
        topology=[{"name": gateway.name, "host": gateway.host, "role": "probe endpoint"}],
        ssh_options=list(ssh.SSH_OPTIONS),
    )

    print(
        f"probing {gateway.host} for {args.duration}s: {workers} worker(s), "
        f"{interval * 1000:.1f} ms dispatch into {args.database}.{chaos.probe_table}"
    )
    probe = RtoProbe(
        dsn,
        table=chaos.probe_table,
        interval_s=interval,
        workers=workers,
        statement_timeout_ms=chaos.probe_statement_timeout_ms,
        connect_timeout_s=chaos.probe_connect_timeout_s,
        log_path=run_dir.probe_log,
    )
    manifest.clock_epoch_utc = probe.epoch_utc
    # Carriage-return progress only when someone is watching. run-experiment.sh
    # tees this to a log file, where \r produces one unreadable kilometre-long
    # line; a non-tty gets a periodic newline-terminated line instead.
    tty = sys.stdout.isatty()
    with probe:
        deadline = time.monotonic() + args.duration
        last_line = 0.0
        while time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            served = sum(1 for a in probe.attempts if a.served)
            line = (
                f"  [{probe.offset():6.1f}s] {len(probe.attempts)} attempts, "
                f"{served} served"
            )
            if tty:
                print(line, end="\r")
            elif probe.offset() - last_line >= 15.0:
                print(line)
                last_line = probe.offset()
    if tty:
        print()

    with MetricsWriter(run_dir.probe_csv, PROBE_COLUMNS) as writer:
        for attempt in sorted(probe.attempts, key=lambda a: a.complete_offset_s):
            writer.write(attempt.to_row())

    summary = probe.summary()
    manifest.finished_utc = utcnow()
    manifest.validation = {"probe": summary}
    run_dir.write_manifest(manifest)
    run_dir.write_events({"phase": "p4_probe", "probe": {"enabled": True, **summary}})

    if probe.error:
        print(f"probe failed: {probe.error}", file=sys.stderr)
        return 1

    print(f"run: {run_dir.path}")
    print(f"  attempts      {summary['attempts']}  {summary['outcomes']}")
    print(
        f"  achieved      {summary['achieved_rate_per_s']}/s of "
        f"{1 / interval:.0f}/s dispatched "
        f"({summary['dispatch_saturation_pct']}% of ticks found every worker busy)"
    )
    print(f"  median write  {summary['median_write_ms']} ms")
    print(
        f"  resolution    {summary['resolution_s'] * 1000:.1f} ms"
        if summary["resolution_s"]
        else "  resolution    not measurable (fewer than two served writes)"
    )
    print(f"\nlog: {run_dir.probe_log}")
    print(f"next: crdblab validate {run_dir.path}")
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = Profile.load(args.name)
    print(json.dumps(profile.to_dict(), indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crdblab",
        description="Multi-cloud Database benchmarking and chaos testbed harness",
    )
    parser.add_argument(
        "--engine",
        default="cockroachdb",
        choices=("cockroachdb", "postgresql"),
        help="target database engine (default: cockroachdb)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser(
        "capture",
        help="capture raw generator output and report its column layout",
    )
    cap.add_argument(
        "--node",
        default="gcp-1",
        help="cluster node on which to run the generator, to pin the column "
        "layout the deployed CockroachDB version emits; defaults to the "
        "gateway (crdblab.topology). A measured sweep instead runs the "
        "generator from the dedicated client node, but the output format this "
        "captures does not depend on which node ran it, only on the "
        "CockroachDB version.",
    )
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
        "or every lookup silently matches nothing (D8)",
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
        "of this value (D8)",
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
        "bench", help="Phase II: steady-state throughput and latency"
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

    prb = sub.add_parser(
        "probe",
        help="high-frequency availability probe, standalone (no workload generator)",
    )
    prb_sub = prb.add_subparsers(dest="probe_command", required=True)
    prb_rto = prb_sub.add_parser(
        "rto",
        help="write canary rows continuously and record when the database "
        "stopped and resumed serving them",
    )
    prb_rto.add_argument(
        "--duration", type=int, default=60, help="seconds to probe for (default: 60)"
    )
    prb_rto.add_argument("--profile", default="thesis")
    prb_rto.add_argument(
        "--database",
        default="bench",
        help="database holding the canary table; deliberately not the workload's",
    )
    prb_rto.add_argument(
        "--workers",
        type=int,
        help="concurrent in-flight canary writes; overrides the profile. This is "
        "the resolution dial: the gap between observations is roughly the write "
        "cost over this number",
    )
    prb_rto.add_argument(
        "--interval",
        type=float,
        help="dispatch cadence in seconds; overrides the profile. Ticks that find "
        "every worker busy are dropped and counted, so this is an upper bound on "
        "the sampling rate rather than the rate itself",
    )
    prb_rto.add_argument(
        "--keep-table",
        action="store_true",
        help="do not drop and recreate the canary table first. For probing a "
        "cluster you would rather not issue DDL against",
    )
    prb_rto.set_defaults(func=_cmd_probe)

    cha = sub.add_parser("chaos", help="Phases III-IV: fault injection, RTO and RPO")
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
        "steady-state", help="one run's throughput and latency by tier"
    )
    ss.add_argument("run", help="run id or directory")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=_cmd_analyze, analysis="steady-state")

    ec = ana_sub.add_parser(
        "engine-comparison",
        help="replication cost: CockroachDB against PostgreSQL+Patroni on the "
        "same five-node topology, at matched throughput",
    )
    ec.add_argument("--crdb", required=True, help="CockroachDB run id or directory")
    ec.add_argument("--pg", required=True, help="PostgreSQL run id or directory")
    ec.add_argument(
        "--op",
        default="update",
        help="operation type to compare; writes are the path replication affects",
    )
    ec.add_argument(
        "--accept-hardware-difference",
        action="store_true",
        help="proceed even though the two runs were measured on different CPU "
        "models or memory sizes. Use only when that difference is a stated "
        "limitation of the study rather than a mistake; it is recorded as a "
        "warning in the output. Latency ratios on a network-bound path are "
        "least affected by it, absolute throughput most. Both engines run on "
        "the same five-node topology, so this should not normally be needed.",
    )
    ec.add_argument("--json", action="store_true")
    ec.set_defaults(func=_cmd_analyze, analysis="engine-comparison")

    rs = ana_sub.add_parser("resilience", help="Phases III-IV: RTO and RPO with their limits")
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
    figs.add_argument("--cluster", help="benchmark cluster run id (default: most recent)")
    figs.add_argument(
        "--chaos",
        action="append",
        help="Phase III/IV run id; repeatable (default: the most recent run of each "
        "fault class, one figure per class)",
    )
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

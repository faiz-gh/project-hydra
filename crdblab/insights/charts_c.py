"""Group C: resilience, from the newest chaos run of each fault class, per engine.

Recovery figures come from :mod:`crdblab.analysis.resilience`. Time axes use
the harness clock, on which the fault is recorded.
"""

from __future__ import annotations

import numpy as np

from ..analysis import resilience
from ..report.style import BLUE_RAMP, CRITICAL, GRID, INK_MUTED, SERIES, WARNING
from ._base import COLOR, LABEL, Context, Drawn, chart, need, new_figure, save
from .data import cluster_only, fault_offset, hardware, probe_attempts

#: Probe outcome -> colour. ``ok`` is the only outcome that establishes the
#: database was serving; the three failure outcomes keep their distinct meanings.
OUTCOME_COLORS = {
    "ok": SERIES[2],
    "timeout": "#e8a0a0",
    "conn_error": CRITICAL,
    "refused": WARNING,
}


def _chaos(ctx: Context) -> list[tuple[str, str, object]]:
    return need(
        ctx.inventory.chaos(),
        "no chaos run passed the loader's gates for either engine or fault class",
    )


def _summaries(ctx: Context) -> dict[str, dict]:
    """``resilience.availability`` / ``probe_availability`` per chaos run, computed once."""

    def compute():
        out = {}
        for engine, mode, run in ctx.inventory.chaos():
            out[run.run_id] = {
                "availability": resilience.availability(run),
                "probe": resilience.probe_availability(run),
                "rpo": resilience.rpo(run),
            }
        return out

    return ctx.memo("resilience", compute)


def _runs(chaos) -> list:
    return [run for _, _, run in chaos]


def _mark_fault(ax, run) -> None:
    fault = fault_offset(run)
    if fault is not None:
        ax.axvline(fault, color=CRITICAL, linewidth=1.2)
        ax.text(fault, 0.97, " fault", transform=ax.get_xaxis_transform(),
                color=CRITICAL, fontsize=6, va="top")


@chart(
    "C1",
    "Probe attempt strip",
    """One vertical rule per canary write, placed at the moment it completed and
    coloured by outcome. The outage is the blank band: this is the outage as
    directly observed, with no statistic between the reader and the measurement.
    Writes are placed by completion time, never dispatch time.""",
)
def c1_probe_strip(ctx: Context) -> Drawn:
    chaos = [(e, m, r) for e, m, r in _chaos(ctx) if probe_attempts(r) is not None]
    need(chaos, "no chaos run carries an RTO probe log (rto_probe.csv)")
    fig, axes = new_figure(len(chaos), 1, figsize=(6.2, 1.25 * len(chaos)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, (engine, mode, run) in zip(axes, chaos):
        attempts = probe_attempts(run)
        for outcome, rows in attempts.groupby("outcome", sort=False):
            ax.vlines(rows["complete_offset_s"], 0, 1,
                      colors=OUTCOME_COLORS.get(outcome, INK_MUTED), linewidth=0.3,
                      label=outcome)
        ax.set_yticks([])
        ax.set_ylim(0, 1)
        ax.grid(False)
        ax.set_title(f"{LABEL[engine]} - {mode}: {len(attempts)} canary writes", loc="left")
        ax.legend(fontsize=4.5, loc="upper right")
        counts = attempts["outcome"].value_counts()
        stats[f"{engine}_{mode}_attempts"] = int(len(attempts))
        stats[f"{engine}_{mode}_outcomes"] = {str(k): int(v) for k, v in counts.items() if v}
    axes[-1].set_xlabel("seconds from run start (write completion time)")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "C1", fig, axes, _runs(chaos)))


@chart(
    "C2",
    "Probe latency through the fault",
    """Duration of every served canary write, log scale. A step in the floor after the
    fault is a structural change in the write path -- the new primary or
    leaseholder is a different distance away -- and is a separate finding from how
    long writes were unavailable.""",
)
def c2_probe_latency(ctx: Context) -> Drawn:
    chaos = [(e, m, r) for e, m, r in _chaos(ctx) if probe_attempts(r) is not None]
    need(chaos, "no chaos run carries an RTO probe log (rto_probe.csv)")
    fig, axes = new_figure(len(chaos), 1, figsize=(6.2, 2.1 * len(chaos)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, (engine, mode, run) in zip(axes, chaos):
        served = probe_attempts(run)
        served = served[served["outcome"] == "ok"]
        ax.scatter(served["complete_offset_s"], served["duration_ms"], s=0.6,
                   color=COLOR[engine], linewidths=0, rasterized=True)
        ax.set_yscale("log")
        _mark_fault(ax, run)
        ax.set_title(f"{LABEL[engine]} - {mode}", loc="left")
        ax.set_ylabel("canary write (ms, log)", fontsize=6)
        fault = fault_offset(run)
        if fault is not None:
            before = served.loc[served["complete_offset_s"] < fault, "duration_ms"]
            after = served.loc[served["complete_offset_s"] >= fault, "duration_ms"]
            if len(before):
                stats[f"{engine}_{mode}_pre_fault_p50_ms"] = round(float(before.median()), 2)
            if len(after):
                stats[f"{engine}_{mode}_post_fault_p50_ms"] = round(float(after.median()), 2)
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "C2", fig, axes, _runs(chaos)))


def _label(engine: str, mode: str) -> str:
    return f"{LABEL[engine]}_{mode}"


@chart(
    "C3",
    "Instrument agreement",
    """The same outage as measured by two instruments that share no code path, with
    each one's own sampling resolution drawn as its error bar. Agreement within
    those bars is the defensibility claim: a single instrument can be wrong in the
    flattering direction and nothing in its own output would show it.""",
)
def c3_instrument_agreement(ctx: Context) -> Drawn:
    chaos = _chaos(ctx)
    summaries = _summaries(ctx)
    rows = []
    for engine, mode, run in chaos:
        audit = summaries[run.run_id]["availability"]
        probe = summaries[run.run_id]["probe"]
        audit_s = audit.get("availability_rto_s") if audit.get("available") else None
        probe_s = probe.get("observed_outage_s") if probe.get("available") else None
        if audit_s is None and probe_s is None:
            continue
        rows.append((engine, mode, run, audit_s, audit.get("resolution_s"),
                     probe_s, probe.get("resolution_s")))
    need(rows, "no chaos run yielded an availability RTO from either instrument")

    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = np.arange(len(rows))
    width = 0.36
    audit_vals = [r[3] if r[3] is not None else np.nan for r in rows]
    probe_vals = [r[5] if r[5] is not None else np.nan for r in rows]
    ax.bar(x - width / 2, audit_vals, width, yerr=[r[4] or 0 for r in rows],
           color=SERIES[0], capsize=3, label="audit writer")
    ax.bar(x + width / 2, probe_vals, width, yerr=[r[6] or 0 for r in rows],
           color=SERIES[1], capsize=3, label="high-frequency probe")
    ax.set_xticks(x, [f"{LABEL[r[0]]}\n{r[1]}" for r in rows], fontsize=6.5)
    ax.set_ylabel("observed outage (s)")
    ax.set_title("Do the independent instruments agree?")
    ax.legend()
    stats = {
        _label(r[0], r[1]): {
            k: v for k, v in (("audit_s", r[3]), ("probe_s", r[5])) if v is not None
        }
        for r in rows
    }
    return Drawn(stats, save(ctx, "C3", fig, ax, _runs(chaos)))


@chart(
    "C4",
    "Recovery decomposition",
    """The interval from the fault to the first blocked write is detection, not
    recovery -- the injected command returns before established connections stop
    working, so writes continue briefly after the fault is nominally in place.
    Separating the two prevents a fast failover from being credited with a slow
    detection.""",
)
def c4_recovery_decomposition(ctx: Context) -> Drawn:
    chaos = _chaos(ctx)
    summaries = _summaries(ctx)
    rows = []
    for engine, mode, run in chaos:
        probe = summaries[run.run_id]["probe"]
        detection = probe.get("detection_lag_s")
        outage = probe.get("observed_outage_s")
        if probe.get("available") and detection is not None and outage is not None:
            rows.append((engine, mode, float(detection), float(outage)))
    need(rows, "no chaos run's probe measured both a detection lag and an outage")
    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = np.arange(len(rows))
    detection = [r[2] for r in rows]
    outage = [r[3] for r in rows]
    ax.bar(x, detection, 0.6, color=SERIES[2], label="detection (fault to first blocked write)")
    ax.bar(x, outage, 0.6, bottom=detection, color=SERIES[0],
           label="observed outage (no writes served)")
    ax.set_xticks(x, [f"{LABEL[r[0]]}\n{r[1]}" for r in rows], fontsize=6.5)
    ax.set_ylabel("seconds after the fault")
    ax.set_title("What the recovery time is made of")
    ax.legend(fontsize=7)
    stats = {_label(r[0], r[1]): {"detection_s": r[2], "outage_s": r[3]} for r in rows}
    return Drawn(stats, save(ctx, "C4", fig, ax, _runs(chaos)))


@chart(
    "C5",
    "Post-fault settling",
    """Throughput through the fault, with the post-fault mean and its one-sigma band.
    The verdict is the harness's own: a run whose coefficient of variation stays
    above 0.25 has not settled, and no recovery time can be stated for it -- which
    is a result to report, not a missing number.""",
)
def c5_post_fault_settling(ctx: Context) -> Drawn:
    chaos = _chaos(ctx)
    fig, axes = new_figure(len(chaos), 1, figsize=(6.2, 2.1 * len(chaos)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, (engine, mode, run) in zip(axes, chaos):
        alignment = resilience.align(run)
        ticks = resilience.degradation_profile(run, alignment)
        x = ticks["wall_offset_s"] if "wall_offset_s" in ticks.columns else ticks["elapsed_s"]
        ax.plot(x, ticks["total_tps"], color=COLOR[engine], linewidth=1.0)
        _mark_fault(ax, run)
        state = resilience.post_fault_steady_state(run, alignment)
        mean = state.get("mean_tps")
        cv = state.get("coefficient_of_variation")
        if mean and cv is not None and np.isfinite(cv):
            sd = state.get("sd_tps") or 0.0
            ax.axhline(mean, color=INK_MUTED, linewidth=0.9, linestyle="--")
            ax.axhspan(mean - sd, mean + sd, color=GRID, alpha=0.6, zorder=0)
            frac = state.get("fraction_of_baseline")
            verdict = "settled" if state.get("settled") else "not settled"
            frac_text = f" ({frac:.0%} of baseline)" if frac is not None else ""
            ax.text(0.99, 0.04, f"{verdict}: {mean:,.0f} ops/s{frac_text}, CV={cv:.2f}",
                    transform=ax.transAxes, ha="right", va="bottom", fontsize=6,
                    color=INK_MUTED)
            stats[f"{engine}_{mode}"] = {
                "settled": bool(state.get("settled")),
                "mean_tps": mean,
                "fraction_of_baseline": frac,
                "cv": cv,
            }
        ax.set_title(f"{LABEL[engine]} - {mode}", loc="left")
        ax.set_ylabel("throughput (ops/s)", fontsize=6.5)
        ax.set_ylim(bottom=0)
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "C5", fig, axes, _runs(chaos)))


@chart(
    "C6",
    "Read and write paths after failover",
    """Read and update medians either side of the fault, log scale. This is the
    asymmetry the cross-engine write-up must quote carefully: PostgreSQL's clients
    follow the primary through HAProxy, so a promotion into another region moves the
    read path too -- and reads are 80% of this workload. A throughput RTO that never
    resolves is then a statement about where the new primary landed, not about the
    write path, and must be quoted with these read medians beside it. Note: the
    "after" median is taken from the moment of the fault, so it includes the outage's
    own intervals, which record a p50 of 0; where the outage is a large share of what
    followed the fault, the "after" median understates the latency, down to 0.""",
)
def c6_paths_after_failover(ctx: Context) -> Drawn:
    chaos = _chaos(ctx)
    stats: dict = {}
    for engine, mode, run in chaos:
        fault = fault_offset(run)
        if fault is None or not run.records_wall_clock:
            continue
        metrics = run.metrics
        entry = {}
        for op in ("read", "update"):
            rows = metrics[metrics["op"] == op]
            before = rows.loc[rows["wall_offset_s"] < fault, "p50_ms"]
            # Includes the outage's own intervals (p50 = 0), as the caption says.
            after = rows.loc[rows["wall_offset_s"] >= fault, "p50_ms"]
            if len(before) and len(after):
                entry[f"{op}_p50_before_ms"] = float(before.median())
                entry[f"{op}_p50_after_ms"] = float(after.median())
        if entry:
            stats[f"{engine}_{mode}"] = entry
    need(stats, "no chaos run records per-interval latency on the harness clock")

    series = [
        ("read_p50_before_ms", "read p50 before", SERIES[2]),
        ("read_p50_after_ms", "read p50 after", SERIES[0]),
        ("update_p50_before_ms", "update p50 before", WARNING),
        ("update_p50_after_ms", "update p50 after", SERIES[1]),
    ]
    keys = list(stats)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = np.arange(len(keys))
    width = 0.2
    for index, (field, label, color) in enumerate(series):
        values = [stats[k].get(field, np.nan) for k in keys]
        values = [v if v and v > 0 else np.nan for v in values]  # log axis
        ax.bar(x + (index - 1.5) * width, values, width, color=color, label=label)
    ax.set_yscale("log")
    ax.set_xticks(x, [LABEL[k.rsplit("_", 1)[0]] + "\n" + k.rsplit("_", 1)[1] for k in keys],
                  fontsize=6.5)
    ax.set_ylabel("median latency (ms, log)")
    ax.set_title("Which path moved when the primary did")
    ax.legend(ncol=2, fontsize=6)
    return Drawn(stats, save(ctx, "C6", fig, ax, _runs(chaos)))


@chart(
    "C7",
    "Hardware through the failover",
    """Per-node CPU across the fault, with the faulted node in the reserved status
    colour. The faulted node going quiet confirms the fault landed on the machine it
    was aimed at, and a survivor rising afterwards is the promotion visible in
    hardware rather than inferred from the database's own logs.""",
)
def c7_hardware_through_failover(ctx: Context) -> Drawn:
    chaos = [(e, m, r) for e, m, r in _chaos(ctx) if hardware(r) is not None]
    need(chaos, "no chaos run carries hardware_metrics.csv")
    fig, axes = new_figure(len(chaos), 1, figsize=(6.2, 2.2 * len(chaos)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    ramp = BLUE_RAMP[2:]
    for ax, (engine, mode, run) in zip(axes, chaos):
        cluster = cluster_only(hardware(run))
        target = str((run.events or {}).get("target") or "")
        for index, node in enumerate(sorted(cluster["node"].unique())):
            rows = cluster[cluster["node"] == node]
            faulted = node == target
            ax.plot(rows["wall_offset_s"], rows["cpu_busy_pct"],
                    color=CRITICAL if faulted else ramp[index % len(ramp)],
                    linewidth=1.3 if faulted else 0.9,
                    label=f"{node} (faulted)" if faulted else node, zorder=3 if faulted else 2)
        _mark_fault(ax, run)
        ax.set_title(f"{LABEL[engine]} - {mode}, target {target}", loc="left")
        ax.set_ylabel("CPU busy (%)", fontsize=6.5)
        ax.legend(ncol=3, fontsize=5)
        stats[f"{engine}_{mode}_target"] = target
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "C7", fig, axes, _runs(chaos)))


@chart(
    "C8",
    "RTO and RPO summary",
    """Recovery time from both instruments and acknowledged writes lost, for every
    engine and fault class. RPO zero is the expected result for a quorum-replicated
    database and is meaningful only because the measurement could have shown
    otherwise: the audit client records what it was told committed and advances
    past ambiguous writes rather than retrying them.""",
)
def c8_rto_rpo(ctx: Context) -> Drawn:
    chaos = _chaos(ctx)
    summaries = _summaries(ctx)
    stats: dict = {}
    for engine, mode, run in chaos:
        s = summaries[run.run_id]
        entry = {}
        if s["availability"].get("available"):
            entry["availability_rto_s"] = s["availability"].get("availability_rto_s")
        if s["probe"].get("available"):
            entry["probe_outage_s"] = s["probe"].get("observed_outage_s")
        if s["rpo"].get("available"):
            entry["rpo_lost"] = s["rpo"].get("rpo_violations", 0)
            entry["rpo_acknowledged"] = s["rpo"].get("acknowledged", 0)
        if entry:
            stats[_label(engine, mode)] = entry
    need(stats, "no chaos run recorded an RTO or an RPO audit")

    keys = list(stats)
    labels = [f"{k.rsplit('_', 1)[0]}\n{k.rsplit('_', 1)[1]}" for k in keys]
    fig, (top, bottom) = new_figure(2, 1, figsize=(6.2, 4.8), sharex=True)
    x = np.arange(len(keys))
    width = 0.36

    def values(field):
        return [stats[k].get(field) if stats[k].get(field) is not None else np.nan for k in keys]

    top.bar(x - width / 2, values("availability_rto_s"), width, color=SERIES[0],
            label="availability RTO (audit)")
    top.bar(x + width / 2, values("probe_outage_s"), width, color=SERIES[1],
            label="observed outage (probe)")
    top.set_ylabel("seconds")
    top.set_title("Recovery time, by instrument")
    top.legend(fontsize=6)

    lost = values("rpo_lost")
    bottom.bar(x, lost, 0.5, color=CRITICAL)
    for i, key in enumerate(keys):
        if stats[key].get("rpo_acknowledged") is not None:
            bottom.text(i, 0.02, f"{stats[key].get('rpo_lost', 0)} of "
                        f"{stats[key]['rpo_acknowledged']}", ha="center", va="bottom",
                        fontsize=6, color=INK_MUTED, transform=bottom.get_xaxis_transform())
    if not np.nansum(lost):
        bottom.set_ylim(0, 1)
    bottom.set_ylabel("acknowledged writes lost")
    bottom.set_title("Data loss")
    bottom.set_xticks(x, labels, fontsize=6.5)
    fig.tight_layout()
    return Drawn(stats, save(ctx, "C8", fig, [top, bottom], _runs(chaos)))


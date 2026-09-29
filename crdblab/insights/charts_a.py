"""Group A: benchmark and saturation, from each engine's newest Phase II sweep.

Tier statistics come from :mod:`crdblab.analysis.steady_state`; nothing is
re-aggregated here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis import steady_state
from ..report.style import INK_MUTED, INK_SECONDARY, SURFACE
from ._base import (
    COLOR,
    DASH,
    LABEL,
    MARKER,
    Context,
    Drawn,
    chart,
    need,
    new_figure,
    save,
)

#: p99 budgets for A6, in ms: from the read path (a few ms) to well past the
#: write quorum floor (~70 ms).
BUDGETS_MS: tuple[int, ...] = (10, 25, 50, 100, 200, 400, 800)

_QUANTILE_DASHES = {"p50_ms": "-", "p95_ms": "--", "p99_ms": "-.", "pmax_ms": ":"}


def _bench(ctx: Context) -> dict:
    return need(
        ctx.inventory.per_engine("bench"),
        "no benchmark run passed the loader's gates for either engine",
    )


def _peak(tiers: pd.DataFrame) -> pd.Series:
    return tiers.loc[tiers["mean_total_tps"].idxmax()]


@chart(
    "A1",
    "Throughput-latency curve",
    """Each point is one concurrency tier. The curve bends upward at the knee, past
    which offered load buys queueing rather than throughput. Points are ordered by
    concurrency, not by throughput, because past saturation the curve genuinely
    bends backwards.""",
)
def a1_throughput_latency(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, run in runs.items():
        curve = steady_state.throughput_latency_curve(run, "update")
        ax.plot(
            curve["mean_total_tps"], curve["p50_ms"],
            color=COLOR[engine], marker=MARKER[engine], linestyle=DASH[engine],
            markersize=5, label=LABEL[engine],
        )
        # The knee is where the sweep stopped buying throughput: the tier of
        # peak delivered throughput. Past it, more workers only queue.
        knee = _peak(steady_state.per_tier(run))
        knee_c = int(knee["concurrency"])
        knee_tps = float(knee["mean_total_tps"])
        knee_lat = float(curve.loc[curve["concurrency"] == knee_c, "p50_ms"].iloc[0])
        ax.annotate(
            f"knee C={knee_c}\n{knee_tps:,.0f} ops/s",
            xy=(knee_tps, knee_lat), xytext=(-40, 28), textcoords="offset points",
            fontsize=7, color=INK_SECONDARY,
            arrowprops={"arrowstyle": "-", "color": INK_MUTED, "linewidth": 0.8},
        )
        stats[f"{engine}_knee_concurrency"] = knee_c
        stats[f"{engine}_knee_tps"] = round(knee_tps, 1)
        stats[f"{engine}_peak_tps"] = round(knee_tps, 1)
    ax.set_xlabel("delivered throughput (ops/s)")
    ax.set_ylabel("update p50 latency (ms)")
    ax.set_title("Throughput against the latency it cost")
    ax.legend()
    return Drawn(stats, save(ctx, "A1", fig, ax, list(runs.values())))


@chart(
    "A2",
    "Latency percentile fan",
    """p50, p95, p99 and max for each operation type, per engine, on a log axis. A fan
    that opens with concurrency is queueing; one that stays parallel is a shifted
    floor. Operation types are never pooled.""",
)
def a2_percentile_fan(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    lat = {engine: steady_state.latency_by_op(run) for engine, run in runs.items()}
    ops = sorted({op for frame in lat.values() for op in frame["op"].unique()})
    fig, axes = new_figure(len(ops), 1, figsize=(6.2, 2.7 * len(ops)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, op in zip(axes, ops):
        for engine, frame in lat.items():
            rows = frame[frame["op"] == op].groupby("concurrency", as_index=False).mean(
                numeric_only=True
            )
            if rows.empty:
                continue
            for quantile, dash in _QUANTILE_DASHES.items():
                if quantile not in rows.columns:
                    continue
                ax.plot(
                    rows["concurrency"], rows[quantile], color=COLOR[engine],
                    linestyle=dash, linewidth=1.4,
                    label=f"{LABEL[engine]} {quantile.replace('_ms', '')}",
                )
            stats[f"{engine}_{op}_p99_max_ms"] = round(float(rows["p99_ms"].max()), 2)
        ax.set_yscale("log")
        ax.set_title(f"{op}: median against the tail", loc="left")
        ax.set_ylabel(f"{op} latency (ms, log)")
        ax.legend(ncol=2, fontsize=5.5)
    axes[-1].set_xlabel("concurrency")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "A2", fig, axes, list(runs.values())))


@chart(
    "A3",
    "Concurrency slot occupancy",
    """Little's law applied per operation type: an operation occupies throughput x
    latency of the client's fixed concurrency budget, and both factors are measured
    per operation rather than assumed from the configured mix. Where reads grow
    expensive they crowd out writes for the same slots, which is a different
    statement from either one simply being slow.""",
)
def a3_slot_occupancy(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, run in runs.items():
        # Per operation and tier: mean ops/s x mean p50 is the mean number of that
        # operation in flight (Little's law, L = lambda W).
        per = run.metrics.groupby(["op", "concurrency"], as_index=False).agg(
            tps=("tps", "mean"), p50_ms=("p50_ms", "mean")
        )
        per["slots"] = per["tps"] * per["p50_ms"] / 1000.0
        top = per["concurrency"].max()
        for op, dash in zip(sorted(per["op"].unique()), ("-", "--", "-.", ":")):
            rows = per[per["op"] == op].sort_values("concurrency")
            ax.plot(
                rows["concurrency"], rows["slots"], color=COLOR[engine],
                marker=MARKER[engine], linestyle=dash, markersize=4,
                label=f"{LABEL[engine]} {op}",
            )
            at_top = rows.loc[rows["concurrency"] == top, "slots"]
            stats[f"{engine}_{op}_slots_at_max_c"] = round(float(at_top.iloc[0]), 1)
    ax.plot([], [], " ", label="(slots = throughput x latency)")
    ax.set_xlabel("offered concurrency")
    ax.set_ylabel("occupied slots (Little's law)")
    ax.set_title("How the fixed concurrency budget is spent")
    ax.legend(fontsize=7)
    return Drawn(stats, save(ctx, "A3", fig, ax, list(runs.values())))


#: Within-tier coefficient of variation above which a tier was still moving
#: (same threshold as resilience analysis).
SETTLED_CV = 0.25


@chart(
    "A4",
    "Steady-state stability",
    """Within-tier coefficient of variation of throughput. A tier above the line was
    still moving while it was being recorded, and its mean is a average over a
    transient rather than a steady state.""",
)
def a4_stability(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, run in runs.items():
        grouped = run.ticks().groupby("concurrency")["total_tps"]
        cv = (grouped.std() / grouped.mean()).dropna()
        ax.plot(
            cv.index, cv.values, color=COLOR[engine], marker=MARKER[engine],
            linestyle=DASH[engine], markersize=5, label=LABEL[engine],
        )
        stats[f"{engine}_max_cv"] = round(float(cv.max()), 3)
        stats[f"{engine}_worst_tier"] = int(cv.idxmax())
    ax.axhline(SETTLED_CV, color=INK_MUTED, linewidth=1.0)
    ax.text(
        ax.get_xlim()[0], SETTLED_CV, " CV = 0.25, the settled threshold used throughout",
        fontsize=6.5, color=INK_MUTED, va="bottom",
    )
    ax.set_xlabel("concurrency")
    ax.set_ylabel("coefficient of variation of throughput")
    ax.set_title("Was each tier steady while it was measured?")
    ax.legend()
    return Drawn(stats, save(ctx, "A4", fig, ax, list(runs.values())))


@chart(
    "A5",
    "Error rate against load",
    """Errors per tier. The bench sweep runs without --tolerate-errors, so a non-zero
    count here would mean the generator survived something it was not configured to
    absorb; zero is the expected result and is what makes the throughput figures
    quotable.""",
)
def a5_errors(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, run in runs.items():
        tiers = steady_state.per_tier(run)
        ax.plot(
            tiers["concurrency"], tiers["errors_cum"], color=COLOR[engine],
            marker=MARKER[engine], linestyle=DASH[engine], markersize=5,
            label=LABEL[engine],
        )
        stats[f"{engine}_errors_total"] = int(tiers["errors_cum"].sum())
    if not any(stats.values()):
        ax.text(
            0.5, 0.5, "no errors were recorded on any tier of either engine",
            transform=ax.transAxes, ha="center", va="center", fontsize=7.5,
            color=INK_MUTED,
        )
    ax.set_xlabel("concurrency")
    ax.set_ylabel("errors observed in tier")
    ax.set_title("Errors against offered load")
    ax.legend()
    return Drawn(stats, save(ctx, "A5", fig, ax, list(runs.values())))


@chart(
    "A6",
    "Throughput under a latency budget",
    """The highest measured throughput whose p99 stayed inside each budget. A zero bar
    means no tier met that budget at all. This restates the same tiers as A1 in the
    form a service owner buys: peak throughput at an unbounded tail is not
    deliverable capacity.""",
)
def a6_latency_budget(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    x = np.arange(len(BUDGETS_MS))
    width = 0.8 / max(1, len(runs))
    for index, (engine, run) in enumerate(runs.items()):
        tiers = steady_state.per_tier(run).set_index("concurrency")
        lat = steady_state.latency_by_op(run)
        p99 = lat[lat["op"] == "update"].groupby("concurrency")["p99_ms"].mean()
        joined = tiers.join(p99, how="inner")
        values = []
        for budget in BUDGETS_MS:
            within = joined.loc[joined["p99_ms"] <= budget, "mean_total_tps"]
            best = round(float(within.max()), 1) if len(within) else 0.0
            values.append(best)
            stats[f"{engine}_tps_under_p99_{budget}ms"] = best
        offset = (index - (len(runs) - 1) / 2) * width
        ax.bar(x + offset, values, width, color=COLOR[engine], label=LABEL[engine])
    ax.set_xticks(x, [f"{b} ms" for b in BUDGETS_MS])
    ax.set_xlabel("update p99 budget")
    ax.set_ylabel("highest throughput meeting the budget (ops/s)")
    ax.set_title("Capacity that is actually usable under a tail-latency budget")
    ax.legend()
    return Drawn(stats, save(ctx, "A6", fig, ax, list(runs.values())))


@chart(
    "A7",
    "Throughput by concurrency",
    """Mean throughput per tier against the concurrency that produced it. Flattening
    or falling back past some concurrency is saturation; A1 shows what that
    saturation costs in latency.""",
)
def a7_throughput_by_concurrency(ctx: Context) -> Drawn:
    runs = _bench(ctx)
    fig, ax = new_figure(figsize=(5.6, 3.6))
    stats: dict = {}
    any_interval = False
    for engine, run in runs.items():
        tiers = steady_state.per_tier(run)
        # 95% interval over repetitions; zero-width (not drawn) for a single repetition.
        errors = [0.0 if v is None or pd.isna(v) else float(v) for v in tiers["ci95_half_width_tps"]]
        has_interval = any(e > 0 for e in errors)
        any_interval |= has_interval
        ax.errorbar(
            tiers["concurrency"], tiers["mean_total_tps"],
            yerr=errors if has_interval else None,
            color=COLOR[engine], marker=MARKER[engine], linestyle=DASH[engine],
            markersize=6, markeredgecolor=SURFACE, markeredgewidth=2, capsize=3,
            label=LABEL[engine],
        )
        peak = _peak(tiers)
        stats[f"{engine}_peak_tps"] = round(float(peak["mean_total_tps"]), 1)
        stats[f"{engine}_peak_concurrency"] = int(peak["concurrency"])
    if not any_interval:
        ax.text(
            0.99, 0.03, "single repetition per tier: no interval estimate",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color=INK_MUTED,
        )
    ax.set_xlabel("offered concurrency (workers)")
    ax.set_ylabel("throughput (ops/s), summed across operation types")
    ax.set_title("Steady-state throughput by concurrency", loc="left")
    ax.set_ylim(bottom=0)
    ax.legend(labelcolor=INK_SECONDARY)
    return Drawn(stats, save(ctx, "A7", fig, ax, list(runs.values())))

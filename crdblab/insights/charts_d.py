"""Group D: engine comparison, drawn from :func:`engine_comparison.compare`.

Uses the newest benchmark run of each engine. If the two runs are not
comparable, every chart here skips with the comparison's own reason.
"""

from __future__ import annotations

import numpy as np

from ..analysis import engine_comparison, resilience
from ..report.style import INK_MUTED, INK_SECONDARY
from ._base import (
    COLOR,
    DASH,
    LABEL,
    MARKER,
    Context,
    Drawn,
    Skip,
    chart,
    new_figure,
    save,
)

ARMS = {"cockroachdb": "crdb", "postgresql": "pg"}


def _pair(ctx: Context):
    runs = ctx.inventory.per_engine("bench")
    missing = [LABEL[e] for e in ARMS if e not in runs]
    if missing:
        raise Skip(
            f"an engine comparison needs a passing benchmark run from both engines; "
            f"none for {', '.join(missing)}"
        )
    return runs["cockroachdb"], runs["postgresql"]


def _comparison(ctx: Context) -> dict:
    crdb, pg = _pair(ctx)

    def compute():
        try:
            return engine_comparison.compare(crdb, pg, "update")
        except engine_comparison.NotComparable as exc:
            return {"_refused": str(exc)}

    result = ctx.memo("comparison", compute)
    if "_refused" in result:
        raise Skip(f"the two benchmark runs are not comparable: {result['_refused']}")
    return result


@chart(
    "D1",
    "Engine throughput-latency curves",
    """Each engine's own curve, annotated with the concurrency that produced each
    point. Read the horizontal distance as capacity and the vertical as cost; quote
    the saturation point rather than a single ratio, because the gap between the
    curves depends entirely on where along them it is measured.""",
)
def d1_curves(ctx: Context) -> Drawn:
    result = _comparison(ctx)
    crdb, pg = _pair(ctx)
    curves = engine_comparison.curves(crdb, pg, "update")
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, run in (("cockroachdb", crdb), ("postgresql", pg)):
        rows = curves[curves["run_id"] == run.run_id].sort_values("concurrency")
        ax.plot(rows["mean_total_tps"], rows["p50_ms"], color=COLOR[engine],
                marker=MARKER[engine], linestyle=DASH[engine], markersize=5,
                label=LABEL[engine])
        for row in rows.itertuples():
            ax.annotate(f"C={row.concurrency}", (row.mean_total_tps, row.p50_ms),
                        xytext=(3, -8), textcoords="offset points", fontsize=5.5,
                        color=INK_MUTED)
        sat = result["saturation"][ARMS[engine]]
        stats[f"{engine}_peak_tps"] = sat.get("peak_tps")
        stats[f"{engine}_peak_concurrency"] = sat.get("peak_concurrency")
        stats[f"{engine}_saturated"] = sat.get("saturated")
    ax.set_xlabel("delivered throughput (ops/s)")
    ax.set_ylabel("update p50 latency (ms)")
    ax.set_title("Both engines, same topology, same workload")
    ax.legend()
    return Drawn(stats, save(ctx, "D1", fig, ax, [crdb, pg]))


def _paired_bars(ax, labels, crdb_values, pg_values) -> np.ndarray:
    x = np.arange(len(labels))
    width = 0.38
    ax.bar(x - width / 2, crdb_values, width, color=COLOR["cockroachdb"], label=LABEL["cockroachdb"])
    ax.bar(x + width / 2, pg_values, width, color=COLOR["postgresql"], label=LABEL["postgresql"])
    ax.set_xticks(x, labels)
    return x


@chart(
    "D2",
    "Latency at matched throughput",
    """Both engines evaluated at throughputs each of them genuinely measured -- no
    extrapolation beyond either curve. The ratio above each pair is the overhead at
    that load; the starred point is the least confounded one, where the two engines
    were closest to the same fraction of their own capacity. Do not quote the
    largest ratio: it is the one where the slower engine is nearest saturation and
    so carries the most of its own queueing.""",
)
def d2_matched_throughput(ctx: Context) -> Drawn:
    matched = _comparison(ctx)["matched_throughput"]
    if not matched.get("comparable"):
        raise Skip(f"no throughput both engines measured: {matched.get('reason')}")
    points = matched["points"]
    best = matched.get("least_confounded") or {}
    crdb, pg = _pair(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = _paired_bars(
        ax, [f"{p['throughput_tps']:,.0f}" for p in points],
        [p["crdb_latency_ms"] for p in points],
        [p["pg_latency_ms"] for p in points],
    )
    for i, p in enumerate(points):
        star = " *" if best and p["throughput_tps"] == best.get("throughput_tps") else ""
        top = max(p["crdb_latency_ms"], p["pg_latency_ms"])
        ax.text(x[i], top, f"{p['overhead_x']:.2f}x{star}", ha="center", va="bottom",
                fontsize=6, color=INK_SECONDARY)
    ax.set_xlabel("matched throughput (ops/s)")
    ax.set_ylabel("update p50 latency (ms)")
    ax.set_title("Latency at throughputs both engines actually reached")
    ax.legend()
    stats = {"points": points, "least_confounded": best}
    return Drawn(stats, save(ctx, "D2", fig, ax, [crdb, pg]))


@chart(
    "D3",
    "Latency at matched utilisation",
    """The engines held at the same fraction of their own measured capacity, so their
    queueing components are comparable and the residual is closer to the
    replication path alone. The throughputs beneath each pair are deliberately
    different -- that is what this framing holds variable -- so these bars must
    never be quoted as a cost at any particular ops/s.""",
)
def d3_matched_utilisation(ctx: Context) -> Drawn:
    util = _comparison(ctx).get("matched_utilisation") or {}
    if not util.get("comparable"):
        raise Skip(f"no utilisation both engines measured: {util.get('reason')}")
    points = util["points"]
    crdb, pg = _pair(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = _paired_bars(
        ax, [f"{p['utilisation']:.0%}" for p in points],
        [p["crdb_latency_ms"] for p in points],
        [p["pg_latency_ms"] for p in points],
    )
    for i, p in enumerate(points):
        ax.text(x[i], 1.0, f"{p['crdb_tps']:,.0f}\nvs {p['pg_tps']:,.0f} ops/s",
                ha="center", va="bottom", fontsize=4.5, color=INK_MUTED)
    ax.set_xlabel("utilisation (fraction of each engine's own peak)")
    ax.set_ylabel("update p50 latency (ms)")
    ax.set_title("Latency at equal distance from saturation")
    ax.legend()
    stats = {
        "points": points,
        "crdb_peak_tps": util.get("crdb_peak_tps"),
        "pg_peak_tps": util.get("pg_peak_tps"),
    }
    return Drawn(stats, save(ctx, "D3", fig, ax, [crdb, pg]))


def _outage(ctx: Context, engine: str, mode: str):
    run = ctx.inventory.latest(f"chaos-{mode}", engine)
    if run is None:
        return None
    probe = resilience.probe_availability(run)
    return probe.get("observed_outage_s") if probe.get("available") else None


@chart(
    "D4",
    "Engine scorecard",
    """Each row normalised to the larger of the two values so that quantities in
    different units share an axis; the raw value is printed beside every bar because
    the normalised length alone is not quotable. The direction that counts as better
    differs by row and is stated on each one.""",
)
def d4_scorecard(ctx: Context) -> Drawn:
    result = _comparison(ctx)
    crdb, pg = _pair(ctx)
    light = result.get("lightest_load_write_latency") or {}
    candidates = [
        ("peak throughput", True, {
            e: result["saturation"][ARMS[e]].get("peak_tps") for e in ARMS}),
        ("write p50 at lightest load", False, {
            e: (light.get(ARMS[e]) or {}).get("p50_ms") for e in ARMS}),
        ("outage, recover fault", False, {e: _outage(ctx, e, "recover") for e in ARMS}),
        ("outage, dead fault", False, {e: _outage(ctx, e, "dead") for e in ARMS}),
    ]
    rows = [(name, higher, vals) for name, higher, vals in candidates
            if all(v is not None for v in vals.values())]
    if not rows:
        raise Skip("no headline figure was measured for both engines")

    fig, ax = new_figure(figsize=(6.2, 4.2))
    y = np.arange(len(rows))
    height = 0.36
    for offset, engine in ((-height / 2, "cockroachdb"), (height / 2, "postgresql")):
        for i, (_, _, vals) in enumerate(rows):
            largest = max(abs(v) for v in vals.values()) or 1.0
            value = vals[engine]
            ax.barh(y[i] + offset, value / largest, height, color=COLOR[engine],
                    label=LABEL[engine] if i == 0 else None)
            ax.text(value / largest + 0.01, y[i] + offset, f"{value:,.4g}",
                    va="center", fontsize=5.5, color=INK_SECONDARY)
    ax.set_yticks(y, [f"{name}  ({'higher' if higher else 'lower'} is better)"
                      for name, higher, _ in rows], fontsize=6.5)
    ax.set_xlim(0, 1.3)
    ax.set_xlabel("normalised to the larger of the two (raw value labelled)")
    ax.set_title("Headline numbers side by side")
    ax.legend(fontsize=6, loc="lower right")
    stats = {name: dict(vals) for name, _, vals in rows}
    return Drawn(stats, save(ctx, "D4", fig, ax, [crdb, pg]))


@chart(
    "D5",
    "Cost of consistency",
    """Each engine's write median at its lightest measured load, against the quorum
    floor measured independently by ping in Phase I. The floor is what the speed of
    light and this topology cost before any database is involved -- a 3-of-5 Raft
    quorum and Patroni's ANY 2 acknowledgement are the same geometry -- so the
    excess above it is the software's own contribution. Taken at the lightest load
    because that is where queueing contributes least.""",
)
def d5_cost_of_consistency(ctx: Context) -> Drawn:
    result = _comparison(ctx)
    crdb, pg = _pair(ctx)
    network = ctx.inventory.latest("network")
    floor = network.quorum_floor_ms if network is not None else None
    if floor is None:
        raise Skip("no Phase I run recorded a quorum floor to measure the engines against")
    light = result.get("lightest_load_write_latency") or {}
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {"quorum_floor_ms": floor}
    for i, engine in enumerate(ARMS):
        p50 = (light.get(ARMS[engine]) or {}).get("p50_ms")
        if p50 is None:
            continue
        ratio = round(p50 / floor, 3)
        ax.bar(i, p50, 0.5, color=COLOR[engine])
        ax.text(i, p50, f"{p50:.1f} ms\n{ratio:.2f}x floor", ha="center", va="bottom",
                fontsize=7, color=INK_SECONDARY)
        stats[f"{LABEL[engine]}_write_p50_ms"] = p50
        stats[f"{LABEL[engine]}_x_over_floor"] = ratio
    ax.axhline(floor, color=INK_MUTED, linestyle="--", linewidth=1.2)
    ax.text(0.5, floor, f"Phase I quorum floor {floor:.1f} ms -- the network's own lower bound",
            ha="center", va="bottom", fontsize=6.5, color=INK_MUTED)
    ax.set_xticks(range(len(ARMS)), [LABEL[e] for e in ARMS])
    ax.set_ylabel("write p50 at lightest measured load (ms)")
    ax.set_title("What each engine adds to the network's own floor")
    return Drawn(stats, save(ctx, "D5", fig, ax, [crdb, pg]))

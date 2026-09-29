"""CockroachDB vs PostgreSQL/Patroni on the same five-node topology.

Both engines are replicated (Raft vs. Patroni with ``synchronous_standby_names:
'ANY 2 (*)'``), so this compares two replication mechanisms.

**Concurrency is not load.** ``--concurrency`` fixes the number of workers,
not the work done, so two engines at the same tier sit at different points on
their throughput-latency curves. Comparing them there is invalid (reported
only as :func:`same_concurrency_delta`, labelled as such). Instead:

* each engine's **throughput-latency curve**;
* latency **at matched throughput**, only where the measured ranges overlap;
* latency **at matched utilisation** (equal fractions of each engine's peak);
* the **lightest-load write median**, bounded by each engine's quorum round trip.

Every comparison is gated on
:func:`crdblab.analysis.validation.check_run_comparability`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .loader import Run
from .steady_state import latency_by_op, per_tier, throughput_latency_curve
from .validation import ValidationReport, validate_comparison

#: Final-tier throughput gain below which a curve counts as saturated; above it
#: the peak is only a lower bound on capacity.
SATURATION_TOLERANCE = 0.05


class NotComparable(RuntimeError):
    """Raised when the two runs may not legitimately be compared at all."""


def curves(crdb: Run, pg: Run, op: str) -> pd.DataFrame:
    """Both engines' throughput-latency curves for one operation type, one row per tier."""
    frames = []
    for run, label in ((crdb, "CockroachDB"), (pg, "PostgreSQL")):
        frame = throughput_latency_curve(run, op)
        frame.insert(0, "phase", label)
        frame.insert(1, "run_id", run.run_id)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _saturation(tiers: pd.DataFrame) -> dict[str, Any]:
    """Whether an engine's peak measured throughput is its capacity.

    A curve still rising at its highest tier has a peak that is only a lower bound.
    """
    ordered = tiers.sort_values("concurrency")
    values = ordered["mean_total_tps"].tolist()
    peak = max(values) if values else None
    if len(values) < 2:
        return {
            "peak_tps": peak,
            "saturated": None,
            "detail": "a single tier cannot establish whether the curve has flattened",
        }
    gain = (values[-1] - values[-2]) / values[-2] if values[-2] else float("inf")
    saturated = gain < SATURATION_TOLERANCE
    return {
        "peak_tps": round(float(peak), 1),
        "peak_concurrency": int(ordered["concurrency"].iloc[values.index(peak)]),
        "final_tier_gain": round(float(gain), 4),
        "saturated": bool(saturated),
        "detail": (
            "throughput has flattened, so the peak is the measured capacity"
            if saturated
            else f"throughput is still rising by {gain:.1%} at the highest tier "
            "measured, so the peak is a lower bound on capacity, not capacity"
        ),
    }


def _interpolate(curve: pd.DataFrame, tps: float, column: str) -> float | None:
    """Latency at a given throughput, linearly between the two bracketing tiers.

    Returns ``None`` outside the measured range (no extrapolation). Only the
    rising branch up to the peak is used: past saturation one throughput maps
    to two latencies.
    """
    ordered = curve.sort_values("concurrency")
    peak = int(ordered["mean_total_tps"].to_numpy().argmax())
    ordered = ordered.iloc[: peak + 1]
    ordered = ordered.sort_values("mean_total_tps")
    xs = ordered["mean_total_tps"].tolist()
    ys = ordered[column].tolist()
    if not xs or tps < xs[0] or tps > xs[-1]:
        return None
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= tps <= x1:
            if x1 == x0:
                return float(y0)
            return float(y0 + (y1 - y0) * (tps - x0) / (x1 - x0))
    return float(ys[-1])


def _overlap_remedy(crdb: Run, pg: Run) -> str:
    """Advice on making the two engines' throughput ranges overlap.

    If the slower engine has saturated, more concurrency cannot help; the faster
    engine must be measured at lower concurrency instead.
    """
    a, b = per_tier(crdb), per_tier(pg)
    slower, faster = (b, a) if b["mean_total_tps"].max() < a["mean_total_tps"].max() else (a, b)
    slower_name = "PostgreSQL" if slower is b else "CockroachDB"
    faster_name = "CockroachDB" if slower is b else "PostgreSQL"
    saturated = _saturation(slower)["saturated"]

    if saturated:
        return (
            f"{slower_name} has already saturated (peak "
            f"{slower['mean_total_tps'].max():.0f} ops/s), so a higher concurrency "
            f"tier cannot raise it into {faster_name}'s range and would lower it. "
            f"The only way to an overlap is to measure {faster_name} at *lower* "
            f"concurrency, below its current minimum of "
            f"{faster['mean_total_tps'].min():.0f} ops/s"
        )
    return (
        f"{slower_name} is still rising at its highest tier, so extending its "
        f"sweep upward should close the gap; failing that, measure {faster_name} "
        "at lower concurrency"
    )


def matched_throughput(
    crdb: Run,
    pg: Run,
    op: str,
    quantile: str = "p50_ms",
) -> dict[str, Any]:
    """Latency of both engines at each measured tier throughput inside both ranges.

    Each point also carries both engines' utilisation, since matched throughput
    is not matched utilisation; the narrowest gap is ``least_confounded``.
    """
    a = throughput_latency_curve(crdb, op)
    b = throughput_latency_curve(pg, op)
    lo = max(a["mean_total_tps"].min(), b["mean_total_tps"].min())
    hi = min(a["mean_total_tps"].max(), b["mean_total_tps"].max())

    if lo > hi:
        return {
            "comparable": False,
            "reason": (
                f"the two engines' measured throughput ranges do not overlap: "
                f"CockroachDB spans {a['mean_total_tps'].min():.0f}-"
                f"{a['mean_total_tps'].max():.0f} ops/s and PostgreSQL "
                f"{b['mean_total_tps'].min():.0f}-{b['mean_total_tps'].max():.0f} "
                "ops/s. There is no load level at which both were measured, so a "
                "matched-throughput comparison would have to extrapolate one curve "
                "beyond the data defining it"
            ),
            "crdb_range_tps": [
                round(float(a["mean_total_tps"].min()), 1),
                round(float(a["mean_total_tps"].max()), 1),
            ],
            "pg_range_tps": [
                round(float(b["mean_total_tps"].min()), 1),
                round(float(b["mean_total_tps"].max()), 1),
            ],
            "remedy": _overlap_remedy(crdb, pg),
            "points": [],
        }

    peak_a = float(a["mean_total_tps"].max())
    peak_b = float(b["mean_total_tps"].max())
    points: list[dict[str, Any]] = []
    candidates = sorted(
        set(a["mean_total_tps"].tolist()) | set(b["mean_total_tps"].tolist())
    )
    for tps in candidates:
        if not (lo <= tps <= hi):
            continue
        ya = _interpolate(a, tps, quantile)
        yb = _interpolate(b, tps, quantile)
        if ya is None or yb is None or ya <= 0:
            continue
        util_a = float(tps) / peak_a if peak_a else None
        util_b = float(tps) / peak_b if peak_b else None
        points.append(
            {
                "throughput_tps": round(float(tps), 1),
                "crdb_latency_ms": round(ya, 3),
                "pg_latency_ms": round(yb, 3),
                "overhead_x": round(yb / ya, 2),
                "crdb_utilisation": round(util_a, 3) if util_a else None,
                "pg_utilisation": round(util_b, 3) if util_b else None,
                "utilisation_gap": round(abs(util_a - util_b), 3)
                if util_a and util_b
                else None,
                "measured_in": (
                    "both"
                    if tps in set(a["mean_total_tps"]) and tps in set(b["mean_total_tps"])
                    else "CockroachDB" if tps in set(a["mean_total_tps"]) else "PostgreSQL"
                ),
            }
        )

    return {
        "comparable": bool(points),
        "operation": op,
        "quantile": quantile,
        "overlap_tps": [round(float(lo), 1), round(float(hi), 1)],
        "points": points,
        # Closest utilisations: the least confounded single number.
        "least_confounded": (
            min(
                (p for p in points if p["utilisation_gap"] is not None),
                key=lambda p: p["utilisation_gap"],
                default=None,
            )
        ),
        "caveat": (
            "values at a throughput not measured for an engine are linearly "
            "interpolated between its bracketing tiers; the true curve is convex "
            "near saturation, so interpolated latency is an underestimate there"
        ),
    }


def matched_utilisation(
    crdb: Run,
    pg: Run,
    op: str = "update",
    quantile: str = "p50_ms",
) -> dict[str, Any]:
    """Compare the engines at equal fractions of their own measured capacity.

    Complements :func:`matched_throughput`. With different capacities the two
    cannot coincide: matched throughput loads the smaller system harder, while
    matched utilisation compares two different throughputs. Both are reported,
    each labelled with what it holds fixed.
    """
    a = throughput_latency_curve(crdb, op)
    b = throughput_latency_curve(pg, op)
    peak_a = float(a["mean_total_tps"].max())
    peak_b = float(b["mean_total_tps"].max())
    if not peak_a or not peak_b:
        return {"comparable": False, "reason": "an engine reports no throughput", "points": []}

    # Only levels inside both engines' measured ranges; no extrapolation.
    lo = max(float(a["mean_total_tps"].min()) / peak_a,
             float(b["mean_total_tps"].min()) / peak_b)
    hi = min(1.0, 1.0)
    if lo > hi:
        return {
            "comparable": False,
            "reason": (
                f"no utilisation level is inside both engines' measured ranges "
                f"(CockroachDB from {lo:.2f}, PostgreSQL from "
                f"{float(b['mean_total_tps'].min()) / peak_b:.2f})"
            ),
            "points": [],
        }

    # Levels are labelled by their rounded value but computed from the exact
    # ratio, so a measured tier is never shifted to an interpolated point.
    levels: dict[float, float] = {}
    for peak, frame in ((peak_a, a), (peak_b, b)):
        for t in frame["mean_total_tps"]:
            exact = float(t) / peak
            levels.setdefault(round(exact, 3), exact)
    points: list[dict[str, Any]] = []
    for key in sorted(levels):
        u = levels[key]
        if not (lo <= u <= hi):
            continue
        ta, tb = u * peak_a, u * peak_b
        ya = _interpolate(a, ta, quantile)
        yb = _interpolate(b, tb, quantile)
        if ya is None or yb is None or ya <= 0:
            continue
        points.append(
            {
                "utilisation": round(u, 3),
                "crdb_tps": round(ta, 1),
                "pg_tps": round(tb, 1),
                "crdb_latency_ms": round(ya, 3),
                "pg_latency_ms": round(yb, 3),
                "overhead_x": round(yb / ya, 2),
            }
        )

    return {
        "comparable": bool(points),
        "operation": op,
        "quantile": quantile,
        "holds_fixed": "utilisation (throughput differs between the engines)",
        "crdb_peak_tps": round(peak_a, 1),
        "pg_peak_tps": round(peak_b, 1),
        "utilisation_range": [round(lo, 3), round(hi, 3)],
        "points": points,
        "caveat": (
            "the two engines are compared at different throughputs by construction, "
            "so a ratio here is not the cost of replication at any single offered "
            "load; capacity is each engine's own measured peak, which is a lower "
            "bound if that engine had not saturated"
        ),
    }


def lightest_load_write_latency(
    crdb: Run, pg: Run, op: str = "update"
) -> dict[str, Any]:
    """Each engine's write median at its lowest measured concurrency.

    Comparable despite differing loads because it approaches each engine's
    quorum round-trip floor. The offered load is reported alongside.
    """
    out: dict[str, Any] = {"operation": op}
    for run, key in ((crdb, "crdb"), (pg, "pg")):
        lat = latency_by_op(run)
        lat = lat[lat["op"] == op]
        if lat.empty:
            out[key] = None
            continue
        lightest = int(lat["concurrency"].min())
        row = lat[lat["concurrency"] == lightest].iloc[0]
        tiers = per_tier(run).set_index("concurrency")
        tps = float(tiers.loc[lightest, "mean_total_tps"])
        # One worker means one operation in flight: no queueing by construction.
        # Little's law is recorded as corroboration only, not as a gate.
        weighted = float(tiers.loc[lightest, "mean_weighted_p50_ms"])
        implied = lightest / tps * 1000.0 if tps else None
        out[key] = {
            "run_id": run.run_id,
            "concurrency": lightest,
            # Unrounded, so ratios are computed from measurements, not display values.
            "_p50_exact": float(row["p50_ms"]),
            "p50_ms": round(float(row["p50_ms"]), 3),
            "p99_ms": round(float(row["p99_ms"]), 3),
            "offered_load_tps": round(tps, 1),
            "implied_mean_latency_ms": round(implied, 3) if implied else None,
            "weighted_p50_ms": round(weighted, 3),
            "unqueued": lightest == 1,
            "littles_law_agreement": (
                round(abs(implied - weighted) / weighted, 4)
                if implied and weighted else None
            ),
        }
    if out.get("crdb") and out.get("pg"):
        out["ratio_x"] = round(
            out["pg"]["_p50_exact"] / out["crdb"]["_p50_exact"], 2
        )
        both_unqueued = out["crdb"]["unqueued"] and out["pg"]["unqueued"]
        out["both_unqueued"] = both_unqueued
        if both_unqueued:
            # Both at C=1: two serial write paths, each bound by its own quorum trip.
            worst = max(
                out["crdb"]["littles_law_agreement"] or 0.0,
                out["pg"]["littles_law_agreement"] or 0.0,
            )
            out["caveat"] = (
                "both medians are single-worker measurements, so exactly one "
                "operation was outstanding in each and neither median contains "
                f"queueing (Little's law corroborates to {worst:.1%}). "
                "The throughputs differ "
                f"({out['crdb']['offered_load_tps']:.0f} vs "
                f"{out['pg']['offered_load_tps']:.0f} ops/s) as a "
                "consequence of the latency difference, not as a confound in it. "
                "This is the least confounded cross-engine cost figure the "
                "experiment produces"
            )
        else:
            out["caveat"] = (
                "the two medians were measured at different offered loads "
                f"({out['crdb']['offered_load_tps']:.0f} vs "
                f"{out['pg']['offered_load_tps']:.0f} ops/s) and at least "
                "one side is queueing, so the ratio is not purely the cost of one "
                "engine's replication mechanism against the other's; it is "
                "quotable because each side's component is dominated by its own "
                "quorum round trip, not because the loads match"
            )
    return out


def same_concurrency_delta(crdb: Run, pg: Run) -> dict[str, Any]:
    """The invalid comparison, computed and labelled as invalid.

    Kept so the intuitive-but-wrong comparison is shown with its reason.
    """
    a, b = per_tier(crdb).set_index("concurrency"), per_tier(pg).set_index("concurrency")
    shared = sorted(set(a.index) & set(b.index))
    la, lb = latency_by_op(crdb), latency_by_op(pg)

    rows: list[dict[str, Any]] = []
    for concurrency in shared:
        # Divide first, then round.
        crdb_tps = float(a.loc[concurrency, "mean_total_tps"])
        pg_tps = float(b.loc[concurrency, "mean_total_tps"])
        row: dict[str, Any] = {
            "concurrency": int(concurrency),
            "crdb_tps": round(crdb_tps, 1),
            "pg_tps": round(pg_tps, 1),
        }
        row["throughput_ratio_x"] = round(crdb_tps / pg_tps, 2)
        for op in sorted(set(la["op"]) & set(lb["op"])):
            pa = la[(la["concurrency"] == concurrency) & (la["op"] == op)]["p50_ms"]
            pb = lb[(lb["concurrency"] == concurrency) & (lb["op"] == op)]["p50_ms"]
            if not pa.empty and not pb.empty and float(pa.iloc[0]) > 0:
                row[f"{op}_p50_ratio_x"] = round(float(pb.iloc[0]) / float(pa.iloc[0]), 2)
        rows.append(row)

    return {
        "comparable": False,
        "reason": (
            "concurrency fixes the worker count, not the offered load, so the two "
            "engines sit at different points on their own throughput-latency "
            "curves at a shared concurrency tier. Whichever engine is further "
            "from its own saturation at that tier reports a lower latency there "
            "for reasons that have nothing to do with replication cost -- an "
            "artefact that can flatter either engine depending on which one is "
            "closer to its own capacity limit"
        ),
        "use": "error case study only; never as a results table",
        "rows": rows,
    }


def compare(
    crdb: Run,
    pg: Run,
    op: str = "update",
    accept_hardware_difference: bool = False,
) -> dict[str, Any]:
    """Full replication-cost comparison, gated on the two runs being comparable.

    ``accept_hardware_difference`` downgrades a CPU or memory mismatch from a
    refusal to a recorded warning.
    """
    comparability: ValidationReport = validate_comparison(
        crdb.manifest, pg.manifest, crdb.phase, pg.phase,
        accept_hardware_difference=accept_hardware_difference,
    )
    if not comparability.ok:
        raise NotComparable(
            "; ".join(f.message for f in comparability.findings if f.severity == "error")
        )

    return {
        "crdb_run_id": crdb.run_id,
        "pg_run_id": pg.run_id,
        "operation": op,
        "comparability": comparability.to_dict(),
        "server_config": {
            "crdb": crdb.server_command,
            "pg": pg.server_command,
        },
        "saturation": {
            "crdb": _saturation(per_tier(crdb)),
            "pg": _saturation(per_tier(pg)),
        },
        "curves": curves(crdb, pg, op).to_dict(orient="records"),
        "matched_throughput": matched_throughput(crdb, pg, op),
        "matched_utilisation": matched_utilisation(crdb, pg, op),
        "lightest_load_write_latency": lightest_load_write_latency(crdb, pg),
        "same_concurrency_delta": same_concurrency_delta(crdb, pg),
    }

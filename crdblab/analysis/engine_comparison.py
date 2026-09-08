"""The comparison of CockroachDB vs PostgreSQL/Patroni on identical cluster topologies.

Both engines are measured on the *same* five-node, three-provider topology
(``crdblab/topology.py``): CockroachDB with its own Raft-based replication,
PostgreSQL under Patroni configured for synchronous replication with a quorum
of standbys (``synchronous_standby_names: 'ANY 2 (*)'``, see
``terraform/scripts/bootstrap-patroni.tftpl``). Neither side is unreplicated,
so this module's job is isolating the two engines' different replication
mechanisms, not isolating replication cost from its absence.

The comparison at matched *concurrency* -- reading two engines' numbers off
the same ``--concurrency`` tier and subtracting them -- is invalid, and the
reason is worth stating precisely because the invalid form is the intuitive
one.

**Concurrency is not load.** The profile's ``--concurrency`` fixes the number of
client workers, not the work they accomplish. A closed workload of N workers
offers whatever load the system under test can absorb, so two engines of
different capacity run at the same concurrency sit at *different points on their
respective throughput-latency curves*. Subtracting them measures the difference
between two arbitrary operating points, not the difference between the engines.
An engine further from saturation at a given concurrency reports a lower
latency at that tier for reasons that have nothing to do with replication --
purely an artefact of where each curve happens to sit, which can flatter
either engine depending on which one saturates first.

The comparison is therefore reported in two forms, both load-explicit:

* the **throughput-latency curve** of each engine, which is the honest primitive
  and the only form that carries its own caveat; and
* scalars **at matched throughput**, computed only where the two engines'
  measured throughput ranges actually overlap. Where they do not, this module
  says so and declines to produce the number rather than extrapolating a curve
  beyond the data that defines it.

A third quantity is reported and is comparable without matching load: the
**lightest-load write median** of each engine. A committed write cannot outrun
the round trip to however many replicas its quorum requires, so each engine's
write latency at its lowest measured concurrency is floored by a physical
constant of the topology rather than by queueing. That floor is a property of
the topology and the quorum size, not of the operating point, which is what
makes it quotable -- the two engines' floors are not asserted to be equal, since
Raft and Patroni's synchronous-replication quorum need not agree even on
identical hardware.

Finally, every comparison is gated on
:func:`crdblab.analysis.validation.check_run_comparability`. Defect D9 -- a
fifteen-fold block-cache asymmetry between two runs that were otherwise
individually valid -- inflated an apparent replication-cost ratio by 43% in
this project's history. No check on a single run can detect that, so the check
belongs here, on the pair.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .loader import Run
from .steady_state import latency_by_op, per_tier, throughput_latency_curve
from .validation import ValidationReport, validate_comparison

#: A tier whose throughput exceeds the previous tier's by less than this is
#: treated as being on the plateau of the saturation curve. Below it, the
#: highest measured throughput is a *lower bound* on capacity rather than
#: capacity, and is reported as such.
SATURATION_TOLERANCE = 0.05


class NotComparable(RuntimeError):
    """Raised when the two runs may not legitimately be compared at all."""


def curves(crdb: Run, pg: Run, op: str) -> pd.DataFrame:
    """Both phases' throughput-latency curves for one operation type.

    This is the primitive the comparison rests on and the form any figure should
    take. Each row is one tier: an operating point, labelled with the concurrency
    that produced it so that the reader can see the two phases reach a given
    throughput at different worker counts.
    """
    frames = []
    for run, label in ((crdb, "CockroachDB"), (pg, "PostgreSQL")):
        frame = throughput_latency_curve(run, op)
        frame.insert(0, "phase", label)
        frame.insert(1, "run_id", run.run_id)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _saturation(tiers: pd.DataFrame) -> dict[str, Any]:
    """Whether the phase's peak measured throughput is its capacity.

    A curve still rising at the highest concurrency measured has not reached
    saturation, so its peak is a lower bound on capacity. Saying "capacity" of a
    number that is still climbing is the same class of error as the original
    C=200 "collapse": a statement about the system inferred from the edge of the
    measurement rather than from the system.
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

    Returns ``None`` outside the measured range: the curve is defined by the
    tiers that were run, and continuing it past them would be an assertion about
    load levels this experiment never applied. Linear interpolation *between*
    measured tiers is itself an approximation -- the true curve is convex as
    saturation approaches -- and is reported as such by the caller.

    **Only the rising branch is interpolated.** Past saturation a load curve bends
    backwards: adding workers costs throughput and adds latency, so one throughput
    corresponds to two different latencies and "the latency at 1,700 ops/s" stops
    being well defined. Measured 2026-09-02, the pg reached 1,728 ops/s at
    C=50 with an update median of 108 ms and 1,732 ops/s at C=200 with 230 ms --
    the same throughput at twice the latency. Interpolating across that fold would
    silently average two operating points that differ by a factor of two, so the
    curve is truncated at its peak and only the branch where throughput still
    rises with load is used.
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
    """How to make the two engines' throughput ranges meet -- if it is possible.

    The obvious advice, "run the slower engine at higher concurrency", is only
    sound while that engine's curve is still rising. Once it has saturated, more
    workers do not buy more throughput and may cost some. If the saturated
    engine's peak lies below the other engine's slowest measured tier, no
    amount of added concurrency can close the gap -- the only way to an overlap
    is to measure the faster engine at *lower* concurrency instead.

    Stating that distinction matters because the wrong remedy costs half an hour
    of sweep and produces the same refusal.
    """
    a, b = per_tier(crdb), per_tier(pg)
    slower, faster = (b, a) if b["mean_total_tps"].max() < a["mean_total_tps"].max() else (a, b)
    slower_name = "the pg" if slower is b else "the crdb"
    faster_name = "the crdb" if slower is b else "the pg"
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
    """Latency of both phases at throughputs both actually reached.

    The comparison is evaluated at every measured tier throughput that falls
    inside both phases' measured ranges, so that at least one side of each
    comparison is an observation rather than an interpolation.
    """
    a = throughput_latency_curve(crdb, op)
    b = throughput_latency_curve(pg, op)
    lo = max(a["mean_total_tps"].min(), b["mean_total_tps"].min())
    hi = min(a["mean_total_tps"].max(), b["mean_total_tps"].max())

    if lo > hi:
        return {
            "comparable": False,
            "reason": (
                f"the two phases' measured throughput ranges do not overlap: "
                f"CockroachDB spans {a['mean_total_tps'].min():.0f}-"
                f"{a['mean_total_tps'].max():.0f} ops/s and PostgreSQL "
                f"{b['mean_total_tps'].min():.0f}-{b['mean_total_tps'].max():.0f} "
                "ops/s. There is no load level at which both were measured, so a "
                "matched-throughput comparison would have to extrapolate one curve "
                "beyond the data defining it"
            ),
            "phase_ii_range_tps": [
                round(float(a["mean_total_tps"].min()), 1),
                round(float(a["mean_total_tps"].max()), 1),
            ],
            "phase_iii_range_tps": [
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
        # Matching throughput does NOT match utilisation, and conflating the two
        # is the residual trap in this comparison. Two engines delivering the
        # same work rate can sit at very different distances from their own
        # capacity -- one near its measured peak, the other with headroom to
        # spare -- so part of the latency ratio at a matched-throughput point can
        # be one engine's own queueing rather than a difference between the
        # engines. Reporting each side's utilisation lets a reader see which
        # points are like-for-like; the least confounded comparison is the one
        # where the two are closest, not the one at the highest throughput.
        util_a = float(tps) / peak_a if peak_a else None
        util_b = float(tps) / peak_b if peak_b else None
        points.append(
            {
                "throughput_tps": round(float(tps), 1),
                "phase_ii_latency_ms": round(ya, 3),
                "phase_iii_latency_ms": round(yb, 3),
                "overhead_x": round(yb / ya, 2),
                "phase_ii_utilisation": round(util_a, 3) if util_a else None,
                "phase_iii_utilisation": round(util_b, 3) if util_b else None,
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
        # The point whose two utilisations are closest, i.e. where both systems
        # are the same distance from their own capacity. This is the defensible
        # single number if one is needed; the others are still correct, but
        # increasingly mix replication cost with the pg's own saturation.
        "least_confounded": (
            min(
                (p for p in points if p["utilisation_gap"] is not None),
                key=lambda p: p["utilisation_gap"],
                default=None,
            )
        ),
        "caveat": (
            "values at a throughput not measured in a phase are linearly "
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
    """Compare the phases at equal fractions of their own measured capacity.

    Matched throughput and matched utilisation are *mutually exclusive* whenever
    the two systems' capacities differ, which is why both exist here rather than
    one superseding the other. At a common throughput ``T`` the utilisation gap is
    ``T * (1/peak_iii - 1/peak_ii)``: it grows linearly in ``T`` and reaches zero
    only at zero load. Measured 2026-09-03 with peaks of 2,565 and 1,792 ops/s,
    the narrowest gap available at matched throughput is 0.18, at the bottom of
    the overlap. Adding tiers cannot reduce it -- the gap is set by the ratio of
    the capacities, not by the sampling.

    So the two comparisons hold different things constant and answer different
    questions, and neither is the correction of the other:

    * **Matched throughput** asks what the same delivered work rate costs. It is
      the operationally meaningful comparison -- a service must serve the load it
      is given -- but it necessarily loads the smaller system harder relative to
      its capacity, so the ratio includes the pg's own queueing.
    * **Matched utilisation** asks what replication costs when both systems are
      the same distance from saturation, so their queueing components are
      comparable and the residual is closer to the replication path alone. It
      compares two *different* throughputs, which is why it cannot be quoted as
      "the cost at N ops/s".

    Reporting only the first overstates replication cost near the pg's peak;
    reporting only the second invites the ratio to be read as a cost at a load
    that was never offered. Both are emitted, each labelled with what it holds
    fixed.
    """
    a = throughput_latency_curve(crdb, op)
    b = throughput_latency_curve(pg, op)
    peak_a = float(a["mean_total_tps"].max())
    peak_b = float(b["mean_total_tps"].max())
    if not peak_a or not peak_b:
        return {"comparable": False, "reason": "a phase reports no throughput", "points": []}

    # A utilisation level is usable only where *both* phases have data, i.e.
    # where each phase's implied throughput lies inside its own measured range.
    # No extrapolation: a curve is not evaluated beyond the tiers defining it.
    lo = max(float(a["mean_total_tps"].min()) / peak_a,
             float(b["mean_total_tps"].min()) / peak_b)
    hi = min(1.0, 1.0)
    if lo > hi:
        return {
            "comparable": False,
            "reason": (
                f"no utilisation level is inside both phases' measured ranges "
                f"(CockroachDB from {lo:.2f}, PostgreSQL from "
                f"{float(b['mean_total_tps'].min()) / peak_b:.2f})"
            ),
            "points": [],
        }

    # A level is *identified* by its displayed 3 dp value, so the set of points
    # is the set of measured tiers on either side; but the arithmetic below uses
    # the exact ratio. Rounding a level and multiplying it back by the peak
    # displaces the throughput it names, which silently converts a *measured*
    # point into an interpolated one: 0.843 x 3563.335 = 3003.89 ops/s, where
    # the C=2 tier it came from measured 3004.532. The displacement here is
    # 0.6 ops/s; the mechanism is unbounded, and it also drops the lowest level
    # altogether whenever rounding pushes it below ``lo``.
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
                "phase_ii_tps": round(ta, 1),
                "phase_iii_tps": round(tb, 1),
                "phase_ii_latency_ms": round(ya, 3),
                "phase_iii_latency_ms": round(yb, 3),
                "overhead_x": round(yb / ya, 2),
            }
        )

    return {
        "comparable": bool(points),
        "operation": op,
        "quantile": quantile,
        "holds_fixed": "utilisation (throughput differs between the phases)",
        "phase_ii_peak_tps": round(peak_a, 1),
        "phase_iii_peak_tps": round(peak_b, 1),
        "utilisation_range": [round(lo, 3), round(hi, 3)],
        "points": points,
        "caveat": (
            "the two phases are compared at different throughputs by construction, "
            "so a ratio here is not the cost of replication at any single offered "
            "load; capacity is each phase's own measured peak, which is a lower "
            "bound if that phase had not saturated"
        ),
    }


def lightest_load_write_latency(
    crdb: Run, pg: Run, op: str = "update"
) -> dict[str, Any]:
    """Each engine's write median at its lowest measured concurrency.

    Comparable across engines despite the differing load, because the quantity
    it exposes is a floor rather than an operating point: a committed write
    cannot be acknowledged faster than the round trip to however many replicas
    each engine's quorum requires. Both engines are replicated on this topology
    and both pay some such floor -- CockroachDB's Raft quorum and Patroni's
    synchronous-replication quorum are not asserted to agree, even on identical
    hardware, which is exactly why this is reported as two measured numbers
    rather than assumed equal. The offered load of each measurement is reported
    alongside so that the residual queueing component remains visible rather
    than implied away.
    """
    out: dict[str, Any] = {"operation": op}
    for run, key in ((crdb, "phase_ii"), (pg, "phase_iii")):
        lat = latency_by_op(run)
        lat = lat[lat["op"] == op]
        if lat.empty:
            out[key] = None
            continue
        lightest = int(lat["concurrency"].min())
        row = lat[lat["concurrency"] == lightest].iloc[0]
        tiers = per_tier(run).set_index("concurrency")
        tps = float(tiers.loc[lightest, "mean_total_tps"])
        # Queueing is settled structurally, not statistically. A closed-loop
        # generator with one worker has exactly one operation outstanding at any
        # instant, so there is nothing for an operation to wait behind; that is a
        # property of the harness, not an inference from the numbers. Little's
        # law is recorded beside it as corroboration only.
        #
        # It is deliberately not the gate. An earlier draft required N/X to agree
        # with the frequency-weighted median to within 5% and PostgreSQL's C=1
        # tier missed at 5.1%, which would have denied a structurally impossible
        # queue on the strength of a blend artefact: the weighted median averages
        # a 0.74 ms read against a 72.7 ms update, and the mean of per-interval
        # blends is not the blend of per-tier means when the op mix varies
        # between intervals. A few percent there is arithmetic, not waiting.
        weighted = float(tiers.loc[lightest, "mean_weighted_p50_ms"])
        implied = lightest / tps * 1000.0 if tps else None
        out[key] = {
            "run_id": run.run_id,
            "concurrency": lightest,
            # Retained unrounded so that ratios below are computed from the
            # measurement rather than from its display form. Rounding an input,
            # then dividing, then rounding again puts the error of the first
            # rounding into the result: the unqueued ratio read 50.37x that way
            # against 50.38x computed from the medians themselves, and the
            # dissertation then had to reconcile two figures for one quantity.
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
    if out.get("phase_ii") and out.get("phase_iii"):
        out["ratio_x"] = round(
            out["phase_iii"]["_p50_exact"] / out["phase_ii"]["_p50_exact"], 2
        )
        both_unqueued = out["phase_ii"]["unqueued"] and out["phase_iii"]["unqueued"]
        out["both_unqueued"] = both_unqueued
        if both_unqueued:
            # The strongest form this comparison can take: sweeping both engines
            # down to a single worker (C=1) means exactly one operation is in
            # flight for each, so neither median contains any waiting time. The
            # ratio is then between two serial write paths, each bound by its own
            # engine's quorum round trip across the same topology -- not by
            # queueing. The differing throughputs are a *consequence* of the
            # latency difference rather than a confound in it, which is precisely
            # what cannot be said of any comparison where both sides are queueing.
            worst = max(
                out["phase_ii"]["littles_law_agreement"] or 0.0,
                out["phase_iii"]["littles_law_agreement"] or 0.0,
            )
            out["caveat"] = (
                "both medians are single-worker measurements, so exactly one "
                "operation was outstanding in each and neither median contains "
                f"queueing (Little's law corroborates to {worst:.1%}). "
                "The throughputs differ "
                f"({out['phase_ii']['offered_load_tps']:.0f} vs "
                f"{out['phase_iii']['offered_load_tps']:.0f} ops/s) as a "
                "consequence of the latency difference, not as a confound in it. "
                "This is the least confounded cross-engine cost figure the "
                "experiment produces"
            )
        else:
            out["caveat"] = (
                "the two medians were measured at different offered loads "
                f"({out['phase_ii']['offered_load_tps']:.0f} vs "
                f"{out['phase_iii']['offered_load_tps']:.0f} ops/s) and at least "
                "one side is queueing, so the ratio is not purely the cost of one "
                "engine's replication mechanism against the other's; it is "
                "quotable because each side's component is dominated by its own "
                "quorum round trip, not because the loads match"
            )
    return out


def same_concurrency_delta(crdb: Run, pg: Run) -> dict[str, Any]:
    """The invalid comparison, computed and labelled as invalid.

    Retained deliberately: stating why the intuitive same-concurrency
    comparison is wrong, with the numbers alongside, is more useful than
    omitting it and leaving readers to reach for it anyway. It must never
    appear in a results table without this label.
    """
    a, b = per_tier(crdb).set_index("concurrency"), per_tier(pg).set_index("concurrency")
    shared = sorted(set(a.index) & set(b.index))
    la, lb = latency_by_op(crdb), latency_by_op(pg)

    rows: list[dict[str, Any]] = []
    for concurrency in shared:
        # Divide the throughputs, then round the quotient -- never the reverse.
        # The 1 dp forms below exist to be displayed; dividing them instead put
        # the display rounding into the ratio and reported 25.26x at C=1 for a
        # quantity whose value is 25.25x. This is the same defect the unqueued
        # ``ratio_x`` was fixed for, in the same module.
        phase_ii_tps = float(a.loc[concurrency, "mean_total_tps"])
        phase_iii_tps = float(b.loc[concurrency, "mean_total_tps"])
        row: dict[str, Any] = {
            "concurrency": int(concurrency),
            "phase_ii_tps": round(phase_ii_tps, 1),
            "phase_iii_tps": round(phase_iii_tps, 1),
        }
        row["throughput_ratio_x"] = round(phase_ii_tps / phase_iii_tps, 2)
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
    refusal to a recorded warning. It exists because this study's two phases run
    on different CPU models permanently, which is a stated limitation rather than
    a fixable defect; it is off by default so that the decision has to be made
    rather than inherited.
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
            "phase_ii": crdb.server_command,
            "phase_iii": pg.server_command,
        },
        "saturation": {
            "phase_ii": _saturation(per_tier(crdb)),
            "phase_iii": _saturation(per_tier(pg)),
        },
        "curves": curves(crdb, pg, op).to_dict(orient="records"),
        "matched_throughput": matched_throughput(crdb, pg, op),
        "matched_utilisation": matched_utilisation(crdb, pg, op),
        "lightest_load_write_latency": lightest_load_write_latency(crdb, pg),
        "same_concurrency_delta": same_concurrency_delta(crdb, pg),
    }

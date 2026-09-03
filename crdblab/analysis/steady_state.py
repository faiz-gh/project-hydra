"""Phase II and Phase III steady-state aggregation.

This module replaces ``analyze_single_node_baseline.py``, whose central
operation was a mean over the rows of a long table. That is the wrong operation
on this data and it is the arithmetic behind defect D1: the generator emits one
row per operation type per interval, so a mean over rows averages the read and
write *rates* instead of summing them, and simultaneously averages two distinct
latency distributions' quantiles into a number that is a quantile of nothing.

The aggregation is therefore stated explicitly at every level, and the two rules
are separated because they are genuinely different:

* **Across operation types, throughput sums and latency does not pool.** Applied
  in :meth:`crdblab.analysis.loader.Run.ticks`, which is the only place the long
  table is folded.
* **Across time within a tier, throughput and each per-operation quantile are
  averaged.** A mean of per-interval medians is a legitimate summary statistic
  and is what the tables below report; it is not itself a median of the run, and
  the column names say so.
* **Across repetitions, an interval estimate is reported rather than a point.**
  The original design measured each tier once. Three repetitions in randomised
  order were adopted precisely so that a difference between tiers can be
  distinguished from drift across the sweep, and reporting only their mean would
  discard the reason for running them.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .loader import QUANTILES, Run

#: Two-sided 95% critical values of Student's t by degrees of freedom.
#: Tabulated rather than imported so that the measurement path takes no
#: dependency on SciPy: every runtime dependency is a version that has to be
#: pinned and reported for the sweep to be reproducible. Beyond the table the
#: normal approximation is within 2% and is used.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179}


def _t95(df: int) -> float:
    return _T95.get(df, 1.960)


def confidence_interval(values: pd.Series, level: float = 0.95) -> dict[str, Any]:
    """Mean of ``values`` with a Student's t interval, or ``None`` if n < 2.

    A single repetition yields no interval. It is reported as ``None`` rather
    than as zero, because a half-width of zero asserts perfect agreement between
    repetitions that were never run -- the same conflation of "not measured" with
    "measured as zero" that left ``ram_pct`` at a constant 0.0 for an entire
    dissertation's worth of runs (D5).
    """
    clean = pd.Series(values).dropna()
    n = int(clean.size)
    if n == 0:
        return {"mean": None, "sd": None, "n": 0, "ci95_half_width": None}
    mean = float(clean.mean())
    if n < 2:
        return {"mean": round(mean, 3), "sd": None, "n": n, "ci95_half_width": None}
    sd = float(clean.std(ddof=1))
    half = _t95(n - 1) * sd / np.sqrt(n)
    return {
        "mean": round(mean, 3),
        "sd": round(sd, 3),
        "n": n,
        "ci95_half_width": round(float(half), 3),
    }


def steady_state_window(run: Run) -> dict[str, Any]:
    """What part of each tier the recorded rows already represent.

    Phase II and III discard the ramp-up window at write time, so applying a
    warmup filter again here would silently trim twice. This reports the profile's
    declared warmup against the earliest interval actually present, so the caller
    can see which is the case instead of assuming.
    """
    declared = float(run.workload.get("warmup_s", 0.0) or 0.0)
    first = float(run.metrics["elapsed_s"].min())
    return {
        "declared_warmup_s": declared,
        "first_interval_s": first,
        "already_trimmed": bool(declared > 0 and first > declared),
    }


def per_repetition(run: Run, warmup_s: float = 0.0) -> pd.DataFrame:
    """One row per (concurrency, repetition): the unit a repetition produces."""
    ticks = run.ticks(warmup_s=warmup_s)
    grouped = ticks.groupby(["concurrency", "repetition"], as_index=False)
    out = grouped.agg(
        ticks=("elapsed_s", "count"),
        mean_total_tps=("total_tps", "mean"),
        sd_total_tps=("total_tps", "std"),
        min_total_tps=("total_tps", "min"),
        max_total_tps=("total_tps", "max"),
        mean_weighted_p50_ms=("weighted_p50_ms", "mean"),
        errors_cum=("errors_cum", "max"),
    )

    # Little's law, recomputed here as a reported quantity rather than only as a
    # validation predicate: N / X is the mean residence time implied by the
    # offered concurrency and the achieved throughput, and printing it beside the
    # measured latency lets a reader check the run's internal consistency without
    # taking the harness's word for it.
    out["implied_mean_latency_ms"] = out["concurrency"] / out["mean_total_tps"] * 1000.0
    return out


def per_tier(run: Run, warmup_s: float = 0.0) -> pd.DataFrame:
    """One row per concurrency tier, aggregating across repetitions.

    The interval is computed over repetition means, not over the pooled
    per-second samples. Successive samples within one run are not independent --
    they share a process, a cache state and a thermal state -- so pooling them
    would produce an interval far narrower than the experiment supports.
    """
    reps = per_repetition(run, warmup_s=warmup_s)
    rows: list[dict[str, Any]] = []
    for concurrency, group in reps.groupby("concurrency"):
        stat = confidence_interval(group["mean_total_tps"])
        rows.append(
            {
                "concurrency": int(concurrency),
                "repetitions": stat["n"],
                "mean_total_tps": stat["mean"],
                "sd_total_tps": stat["sd"],
                "ci95_half_width_tps": stat["ci95_half_width"],
                "mean_weighted_p50_ms": round(
                    float(group["mean_weighted_p50_ms"].mean()), 3
                ),
                "implied_mean_latency_ms": round(
                    float(group["implied_mean_latency_ms"].mean()), 3
                ),
                "errors_cum": int(group["errors_cum"].max()),
            }
        )
    return pd.DataFrame(rows).sort_values("concurrency", ignore_index=True)


def latency_by_op(run: Run, warmup_s: float = 0.0) -> pd.DataFrame:
    """Per-operation latency by tier. Operation type is never collapsed.

    Each cell is the mean over intervals of that interval's quantile. It is not a
    quantile of the run's pooled latency distribution, which the generator does
    not expose and which cannot be reconstructed from per-interval quantiles.
    """
    per_op = run.latency_by_op(warmup_s=warmup_s)
    cols = [q for q in QUANTILES if q in per_op.columns]
    grouped = per_op.groupby(["concurrency", "op"], as_index=False)
    out = grouped.agg(
        repetitions=("repetition", "nunique"),
        **{q: (q, "mean") for q in cols},
    )
    return out.sort_values(["concurrency", "op"], ignore_index=True)


def throughput_latency_curve(run: Run, op: str, warmup_s: float = 0.0) -> pd.DataFrame:
    """Offered-load curve for one operation type: throughput against latency.

    This is the form in which a phase's cost must be reported, and the input to
    any comparison between phases. A tier is a point on it, not a measurement of
    the system at some canonical load: the concurrency setting fixes the number
    of workers, not the load they achieve, so two phases at the same concurrency
    sit at different points on their respective curves and are not comparable.
    See :mod:`crdblab.analysis.raft_overhead`.
    """
    tiers = per_tier(run, warmup_s=warmup_s).set_index("concurrency")
    lat = latency_by_op(run, warmup_s=warmup_s)
    lat = lat[lat["op"] == op].set_index("concurrency")
    if lat.empty:
        raise KeyError(
            f"run {run.run_id} reports no operation type {op!r}; "
            f"observed: {sorted(run.metrics['op'].unique())}"
        )
    joined = tiers.join(lat, how="inner", rsuffix="_lat")
    out = joined.reset_index()[
        ["concurrency", "mean_total_tps", "ci95_half_width_tps"]
        + [q for q in QUANTILES if q in joined.columns]
    ]
    out.insert(1, "op", op)
    # Ordered by concurrency, the control variable, and never by throughput.
    # Throughput is the *response*, and past saturation it is not monotonic in
    # concurrency: measured 2026-09-02, the cluster peaked at 1,855 ops/s at
    # C=100 and fell to 1,732 at C=200. Ordering by throughput would interleave
    # the tiers, so a line drawn through them doubles back on itself and no
    # longer traces the path the experiment actually took. A saturating system's
    # curve genuinely bends backwards -- more load, less throughput, more latency
    # -- and that shape is a finding, not a plotting artefact to sort away.
    return out.sort_values("concurrency", ignore_index=True)


def summarise(run: Run, warmup_s: float = 0.0) -> dict[str, Any]:
    """Everything a results table for this phase needs, as plain data."""
    tiers = per_tier(run, warmup_s=warmup_s)
    lat = latency_by_op(run, warmup_s=warmup_s)
    ops = sorted(run.metrics["op"].unique())

    peak = tiers.loc[tiers["mean_total_tps"].idxmax()] if not tiers.empty else None
    return {
        "run_id": run.run_id,
        "phase": run.phase,
        "schema_version": run.schema_version,
        "server_command": run.server_command,
        "operation_types": ops,
        "window": steady_state_window(run),
        "peak_throughput": (
            {
                "concurrency": int(peak["concurrency"]),
                "mean_total_tps": float(peak["mean_total_tps"]),
            }
            if peak is not None
            else None
        ),
        "tiers": tiers.to_dict(orient="records"),
        "latency_by_op": lat.to_dict(orient="records"),
        "aggregation": {
            "throughput_across_op_types": "summed",
            "latency_across_op_types": "never pooled; reported per operation type",
            "across_time": "mean of per-interval values over steady-state intervals",
            "across_repetitions": "mean with a Student's t 95% interval; None when n < 2",
        },
    }

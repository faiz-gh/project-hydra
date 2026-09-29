"""Phase II steady-state aggregation.

* Across operation types, throughput sums and latency is never pooled
  (applied in :meth:`crdblab.analysis.loader.Run.ticks`).
* Across time within a tier, throughput and each per-op quantile are averaged.
* Across repetitions, a mean with a 95% interval is reported.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .loader import QUANTILES, Run

#: Two-sided 95% Student's t critical values by degrees of freedom (avoids a
#: SciPy dependency); beyond the table the normal value is used.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179}


def _t95(df: int) -> float:
    return _T95.get(df, 1.960)


def confidence_interval(values: pd.Series, level: float = 0.95) -> dict[str, Any]:
    """Mean of ``values`` with a Student's t interval, or ``None`` if n < 2.

    With one repetition the half-width is ``None``, never zero.
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

    Phase II already drops the warm-up at write time; this shows whether the
    rows are trimmed, so a caller does not trim twice.
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

    # Little's law: N / X is the mean latency implied by concurrency and throughput.
    out["implied_mean_latency_ms"] = out["concurrency"] / out["mean_total_tps"] * 1000.0
    return out


def per_tier(run: Run, warmup_s: float = 0.0) -> pd.DataFrame:
    """One row per concurrency tier, aggregating across repetitions.

    The interval is over repetition means: per-second samples within a run
    are not independent.
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

    Each cell is the mean of per-interval quantiles, not a quantile of the run.
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

    Concurrency fixes the number of workers, not the load, so phases are
    compared along this curve rather than at equal concurrency.
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
    # Ordered by concurrency (the control variable): past saturation throughput
    # falls, and the curve bending back is a finding.
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

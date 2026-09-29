"""Post-run validation: is a recorded run internally consistent?

Each check targets an observable symptom of a real parsing or measurement bug.
The cross-run checks assert that two runs being compared differ only in the
variable under study. ``validate_probe`` checks an RTO probe log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

#: A sample above this is a cumulative total leaking into the per-interval stream.
DEFAULT_TPS_CEILING = 20_000.0


@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warning"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def add(self, check: str, severity: str, message: str, **detail: Any) -> None:
        self.findings.append(Finding(check, severity, message, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [
                {"check": f.check, "severity": f.severity, "message": f.message, **f.detail}
                for f in self.findings
            ],
        }


def _ticks(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the long table to one row per (concurrency, repetition, tick)."""
    grouped = df.groupby(["concurrency", "repetition", "elapsed_s"], as_index=False)
    return grouped.agg(total_tps=("tps", "sum"), errors_cum=("errors_cum", "max"))


def check_plausibility(df: pd.DataFrame, ceiling: float = DEFAULT_TPS_CEILING) -> list[Finding]:
    """Cumulative summary rows admitted as per-interval samples."""
    bad = df[df["tps"] > ceiling]
    if bad.empty:
        return []
    return [
        Finding(
            "plausibility",
            "error",
            f"{len(bad)} sample(s) exceed the throughput ceiling of {ceiling:.0f}; "
            "these are almost certainly cumulative totals, not per-interval rates",
            {"max_observed": float(bad["tps"].max()), "rows": bad.index.tolist()[:10]},
        )
    ]


def check_quantile_ordering(df: pd.DataFrame) -> list[Finding]:
    """Latency columns bound to the wrong header positions break p50 <= p95 <= p99 <= pMax."""
    cols = ["p50_ms", "p95_ms", "p99_ms", "pmax_ms"]
    present = [c for c in cols if c in df.columns]
    violations = pd.Series(False, index=df.index)
    for lo, hi in zip(present, present[1:]):
        violations |= df[lo] > df[hi] + 1e-9
    n = int(violations.sum())
    if n == 0:
        return []
    return [
        Finding(
            "quantile_ordering",
            "error",
            f"{n} sample(s) violate p50 <= p95 <= p99 <= pMax; latency columns are "
            "likely bound to the wrong header positions",
            {"rows": df.index[violations].tolist()[:10]},
        )
    ]


def check_littles_law(df: pd.DataFrame, tolerance: float = 0.9) -> list[Finding]:
    """Little's law: implied mean latency ``N / X`` must not fall below the median.

    For a closed workload of ``N`` workers, ``N / X`` is an upper bound on mean
    latency. It is compared with the throughput-weighted mean of per-op medians
    (not the slowest op, which would reject sound mixed workloads). It fires when
    throughput is over-counted or latency is bound to the wrong column.
    """
    findings: list[Finding] = []
    work = df[df["tps"] > 0].copy()
    if work.empty:
        return findings

    work["p50_weight"] = work["tps"] * work["p50_ms"]
    per_tick = work.groupby(
        ["concurrency", "repetition", "elapsed_s"], as_index=False
    ).agg(total_tps=("tps", "sum"), p50_weight=("p50_weight", "sum"))
    per_tick = per_tick[per_tick["total_tps"] > 0]
    if per_tick.empty:
        return findings

    per_tick["weighted_p50_ms"] = per_tick["p50_weight"] / per_tick["total_tps"]
    per_tick["implied_mean_ms"] = (
        per_tick["concurrency"] / per_tick["total_tps"] * 1000.0
    )

    per_tier = per_tick.groupby("concurrency").agg(
        implied_mean_ms=("implied_mean_ms", "mean"),
        weighted_p50_ms=("weighted_p50_ms", "mean"),
        total_tps=("total_tps", "mean"),
    )
    for concurrency, row in per_tier.iterrows():
        if row["implied_mean_ms"] < row["weighted_p50_ms"] * tolerance:
            ratio = row["weighted_p50_ms"] / row["implied_mean_ms"]
            findings.append(
                Finding(
                    "littles_law",
                    "error",
                    f"C={concurrency}: implied mean latency "
                    f"{row['implied_mean_ms']:.1f} ms is below the frequency-weighted "
                    f"median {row['weighted_p50_ms']:.1f} ms (ratio {ratio:.2f}x); "
                    "throughput is over-counted or latency is mis-bound",
                    {
                        "concurrency": int(concurrency),
                        "implied_mean_ms": round(float(row["implied_mean_ms"]), 2),
                        "weighted_p50_ms": round(float(row["weighted_p50_ms"]), 2),
                        "mean_total_tps": round(float(row["total_tps"]), 1),
                    },
                )
            )
    return findings


def check_sample_cadence(df: pd.DataFrame, expected_interval_s: float = 1.0) -> list[Finding]:
    """Detect doubled or dropped ticks (irregular gaps between intervals)."""
    findings: list[Finding] = []
    for (concurrency, rep), group in df.groupby(["concurrency", "repetition"]):
        ticks = sorted(group["elapsed_s"].unique())
        if len(ticks) < 2:
            continue
        deltas = pd.Series(ticks).diff().dropna()
        off = deltas[(deltas - expected_interval_s).abs() > expected_interval_s * 0.5]
        if not off.empty:
            findings.append(
                Finding(
                    "sample_cadence",
                    "warning",
                    f"C={concurrency} rep={rep}: {len(off)} irregular inter-sample "
                    f"interval(s); expected {expected_interval_s:.1f} s",
                    {"observed_median_s": float(deltas.median())},
                )
            )
    return findings


def check_op_coverage(df: pd.DataFrame) -> list[Finding]:
    """Every interval should report the same set of operation types."""
    findings: list[Finding] = []
    for (concurrency, rep), group in df.groupby(["concurrency", "repetition"]):
        counts = group.groupby("elapsed_s")["op"].nunique()
        if counts.nunique() > 1:
            findings.append(
                Finding(
                    "op_coverage",
                    "error",
                    f"C={concurrency} rep={rep}: operation types per interval are "
                    "inconsistent, so any parity-based inference of op identity is unsafe",
                    {"distinct_counts": sorted(map(int, counts.unique()))},
                )
            )
    return findings


def check_error_monotonicity(df: pd.DataFrame) -> list[Finding]:
    """``errors`` is cumulative; a decrease means blocks were interleaved."""
    findings: list[Finding] = []
    for keys, group in df.groupby(["concurrency", "repetition", "op"]):
        series = group.sort_values("elapsed_s")["errors_cum"]
        if (series.diff().dropna() < 0).any():
            findings.append(
                Finding(
                    "error_monotonicity",
                    "error",
                    f"{keys}: cumulative error count decreases within a tier",
                    {},
                )
            )
    return findings


# Cross-run checks: two individually valid runs can still be an invalid comparison
# (e.g. different cache sizes), so these assert they differ only in what is studied.

#: Server flags that must match between two same-engine runs.
_MATCHED_SERVER_FLAGS: tuple[str, ...] = ("--cache", "--max-sql-memory")

#: Tolerated relative difference in total memory (providers round differently).
MEMORY_TOLERANCE = 0.05

#: Tolerated relative difference between CockroachDB's implied cache (``--cache``
#: x RAM) and PostgreSQL's ``shared_buffers``; both target 25% of RAM.
CACHE_EQUIVALENCE_TOLERANCE = 0.05

#: Workload parameters that must match, or the runs did different work.
_MATCHED_WORKLOAD_KEYS: tuple[str, ...] = (
    "generator",
    "ycsb_workload",
    "read_freq",
    "update_freq",
    "request_distribution",
    "seed",
    "insert_count",
    "duration_s",
    "warmup_s",
)


def server_flags(command: str | None) -> dict[str, str]:
    """``--flag=value`` pairs from a recorded server command line."""
    if not command:
        return {}
    flags: dict[str, str] = {}
    for token in command.split():
        if not token.startswith("--"):
            continue
        name, _, value = token.partition("=")
        flags[name] = value
    return flags


def _server_command(manifest: dict[str, Any]) -> str | None:
    for note in manifest.get("notes", []) or []:
        if " server: " in note:
            return note.split(" server: ", 1)[1]
    return None


def host_hardware(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """The manifest's ``host:`` note as a dict, or ``None`` if unrecorded."""
    for note in manifest.get("notes", []) or []:
        if " host: " in note:
            fields = dict(
                part.split("=", 1)
                for part in note.split(" host: ", 1)[1].split(" ")
                if "=" in part
            )
            cpus, mem = fields.get("cpus"), fields.get("mem_total_kb")
            return {
                "cpus": int(cpus) if cpus and cpus.isdigit() else None,
                "mem_total_kb": int(mem) if mem and mem.isdigit() else None,
                "cpu_model": note.split("cpu_model=", 1)[1] if "cpu_model=" in note else None,
            }
    return None


def pg_cache_config(manifest: dict[str, Any]) -> dict[str, int | None] | None:
    """The ``pg memory:`` note as a dict, or ``None`` for a run without one.

    Absent for CockroachDB runs, and for PostgreSQL runs whose probe failed.
    """
    for note in manifest.get("notes", []) or []:
        if " pg memory: " in note:
            fields = dict(
                part.split("=", 1)
                for part in note.split(" pg memory: ", 1)[1].split(" ")
                if "=" in part
            )
            sb, ec = fields.get("shared_buffers_kb"), fields.get("effective_cache_size_kb")
            return {
                "shared_buffers_kb": int(sb) if sb and sb.isdigit() else None,
                "effective_cache_size_kb": int(ec) if ec and ec.isdigit() else None,
            }
    return None


def check_run_comparability(
    a: dict[str, Any],
    b: dict[str, Any],
    label_a: str = "A",
    label_b: str = "B",
    accept_hardware_difference: bool = False,
) -> list[Finding]:
    """Assert that two runs differ only in the variable under study.

    Compares manifests: workload parameters, server flags (or, across
    engines, cache budgets), host hardware and server version.
    """
    findings: list[Finding] = []

    wa = (a.get("profile", {}) or {}).get("workload", {}) or {}
    wb = (b.get("profile", {}) or {}).get("workload", {}) or {}
    differing = {
        key: (wa.get(key), wb.get(key))
        for key in _MATCHED_WORKLOAD_KEYS
        if wa.get(key) != wb.get(key)
    }
    if differing:
        findings.append(
            Finding(
                "run_comparability",
                "error",
                f"{label_a} and {label_b} ran different workloads, so their "
                f"difference is not the quantity under study: "
                + ", ".join(f"{k}={v0!r} vs {v1!r}" for k, (v0, v1) in differing.items()),
                {"differing_workload_parameters": {k: list(v) for k, v in differing.items()}},
            )
        )

    # Older runs recorded the version only as `cockroach_version`.
    va = a.get("server_version") or a.get("cockroach_version")
    vb = b.get("server_version") or b.get("cockroach_version")
    ea = a.get("engine") or "cockroachdb"
    eb = b.get("engine") or "cockroachdb"

    cmd_a, cmd_b = _server_command(a), _server_command(b)
    if cmd_a is None or cmd_b is None:
        findings.append(
            Finding(
                "run_comparability",
                "warning",
                f"server configuration is unrecorded for "
                f"{label_a if cmd_a is None else label_b}, so the two runs cannot be "
                "shown to have been configured alike",
                {},
            )
        )
    elif ea == eb:
        fa, fb = server_flags(cmd_a), server_flags(cmd_b)
        mismatched = {
            flag: (fa.get(flag), fb.get(flag))
            for flag in _MATCHED_SERVER_FLAGS
            if fa.get(flag) != fb.get(flag)
        }
        if mismatched:
            findings.append(
                Finding(
                    "run_comparability",
                    "error",
                    f"{label_a} and {label_b} were started with different "
                    + ", ".join(
                        f"{flag} ({v0 or 'unset, i.e. the 128 MiB default'} vs "
                        f"{v1 or 'unset, i.e. the 128 MiB default'})"
                        for flag, (v0, v1) in mismatched.items()
                    )
                    + "; the difference between them therefore confounds the "
                    "variable under study with cache residency",
                    {"mismatched_server_flags": {k: list(v) for k, v in mismatched.items()}},
                )
            )
    else:
        # Across engines the flags differ by construction; compare CockroachDB's
        # implied cache with PostgreSQL's shared_buffers instead.
        if {ea, eb} == {"cockroachdb", "postgresql"}:
            crdb_a = ea == "cockroachdb"
            crdb_manifest, crdb_label, crdb_cmd = (
                (a, label_a, cmd_a) if crdb_a else (b, label_b, cmd_b)
            )
            pg_manifest, pg_label = (b, label_b) if crdb_a else (a, label_a)

            crdb_hw = host_hardware(crdb_manifest)
            cache_fraction = server_flags(crdb_cmd).get("--cache")
            crdb_cache_kb = None
            if cache_fraction and crdb_hw and crdb_hw.get("mem_total_kb"):
                try:
                    crdb_cache_kb = float(cache_fraction) * crdb_hw["mem_total_kb"]
                except ValueError:
                    crdb_cache_kb = None
            pg_mem = pg_cache_config(pg_manifest)
            pg_cache_kb = pg_mem.get("shared_buffers_kb") if pg_mem else None

            if crdb_cache_kb is not None and pg_cache_kb is not None:
                rel_diff = abs(crdb_cache_kb - pg_cache_kb) / max(crdb_cache_kb, pg_cache_kb)
                if rel_diff > CACHE_EQUIVALENCE_TOLERANCE:
                    findings.append(
                        Finding(
                            "run_comparability",
                            "error",
                            f"{crdb_label}'s implied cache ({crdb_cache_kb:.0f} kB, "
                            f"--cache={cache_fraction} of {crdb_hw['mem_total_kb']} kB "
                            f"RAM) and {pg_label}'s shared_buffers ({pg_cache_kb} kB) "
                            f"differ by {rel_diff:.1%}, more than the "
                            f"{CACHE_EQUIVALENCE_TOLERANCE:.0%} tolerance; the "
                            "difference between them therefore confounds the "
                            "variable under study with cache residency",
                            {
                                "crdb_implied_cache_kb": round(crdb_cache_kb, 1),
                                "pg_shared_buffers_kb": pg_cache_kb,
                                "relative_difference": round(rel_diff, 4),
                            },
                        )
                    )
            else:
                findings.append(
                    Finding(
                        "run_comparability",
                        "warning",
                        f"PostgreSQL's cache budget was not recorded for {pg_label} "
                        f"(or CockroachDB's --cache/mem_total_kb could not be read "
                        f"for {crdb_label}), so cross-engine cache-budget "
                        "equivalence could not be verified",
                        {
                            "crdb_implied_cache_kb": crdb_cache_kb,
                            "pg_shared_buffers_kb": pg_cache_kb,
                        },
                    )
                )

    ha, hb = host_hardware(a), host_hardware(b)
    if ha is None or hb is None:
        findings.append(
            Finding(
                "run_comparability",
                "warning",
                f"host hardware is unrecorded for "
                f"{label_a if ha is None else label_b}, so the two runs cannot be "
                "shown to have run on comparable machines",
                {},
            )
        )
    else:
        # CPU count and model must match exactly; memory within MEMORY_TOLERANCE,
        # since cache flags are fractions of it.
        differing_hw = {
            key: (ha.get(key), hb.get(key))
            for key in ("cpus", "cpu_model")
            if ha.get(key) != hb.get(key)
        }
        ma, mb = ha.get("mem_total_kb"), hb.get("mem_total_kb")
        if ma and mb and abs(ma - mb) / max(ma, mb) > MEMORY_TOLERANCE:
            differing_hw["mem_total_kb"] = (ma, mb)
        elif (ma is None) != (mb is None):
            differing_hw["mem_total_kb"] = (ma, mb)
        if differing_hw:
            # Only an explicit caller override downgrades this to a warning.
            detail = ", ".join(
                f"{k}: {v0!r} vs {v1!r}" for k, (v0, v1) in differing_hw.items()
            )
            if accept_hardware_difference:
                findings.append(
                    Finding(
                        "run_comparability",
                        "warning",
                        f"{label_a} and {label_b} were measured on different "
                        f"hardware ({detail}); this was explicitly accepted by the "
                        "caller and the comparison proceeds. Latency ratios on a "
                        "path bounded by network round trips are the least "
                        "affected; absolute throughput and any CPU-bound "
                        "quantity are the most",
                        {
                            "differing_hardware": {k: list(v) for k, v in differing_hw.items()},
                            "accepted": True,
                        },
                    )
                )
            else:
                findings.append(
                    Finding(
                        "run_comparability",
                        "error",
                        f"{label_a} and {label_b} were measured on different "
                        f"hardware ({detail}); a throughput difference between "
                        "them is not attributable to the variable under study. "
                        "If this difference is a known limitation of the study "
                        "rather than a mistake, say so explicitly rather than "
                        "comparing anyway",
                        {"differing_hardware": {k: list(v) for k, v in differing_hw.items()}},
                    )
                )

    if ea != eb:
        # Different engines have different versions by definition; report only.
        findings.append(
            Finding(
                "run_comparability",
                "warning",
                f"{label_a} and {label_b} are different engines "
                f"({ea} {va} vs {eb} {vb}), which is the comparison being made; "
                "record both versions alongside any result drawn from it",
                {"engines": [ea, eb], "server_versions": [va, vb]},
            )
        )
    elif va != vb:
        findings.append(
            Finding(
                "run_comparability",
                "error",
                f"{label_a} and {label_b} ran against different server versions "
                f"({va} vs {vb})",
                {},
            )
        )
    return findings


def validate_comparison(
    a: dict[str, Any],
    b: dict[str, Any],
    label_a: str = "A",
    label_b: str = "B",
    accept_hardware_difference: bool = False,
) -> ValidationReport:
    """Report on whether two runs may legitimately be compared."""
    report = ValidationReport()
    report.findings.extend(
        check_run_comparability(a, b, label_a, label_b, accept_hardware_difference)
    )
    return report


#: Valid probe outcomes, kept literal so a writer change cannot silently pass.
PROBE_OUTCOMES = ("ok", "timeout", "conn_error", "refused")


def check_probe_ordering(df: pd.DataFrame) -> list[Finding]:
    """A write cannot complete before dispatch, nor be dispatched before the epoch."""
    findings: list[Finding] = []
    backwards = df[df["complete_offset_s"] < df["dispatch_offset_s"] - 1e-9]
    if not backwards.empty:
        findings.append(
            Finding(
                "probe_ordering",
                "error",
                f"{len(backwards)} probe attempt(s) completed before they were "
                "dispatched; the two offset columns are not on the same clock",
                {"rows": backwards.index.tolist()[:10]},
            )
        )
    negative = df[df["dispatch_offset_s"] < -1e-9]
    if not negative.empty:
        findings.append(
            Finding(
                "probe_ordering",
                "error",
                f"{len(negative)} probe attempt(s) were dispatched before the run's "
                "epoch; the probe was given an epoch later than its own start",
                {"rows": negative.index.tolist()[:10]},
            )
        )
    return findings


def check_probe_outcomes(df: pd.DataFrame) -> list[Finding]:
    """Every outcome must be known, and at least one write served.

    ``refused`` writes are a probe defect, not downtime, so they are warned about.
    """
    findings: list[Finding] = []
    seen = set(df["outcome"].dropna().unique())
    unknown = sorted(seen - set(PROBE_OUTCOMES))
    if unknown:
        findings.append(
            Finding(
                "probe_outcomes",
                "error",
                f"probe log contains unrecognised outcome(s) {unknown}; the "
                f"downtime split is defined only over {list(PROBE_OUTCOMES)}",
                {"unknown": unknown},
            )
        )
    refused = int((df["outcome"] == "refused").sum())
    if refused:
        findings.append(
            Finding(
                "probe_outcomes",
                "warning",
                f"{refused} probe write(s) were refused by a reachable database. "
                "That is a defect in the probe, not an outage, and those attempts "
                "must not be read as downtime",
                {"refused": refused},
            )
        )
    if not (df["outcome"] == "ok").any():
        findings.append(
            Finding(
                "probe_outcomes",
                "error",
                "no probe write was ever served, so there is no baseline against "
                "which an outage could be measured; the probe never reached the "
                "database",
                {},
            )
        )
    return findings


def check_probe_sequence(df: pd.DataFrame) -> list[Finding]:
    """No sequence number is used twice (a retry would double-count an observation).

    Gaps are expected: numbers queued at the end of a run are never attempted.
    """
    duplicated = int(df["seq_id"].duplicated().sum())
    if not duplicated:
        return []
    return [
        Finding(
            "probe_sequence",
            "error",
            f"{duplicated} probe sequence number(s) appear more than once; an "
            "attempt was retried under its own id, which double-counts an "
            "observation",
            {"duplicates": df.loc[df["seq_id"].duplicated(), "seq_id"].tolist()[:10]},
        )
    ]


def validate_probe(df: pd.DataFrame) -> ValidationReport:
    """Consistency checks for an ``rto_probe.csv`` (separate schema from metrics)."""
    report = ValidationReport()
    required = {"seq_id", "dispatch_offset_s", "complete_offset_s", "outcome"}
    missing = sorted(required - set(df.columns))
    if missing:
        report.add(
            "probe_schema",
            "error",
            f"probe log is missing required column(s) {missing}",
            missing=missing,
        )
        return report
    if df.empty:
        report.add(
            "probe_schema",
            "error",
            "probe log has no rows, so the probe recorded no observation of "
            "availability at all",
        )
        return report
    for finding in (
        *check_probe_ordering(df),
        *check_probe_outcomes(df),
        *check_probe_sequence(df),
    ):
        report.findings.append(finding)
    return report


def validate(df: pd.DataFrame, tps_ceiling: float = DEFAULT_TPS_CEILING) -> ValidationReport:
    """Run every single-run check on a metrics table."""
    report = ValidationReport()
    for findings in (
        check_plausibility(df, tps_ceiling),
        check_quantile_ordering(df),
        check_littles_law(df),
        check_sample_cadence(df),
        check_op_coverage(df),
        check_error_monotonicity(df),
    ):
        report.findings.extend(findings)
    return report

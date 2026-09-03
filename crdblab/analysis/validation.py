"""Post-run validation.

Each check below corresponds to an observable symptom of a defect that actually
occurred in this project. Running them automatically after every run converts a
class of silent corruption into a loud, immediate failure, which is the
methodological point: the legacy exports were not wrong because the bugs were
subtle, but because nothing ever asserted that the numbers were internally
consistent.

The checks are deliberately cheap and assumption-light. In particular, the
Little's law check requires no knowledge of the workload beyond the offered
concurrency, and would alone have flagged the operation-type defect in every
tier of both Phase II and Phase III.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

#: Any observation above this is treated as a cumulative total leaking into the
#: per-interval stream rather than a genuine sample. Set well above the
#: hardware's plausible ceiling so it flags artefacts, not fast runs.
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
    """Defect 3: cumulative summary rows admitted as per-interval samples."""
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
    """Defect 2: latency columns bound to the wrong header positions.

    A positional shift does not always break ordering, but when it does the
    violation is unambiguous, so this is a cheap first line of defence.
    """
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
    """Defect 2: latency recorded larger than the throughput can support.

    For a closed workload of ``N`` workers each holding at most one outstanding
    request, Little's law gives ``N = X * R``, so the implied mean residence time
    is ``N / X``. Workers that idle between operations make ``N / X`` an
    *over*-estimate of the true mean, so it is a legitimate upper bound.

    The lower bound it is compared against must be the **frequency-weighted**
    mean of the per-operation medians, ``sum(share_o * p50_o)``, not the largest
    of them. ``N / X`` is the mean residence time across every operation the
    workers performed, and in a mixed workload the cheap operations dominate that
    average: at 80% reads of 0.85 ms and 20% updates of 71.3 ms the blend is
    15.1 ms, while the slowest component alone is 71.3 ms.

    Comparing against the maximum was this check's original formulation and it is
    wrong. It survived because the data it was written against had only a 2.7x
    spread between operation types; once the leaseholder was placed locally the
    spread became 84x (reads served from the local replica, updates paying
    cross-region quorum) and the check failed every tier of an entirely sound
    run. A validation check that rejects correct data is not conservative, it is
    broken, and it would have blocked the whole of Stage 6.

    Detection power is retained: since mean latency is at least the median for
    these right-skewed distributions, an implied mean below the weighted median
    is not physically realisable. Binding p95 to the p50 column (D2) inflates the
    weighted figure and still trips the check.

    Note the *direction* this check is sensitive in, which the original message
    stated backwards. It fires when ``N / X`` is too small, i.e. when throughput
    is over-counted (a cumulative total admitted as an interval sample, D3) or
    latency is recorded too large (D2). Throughput that is *under*-counted, as
    averaging the per-operation rates produced in D1, makes ``N / X`` larger and
    moves the run away from this condition -- which is exactly the history:
    correcting D1 doubled ``X``, halved the implied mean, and only then did the
    mis-bound latency of D2 push it below the median and become visible. D1 is
    caught structurally by the parser, not here.
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
    """Detects doubled or dropped ticks.

    The legacy chaos runner incremented its elapsed counter once per *line*
    rather than once per interval; because two operation types were reported
    per interval, its clock advanced at roughly twice wall-clock and injected
    the fault at 34 s rather than the intended 60 s.
    """
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


# --- cross-run checks ------------------------------------------------------
#
# Every check above interrogates one run against itself. Defect D9 is the reason
# that is not enough: the Phase II baseline and the Phase III cluster were
# started with block caches differing by a factor of about fifteen against a
# 205 MB working set, so the baseline served the whole dataset from cache and
# the cluster could not. Both runs were individually correct and passed every
# check in this module; the error lay only in the inference drawn from their
# difference, which no property of a single run can expose. Correcting it moved
# the apparent write-latency overhead from 18.3x to 12.8x, a 43% revision.
#
# The checks below therefore take two runs and assert that the difference
# between them is the one the comparison claims to measure.

#: Server flags that must match between two runs being compared. Cache size is
#: here because it caused D9; the SQL memory pool because it is set alongside it
#: and has the same character.
_MATCHED_SERVER_FLAGS: tuple[str, ...] = ("--cache", "--max-sql-memory")

#: Relative difference in total memory tolerated between two runs being
#: compared. Below this the implied block-cache difference is immaterial against
#: this project's 179 MiB working set; above it, ``--cache`` being a fraction
#: means the two servers cached materially different amounts on identical flags.
MEMORY_TOLERANCE = 0.05

#: Workload parameters that must match. A difference in any of these means the
#: two runs did different work, so their difference is not replication cost.
#: ``seed`` and ``insert_count`` are included because a mismatch against the
#: loaded table makes every operation match zero rows and return in ~3 ms (D8),
#: which reads as a spectacular result rather than a broken one.
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
    """Flags from a recorded ``cockroach start`` command line.

    Parsed from the raw argument list rather than from a curated subset, because
    the recorded artefact deliberately keeps the whole command: the next confound
    of this class will involve a flag not anticipated here.
    """
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
    """The ``host:`` note as a dict, or ``None`` for a run recorded before it.

    Runs measured before 2026-09-03 carry no such note. They are reported as
    unrecorded rather than as some assumed machine, because the whole reason this
    was added is that an unrecorded machine had been silently assumed constant
    across a redeployment that changed it.
    """
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


def check_run_comparability(
    a: dict[str, Any],
    b: dict[str, Any],
    label_a: str = "A",
    label_b: str = "B",
    accept_hardware_difference: bool = False,
) -> list[Finding]:
    """Assert that two runs differ only in the variable under study.

    Takes manifests, not measurement tables: the asymmetry that produced D9 was
    invisible in the data and visible only in how the servers had been started,
    which is why ``preflight.capture_server_config`` writes that into every
    manifest. A comparison drawn across runs whose configurations were never
    checked against each other is exactly the artefact this project exists to
    stop producing.
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

    cmd_a, cmd_b = _server_command(a), _server_command(b)
    if cmd_a is None or cmd_b is None:
        findings.append(
            Finding(
                "run_comparability",
                "warning",
                f"server configuration is unrecorded for "
                f"{label_a if cmd_a is None else label_b}, so the two runs cannot be "
                "shown to have been configured alike; this is the condition under "
                "which D9 went unnoticed",
                {},
            )
        )
    else:
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
                    "variable under study with cache residency (docs/defects.md, D9)",
                    {"mismatched_server_flags": {k: list(v) for k, v in mismatched.items()}},
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
                "shown to have run on comparable machines; this is the condition "
                "under which the unexplained 22% Phase II baseline shift of "
                "2026-09-02 became undiagnosable after the fact",
                {},
            )
        )
    else:
        # CPU count and model are compared exactly; total memory within a
        # tolerance. Memory is compared at all -- despite --cache and
        # --max-sql-memory already being compared -- because those flags are
        # *fractions* of it, so identical flags on unlike machines give unlike
        # absolute caches: D9 in a form the flag comparison cannot see.
        #
        # The tolerance exists because providers report memory that differs by
        # rounding: the two machines in this topology read 4,005,712 kB and
        # 4,007,012 kB, a 0.03% difference that changes the block cache by
        # ~325 kB against a 179 MiB working set. Erroring on that would make the
        # check fire on every legitimate Phase II/III comparison, and a check
        # that rejects correct data gets disabled -- the same failure that made
        # check_littles_law reject sound runs until it was corrected.
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
            # Downgraded to a warning only when the caller has said so
            # explicitly, and the acknowledgement is recorded in the finding
            # rather than making the difference disappear. The two phases of
            # this study are permanently on different CPU models -- the baseline
            # an Intel Xeon, the gateway an AMD EPYC -- which is a stated
            # limitation of the comparison rather than a defect to be fixed, so
            # a hard block would make the project's own headline result
            # uncomputable. Defaulting to an error keeps anyone from stumbling
            # into an unlike comparison without having decided to.
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

    if a.get("cockroach_version") != b.get("cockroach_version"):
        findings.append(
            Finding(
                "run_comparability",
                "error",
                f"{label_a} and {label_b} ran against different server versions "
                f"({a.get('cockroach_version')} vs {b.get('cockroach_version')})",
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


def validate(df: pd.DataFrame, tps_ceiling: float = DEFAULT_TPS_CEILING) -> ValidationReport:
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

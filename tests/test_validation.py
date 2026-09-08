"""Tests for the post-run consistency checks.

The Little's law tests below carry the most weight. That check is the project's
only defence against a latency column bound to the wrong header position (D2),
and it is also the check most capable of rejecting sound data if its lower bound
is chosen carelessly -- which it originally was.
"""

from __future__ import annotations

import pandas as pd
import pytest

from crdblab.analysis.validation import (
    check_error_monotonicity,
    check_littles_law,
    check_plausibility,
    check_quantile_ordering,
    validate,
)


def _rows(concurrency, ticks, ops):
    """Build a long-format frame: one row per (tick, operation type)."""
    out = []
    for elapsed in range(1, ticks + 1):
        for op, (tps, p50, p95, p99, pmax) in ops.items():
            out.append(
                {
                    "ts_utc": "2026-09-02T00:00:00Z",
                    "elapsed_s": float(elapsed),
                    "concurrency": concurrency,
                    "repetition": 1,
                    "op": op,
                    "tps": tps,
                    "tps_cum": tps,
                    "errors_cum": 0,
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "p99_ms": p99,
                    "pmax_ms": pmax,
                    "gateway_cpu_pct": 10.0,
                    "gateway_disk_iops": 100.0,
                    "gateway_rss_bytes": 1_000_000,
                }
            )
    return pd.DataFrame(out)


#: Measured on the cluster, 2026-09-02: reads are served by the local leaseholder
#: while updates pay cross-region Raft quorum, an 84x spread between operation
#: types. N/X = 10/660 * 1000 = 15.15 ms; the frequency-weighted median is
#: 15.14 ms, so Little's law holds to better than a tenth of a percent.
HETEROGENEOUS = _rows(
    10, 12, {"read": (526.0, 0.85, 3.9, 5.8, 7.6), "update": (134.0, 71.3, 104.9, 121.6, 125.8)}
)


def test_littles_law_accepts_a_heterogeneous_workload():
    """The bound is the frequency-weighted median, not the slowest component.

    Comparing N/X against the maximum per-operation p50 rejects this frame --
    15.15 ms against 71.3 ms -- even though it is a faithful recording of a sound
    run. A check that rejects correct data is not conservative but broken, and
    this formulation blocked every tier of the first real cluster benchmark.
    """
    assert check_littles_law(HETEROGENEOUS) == []


def test_littles_law_still_catches_latency_bound_to_the_wrong_column():
    """D2: binding p95 into the p50 column inflates the weighted median."""
    shifted = HETEROGENEOUS.copy()
    shifted["p50_ms"] = shifted["p95_ms"]  # what positional indexing produced
    findings = check_littles_law(shifted)
    assert findings, "a p95-for-p50 substitution must be detected"
    assert findings[0].check == "littles_law"


def test_littles_law_catches_throughput_over_counted():
    """A cumulative total admitted as an interval sample collapses N/X (D3)."""
    inflated = HETEROGENEOUS.copy()
    inflated["tps"] = inflated["tps"] * 20.0
    assert check_littles_law(inflated), "over-counted throughput must be detected"


def test_littles_law_is_insensitive_to_under_counted_throughput():
    """Documents the direction this check does *not* cover.

    Averaging the per-operation rates instead of summing them (D1) halves ``X``
    and therefore *raises* ``N / X``, moving the run away from the failure
    condition. This check cannot detect that and must not be described as
    though it can; D1 is prevented structurally by the parser retaining the
    operation type as an explicit dimension.
    """
    halved = HETEROGENEOUS.copy()
    halved["tps"] = halved["tps"] / 2.0
    assert check_littles_law(halved) == []


def test_littles_law_ignores_ticks_with_no_throughput():
    idle = _rows(10, 3, {"read": (0.0, 0.0, 0.0, 0.0, 0.0)})
    assert check_littles_law(idle) == []


# --- the other checks ------------------------------------------------------

def test_plausibility_catches_a_cumulative_total_admitted_as_a_sample():
    """D3: the summary block's ops(total) read as an instantaneous rate."""
    df = HETEROGENEOUS.copy()
    df.loc[0, "tps"] = 185_000.0
    findings = check_plausibility(df, ceiling=20_000.0)
    assert findings and findings[0].check == "plausibility"


def test_quantile_ordering_catches_a_transposition():
    df = HETEROGENEOUS.copy()
    df.loc[0, "p50_ms"] = df.loc[0, "p99_ms"] + 1.0
    findings = check_quantile_ordering(df)
    assert findings and findings[0].check == "quantile_ordering"


def test_error_counter_must_not_decrease_within_a_tier():
    df = HETEROGENEOUS.copy()
    df.loc[df.index[-1], "errors_cum"] = -5
    assert check_error_monotonicity(df)


def test_a_sound_run_passes_every_check():
    report = validate(HETEROGENEOUS)
    assert report.ok, [f.message for f in report.findings if f.severity == "error"]


# --- cross-engine comparability --------------------------------------------

def _manifest(engine, version, cpus=2, mem=4007004):
    return {
        "engine": engine,
        "server_version": version,
        "profile": {"workload": {}},
        "notes": [
            " server: some server start command",
            f" host: cpus={cpus} mem_total_kb={mem} cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz",
        ],
    }


def test_two_engines_differing_in_version_is_the_comparison_not_a_confound():
    """This refused every CockroachDB vs PostgreSQL comparison the project
    exists to produce -- "ran against different server versions (v26.3.0 vs
    None)" -- because the version check did not know the engines were meant to
    differ. It is reported, since which build was measured is part of the
    claim, but it is not grounds for refusal."""
    from crdblab.analysis.validation import check_run_comparability

    findings = check_run_comparability(
        _manifest("cockroachdb", "v26.3.0"),
        _manifest("postgresql", "postgres (PostgreSQL) 16.15"),
        "crdb", "pg",
    )
    assert [f for f in findings if f.severity == "error"] == []
    assert any("different engines" in f.message for f in findings)


def test_two_runs_of_the_same_engine_still_must_match_versions():
    from crdblab.analysis.validation import check_run_comparability

    findings = check_run_comparability(
        _manifest("cockroachdb", "v26.3.0"),
        _manifest("cockroachdb", "v25.1.0"),
        "a", "b",
    )
    assert any(f.severity == "error" and "server versions" in f.message for f in findings)


def test_a_legacy_run_records_its_version_only_as_cockroach_version():
    """Runs written before `server_version` existed carry it in the old field;
    reading both keeps an old CockroachDB run comparable with a new one."""
    from crdblab.analysis.validation import check_run_comparability

    old = _manifest("cockroachdb", None)
    old["cockroach_version"] = "v26.3.0"
    findings = check_run_comparability(old, _manifest("cockroachdb", "v26.3.0"), "old", "new")
    assert [f for f in findings if f.severity == "error"] == []


def test_unlike_machines_are_still_refused_across_engines():
    """The point of capturing hardware for PostgreSQL too: this comparison used
    to pass unexamined, because a run with no `host:` note could not be checked
    against one that had it."""
    from crdblab.analysis.validation import check_run_comparability

    findings = check_run_comparability(
        _manifest("cockroachdb", "v26.3.0", cpus=2),
        _manifest("postgresql", "16.15", cpus=8),
        "crdb", "pg",
    )
    assert any(f.severity == "error" and "hardware" in f.message.lower() for f in findings)

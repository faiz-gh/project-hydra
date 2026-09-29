"""Tests for the analysis layer: loading, steady state, engine comparison and resilience."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from crdblab.analysis import engine_comparison, resilience, steady_state
from crdblab.analysis.loader import RunLoadError, load_run
from crdblab.analysis.validation import validate_comparison
from crdblab.core.recorder import COLUMNS, NETWORK_COLUMNS
from crdblab.topology import Node, Topology

# --- fixtures --------------------------------------------------------------

_SERVER_NOTE = (
    "2026-09-02T00:00:00Z server: 1 cockroach start --insecure "
    "--store=/var/lib/cockroach --cache=0.25 --max-sql-memory=0.25"
)


def _rows(tiers, repetitions=1, ticks=12, wall_offset=None):
    """A sound long-format table: 80/20 reads to updates, Little's law satisfied."""
    out = []
    for concurrency, read_tps, read_p50, update_tps, update_p50 in tiers:
        for rep in range(1, repetitions + 1):
            for tick in range(1, ticks + 1):
                for op, tps, p50 in (
                    ("read", read_tps, read_p50),
                    ("update", update_tps, update_p50),
                ):
                    row = {c: "" for c in COLUMNS}
                    row.update(
                        ts_utc="2026-09-02T00:00:00Z",
                        elapsed_s=float(tick),
                        concurrency=concurrency,
                        repetition=rep,
                        op=op,
                        tps=tps,
                        tps_cum=tps,
                        errors_cum=0,
                        p50_ms=p50,
                        p95_ms=p50 * 2,
                        p99_ms=p50 * 3,
                        pmax_ms=p50 * 4,
                    )
                    if wall_offset is not None:
                        row["wall_offset_s"] = float(tick) + wall_offset
                    out.append(row)
    return out


def _write_run(
    tmp_path: Path,
    name: str,
    rows,
    phase="bench_cluster",
    events=None,
    manifest_extra=None,
    schema_version="2.1",
):
    path = tmp_path / name
    (path / "raw").mkdir(parents=True)
    pd.DataFrame(rows, columns=list(COLUMNS)).to_csv(path / "metrics.csv", index=False)
    manifest = {
        "run_id": name,
        "phase": phase,
        "schema_version": schema_version,
        "cockroach_version": "v26.3.0",
        "notes": [_SERVER_NOTE],
        "profile": {
            "name": "test",
            "workload": {
                "generator": "ycsb",
                "ycsb_workload": "CUSTOM",
                "read_freq": 0.8,
                "update_freq": 0.2,
                "request_distribution": "uniform",
                "seed": 42,
                "insert_count": 125000,
                "duration_s": 60,
                "warmup_s": 5,
            },
            "chaos": {"recovery_threshold": 0.8, "recovery_hold_s": 5, "inject_at_s": 10},
            "tps_ceiling": 20000.0,
        },
    }
    manifest.update(manifest_extra or {})
    (path / "manifest.json").write_text(json.dumps(manifest))
    if events is not None:
        (path / "events.json").write_text(json.dumps(events))
    return path


# --- loader ----------------------------------------------------------------


def test_a_run_without_a_manifest_is_not_loadable(tmp_path):
    """Provenance is a precondition of analysis, not an optional extra."""
    path = _write_run(tmp_path, "no_manifest", _rows([(10, 800, 1.0, 200, 40.0)]))
    (path / "manifest.json").unlink()
    with pytest.raises(RunLoadError, match="manifest"):
        load_run(path)


def test_a_run_that_fails_validation_is_refused(tmp_path):
    """Validation gates analysis; it is not advisory."""
    rows = _rows([(10, 800, 1.0, 200, 40.0)])
    rows[0]["tps"] = 150_000.0
    path = _write_run(tmp_path, "implausible", rows)
    with pytest.raises(RunLoadError, match="ceiling"):
        load_run(path)
    # It remains inspectable, so a failed run can still be diagnosed.
    assert load_run(path, require_valid=False).report.ok is False


def test_throughput_sums_across_op_types_but_errors_do_not(tmp_path):
    """Read and write rates are components of one load, so throughput sums across ops."""
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=3)
    for row in rows:
        row["errors_cum"] = 7
    run = load_run(_write_run(tmp_path, "sums", rows))
    ticks = run.ticks()
    assert ticks["total_tps"].unique().tolist() == [1000.0]
    assert ticks["errors_cum"].unique().tolist() == [7]


def test_latency_is_never_pooled_across_operation_types(tmp_path):
    """The op type stays a grouping key, so pooling is structurally impossible."""
    run = load_run(_write_run(tmp_path, "unpooled", _rows([(10, 800, 1.0, 200, 40.0)])))
    per_op = run.latency_by_op()
    assert set(per_op["op"]) == {"read", "update"}
    assert per_op.set_index("op").loc["read", "p50_ms"] == pytest.approx(1.0)
    assert per_op.set_index("op").loc["update", "p50_ms"] == pytest.approx(40.0)

    # The only cross-op latency scalar offered is named as a weighted blend, and
    # is neither of the components nor their unweighted mean (20.5 ms).
    weighted = run.ticks()["weighted_p50_ms"].iloc[0]
    assert weighted == pytest.approx((800 * 1.0 + 200 * 40.0) / 1000.0)


# --- steady state ----------------------------------------------------------


def test_a_single_repetition_yields_no_interval(tmp_path):
    """An unmeasured spread is None, never zero."""
    run = load_run(_write_run(tmp_path, "one_rep", _rows([(10, 800, 1.0, 200, 40.0)])))
    tier = steady_state.per_tier(run).iloc[0]
    assert tier["repetitions"] == 1
    assert tier["ci95_half_width_tps"] is None
    assert tier["sd_total_tps"] is None


def test_the_interval_is_computed_over_repetitions_not_pooled_samples(tmp_path):
    """Successive seconds of one run are not independent observations."""
    run = load_run(
        _write_run(tmp_path, "three_reps", _rows([(10, 800, 1.0, 200, 40.0)], repetitions=3))
    )
    tier = steady_state.per_tier(run).iloc[0]
    assert tier["repetitions"] == 3
    # The three repetitions are identical here, so the interval is zero; the
    # point is that n is the repetition count, not the tick count.
    assert tier["ci95_half_width_tps"] == pytest.approx(0.0)


# --- engine comparison -----------------------------------------------------


def _pair(tmp_path):
    crdb = load_run(
        _write_run(
            tmp_path,
            "crdb",
            _rows([(10, 2800, 2.0, 700, 5.5), (50, 2900, 12.0, 720, 19.0)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path,
            "pg",
            _rows([(10, 520, 0.9, 130, 71.0), (50, 1330, 10.0, 330, 106.0)]),
            phase="bench_cluster",
        )
    )
    return crdb, pg


def test_matched_throughput_refuses_when_the_ranges_do_not_overlap(tmp_path):
    """No load level was measured for both engines, so no matched-throughput scalar is produced."""
    crdb, pg = _pair(tmp_path)
    matched = engine_comparison.matched_throughput(crdb, pg, "update")
    assert matched["comparable"] is False
    assert "do not overlap" in matched["reason"]
    assert matched["points"] == []


def test_matched_throughput_compares_only_inside_the_measured_range(tmp_path):
    """Where the ranges do overlap, every point lies within both curves."""
    crdb = load_run(
        _write_run(
            tmp_path,
            "crdb_wide",
            _rows([(10, 800, 2.0, 200, 5.0), (50, 1600, 8.0, 400, 9.0)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path,
            "pg_wide",
            _rows([(10, 600, 1.0, 150, 70.0), (50, 1200, 6.0, 300, 90.0)]),
            phase="bench_cluster",
        )
    )
    matched = engine_comparison.matched_throughput(crdb, pg, "update")
    assert matched["comparable"] is True
    lo, hi = matched["overlap_tps"]
    assert all(lo <= p["throughput_tps"] <= hi for p in matched["points"])
    assert all(p["overhead_x"] > 1 for p in matched["points"])


def test_the_same_concurrency_delta_is_produced_only_as_a_labelled_artefact(tmp_path):
    """The intuitive comparison is computed, and marked as not a result."""
    crdb, pg = _pair(tmp_path)
    delta = engine_comparison.same_concurrency_delta(crdb, pg)
    assert delta["comparable"] is False
    assert "error case study" in delta["use"]
    assert delta["rows"][0]["read_p50_ratio_x"] < 1.0


def test_a_comparison_across_mismatched_block_caches_is_refused(tmp_path):
    """Mismatched block caches make a comparison invalid; no single-run check can detect it."""
    crdb, pg = _pair(tmp_path)
    starved = dict(pg.manifest)
    # No --cache flag at all, i.e. CockroachDB's 128 MiB default.
    starved["notes"] = [
        (
            "2026-09-02T00:00:00Z server: 1 cockroach start --insecure "
            "--store=/var/lib/cockroach"
        )
    ]
    report = validate_comparison(crdb.manifest, starved, "crdb", "pg")
    assert report.ok is False
    assert "--cache" in report.findings[0].message
    assert "128 MiB default" in report.findings[0].message


def test_a_comparison_across_different_workloads_is_refused(tmp_path):
    """A seed mismatch means the two runs did different work."""
    crdb, pg = _pair(tmp_path)
    other = json.loads(json.dumps(pg.manifest))
    other["profile"]["workload"]["seed"] = 1
    report = validate_comparison(crdb.manifest, other, "crdb", "pg")
    assert report.ok is False
    assert "seed" in report.findings[0].message


def test_compare_refuses_outright_when_the_runs_are_not_comparable(tmp_path):
    crdb, _ = _pair(tmp_path)
    broken = load_run(
        _write_run(
            tmp_path,
            "pg_v2",
            _rows([(10, 520, 0.9, 130, 71.0)]),
            phase="bench_cluster",
            manifest_extra={"cockroach_version": "v25.1.0"},
        )
    )
    with pytest.raises(engine_comparison.NotComparable):
        engine_comparison.compare(crdb, broken)


def test_a_still_rising_curve_reports_its_peak_as_a_lower_bound(tmp_path):
    """A throughput that is still climbing is a lower bound, not the capacity."""
    crdb, pg = _pair(tmp_path)
    rising = engine_comparison._saturation(steady_state.per_tier(pg))
    assert rising["saturated"] is False
    assert "lower bound" in rising["detail"]
    flat = engine_comparison._saturation(steady_state.per_tier(crdb))
    assert flat["saturated"] is True


# --- resilience ------------------------------------------------------------

_EVENTS = {
    "mode": "recover",
    "target": "linode-2",
    "t_start_utc": "2026-09-02T00:00:00.000000Z",
    "t_end_utc": "2026-09-02T00:00:17.000000Z",
    "injected": {"at_offset_s": 10.0, "at_monotonic": 100.0},
    "recovery_threshold": 0.8,
    "recovery_hold_s": 5,
    "rpo": {"acknowledged": 100, "ambiguous": 0, "refused": 0, "rpo_violations": 0},
}


def test_alignment_is_measured_when_both_clocks_were_recorded(tmp_path):
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_21",
            _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
            phase="p4_chaos",
            events=_EVENTS,
        )
    )
    alignment = resilience.align(run)
    assert alignment.method == "measured"
    assert alignment.offset_s == pytest.approx(5.4)
    assert alignment.uncertainty_s == 0.0
    assert alignment.to_generator(10.0) == pytest.approx(4.6)


def test_alignment_is_bounded_when_only_the_generator_clock_was_recorded(tmp_path):
    """A schema 2.0 run gets an interval, not an estimate."""
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_20",
            _rows([(10, 800, 1.0, 200, 40.0)]),
            phase="p4_chaos",
            events=_EVENTS,
            schema_version="2.0",
        )
    )
    alignment = resilience.align(run)
    assert alignment.method == "bounded"
    assert alignment.offset_s is None
    # 17 s envelope, 12 s of generator intervals.
    assert (alignment.lower_s, alignment.upper_s) == (0.0, 5.0)
    assert alignment.to_generator(10.0) == (5.0, 10.0)


def test_the_fault_position_is_an_interval_under_a_bounded_alignment(tmp_path):
    """The figure caveat is data, not prose: a band cannot be drawn as a line."""
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_band",
            _rows([(10, 800, 1.0, 200, 40.0)]),
            phase="p4_chaos",
            events=_EVENTS,
            schema_version="2.0",
        )
    )
    fault = resilience.fault_offsets(run, resilience.align(run))
    assert fault["generator_elapsed_s"] is None
    assert fault["generator_elapsed_bounds_s"] == [5.0, 10.0]

    profile = resilience.degradation_profile(run, resilience.align(run))
    assert "since_fault_s" not in profile.columns
    assert {"since_fault_lower_s", "since_fault_upper_s"} <= set(profile.columns)


def test_a_measured_alignment_puts_the_fault_on_the_throughput_axis(tmp_path):
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_exact",
            _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
            phase="p4_chaos",
            events=_EVENTS,
        )
    )
    profile = resilience.degradation_profile(run, resilience.align(run))
    assert "since_fault_s" in profile.columns
    assert "since_fault_lower_s" not in profile.columns


def test_an_availability_rto_below_the_audit_cadence_is_not_quotable(tmp_path):
    """0.07 s against a 0.40 s sampling interval is not a recovery time."""
    events = dict(_EVENTS)
    events["availability"] = {
        "availability_rto_s": 0.068,
        "resolution_s": 0.4017,
        "write_gap_s": 0.444,
    }
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_fast",
            _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
            phase="p4_chaos",
            events=events,
        )
    )
    result = resilience.availability(run)
    assert result["below_resolution"] is True
    assert result["quotable_value_s"] is None
    assert "0.40 s resolution" in result["claim"]
    assert "0.068" not in result["claim"]


def test_an_availability_rto_above_the_cadence_is_quotable(tmp_path):
    events = dict(_EVENTS)
    events["availability"] = {"availability_rto_s": 4.2, "resolution_s": 0.4}
    run = load_run(
        _write_run(
            tmp_path,
            "chaos_slow",
            _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
            phase="p4_chaos",
            events=events,
        )
    )
    result = resilience.availability(run)
    assert result["below_resolution"] is False
    assert result["quotable_value_s"] == 4.2


def test_availability_rto_is_rederived_from_the_audit_log_when_present(tmp_path):
    """The figure is recomputed from its observations, not taken on trust."""
    path = _write_run(
        tmp_path,
        "chaos_audit",
        _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
        phase="p4_chaos",
        events=_EVENTS,
    )
    # Writes every 0.5 s, a gap after the fault at 10.0 s, then resumption. The
    # cadence must be denser than the outage for it to be measurable.
    attempts = [
        {"wall_offset_s": round(0.5 * i, 3), "seq_id": i, "outcome": "ack"}
        for i in range(1, 20)  # last ack at 9.5 s, just before the fault at 10.0 s
    ]
    attempts += [
        {"wall_offset_s": 10.5, "seq_id": 21, "outcome": "ambiguous"},
        {"wall_offset_s": 11.0, "seq_id": 22, "outcome": "refused"},
        {"wall_offset_s": 14.0, "seq_id": 23, "outcome": "ack"},
    ]
    pd.DataFrame(attempts).to_csv(path / "audit.csv", index=False)

    result = resilience.availability(load_run(path))
    assert result["source"] == "re-derived from audit.csv"
    # Fault at 10.0 s; the next acknowledged write is at 14.0 s.
    assert result["availability_rto_s"] == pytest.approx(4.0)
    assert result["resolution_s"] == pytest.approx(0.5)
    assert result["quotable_value_s"] == pytest.approx(4.0)


def test_a_new_stable_state_is_reported_as_undefined_not_as_no_recovery(tmp_path):
    """A new stable state below the threshold is reported as undefined, not as no recovery."""
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40, wall_offset=0.0)
    for row in rows:
        if row["elapsed_s"] > 12:
            row["tps"] = row["tps"] * 0.65
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(tmp_path, "chaos_degraded", rows, phase="p4_chaos", events=events)
    )
    result = resilience.performance(run, resilience.align(run))
    assert result["defined"] is False
    assert result["classification"] == "degraded_steady_state"
    assert result["post_fault_state"]["settled"] is True
    assert result["post_fault_state"]["fraction_of_baseline"] == pytest.approx(0.65, abs=0.02)
    assert "undefined" in result["claim"]


def test_a_recovering_run_reports_a_performance_rto(tmp_path):
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40, wall_offset=0.0)
    for row in rows:
        if 10 < row["elapsed_s"] <= 14:
            row["tps"] = row["tps"] * 0.2
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(tmp_path, "chaos_recovered", rows, phase="p4_chaos", events=events)
    )
    result = resilience.performance(run, resilience.align(run))
    assert result["defined"] is True
    assert result["rto_s"] == pytest.approx(5.0)


def test_a_bounded_alignment_makes_the_performance_rto_an_interval(tmp_path):
    """The unmeasured clock offset propagates into the figure, visibly."""
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40)
    for row in rows:
        if 10 < row["elapsed_s"] <= 14:
            row["tps"] = row["tps"] * 0.2
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(
            tmp_path, "chaos_bounded_rto", rows, phase="p4_chaos",
            events=events, schema_version="2.0",
        )
    )
    result = resilience.performance(run, resilience.align(run))
    assert result["rto_s"] is None
    assert result["rto_bounds_s"][0] != result["rto_bounds_s"][1]
    assert "unmeasured clock offset" in result["claim"]


# --- schema contract -------------------------------------------------------


def test_the_metrics_schema_is_pinned():
    """Changing the measurement schema must be a deliberate, reviewed edit."""
    from crdblab.core.recorder import AUDIT_COLUMNS, SCHEMA_VERSION

    assert SCHEMA_VERSION == "2.1"
    assert COLUMNS == (
        "ts_utc",
        "elapsed_s",
        "wall_offset_s",
        "concurrency",
        "repetition",
        "op",
        "tps",
        "tps_cum",
        "errors_cum",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "pmax_ms",
        "gateway_cpu_pct",
        "gateway_disk_iops",
        "gateway_rss_bytes",
    )
    assert AUDIT_COLUMNS == ("wall_offset_s", "seq_id", "outcome")


@pytest.mark.parametrize("module", ["bench", "p4_chaos"])
def test_every_phase_writes_a_row_matching_the_declared_schema(module):
    """Catch schema drift statically, not thirty minutes into a measurement."""
    import ast
    import inspect

    from crdblab.core.recorder import AUDIT_COLUMNS
    from crdblab.phases import bench, p4_chaos

    source = inspect.getsource({"bench": bench, "p4_chaos": p4_chaos}[module])
    schemas = {frozenset(COLUMNS), frozenset(AUDIT_COLUMNS)}

    rows = [
        {k.value for k in node.keys}
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Dict)
        and node.keys
        and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)
        and ({"ts_utc", "elapsed_s"} <= {k.value for k in node.keys}
             or {"seq_id", "outcome"} <= {k.value for k in node.keys})
    ]
    assert rows, f"{module} builds no measurement row; has the writer been bypassed?"
    for keys in rows:
        assert frozenset(keys) in schemas, (
            f"{module} builds a row with keys {sorted(keys)}, which matches no "
            "declared schema in recorder.py"
        )


def test_a_run_whose_preflight_failed_is_refused(tmp_path):
    """Pre-flight is a separate gate from validation and must be enforced too."""
    import json as _json

    path = _write_run(tmp_path, "preflight_failed", _rows([(10, 800, 1.0, 200, 40.0)]))
    (path / "preflight.json").write_text(
        _json.dumps(
            {
                "ok": False,
                "checks": [
                    {"name": "row_match", "passed": False,
                     "detail": "0/50000 operations matched a row (rate 0.0000)"}
                ],
            }
        )
    )
    with pytest.raises(RunLoadError, match="failed pre-flight"):
        load_run(path)
    assert load_run(path, require_valid=False).report.ok is True


def test_the_overlap_remedy_does_not_advise_raising_a_saturated_phase(tmp_path):
    """Once the slower engine has saturated, more concurrency cannot close the gap."""
    crdb = load_run(
        _write_run(
            tmp_path, "crdb_sat",
            _rows([(10, 2000, 2.0, 500, 6.0), (50, 2020, 8.0, 505, 24.0)]),
            phase="bench_cluster",
        )
    )
    # PostgreSQL peaks then declines: saturated, with its ceiling below
    # CockroachDB's floor.
    pg = load_run(
        _write_run(
            tmp_path, "pg_sat",
            _rows([(10, 400, 1.0, 100, 74.0), (50, 380, 9.0, 95, 108.0)]),
            phase="bench_cluster",
        )
    )
    remedy = engine_comparison.matched_throughput(crdb, pg, "update")["remedy"]
    assert "saturated" in remedy
    assert "lower" in remedy
    assert "higher concurrency tier cannot" in remedy


def test_the_overlap_remedy_suggests_extending_a_still_rising_phase(tmp_path):
    """While the slower phase is still climbing, extending its sweep is right."""
    crdb = load_run(
        _write_run(
            tmp_path, "crdb_rise",
            _rows([(10, 2000, 2.0, 500, 6.0), (50, 2020, 8.0, 505, 24.0)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path, "pg_rise",
            _rows([(10, 300, 1.0, 75, 74.0), (50, 700, 9.0, 175, 108.0)]),
            phase="bench_cluster",
        )
    )
    remedy = engine_comparison.matched_throughput(crdb, pg, "update")["remedy"]
    assert "still rising" in remedy


def test_interpolation_does_not_cross_the_saturation_fold(tmp_path):
    """Past saturation one throughput maps to two latencies; don't average them."""
    pg = load_run(
        _write_run(
            tmp_path, "pg_fold",
            _rows([
                (10, 400, 1.0, 100, 74.0),    # 500 tps
                (50, 1000, 8.0, 250, 108.0),  # 1250 tps  <- peak
                (200, 800, 90.0, 200, 230.0),  # 1000 tps, folded back
            ]),
            phase="bench_cluster",
        )
    )
    curve = engine_comparison.throughput_latency_curve(pg, "update")
    # Ordered by the control variable, so the fold is visible rather than sorted away.
    assert curve["concurrency"].tolist() == [10, 50, 200]

    # 1000 tps occurs twice: on the rising branch (interpolated) and at C=200.
    # Only the rising branch is used, so the answer stays below the peak's latency.
    value = engine_comparison._interpolate(curve, 1000.0, "p50_ms")
    assert value is not None
    assert value < 108.0
    # And nothing beyond the peak is reachable.
    assert engine_comparison._interpolate(curve, 1300.0, "p50_ms") is None


def test_matched_throughput_reports_overhead_where_the_ranges_meet(tmp_path):
    """Matched throughput reports latency overhead where the two ranges meet."""
    # CockroachDB swept down to 600 ops/s, so it overlaps PostgreSQL's band.
    crdb = load_run(
        _write_run(
            tmp_path, "crdb_low",
            _rows([
                (1, 480, 0.9, 120, 1.6),      # 600 tps
                (2, 960, 1.1, 240, 2.0),      # 1200 tps
                (10, 2000, 3.0, 500, 6.7),    # 2500 tps
            ]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path, "pg_low",
            _rows([
                (10, 500, 1.0, 125, 74.0),    # 625 tps
                (50, 1400, 8.7, 350, 107.6),  # 1750 tps
            ]),
            phase="bench_cluster",
        )
    )

    matched = engine_comparison.matched_throughput(crdb, pg, "update")
    assert matched["comparable"] is True
    lo, hi = matched["overlap_tps"]
    assert lo == pytest.approx(625.0) and hi == pytest.approx(1750.0)

    assert matched["points"], "an overlapping band must yield comparison points"
    for point in matched["points"]:
        assert lo <= point["throughput_tps"] <= hi
        # Replication cost is a real penalty at every common load.
        assert point["overhead_x"] > 1.0
        assert point["measured_in"] in ("both", "CockroachDB", "PostgreSQL")

    # At least one point is an observation on each side rather than two
    # interpolations meeting in the middle.
    assert {p["measured_in"] for p in matched["points"]} & {"CockroachDB", "PostgreSQL", "both"}
    assert "interpolated" in matched["caveat"]


def test_matched_throughput_reports_how_loaded_each_phase_is(tmp_path):
    """Matched throughput reports each engine's utilisation, since the gap matters."""
    crdb = load_run(
        _write_run(
            tmp_path, "crdb_util",
            _rows([
                (1, 480, 0.9, 120, 1.6),      # 600 tps
                (2, 960, 1.1, 240, 2.0),      # 1200 tps
                (10, 2000, 3.0, 500, 6.7),    # 2500 tps = peak
            ]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path, "pg_util",
            _rows([
                (10, 500, 1.0, 125, 74.0),    # 625 tps
                (50, 1400, 8.7, 350, 107.6),  # 1750 tps = peak
            ]),
            phase="bench_cluster",
        )
    )
    matched = engine_comparison.matched_throughput(crdb, pg, "update")

    for point in matched["points"]:
        assert 0.0 < point["crdb_utilisation"] <= 1.0
        assert 0.0 < point["pg_utilisation"] <= 1.0
        assert point["utilisation_gap"] == pytest.approx(
            abs(point["crdb_utilisation"] - point["pg_utilisation"]), abs=1e-3
        )

    # PostgreSQL saturates first, so its utilisation runs ahead and the gap
    # widens with throughput.
    top = max(matched["points"], key=lambda p: p["throughput_tps"])
    assert top["pg_utilisation"] > top["crdb_utilisation"]

    # The nominated point is genuinely the one with the smallest gap.
    least = matched["least_confounded"]
    assert least is not None
    assert least["utilisation_gap"] == min(p["utilisation_gap"] for p in matched["points"])


def test_a_comparison_across_different_hardware_is_refused(tmp_path):
    """Identical flags on unlike machines are not an identical configuration."""
    crdb, pg = _pair(tmp_path)
    smaller = json.loads(json.dumps(pg.manifest))
    crdb.manifest["notes"] = crdb.manifest.get("notes", []) + [
        "2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4007012 cpu_model=Xeon @ 2.80GHz"
    ]
    smaller["notes"] = smaller.get("notes", []) + [
        "2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=3072000 cpu_model=Xeon @ 2.80GHz"
    ]
    report = validate_comparison(crdb.manifest, smaller, "crdb", "pg")
    assert report.ok is False
    assert any("different hardware" in f.message for f in report.findings)
    assert any("mem_total_kb" in f.message for f in report.findings)


def test_an_unrecorded_machine_warns_rather_than_passing_silently(tmp_path):
    crdb, pg = _pair(tmp_path)
    report = validate_comparison(crdb.manifest, pg.manifest, "crdb", "pg")
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert any("host hardware is unrecorded" in f.message for f in warnings)


def test_provider_memory_rounding_does_not_fire_the_hardware_check(tmp_path):
    """The two real machines report 4,005,712 and 4,007,012 kB. Erroring on a
    0.03% difference would make the check fire on every legitimate Phase II/III
    comparison, and a check that rejects correct data gets disabled."""
    crdb, pg = _pair(tmp_path)
    model = "cpu_model=AMD EPYC 7713 64-Core Processor"
    crdb.manifest["notes"] = crdb.manifest.get("notes", []) + [
        f"2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4005712 {model}"
    ]
    other = json.loads(json.dumps(pg.manifest))
    other["notes"] = other.get("notes", []) + [
        f"2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4007012 {model}"
    ]
    report = validate_comparison(crdb.manifest, other, "crdb", "pg")
    assert not any("different hardware" in f.message for f in report.findings)


def test_a_materially_different_memory_size_still_fires(tmp_path):
    crdb, pg = _pair(tmp_path)
    model = "cpu_model=AMD EPYC 7713 64-Core Processor"
    crdb.manifest["notes"] = crdb.manifest.get("notes", []) + [
        f"2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4005712 {model}"
    ]
    other = json.loads(json.dumps(pg.manifest))
    other["notes"] = other.get("notes", []) + [
        f"2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=3072000 {model}"
    ]
    report = validate_comparison(crdb.manifest, other, "crdb", "pg")
    assert report.ok is False
    assert any("mem_total_kb" in f.message for f in report.findings)


# --- matched utilisation ----------------------------------------------------

def _low_tier_pair(tmp_path):
    """Both engines swept down to a single worker."""
    crdb = load_run(
        _write_run(
            tmp_path,
            "crdb_low",
            _rows([(1, 865, 0.6, 216, 2.2), (10, 2000, 2.0, 500, 6.9),
                   (50, 2050, 17.0, 512, 25.3)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path,
            "pg_low",
            _rows([(1, 53, 0.7, 13, 72.7), (10, 490, 1.1, 122, 75.3),
                   (50, 1390, 8.7, 348, 105.1)]),
            phase="bench_cluster",
        )
    )
    return crdb, pg


def test_matched_utilisation_compares_at_different_throughputs_by_design(tmp_path):
    crdb, pg = _low_tier_pair(tmp_path)
    out = engine_comparison.matched_utilisation(crdb, pg)
    assert out["comparable"] is True
    assert "utilisation" in out["holds_fixed"]
    for point in out["points"]:
        # The defining property: equal utilisation, unequal throughput.
        assert point["crdb_tps"] != point["pg_tps"]
    assert out["crdb_peak_tps"] > out["pg_peak_tps"]


def test_matched_throughput_can_never_reach_equal_utilisation(tmp_path):
    """The gap is T * (1/peak_iii - 1/peak_ii): linear in T, zero only at zero
    load. This is why both comparisons exist rather than one superseding the
    other -- no amount of extra tiers closes it."""
    crdb, pg = _low_tier_pair(tmp_path)
    matched = engine_comparison.matched_throughput(crdb, pg, "update")
    gaps = [p["utilisation_gap"] for p in matched["points"] if p["utilisation_gap"]]
    assert gaps and min(gaps) > 0.0
    # Narrowest at the bottom of the overlap, widening with throughput.
    ordered = [
        p["utilisation_gap"] for p in
        sorted(matched["points"], key=lambda p: p["throughput_tps"])
        if p["utilisation_gap"] is not None
    ]
    assert ordered == sorted(ordered)


def test_a_single_worker_tier_is_unqueued_structurally_not_statistically(tmp_path):
    """One worker means one operation outstanding, so there is nothing to wait
    behind. Gating this on a Little's-law threshold denied a structurally
    impossible queue on a 5.1% blend artefact."""
    crdb, pg = _low_tier_pair(tmp_path)
    out = engine_comparison.lightest_load_write_latency(crdb, pg)
    assert out["crdb"]["concurrency"] == 1
    assert out["pg"]["concurrency"] == 1
    assert out["both_unqueued"] is True
    assert "neither median contains queueing" in out["caveat"]


def test_a_phase_that_never_reached_one_worker_is_not_called_unqueued(tmp_path):
    crdb, pg = _pair(tmp_path)
    out = engine_comparison.lightest_load_write_latency(crdb, pg)
    assert out["both_unqueued"] is False
    assert "at least one side is queueing" in out["caveat"]


def test_a_hardware_difference_can_be_accepted_explicitly_and_is_recorded(tmp_path):
    """A hardware difference can be accepted explicitly, and is then recorded as a warning."""
    crdb, pg = _pair(tmp_path)
    crdb.manifest["notes"] = crdb.manifest.get("notes", []) + [
        "2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4007012 cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz"
    ]
    other = json.loads(json.dumps(pg.manifest))
    other["notes"] = other.get("notes", []) + [
        "2026-09-03T00:00:00Z host: cpus=2 mem_total_kb=4005704 cpu_model=AMD EPYC 7713 64-Core Processor"
    ]
    refused = validate_comparison(crdb.manifest, other, "crdb", "pg")
    assert refused.ok is False

    accepted = validate_comparison(
        crdb.manifest, other, "crdb", "pg", accept_hardware_difference=True
    )
    assert accepted.ok is True
    warning = next(f for f in accepted.findings if "different" in f.message)
    assert warning.severity == "warning"
    assert warning.detail["accepted"] is True
    # The difference is still named, not erased.
    assert "AMD EPYC" in warning.message


def test_accepting_hardware_does_not_excuse_a_workload_difference(tmp_path):
    """The override is scoped to hardware. A seed mismatch still refuses."""
    crdb, pg = _pair(tmp_path)
    other = json.loads(json.dumps(pg.manifest))
    other["profile"]["workload"]["seed"] = 1
    report = validate_comparison(
        crdb.manifest, other, "crdb", "pg", accept_hardware_difference=True
    )
    assert report.ok is False
    assert any("seed" in f.message for f in report.findings)


def test_the_unqueued_ratio_is_computed_before_rounding(tmp_path):
    """Rounding an input, dividing, then rounding again carries the first
    rounding's error into the result. It produced two figures for one quantity
    (50.37x against 50.38x), which a dissertation then has to reconcile."""
    crdb, pg = _low_tier_pair(tmp_path)
    out = engine_comparison.lightest_load_write_latency(crdb, pg)
    exact = out["pg"]["_p50_exact"] / out["crdb"]["_p50_exact"]
    assert out["ratio_x"] == round(exact, 2)
    # ...and specifically not the ratio of the displayed values.
    from_display = round(out["pg"]["p50_ms"] / out["crdb"]["p50_ms"], 2)
    assert out["ratio_x"] == round(exact, 2) and abs(exact - from_display) < 1.0


def test_the_same_concurrency_throughput_ratio_is_computed_before_rounding(tmp_path):
    """The same-concurrency throughput ratio is computed from unrounded values."""
    crdb = load_run(
        _write_run(
            tmp_path,
            "crdb_ratio",
            _rows([(1, 1365.813, 0.4, 341.453, 1.416),
                   (10, 2840.465, 2.0, 710.116, 5.093)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path,
            "pg_ratio",
            _rows([(1, 54.087, 0.7, 13.522, 71.325),
                   (10, 507.062, 1.1, 126.766, 72.013)]),
            phase="bench_cluster",
        )
    )
    row = next(
        r
        for r in engine_comparison.same_concurrency_delta(crdb, pg)["rows"]
        if r["concurrency"] == 1
    )
    a = steady_state.per_tier(crdb).set_index("concurrency")
    b = steady_state.per_tier(pg).set_index("concurrency")
    exact = float(a.loc[1, "mean_total_tps"]) / float(b.loc[1, "mean_total_tps"])
    assert row["throughput_ratio_x"] == round(exact, 2)
    # ...and specifically not the ratio of the displayed values, which differs
    # here in the second decimal.
    from_display = round(row["crdb_tps"] / row["pg_tps"], 2)
    assert from_display != row["throughput_ratio_x"]


def test_a_matched_utilisation_level_is_not_rounded_before_it_is_used(tmp_path):
    """Rounding a level and multiplying it back by the peak moves the point."""
    crdb = load_run(
        _write_run(
            tmp_path,
            "crdb_util",
            _rows([(1, 1365.813, 0.4, 341.453, 1.416),
                   (2, 2403.626, 0.5, 600.906, 1.485),
                   (50, 2850.668, 6.0, 712.667, 19.606)]),
            phase="bench_cluster",
        )
    )
    pg = load_run(
        _write_run(
            tmp_path,
            "pg_util",
            _rows([(1, 54.087, 0.7, 13.522, 71.325),
                   (2, 107.167, 0.74, 26.792, 71.351),
                   (50, 1479.638, 3.0, 369.910, 105.472)]),
            phase="bench_cluster",
        )
    )
    out = engine_comparison.matched_utilisation(crdb, pg)
    tiers = steady_state.per_tier(crdb).set_index("concurrency")
    lat = steady_state.latency_by_op(crdb)
    lat = lat[lat["op"] == "update"].set_index("concurrency")
    peak = float(tiers["mean_total_tps"].max())

    for concurrency in (1, 2):
        tps = float(tiers.loc[concurrency, "mean_total_tps"])
        level = round(tps / peak, 3)
        point = next(p for p in out["points"] if p["utilisation"] == level)
        # The point sits on the tier it came from, not near it.
        assert point["crdb_tps"] == round(tps, 1)
        assert point["crdb_latency_ms"] == round(
            float(lat.loc[concurrency, "p50_ms"]), 3
        )
    # The lowest level is the bottom of the comparable range, not a casualty of
    # rounding it below that bound.
    assert min(p["utilisation"] for p in out["points"]) == out["utilisation_range"][0]


# --- write_latency_recovery ---
# Throughput can recover while the write path stays permanently slower.


def test_write_latency_that_returns_to_baseline_is_reported_as_such(tmp_path):
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40, wall_offset=0.0)
    # A brief latency spike during failover, then back to the 40 ms baseline --
    # nothing structural, just the fault being noticed.
    for row in rows:
        if row["op"] == "update" and 10 < row["elapsed_s"] <= 14:
            row["p50_ms"] = 120.0
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(tmp_path, "chaos_latency_ok", rows, phase="p4_chaos", events=events),
        require_valid=False,
    )
    result = resilience.write_latency_recovery(run, resilience.align(run))
    assert result["available"] is True
    assert result["settled"] is True
    assert result["classification"] == "returned_to_baseline"
    assert result["ratio_to_baseline"] == pytest.approx(1.0, abs=0.05)


def test_a_permanent_write_latency_shift_is_reported_even_though_throughput_recovers(tmp_path):
    """The exact scenario a TPS-only view misses: quorum now needs a slower
    member, so the write path is permanently ~2x, but reads dominate throughput
    and it looks fully recovered on that axis alone."""
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40, wall_offset=0.0)
    for row in rows:
        if row["op"] == "update" and row["elapsed_s"] > 10:
            row["p50_ms"] = 80.0  # settled at 2x baseline for the rest of the run
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(tmp_path, "chaos_latency_shift", rows, phase="p4_chaos", events=events),
        require_valid=False,
    )
    perf = resilience.performance(run, resilience.align(run))
    result = resilience.write_latency_recovery(run, resilience.align(run))

    # Throughput was never touched in this fixture -- it looks fully recovered.
    assert perf["defined"] is True
    assert perf["rto_s"] == pytest.approx(0.0, abs=0.5)
    # The write path did not come back, and this axis says so on its own terms.
    assert result["settled"] is True
    assert result["classification"] == "structural_latency_shift"
    assert result["ratio_to_baseline"] == pytest.approx(2.0, abs=0.05)
    assert "not a contradiction" not in result["claim"]  # that line lives in cli.py
    assert "structural change" in result["claim"]


def test_write_latency_still_changing_is_reported_as_unsettled(tmp_path):
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=40, wall_offset=0.0)
    for row in rows:
        if row["op"] == "update" and row["elapsed_s"] > 10:
            # Alternates far above and below baseline: never settles.
            row["p50_ms"] = 400.0 if int(row["elapsed_s"]) % 2 == 0 else 40.0
    events = dict(_EVENTS, t_end_utc="2026-09-02T00:00:45.000000Z")
    run = load_run(
        _write_run(tmp_path, "chaos_latency_unsettled", rows, phase="p4_chaos", events=events),
        require_valid=False,
    )
    result = resilience.write_latency_recovery(run, resilience.align(run))
    assert result["settled"] is False
    assert result["classification"] == "unsettled_within_run"


def test_write_latency_recovery_reports_unavailable_without_a_fault(tmp_path):
    rows = _rows([(10, 800, 1.0, 200, 40.0)], ticks=12, wall_offset=0.0)
    run = load_run(
        _write_run(tmp_path, "bench_no_fault", rows, phase="bench_cluster", events=None)
    )
    result = resilience.write_latency_recovery(run, resilience.align(run))
    assert result["available"] is False


# --- quorum_geometry when the leaseholder is the target ---
# The gateway is the target, so survivors must be evaluated as candidate leaders.

_FIVE_NODES = Topology(
    nodes=(
        Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1",
             "cloud=gcp,region=us-east1", gateway=True),
        Node("linode-1", "crdb-linode-1", "root", "linode", "us-east",
             "cloud=linode,region=us-east"),
        Node("linode-2", "crdb-linode-2", "root", "linode", "us-west",
             "cloud=linode,region=us-west"),
        Node("azure-1", "crdb-azure-1", "ubuntu", "azure", "centralindia",
             "cloud=azure,region=centralindia"),
        Node("azure-2", "crdb-azure-2", "ubuntu", "azure", "eastasia",
             "cloud=azure,region=eastasia"),
    )
)


def _write_network_csv(tmp_path: Path, rtts: dict) -> Path:
    """A minimal, valid network.csv: symmetric RTTs, one row per direction."""
    rows = []
    for pair, ms in rtts.items():
        a, b = tuple(pair)
        for source, dest in ((a, b), (b, a)):
            rows.append(
                {
                    "ts_utc": "2026-09-08T00:00:00Z",
                    "source": source,
                    "destination": dest,
                    "source_region": "",
                    "destination_region": "",
                    "samples": 100,
                    "loss_pct": 0.0,
                    "rtt_min_ms": ms,
                    "rtt_mean_ms": ms,
                    "rtt_p50_ms": ms,
                    "rtt_p95_ms": ms,
                    "rtt_p99_ms": ms,
                    "rtt_max_ms": ms,
                    "rtt_mdev_ms": 0.1,
                    "rtt_resolution_ms": 0.1,
                }
            )
    path = tmp_path / "network.csv"
    pd.DataFrame(rows, columns=list(NETWORK_COLUMNS)).to_csv(path, index=False)
    return path


# Modelled on the testbed: a fast triangle (gcp-1, linode-1, linode-2) under 70 ms,
# the two Azure nodes 150-230 ms from everything.
_TESTBED_RTTS = {
    frozenset({"crdb-gcp-1", "crdb-linode-1"}): 24.9,
    frozenset({"crdb-gcp-1", "crdb-linode-2"}): 69.7,
    frozenset({"crdb-gcp-1", "crdb-azure-1"}): 210.1,
    frozenset({"crdb-gcp-1", "crdb-azure-2"}): 198.9,
    frozenset({"crdb-linode-1", "crdb-linode-2"}): 68.8,
    frozenset({"crdb-linode-1", "crdb-azure-1"}): 179.1,
    frozenset({"crdb-linode-1", "crdb-azure-2"}): 199.1,
    frozenset({"crdb-linode-2", "crdb-azure-1"}): 231.1,
    frozenset({"crdb-linode-2", "crdb-azure-2"}): 153.7,
    frozenset({"crdb-azure-1", "crdb-azure-2"}): 87.1,
}


def test_a_fault_on_a_follower_removes_it_from_the_leaders_row(tmp_path):
    """azure-1 dying while gcp-1 stays leaseholder: remove one entry from the
    gateway's own row. This is the case the function was first written for."""
    network_csv = _write_network_csv(tmp_path, _TESTBED_RTTS)
    events = dict(_EVENTS, target="azure-1")
    run = load_run(
        _write_run(
            tmp_path, "chaos_follower_fault",
            _rows([(10, 800, 1.0, 200, 40.0)]),
            phase="p4_chaos", events=events,
        )
    )
    geom = resilience.quorum_geometry(run, network_csv, _FIVE_NODES)
    assert geom["available"] is True
    assert geom["leaseholder_displaced"] is False
    # linode-1 (24.9) and linode-2 (69.7) are still there; losing azure-1 does
    # not touch the fast quorum at all.
    assert geom["surviving_quorum_floor_ms"] == pytest.approx(69.68, abs=0.05)
    assert geom["target_in_fast_quorum"] is False


def test_a_fault_on_the_leaseholder_is_reported_as_a_range_not_a_false_point(tmp_path):
    """With the leaseholder as target, every survivor is a candidate leader and a range is
    reported.
    """
    network_csv = _write_network_csv(tmp_path, _TESTBED_RTTS)
    events = dict(_EVENTS, target="gcp-1")
    run = load_run(
        _write_run(
            tmp_path, "chaos_leader_fault",
            _rows([(10, 800, 1.0, 200, 40.0)]),
            phase="p4_chaos", events=events,
        )
    )
    geom = resilience.quorum_geometry(run, network_csv, _FIVE_NODES)
    assert geom["available"] is True
    assert geom["leaseholder_displaced"] is True
    # It must NOT collapse to before == after -- that was the bug.
    lo, hi = geom["surviving_quorum_floor_range_ms"]
    assert lo > geom["quorum_floor_ms"] + 1.0
    assert hi > lo
    # linode-2 and azure-2 (153.7ms to each other) is the best surviving pair;
    # linode-1 as leader must reach into Azure at 179ms, the worst case.
    assert geom["best_case_leader"] == "linode-2"
    assert geom["worst_case_leader"] == "linode-1"
    assert geom["target_in_fast_quorum"] is True
    assert "displaces it" in geom["detail"]


def test_the_consequence_text_is_engine_aware_about_the_read_path(tmp_path):
    """Reads are unaffected on CockroachDB but follow the primary on PostgreSQL."""
    network_csv = _write_network_csv(tmp_path, _TESTBED_RTTS)
    events = dict(_EVENTS, target="gcp-1")

    def _geom(name, manifest_extra):
        run = load_run(
            _write_run(
                tmp_path, name,
                _rows([(10, 800, 1.0, 200, 40.0)]),
                phase="p4_chaos", events=events, manifest_extra=manifest_extra,
            )
        )
        return resilience.quorum_geometry(run, network_csv, _FIVE_NODES)

    pg = _geom("chaos_pg_consequence", {"engine": "postgresql"})
    assert "read" in pg["consequence"].lower()
    assert "client-to-primary distance" in pg["consequence"]
    assert "unaffected" not in pg["consequence"], (
        "the PostgreSQL read path is the opposite of unaffected when the "
        "pinned primary is the fault target"
    )
    # Vocabulary: a Patroni cluster has no leaseholder and no CockroachDB
    # allocator, and this text is printed verbatim under the figure.
    assert "leaseholder" not in pg["detail"]
    assert "CockroachDB" not in pg["detail"]
    assert "primary" in pg["detail"]
    assert "Patroni promotes" in pg["detail"]

    crdb = _geom("chaos_crdb_consequence", {"engine": "cockroachdb"})
    assert "unaffected by which candidate takes the lease" in crdb["consequence"]
    assert "leaseholder" in crdb["detail"]

    # The geometry is engine-neutral: 3-of-5 Raft and Patroni's `ANY 2` alike.
    assert pg["surviving_quorum_floor_range_ms"] == crdb["surviving_quorum_floor_range_ms"]
    assert pg["candidate_floors_ms"] == crdb["candidate_floors_ms"]


def test_the_displaced_case_evaluates_every_survivor_as_a_candidate(tmp_path):
    network_csv = _write_network_csv(tmp_path, _TESTBED_RTTS)
    events = dict(_EVENTS, target="gcp-1")
    run = load_run(
        _write_run(
            tmp_path, "chaos_candidates",
            _rows([(10, 800, 1.0, 200, 40.0)]),
            phase="p4_chaos", events=events,
        )
    )
    geom = resilience.quorum_geometry(run, network_csv, _FIVE_NODES)
    assert set(geom["candidate_floors_ms"]) == {"linode-1", "linode-2", "azure-1", "azure-2"}
    assert "gcp-1" not in geom["candidate_floors_ms"]

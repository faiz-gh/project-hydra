"""Tests for the insights catalogue.

The unit tests pin the report conventions a reader relies on: how a number is
printed, how nested stats flatten into ``summary.csv``, how a chart's filename
carries its provenance, and that a chart with nothing to draw is *skipped with a
reason* rather than silently missing. The end-to-end test renders the committed
smoke runs and checks the numbers against values the 2026-09-23 render of the
same runs recorded, so a change that moves a figure is caught here rather than
in a viva.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from crdblab.config import DEFAULT_RUNS_DIR
from crdblab.insights import generate
from crdblab.insights._base import REGISTRY, Chart
from crdblab.insights.data import Inventory
from crdblab.insights.report import flatten, fmt

SMOKE_RUNS = (
    "20260923T203655Z_p1-network",
    "20260923T203817Z_bench_cluster",
    "20260923T204006Z_p4-chaos-recover",
    "20260923T204359Z_p4-chaos-dead",
    "20260923T210315Z_p1-network",
    "20260923T210425Z_bench_cluster",
    "20260923T210603Z_p4-chaos-recover",
    "20260923T210911Z_p4-chaos-dead",
)


# -- formatting --------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "text"),
    [
        (2930.9, "2,931"),
        (79.7, "79.7"),
        (6.891528, "6.892"),
        (0.2453, "0.2453"),
        (1.0, "1"),
        (0.0, "0"),
        (13.899999999999999, "13.9"),
        (33774, "33774"),  # a count is printed exactly, never rounded
        (True, "True"),
        ("gcp-1", "gcp-1"),
    ],
)
def test_a_stat_is_printed_to_four_significant_figures(value, text):
    assert fmt(value) == text


def test_nested_stats_print_as_pairs_and_series_as_a_length():
    assert fmt({"ok": 620, "conn_error": 1}) == "ok=620, conn_error=1"
    assert fmt([{"a": 1}, {"a": 2}]) == "2 entries"


def test_summary_csv_flattens_with_dots_and_indices():
    rows = flatten({"points": [{"tps": 1.0}], "x": {"y": 2}})
    assert rows == [("points[0].tps", 1.0), ("x.y", 2)]


# -- the catalogue -----------------------------------------------------------

def test_the_catalogue_is_thirty_one_charts_in_five_groups():
    ids = [c.id for c in REGISTRY]
    assert len(ids) == 31 and len(set(ids)) == 31
    assert [i for i in ids if i[0] == "A"] == [f"A{n}" for n in range(1, 8)]
    assert [i for i in ids if i[0] == "C"] == [f"C{n}" for n in range(1, 9)]
    assert {i[0] for i in ids} == set("ABCDE")


def test_a_chart_filename_stem_is_its_id_and_title():
    chart = Chart("B3", "Disk I/O and busy time", "", lambda ctx: None)
    assert chart.stem == "b3_disk_i_o_and_busy_time"


def test_an_empty_runs_directory_skips_every_chart_with_a_reason(tmp_path):
    results, inventory = generate(tmp_path / "out", tmp_path / "runs")
    assert inventory.entries == []
    assert not any(r.drawn for r in results)
    assert all(r.skipped for r in results), "a skip must always say why"
    status = list(csv.DictReader(open(tmp_path / "out" / "chart_status.csv")))
    assert {row["status"] for row in status} == {"skipped"}
    report = (tmp_path / "out" / "insights.md").read_text()
    assert "## Skipped charts" in report


def test_a_run_that_fails_the_gate_is_refused_not_charted(tmp_path):
    runs = tmp_path / "runs"
    broken = runs / "20260101T000000Z_bench_cluster"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(json.dumps({"engine": "cockroachdb"}))
    inventory = Inventory.scan(runs)
    assert len(inventory.entries) == 1
    entry = inventory.entries[0]
    assert not entry.passing and "metrics.csv" in entry.refused
    assert inventory.latest("bench") is None


# -- end to end against committed runs ---------------------------------------

@pytest.fixture(scope="module")
def smoke_render(tmp_path_factory):
    if not all((DEFAULT_RUNS_DIR / r).is_dir() for r in SMOKE_RUNS):
        pytest.skip("the committed 2026-09-23 smoke runs are not present")
    out = tmp_path_factory.mktemp("insights") / "render"
    results, inventory = generate(out, DEFAULT_RUNS_DIR, profile="smoke")
    return out, {r.chart.id: r for r in results}, inventory


def test_every_chart_draws_from_the_smoke_runs(smoke_render):
    out, results, inventory = smoke_render
    assert [e.run_id for e in inventory.entries] == list(SMOKE_RUNS)
    skipped = {cid: r.skipped for cid, r in results.items() if not r.drawn}
    assert skipped == {}
    for result in results.values():
        for name in result.files:
            assert (out / name).exists()
    for name in ("insights.md", "dashboard.html", "summary.json", "summary.csv",
                 "chart_status.csv"):
        assert (out / name).exists()


def test_filenames_carry_engine_profile_and_runs(smoke_render):
    _, results, _ = smoke_render
    assert results["A1"].files[0] == (
        "a1_throughput_latency_curve_mixed-engine_smoke_"
        "20260923T203817Z_bench_cluster_20260923T210425Z_bench_cluster.png"
    )
    # Chaos charts name every run they drew, engine then fault class.
    assert results["C3"].files[0].endswith(
        "_mixed-engine_smoke_20260923T204359Z_p4-chaos-dead_20260923T204006Z_p4-chaos-recover"
        "_20260923T210911Z_p4-chaos-dead_20260923T210603Z_p4-chaos-recover.png"
    )
    assert results["E3"].files[0] == "e3_run_provenance_mixed-engine_mixed-profile.png"


def test_numbers_match_the_recorded_smoke_render(smoke_render):
    """Values the 2026-09-23 render of these same runs recorded."""
    _, results, _ = smoke_render
    assert results["A1"].stats["cockroachdb_peak_tps"] == 2930.9
    assert results["A1"].stats["postgresql_knee_concurrency"] == 50
    assert results["A6"].stats["postgresql_tps_under_p99_100ms"] == 616.1
    assert results["B7"].stats["cockroachdb_busiest_node"] == "gcp-1"
    assert results["C1"].stats["cockroachdb_dead_attempts"] == 622
    assert results["C3"].stats["CockroachDB_dead"] == {"audit_s": 11.391, "probe_s": 7.098761}
    assert "postgresql_dead" not in results["C5"].stats  # throughput never resumed
    assert results["D5"].stats["quorum_floor_ms"] == 75.119
    assert results["E1"].stats["derived_floor_ms"] == 74.905
    assert results["E2"].stats["worst_link"] == "crdb-linode-1 -> crdb-azure-2"

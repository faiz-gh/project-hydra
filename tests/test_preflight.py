"""Tests for the pre-flight assertions.

``RowMatchProbe`` is the only detector this project has for a workload that
completes without touching data (D8). Its failure modes 
matter as much as its success path -- a probe that
cries wolf gets disabled, and a probe that passes silently on reset counters is
worse than none.
"""

from __future__ import annotations

from crdblab.analysis.validation import host_hardware
from crdblab.core.preflight import (
    PreflightReport,
    RowMatchProbe,
    format_hardware,
    parse_hardware,
)
from crdblab.topology import CLIENT_NODE


def _probe(before, after):
    """A probe with its two samples stubbed, so no cluster is required."""
    probe = RowMatchProbe(CLIENT_NODE, "usertable")
    probe._before = before
    probe._sample = lambda: after  # type: ignore[method-assign]
    return probe


def test_a_clean_window_reports_the_differenced_rate():
    report = PreflightReport()
    # 1000 -> 3000 executions, all matching.
    rate = _probe((1000.0, 1000.0), (3000.0, 3000.0)).finish(report)
    assert rate == 1.0
    assert report.ok
    assert report.checks[-1].observed["window"] == "interval"


def test_a_seed_mismatch_still_fails_on_a_clean_window():
    """D8's signature: statements execute, no rows are touched."""
    report = PreflightReport()
    rate = _probe((0.0, 0.0), (50_000.0, 0.0)).finish(report)
    assert rate == 0.0
    assert not report.ok


def test_a_mid_tier_statistics_flush_does_not_read_as_no_work():
    """CockroachDB flushes in-memory statement statistics every 10 minutes.

    A tier straddling that boundary differences a large "before" against a small
    "after" and gets a non-positive delta. Observed on the 2026-09-02 thesis
    sweep: eleven of twelve tiers matched at 1.0000 and the twelfth reported no
    statements while sustaining 2,431 ops/s, which aborted the sweep before
    Phase III ever started.

    The absolute counters survive and are attributable: a reset detected here
    happened after ``start()``, so everything since belongs to this tier. The
    window narrows; the assertion does not relax.
    """
    report = PreflightReport()
    rate = _probe((238_710.0, 238_710.0), (9_000.0, 9_000.0)).finish(report)
    assert rate == 1.0
    assert report.ok
    assert report.checks[-1].observed["window"] == "post-flush partial"
    assert "partial window" in report.checks[-1].detail


def test_the_flush_fallback_still_catches_a_seed_mismatch():
    """The narrowed window must not become an escape hatch for D8."""
    report = PreflightReport()
    rate = _probe((238_710.0, 238_710.0), (9_000.0, 0.0)).finish(report)
    assert rate == 0.0
    assert not report.ok


def test_a_vanished_counter_fails_whatever_it_is_called():
    """Counters that stood at 1000 and now read 0 fail, uncorroborated.

    This case was originally read as an idle workload and asserted the message
    "may not have run at all". That reading was wrong: a non-zero ``c0`` means
    statements *were* recorded, so the view was flushed rather than the workload
    being idle, and the tier that provoked it in the field had just sustained
    611.7 ops/s. The verdict is unchanged -- absent corroboration there is
    nothing to assert on -- but the stated reason now matches the evidence.
    Genuine idleness is ``c0 == c1 == 0``, covered separately below.
    """
    report = PreflightReport()
    rate = _probe((1000.0, 1000.0), (0.0, 0.0)).finish(report)
    assert rate == 0.0
    assert not report.ok
    assert "flushed after this tier ended" in report.checks[-1].detail


# --- hardware capture ------------------------------------------------------
#
# The hardware baseline fell 22% across the redeployment of 2026-09-02 with every
# recorded field identical, because nothing recorded the machine. These pin the
# parse so that the *next* such shift is answerable from the artefact.

def test_the_hardware_block_is_parsed_into_its_three_fields():
    parsed = parse_hardware(
        "\n2\n Intel(R) Xeon(R) CPU @ 2.80GHz\nMemTotal:        4007012 kB\n"
    )
    assert parsed == {
        "cpus": 2,
        "cpu_model": "Intel(R) Xeon(R) CPU @ 2.80GHz",
        "mem_total_kb": 4007012,
    }


def test_a_missing_field_is_recorded_as_unknown_never_as_a_default():
    """A defaulted CPU count would compare equal to a real reading and so make
    two unlike machines look alike -- the D5 failure, not a lesser one."""
    parsed = parse_hardware("2\n")
    assert parsed["cpus"] == 2
    assert parsed["cpu_model"] is None
    assert parsed["mem_total_kb"] is None


def test_the_note_round_trips_through_the_manifest():
    hardware = parse_hardware("2\nIntel(R) Xeon(R) CPU @ 2.80GHz\nMemTotal: 4007012 kB")
    note = f"2026-09-03T00:00:00Z host: {format_hardware(hardware)}"
    assert host_hardware({"notes": [note]}) == hardware


# --- the post-tier flush race ----------------------------------------------
#
# A flush landing *during* a tier is recovered by the partial-window fallback.
# A flush landing after the tier's workload has stopped cannot be: there is no
# evidence left. Observed 2026-09-03, where twenty of twenty-one Phase II tiers
# matched at >= 0.9999 and the twenty-first reported nothing while having just
# sustained 611.7 ops/s.

def test_a_post_tier_flush_is_not_reported_as_the_workload_never_running():
    """The old message said "the workload may not have run at all" about a tier
    that had just sustained 611.7 ops/s for 55 intervals. That is a wrong fact,
    not merely an unhelpful one."""
    probe = _probe((5000.0, 5000.0), (0.0, 0.0))
    report = PreflightReport()
    probe.finish(report, corroborated=False)
    check = report.checks[-1]
    assert check.passed is False
    assert "flushed after this tier ended" in check.detail
    assert "may not have run at all" not in check.detail


def test_a_post_tier_flush_is_survivable_when_the_quorum_floor_corroborates():
    probe = _probe((5000.0, 5000.0), (0.0, 0.0))
    report = PreflightReport()
    probe.finish(report, corroborated=True)
    check = report.checks[-1]
    assert check.passed is True
    assert check.observed["window"] == "flushed; corroborated by quorum floor"
    assert "Reads are not independently corroborated" in check.detail


def test_corroboration_never_rescues_a_measured_seed_mismatch():
    """Corroboration applies only where there is no evidence. A window that did
    produce a measurement is asserted on, whatever the floor check said -- an
    escape hatch here would disable D8's only detector."""
    probe = _probe((0.0, 0.0), (10000.0, 0.0))
    report = PreflightReport()
    probe.finish(report, corroborated=True)
    assert report.checks[-1].passed is False


def test_a_run_with_no_statements_at_all_still_fails_outright():
    """c0 == 0 and c1 == 0 is genuinely no work, not a flush, and keeps the
    original message."""
    probe = _probe((0.0, 0.0), (0.0, 0.0))
    report = PreflightReport()
    probe.finish(report, corroborated=True)
    check = report.checks[-1]
    assert check.passed is False
    assert "may not have run at all" in check.detail


def test_an_unmeasured_rate_is_null_in_the_manifest_not_zero_or_nan():
    import json
    probe = _probe((5000.0, 5000.0), (0.0, 0.0))
    rate = probe.finish(PreflightReport(), corroborated=True)
    assert rate is None
    assert json.loads(json.dumps({"row_match_rate": rate})) == {"row_match_rate": None}

"""Tests for Phase IV recovery detection.

``find_recovery`` decides the single number Phase IV exists to produce, and the
legacy implementation of it did not measure recovery at all: its guard clause
prevented a recovery being declared sooner than ten of its own (double-speed)
seconds, so the reported RTOs of 6.0 s and 5.2 s are the guard. These tests pin
the corrected semantics.
"""

from __future__ import annotations

import pytest

from crdblab.phases.p4_chaos import find_recovery

BASELINE = 1000.0
THRESHOLD = 0.8   # floor of 800 tps
HOLD = 5.0


def _series(values, start=0.0, step=1.0):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_rto_is_the_start_of_the_sustained_window_not_its_end():
    """The hold qualifies the recovery; it must not postpone the timestamp.

    Throughput collapses at the fault (t=10) and first reaches the 800 tps floor
    at t=14, holding from there. The correct answer is 14, an RTO of 4 s.
    Returning the end of the five-second hold would report 19 and an RTO of 9 s
    -- more than double, and an artefact of the measurement rather than a
    property of the system.
    """
    series = _series([1000] * 10 + [0, 100, 300, 700, 950, 980, 1000, 1000, 1000, 1000, 1000])
    assert find_recovery(series, 10.0, BASELINE, THRESHOLD, HOLD) == pytest.approx(14.0)


def test_a_transient_spike_does_not_count_as_recovery():
    """One good sample inside a degraded period must not end the outage."""
    series = _series([1000] * 10 + [0, 0, 900, 0, 0, 0, 0, 0, 0, 0, 0])
    assert find_recovery(series, 10.0, BASELINE, THRESHOLD, HOLD) is None


def test_no_recovery_is_reported_when_throughput_never_returns():
    series = _series([1000] * 10 + [0] * 15)
    assert find_recovery(series, 10.0, BASELINE, THRESHOLD, HOLD) is None


def test_recovery_is_not_declared_without_enough_samples_to_establish_the_hold():
    """A run that ends mid-window must report no recovery rather than guess.

    Throughput is above the floor for the final two samples, but the hold is five
    seconds and the run stops before that can be established. Reporting a
    recovery here would be an assertion the data does not support.
    """
    series = _series([1000] * 10 + [0, 0, 0, 1000, 1000])
    assert find_recovery(series, 10.0, BASELINE, THRESHOLD, HOLD) is None


def test_recovery_exactly_at_the_threshold_qualifies():
    """The floor is inclusive: 'at or above' the threshold."""
    series = _series([1000] * 5 + [800.0] * 8)
    assert find_recovery(series, 5.0, BASELINE, THRESHOLD, HOLD) == pytest.approx(5.0)


def test_degradation_below_the_floor_by_a_hair_does_not_qualify():
    series = _series([1000] * 5 + [799.9] * 8)
    assert find_recovery(series, 5.0, BASELINE, THRESHOLD, HOLD) is None


def test_samples_before_the_fault_are_ignored():
    """Pre-fault throughput trivially exceeds the floor and must not be matched."""
    series = _series([1000] * 25)
    # Fault at t=15; recovery can only be found at or after that point, and the
    # series must extend far enough past it to establish the hold.
    assert find_recovery(series, 15.0, BASELINE, THRESHOLD, HOLD) == pytest.approx(15.0)


# --- availability RTO ------------------------------------------------------

from crdblab.phases.p4_chaos import availability_rto


def _attempts(pairs):
    return [(t, i + 1, outcome) for i, (t, outcome) in enumerate(pairs)]


def test_availability_rto_is_the_first_acknowledged_write_after_the_fault():
    """The question is when the database accepted a write again, not when
    throughput recovered. Fault at t=10; writes fail until 12.5."""
    a = _attempts(
        [(9.0, "ack"), (9.5, "ack"), (10.2, "ambiguous"), (11.0, "refused"),
         (12.5, "ack"), (13.0, "ack")]
    )
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["availability_rto_s"] == pytest.approx(2.5)
    # The observed outage is longer than the RTO: the last success predates the
    # fault by up to one audit cadence, and conflating the two overstates it.
    assert r["write_gap_s"] == pytest.approx(3.0)


def test_availability_rto_is_zero_when_writes_never_stopped():
    """A fault on a node that is not in the write path interrupts nothing."""
    a = _attempts([(9.0, "ack"), (10.1, "ack"), (11.0, "ack")])
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["availability_rto_s"] == pytest.approx(0.1)


def test_availability_rto_is_none_when_writes_never_resume():
    a = _attempts([(9.0, "ack"), (10.5, "refused"), (11.0, "refused")])
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["availability_rto_s"] is None
    assert r["writes_acknowledged_after_fault"] == 0


def test_resolution_is_reported_so_the_figure_can_be_qualified():
    """An RTO below the audit cadence is indistinguishable from no outage."""
    a = _attempts([(9.0, "ack"), (9.5, "ack"), (10.0, "ack"), (10.5, "ack")])
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["resolution_s"] == pytest.approx(0.5)

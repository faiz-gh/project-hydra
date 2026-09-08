"""Regression tests for the defects identified in the legacy measurement pipeline.

Each test corresponds to a specific corruption observed in the Phase II/III CSVs
or found while re-provisioning the testbed, so a future refactor cannot silently
reintroduce it.

Provenance of the fixtures
--------------------------
Both fixtures are verbatim captures taken from the provisioned testbed on
2026-09-02: CockroachDB v26.3.0, generator executed on the gateway node
(``linode-1``, before the gateway moved to ``gcp-1``) against a single-host
connection string, 15 s at concurrency 10,
``ycsb`` against a 125,000-row (~205 MB) working set loaded under seed 42.
``ycsb_multi_op.txt`` is the CUSTOM 80/20 read/update mix; ``ycsb_single_op.txt``
is workload C, read-only.

Three properties of these captures are load-bearing and were each established by
measurement rather than assumption:

* The configuration matches rows. Measured by differencing
  ``crdb_internal.statement_statistics`` across the run, the capture
  configuration returned 9,851 rows for 9,851 operations, a match rate of 1.0000.
  Under the generator's default per-invocation seed the same configuration
  returns 0.0000 while appearing to run normally (D8).
* Update latency is quorum-bound. Updates show a p50 of 75.5 ms against an
  independently measured 70.6 ms round trip to the second-fastest follower, which
  is the floor Raft quorum imposes. The pre-D8 figure of 3.1 ms was below that
  floor and therefore not physically realisable.
* All four latency quantiles are distinct in the first sample of both fixtures,
  so every quantile assertion below discriminates a positional mis-binding rather
  than coinciding with the correct value. The earlier kv captures could not do
  this: at ~35 ops/s the histogram collapsed p95, p99 and pMax into one bucket.

The superseded kv fixtures have been removed. Their block shapes are identical to
these, so they contributed no parser coverage, and their values were recorded
under configurations later shown to be defective.
"""

from __future__ import annotations

import pathlib

import pytest

from crdblab.core.workload import (
    PERIODIC,
    SUMMARY,
    WorkloadParseError,
    WorkloadParser,
    group_ticks,
    group_timed_ticks,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "workload"


def _samples(name: str):
    text = (FIXTURES / name).read_text().splitlines()
    return list(WorkloadParser().parse_stream(text))


# --- Defect 1: op-type lines treated as independent samples ----------------

def test_op_type_label_is_preserved():
    samples = [s for s in _samples("ycsb_multi_op.txt") if s.kind == PERIODIC]
    assert {s.op for s in samples} == {"read", "update"}


def test_throughput_is_summed_across_op_types_not_averaged():
    ticks = list(group_ticks(_samples("ycsb_multi_op.txt")))
    first = ticks[0]
    assert first.total_tps == pytest.approx(508.6 + 104.5)
    # The legacy behaviour recorded the mean of the two lines, halving the
    # reported load; assert explicitly that we do not do that.
    assert first.total_tps != pytest.approx((508.6 + 104.5) / 2)


def test_latency_distributions_are_not_pooled():
    """Reads are leaseholder-local; updates pay cross-region quorum.

    The two differ by a factor of roughly eighty in this capture, which is the
    clearest possible demonstration that pooling them yields a quantile of
    nothing. It is also the substantive result the topology exists to produce.
    """
    first = list(group_ticks(_samples("ycsb_multi_op.txt")))[0]
    assert first.latency_ms("read", "p50") == pytest.approx(0.9)
    assert first.latency_ms("update", "p50") == pytest.approx(75.5)


# --- Defect 2: index shift caused by the unheaded trailing column ----------

def test_quantiles_bind_by_header_name_not_position():
    samples = [s for s in _samples("ycsb_multi_op.txt") if s.kind == PERIODIC]
    read = next(s for s in samples if s.op == "read")
    # Positionally, fields[5] is p95 and fields[7] is pMax; the legacy parser
    # recorded those as p50 and p99. All four values are distinct here, so each
    # assertion independently discriminates.
    assert read.latency_ms("p50") == pytest.approx(0.9)
    assert read.latency_ms("p95") == pytest.approx(3.9)
    assert read.latency_ms("p99") == pytest.approx(5.8)
    assert read.latency_ms("pmax") == pytest.approx(7.6)
    assert len({read.latency_ms(q) for q in ("p50", "p95", "p99", "pmax")}) == 4


def test_single_op_output_is_still_labelled():
    """A read-only run labels its lines; it does not fall back to an 8-field row.

    A superseded, hand-written fixture asserted that a workload reporting one
    operation type emits an unheaded row parsed as ``op == "all"``. Capture shows
    v26.3.0 does no such thing under either generator: every periodic line still
    carries a trailing label. That expectation was an artefact of the fixture
    having been written rather than observed.
    """
    samples = [s for s in _samples("ycsb_single_op.txt") if s.kind == PERIODIC]
    assert {s.op for s in samples} == {"read"}
    assert samples[0].tps == pytest.approx(2557.0)


# --- Defect 3: cumulative summary block admitted as a one-second sample ----

def test_summary_block_is_classified_not_measured():
    samples = _samples("ycsb_multi_op.txt")
    summaries = [s for s in samples if s.kind == SUMMARY]
    assert summaries, "summary block should be parsed, not dropped silently"
    assert all(s.kind == PERIODIC for s in samples if s.values.get("ops_total") is None)


def test_summary_rows_never_reach_the_tick_stream():
    ticks = list(group_ticks(_samples("ycsb_multi_op.txt")))
    assert len(ticks) == 15
    assert max(t.total_tps for t in ticks) < 10_000


def test_summary_totals_cross_check_the_periodic_stream():
    samples = _samples("ycsb_multi_op.txt")
    summary_total = sum(
        s.values["ops_total"] for s in samples if s.kind == SUMMARY and s.op in {"read", "update"}
    )
    periodic_total = sum(t.total_tps for t in group_ticks(samples))
    # Sums agree to within one interval's rounding; a wider gap indicates
    # dropped lines in the SSH pipe.
    assert abs(summary_total - periodic_total) / summary_total < 0.02


# --- Defect 6: summary header declares the op column, periodic header does not

def test_summary_header_op_column_is_not_bound_as_a_measurement():
    """The cumulative header ends ``__total``; the periodic header does not.

    The periodic header stops at ``pMax(ms)`` and leaves the op label unheaded
    (8 columns, 9 fields), whereas the cumulative header declares a tenth column
    named ``total`` and its row also has ten fields. Binding by width alone
    therefore zipped the header token ``total`` onto the value ``read`` and
    attempted ``float("read")``. The trailing non-metric header token must be
    recognised as the op-type column.
    """
    samples = [s for s in _samples("ycsb_multi_op.txt") if s.kind == SUMMARY]
    labelled = {s.op: s for s in samples}
    assert "read" in labelled and "update" in labelled
    assert labelled["read"].values["ops_total"] == pytest.approx(7758.0)
    assert labelled["update"].values["ops_total"] == pytest.approx(1954.0)
    # "total" named a label column, not a quantity, and must not survive as one.
    assert all("total" not in s.values for s in samples)


def test_result_block_is_the_cross_op_aggregate():
    """The final ``__result`` block carries a blank op field and totals both ops."""
    samples = [s for s in _samples("ycsb_multi_op.txt") if s.kind == SUMMARY]
    combined = [s for s in samples if s.op == "all"]
    assert len(combined) == 1
    per_op = sum(s.values["ops_total"] for s in samples if s.op in {"read", "update"})
    assert combined[0].values["ops_total"] == pytest.approx(per_op)


# --- Defect 8: operations that complete without touching data --------------

def test_update_latency_is_above_the_quorum_floor():
    """A committed write cannot be faster than the follower that completes quorum.

    Measured RTTs from the gateway (crdb-gcp-1, us-east1) are 23.7 ms (linode
    us-east), 68.8 ms (linode us-west) and 197/218 ms (azure). Quorum over five
    voters needs the leader plus two followers, so no committed write can beat
    ~69 ms. Under a mismatched
    generator seed, updates matched zero rows and returned in 3.1 ms -- below the
    floor, and therefore a detectable impossibility rather than a good result.
    This test pins that reasoning to the fixture so a future capture taken under a
    silently broken configuration fails here.
    """
    ticks = list(group_ticks(_samples("ycsb_multi_op.txt")))
    # The fixture was captured with crdb-linode-1 as the gateway, whose floor was
    # 70.6 ms; the gateway is now crdb-gcp-1 at 68.8 ms. The lower of the two is
    # used, because this test asserts an impossibility and must not manufacture
    # one: a capture taken from either vantage point has to clear the floor that
    # actually applied to it.
    quorum_floor_ms = 68.8
    observed = [t.latency_ms("update", "p50") for t in ticks]
    assert min(observed) >= quorum_floor_ms * 0.9, (
        f"update p50 {min(observed)} ms is below the {quorum_floor_ms} ms quorum "
        "floor; the writes are probably matching no rows (D8)"
    )


# --- Structural guarantees -------------------------------------------------

def test_data_before_header_is_refused_in_strict_mode():
    parser = WorkloadParser(strict=True)
    with pytest.raises(WorkloadParseError):
        parser.feed("    1.0s        0          508.6          508.6      0.9      3.9      5.8      7.6 read")


def test_unexpected_field_count_raises_rather_than_guessing():
    parser = WorkloadParser()
    parser.feed("_elapsed___errors__ops/sec(inst)___ops/sec(cum)__p50(ms)__p95(ms)__p99(ms)_pMax(ms)")
    with pytest.raises(WorkloadParseError):
        parser.feed("    1.0s        0          508.6      0.9 read")


def test_non_numeric_token_names_the_offending_column():
    """A future layout change must fail legibly, not as a bare ValueError."""
    parser = WorkloadParser()
    parser.feed("_elapsed___errors__ops/sec(inst)___ops/sec(cum)__p50(ms)__p95(ms)__p99(ms)_pMax(ms)")
    with pytest.raises(WorkloadParseError, match="p95_ms"):
        parser.feed("    1.0s        0          508.6          508.6      0.9     oops      5.8      7.6 read")


def test_timed_ticks_carry_the_arrival_of_their_first_line():
    """The harness's clock is attached to each interval, not inferred from it.

    Both phases record ``wall_offset_s`` from this, and the resilience analysis
    uses it to place a fault time and a throughput series on one axis. Before it
    existed the two lived on clocks whose origins differed by the SSH and process
    startup cost -- about 5.4 s -- and a figure drawn from both displaced the
    fault by an interval nobody had measured.
    """
    samples = _samples("ycsb_multi_op.txt")
    arrivals = [(100.0 + index * 0.1, sample) for index, sample in enumerate(samples)]

    timed = list(group_timed_ticks(arrivals))
    plain = list(group_ticks(samples))

    # Grouping is the shared implementation: the ticks must be identical.
    assert [t.elapsed_s for _, t in timed] == [t.elapsed_s for t in plain]
    assert [t.total_tps for _, t in timed] == [t.total_tps for t in plain]

    # Arrival is the first line of the interval, and increases monotonically.
    arrival_times = [at for at, _ in timed]
    assert arrival_times == sorted(arrival_times)
    first_line_of_first_tick = arrivals[0][0]
    assert arrival_times[0] == first_line_of_first_tick


def test_timed_ticks_discard_summary_blocks_like_group_ticks_does():
    """A cumulative total is not an interval, whichever grouper sees it (D3)."""
    samples = _samples("ycsb_multi_op.txt")
    assert any(s.kind == SUMMARY for s in samples)
    timed = list(group_timed_ticks((0.0, s) for s in samples))
    assert all(
        all(inner.kind == PERIODIC for inner in tick.by_op.values()) for _, tick in timed
    )

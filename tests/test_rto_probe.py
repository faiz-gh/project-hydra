"""Tests for the high-frequency RTO probe.

Each test pins a decision the probe makes about what its observations do and do
not license, rather than a number it produces. A probe that reports a plausible
recovery time it cannot actually resolve is the same failure as this project's
recorded instrumentation defects: output that looks right and is not checkable.

No database is touched. ``_FakeProbe`` replaces the connection with a scripted
one whose latency and failure window are set by the test, which is what makes the
timing behaviour assertable at all -- against a live cluster the quantities under
test here are exactly the ones that vary.
"""

from __future__ import annotations

import json
import threading
import time

import pandas as pd
import pytest

from crdblab.analysis.validation import validate_probe
from crdblab.core.recorder import PROBE_COLUMNS, PROBE_OUTCOMES
from crdblab.core.rto_probe import (
    ProbeAttempt,
    RtoProbe,
    attempts_from_rows,
    classify,
    measure_rto,
    outage_windows,
    summarise,
)


# --- fixtures --------------------------------------------------------------


class _FakeConnectionError(Exception):
    """Named so :func:`classify` sees "Connection" in the type name, as psycopg's
    OperationalError subclasses do."""


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *_args, **_kwargs):
        self._conn.execute()


class _FakeConnection:
    """A connection whose write cost and outage window the test dictates."""

    def __init__(self, probe):
        self._probe = probe
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def execute(self):
        time.sleep(self._probe.write_cost_s)
        if self._probe.down_until is not None and time.monotonic() < self._probe.down_until:
            raise _FakeConnectionError("connection reset by peer")

    def close(self):
        self.closed = True


class _FakeProbe(RtoProbe):
    """The real probe with the driver replaced.

    Only :meth:`_connect` is overridden, so the dispatcher, the worker pool, the
    backpressure permit, the classification and the log are all the production
    ones. Substituting more than the connection would mean testing a different
    program from the one that runs.
    """

    def __init__(self, *args, write_cost_s=0.01, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_cost_s = write_cost_s
        self.down_until: float | None = None
        self.connects = 0
        self._connect_lock = threading.Lock()

    def _connect(self, worker):
        with self._connect_lock:
            self.connects += 1
        if self.down_until is not None and time.monotonic() < self.down_until:
            raise _FakeConnectionError("could not connect")
        return _FakeConnection(self)


def _attempts(spec):
    """Build attempts from ``(dispatch, complete, outcome)`` triples."""
    return [
        ProbeAttempt(
            seq_id=index + 1,
            dispatch_offset_s=dispatch,
            complete_offset_s=complete,
            outcome=outcome,
            worker=0,
        )
        for index, (dispatch, complete, outcome) in enumerate(spec)
    ]


# --- what the probe records ------------------------------------------------


def test_the_probe_writes_continuously_on_a_background_thread(tmp_path):
    """The workload's path is a thread that does not exist here.

    The caller starts the probe and does nothing; observations accumulate anyway.
    That is the whole of "decoupled from the generator" as a testable property.
    """
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.002,
        workers=4,
        write_cost_s=0.01,
        log_path=tmp_path / "rto_probe.log",
    )
    with probe:
        time.sleep(0.5)
    assert probe.error is None
    assert len(probe.attempts) > 20, "the probe recorded almost nothing in half a second"
    assert all(a.served for a in probe.attempts)
    # Every attempt takes a fresh number; a repeat would double-count an
    # observation, which is the livelock the RPO writer was rewritten to avoid.
    assert len({a.seq_id for a in probe.attempts}) == len(probe.attempts)


def test_concurrency_is_what_makes_the_sampling_finer_than_the_write_cost(tmp_path):
    """A serial client cannot observe a 10 ms write more often than every 10 ms.

    This is the probe's entire reason to exist: the RPO audit writer is serial, so
    its resolution is pinned at the cost of one quorum write no matter what its
    configured interval says. Four workers must beat one measurably, or the
    concurrency is decoration.
    """
    def resolution(workers):
        probe = _FakeProbe(
            "postgresql://example/bench",
            interval_s=0.001,
            workers=workers,
            write_cost_s=0.02,
            log_path=tmp_path / f"probe_{workers}.log",
        )
        with probe:
            time.sleep(0.6)
        return probe.summary()["resolution_s"]

    serial = resolution(1)
    parallel = resolution(4)
    assert serial == pytest.approx(0.02, abs=0.015)
    assert parallel < serial / 2, (
        f"four workers resolved {parallel * 1000:.1f} ms against one worker's "
        f"{serial * 1000:.1f} ms; the pool is not buying resolution"
    )


def test_the_achieved_cadence_is_reported_and_is_not_the_configured_one(tmp_path):
    """Quoting the dispatch interval as the resolution would be false precision.

    Asked for a 1 ms cadence against a 20 ms write with two workers, the probe can
    achieve about 100 attempts a second and not 1000. It must say so: the summary
    reports what happened, and the discarded ticks are counted rather than hidden.
    """
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.001,
        workers=2,
        write_cost_s=0.02,
        log_path=tmp_path / "rto_probe.log",
    )
    with probe:
        time.sleep(0.5)
    summary = probe.summary()
    assert summary["dispatch_interval_s"] == 0.001
    assert summary["achieved_rate_per_s"] < 1000
    assert summary["dispatch_saturation"] > 0
    assert summary["resolution_s"] > summary["dispatch_interval_s"]


def test_a_failure_and_the_reconnect_after_it_reach_the_log_file(tmp_path):
    """The separate log is the crash-proof record of the outage edges.

    The CSV is assembled when the run ends, and a chaos run interrupted while the
    fault is in place -- the case whose timings matter most -- would leave none.
    Every failure and every reconnect is therefore flushed as it happens.
    """
    log_path = tmp_path / "rto_probe.log"
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.002,
        workers=2,
        write_cost_s=0.005,
        log_path=log_path,
    )
    with probe:
        time.sleep(0.15)
        probe.down_until = time.monotonic() + 0.2
        time.sleep(0.35)

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "probe_start"
    assert kinds[-1] == "probe_stop"
    assert "attempt_failed" in kinds, "no failure was logged during the outage"
    assert "reconnect" in kinds, "no successful reconnect was logged after the outage"
    # High-resolution timestamps, or the log cannot date a millisecond outage.
    assert all("." in e["ts_utc"] for e in events)
    assert all(isinstance(e["offset_s"], float) for e in events)
    failure = next(e for e in events if e["event"] == "attempt_failed")
    assert failure["outcome"] in PROBE_OUTCOMES
    assert failure["outcome"] != "ok"


def test_the_probe_never_raises_into_the_run_it_is_observing(tmp_path):
    """A probe that can fail a chaos run is a new way to lose an hour of testbed.

    Every driver exception becomes a classified observation. The context manager
    exits normally even though every single write failed.
    """
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.002,
        workers=2,
        write_cost_s=0.001,
        log_path=tmp_path / "rto_probe.log",
    )
    probe.down_until = time.monotonic() + 3600
    with probe:
        time.sleep(0.2)
    assert probe.error is None
    assert probe.attempts
    assert not any(a.served for a in probe.attempts)


def test_offsets_are_taken_from_the_epoch_the_caller_supplies(tmp_path):
    """Four clocks in one run directory is D5; the caller owns the origin.

    Phases III-IV hand the probe the same monotonic zero they give ``events.json`` and
    ``wall_offset_s``, so the fault offset and the probe's observations can be
    placed on one axis without an unmeasured conversion between them.
    """
    epoch = time.monotonic() - 50.0
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.002,
        workers=1,
        write_cost_s=0.001,
        epoch_monotonic=epoch,
        log_path=tmp_path / "rto_probe.log",
    )
    with probe:
        time.sleep(0.1)
    assert min(a.dispatch_offset_s for a in probe.attempts) > 49.0


# --- classification --------------------------------------------------------


def test_a_timeout_is_not_the_same_observation_as_a_refusal():
    """The three failure kinds mean different things for a downtime figure.

    A timeout is what a lease transfer looks like from a client and is the
    outage's signature. A refusal is a reachable database rejecting the statement
    -- a bug in the probe -- and counting it as downtime would manufacture an
    outage out of a duplicate key.
    """
    assert classify(TimeoutError("canceling statement due to statement timeout"))[0] == "timeout"
    assert classify(_FakeConnectionError("connection reset"))[0] == "conn_error"
    assert classify(ValueError("duplicate key value"))[0] == "refused"
    for exc in (TimeoutError("x"), _FakeConnectionError("y"), ValueError("z")):
        assert classify(exc)[0] in PROBE_OUTCOMES


# --- the RTO derivation ----------------------------------------------------


def test_rto_runs_from_the_fault_to_the_end_of_the_outage_that_followed_it():
    """Not to the next write served, which on this cluster usually succeeds.

    A healthy cadence of ~0.1 s here, then a 2.4 s gap spanning the fault. The
    RTO is the fault to the far edge of that gap; the outage is the gap itself.
    """
    attempts = _attempts(
        [
            (0.80, 0.90, "ok"),
            (0.90, 1.00, "ok"),
            (1.00, 1.10, "ok"),
            (1.15, 3.40, "timeout"),
            (3.30, 3.50, "ok"),
            (3.50, 3.60, "ok"),
        ]
    )
    result = measure_rto(attempts, fault_offset_s=1.20)
    assert result["measurable"] is True
    assert result["outage"]["started_s"] == pytest.approx(1.10)
    assert result["outage"]["ended_s"] == pytest.approx(3.50)
    assert result["observed_outage_s"] == pytest.approx(2.40)
    assert result["rto_s"] == pytest.approx(2.30)
    # The RTO is shorter than the observed outage, because the outage began
    # before the fault could possibly have caused it -- up to one sampling gap
    # earlier, which is what the resolution means.
    assert result["rto_s"] < result["observed_outage_s"]


def test_detection_lag_is_reported_separately_from_recovery():
    """Writes keep committing until the cluster notices a node is gone.

    On this testbed liveness detection alone runs ~6 s. Folding that interval into
    the RTO would attribute the cluster's detection time to its recovery, which is
    the same category error as the legacy runner's ten-second guard being reported
    as a 6.0 s recovery.
    """
    # Healthy at ~0.1 s intervals through the fault at t=1.0 and on to t=6.0,
    # which is the cluster not yet noticing. Then a 3 s gap.
    healthy = [(t / 10, t / 10 + 0.1, "ok") for t in range(60)]
    attempts = _attempts([*healthy, (6.0, 9.0, "timeout"), (8.8, 9.0, "ok")])
    result = measure_rto(attempts, fault_offset_s=1.0)

    # Recovery: the fault to the end of the outage.
    assert result["rto_s"] == pytest.approx(8.0, abs=0.05)
    # Detection: the fault to the first attempt that did not get served. It is
    # ~5 s here, and it is not the recovery.
    assert result["detection_lag_s"] == pytest.approx(8.0, abs=0.05)
    # The outage did not start when the fault landed. Reporting only the RTO
    # would attribute those five seconds of healthy service to the recovery.
    assert result["outage"]["started_after_fault_s"] > 4.0
    assert result["outage"]["duration_s"] < result["rto_s"]


def test_an_interval_shorter_than_the_sampling_gap_is_not_quoted_as_a_number():
    """Below its own resolution the probe has not measured an outage.

    This mirrors ``resilience.availability``: the smaller of two indistinguishable
    quantities must not be reported as a result. It is the difference between a
    recovery time and an artefact of how often anyone looked.
    """
    attempts = _attempts([(t / 10, t / 10 + 0.05, "ok") for t in range(20)])
    result = measure_rto(attempts, fault_offset_s=1.02)
    assert result["measurable"] is True
    assert result["rto_s"] is None
    assert result["quotable_value_s"] is None
    assert result["outage"] is None
    assert "no interruption" in result["claim"]
    # "Not detectable at this resolution" is a result. A recovery time of zero
    # would be a claim the sampling cannot support.
    assert "not a recovery time of zero" in result["detail"]


def test_a_run_that_ended_during_the_outage_reports_no_rto_rather_than_a_bound():
    """The probe cannot see a recovery that happened after it stopped.

    Reporting the truncation as a measurement would put a floor into the figure
    that is an artefact of the run's duration -- which is precisely what the
    legacy 6.0 s and 5.2 s RTOs were.
    """
    healthy = [(t / 10, t / 10 + 0.1, "ok") for t in range(5)]
    attempts = _attempts([*healthy, (0.5, 3.0, "timeout"), (3.0, 5.0, "timeout")])
    result = measure_rto(attempts, fault_offset_s=0.45)
    assert result["measurable"] is False
    assert result["truncated"] is True
    assert result["rto_s"] is None
    # The interval is reported as a lower bound and never as the RTO.
    assert result["outage"]["ended_s"] is None
    assert result["outage"]["at_least_s"] > 4.0
    assert "outside the observation window" in result["detail"]


def test_an_in_flight_write_dates_the_recovery_more_tightly_than_a_later_one():
    """A blocked INSERT returns the moment the range is served again.

    The distinction is recorded because it is the difference between an
    observation of the recovery and a poll that happened to follow it.
    """
    healthy = [(t / 10, t / 10 + 0.1, "ok") for t in range(5)]

    # Dispatched at 0.5 and served at 5.0: it waited out the whole outage, so its
    # return is an observation of the recovery.
    blocked = measure_rto(_attempts([*healthy, (0.5, 5.0, "ok")]), 0.6)
    assert blocked["closed_by_in_flight_write"] is True
    assert blocked["in_flight_fraction"] > 0.9

    # Dispatched at 4.9, after service had already returned: it dates the
    # recovery only to the next poll, and is an upper bound rather than an
    # observation.
    polled = measure_rto(_attempts([*healthy, (4.9, 5.0, "ok")]), 0.6)
    assert polled["closed_by_in_flight_write"] is False
    assert polled["in_flight_fraction"] < 0.1


def test_outage_windows_are_ordered_by_duration_and_carry_both_edges():
    windows = outage_windows(_attempts([(0.0, 0.1, "ok"), (0.1, 0.2, "ok"), (0.2, 4.0, "ok")]))
    assert windows[0]["duration_s"] == pytest.approx(3.8)
    assert windows[0]["from_s"] == pytest.approx(0.2)
    assert windows[0]["to_s"] == pytest.approx(4.0)


def test_summarise_reports_no_resolution_rather_than_zero_when_nothing_was_served():
    """An unmeasured quantity must not be indistinguishable from one measured as
    zero. That is D5, and a resolution of 0.0 s would read as perfect precision."""
    summary = summarise(_attempts([(0.0, 0.5, "timeout")]))
    assert summary["resolution_s"] is None
    assert summary["outcomes"]["timeout"] == 1
    assert summary["outcomes"]["ok"] == 0


# --- the recorded artefact -------------------------------------------------


def test_the_csv_round_trips_into_the_same_rto(tmp_path):
    """The analysis layer re-derives from disk; it does not trust the summary.

    A published recovery time whose underlying observations cannot be recomputed
    cannot be disputed, which is why ``audit.csv`` exists and why this does too.
    """
    healthy = [(t / 10, t / 10 + 0.1, "ok") for t in range(8)]
    attempts = _attempts([*healthy, (0.8, 3.0, "timeout"), (0.9, 3.1, "ok")])
    frame = pd.DataFrame([a.to_row() for a in attempts], columns=list(PROBE_COLUMNS))
    path = tmp_path / "rto_probe.csv"
    frame.to_csv(path, index=False)

    reloaded = attempts_from_rows(pd.read_csv(path).to_dict("records"))
    assert measure_rto(reloaded, 1.0) == measure_rto(attempts, 1.0)


def test_rows_match_the_declared_schema_exactly():
    """MetricsWriter rejects a row that does not, so this fails at the writer
    rather than producing a file the analysis layer silently misreads."""
    row = _attempts([(0.0, 0.1, "ok")])[0].to_row()
    assert set(row) == set(PROBE_COLUMNS)


# --- validation ------------------------------------------------------------


def test_validation_accepts_a_sound_probe_log():
    frame = pd.DataFrame([a.to_row() for a in _attempts(
        [(0.0, 0.1, "ok"), (0.2, 2.0, "timeout"), (1.9, 2.1, "ok")]
    )])
    assert validate_probe(frame).ok


def test_validation_rejects_a_write_that_returned_before_it_was_sent():
    """Two offsets on different clocks would move an outage edge without making
    any single value look wrong."""
    frame = pd.DataFrame([a.to_row() for a in _attempts([(5.0, 1.0, "ok")])])
    report = validate_probe(frame)
    assert not report.ok
    assert any(f.check == "probe_ordering" for f in report.findings)


def test_validation_rejects_a_repeated_sequence_number():
    rows = [a.to_row() for a in _attempts([(0.0, 0.1, "ok"), (0.1, 0.2, "ok")])]
    rows[1]["seq_id"] = rows[0]["seq_id"]
    report = validate_probe(pd.DataFrame(rows))
    assert not report.ok
    assert any(f.check == "probe_sequence" for f in report.findings)


def test_validation_flags_a_refused_write_as_a_probe_defect_not_an_outage():
    rows = [a.to_row() for a in _attempts([(0.0, 0.1, "ok"), (0.1, 0.2, "refused")])]
    report = validate_probe(pd.DataFrame(rows))
    assert report.ok, "a probe bug is a warning, not a reason to discard the run"
    finding = next(f for f in report.findings if f.check == "probe_outcomes")
    assert finding.severity == "warning"
    assert "not an outage" in finding.message


def test_validation_rejects_a_log_in_which_nothing_was_ever_served():
    """With no served write there is no baseline, so there is no outage to
    measure -- the probe never reached the database."""
    frame = pd.DataFrame([a.to_row() for a in _attempts([(0.0, 0.5, "conn_error")])])
    report = validate_probe(frame)
    assert not report.ok
    assert any("never reached the database" in f.message for f in report.findings)


def test_validation_rejects_an_outcome_nobody_declared():
    rows = [a.to_row() for a in _attempts([(0.0, 0.1, "ok")])]
    rows.append({**rows[0], "seq_id": 99, "outcome": "probably_fine"})
    report = validate_probe(pd.DataFrame(rows))
    assert not report.ok
    assert any(f.check == "probe_outcomes" and f.severity == "error" for f in report.findings)


# --- through the analysis layer --------------------------------------------


def test_resilience_rederives_the_probe_rto_from_the_run_directory(tmp_path):
    """``analyze resilience`` reads the observations, not the phase's summary.

    The same discipline ``audit.csv`` exists for: a recovery time whose
    underlying observations were discarded cannot be re-derived, disputed, or
    plotted, so the analysis layer recomputes it and the phase's own figure is
    only a convenience.
    """
    import json

    from crdblab.analysis import resilience
    from crdblab.analysis.loader import load_run

    from tests.test_analysis import _EVENTS, _rows, _write_run

    events = {
        **_EVENTS,
        "probe": {
            "enabled": True,
            "workers": 8,
            "dispatch_interval_s": 0.002,
            "achieved_rate_per_s": 18.4,
        },
    }
    path = _write_run(
        tmp_path,
        "chaos_probe",
        _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
        phase="p4_chaos",
        events=events,
    )

    # Healthy at ~0.1 s, a fault at 10.0 s (from _EVENTS), an outage from 11.0 to
    # 13.4 s closed by a write that was in flight throughout it.
    healthy = [(t / 10, t / 10 + 0.1, "ok") for t in range(110)]
    attempts = _attempts([*healthy, (11.0, 13.4, "timeout"), (11.1, 13.4, "ok")])
    pd.DataFrame(
        [a.to_row() for a in attempts], columns=list(PROBE_COLUMNS)
    ).to_csv(path / "rto_probe.csv", index=False)

    summary = resilience.summarise(load_run(path))
    probe = summary["probe_rto"]
    assert probe["available"] is True
    assert probe["source"] == "re-derived from rto_probe.csv"
    assert probe["rto_s"] == pytest.approx(3.4, abs=0.05)
    assert probe["observed_outage_s"] == pytest.approx(2.4, abs=0.05)
    assert probe["closed_by_in_flight_write"] is True
    # Carried through from the phase so the figure can be read against the load
    # the probe itself added.
    assert probe["achieved_rate_per_s"] == 18.4
    # The audit-log figure is still reported, unchanged and beside it.
    assert "availability_rto" in summary
    assert json.dumps(summary, default=str)


def test_a_run_without_a_probe_log_says_so_rather_than_reporting_nothing(tmp_path):
    """Every committed run predates the probe. They must keep analysing, and the
    absence must be legible rather than an empty section."""
    from crdblab.analysis import resilience
    from crdblab.analysis.loader import load_run

    from tests.test_analysis import _EVENTS, _rows, _write_run

    path = _write_run(
        tmp_path,
        "chaos_no_probe",
        _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
        phase="p4_chaos",
        events=_EVENTS,
    )
    probe = resilience.summarise(load_run(path))["probe_rto"]
    assert probe["available"] is False
    assert "predates" in probe["detail"]


def test_a_disabled_probe_is_distinguished_from_a_missing_one(tmp_path):
    """"Turned off" and "did not exist yet" are different facts about a run, and
    only one of them is a reason to re-measure."""
    from crdblab.analysis import resilience
    from crdblab.analysis.loader import load_run

    from tests.test_analysis import _EVENTS, _rows, _write_run

    path = _write_run(
        tmp_path,
        "chaos_probe_off",
        _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
        phase="p4_chaos",
        events={**_EVENTS, "probe": {"enabled": False}},
    )
    probe = resilience.summarise(load_run(path))["probe_rto"]
    assert probe["available"] is False
    assert "disabled" in probe["detail"]


# --- what the operator is shown --------------------------------------------


def test_the_chaos_summary_prints_the_probe_beside_the_audit_figure(monkeypatch, capsys):
    """Two measurements of the same quantity are only useful when compared.

    They are printed adjacently and both labelled, so a disagreement between the
    audit log's cadence-bound figure and the probe's finer one is visible rather
    than resolved silently in favour of whichever the code happened to print.
    """
    from crdblab import cli
    from crdblab.phases import p4_chaos

    events = {
        "baseline_tps": 1855.0,
        "recovery_floor_tps": 1484.0,
        "recovery_threshold": 0.8,
        "recovery_hold_s": 10,
        "availability": {"availability_rto_s": 9.4, "resolution_s": 0.4, "write_gap_s": 9.8},
        "performance_rto_s": 12.0,
        "rpo": {
            "rpo_violations": 0,
            "acknowledged": 420,
            "ambiguous": 3,
            "refused": 0,
            "ambiguous_but_committed": 1,
        },
        "probe": {
            "enabled": True,
            "attempts": 3310,
            "achieved_rate_per_s": 18.4,
            "dispatch_interval_s": 0.002,
            "error": None,
            "rto": {
                "claim": "writes stopped being served 6.1s after the fault and "
                "resumed 9204 ms after it, an observed outage of 3104 ms",
                "observed_outage_s": 3.104,
                "detection_lag_s": 6.1,
                "resolution_s": 0.056,
            },
        },
    }

    class _Dir:
        path = "runs/x"
        events_json = "runs/x/events.json"

    monkeypatch.setattr(p4_chaos, "run", lambda *a, **k: (_Dir(), events))
    args = cli.build_parser().parse_args(["chaos", "run", "--mode", "dead"])
    assert cli._cmd_chaos(args) == 0

    out = capsys.readouterr().out
    assert "RTO availability" in out
    assert "RTO probe" in out
    assert "3104 ms" in out or "3104" in out
    assert "detection" in out
    assert "56.0 ms" in out
    assert "18.4/s achieved of 500/s dispatched" in out


def test_a_probe_that_failed_is_reported_as_a_failed_probe(monkeypatch, capsys):
    """And not as an outage, and not silently. A broken probe is a fact about the
    instrument; letting it print an empty section would invite the reader to
    assume there was nothing to see."""
    from crdblab import cli
    from crdblab.phases import p4_chaos

    events = {
        "baseline_tps": 1.0,
        "recovery_floor_tps": 1.0,
        "recovery_threshold": 0.8,
        "recovery_hold_s": 10,
        "availability": {"availability_rto_s": None, "detail": "not measured"},
        "performance_rto_s": None,
        "rpo": {
            "rpo_violations": 0, "acknowledged": 0, "ambiguous": 0,
            "refused": 0, "ambiguous_but_committed": 0,
        },
        "probe": {"enabled": True, "error": "ModuleNotFoundError: psycopg"},
    }

    class _Dir:
        path = "runs/x"
        events_json = "runs/x/events.json"

    monkeypatch.setattr(p4_chaos, "run", lambda *a, **k: (_Dir(), events))
    args = cli.build_parser().parse_args(["chaos", "run", "--mode", "dead"])
    cli._cmd_chaos(args)
    captured = capsys.readouterr()
    assert "RTO probe         FAILED" in captured.err


def test_a_run_whose_probe_log_is_corrupt_will_not_load(tmp_path):
    """The loader is the only way into a run, and it gates on the probe too.

    ``probe_availability`` reads ``rto_probe.csv`` with a bare ``read_csv``, so
    without this gate a probe log that fails its own checks would still reach a
    published recovery time -- which is the exact shape of the defect the loader
    exists to prevent for ``metrics.csv``.
    """
    from crdblab.analysis.loader import RunLoadError, load_run

    from tests.test_analysis import _EVENTS, _rows, _write_run

    path = _write_run(
        tmp_path,
        "chaos_bad_probe",
        _rows([(10, 800, 1.0, 200, 40.0)], wall_offset=5.4),
        phase="p4_chaos",
        events=_EVENTS,
    )
    # A write that returned before it was dispatched: two offsets on different
    # clocks, which would move an outage edge without looking wrong.
    bad = _attempts([(5.0, 1.0, "ok"), (1.0, 1.1, "ok")])
    pd.DataFrame(
        [a.to_row() for a in bad], columns=list(PROBE_COLUMNS)
    ).to_csv(path / "rto_probe.csv", index=False)

    with pytest.raises(RunLoadError, match="probe log"):
        load_run(path)


def test_the_standalone_probe_command_produces_a_normal_run_directory(tmp_path, monkeypatch, capsys):
    """`crdblab probe rto` is a measurement, so it leaves a manifest like any other.

    A run directory without one cannot be cited later, and there is no reason for
    the probe to be the exception -- especially since its whole purpose is to
    produce a number someone will quote.
    """
    from crdblab import cli
    from crdblab.core import ssh

    issued = []
    monkeypatch.setattr(ssh, "run", lambda node, cmd, timeout=None: issued.append(cmd))
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(
        lambda cls: cli.Settings(db_uri="postgresql://root@crdb-gcp-1:26257/ycsb", runs_dir=tmp_path)
    ))
    monkeypatch.setattr(RtoProbe, "write_cost_s", 0.005, raising=False)
    monkeypatch.setattr(RtoProbe, "down_until", None, raising=False)
    monkeypatch.setattr(RtoProbe, "_connect", lambda self, worker: _FakeConnection(self))

    args = cli.build_parser().parse_args(["probe", "rto", "--duration", "1", "--workers", "2"])
    assert cli._cmd_probe(args) == 0

    run_dir = next(tmp_path.glob("*_p4-probe"))
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "rto_probe.csv").exists()
    assert (run_dir / "rto_probe.log").exists()

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["phase"] == "p4_probe"
    assert manifest["clock_epoch_utc"], "the run's monotonic zero must be datable"
    assert manifest["profile"]["chaos"]["probe_table"] == "rto_canary"

    # The canary table is dropped and recreated, and against the gateway.
    assert any("rto_canary" in cmd and "DROP TABLE" in cmd for cmd in issued)
    assert any("crdb-gcp-1" in cmd for cmd in issued)

    # And it validates under its own schema, with no metrics.csv in sight.
    validate_args = cli.build_parser().parse_args(["validate", str(run_dir)])
    assert cli._cmd_validate(validate_args) == 0
    assert "probe log is internally consistent" in capsys.readouterr().out


# --- defects found against the live testbed --------------------------------


def test_the_pool_does_not_phase_lock_into_bursts(tmp_path):
    """Eight workers must give eight spread observations, not eight at once.

    Found against the live cluster: with a 2 ms dispatch interval and a 368 ms
    write, all eight workers finished together, all eight permits were released
    together, and the dispatcher refilled them 2 ms apart -- so the pool sampled
    in a burst once per round trip and left a ~350 ms hole between bursts. The
    recorded gaps were p50 0.22 ms and p90 342 ms, and the probe reported the
    0.22 ms as its resolution.

    The assertion is on the *shape* of the gap distribution rather than on any
    particular value: if the workers are spread, the tail cannot be many multiples
    of the middle.
    """
    probe = _FakeProbe(
        "postgresql://example/bench",
        interval_s=0.002,
        workers=8,
        write_cost_s=0.08,
        log_path=tmp_path / "rto_probe.log",
    )
    with probe:
        time.sleep(1.5)

    summary = probe.summary()
    p50, p95 = summary["gap_p50_s"], summary["resolution_s"]
    assert p50 and p95
    # A phase-locked pool gives p95/p50 in the hundreds. A spread one gives a
    # small multiple, because every gap is about latency/workers.
    assert p95 / p50 < 10, (
        f"gap distribution is bimodal (p50 {p50 * 1000:.2f} ms, p95 "
        f"{p95 * 1000:.2f} ms): the pool is completing in bursts, so its real "
        "sampling interval is the hole between them"
    )
    # And the spread interval should be near the write cost over the pool size.
    assert p95 < 0.08, "observations are no finer than a single write"


def test_resolution_is_the_tail_of_the_gap_distribution_not_the_median():
    """A bimodal sampling pattern must not be reported by its flattering mode.

    These are the gaps actually recorded against the live cluster, in shape: two
    thirds of them sub-millisecond because the pool completed in a burst, the rest
    a third of a second because nothing was in flight. An outage of 300 ms could
    begin and end inside one of the holes.
    """
    burst = [(t * 0.0002, t * 0.0002 + 0.36, "ok") for t in range(8)]
    later = [(0.36 + t * 0.0002, 0.36 + t * 0.0002 + 0.36, "ok") for t in range(8)]
    summary = summarise(_attempts([*burst, *later]))

    assert summary["gap_p50_s"] < 0.001, "fixture is not bimodal; test is not testing"
    assert summary["resolution_s"] > 0.3, (
        "resolution reported from the dense mode; an outage falling in the sparse "
        "mode would be invisible and the figure would claim sub-millisecond precision"
    )
    assert summary["gap_max_s"] >= summary["resolution_s"]


def test_the_outage_threshold_is_not_raised_by_the_outage_itself():
    """Sampling resolution is characterised from the healthy period only.

    Otherwise the whole-run tail includes the outage gap, so the longer the
    interruption the higher the bar for calling it one -- a detector that gets
    worse exactly as the event gets bigger.
    """
    healthy = [(t / 20, t / 20 + 0.05, "ok") for t in range(40)]
    short = measure_rto(_attempts([*healthy, (2.0, 3.0, "ok")]), 1.9)
    long = measure_rto(_attempts([*healthy, (2.0, 30.0, "ok")]), 1.9)

    assert short["outage"] is not None and long["outage"] is not None
    # The floor is a property of the healthy sampling, so it is the same in both
    # runs however long the outage was.
    assert short["noise_floor_s"] == pytest.approx(long["noise_floor_s"])


def test_a_longer_post_fault_window_does_not_manufacture_an_outage():
    """The exact false positive found against live data on 2026-09-05.

    A `dead` run's post-fault window held 1462 gaps against 711 pre-fault; drawing
    twice as many samples from the same heavy-tailed link produces a larger
    maximum on its own, with nothing having gone wrong. The naive detector (any
    gap over the healthy max plus one sampling period) reported an 869 ms
    "outage" 40 s after the fault. The exceedance-rate test below is what a
    correct detector must say instead: not distinguishable from the probe's own
    tail, because the post-fault rate of large gaps was, if anything, lower than
    the pre-fault one.

    This fixture reproduces the shape (not the exact values) of that run: a
    healthy tail with a small, constant per-observation chance of a slow gap, no
    change in that chance after the fault, and a longer post-fault window purely
    because the fault landed a third of the way through the run.
    """
    import random

    rng = random.Random(20260905)
    t = 0.0
    spec = []
    # Pre-fault: ~700 gaps, healthy cadence ~0.08s, occasionally slow.
    for _ in range(700):
        gap = 0.6 if rng.random() < 0.003 else rng.uniform(0.05, 0.12)
        t += gap
        spec.append((t - 0.01, t, "ok"))
    fault = t + 0.5
    t = fault + 0.5
    # Post-fault: same healthy cadence, same rare-slow-gap chance, just a longer
    # window (twice as many draws) -- no change in the underlying process.
    for _ in range(1450):
        gap = 0.6 if rng.random() < 0.003 else rng.uniform(0.05, 0.12)
        t += gap
        spec.append((t - 0.01, t, "ok"))

    result = measure_rto(_attempts(spec), fault)
    if result["outage"] is not None:
        assert result["fault_attributable"] is False, (
            "an outage was reported as attributable to the fault, but the "
            "post-fault window is simply longer than the pre-fault one and the "
            "underlying process never changed"
        )
        assert result["quotable_value_s"] is None
        assert "NOT distinguishable" in result["claim"]


def test_a_genuine_rate_increase_after_the_fault_is_attributable():
    """The other side of the same test: a real change in the exceedance rate
    must still be reported as a recovery time, or the fix would have traded a
    false positive for blindness to true positives."""
    healthy = [(t / 20, t / 20 + 0.05, "ok") for t in range(200)]
    # A sustained run of slow gaps immediately after the fault -- the rate
    # genuinely rises, not just the count.
    slow = [(4.9 + t * 0.6, 4.9 + t * 0.6 + 0.5, "ok") for t in range(1, 15)]
    attempts = _attempts([*healthy, *slow])
    result = measure_rto(attempts, fault_offset_s=5.0)

    assert result["outage"] is not None
    assert result["fault_attributable"] is True
    assert result["quotable_value_s"] is not None
    assert result["attribution"]["exceedance_rate_ratio"] > 1.5


def test_attribution_declines_to_call_it_with_too_few_pre_fault_observations():
    """A fault seconds into the run cannot be tested against a tail nobody
    characterised yet. The result must say so rather than default either way."""
    attempts = _attempts([(0.0, 0.1, "ok"), (0.1, 0.2, "ok"), (2.0, 5.0, "ok")])
    result = measure_rto(attempts, fault_offset_s=0.5)
    if result.get("outage") is not None:
        assert result["attribution"]["testable"] is False


def test_in_flight_fraction_is_clamped_to_one_when_the_write_started_early():
    """A fraction greater than one is not a fraction of anything.

    Reproduces seq_id 1169 from the retained 2026-09-05 dead-fault run: the write
    that closed the gap was dispatched at 99.762s, before the previous served
    write even completed at 100.220s (workers are concurrent, so this is
    ordinary, not an error). Its own flight time was 1.327s against a 0.869s gap,
    and dividing flight time by gap duration without accounting for the overlap
    gave 1.526 -- a value the field's own name rules out.

    The correct reading is the *overlap* between the write's flight window and
    the gap window: since it started before the gap even opened, it was in
    flight for the gap's entire duration, so the fraction is 1.0, not 1.53.
    """
    # A healthy run of served writes, then one slow write dispatched slightly
    # *before* the previous one completed -- ordinary with concurrent workers --
    # that takes long enough to be the gap the detector selects.
    healthy = [(t / 10, t / 10 + 0.08, "ok") for t in range(500)]  # up to t=50.0
    last_complete = healthy[-1][1]
    after = (last_complete - 0.05, last_complete + 1.2, "ok")
    attempts = _attempts([*healthy, after])
    result = measure_rto(attempts, fault_offset_s=40.0)
    assert result["outage"] is not None
    assert result["in_flight_fraction"] == pytest.approx(1.0)
    assert result["closed_by_in_flight_write"] is True


def test_in_flight_fraction_never_exceeds_one(tmp_path):
    """Property check across many overlap shapes, not just the one that was
    caught: whatever the relative timing of dispatch and completion, the
    reported fraction must stay in [0, 1] because it is documented as one."""
    import random

    rng = random.Random(7)
    for _ in range(200):
        before_complete = rng.uniform(1.0, 5.0)
        # `after` may be dispatched anywhere from well before `before` completed
        # to well after -- both are realistic with concurrent workers.
        after_dispatch = before_complete + rng.uniform(-2.0, 1.0)
        after_complete = after_dispatch + rng.uniform(0.01, 3.0)
        if after_complete <= before_complete:
            continue
        attempts = _attempts(
            [(before_complete - 0.1, before_complete, "ok"), (after_dispatch, after_complete, "ok")]
        )
        result = measure_rto(attempts, fault_offset_s=0.0)
        if result["outage"] is not None:
            assert 0.0 <= result["in_flight_fraction"] <= 1.0


# --------------------------------------------------------------------------
# The probe runs on the client node, so its offsets arrive on a different
# machine's clock and have to be rebased onto the run's. Getting that wrong
# displaces every observation relative to the fault time by an interval nobody
# measured, which is D5 -- so the conversion is pinned here.
# --------------------------------------------------------------------------

from crdblab.core.remote_probe import AGENT_FILES, RemoteRtoProbe
from crdblab.topology import CLIENT_NODE


def _remote_probe(**kw):
    from pathlib import Path

    defaults = dict(
        package_root=Path("/nonexistent"),
        duration_s=10.0,
        epoch_monotonic=1000.0,
        epoch_utc="2026-09-08T02:00:00.000000Z",
    )
    defaults.update(kw)
    return RemoteRtoProbe(CLIENT_NODE, "postgresql://root@h:26257/bench", **defaults)


def test_agent_offsets_are_rebased_onto_the_runs_clock():
    """An agent offset is placed by the gap between the two epochs."""
    probe = _remote_probe()
    # The agent started 8.5 s after the harness took its epoch.
    probe._on_start({"epoch_utc": "2026-09-08T02:00:08.500000Z"})
    assert probe.epoch_skew_s == pytest.approx(8.5)

    probe._on_attempt(
        {
            "seq_id": 1,
            "dispatch_offset_s": 2.0,
            "complete_offset_s": 2.25,
            "outcome": "ok",
            "worker": 0,
        }
    )
    (attempt,) = probe.attempts
    # 2.0 s on the agent's clock is 10.5 s on the run's.
    assert attempt.dispatch_offset_s == pytest.approx(10.5)
    assert attempt.complete_offset_s == pytest.approx(10.75)
    # The interval between the two is a property of the write, not of either
    # clock, so it must survive the conversion untouched.
    assert attempt.duration_ms == pytest.approx(250.0)


def test_attempts_arriving_before_the_epoch_are_dropped_not_guessed():
    """Without the epoch line there is no origin, and inventing one is the bug."""
    probe = _remote_probe()
    probe._on_attempt(
        {
            "seq_id": 1,
            "dispatch_offset_s": 2.0,
            "complete_offset_s": 2.25,
            "outcome": "ok",
            "worker": 0,
        }
    )
    assert probe.attempts == []


def test_an_unparseable_agent_epoch_is_an_error_not_a_zero_skew():
    probe = _remote_probe()
    probe._on_start({"epoch_utc": "not-a-timestamp"})
    assert probe.epoch_skew_s is None
    assert probe.error and "epoch" in probe.error


def test_malformed_attempt_lines_do_not_abort_the_run():
    """A probe that could kill the measurement it observes is a new failure mode."""
    probe = _remote_probe()
    probe._on_start({"epoch_utc": "2026-09-08T02:00:00.000000Z"})
    probe._on_attempt({"seq_id": "not-an-int", "outcome": "ok"})
    probe._on_attempt({})
    assert probe.attempts == []


def test_summary_records_where_the_probe_ran_and_the_skew_it_applied():
    """Both are part of the measurement and must reach the run directory."""
    probe = _remote_probe()
    probe._on_start({"epoch_utc": "2026-09-08T02:00:03.250000Z"})
    summary = probe.summary()
    assert summary["ran_on"] == CLIENT_NODE.host
    assert summary["epoch_skew_s"] == pytest.approx(3.25)
    assert "preflight.check_clock_offset" in summary["note_clock"]


def test_the_agent_ships_only_stdlib_only_modules():
    """The agent's dependency surface is psycopg plus the standard library.

    Adding a module here that imports pandas (or anything else the cluster nodes
    do not have) would turn a probe failure into a run failure, discovered
    mid-measurement.
    """
    assert set(AGENT_FILES) == {
        "crdblab/__init__.py",
        "crdblab/core/__init__.py",
        "crdblab/core/recorder.py",
        "crdblab/core/rto_probe.py",
    }

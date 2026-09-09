"""Tests for Phases III-IV recovery detection.

``find_recovery`` decides the single number Phases III-IV exist to produce, and the
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


def test_an_audit_writer_that_stopped_observing_reports_no_rto_at_all():
    """The 2026-09-09 regression, at the real run's numbers.

    ``runs/20260909T012233Z_p4-chaos-recover``: fault at 24.792 s, the last
    acknowledgement 3.7 s later at 28.49 s over a connection the partition had
    already black-holed, then nothing for the remaining ~25 s of the run because
    the writer blocked inside one INSERT. No gap between acknowledgements
    cleared the detection floor -- there were no further acknowledgements to
    make one -- so the old code fell through to "the interval to the next
    acknowledged write" and reported **0.082 s** for an outage the generator
    recorded as two consecutive ticks of zero throughput. A 0.08 s RTO is not a
    conservative reading of that evidence; it is the flattering one.
    """
    acks = [(24.792 + 0.39 * i, "ack") for i in range(-40, 10)]
    a = _attempts(acks)
    r = availability_rto(a, fault_monotonic=24.792, observation_end=53.8)

    assert r["availability_rto_s"] is None, "silence must not be reported as recovery"
    assert r["coverage_truncated"] is True
    assert r["coverage_gap_s"] > 20
    assert "unmeasured, not zero" in r["detail"]


def test_a_writer_still_blocked_at_the_end_of_the_run_is_not_counted_as_covering_it():
    """Coverage is judged on acknowledgements, not on attempts.

    The real writer is single-threaded and never abandons an attempt: the one it
    was blocked on eventually resolved as ``ambiguous`` 48 s later, *after* the
    run had ended. Judged on "did it attempt anything recently" it looks like it
    covered the whole run and more; judged on what it actually observed, it made
    one observation in fifty seconds while nominally sampling at 2.5/s.
    """
    acks = [(24.792 + 0.39 * i, "ack") for i in range(-40, 10)]
    a = _attempts(acks + [(76.778, "ambiguous")])
    r = availability_rto(a, fault_monotonic=24.792, observation_end=53.8)

    assert r["availability_rto_s"] is None
    assert r["coverage_truncated"] is True


def test_full_coverage_still_reports_no_interruption_as_a_result():
    """The control. Coverage reaching the end of the run is the ordinary case,
    and an instrument that watched throughout and saw nothing has measured
    something -- that must not become an "unmeasured" verdict."""
    a = _attempts([(9.0, "ack"), (10.1, "ack"), (11.0, "ack"), (11.9, "ack")])
    r = availability_rto(a, fault_monotonic=10.0, observation_end=12.0)
    assert r["availability_rto_s"] == pytest.approx(0.1)
    assert r["coverage_truncated"] is False


def test_coverage_is_not_judged_at_all_when_the_run_end_is_unknown():
    """Runs recorded before the field existed must read exactly as they did."""
    a = _attempts([(9.0, "ack"), (10.1, "ack"), (11.0, "ack")])
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["coverage_truncated"] is None
    assert r["availability_rto_s"] == pytest.approx(0.1)


def test_resolution_is_reported_so_the_figure_can_be_qualified():
    """An RTO below the audit cadence is indistinguishable from no outage."""
    a = _attempts([(9.0, "ack"), (9.5, "ack"), (10.0, "ack"), (10.5, "ack")])
    r = availability_rto(a, fault_monotonic=10.0)
    assert r["resolution_s"] == pytest.approx(0.5)


# --- Patroni primary resolution ---------------------------------------------
#
# Unlike CockroachDB's lease_preferences, nothing pins which node wins
# Patroni's leader election, so a profile's static chaos.target cannot be
# trusted for PostgreSQL: resolve_patroni_primary queries the cluster live
# instead, immediately before the fault is scheduled.

import json
from unittest.mock import patch

from crdblab.phases.p4_chaos import resolve_patroni_primary
from crdblab.topology import Node, Topology

_NODES = tuple(
    Node(f"n{i}", f"host{i}", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")
    for i in range(3)
)
_TOPO = Topology(nodes=_NODES)


class _FakeResponse:
    def __init__(self, status, body=None):
        self.status = status
        self._body = b"" if body is None else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


#: A healthy replica's ``/patroni`` document, in the two fields
#: ``patroni_candidate_ready`` reads. It is streaming, and it is on the same
#: timeline as the leader below -- which is exactly what the live failure of
#: 2026-09-09 was not: that node answered ``/replica`` 200 while sitting on
#: timeline 1 with no replication connection at all.
_STREAMING = {"role": "replica", "replication_state": "streaming", "timeline": 2}
_LEADING = {"role": "primary", "timeline": 2}


def _urlopen_returning(
    statuses_by_host, replica_statuses=None, patroni_states=None, quorum_statuses=None
):
    """Build a fake ``urlopen`` keyed on the host embedded in the URL.

    ``statuses_by_host`` answers ``/primary``. ``/replica`` is answered from
    ``replica_statuses`` when given, and otherwise by inverting ``/primary`` --
    which is what a healthy cluster does: exactly one member is the leader and
    every other running member is a candidate. The two endpoints have to be
    distinguishable here because ``check_patroni_primary_placement`` reads both,
    and a fixture that returned the leader's 503 for ``/replica`` as well would
    make every replica look ineligible.

    ``/patroni`` is the third endpoint and it is answered by default as a
    healthy cluster would: the leader leading on timeline 2, everyone else
    streaming on timeline 2. ``patroni_states`` overrides that per host, which
    is how the regression test below reproduces a member that is up and
    unlagged and still cannot be handed leadership.

    ``/quorum`` answers 200 by default for every non-leader -- a healthy,
    already-converged cluster's steady state -- and ``quorum_statuses``
    overrides that per host, for a candidate that is streaming and on the right
    timeline but not yet admitted to ``synchronous_standby_names``.
    """

    def _status_for(url, host):
        if url.endswith("/replica"):
            if replica_statuses is not None:
                return replica_statuses.get(host, 503)
            primary = statuses_by_host.get(host)
            return 503 if primary == 200 else 200
        if url.endswith("/quorum"):
            if quorum_statuses is not None:
                return quorum_statuses.get(host, 200)
            return 200
        return statuses_by_host[host]

    def _body_for(host):
        if patroni_states is not None and host in patroni_states:
            return patroni_states[host]
        return _LEADING if statuses_by_host.get(host) == 200 else _STREAMING

    def _urlopen(url, timeout=None):
        for host in statuses_by_host:
            if f"//{host}:" in url:
                if url.endswith("/patroni"):
                    return _FakeResponse(200, _body_for(host))
                outcome = _status_for(url, host)
                if isinstance(outcome, Exception):
                    raise outcome
                return _FakeResponse(outcome)
        raise AssertionError(f"unexpected URL in test: {url}")

    return _urlopen


def test_the_single_node_answering_200_is_the_primary():
    import urllib.error

    fake = _urlopen_returning(
        {"host0": 503, "host1": 200, "host2": urllib.error.URLError("refused")}
    )
    with patch("urllib.request.urlopen", side_effect=fake):
        assert resolve_patroni_primary(_TOPO).name == "n1"


def test_no_primary_found_refuses_rather_than_guessing():
    import urllib.error

    fake = _urlopen_returning(
        {"host0": 503, "host1": 503, "host2": urllib.error.URLError("refused")}
    )
    with patch("urllib.request.urlopen", side_effect=fake):
        with pytest.raises(ValueError, match="no cluster member"):
            resolve_patroni_primary(_TOPO)


def test_two_primaries_is_a_split_brain_and_refuses_rather_than_picking_one():
    fake = _urlopen_returning({"host0": 200, "host1": 200, "host2": 503})
    with patch("urllib.request.urlopen", side_effect=fake):
        with pytest.raises(ValueError, match="split-brain"):
            resolve_patroni_primary(_TOPO)


# --------------------------------------------------------------------------
# Fault injection has to actually land.
#
# The 2026-09-08 chaos runs are the reason these exist. `crdb-gcp-1` is reached
# over SSH as `ubuntu` while `cockroach` runs as root, so `killall -9 cockroach`
# returned `Operation not permitted` (rc=1) and the target served uninterrupted
# for the whole run -- its pid was unchanged afterwards. The harness recorded
# `"detail": "rc=1"` and produced a complete run directory that passed
# `validate` and reported "no write interruption detectable", which reads as an
# excellent resilience result and is in fact a measurement of nothing.
# `recover` was worse: its payload is backgrounded, so a denied
# `tailscale down` cannot even be seen in the exit status.
# --------------------------------------------------------------------------

from types import SimpleNamespace

from crdblab.core import preflight as _preflight
from crdblab.core.ssh import RemoteResult
from crdblab.phases.p4_chaos import (
    check_fault_authorisation,
    get_payload,
    inject_fault,
    preflight_payload,
)

_TARGET = Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")


@pytest.mark.parametrize("mode", ["dead", "recover"])
@pytest.mark.parametrize("engine", ["cockroachdb", "postgresql"])
def test_every_fault_payload_is_privileged(mode, engine):
    """A payload without sudo is silently refused on any non-root SSH user."""
    assert get_payload(mode, engine).startswith("sudo -n ")


def test_dead_payload_kills_the_right_process_per_engine():
    assert "cockroach" in get_payload("dead", "cockroachdb")
    assert "patroni" in get_payload("dead", "postgresql")


def test_the_postgresql_dead_fault_disables_restart_with_a_drop_in_not_set_property():
    """`systemctl set-property patroni.service Restart=no` -- the obvious
    one-liner -- fails with "Cannot set property Restart, or unknown property":
    set-property only accepts properties settable on a running unit, which
    Restart= is not. Measured against crdb-azure-1: rc=1, the `&&`
    short-circuited, and the primary served on untouched. A drop-in file plus
    daemon-reload is the supported mechanism."""
    payload = get_payload("dead", "postgresql")
    assert "set-property" not in payload
    assert "patroni.service.d" in payload and "daemon-reload" in payload


def test_the_postgresql_dead_fault_cannot_be_undone_by_systemd():
    """patroni.service ships Restart=on-failure, so a SIGKILL is a failure by
    systemd's definition and the unit returns within RestartSec (~100 ms). A
    dead run would then measure systemd's restart rather than the cluster's
    failover -- and report a better RTO than CockroachDB's on a fault that was
    never the same fault. Restart must be disabled before the kill lands."""
    payload = get_payload("dead", "postgresql")
    assert "Restart=no" in payload
    assert payload.index("Restart=no") < payload.index("kill")


def test_the_postgresql_dead_fault_signals_the_unit_not_a_process_name():
    """Patroni runs as `/usr/bin/python3 /usr/bin/patroni`, so its comm is
    `python3` and `killall -9 patroni` matches nothing; a `pkill -f patroni`
    written to fix that matches the SSH command carrying it. Signalling the
    unit's cgroup avoids both, and takes the postmaster with it."""
    payload = get_payload("dead", "postgresql")
    assert "systemctl kill" in payload
    assert "--kill-who=all" in payload
    assert "killall" not in payload and "pkill" not in payload


def test_restoring_postgresql_removes_the_restart_override():
    """Otherwise the node comes back with Restart=no still in place and the next
    dead run measures a node systemd has stopped supervising."""
    import inspect

    from crdblab.phases import p4_chaos

    source = inspect.getsource(p4_chaos.restore_target)
    # The path is interpolated from the constant, so the source names the
    # constant rather than the expanded path -- which is the point: the fault
    # and the restore cannot drift apart onto two different files.
    assert "rm -f {PG_RESTART_OVERRIDE}" in source
    assert "daemon-reload" in source
    assert p4_chaos.PG_RESTART_OVERRIDE in p4_chaos.get_payload("dead", "postgresql")


@pytest.mark.parametrize("mode", ["dead", "recover"])
def test_the_authorisation_probe_is_privileged_and_harmless(mode):
    """It must need the same rights as the fault while changing nothing."""
    probe = preflight_payload(mode, "cockroachdb")
    assert probe.startswith("sudo -n ")
    # -9 would kill; -0 only asks whether we are allowed to signal.
    assert "-9" not in probe
    assert "tailscale down" not in probe


def test_a_refused_injection_is_recorded_as_not_landed():
    denied = RemoteResult(1, "", "cockroach(2553): Operation not permitted")
    with patch("crdblab.core.ssh.run", return_value=denied):
        result = inject_fault(_TARGET, "dead", "cockroachdb")
    assert result["landed"] is False
    # The reason has to survive into events.json; "rc=1" alone was what made the
    # original failure unreadable after the fact.
    assert "Operation not permitted" in result["detail"]


def test_a_successful_injection_is_recorded_as_landed():
    with patch("crdblab.core.ssh.run", return_value=RemoteResult(0, "", "")):
        result = inject_fault(_TARGET, "dead", "cockroachdb")
    assert result["landed"] is True


def test_a_transport_error_is_not_treated_as_a_refusal():
    """For a `dead` fault, losing the connection is evidence the fault landed."""
    with patch("crdblab.core.ssh.run", side_effect=OSError("connection reset")):
        result = inject_fault(_TARGET, "dead", "cockroachdb")
    assert result["landed"] is None
    assert result["landed"] is not False


def test_preflight_fails_when_the_fault_would_not_be_permitted():
    denied = RemoteResult(1, "", "cockroach(2553): Operation not permitted")
    report = _preflight.PreflightReport()
    with patch("crdblab.core.ssh.run", return_value=denied):
        check_fault_authorisation(report, _TARGET, "dead", "cockroachdb")
    assert not report.ok
    with pytest.raises(_preflight.PreflightError):
        report.raise_if_failed()


def test_preflight_passes_when_the_fault_would_be_permitted():
    report = _preflight.PreflightReport()
    with patch("crdblab.core.ssh.run", return_value=RemoteResult(0, "", "")):
        check_fault_authorisation(report, _TARGET, "recover", "cockroachdb")
    assert report.ok


# --------------------------------------------------------------------------
# The post-fault series is the measurement, so the run has to outlive the
# fault. Two separate things guarantee it, and the 2026-09-08 thesis run shows
# why both are needed: the generator was given 120s of post-fault time and used
# 7.1s of it, because `cockroach workload run` exits on its first failed
# statement and the fault is a failed statement.
# --------------------------------------------------------------------------

from crdblab.config import ChaosSpec, Profile
from crdblab.phases.p4_chaos import (
    RECOVER_HEAL_DELAY_S,
    generator_duration_s,
    get_payload,
    restore_target,
)


def test_a_profile_that_already_observes_long_enough_is_left_alone():
    chaos = ChaosSpec(duration_s=180, inject_at_s=60, min_post_fault_s=60)
    assert generator_duration_s(chaos, "dead") == 180


def test_a_run_too_short_to_observe_the_recovery_is_extended():
    """inject_at_s is measured from the first sample, so duration_s alone
    cannot guarantee anything about what follows the fault."""
    chaos = ChaosSpec(duration_s=100, inject_at_s=90, min_post_fault_s=60)
    assert generator_duration_s(chaos, "dead") == 150


def test_the_window_is_never_shortened():
    chaos = ChaosSpec(duration_s=600, inject_at_s=60, min_post_fault_s=60)
    assert generator_duration_s(chaos, "dead") == 600


def test_recover_mode_observes_from_the_heal_not_from_the_fault():
    """The partition heals itself after RECOVER_HEAL_DELAY_S, so nothing
    between the fault and the heal can contain a recovery. Counting the
    post-fault window from the fault therefore buys observation of the outage
    and none of what the phase exists to measure."""
    chaos = ChaosSpec(duration_s=45, inject_at_s=15, min_post_fault_s=20)
    assert generator_duration_s(chaos, "dead") == 45
    assert generator_duration_s(chaos, "recover") == 15 + RECOVER_HEAL_DELAY_S + 20


def test_the_smoke_profile_can_actually_observe_a_recover_recovery():
    """Regression on experiment-20260909T031334Z.log, where it could not.

    The run was 45s with the fault at 15s, so it ended at 45s while the
    partition did not lift until 60s: no post-heal sample could exist at any
    point in the run, and both independent instruments duly reported the RTO as
    UNMEASURED. The 45s window that follows the heal is sized on the
    thesis-scale run of 2026-09-08, which measured writes resuming ~24s after
    the partition lifted -- promotion and client reconnection both happen after
    the network comes back, not during the outage."""
    chaos = Profile.load("smoke").chaos
    heal_at = chaos.inject_at_s + RECOVER_HEAL_DELAY_S
    assert generator_duration_s(chaos, "recover") >= heal_at + 30


def test_thesis_extended_recover_run_is_long_enough_to_settle():
    """Regression pin for the 2026-09-10 lengthening: at the 60s default,
    resilience.post_fault_steady_state's CV<0.25 test could not resolve
    either chaos mode's tail on the 2026-09-09 thesis-scale PostgreSQL runs
    (104-105 post-settle ticks, cv=0.86-0.87). 900s was chosen by replaying
    that recover run's own recorded series and confirming its window's CV
    crosses below 0.25 only once ~800-850 post-settle ticks are in it -- see
    profiles/thesis-extended.yaml's comment for the full derivation."""
    chaos = Profile.load("thesis-extended").chaos
    assert chaos.min_post_fault_s == 900
    assert generator_duration_s(chaos, "recover") == 60 + RECOVER_HEAL_DELAY_S + 900


def test_thesis_extended_dead_run_is_long_enough_to_settle():
    chaos = Profile.load("thesis-extended").chaos
    assert generator_duration_s(chaos, "dead") == 60 + 900


def test_thesis_recover_run_is_long_enough_to_settle():
    """thesis.yaml was lengthened too (2026-09-10, at the user's request),
    to 450s -- half of thesis-extended.yaml's 900s, since this is the
    canonical cross-engine comparability profile and its overall run budget
    matters more here."""
    chaos = Profile.load("thesis").chaos
    assert chaos.min_post_fault_s == 450
    assert generator_duration_s(chaos, "recover") == 60 + RECOVER_HEAL_DELAY_S + 450


def test_thesis_dead_run_is_long_enough_to_settle():
    chaos = Profile.load("thesis").chaos
    assert generator_duration_s(chaos, "dead") == 60 + 450


def test_thesis_extended_still_has_the_longer_window():
    """thesis.yaml (450s) and thesis-extended.yaml (900s) were raised by
    different amounts deliberately -- this pins that they didn't drift back
    into agreement."""
    assert Profile.load("thesis").chaos.min_post_fault_s == 450
    assert Profile.load("thesis-extended").chaos.min_post_fault_s == 900


def test_the_payload_and_the_run_length_read_the_same_heal_delay():
    """Two numbers that must never drift apart: if the payload's sleep and the
    run's length disagree, the run is silently either too short to observe the
    recovery or padded with dead time."""
    payload = get_payload("recover", "cockroachdb")
    assert f"sleep {RECOVER_HEAL_DELAY_S} " in payload, payload


# --------------------------------------------------------------------------
# Restoring the dead target. Two mistakes this pins, both real: an
# unprivileged `cockroach start` cannot open the root-owned store, and asking
# the fault target whether it is alive always answers "no".
# --------------------------------------------------------------------------

_FIVE = Topology(nodes=tuple(
    Node(f"n{i}", f"host{i}", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")
    for i in range(5)
))


def _restore_with(target_name="n0", engine="cockroachdb", live="5"):
    calls = []

    def fake_run(node, cmd, timeout=None):
        calls.append((node.name, cmd))
        return RemoteResult(0, live if "node status" in cmd else "", "")

    with patch("crdblab.core.ssh.run", side_effect=fake_run):
        with patch("time.sleep"):
            result = restore_target(
                _FIVE.get(target_name), _FIVE, engine, timeout_s=1, poll_interval_s=0.01
            )
    return result, calls


def test_the_restart_is_privileged():
    """/var/lib/cockroach is root-owned and the SSH user is not root."""
    _, calls = _restore_with()
    start = next(cmd for name, cmd in calls if "cockroach start" in cmd)
    assert "sudo -n cockroach start" in start


def test_the_restored_node_does_not_try_to_rejoin_via_itself():
    _, calls = _restore_with(target_name="n0")
    start = next(cmd for name, cmd in calls if "cockroach start" in cmd)
    join = start.split("--join=")[1].split()[0]
    assert "host0:" not in join
    assert "host1:" in join


def test_liveness_is_read_from_a_survivor_not_from_the_fault_target():
    """Polling the killed node always answers 0 live, which is what made
    run-experiment.sh warn on every successful dead-mode run."""
    result, calls = _restore_with(target_name="n0")
    status_nodes = {name for name, cmd in calls if "node status" in cmd}
    assert status_nodes and "n0" not in status_nodes
    assert result["witness"] != "n0"
    assert result["rejoined"] is True


def test_a_node_that_never_comes_back_is_reported_as_not_rejoined():
    result, _ = _restore_with(live="4")
    assert result["rejoined"] is False
    assert result["nodes_live"] == 4


def test_postgresql_restarts_patroni_rather_than_cockroach():
    _, calls = _restore_with(engine="postgresql")
    assert any("systemctl start patroni" in cmd for _, cmd in calls)
    assert not any("cockroach start" in cmd for _, cmd in calls)


# --- Patroni primary placement (the PostgreSQL counterpart to D7's check) ----
#
# Where the write path is led from is a property of the deployment, not of the
# engine, so it has to hold identically on both arms or the comparison measures
# cloud geography. Measured 2026-09-09: an unpinned election put the primary on
# crdb-azure-2 (eastasia, 199 ms from the client) where a single connection cost
# 1.02 s against 0.05 s to the gateway.
#
# The reason this needs its own repair, rather than the settle window
# check_leaseholder_placement uses, is that Patroni never fails back. Waiting
# would be waiting for something that cannot happen.

_GW = Node("gcp-1", "hostgw", "ubuntu", "gcp", "us-east1", "cloud=gcp", gateway=True)
_OTHER = Node("azure-2", "hostaz", "ubuntu", "azure", "eastasia", "cloud=azure")
_PLACEMENT_TOPO = Topology(nodes=(_GW, _OTHER))


def _placement_report(statuses, ssh_result=None):
    report = _preflight.PreflightReport()
    with (
        patch("urllib.request.urlopen", side_effect=_urlopen_returning(statuses)),
        patch.object(_preflight.ssh, "run", return_value=ssh_result) as run,
    ):
        _preflight.check_patroni_primary_placement(report, _PLACEMENT_TOPO)
    return report, run


def _entry(report):
    return next(c for c in report.checks if c.name == "patroni_primary_placement")


def test_primary_already_on_the_gateway_passes_without_switching_over():
    report, run = _placement_report({"hostgw": 200, "hostaz": 503})
    entry = _entry(report)
    assert entry.passed
    assert entry.observed["repaired"] is False
    # The switchover is a state change on a live cluster; it must not fire when
    # the condition already holds.
    run.assert_not_called()


def test_primary_elsewhere_is_switched_over_to_the_gateway():
    # Before the switchover the far node answers; afterwards the gateway does.
    responses = iter(
        [
            _urlopen_returning({"hostgw": 503, "hostaz": 200}),
            _urlopen_returning({"hostgw": 200, "hostaz": 503}),
        ]
    )
    current = {"fn": next(responses)}

    def _urlopen(url, timeout=None):
        return current["fn"](url, timeout=timeout)

    report = _preflight.PreflightReport()
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    def _switch(node, command, timeout=None):
        assert "switchover" in command
        # Patroni identifies members by the `name:` in its own config, which
        # bootstrap-patroni.tftpl sets to the node's HOSTNAME (`crdb-gcp-1`) --
        # not this harness's short Node.name (`gcp-1`) that profiles use for
        # chaos.target. This assertion previously demanded the short name and so
        # locked the bug in: the live Phase IV repair on 2026-09-08 ran
        # `switchover --leader linode-2 --candidate gcp-1`, Patroni answered
        # "Member linode-2 is not the leader of cluster postgres-cluster", and
        # the phase never ran.
        assert "--candidate hostgw" in command, command
        assert "--leader hostaz" in command, command
        assert "gcp-1" not in command, (
            "member must be addressed by hostname, not by the short Node.name"
        )
        assert command.startswith("sudo -n"), "patronictl needs privilege"
        current["fn"] = next(responses)
        return result

    with (
        patch("urllib.request.urlopen", side_effect=_urlopen),
        patch.object(_preflight.ssh, "run", side_effect=_switch),
    ):
        _preflight.check_patroni_primary_placement(report, _PLACEMENT_TOPO)

    entry = _entry(report)
    assert entry.passed
    assert entry.observed["repaired"] is True
    assert entry.observed["observed"] == "gcp-1"


def test_a_candidate_that_is_still_catching_up_is_waited_for_not_refused():
    """Phase IV must survive following Phase III on the PostgreSQL arm.

    Phase III partitions the primary, so the demoted node's timeline diverges
    and it has to be rewound or (per ``remove_data_directory_on_diverged_
    timelines``) re-cloned from the leader before Patroni will hand leadership
    back. At thesis scale that is ~6 GB across a WAN link. Phase IV starts the
    instant Phase III returns, so with no wait the repair asks for a handover to
    a member still taking its basebackup and Patroni answers "no good candidates
    have been found" -- aborting the sweep on a condition that clears itself in
    minutes.
    """
    # /replica: 503 while it catches up, then 200. /primary: the far node until
    # the switchover, then the gateway.
    replica_states = iter([{"hostgw": 503}, {"hostgw": 503}, {"hostgw": 200}])
    primary_states = {"phase": {"hostgw": 503, "hostaz": 200}}
    switched = {"done": False}

    def _urlopen(url, timeout=None):
        if url.endswith("/replica"):
            return _FakeResponse(next(replica_states).get("hostgw", 503))
        if url.endswith("/patroni"):
            leading = primary_states["phase"].get("hostgw") == 200
            return _FakeResponse(200, _LEADING if leading else _STREAMING)
        if url.endswith("/quorum"):
            return _FakeResponse(200)
        for host, code in primary_states["phase"].items():
            if f"//{host}:" in url:
                return _FakeResponse(code)
        raise AssertionError(url)

    def _switch(node, command, timeout=None):
        switched["done"] = True
        primary_states["phase"] = {"hostgw": 200, "hostaz": 503}
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = _preflight.PreflightReport()
    with (
        patch("urllib.request.urlopen", side_effect=_urlopen),
        patch.object(_preflight.ssh, "run", side_effect=_switch),
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=30
        )

    entry = _entry(report)
    assert entry.passed, entry.detail
    assert switched["done"], "the wait must end in the explicit switchover"


def test_a_replica_that_is_up_but_not_streaming_is_not_a_candidate():
    """The 2026-09-09 failure: /replica 200 is not sufficient.

    After Phase III's partition, gcp-1 came back up and answered /replica 200,
    the wait ended immediately on "running replica, lag within bounds", and the
    switchover that followed failed with `503, Switchover failed`. The cluster
    table printed with that failure shows why: gcp-1 was `Role: Replica` on
    TIMELINE 1 with `Receive LSN: unknown`, while the leader and the other three
    members were streaming on timeline 2. It was unlagged because it was not
    attached to anything -- lag is measured against a position it could not
    advance -- and /replica cannot distinguish that from health.

    The switchover must not be attempted here. Asking Patroni for a handover it
    will refuse achieves nothing and buries the real reason under a second,
    downstream error, which is precisely how the live run reported it.
    """
    report = _preflight.PreflightReport()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_returning(
                {"hostgw": 503, "hostaz": 200},
                patroni_states={
                    "hostgw": {
                        "role": "replica",
                        "timeline": 1,
                        # Patroni omits replication_state entirely on a member
                        # that has no replication connection.
                    },
                    "hostaz": {"role": "primary", "timeline": 2},
                },
            ),
        ),
        patch.object(_preflight.ssh, "run") as run,
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=0.05
        )

    entry = _entry(report)
    assert not entry.passed
    assert "not streaming" in entry.detail, entry.detail
    run.assert_not_called()


def test_a_streaming_replica_left_on_an_older_timeline_is_not_a_candidate():
    """The same shape one step further on: attached, but behind a timeline
    switch it has not yet followed. The leader's timeline is read from its own
    /patroni document, so the comparison is against the live cluster rather
    than against a constant."""
    report = _preflight.PreflightReport()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_returning(
                {"hostgw": 503, "hostaz": 200},
                patroni_states={
                    "hostgw": {
                        "role": "replica",
                        "replication_state": "streaming",
                        "timeline": 1,
                    },
                    "hostaz": {"role": "primary", "timeline": 2},
                },
            ),
        ),
        patch.object(_preflight.ssh, "run") as run,
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=0.05
        )

    entry = _entry(report)
    assert not entry.passed
    assert "timeline" in entry.detail, entry.detail
    run.assert_not_called()


def test_a_streaming_correctly_timelined_replica_can_still_not_be_a_quorum_member():
    """Regression on experiment-20260909T043036Z.log, one step further than the
    timeline test above: gcp-1 answered /replica 200, was streaming, and was on
    the leader's timeline -- both prior gates passed -- and the switchover still
    failed with `503, Switchover failed`. The cluster table printed with that
    failure showed gcp-1 as `Role: Replica`, not `Quorum Standby`, while every
    other survivor was: it had not yet been admitted to
    synchronous_standby_names, which Patroni tracks on its own loop_wait
    cadence, independently of whether the node is caught up. The switchover must
    not be attempted on a candidate /quorum does not yet accept."""
    report = _preflight.PreflightReport()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_returning(
                {"hostgw": 503, "hostaz": 200},
                quorum_statuses={"hostgw": 503},
            ),
        ),
        patch.object(_preflight.ssh, "run") as run,
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=0.05
        )

    entry = _entry(report)
    assert not entry.passed
    assert "quorum" in entry.detail.lower(), entry.detail
    run.assert_not_called()


def test_the_wait_holds_for_quorum_membership_after_streaming_is_already_true():
    """The candidate can flip to streaming-on-timeline well before Patroni
    admits it to the synchronous set; the wait must span both transitions, not
    just the first."""
    quorum_states = iter([{"hostgw": 503}, {"hostgw": 503}, {"hostgw": 200}])
    primary_states = {"phase": {"hostgw": 503, "hostaz": 200}}
    switched = {"done": False}

    def _urlopen(url, timeout=None):
        if url.endswith("/replica"):
            return _FakeResponse(200 if primary_states["phase"].get("hostgw") != 200 else 503)
        if url.endswith("/patroni"):
            leading = primary_states["phase"].get("hostgw") == 200
            return _FakeResponse(200, _LEADING if leading else _STREAMING)
        if url.endswith("/quorum"):
            return _FakeResponse(next(quorum_states).get("hostgw", 503))
        for host, code in primary_states["phase"].items():
            if f"//{host}:" in url:
                return _FakeResponse(code)
        raise AssertionError(url)

    def _switch(node, command, timeout=None):
        switched["done"] = True
        primary_states["phase"] = {"hostgw": 200, "hostaz": 503}
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = _preflight.PreflightReport()
    with (
        patch("urllib.request.urlopen", side_effect=_urlopen),
        patch.object(_preflight.ssh, "run", side_effect=_switch),
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=30
        )

    entry = _entry(report)
    assert entry.passed, entry.detail
    assert switched["done"], "the wait must end in the explicit switchover"


def test_an_ineligible_candidate_still_fails_once_the_window_expires():
    """The window is a wait, not a loosening: the condition is unchanged, and
    the reason Patroni last gave is reported rather than a bare timeout."""
    report = _preflight.PreflightReport()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_returning(
                {"hostgw": 503, "hostaz": 200}, replica_statuses={"hostgw": 503}
            ),
        ),
        patch.object(_preflight.ssh, "run") as run,
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 0.0),
    ):
        _preflight.check_patroni_primary_placement(
            report, _PLACEMENT_TOPO, settle_timeout_s=0.05
        )

    entry = _entry(report)
    assert not entry.passed
    assert "not a switchover candidate" in entry.detail
    assert "503" in entry.detail
    # Asking Patroni for a handover it will refuse achieves nothing and muddies
    # the failure with a second, downstream error message.
    run.assert_not_called()


def test_bench_and_net_probe_still_fail_fast_with_no_settle_window():
    """Default 0 means one reading, exactly as before. Only the chaos phases
    pass a window, mirroring check_leaseholder_placement."""
    report = _preflight.PreflightReport()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_returning(
                {"hostgw": 503, "hostaz": 200}, replica_statuses={"hostgw": 503}
            ),
        ),
        patch.object(_preflight.ssh, "run") as run,
        patch.object(_preflight, "PATRONI_CANDIDATE_POLL_S", 60.0),
    ):
        # Would hang for a minute per poll if a window were being applied.
        _preflight.check_patroni_primary_placement(report, _PLACEMENT_TOPO)

    assert not _entry(report).passed
    run.assert_not_called()


def test_a_switchover_that_does_not_take_fails_rather_than_reporting_success():
    # The far node stays primary however many times it is asked. A repair that
    # silently failed would hand the next phase the wrong fault target.
    report, _ = _placement_report(
        {"hostgw": 503, "hostaz": 200},
        ssh_result=SimpleNamespace(returncode=1, stdout="", stderr="switchover failed"),
    )
    entry = _entry(report)
    assert not entry.passed
    assert "did not take" in entry.detail


def test_no_primary_at_all_fails_the_check_rather_than_raising():
    # resolve_patroni_primary raises here; the check has to turn that into a
    # failed pre-flight entry so the report names it alongside everything else.
    report, run = _placement_report({"hostgw": 503, "hostaz": 503})
    entry = _entry(report)
    assert not entry.passed
    assert "no cluster member" in entry.detail
    run.assert_not_called()

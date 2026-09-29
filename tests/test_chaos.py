"""Tests for Phases III-IV recovery detection."""

from __future__ import annotations

import pytest

from crdblab.phases.p4_chaos import find_recovery

BASELINE = 1000.0
THRESHOLD = 0.8   # floor of 800 tps
HOLD = 5.0


def _series(values, start=0.0, step=1.0):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_rto_is_the_start_of_the_sustained_window_not_its_end():
    """The hold qualifies the recovery; it must not postpone the timestamp."""
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
    """A run that ends mid-window must report no recovery rather than guess."""
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
    """An audit writer that stopped observing reports no RTO, not a tiny one."""
    acks = [(24.792 + 0.39 * i, "ack") for i in range(-40, 10)]
    a = _attempts(acks)
    r = availability_rto(a, fault_monotonic=24.792, observation_end=53.8)

    assert r["availability_rto_s"] is None, "silence must not be reported as recovery"
    assert r["coverage_truncated"] is True
    assert r["coverage_gap_s"] > 20
    assert "unmeasured, not zero" in r["detail"]


def test_a_writer_still_blocked_at_the_end_of_the_run_is_not_counted_as_covering_it():
    """Coverage is judged on acknowledgements, not on attempts."""
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


# --- Patroni primary resolution ---
# Nothing pins Patroni's leader, so the primary is resolved live before the fault.

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


#: A healthy replica's ``/patroni`` fields: streaming, on the leader's timeline.
_STREAMING = {"role": "replica", "replication_state": "streaming", "timeline": 2}
_LEADING = {"role": "primary", "timeline": 2}


def _urlopen_returning(
    statuses_by_host, replica_statuses=None, patroni_states=None, quorum_statuses=None
):
    """Build a fake ``urlopen`` keyed on the host embedded in the URL."""

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


# --- fault authorisation ---
# A denied fault yields a complete run of an undisturbed cluster; it must be caught.

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
    """The PostgreSQL dead fault disables restarts with a drop-in (set-property cannot set
    Restart=).
    """
    payload = get_payload("dead", "postgresql")
    assert "set-property" not in payload
    assert "patroni.service.d" in payload and "daemon-reload" in payload


def test_the_postgresql_dead_fault_cannot_be_undone_by_systemd():
    """Patroni's Restart=on-failure would undo the kill in ~100 ms, so restarts are
    disabled first.
    """
    payload = get_payload("dead", "postgresql")
    assert "Restart=no" in payload
    assert payload.index("Restart=no") < payload.index("kill")


def test_the_postgresql_dead_fault_signals_the_unit_not_a_process_name():
    """The kill signals the unit's cgroup; Patroni's process name is python3, not patroni."""
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
    # Both use the constant, so the fault and restore cannot drift onto different files.
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
    # The reason must survive into events.json; "rc=1" alone is unreadable later.
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


# --- post-fault observation ---
# The run must outlive the fault, and the generator must tolerate errors.

from crdblab.config import ChaosSpec, Profile
from crdblab.phases.p4_chaos import (
    RECOVER_HEAL_DELAY_S,
    generator_duration_s,
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
    """In recover mode the post-fault window is counted from the heal, not from the fault."""
    chaos = ChaosSpec(duration_s=45, inject_at_s=15, min_post_fault_s=20)
    assert generator_duration_s(chaos, "dead") == 45
    assert generator_duration_s(chaos, "recover") == 15 + RECOVER_HEAL_DELAY_S + 20


def test_the_smoke_profile_can_actually_observe_a_recover_recovery():
    """The smoke profile runs long enough to observe a recover-mode recovery."""
    chaos = Profile.load("smoke").chaos
    heal_at = chaos.inject_at_s + RECOVER_HEAL_DELAY_S
    assert generator_duration_s(chaos, "recover") >= heal_at + 30


def test_thesis_extended_recover_run_is_long_enough_to_settle():
    """thesis-extended runs long enough after the fault for the settled-state test to resolve."""
    chaos = Profile.load("thesis-extended").chaos
    assert chaos.min_post_fault_s == 900
    assert generator_duration_s(chaos, "recover") == 60 + RECOVER_HEAL_DELAY_S + 900


def test_thesis_extended_dead_run_is_long_enough_to_settle():
    chaos = Profile.load("thesis-extended").chaos
    assert generator_duration_s(chaos, "dead") == 60 + 900


def test_thesis_recover_run_is_long_enough_to_settle():
    """thesis runs long enough after the fault for the settled-state test to resolve."""
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


# --- restoring the dead target ---
# The restart needs sudo, and liveness is read from a survivor, not the target.

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


# --- Patroni primary placement ---
# Patroni never fails back, so the primary is repaired by switchover, not waited for.

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
        # Patroni names members by hostname (`crdb-gcp-1`), not the short Node.name.
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
    """Phase IV must survive following Phase III on the PostgreSQL arm."""
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
    """A replica answering /replica 200 but not streaming is not a switchover candidate."""
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
    """A streaming replica on an older timeline than the leader is not a candidate."""
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
    """A streaming replica on the right timeline is not a candidate until it is a quorum member."""
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

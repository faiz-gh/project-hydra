"""Tests for Phase I substrate characterisation.

The fixtures inline below are verbatim ``ping`` output from the testbed on
2026-09-02, retained because the precision behaviour they encode is not obvious
and was found by inspection of an implausible result rather than anticipated.
"""

from __future__ import annotations

import pytest

from crdblab.core.preflight import quorum_floor_ms
from crdblab.phases.p1_network import _quantile, parse_ping

# 186 ms link: ping prints whole milliseconds at this magnitude.
AZURE = """64 bytes from crdb-azure-1: icmp_seq=1 ttl=64 time=186 ms
64 bytes from crdb-azure-1: icmp_seq=2 ttl=64 time=186 ms
64 bytes from crdb-azure-1: icmp_seq=3 ttl=64 time=187 ms
3 packets transmitted, 3 received, 0% packet loss, time 402ms
rtt min/avg/max/mdev = 185.625/185.726/185.868/0.070 ms, pipe 2"""

# 25 ms link: one decimal at this magnitude.
GCP = """64 bytes from crdb-gcp-1: icmp_seq=1 ttl=64 time=25.5 ms
64 bytes from crdb-gcp-1: icmp_seq=2 ttl=64 time=25.4 ms
64 bytes from crdb-gcp-1: icmp_seq=3 ttl=64 time=25.5 ms
3 packets transmitted, 3 received, 0% packet loss, time 402ms
rtt min/avg/max/mdev = 25.281/25.470/25.741/0.113 ms"""


def test_summary_line_supplies_precision_the_packet_lines_lack():
    """Central statistics must not inherit the per-packet print resolution.

    Recomputing the mean from lines reading ``time=186 ms`` yields 186.333 and a
    deviation of 0.0, which would present a hundred-sample intercontinental link
    as perfectly stable. ping's own summary line carries three decimals at any
    magnitude and is the correct source.
    """
    s = parse_ping(AZURE)
    assert s.rtt_min_ms == pytest.approx(185.625)
    assert s.rtt_mean_ms == pytest.approx(185.726)
    assert s.rtt_max_ms == pytest.approx(185.868)
    # The figure that would otherwise have been a spurious 0.0.
    assert s.rtt_mdev_ms == pytest.approx(0.070)
    assert s.rtt_mdev_ms > 0


def test_per_packet_resolution_is_recorded_not_implied():
    """Quantiles come from the packet lines, so their precision is link-dependent."""
    assert parse_ping(AZURE).rtt_resolution_ms == pytest.approx(1.0)
    assert parse_ping(GCP).rtt_resolution_ms == pytest.approx(0.1)


def test_quantiles_are_not_rounded_beyond_their_resolution():
    azure = parse_ping(AZURE)
    assert azure.rtt_p50_ms == pytest.approx(186.0)
    gcp = parse_ping(GCP)
    assert gcp.rtt_p50_ms == pytest.approx(25.5)


def test_unreachable_destination_is_recorded_not_dropped():
    """A dead link must appear in the matrix, not vanish from it."""
    s = parse_ping("5 packets transmitted, 0 received, 100% packet loss")
    assert s.samples == 0
    assert s.loss_pct == pytest.approx(100.0)
    assert s.rtt_mean_ms is None


def test_missing_summary_line_falls_back_rather_than_failing():
    s = parse_ping(
        "time=10.5 ms\ntime=11.5 ms\n2 packets transmitted, 2 received, 0% packet loss"
    )
    assert s.rtt_mean_ms == pytest.approx(11.0)


def test_nearest_rank_quantile_is_exact_at_the_boundaries():
    """The legacy `int(count * q) - 1` indexing was off by one for small samples."""
    ordered = [float(i) for i in range(1, 101)]
    assert _quantile(ordered, 0.95) == 95.0
    assert _quantile(ordered, 0.99) == 99.0
    assert _quantile(ordered, 1.0) == 100.0
    assert _quantile([1.0], 0.99) == 1.0


# --- the quorum floor, which is what Phase I exists to produce --------------

def test_quorum_floor_is_the_ack_that_completes_the_majority():
    """Five voters need the leader plus two followers, so the second-fastest binds.

    Measured RTTs from the gateway, taken from
    runs/20260902T152535Z_p1-network/network.csv (the crdb-gcp-1 source rows,
    the gateway since the CPU confound was removed). The floor of ~69 ms is what
    makes a reported 3.1 ms write latency detectably impossible rather than
    merely surprising (docs/defects.md, D8).

    The floor moved from 70.6 ms to 68.8 ms when the gateway moved from
    crdb-linode-1 to crdb-gcp-1, because it is a property of the *gateway's* view
    of the cluster and not of the cluster alone. Two percent is immaterial to
    D8's argument -- 3.1 ms is impossible against either -- but the number is
    re-read from the matrix rather than carried over, because a floor quoted from
    the wrong vantage point is the kind of stale constant this project keeps
    finding.
    """
    rtts = {
        "crdb-linode-1": 23.7,
        "crdb-linode-2": 68.8,
        "crdb-azure-2": 197.6,
        "crdb-azure-1": 218.5,
    }
    assert quorum_floor_ms(rtts, voters=5) == pytest.approx(68.8)
    # Three voters need only one follower ack, so the fastest binds.
    assert quorum_floor_ms(rtts, voters=3) == pytest.approx(23.7)


def test_quorum_floor_rejects_a_configuration_that_cannot_form_a_majority():
    with pytest.raises(ValueError):
        quorum_floor_ms({"a": 1.0}, voters=2)
    with pytest.raises(ValueError):
        quorum_floor_ms({"a": 1.0}, voters=5)

"""Tests for the node_exporter poller in crdblab/core/hardware_metrics.py.

node_exporter exposes raw counters, not rates, so every rate this module
reports depends on differencing two scrapes -- these tests pin the exact
formulas and the "no prior scrape yet" edge case (D5: an unmeasured quantity
must never be indistinguishable from one measured as zero).
"""

from __future__ import annotations

import csv
from unittest.mock import patch

import pytest

from crdblab.core.hardware_metrics import HardwareMetricsSampler, _parse
from crdblab.core.recorder import HARDWARE_METRICS_COLUMNS
from crdblab.topology import Node

_NODE = Node("n", "h", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")

#: Two scrapes of the same node, five seconds apart, with known deltas.
#: `lo`'s counters jump by a huge amount between the two bodies so that a
#: rate calculation that wrongly includes it is unmistakably wrong rather
#: than merely off by a rounding error.
_BODY_1 = """\
# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 50.0
node_cpu_seconds_total{cpu="0",mode="user"} 25.0
node_cpu_seconds_total{cpu="1",mode="idle"} 50.0
node_cpu_seconds_total{cpu="1",mode="user"} 25.0
node_memory_MemTotal_bytes 4294967296
node_memory_MemAvailable_bytes 2147483648
node_disk_read_bytes_total{device="sda"} 1000
node_disk_written_bytes_total{device="sda"} 2000
node_disk_io_time_seconds_total{device="sda"} 10.0
node_network_receive_bytes_total{device="lo"} 100000
node_network_receive_bytes_total{device="eth0"} 500
node_network_transmit_bytes_total{device="lo"} 100000
node_network_transmit_bytes_total{device="eth0"} 300
node_load1 0.10
node_filesystem_avail_bytes{device="/dev/sda1"} 999999999
"""

_BODY_2 = """\
node_cpu_seconds_total{cpu="0",mode="idle"} 70.0
node_cpu_seconds_total{cpu="0",mode="user"} 30.0
node_cpu_seconds_total{cpu="1",mode="idle"} 70.0
node_cpu_seconds_total{cpu="1",mode="user"} 30.0
node_memory_MemTotal_bytes 4294967296
node_memory_MemAvailable_bytes 2000000000
node_disk_read_bytes_total{device="sda"} 1500
node_disk_written_bytes_total{device="sda"} 2500
node_disk_io_time_seconds_total{device="sda"} 15.0
node_network_receive_bytes_total{device="lo"} 999999
node_network_receive_bytes_total{device="eth0"} 800
node_network_transmit_bytes_total{device="lo"} 999999
node_network_transmit_bytes_total{device="eth0"} 360
node_load1 0.20
"""


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_parses_the_metric_families_this_project_reads_and_ignores_the_rest():
    families = _parse(_BODY_1)
    assert "node_filesystem_avail_bytes" not in families
    assert set(families) == {
        "node_cpu_seconds_total",
        "node_memory_MemTotal_bytes",
        "node_memory_MemAvailable_bytes",
        "node_disk_read_bytes_total",
        "node_disk_written_bytes_total",
        "node_disk_io_time_seconds_total",
        "node_network_receive_bytes_total",
        "node_network_transmit_bytes_total",
        "node_load1",
    }
    assert len(families["node_cpu_seconds_total"]) == 4
    assert families["node_load1"] == [({}, 0.10)]


def test_the_first_sample_per_node_has_no_rate_yet():
    """No prior scrape to difference against: rates are '', not 0 or NaN."""
    sampler = HardwareMetricsSampler([_NODE], t_zero=0.0)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_BODY_1)):
        row = sampler._sample_node(_NODE)

    assert row is not None
    for col in (
        "cpu_busy_pct",
        "disk_read_bytes_per_s",
        "disk_write_bytes_per_s",
        "disk_busy_pct",
        "net_rx_bytes_per_s",
        "net_tx_bytes_per_s",
    ):
        assert row[col] == "", col

    # Gauges and raw cumulative counters have no such gap: the first scrape
    # already has them.
    assert row["mem_total_bytes"] == 4294967296.0
    assert row["load1"] == 0.10
    assert row["cpu_seconds_idle_cum"] == 100.0
    assert row["cpu_seconds_total_cum"] == 150.0


def test_cpu_busy_pct_is_one_minus_idle_over_total_across_all_cores():
    sampler = HardwareMetricsSampler([_NODE], t_zero=0.0)
    responses = iter([_FakeResponse(_BODY_1), _FakeResponse(_BODY_2)])
    with (
        patch("urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)),
        patch("crdblab.core.hardware_metrics.time.monotonic", side_effect=[0.0, 5.0]),
    ):
        sampler._sample_node(_NODE)
        row = sampler._sample_node(_NODE)

    # idle delta = 40 (100 -> 140), total delta = 50 (150 -> 200):
    # busy% = 100 * (1 - 40/50) = 20.0
    assert row["cpu_busy_pct"] == pytest.approx(20.0)


def test_disk_and_network_rates_are_deltas_over_elapsed_time():
    sampler = HardwareMetricsSampler([_NODE], t_zero=0.0)
    responses = iter([_FakeResponse(_BODY_1), _FakeResponse(_BODY_2)])
    with (
        patch("urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)),
        patch("crdblab.core.hardware_metrics.time.monotonic", side_effect=[0.0, 5.0]),
    ):
        sampler._sample_node(_NODE)
        row = sampler._sample_node(_NODE)

    assert row["disk_read_bytes_per_s"] == 100.0  # (1500-1000)/5
    assert row["disk_write_bytes_per_s"] == 100.0  # (2500-2000)/5
    assert row["disk_busy_pct"] == 100.0  # 100 * (15.0-10.0)/5
    assert row["wall_offset_s"] == 5.0  # t_zero=0.0


def test_network_rate_excludes_loopback():
    """`lo`'s counters jump by ~900000 between scrapes; a correct rate (from
    eth0 alone) is 60.0 and 12.0 bytes/s. Anything near 180000/12.0 means `lo`
    leaked into the sum."""
    sampler = HardwareMetricsSampler([_NODE], t_zero=0.0)
    responses = iter([_FakeResponse(_BODY_1), _FakeResponse(_BODY_2)])
    with (
        patch("urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)),
        patch("crdblab.core.hardware_metrics.time.monotonic", side_effect=[0.0, 5.0]),
    ):
        sampler._sample_node(_NODE)
        row = sampler._sample_node(_NODE)

    assert row["net_rx_bytes_per_s"] == 60.0  # (800-500)/5, eth0 only
    assert row["net_tx_bytes_per_s"] == 12.0  # (360-300)/5, eth0 only


def test_a_node_that_fails_to_scrape_does_not_block_the_others():
    good = Node("good", "goodhost", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")
    bad = Node("bad", "badhost", "ubuntu", "gcp", "us-east1", "cloud=gcp,region=us-east1")
    sampler = HardwareMetricsSampler([good, bad], t_zero=0.0)

    def fake_urlopen(url, timeout=None):
        if "badhost" in url:
            raise OSError("connection refused")
        return _FakeResponse(_BODY_1)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        row_good = sampler._sample_node(good)
        row_bad = sampler._sample_node(bad)

    assert row_good is not None
    assert row_bad is None
    assert sampler.scrape_failures["good"] == 0
    assert sampler.scrape_failures["bad"] == 1


def test_hardware_metrics_columns_round_trip_through_metricswriter(tmp_path):
    """Guards against the sampler's row-builder drifting out of sync with the
    declared schema -- MetricsWriter raises ValueError on any mismatch."""
    sampler = HardwareMetricsSampler([_NODE], t_zero=0.0)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_BODY_1)):
        row = sampler._sample_node(_NODE)
    sampler.rows.append(row)

    path = tmp_path / "hardware_metrics.csv"
    sampler.write(path)  # raises if `row`'s keys don't match HARDWARE_METRICS_COLUMNS

    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert set(rows[0].keys()) == set(HARDWARE_METRICS_COLUMNS)

"""Per-node CPU/memory/disk/network utilisation, polled from node_exporter.

Every node in this testbed (5 cluster members + the client node) runs the
Ubuntu-packaged ``prometheus-node-exporter`` on its default port, ``:9100``
(see ``terraform/scripts/bootstrap-{cockroachdb,patroni,client}.tftpl``).
This module polls it directly over HTTP -- the same idiom
``core/preflight.py`` already uses for Patroni's ``:8008`` REST API -- rather
than through a standalone Prometheus server; there is no long-lived service
between runs, and every scraped row lands in the measured run's own
directory, consistent with this project's "every figure traces back to
retained raw output on disk" design commitment.

node_exporter exposes raw, monotonic *counters* (bytes transferred, seconds
busy), not pre-normalised rates -- unlike CockroachDB's own ``/_status/vars``
endpoint that ``phases/bench.py``'s (currently dead) ``HostSampler`` expects.
Turning a counter into a rate needs two scrapes, so :class:`HardwareMetricsSampler`
keeps the previous scrape's counters per node and differences them; a node's
first observed poll therefore has no rate to report, and is written as an
empty string rather than 0 or NaN, distinguishing "not yet measured" from
"measured as zero" (D5).
"""

from __future__ import annotations

import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ..topology import Node
from .recorder import HARDWARE_METRICS_COLUMNS, MetricsWriter, utcnow

#: Matches one Prometheus text-exposition line for a `node_*` metric, with or
#: without a label set. Deliberately narrower than the full exposition
#: grammar (no ``# HELP``/``# TYPE`` handling beyond skipping comment lines,
#: no ``+Inf``/quantile support) -- only the ~9 families this project reads
#: are ever matched, out of the hundreds node_exporter exposes, so a minimal
#: hand-rolled parser is simpler to audit than pulling in a general-purpose
#: one for cases that never arise here.
_LINE_RE = re.compile(
    r"^(?P<name>node_[A-Za-z0-9_]+)(\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)\s*$"
)
_LABEL_RE = re.compile(r'(?P<key>[A-Za-z0-9_]+)="(?P<value>[^"]*)"')

#: The only metric families this project reads. Everything else in a scrape
#: body (filesystem, systemd, textfile-collector metrics, ...) is ignored.
_WANTED = frozenset(
    {
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
)

#: name -> [(labels, value), ...], unaggregated. Kept ungrouped until the
#: caller combines per-core/per-device series, because CPU is summed across
#: every core+mode, disk is summed across every device, and network is summed
#: across every device except `lo` -- three different aggregation rules,
#: applied by the sampler, not here.
_Families = dict[str, list[tuple[dict[str, str], float]]]


def _parse_labels(text: str) -> dict[str, str]:
    return {m.group("key"): m.group("value") for m in _LABEL_RE.finditer(text)}


def _parse(body: str) -> _Families:
    families: _Families = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        if name not in _WANTED:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        families.setdefault(name, []).append(
            (_parse_labels(match.group("labels") or ""), value)
        )
    return families


def _sum_family(
    families: _Families,
    name: str,
    label_filter: Callable[[dict[str, str]], bool] | None = None,
) -> float:
    return sum(
        value
        for labels, value in families.get(name, [])
        if label_filter is None or label_filter(labels)
    )


def _gauge(families: _Families, name: str) -> float | None:
    values = families.get(name, [])
    return values[0][1] if values else None


@dataclass
class _RawCounters:
    """One node's cumulative counters at one scrape, plus when it was taken."""

    ts: float  # time.monotonic()
    cpu_idle: float
    cpu_total: float
    disk_read: float
    disk_write: float
    disk_io_time: float
    net_rx: float
    net_tx: float


def _not_loopback(labels: dict[str, str]) -> bool:
    return labels.get("device") != "lo"


def _extract_counters(families: _Families) -> _RawCounters:
    cpu_samples = families.get("node_cpu_seconds_total", [])
    cpu_idle = sum(v for labels, v in cpu_samples if labels.get("mode") == "idle")
    cpu_total = sum(v for _, v in cpu_samples)
    return _RawCounters(
        ts=time.monotonic(),
        cpu_idle=cpu_idle,
        cpu_total=cpu_total,
        disk_read=_sum_family(families, "node_disk_read_bytes_total"),
        disk_write=_sum_family(families, "node_disk_written_bytes_total"),
        disk_io_time=_sum_family(families, "node_disk_io_time_seconds_total"),
        net_rx=_sum_family(families, "node_network_receive_bytes_total", _not_loopback),
        net_tx=_sum_family(families, "node_network_transmit_bytes_total", _not_loopback),
    )


def _rate(curr: float, prev: float, dt: float) -> float | str:
    return (curr - prev) / dt if dt > 0 else ""


class HardwareMetricsSampler:
    """Polls every node's node_exporter in the background; writes one CSV at the end.

    Modeled on ``AuditWriter``'s "collect into memory, write the CSV once at
    the end" shape (``phases/p4_chaos.py``) rather than ``HostSampler``'s
    per-tick-blended-column shape (``phases/bench.py``): hardware metrics is
    its own table, not a column blended into an existing per-tick row, so
    nothing in the phase's own tick loop needs to read it live.

    A node's scrape failure (timeout, connection refused, malformed body)
    increments :attr:`scrape_failures` for that node and produces no row for
    that tick; it never blocks or corrupts another node's row, the same
    isolation ``phases/p1_network.py``'s per-node fan-out already relies on.
    """

    def __init__(
        self,
        nodes: Sequence[Node],
        t_zero: float,
        interval_s: float = 5.0,
        timeout_s: float = 2.0,
    ) -> None:
        self._nodes = list(nodes)
        self._t_zero = t_zero
        self._interval = interval_s
        self._timeout = timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._prev: dict[str, _RawCounters] = {}
        self.rows: list[dict[str, Any]] = []
        self.scrape_failures: dict[str, int] = {n.name: 0 for n in self._nodes}
        #: Incremented if a whole tick fails outside of a single node's own
        #: scrape (e.g. the thread pool itself raising) -- distinct from
        #: ``scrape_failures``, which is per-node and covers the common case.
        self.tick_failures = 0

    def _sample_node(self, node: Node) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(node.node_exporter_url, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except Exception:
            self.scrape_failures[node.name] += 1
            return None

        families = _parse(body)
        counters = _extract_counters(families)
        prev = self._prev.get(node.name)
        self._prev[node.name] = counters

        if prev is not None:
            dt = counters.ts - prev.ts
            cpu_total_delta = counters.cpu_total - prev.cpu_total
            cpu_busy_pct: float | str = (
                100.0 * (1.0 - (counters.cpu_idle - prev.cpu_idle) / cpu_total_delta)
                if cpu_total_delta > 0
                else ""
            )
            disk_read_rate = _rate(counters.disk_read, prev.disk_read, dt)
            disk_write_rate = _rate(counters.disk_write, prev.disk_write, dt)
            disk_busy_pct = (
                100.0 * (counters.disk_io_time - prev.disk_io_time) / dt if dt > 0 else ""
            )
            net_rx_rate = _rate(counters.net_rx, prev.net_rx, dt)
            net_tx_rate = _rate(counters.net_tx, prev.net_tx, dt)
        else:
            # First observed poll for this node: nothing to difference against.
            cpu_busy_pct = disk_read_rate = disk_write_rate = disk_busy_pct = ""
            net_rx_rate = net_tx_rate = ""

        mem_total = _gauge(families, "node_memory_MemTotal_bytes")
        mem_available = _gauge(families, "node_memory_MemAvailable_bytes")
        load1 = _gauge(families, "node_load1")

        return {
            "ts_utc": utcnow(),
            "wall_offset_s": round(counters.ts - self._t_zero, 3),
            "node": node.name,
            "host": node.host,
            "cpu_busy_pct": cpu_busy_pct,
            "cpu_seconds_idle_cum": counters.cpu_idle,
            "cpu_seconds_total_cum": counters.cpu_total,
            "mem_total_bytes": mem_total if mem_total is not None else "",
            "mem_available_bytes": mem_available if mem_available is not None else "",
            "disk_read_bytes_per_s": disk_read_rate,
            "disk_write_bytes_per_s": disk_write_rate,
            "disk_busy_pct": disk_busy_pct,
            "disk_read_bytes_cum": counters.disk_read,
            "disk_write_bytes_cum": counters.disk_write,
            "disk_io_time_seconds_cum": counters.disk_io_time,
            "net_rx_bytes_per_s": net_rx_rate,
            "net_tx_bytes_per_s": net_tx_rate,
            "net_rx_bytes_cum": counters.net_rx,
            "net_tx_bytes_cum": counters.net_tx,
            "load1": load1 if load1 is not None else "",
        }

    def _tick(self) -> None:
        assert self._pool is not None
        for row in self._pool.map(self._sample_node, self._nodes):
            if row is not None:
                self.rows.append(row)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._tick()
            except Exception:
                self.tick_failures += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))

    def __enter__(self) -> "HardwareMetricsSampler":
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(self._nodes)))
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
        if self._pool is not None:
            self._pool.shutdown(wait=False)

    def write(self, path: Path) -> None:
        with MetricsWriter(path, HARDWARE_METRICS_COLUMNS) as writer:
            writer.write_many(self.rows)

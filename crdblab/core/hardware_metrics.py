"""Per-node CPU/memory/disk/network utilisation, polled from node_exporter.

Every node runs ``prometheus-node-exporter`` on ``:9100``; it is polled
directly over HTTP. node_exporter exposes monotonic counters, so rates come
from differencing consecutive scrapes, and a node's first poll has no rate
(written as ``""``, not 0).
"""

from __future__ import annotations

import re
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..topology import Node
from .recorder import HARDWARE_METRICS_COLUMNS, MetricsWriter, utcnow

#: One Prometheus text line for a ``node_*`` metric. A minimal parser is enough
#: for the few families read here.
_LINE_RE = re.compile(
    r"^(?P<name>node_[A-Za-z0-9_]+)(\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)\s*$"
)
_LABEL_RE = re.compile(r'(?P<key>[A-Za-z0-9_]+)="(?P<value>[^"]*)"')

#: The only metric families read; everything else in a scrape is ignored.
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

#: name -> [(labels, value), ...], unaggregated; the sampler applies
#: per-family aggregation (all cores, all disks, all non-loopback NICs).
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

    A failed scrape counts against that node in :attr:`scrape_failures` and
    produces no row for that tick, without affecting other nodes.
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
        #: Ticks that failed as a whole, outside any single node's scrape.
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

    def __enter__(self) -> HardwareMetricsSampler:
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

"""Measurement schemas, the run manifest, and a schema-enforcing CSV writer.

Tables are long (one row per interval and operation type), and no measurement
is written without a manifest recording code revision, profile and topology.
"""

from __future__ import annotations

import csv
import json
import platform
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.1"

#: Workload metrics, one row per (interval, op). Derived quantities are computed
#: in the analysis layer, not stored.
COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "elapsed_s",
    # Harness monotonic clock offset (vs the generator's own ``elapsed_s``).
    # Absent in schema-2.0 runs.
    "wall_offset_s",
    "concurrency",
    "repetition",
    "op",
    "tps",
    "tps_cum",
    "errors_cum",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "pmax_ms",
    "gateway_cpu_pct",
    "gateway_disk_iops",
    "gateway_rss_bytes",
)

_REQUIRED = frozenset(COLUMNS)

#: Phase I RTT matrix. Quantiles come from ping's per-packet lines, whose precision
#: drops as RTT grows, so ``rtt_resolution_ms`` records the precision available.
NETWORK_COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "source",
    "destination",
    "source_region",
    "destination_region",
    "samples",
    "loss_pct",
    "rtt_min_ms",
    "rtt_mean_ms",
    "rtt_p50_ms",
    "rtt_p95_ms",
    "rtt_p99_ms",
    "rtt_max_ms",
    "rtt_mdev_ms",
    "rtt_resolution_ms",
)


#: RPO audit log: one row per attempted write, outcome ``ack``, ``ambiguous``
#: or ``refused``. Only an acknowledged write later found missing is data loss.
AUDIT_COLUMNS: tuple[str, ...] = (
    "wall_offset_s",
    "seq_id",
    "outcome",
)


#: RTO probe log: one row per canary write. The completion offset of a write that
#: blocked through an outage marks the instant service resumed.
PROBE_COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "seq_id",
    "dispatch_offset_s",
    "complete_offset_s",
    "duration_ms",
    "outcome",
    "worker",
    "detail",
)

#: ``ok`` means served; ``timeout`` is the outage signature; ``conn_error`` is a
#: broken connection; ``refused`` is a probe bug, not downtime.
PROBE_OUTCOMES: tuple[str, ...] = ("ok", "timeout", "conn_error", "refused")


#: Per-node utilisation from node_exporter, one row per (node, poll). Rates sit beside
#: their raw counters and are ``""`` on a node's first poll (nothing to diff).
HARDWARE_METRICS_COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "wall_offset_s",
    "node",
    "host",
    "cpu_busy_pct",
    "cpu_seconds_idle_cum",
    "cpu_seconds_total_cum",
    "mem_total_bytes",
    "mem_available_bytes",
    "disk_read_bytes_per_s",
    "disk_write_bytes_per_s",
    "disk_busy_pct",
    "disk_read_bytes_cum",
    "disk_write_bytes_cum",
    "disk_io_time_seconds_cum",
    "net_rx_bytes_per_s",
    "net_tx_bytes_per_s",
    "net_rx_bytes_cum",
    "net_tx_bytes_cum",
    "load1",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utcnow_us() -> str:
    """UTC timestamp at microsecond resolution, for probe events."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def new_run_id(phase: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{phase}"


def _git_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except Exception:
        return None


@dataclass
class Manifest:
    """Everything needed to reproduce or contextualise a single run."""

    run_id: str
    phase: str
    schema_version: str = SCHEMA_VERSION
    #: "cockroachdb" or "postgresql"; older runs default to cockroachdb.
    engine: str = "cockroachdb"
    started_utc: str = field(default_factory=utcnow)
    finished_utc: str | None = None
    git_revision: str | None = field(default_factory=_git_revision)
    profile: dict[str, Any] = field(default_factory=dict)
    topology: list[dict[str, Any]] = field(default_factory=list)
    #: Wall-clock instant of the run's monotonic zero: the origin of
    #: ``wall_offset_s`` and every offset in ``events.json``.
    clock_epoch_utc: str | None = None
    #: Measured server version for either engine; ``cockroach_version`` is kept
    #: for older runs that only recorded that.
    server_version: str | None = None
    cockroach_version: str | None = None
    generator_command: str | None = None
    ssh_options: list[str] = field(default_factory=list)
    client_platform: str = field(default_factory=platform.platform)
    notes: list[str] = field(default_factory=list)
    generator_totals: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)

    def note(self, message: str) -> None:
        self.notes.append(f"{utcnow()} {message}")


class RunDirectory:
    """An immutable, self-describing output directory for one run."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.path = Path(root) / run_id
        if self.path.exists():
            raise FileExistsError(
                f"{self.path} already exists; run directories are immutable by design"
            )
        (self.path / "raw").mkdir(parents=True)

    @property
    def metrics_csv(self) -> Path:
        return self.path / "metrics.csv"

    @property
    def manifest_json(self) -> Path:
        return self.path / "manifest.json"

    @property
    def events_json(self) -> Path:
        return self.path / "events.json"

    @property
    def network_csv(self) -> Path:
        """Phase I round-trip matrix, written under :data:`NETWORK_COLUMNS`."""
        return self.path / "network.csv"

    @property
    def audit_csv(self) -> Path:
        """Phase III/IV audit attempt log, written under :data:`AUDIT_COLUMNS`."""
        return self.path / "audit.csv"

    @property
    def probe_csv(self) -> Path:
        """High-frequency RTO probe attempts, written under :data:`PROBE_COLUMNS`."""
        return self.path / "rto_probe.csv"

    @property
    def probe_log(self) -> Path:
        """RTO probe connection-lifecycle log (JSONL).

        Appended and flushed as events happen, so a run killed mid-fault still
        leaves the outage edges on disk.
        """
        return self.path / "rto_probe.log"

    @property
    def preflight_json(self) -> Path:
        """Pre-flight assertions and their observed values for this run."""
        return self.path / "preflight.json"

    @property
    def hardware_metrics_csv(self) -> Path:
        """Per-node utilisation under :data:`HARDWARE_METRICS_COLUMNS`, if collected."""
        return self.path / "hardware_metrics.csv"

    def write_preflight(self, report: dict[str, Any]) -> None:
        self.preflight_json.write_text(json.dumps(report, indent=2))

    def raw(self, name: str) -> Path:
        return self.path / "raw" / name

    def write_manifest(self, manifest: Manifest) -> None:
        self.manifest_json.write_text(json.dumps(asdict(manifest), indent=2))

    def write_events(self, events: dict[str, Any]) -> None:
        self.events_json.write_text(json.dumps(events, indent=2))


class MetricsWriter:
    """Append-only CSV writer that rejects rows not matching ``columns`` exactly.

    A missing value must be written explicitly (e.g. ``""``), never omitted.
    """

    def __init__(self, path: Path, columns: tuple[str, ...] = COLUMNS) -> None:
        self.columns = columns
        self._required = frozenset(columns)
        self._fh = open(path, "w", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=list(columns))
        self._writer.writeheader()
        self._fh.flush()
        self.rows_written = 0

    def write(self, row: dict[str, Any]) -> None:
        missing = self._required - row.keys()
        extra = row.keys() - self._required
        if missing or extra:
            raise ValueError(
                f"row does not match schema {SCHEMA_VERSION}: "
                f"missing={sorted(missing)} unexpected={sorted(extra)}"
            )
        self._writer.writerow(row)
        self._fh.flush()
        self.rows_written += 1

    def write_many(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            self.write(row)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> MetricsWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

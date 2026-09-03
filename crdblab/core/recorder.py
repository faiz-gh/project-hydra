"""Canonical measurement schema, run manifest, and schema-enforcing writer.

Two properties are enforced structurally rather than by convention.

First, the measurement table is *long*, one row per (interval, operation type),
never wide. The legacy exports collapsed read and write lines into one
undifferentiated ``tps`` column, which made the later choice between summing
and averaging invisible to the analyst. Keeping the operation type as an
explicit dimension forces that choice to be made in the open, in the analysis
layer, where it can be stated in the methodology.

Second, no measurement file is written without an accompanying manifest
recording the code revision, profile, generator version and node inventory in
force at the time. A figure that cannot be traced to a known code version
cannot be defended in a viva.
"""

from __future__ import annotations

import csv
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "2.1"

#: Canonical column order. Derived quantities (error rate, degradation,
#: implied latency) are deliberately absent: they are computed in the analysis
#: layer from these primitives so that a change of definition does not require
#: re-running the experiment.
COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "elapsed_s",
    # Seconds on the harness's monotonic clock, from the run's epoch
    # (``Manifest.clock_epoch_utc``) to the moment this interval's first line was
    # read from the pipe. Distinct from ``elapsed_s``, which is the *generator's*
    # own accounting and begins only once it starts issuing operations.
    #
    # The two clocks differ by the cost of establishing the SSH session and
    # starting the process -- about 5.4 s on this testbed. Every event the
    # harness schedules, the chaos injection above all, is timed on this clock,
    # so before this column existed a fault offset in ``events.json`` and a
    # throughput series in ``metrics.csv`` could not be placed on one axis: a
    # figure drawn from both displaced the fault by an interval nobody had
    # measured. Recording both per interval makes the offset an observation
    # rather than an assumption, and makes it visible if it ever changes.
    #
    # Schema 2.0 runs predate this column. The analysis layer detects its absence
    # and reports the alignment as an interval rather than a point, instead of
    # silently substituting an estimate.
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
    # Resident set size of the CockroachDB process on the node the generator
    # targets, in bytes. Recorded raw rather than as the percentage the legacy
    # exports carried, because that column was written as a constant 0.0 for
    # every row ever produced (D5) and because a percentage needs a denominator
    # the schema does not state. A byte count is unambiguous; the analysis layer
    # can normalise against a node's memory if it says which.
    "gateway_rss_bytes",
)

_REQUIRED = frozenset(COLUMNS)

#: Schema for Phase I substrate measurements. Network characterisation shares no
#: dimensions with a workload sample -- there is no concurrency, no operation
#: type and no throughput -- so forcing it into :data:`COLUMNS` would mean
#: writing rows that are mostly null and inviting the analysis layer to average
#: across quantities that are not comparable. It is a separate declared schema
#: rather than an undeclared one: the discipline being enforced is that every
#: CSV has a schema, not that every CSV has the *same* schema.
#: ``rtt_min/mean/max/mdev`` are taken from ping's own summary line, which
#: prints three decimals regardless of magnitude. The per-packet lines do not:
#: ``ping`` reduces printed precision as the value grows, so a 25 ms link is
#: reported as ``25.5 ms`` while a 186 ms link is reported as ``186 ms``. Reading
#: the quantiles from those lines is unavoidable, but it means their resolution
#: differs per link -- which is why ``rtt_resolution_ms`` is recorded alongside
#: them rather than left for a reader to infer. Reporting a sub-millisecond
#: deviation on a link measured to the nearest millisecond would be false
#: precision, and this schema makes that distinction explicit instead.
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


#: Schema for the Phase IV RPO audit log: one row per write the audit client
#: attempted, in order, with the outcome it observed.
#:
#: This is recorded rather than only summarised because the availability RTO is
#: derived from it, and a recovery-time claim whose underlying observations were
#: discarded cannot be re-derived, disputed, or plotted. ``outcome`` carries the
#: three-way classification the measurement depends on: a *refused* write was
#: never promised and its absence is not data loss; an *ambiguous* one may or may
#: not have committed; only an *acknowledged* write later found absent is an RPO
#: violation. Collapsing the three into a boolean is what makes an RPO figure
#: unfalsifiable, so the distinction is preserved on disk and not just in memory.
AUDIT_COLUMNS: tuple[str, ...] = (
    "wall_offset_s",
    "seq_id",
    "outcome",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    started_utc: str = field(default_factory=utcnow)
    finished_utc: str | None = None
    git_revision: str | None = field(default_factory=_git_revision)
    profile: dict[str, Any] = field(default_factory=dict)
    topology: list[dict[str, Any]] = field(default_factory=list)
    #: Wall-clock instant corresponding to the zero of the run's monotonic clock,
    #: which is the origin of both ``wall_offset_s`` and every offset recorded in
    #: ``events.json``. Recorded so that the two files are known to share an
    #: origin rather than assumed to.
    clock_epoch_utc: str | None = None
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
        """Phase IV audit attempt log, written under :data:`AUDIT_COLUMNS`."""
        return self.path / "audit.csv"

    @property
    def preflight_json(self) -> Path:
        """Pre-flight assertions and their observed values for this run."""
        return self.path / "preflight.json"

    def write_preflight(self, report: dict[str, Any]) -> None:
        self.preflight_json.write_text(json.dumps(report, indent=2))

    def raw(self, name: str) -> Path:
        return self.path / "raw" / name

    def write_manifest(self, manifest: Manifest) -> None:
        self.manifest_json.write_text(json.dumps(asdict(manifest), indent=2))

    def write_events(self, events: dict[str, Any]) -> None:
        self.events_json.write_text(json.dumps(events, indent=2))


class MetricsWriter:
    """Append-only writer that rejects rows not matching a declared schema.

    ``columns`` selects the schema; it defaults to the workload table so existing
    callers are unaffected, and Phase I passes :data:`NETWORK_COLUMNS`. The
    rejection is deliberate friction: a phase that has not got a value must say
    so rather than omit the key, which is how ``ram_pct`` came to be a constant
    0.0 for an entire dissertation's worth of runs (D5).
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

    def __enter__(self) -> "MetricsWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

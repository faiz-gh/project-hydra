"""The only way analysis code reads a completed run.

:func:`load_run` refuses a run without a manifest, a run that fails
validation, and a run whose pre-flight failed. Aggregation policy lives here
too: throughput is summed across operation types, and latency is never pooled
across them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import DEFAULT_RUNS_DIR
from ..core.recorder import COLUMNS, NETWORK_COLUMNS
from .validation import ValidationReport, validate, validate_probe

#: Quantile columns, in the order the invariant p50 <= p95 <= p99 <= pMax asserts.
QUANTILES: tuple[str, ...] = ("p50_ms", "p95_ms", "p99_ms", "pmax_ms")


class RunLoadError(RuntimeError):
    """Raised when a run cannot be read, or is not fit to be analysed."""


@dataclass(frozen=True)
class Run:
    """One measurement run, loaded and checked.

    ``metrics`` is the long-format table as written, one row per (interval, op).
    """

    path: Path
    manifest: dict[str, Any]
    metrics: pd.DataFrame
    events: dict[str, Any] | None
    preflight: dict[str, Any] | None
    report: ValidationReport

    # -- provenance --------------------------------------------------------
    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.path.name))

    @property
    def phase(self) -> str:
        return str(self.manifest.get("phase", "unknown"))

    @property
    def schema_version(self) -> str:
        return str(self.manifest.get("schema_version", "unknown"))

    @property
    def engine(self) -> str:
        """"cockroachdb" or "postgresql"; older runs without the field were CockroachDB."""
        return str(self.manifest.get("engine") or "cockroachdb")

    @property
    def profile(self) -> dict[str, Any]:
        return self.manifest.get("profile", {}) or {}

    @property
    def workload(self) -> dict[str, Any]:
        return self.profile.get("workload", {}) or {}

    @property
    def server_command(self) -> str | None:
        """How the server under test was started, from the manifest notes."""
        for note in self.manifest.get("notes", []) or []:
            if " server: " in note:
                return note.split(" server: ", 1)[1]
        return None

    @property
    def records_wall_clock(self) -> bool:
        """Whether the metrics carry the harness clock (``wall_offset_s``); schema 2.0 runs do not."""
        return (
            "wall_offset_s" in self.metrics.columns
            and self.metrics["wall_offset_s"].notna().any()
        )

    # -- aggregation -------------------------------------------------------
    def ticks(self, warmup_s: float = 0.0) -> pd.DataFrame:
        """Fold the long table to one row per measurement interval.

        Throughput is summed across operation types; the error counter takes the
        maximum. ``weighted_p50_ms`` is a throughput-weighted blend of per-op
        medians (not itself a median), used for Little's law.
        """
        work = self.metrics
        if warmup_s:
            work = work[work["elapsed_s"] > warmup_s]
        work = work.assign(_p50_weight=work["tps"] * work["p50_ms"])

        keys = ["concurrency", "repetition", "elapsed_s"]
        agg: dict[str, tuple[str, str]] = {
            "total_tps": ("tps", "sum"),
            "errors_cum": ("errors_cum", "max"),
            "_p50_weight": ("_p50_weight", "sum"),
            "ops_reported": ("op", "nunique"),
        }
        if self.records_wall_clock:
            agg["wall_offset_s"] = ("wall_offset_s", "min")
        out = work.groupby(keys, as_index=False).agg(**agg)

        out["weighted_p50_ms"] = (out["_p50_weight"] / out["total_tps"]).where(
            out["total_tps"] > 0
        )
        return out.drop(columns=["_p50_weight"]).sort_values(keys, ignore_index=True)

    def latency_by_op(
        self,
        quantiles: Iterable[str] = QUANTILES,
        warmup_s: float = 0.0,
    ) -> pd.DataFrame:
        """Mean of each per-interval quantile, per operation type.

        ``op`` stays a grouping key, so quantiles are never averaged across ops.
        """
        work = self.metrics
        if warmup_s:
            work = work[work["elapsed_s"] > warmup_s]
        cols = [q for q in quantiles if q in work.columns]
        grouped = work.groupby(["concurrency", "repetition", "op"], as_index=False)
        return grouped.agg(
            samples=("elapsed_s", "count"),
            **{q: (q, "mean") for q in cols},
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def resolve_run(target: str | Path, runs_dir: Path | None = None) -> Path:
    """Accept a run directory, a run id, or a path to a metrics file."""
    path = Path(target)
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        path = Path(runs_dir or DEFAULT_RUNS_DIR) / str(target)
    if not path.is_dir():
        raise RunLoadError(f"no run directory at {target!r}")
    return path


def load_run(
    target: str | Path,
    runs_dir: Path | None = None,
    require_valid: bool = True,
) -> Run:
    """Load a run and refuse to return it unless it is fit to analyse.

    ``require_valid=False`` is for inspecting a failed run, and for tests.
    """
    path = resolve_run(target, runs_dir)

    manifest = _read_json(path / "manifest.json")
    if manifest is None:
        raise RunLoadError(
            f"{path} has no manifest.json. A run whose code revision, profile and "
            "server configuration are unrecorded cannot be cited, so it is not "
            "loadable rather than loadable-with-a-warning."
        )

    metrics_path = path / "metrics.csv"
    if not metrics_path.exists():
        raise RunLoadError(f"{path} has no metrics.csv")
    metrics = pd.read_csv(metrics_path)

    unknown = set(metrics.columns) - set(COLUMNS)
    if unknown:
        raise RunLoadError(
            f"{metrics_path} carries column(s) {sorted(unknown)} that are not in the "
            "declared schema; the analysis layer will not guess at their meaning"
        )
    missing = {"elapsed_s", "concurrency", "repetition", "op", "tps"} - set(metrics.columns)
    if missing:
        raise RunLoadError(f"{metrics_path} is missing required column(s) {sorted(missing)}")

    profile = manifest.get("profile", {}) or {}
    ceiling = float(profile.get("tps_ceiling", 20_000.0))
    report = validate(metrics, tps_ceiling=ceiling)
    if require_valid and not report.ok:
        errors = "; ".join(f.message for f in report.findings if f.severity == "error")
        raise RunLoadError(
            f"{path.name} does not pass validation and must not be used for figures: "
            f"{errors}"
        )

    probe_csv = path / "rto_probe.csv"
    if require_valid and probe_csv.exists():
        probe_report = validate_probe(pd.read_csv(probe_csv))
        if not probe_report.ok:
            errors = "; ".join(
                f.message for f in probe_report.findings if f.severity == "error"
            )
            raise RunLoadError(
                f"{path.name} carries an RTO probe log that does not pass "
                f"validation, so its recovery-time figures must not be used: {errors}"
            )

    # Pre-flight is a separate gate: a misconfigured system can produce numbers
    # that pass every consistency check.
    preflight = _read_json(path / "preflight.json")
    if require_valid and preflight is not None and preflight.get("ok") is False:
        failed = [
            c.get("detail", c.get("name", "?"))
            for c in preflight.get("checks", [])
            if not c.get("passed", True)
        ]
        raise RunLoadError(
            f"{path.name} failed pre-flight and must not be used for figures: "
            + "; ".join(failed)
        )

    return Run(
        path=path,
        manifest=manifest,
        metrics=metrics,
        events=_read_json(path / "events.json"),
        preflight=preflight,
        report=report,
    )


@dataclass(frozen=True)
class NetworkRun:
    """A Phase I network measurement.

    Has its own schema, so workload validation does not apply; a manifest is
    still required so the matrix can be tied to a deployment.
    """

    path: Path
    manifest: dict[str, Any]
    links: pd.DataFrame
    preflight: dict[str, Any] | None

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.path.name))

    @property
    def quorum_floor_ms(self) -> float | None:
        derived = (self.preflight or {}).get("derived") or {}
        floor = derived.get("quorum_floor_ms")
        return float(floor) if floor is not None else None

    def matrix(self, value: str = "rtt_mean_ms") -> pd.DataFrame:
        """Square source-by-destination matrix of one measured column."""
        return self.links.pivot(index="source", columns="destination", values=value)


def load_network_run(target: str | Path, runs_dir: Path | None = None) -> NetworkRun:
    path = resolve_run(target, runs_dir)
    manifest = _read_json(path / "manifest.json")
    if manifest is None:
        raise RunLoadError(f"{path} has no manifest.json")
    csv = path / "network.csv"
    if not csv.exists():
        raise RunLoadError(f"{path} has no network.csv; is this a Phase I run?")
    links = pd.read_csv(csv)
    unknown = set(links.columns) - set(NETWORK_COLUMNS)
    if unknown:
        raise RunLoadError(
            f"{csv} carries column(s) {sorted(unknown)} outside the declared "
            "network schema"
        )
    return NetworkRun(
        path=path,
        manifest=manifest,
        links=links,
        preflight=_read_json(path / "preflight.json"),
    )

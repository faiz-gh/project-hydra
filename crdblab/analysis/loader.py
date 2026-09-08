"""Canonical entry point to a completed run.

Every analysis module in this package reads its data through :func:`load_run`
and through no other route. Three properties are enforced here rather than left
to each caller's discipline, because each corresponds to a way the legacy
pipeline went wrong.

**A run is addressed by identity, not by path to a CSV.** ``metrics.csv`` alone
does not say which code produced it, against which profile, or on a server
started with which arguments. The legacy figures were traceable to a filename
and nothing else, which is why a fifteen-fold difference in block cache between
the two phases being compared could sit unnoticed in the results chapter (D9).
A run without a manifest is refused.

**Validation gates analysis.** :func:`load_run` runs the full check suite from
:mod:`crdblab.analysis.validation` and raises unless it passes. Analysing an
unvalidated run requires saying so explicitly, in code, at the call site. The
legacy pipeline's defining failure was not that its numbers were unchecked but
that two scripts silently disagreed about them: one filtered the symptom of a
cumulative row leaking into the sample stream, the other consumed it (D3).

**Aggregation policy is applied in one place.** Throughput is summed across
operation types; latency distributions are never pooled across them. Both rules
live in :meth:`Run.ticks` and :meth:`Run.latency_by_op` so that no analysis
module can quietly choose otherwise -- the legacy code averaged the read and
write rates, halving reported throughput, while separately averaging their
quantiles into a number that was a quantile of nothing (D1).
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

    ``metrics`` is the long-format table exactly as written: one row per
    (interval, operation type). It is deliberately *not* pre-aggregated, so that
    every analysis states its own aggregation in the open.
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
    def profile(self) -> dict[str, Any]:
        return self.manifest.get("profile", {}) or {}

    @property
    def workload(self) -> dict[str, Any]:
        return self.profile.get("workload", {}) or {}

    @property
    def server_command(self) -> str | None:
        """How the server under test was started, from the manifest notes.

        Recorded by ``preflight.capture_server_config`` because the run manifest
        previously described the client side in full and the server side not at
        all, which is the reason a block-cache asymmetry between the baseline and
        the cluster was attributed to replication cost (D9).
        """
        for note in self.manifest.get("notes", []) or []:
            if " server: " in note:
                return note.split(" server: ", 1)[1]
        return None

    @property
    def records_wall_clock(self) -> bool:
        """Whether the metrics table carries the harness clock alongside elapsed.

        False for schema 2.0 runs, which recorded only the generator's own
        ``elapsed_s``. Anything that must place a harness-scheduled event on the
        same axis as throughput has to consult this before doing so.
        """
        return (
            "wall_offset_s" in self.metrics.columns
            and self.metrics["wall_offset_s"].notna().any()
        )

    # -- aggregation -------------------------------------------------------
    def ticks(self, warmup_s: float = 0.0) -> pd.DataFrame:
        """Fold the long table to one row per measurement interval.

        Throughput is **summed** across operation types: read and write rates are
        components of one offered load, not repeated measurements of it. The
        cumulative error counter is taken as the **maximum**, never summed, since
        each operation type reports the same running total.

        A frequency-weighted median, ``sum(share_o * p50_o)``, is carried
        alongside. It is the only defensible scalar summary of latency across a
        mixed workload -- the mean of a read p50 and a write p50 is a quantile of
        no distribution -- and it is the quantity Little's law compares against.
        It is named as a weighted blend, not as "the p50", so that no figure can
        present it as one.
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
        """Mean of each per-interval quantile, kept separate per operation type.

        Averaging a quantile over time is a legitimate summary and is what this
        returns. Averaging a quantile *across operation types* is not, and this
        function structurally cannot do it: ``op`` remains a grouping key.
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

    ``require_valid=False`` is provided for inspecting a run that has already
    failed, and for the validation tests themselves. It is not a way to get a
    figure out of a run that does not validate: the check suite exists because
    the legacy defects were all individually plausible, and a number that cannot
    survive its own consistency checks cannot survive a viva either.
    """
    path = resolve_run(target, runs_dir)

    manifest = _read_json(path / "manifest.json")
    if manifest is None:
        raise RunLoadError(
            f"{path} has no manifest.json. A run whose code revision, profile and "
            "server configuration are unrecorded cannot be cited, so it is not "
            "loadable rather than loadable-with-a-warning (D9)."
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

    # A Phase III/IV run may also carry a probe log, under its own schema. It is
    # gated here for the same reason everything else is: `probe_availability`
    # reads it with a bare `read_csv`, and the whole point of this loader is that
    # no analysis reaches a CSV that has not been checked first. A run that
    # predates the probe simply has no such file and is unaffected.
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

    # Pre-flight is a separate gate and must be checked separately. ``validate``
    # asks whether the recorded numbers are consistent with each other;
    # pre-flight asks whether the system was in a fit state to be measured. D7
    # and D8 both produce data that passes every consistency check while the
    # workload touches no rows or the leaseholder sits on another continent, so a
    # run whose pre-flight failed is exactly the run whose numbers look fine.
    #
    # This gap was real: a Phase II sweep with one failed row-match assertion
    # would have loaded and rendered into a figure, because nothing outside the
    # phase script ever read preflight.json.
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
    """A Phase I substrate measurement.

    Separate from :class:`Run` because network characterisation shares no
    dimensions with a workload sample -- no concurrency, no operation type, no
    throughput -- and is written under its own declared schema. The workload
    validation suite is meaningless against it, so it is not applied; the
    manifest requirement is, because a round-trip matrix that cannot say which
    deployment produced it is worthless after a redeploy, and the testbed has
    been redeployed with different addresses at least once.
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

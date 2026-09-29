"""Which runs a render considers, and the data the charts read from them.

Every run is loaded through the analysis loader, so a run without a manifest,
or one failing validation or pre-flight, is recorded as refused with the
loader's reason (drawn by chart E3). Selection is the newest passing run of
each kind, per engine; run ids start with a UTC stamp, so name order is time order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..analysis.loader import NetworkRun, Run, RunLoadError, load_network_run, load_run
from ..topology import CLIENT_NODE

#: Run-directory suffix -> run kind. Standalone ``p4-probe`` runs are not charted.
KINDS: dict[str, str] = {
    "p1-network": "network",
    "bench_cluster": "bench",
    "p4-chaos-recover": "chaos-recover",
    "p4-chaos-dead": "chaos-dead",
}

#: Engines in the order every chart draws and lists them.
ENGINES: tuple[str, ...] = ("cockroachdb", "postgresql")

#: Fault classes in the order every chart draws and lists them.
MODES: tuple[str, ...] = ("dead", "recover")

#: ``metrics.csv`` columns that are always blank (superseded by hardware_metrics.csv);
#: named so nothing plots them as zero load.
DEAD_COLUMNS: tuple[str, ...] = ("gateway_cpu_pct", "gateway_disk_iops", "gateway_rss_bytes")


@dataclass
class RunEntry:
    """One run directory the render considered, and what the gate said about it."""

    run_id: str
    path: Path
    kind: str
    engine: str
    profile: str
    run: Run | NetworkRun | None = None
    refused: str = ""

    @property
    def passing(self) -> bool:
        return self.run is not None

    @property
    def mode(self) -> str | None:
        return self.kind.split("-", 1)[1] if self.kind.startswith("chaos-") else None


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return None


@dataclass
class Inventory:
    """Every run of a chartable kind under ``runs_dir``, gated and indexed."""

    runs_dir: Path
    profile: str | None = None
    entries: list[RunEntry] = field(default_factory=list)

    @classmethod
    def scan(cls, runs_dir: Path, profile: str | None = None) -> Inventory:
        inventory = cls(runs_dir=Path(runs_dir), profile=profile)
        if not inventory.runs_dir.is_dir():
            return inventory
        for path in sorted(p for p in inventory.runs_dir.iterdir() if p.is_dir()):
            kind = KINDS.get(path.name.partition("_")[2])
            if kind is None:
                continue
            manifest = _read_manifest(path)
            engine = str((manifest or {}).get("engine") or "cockroachdb")
            name = str(((manifest or {}).get("profile") or {}).get("name") or "unknown")
            if profile and name != profile:
                continue
            entry = RunEntry(
                run_id=path.name, path=path, kind=kind, engine=engine, profile=name
            )
            try:
                if kind == "network":
                    entry.run = load_network_run(path)
                else:
                    entry.run = load_run(path)
            except RunLoadError as exc:
                entry.refused = str(exc)
            except Exception as exc:  # a malformed file is a refusal, not a crash
                entry.refused = f"{type(exc).__name__}: {exc}"
            inventory.entries.append(entry)
        return inventory

    # -- selection ---------------------------------------------------------
    def latest(self, kind: str, engine: str | None = None) -> Any:
        """The most recent passing run of ``kind`` (for ``engine``, if given)."""
        matches = [
            e for e in self.entries
            if e.kind == kind and e.passing and (engine is None or e.engine == engine)
        ]
        return matches[-1].run if matches else None

    def per_engine(self, kind: str) -> dict[str, Any]:
        """``{engine: newest passing run of kind}``, engines in :data:`ENGINES` order."""
        out = {}
        for engine in ENGINES:
            run = self.latest(kind, engine)
            if run is not None:
                out[engine] = run
        return out

    def chaos(self) -> list[tuple[str, str, Run]]:
        """``(engine, mode, run)`` for the newest chaos run of each class, per engine."""
        out = []
        for engine in ENGINES:
            for mode in MODES:
                run = self.latest(f"chaos-{mode}", engine)
                if run is not None:
                    out.append((engine, mode, run))
        return out

    def network(self) -> NetworkRun | None:
        """The Phase I matrix the network charts draw: one deployment's, stated.

        The newest matrix of each engine's deployment is found, and the earlier of
        those is drawn. Charts E1, E2 and E4 all use this one run, so the three of
        them always describe the same set of machines.
        """
        candidates = list(self.per_engine("network").values())
        return min(candidates, key=lambda r: r.run_id) if candidates else None

    @property
    def engines(self) -> list[str]:
        present = {e.engine for e in self.entries}
        return [e for e in ENGINES if e in present] + sorted(present - set(ENGINES))

    @property
    def passing(self) -> list[RunEntry]:
        return [e for e in self.entries if e.passing]


def hardware(run: Run) -> pd.DataFrame | None:
    """A run's per-node hardware samples, ready to plot, or ``None`` if absent.

    Each node's first row has no rates (nothing to difference) and is dropped.
    """
    path = run.path / "hardware_metrics.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if frame.empty:
        return None
    frame = frame.sort_values("wall_offset_s", kind="stable")
    frame = frame[frame.groupby("node").cumcount() > 0]
    return frame.reset_index(drop=True)


def cluster_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop the client node: it is not a cluster member and carries no replica."""
    return frame[frame["node"] != CLIENT_NODE.name]


def probe_attempts(run: Run) -> pd.DataFrame | None:
    """The RTO probe's attempt log for a chaos run, or ``None`` if it has none.

    Read only after :func:`load_run` has validated it: the loader refuses a run
    whose probe log fails its checks, so reaching this means the file is sound.
    """
    path = run.path / "rto_probe.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


def fault_offset(run: Run) -> float | None:
    """When the fault landed, on the harness clock, from the run's own events."""
    injected = ((run.events or {}).get("injected") or {}).get("at_offset_s")
    return float(injected) if injected is not None else None

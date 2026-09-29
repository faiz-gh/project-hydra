"""The chart registry and the conventions every chart shares.

A chart is a function ``(ctx) -> Drawn`` registered with :func:`chart`. It
either draws and returns the numbers behind the picture, or raises
:class:`Skip` with a reason, which is reported beside the charts that drew.
Stats come from the analysis layer and are written to ``summary.json``/``.csv``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from ..report import style
from ..report.style import SERIES
from .data import Inventory

#: Group letter -> heading, in report order.
GROUPS: dict[str, str] = {
    "A": "Benchmark and saturation",
    "B": "Hardware utilisation",
    "C": "Resilience",
    "D": "Engine comparison",
    "E": "Network and provenance",
}

#: Display names for engines; lower-case ids stay in stat keys and filenames.
LABEL: dict[str, str] = {"cockroachdb": "CockroachDB", "postgresql": "PostgreSQL/Patroni"}
COLOR: dict[str, str] = {"cockroachdb": SERIES[0], "postgresql": SERIES[1]}
MARKER: dict[str, str] = {"cockroachdb": "o", "postgresql": "s"}
DASH: dict[str, str] = {"cockroachdb": "-", "postgresql": "--"}


class Skip(Exception):
    """A chart's inputs are absent; the message says which, in a sentence."""


@dataclass
class Context:
    """What a chart may read: the gated inventory, and where to write."""

    inventory: Inventory
    out_dir: Path
    cache: dict[str, Any] = field(default_factory=dict)

    def memo(self, key: str, compute: Callable[[], Any]) -> Any:
        """Compute an expensive shared input once per render (e.g. the comparison)."""
        if key not in self.cache:
            self.cache[key] = compute()
        return self.cache[key]


@dataclass
class Drawn:
    stats: dict[str, Any]
    files: list[str]


@dataclass(frozen=True)
class Chart:
    id: str
    title: str
    caption: str
    fn: Callable[[Context], Drawn]

    @property
    def group(self) -> str:
        return self.id[0]

    @property
    def stem(self) -> str:
        """``a1_throughput_latency_curve``: the id plus the title, filename-safe."""
        words = re.sub(r"[^a-z0-9]+", "_", self.title.lower()).strip("_")
        return f"{self.id.lower()}_{words}"


REGISTRY: list[Chart] = []


def chart(chart_id: str, title: str, caption: str):
    """Register a chart function under a stable id, title and caption."""

    def register(fn: Callable[[Context], Drawn]) -> Callable[[Context], Drawn]:
        REGISTRY.append(Chart(chart_id, title, " ".join(caption.split()), fn))
        return fn

    return register


def new_figure(*args, **kwargs):
    """``plt.subplots`` under the house style."""
    style._style()
    return plt.subplots(*args, **kwargs)


def save(ctx: Context, chart_id: str, fig, axes, runs: Sequence[Any]) -> list[str]:
    """Write one chart as PNG + SVG, named and footed with its provenance.

    ``runs`` is every run the chart drew from, in the order it drew them. With no
    runs at all -- a chart about the inventory itself -- the footer says so
    rather than naming nothing.
    """
    spec = next(c for c in REGISTRY if c.id == chart_id)
    runs = [r for r in runs if r is not None]
    name = f"{spec.stem}{style._provenance_slug(*runs)}.png"
    axes_list = list(axes.flat) if hasattr(axes, "flat") else axes
    png = style._finish(
        fig, axes_list, [r.run_id for r in runs] or ["no run"], ctx.out_dir / name
    )
    return [p.name for p in style._written_formats(png)]


def need(value: Any, reason: str) -> Any:
    """Return ``value``, or skip the chart with ``reason`` if it is missing."""
    if value is None or (hasattr(value, "__len__") and len(value) == 0):
        raise Skip(reason)
    return value

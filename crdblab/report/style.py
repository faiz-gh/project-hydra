"""Shared figure style: palette, rcParams, provenance filenames and PNG+SVG export.

Used by both :mod:`crdblab.report.figures` and :mod:`crdblab.insights`. Figures
are for print on a light background; series differ by hue *and* by marker and
dash, so they survive greyscale printing.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# Palette: categorical slots in fixed order; text never uses a series colour.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # blue, orange, aqua
MARKERS = ("o", "s", "^")
DASHES = ("-", "--", "-.")
CRITICAL = "#d03b3b"  # status: reserved for the fault, never for a series
WARNING = "#fab219"

#: Single-hue sequential ramp for magnitudes.
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQUENTIAL = LinearSegmentedColormap.from_list("crdblab_blue", BLUE_RAMP)

#: Minimum exported width in pixels (4K). Reached by raising export DPI, never by
#: enlarging the figure, so the layout is unchanged.
EXPORT_WIDTH_PX = 3840

#: Vector copy written beside every PNG.
EXPORT_VECTOR_EXT = ".svg"


def _slug(value: object) -> str:
    """Filename-safe form of one provenance component."""
    # Keep ``_`` so the filename matches the run directory name.
    text = str(value or "unknown")
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in text).strip("-") or "unknown"


def _manifest_field(run, *path: str, default: str = "unknown") -> str:
    """One nested manifest value, for either a ``Run`` or a ``NetworkRun``.

    Read from the manifest rather than from a property because the two run types
    do not share one: ``Run`` exposes ``.engine`` and ``.profile``, ``NetworkRun``
    exposes neither, and both carry the manifest itself.
    """
    node = getattr(run, "manifest", None) or {}
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return str(node) if node else default


def _provenance_slug(*runs) -> str:
    """The filename tail naming the engine, profile and run(s) behind a figure.

    E.g. ``_cockroachdb_thesis_<run_id>``. Where runs disagree, the component is
    ``mixed-engine`` or ``mixed-profile``; every run id is always listed.
    """
    present = [r for r in runs if r is not None]
    engines = {_manifest_field(r, "engine", default="cockroachdb") for r in present}
    parts: list[str] = [engines.pop() if len(engines) == 1 else "mixed-engine"]
    profiles = {_manifest_field(r, "profile", "name") for r in present}
    parts.append(profiles.pop() if len(profiles) == 1 else "mixed-profile")
    parts.extend(getattr(r, "run_id", "unknown") for r in present)
    return "".join(f"_{_slug(part)}" for part in parts)


def _style() -> None:
    """Recessive chrome: hairline solid grid, no top/right spines, sans text."""
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 9,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "grid.linestyle": "-",  # never dashed: dashing reads as a threshold
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelcolor": INK_SECONDARY,
            "ytick.labelcolor": INK_SECONDARY,
            "legend.frameon": False,
            "lines.linewidth": 2.0,
            "lines.solid_capstyle": "round",
            "figure.dpi": 160,
        }
    )


def _finish(fig, ax_or_axes, provenance: Sequence[str], path: Path) -> Path:
    """Strip the top/right spines and stamp the run ids the figure came from."""
    axes = ax_or_axes if isinstance(ax_or_axes, (list, tuple)) else [ax_or_axes]
    for ax in axes:
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_linewidth(0.8)

    # Below the axes box, so it never overlaps the x-axis label.
    fig.text(
        0.0,
        -0.045,
        "source: " + "  ".join(provenance),
        fontsize=6,
        color=INK_MUTED,
        ha="left",
        va="top",
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    # DPI from the tight bounding box, which is what bbox_inches="tight" exports.
    fig.canvas.draw()
    bbox = fig.get_tightbbox(fig.canvas.get_renderer())
    dpi = EXPORT_WIDTH_PX / bbox.width

    fig.savefig(path, bbox_inches="tight", dpi=dpi)
    fig.savefig(path.with_suffix(EXPORT_VECTOR_EXT), bbox_inches="tight")
    plt.close(fig)
    return path


def _written_formats(png: Path) -> list[Path]:
    """Every file :func:`_finish` wrote for one figure, for the caller to report."""
    return [png, png.with_suffix(EXPORT_VECTOR_EXT)]

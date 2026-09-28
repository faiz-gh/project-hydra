"""The house style every figure in this project is drawn with.

Extracted from :mod:`crdblab.report.figures` so that module and
:mod:`crdblab.insights` draw with one palette, one set of rcParams, one
provenance slug and one PNG+SVG writer rather than two copies that drift.
``figures.py`` re-exports the names it used to define, so nothing that imported
them from there broke.

Design notes. These are print figures for a Word document, so they are rendered
for a light surface only; a screen palette's dark mode does not apply. Series are
distinguished by hue *and* by marker and dash pattern, so the figures survive
greyscale printing, which is the paper equivalent of the colour-vision case. The
two-hue categorical palette was validated rather than eyeballed (worst adjacent
CVD Delta E 24.7 against a >= 8 target).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# --- palette ---------------------------------------------------------------
# Light-surface values from the validated reference palette. Categorical slots
# are assigned in fixed order and never cycled; text never wears a series colour.
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

#: Sequential ramp for magnitude: one hue, light to dark. Steps 100-700 of the
#: reference blue ramp, which is what a continuous scale is allowed to use.
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQUENTIAL = LinearSegmentedColormap.from_list("crdblab_blue", BLUE_RAMP)

#: Minimum exported width in pixels. 4K (3840) so a figure survives being scaled
#: to a full text column in print and still stands up to a reader zooming in on
#: the printed page.
#:
#: Resolution is raised through the *export* DPI, never by enlarging the figure.
#: Font sizes, line widths and marker sizes are all specified in points, so a
#: higher DPI renders exactly the same layout onto more pixels; making the figure
#: physically larger instead would shrink the text relative to the plot and
#: quietly undo the label placement.
EXPORT_WIDTH_PX = 3840

#: Vector companion, written alongside the PNG rather than instead of it: Word
#: handles PNG more predictably for inline placement, and SVG opens in a browser
#: and in every vector editor without a conversion step. Nothing depends on the
#: format beyond the extension, so this is the only line that decides it.
EXPORT_VECTOR_EXT = ".svg"


def _slug(value: object) -> str:
    """Filename-safe form of one provenance component."""
    # ``_`` is kept, not replaced: run ids contain it (``..Z_bench_cluster``)
    # and rewriting it would make the filename disagree with the run directory
    # it names, which is the one thing this slug exists to state.
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

    A footer stamped inside an image cannot tell two files in one directory
    apart, which is how a figure from a pre-redeploy cluster once sat unnoticed
    beside five from the current one. So the filename says it too.

    Every figure is named this way, Phase I included: ping does not care which
    engine is listening, but switching engines replaces every cluster node, so a
    matrix from each deployment describes a different set of machines.

    Where several runs disagree on engine or profile the component becomes
    ``mixed``, rather than picking one and misattributing the figure to it; the
    run ids that follow always name all of them. With no runs at all -- a figure
    about the run inventory itself -- both components are ``mixed``.
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

    # Placed below the figure's own coordinate box rather than inside it. The
    # tight bounding box expands to include it, which guarantees separation from
    # the x-axis label; at a positive y it overlapped the axis label on every
    # figure whose x-axis carried rotated tick labels.
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

    # Derive the export DPI from the *tight* bounding box, not from the declared
    # figure size: every figure is saved with bbox_inches="tight", which crops or
    # expands the canvas to fit its artists, so figsize alone does not predict
    # the exported width.
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

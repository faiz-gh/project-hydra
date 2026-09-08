"""Dissertation figures, rendered from validated runs.

Every figure here is resolved from a ``run_id`` through
:mod:`crdblab.analysis.loader`, never from a bare path to a CSV. That loader
refuses a run with no manifest and refuses a run that does not pass validation,
so a figure that renders is a figure whose provenance can be stated. Each one
also stamps the run ids it was drawn from into its own footer, because a figure
separated from its caption must still be traceable to the measurement.

Aggregation is never recomputed here. Throughput sums and latency does not pool
in :meth:`Run.ticks` / :meth:`Run.latency_by_op`; tier statistics come from
:mod:`crdblab.analysis.steady_state`; the fault timeline comes from
:class:`crdblab.analysis.resilience.Alignment`. A plotting module that did its
own aggregation would be a second implementation of the policy D1 violated.

One constraint falls directly out of the Stage 5 analysis and is enforced in
code rather than left to the author's memory:

* **The resilience figure takes its time axis from the run's clock alignment.**
  Where the offset between the generator's clock and the harness's was measured,
  the fault is a line; where it was only bounded, it is a **band** of the
  unmeasured width. Drawing a band as a line is the figure-level form of D10.

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
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from ..analysis import resilience, steady_state
from ..analysis.loader import NetworkRun, Run

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
_BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQUENTIAL = LinearSegmentedColormap.from_list("crdblab_blue", _BLUE_RAMP)

#: Minimum exported width in pixels. 4K (3840) so a figure survives being scaled
#: to a full text column in print and still stands up to a reader zooming in on
#: the PDF.
#:
#: Resolution is raised through the *export* DPI, never by enlarging the figure.
#: Font sizes, line widths and marker sizes are all specified in points, so a
#: higher DPI renders exactly the same layout onto more pixels; making the figure
#: physically larger instead would shrink the text relative to the plot and
#: quietly undo the label placement above.
EXPORT_WIDTH_PX = 3840

#: Vector companion. A raster figure has a resolution; a vector one does not, so
#: for anything that will be printed this is the better artefact regardless of
#: how many pixels the PNG has. Written alongside rather than instead, because
#: Word handles PNG more predictably than PDF for inline placement.
EXPORT_VECTOR = True


def _engine_suffix(*runs) -> str:
    """A filename suffix distinguishing which engine a figure came from.

    Blank for CockroachDB, so filenames written before Postgres runs existed --
    and any caption already citing them -- keep meaning the same figure. Blank
    also whenever the runs disagree on engine (an all-engines comparison
    figure) or there are no runs to ask, rather than guessing which one wins;
    a filename that silently picked one engine's tag for a mixed figure would
    misattribute it.
    """
    engines = {getattr(r, "engine", "cockroachdb") for r in runs if r is not None}
    if len(engines) != 1:
        return ""
    (engine,) = engines
    return "" if engine == "cockroachdb" else f"_{engine}"


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
    # figure size. Every figure here is saved with bbox_inches="tight", which
    # crops or expands the canvas to fit its artists -- the provenance footer sits
    # below the figure box on purpose -- so figsize alone does not predict the
    # exported width. Measuring the box that will actually be written is what
    # makes the guarantee hold for every figure rather than for the one whose
    # aspect happened to be checked.
    fig.canvas.draw()
    bbox = fig.get_tightbbox(fig.canvas.get_renderer())
    dpi = EXPORT_WIDTH_PX / bbox.width

    fig.savefig(path, bbox_inches="tight", dpi=dpi)
    if EXPORT_VECTOR:
        fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return path


# --- Phase I ---------------------------------------------------------------

def network_matrix(run: NetworkRun, out_dir: Path) -> Path:
    """All-pairs round-trip matrix.

    A grid of magnitudes, so: heatmap on a single-hue sequential ramp, darker
    for slower. The cell values are printed because a matrix of five nodes *is*
    its own table view, and because the quorum floor argument depends on reading
    two specific cells rather than on the overall pattern. In-cell text takes
    white or ink by the fill's luminance so it always clears contrast.
    """
    _style()
    matrix = run.matrix("rtt_mean_ms")
    labels = [name.replace("crdb-", "") for name in matrix.index]

    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    data = matrix.to_numpy(dtype=float)
    image = ax.imshow(data, cmap=SEQUENTIAL, vmin=0.0)
    ax.grid(False)

    ax.set_xticks(range(len(labels)), labels, rotation=30, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xlabel("destination")
    ax.set_ylabel("source")

    finite = data[~np.isnan(data)]
    threshold = (finite.max() * 0.55) if finite.size else 0.0
    for row in range(data.shape[0]):
        for col in range(data.shape[1]):
            value = data[row, col]
            if np.isnan(value):  # a node does not ping itself
                ax.text(col, row, "-", ha="center", va="center", color=INK_MUTED)
                continue
            ax.text(
                col,
                row,
                f"{value:.0f}",
                ha="center",
                va="center",
                fontsize=8,
                color=SURFACE if value > threshold else INK,
            )

    bar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    bar.set_label("mean RTT (ms)", color=INK_SECONDARY)
    bar.outline.set_visible(False)

    floor = run.quorum_floor_ms
    title = "Inter-node round-trip time"
    if floor is not None:
        title += f"\nquorum floor {floor:.1f} ms: no committed write can be faster"
    ax.set_title(title, loc="left")
    return _finish(fig, ax, [run.run_id], out_dir / "fig1_network_matrix.png")


# --- Phase II ---------------------------------------------------------------

def throughput_sweep(runs: Sequence[Run], out_dir: Path) -> Path:
    """Throughput against offered concurrency, with an interval where one exists.

    Error bars are the Student's t 95% interval over *repetitions*, and are
    absent for a single-repetition tier rather than drawn as zero: a zero-width
    interval asserts agreement between repetitions that were never run.
    """
    _style()
    fig, ax = plt.subplots(figsize=(5.6, 3.6))

    for index, run in enumerate(runs):
        tiers = steady_state.per_tier(run)
        errors = [
            0.0 if value is None else float(value)
            for value in tiers["ci95_half_width_tps"]
        ]
        has_interval = any(e > 0 for e in errors)
        ax.errorbar(
            tiers["concurrency"],
            tiers["mean_total_tps"],
            yerr=errors if has_interval else None,
            color=SERIES[index % len(SERIES)],
            marker=MARKERS[index % len(MARKERS)],
            linestyle=DASHES[index % len(DASHES)],
            markersize=6,
            markeredgecolor=SURFACE,
            markeredgewidth=2,
            capsize=3,
            label=run.phase,
        )

    ax.set_xlabel("offered concurrency (workers)")
    ax.set_ylabel("throughput (ops/s), summed across operation types")
    ax.set_title("Steady-state throughput by concurrency", loc="left")
    ax.set_ylim(bottom=0)
    if len(runs) > 1:
        ax.legend(labelcolor=INK_SECONDARY)
    if not any(
        any(v is not None and v > 0 for v in steady_state.per_tier(r)["ci95_half_width_tps"])
        for r in runs
    ):
        ax.text(
            0.99, 0.03,
            "single repetition per tier: no interval estimate",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=7, color=INK_MUTED,
        )
    suffix = _engine_suffix(*runs)
    return _finish(
        fig, ax, [r.run_id for r in runs], out_dir / f"fig2_throughput_sweep{suffix}.png"
    )


def latency_by_operation(run: Run, out_dir: Path) -> Path:
    """Per-operation latency by tier, as small multiples.

    One panel per operation type rather than one axis, because read and write
    latency differ by roughly two orders of magnitude on this topology and share
    no useful scale. Plotting them together would either flatten the read curve
    into the axis or need a second y-scale, and a dual-axis chart invents a
    relationship the data does not contain.
    """
    _style()
    per_op = steady_state.latency_by_op(run)
    ops = sorted(per_op["op"].unique())
    fig, axes = plt.subplots(1, len(ops), figsize=(2.9 * len(ops), 3.4), squeeze=False)
    axes = list(axes[0])

    for ax, op in zip(axes, ops):
        frame = per_op[per_op["op"] == op].sort_values("concurrency")
        for quantile, style in (("p50_ms", "-"), ("p99_ms", "--")):
            ax.plot(
                frame["concurrency"],
                frame[quantile],
                color=SERIES[0],
                linestyle=style,
                marker="o" if quantile == "p50_ms" else "s",
                markersize=5,
                markeredgecolor=SURFACE,
                markeredgewidth=2,
                alpha=1.0 if quantile == "p50_ms" else 0.55,
                label=quantile.replace("_ms", ""),
            )
        ax.set_title(op, loc="left")
        ax.set_xlabel("concurrency")
        ax.set_ylim(bottom=0)
    axes[0].set_ylabel("latency (ms)")
    axes[-1].legend(labelcolor=INK_SECONDARY)
    fig.suptitle(
        "Latency by operation type (never pooled across types)",
        x=0.02, ha="left", color=INK, fontsize=10, fontweight="bold",
    )
    fig.tight_layout()
    suffix = _engine_suffix(run)
    return _finish(
        fig, axes, [run.run_id], out_dir / f"fig3_latency_by_operation{suffix}.png"
    )


#: Output filename per fault class. Phases III-IV each run one fault and
#: the two timelines are different figures, so the name is keyed on the class
#: rather than fixed: rendering a second run through a single hard-coded
#: ``fig5`` filename silently overwrote the first, which is why
#: ``fig6_resilience_timeline_recover.png`` existed in ``figures/`` with no path
#: through this module that could produce it. The names are constants, not
#: derived from the run id, so a caption citing fig5 or fig6 keeps meaning the
#: same figure across a re-render.
_RESILIENCE_FIGURES = {
    "dead": "fig5_resilience_timeline.png",
    "recover": "fig6_resilience_timeline_recover.png",
}


def _resilience_filename(mode: str | None, engine_suffix: str = "") -> str:
    """Filename for one fault class and engine, distinct for any class not yet named.

    ``engine_suffix`` from :func:`_engine_suffix` is inserted before the
    extension rather than appended after it, so ``fig6_..._recover.png``
    becomes ``fig6_..._recover_postgresql.png`` and stays sorted next to its
    CockroachDB sibling rather than falling under an unrelated ``_postgresql``
    prefix that would also collide with fig2/fig3's own suffix placement.
    """
    if mode in _RESILIENCE_FIGURES:
        name = _RESILIENCE_FIGURES[mode]
    else:
        slug = "".join(c if c.isalnum() else "_" for c in str(mode or "unknown"))
        name = f"fig5_resilience_timeline_{slug}.png"
    if not engine_suffix:
        return name
    stem, _, ext = name.rpartition(".")
    return f"{stem}{engine_suffix}.{ext}"


# --- Phases III-IV ----------------------------------------------------------

def resilience_timeline(run: Run, out_dir: Path) -> Path:
    """Throughput through a fault, on a single, explicitly stated clock.

    The x-axis is whichever clock the run can actually support. When both clocks
    were recorded per interval the offset between them is known, so throughput is
    plotted on the harness clock and the fault is a **line** at its recorded
    offset. When only the generator's clock was recorded the offset is bounded,
    not measured, so throughput is plotted on the generator's clock and the fault
    is a **band** spanning the whole uncertainty -- typically several seconds,
    against recovery times of the same order. Collapsing that band to a line
    would assert a measurement nobody made.
    """
    _style()
    alignment = resilience.align(run)
    profile = resilience.degradation_profile(run, alignment)
    performance = resilience.performance(run, alignment)
    fault = resilience.fault_offsets(run, alignment)
    mode = (run.events or {}).get("mode")

    fig, ax = plt.subplots(figsize=(6.2, 3.6))

    if alignment.exact:
        x = profile["wall_offset_s"]
        xlabel = "time since run start (harness clock, s)"
    else:
        x = profile["elapsed_s"]
        xlabel = "generator elapsed time (s)"

    ax.plot(x, profile["total_tps"], color=SERIES[0], linewidth=1.8, label="throughput")

    floor_tps = (performance.get("recomputed") or [{}])[0].get("floor_tps")
    if floor_tps:
        ax.axhline(floor_tps, color=WARNING, linewidth=1.4, zorder=1)
        ax.text(
            x.max(), floor_tps, f" recovery floor {floor_tps:.0f} ops/s ",
            va="bottom", ha="right", fontsize=7, color=INK_SECONDARY,
        )

    if alignment.exact and fault.get("wall_offset_s") is not None:
        ax.axvline(
            fault["wall_offset_s"], color=CRITICAL, linewidth=1.6,
            label=f"fault ({mode})",
        )
    elif fault.get("generator_elapsed_bounds_s"):
        low, high = fault["generator_elapsed_bounds_s"]
        ax.axvspan(
            low, high, color=CRITICAL, alpha=0.16, zorder=1,
            label=f"fault, located to within {high - low:.1f} s",
        )

    rto = performance.get("rto_s")
    if alignment.exact and rto is not None and fault.get("wall_offset_s") is not None:
        recovered = fault["wall_offset_s"] + rto
        ax.axvline(recovered, color=SERIES[2], linewidth=1.4, linestyle="--",
                   label=f"performance RTO {rto:.1f} s")
    elif not performance.get("defined"):
        state = performance.get("post_fault_state") or {}
        if state.get("mean_tps"):
            frac = state.get('fraction_of_baseline')
            frac_str = f" ({frac:.0%} of baseline)" if frac is not None else ""
            ax.axhline(state["mean_tps"], color=SERIES[1], linewidth=1.4, linestyle="-.",
                       label=f"settled {state['mean_tps']:.0f} ops/s{frac_str}")

    ax.set_xlabel(xlabel)
    ax.set_ylabel("throughput (ops/s)")
    ax.set_ylim(bottom=0)

    subtitle = (
        "clock offset measured; fault located exactly"
        if alignment.exact
        else f"clock offset bounded, not measured: fault located to "
             f"+/-{alignment.uncertainty_s / 2:.1f} s"
    )
    ax.set_title(
        f"Throughput through a {mode} fault on "
        f"{run.events.get('target')}\n{subtitle}",
        loc="left",
    )
    ax.legend(labelcolor=INK_SECONDARY, loc="lower right", fontsize=7.5)
    filename = _resilience_filename(mode, _engine_suffix(run))
    return _finish(fig, ax, [run.run_id], out_dir / filename)


def render_all(
    out_dir: Path,
    network: NetworkRun | None = None,
    cluster: Run | None = None,
    chaos: Run | Sequence[Run] | None = None,
) -> list[Path]:
    """Render every figure whose inputs are available.

    ``chaos`` accepts a sequence because Phases III-IV each produce one timeline per
    fault class and they are separate figures. Calling
    :func:`resilience_timeline` once here was the other half of the fig6
    provenance gap: even with both runs loaded, only one could be drawn.
    """
    written: list[Path] = []
    if network is not None:
        written.append(network_matrix(network, out_dir))
    if cluster is not None:
        written.append(throughput_sweep([cluster], out_dir))
        written.append(latency_by_operation(cluster, out_dir))
    if chaos is not None:
        runs = [chaos] if isinstance(chaos, Run) else list(chaos)
        for run in runs:
            written.append(resilience_timeline(run, out_dir))
    return written

"""Group E -- network and provenance.

E1, E2 and E4 all draw one Phase I matrix, chosen by
:meth:`data.Inventory.network`, so the three always describe the same machines.
E3 draws the inventory itself: every run the render considered and what the
loader's gate said about it.
"""

from __future__ import annotations

import numpy as np

from ..core.preflight import gateway_rtts, quorum_floor_ms
from ..report.style import INK, INK_MUTED, INK_SECONDARY, SEQUENTIAL, SERIES, SURFACE
from ..topology import DEFAULT_TOPOLOGY
from ._base import Context, Drawn, Skip, chart, new_figure, save


def _network(ctx: Context):
    network = ctx.inventory.network()
    if network is None:
        raise Skip("no Phase I network run passed the loader's gates")
    return network


@chart(
    "E1",
    "Quorum floor derivation",
    """Round-trip time from the gateway to every other node, sorted. A write commits
    once the leader and the two fastest acknowledgements have it, so the second bar
    sets the floor and the slower nodes do not gate an ordinary commit at all.
    Measured with ping, so it is independent of both databases -- which is what lets
    it bound them.""",
)
def e1_quorum_floor(ctx: Context) -> Drawn:
    network = _network(ctx)
    gateway = DEFAULT_TOPOLOGY.gateway.host
    rtts = gateway_rtts(network.path / "network.csv", gateway)
    if not rtts:
        raise Skip(f"{network.run_id} records no round trips from the gateway {gateway}")
    voters = len(DEFAULT_TOPOLOGY)
    # Re-derived from network.csv rather than read back, so the floor every
    # write-path claim rests on is checked against the value pre-flight recorded.
    derived = quorum_floor_ms(rtts, voters)
    ordered = sorted(rtts.items(), key=lambda kv: kv[1])
    acks = voters // 2
    fig, ax = new_figure(figsize=(6.2, 4.0))
    ax.bar(
        range(len(ordered)), [v for _, v in ordered], 0.55,
        color=[SERIES[0] if i < acks else INK_MUTED for i in range(len(ordered))],
    )
    ax.axhline(derived, color=INK_SECONDARY, linestyle="--", linewidth=1.2)
    ax.text(0.5, derived, f"quorum floor {derived:.1f} ms -- the 2nd fastest acknowledgement",
            va="bottom", fontsize=7, color=INK_SECONDARY)
    ax.set_xticks(range(len(ordered)), [host for host, _ in ordered], rotation=20, ha="right")
    ax.set_ylabel(f"mean RTT from {gateway} (ms)")
    ax.set_title("How the write-path floor follows from the topology")
    stats = {
        "gateway": gateway,
        "recorded_quorum_floor_ms": network.quorum_floor_ms,
        "derived_floor_ms": round(derived, 3),
    }
    return Drawn(stats, save(ctx, "E1", fig, ax, [network]))


@chart(
    "E2",
    "Link stability",
    """Minimum to p99 for every ordered pair. A short bar is a link whose mean is a
    real description of it; a long one is a link whose mean is an average over two
    different behaviours, and any latency figure resting on it inherits that spread.
    Note that ping's printed precision degrades as RTT grows -- each link's own
    resolution is recorded in network.csv.""",
)
def e2_link_stability(ctx: Context) -> Drawn:
    network = _network(ctx)
    links = network.links.dropna(subset=["rtt_mean_ms"]).copy()
    if links.empty:
        raise Skip(f"{network.run_id} records no round-trip times")
    links["spread"] = links["rtt_p99_ms"] - links["rtt_min_ms"]
    links = links.sort_values("rtt_mean_ms").reset_index(drop=True)
    labels = [f"{r.source} -> {r.destination}" for r in links.itertuples()]
    fig, ax = new_figure(figsize=(6.2, 0.2 * len(links) + 1.2))
    y = np.arange(len(links))
    ax.hlines(y, links["rtt_min_ms"], links["rtt_p99_ms"], color=INK_MUTED, linewidth=1.0)
    ax.scatter(links["rtt_mean_ms"], y, color=SERIES[0], s=12, zorder=3, label="mean")
    ax.scatter(links["rtt_p99_ms"], y, color=SERIES[1], s=12, marker="s", zorder=3, label="p99")
    ax.set_yticks(y, labels, fontsize=5)
    ax.set_xlabel("round-trip time (ms)")
    ax.set_title("Per-link spread from minimum to p99")
    ax.legend(fontsize=6, loc="upper left")
    worst = links.loc[links["spread"].idxmax()]
    stats = {
        "links": int(len(links)),
        "max_spread_ms": round(float(worst["spread"]), 3),
        "worst_link": f"{worst['source']} -> {worst['destination']}",
        "max_loss_pct": float(links["loss_pct"].max()),
    }
    return Drawn(stats, save(ctx, "E2", fig, ax, [network]))


@chart(
    "E3",
    "Run provenance",
    """Every run directory this report considered. A run reaches a chart only by
    loading through the gated loader, which refuses anything without a manifest,
    with unexpected columns, or that fails pre-flight or validation -- so a run
    listed as refused here contributed to nothing in this document.""",
)
def e3_run_provenance(ctx: Context) -> Drawn:
    entries = ctx.inventory.entries
    if not entries:
        raise Skip("no run directory of a chartable kind was found")
    fig, ax = new_figure(figsize=(6.2, 0.3 * len(entries) + 1.0))
    ax.axis("off")
    columns = (("run id", 0.0), ("engine", 0.36), ("profile", 0.5), ("phase", 0.6),
               ("gate", 0.78))
    for header, x in columns:
        ax.text(x, 1.0, header, fontsize=6.5, fontweight="bold", color=INK_SECONDARY,
                transform=ax.transAxes, va="bottom")
    step = 1.0 / (len(entries) + 1)
    for i, entry in enumerate(entries):
        y = 1.0 - (i + 1) * step
        gate = "loads and validates" if entry.passing else "refused: " + entry.refused[:60]
        for (_, x), value in zip(columns, (entry.run_id, entry.engine, entry.profile,
                                           entry.kind, gate)):
            ax.text(x, y, value, fontsize=5.5, family="monospace",
                    color=INK if entry.passing else INK_MUTED, transform=ax.transAxes)
    ax.set_title("Every run considered, and whether it passed its gates", loc="left")
    stats = {
        "runs_considered": len(entries),
        "runs_passing": len(ctx.inventory.passing),
    }
    return Drawn(stats, save(ctx, "E3", fig, ax, []))


@chart(
    "E4",
    "Round-trip matrix",
    """Mean round-trip time between every ordered pair of nodes, including the two
    neither E1 nor E2 puts on one axis with the rest: the non-gateway pairs. Darker
    is slower; a node does not ping itself, shown as a dash.""",
)
def e4_round_trip_matrix(ctx: Context) -> Drawn:
    network = _network(ctx)
    matrix = network.matrix("rtt_mean_ms")
    labels = [name.replace("crdb-", "") for name in matrix.index]
    fig, ax = new_figure(figsize=(5.6, 4.4))
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
            ax.text(col, row, f"{value:.0f}", ha="center", va="center", fontsize=8,
                    color=SURFACE if value > threshold else INK)
    bar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    bar.set_label("mean RTT (ms)", color=INK_SECONDARY)
    bar.outline.set_visible(False)
    floor = network.quorum_floor_ms
    title = "Inter-node round-trip time"
    if floor is not None:
        title += f"\nquorum floor {floor:.1f} ms: no committed write can be faster"
    ax.set_title(title, loc="left")
    return Drawn({"quorum_floor_ms": floor}, save(ctx, "E4", fig, ax, [network]))

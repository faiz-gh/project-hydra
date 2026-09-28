"""Group B -- hardware utilisation, from ``hardware_metrics.csv``.

The first consumer of the per-node node_exporter samples the benchmark has
recorded since 2026-09-10. Nodes are polled independently, so anything that
combines nodes bins them onto a common time axis first; each node's first row
carries no rates and is dropped by :func:`data.hardware`.

Tier-level figures (B2, B5) reproduce the original catalogue's windowing
exactly, and their captions say what that means: a tier's window runs from the
first interval of its earliest repetition to the last interval of its latest, on
the harness clock ``hardware_metrics.csv`` shares. Repetitions run in shuffled
order, so with three repetitions that envelope spans most of the sweep and the
hardware side of each ratio is close to a whole-sweep average. With one
repetition per tier (``smoke``) it is the tier itself. B5's traffic is the mean
*per node*, as the original computed it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..report.style import BLUE_RAMP, INK_MUTED, SEQUENTIAL
from ._base import COLOR, DASH, LABEL, MARKER, Context, Drawn, Skip, chart, need, new_figure, save
from .data import cluster_only, hardware

#: Bins across a run for anything that puts every node on one time axis.
HEATMAP_BINS = 100
#: Seconds per bin when averaging nodes into one per-cluster series.
SERIES_BIN_S = 5.0


def _bench_hardware(ctx: Context) -> dict[str, tuple]:
    """``{engine: (run, hardware frame)}`` for every bench run that recorded hardware."""
    runs = need(
        ctx.inventory.per_engine("bench"),
        "no benchmark run passed the loader's gates for either engine",
    )
    out = {}
    for engine, run in runs.items():
        frame = hardware(run)
        if frame is not None and not cluster_only(frame).empty:
            out[engine] = (run, frame)
    if not out:
        raise Skip(
            "no benchmark run carries hardware_metrics.csv; per-node collection "
            "was added on 2026-09-10 and needs node_exporter on every node"
        )
    return out


def _node_colors(nodes: list[str]) -> dict[str, str]:
    ramp = BLUE_RAMP[2:]
    return {node: ramp[i % len(ramp)] for i, node in enumerate(nodes)}


def _tier_windows(run) -> pd.DataFrame:
    """``concurrency, start, end``: each tier's envelope across its repetitions."""
    ticks = run.ticks()
    if "wall_offset_s" not in ticks.columns:
        raise Skip(
            f"{run.run_id} predates per-interval wall offsets (schema 2.0), so its "
            "tiers cannot be placed on the hardware samples' clock"
        )
    return ticks.groupby("concurrency", as_index=False).agg(
        start=("wall_offset_s", "min"), end=("wall_offset_s", "max")
    )


def _per_tier(frame: pd.DataFrame, windows: pd.DataFrame, column: str) -> pd.Series:
    """Mean of ``column`` over the samples inside each tier's window."""
    rows = []
    for w in windows.itertuples():
        inside = frame[(frame["wall_offset_s"] >= w.start) & (frame["wall_offset_s"] <= w.end)]
        rows.append((w.concurrency, inside[column].mean()))
    return pd.DataFrame(rows, columns=["concurrency", column]).set_index("concurrency")[column]


@chart(
    "B1",
    "Per-node CPU timeline",
    """CPU busy per node across the benchmark sweep, one panel per engine. The client
    node is dotted and is not a cluster member; it is shown so a client-side
    bottleneck can be excluded rather than assumed away.""",
)
def b1_cpu_timeline(ctx: Context) -> Drawn:
    data = _bench_hardware(ctx)
    fig, axes = new_figure(len(data), 1, figsize=(6.2, 2.6 * len(data)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, (engine, (run, frame)) in zip(axes, data.items()):
        cluster = cluster_only(frame)
        nodes = sorted(cluster["node"].unique())
        colors = _node_colors(nodes)
        for node in nodes:
            rows = cluster[cluster["node"] == node]
            ax.plot(rows["wall_offset_s"], rows["cpu_busy_pct"], color=colors[node],
                    linewidth=1.0, label=node)
            stats[f"{engine}_{node}_cpu_mean_pct"] = round(float(rows["cpu_busy_pct"].mean()), 1)
        client = frame[~frame.index.isin(cluster.index)]
        for node in sorted(client["node"].unique()):
            rows = client[client["node"] == node]
            ax.plot(rows["wall_offset_s"], rows["cpu_busy_pct"], color=BLUE_RAMP[-1],
                    linewidth=1.0, linestyle=":", label=f"{node} (client)")
        ax.set_title(f"{LABEL[engine]} - CPU per node through the sweep", loc="left")
        ax.set_ylabel("CPU busy (%)")
        ax.legend(ncol=3, fontsize=5)
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "B1", fig, axes, [run for run, _ in data.values()]))


@chart(
    "B2",
    "CPU efficiency",
    """Delivered throughput divided by mean CPU busy across the five cluster nodes,
    per tier. This is the cost-of-replication figure that throughput alone cannot
    give: an engine can be faster and still be spending more machine to get there.
    The client node is excluded from the CPU mean. Note: each tier's CPU is averaged
    from the start of its first repetition to the end of its last, and repetitions
    run in shuffled order, so with more than one repetition per tier the CPU side of
    this ratio is close to the whole sweep's average.""",
)
def b2_cpu_efficiency(ctx: Context) -> Drawn:
    from ..analysis import steady_state

    data = _bench_hardware(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, (run, frame) in data.items():
        cpu = _per_tier(cluster_only(frame), _tier_windows(run), "cpu_busy_pct")
        tps = steady_state.per_tier(run).set_index("concurrency")["mean_total_tps"]
        ratio = (tps / cpu).dropna()
        ax.plot(ratio.index, ratio.values, color=COLOR[engine], marker=MARKER[engine],
                linestyle=DASH[engine], markersize=5, label=LABEL[engine])
        for concurrency, value in ratio.items():
            stats[f"{engine}_c{int(concurrency)}_ops_per_cpu_pct"] = round(float(value), 1)
    ax.set_xlabel("concurrency")
    ax.set_ylabel("ops/s per 1% mean cluster CPU")
    ax.set_title("Throughput delivered per unit of CPU consumed")
    ax.legend()
    return Drawn(stats, save(ctx, "B2", fig, ax, [run for run, _ in data.values()]))


def _cluster_series(cluster: pd.DataFrame, column: str) -> pd.Series:
    """Mean across nodes of one column, on :data:`SERIES_BIN_S` bins."""
    binned = (cluster["wall_offset_s"] // SERIES_BIN_S) * SERIES_BIN_S
    return cluster.assign(_bin=binned).groupby("_bin")[column].mean()


@chart(
    "B3",
    "Disk I/O and busy time",
    """Mean per-node disk throughput and busy time across the cluster. At the current
    profile the working set is roughly 1.5x node RAM, so sustained read traffic here
    is the evidence that the benchmark is disk-bound rather than served from cache
    -- the assumption the whole profile rests on. A second axis carries busy %; it
    is dotted and grey because it is a different quantity, not a third series.""",
)
def b3_disk(ctx: Context) -> Drawn:
    data = _bench_hardware(ctx)
    fig, axes = new_figure(len(data), 1, figsize=(6.2, 2.6 * len(data)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    for ax, (engine, (run, frame)) in zip(axes, data.items()):
        cluster = cluster_only(frame)
        read = _cluster_series(cluster, "disk_read_bytes_per_s") / 1e6
        write = _cluster_series(cluster, "disk_write_bytes_per_s") / 1e6
        busy = _cluster_series(cluster, "disk_busy_pct")
        ax.plot(read.index, read.values, color=COLOR[engine], linewidth=1.2, label="read MB/s")
        ax.plot(write.index, write.values, color=COLOR[engine], linewidth=1.2,
                linestyle="--", label="write MB/s")
        twin = ax.twinx()
        twin.plot(busy.index, busy.values, color=INK_MUTED, linewidth=0.8, linestyle=":")
        twin.set_ylabel("disk busy (%)")
        twin.grid(False)
        twin.spines["top"].set_visible(False)
        ax.set_title(f"{LABEL[engine]} - disk through the sweep", loc="left")
        ax.set_ylabel("mean per-node MB/s")
        ax.legend(fontsize=5.5, loc="upper left")
        stats[f"{engine}_disk_busy_mean_pct"] = round(float(cluster["disk_busy_pct"].mean()), 1)
        stats[f"{engine}_disk_read_mean_mb_s"] = round(
            float(cluster["disk_read_bytes_per_s"].mean()) / 1e6, 2
        )
        stats[f"{engine}_disk_write_mean_mb_s"] = round(
            float(cluster["disk_write_bytes_per_s"].mean()) / 1e6, 2
        )
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "B3", fig, axes, [run for run, _ in data.values()]))


@chart(
    "B4",
    "Memory headroom",
    """Mean available memory across the five cluster nodes. Both engines are
    configured with a cache of a quarter of measured RAM, so this shows the OS page
    cache absorbing the remainder -- and how little headroom is left once a working
    set larger than RAM is being served.""",
)
def b4_memory(ctx: Context) -> Drawn:
    data = _bench_hardware(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    for engine, (run, frame) in data.items():
        cluster = cluster_only(frame)
        series = cluster.groupby(cluster["wall_offset_s"].round())["mem_available_bytes"].mean() / 1e9
        ax.plot(series.index, series.values, color=COLOR[engine], label=LABEL[engine])
        stats[f"{engine}_mem_available_min_gb"] = round(
            float(cluster["mem_available_bytes"].min()) / 1e9, 2
        )
        stats[f"{engine}_mem_total_gb"] = round(float(cluster["mem_total_bytes"].min()) / 1e9, 2)
    ax.set_xlabel("seconds from run start")
    ax.set_ylabel("mean available memory per node (GB)")
    ax.set_title("Memory headroom while the sweep runs")
    ax.legend()
    return Drawn(stats, save(ctx, "B4", fig, ax, [run for run, _ in data.values()]))


@chart(
    "B5",
    "Replication traffic amplification",
    """Total bytes transmitted by the five cluster nodes divided by the operations they
    served, per tier. This is the closest direct measurement the testbed makes of
    what each replication design costs on the wire -- Raft's per-range replication
    against Patroni's WAL streaming. It is a ratio of two measured rates, so it is
    insensitive to the tiers having different durations. Read it as an order of
    magnitude: the link also carries Tailscale and node exporter traffic, which is
    not separated out. Note: the traffic is the mean per node, not the five-node
    total, and each tier's window runs from the start of its first repetition to the
    end of its last, so with more than one repetition per tier it is close to the
    whole sweep's average.""",
)
def b5_replication_traffic(ctx: Context) -> Drawn:
    from ..analysis import steady_state

    data = _bench_hardware(ctx)
    fig, ax = new_figure(figsize=(6.2, 4.0))
    stats: dict = {}
    per_engine = {}
    for engine, (run, frame) in data.items():
        cluster = cluster_only(frame)
        tx = _per_tier(cluster, _tier_windows(run), "net_tx_bytes_per_s")
        tps = steady_state.per_tier(run).set_index("concurrency")["mean_total_tps"]
        per_op = (tx / tps / 1024.0).dropna()
        per_engine[engine] = per_op
        for concurrency, value in per_op.items():
            stats[f"{engine}_c{int(concurrency)}_tx_kib_per_op"] = round(float(value), 2)
    tiers = sorted({int(c) for series in per_engine.values() for c in series.index})
    x = np.arange(len(tiers))
    width = 0.8 / max(1, len(per_engine))
    for index, (engine, series) in enumerate(per_engine.items()):
        values = [float(series.get(c, np.nan)) for c in tiers]
        ax.bar(x + (index - (len(per_engine) - 1) / 2) * width, values, width,
               color=COLOR[engine], label=LABEL[engine])
    ax.set_xticks(x, [f"C={c}" for c in tiers])
    ax.set_xlabel("concurrency tier")
    ax.set_ylabel("cluster network TX per operation (KiB)")
    ax.set_title("Network cost of replicating each operation")
    ax.legend()
    return Drawn(stats, save(ctx, "B5", fig, ax, [run for run, _ in data.values()]))


@chart(
    "B6",
    "Cluster utilisation heatmap",
    """Every cluster node's CPU on one grid, binned onto a common time axis because the
    nodes are polled independently. A single bright row is a single busy machine; a
    uniformly lit panel is work spread across the cluster. One continuous hue, so
    brightness reads as magnitude rather than as category.""",
)
def b6_heatmap(ctx: Context) -> Drawn:
    data = _bench_hardware(ctx)
    fig, axes = new_figure(len(data), 1, figsize=(6.2, 2.4 * len(data)), squeeze=False)
    axes = list(axes[:, 0])
    stats: dict = {}
    image = None
    for ax, (engine, (run, frame)) in zip(axes, data.items()):
        cluster = cluster_only(frame)
        lo, hi = float(cluster["wall_offset_s"].min()), float(cluster["wall_offset_s"].max())
        edges = np.linspace(lo, hi, HEATMAP_BINS + 1)
        binned = pd.cut(cluster["wall_offset_s"], edges, include_lowest=True, labels=False)
        grid = (
            cluster.assign(_bin=binned)
            .pivot_table(index="node", columns="_bin", values="cpu_busy_pct", aggfunc="mean")
            .reindex(columns=range(HEATMAP_BINS))
        )
        image = ax.imshow(
            grid.to_numpy(dtype=float), aspect="auto", cmap=SEQUENTIAL, vmin=0, vmax=100,
            extent=(lo, hi, len(grid) - 0.5, -0.5), interpolation="nearest",
        )
        ax.set_yticks(range(len(grid)), grid.index, fontsize=6)
        ax.invert_yaxis()
        ax.grid(False)
        ax.set_title(f"{LABEL[engine]} - CPU busy %", loc="left")
        bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02)
        bar.set_label("CPU busy (%)", fontsize=6)
        bar.outline.set_visible(False)
        stats[f"{engine}_cpu_peak_pct"] = round(float(np.nanmax(grid.to_numpy(dtype=float))), 1)
    axes[-1].set_xlabel("seconds from run start")
    fig.tight_layout()
    return Drawn(stats, save(ctx, "B6", fig, axes, [run for run, _ in data.values()]))


@chart(
    "B7",
    "Cluster load imbalance",
    """Mean CPU per cluster node over the whole sweep, with a scale-free coefficient of
    variation recorded alongside so that an engine which simply runs hotter
    everywhere is not counted as imbalanced. Read this against the deployment rather
    than against the engines' reputations: this testbed deliberately pins
    CockroachDB's leaseholders to the gateway via lease_preferences and pins
    Patroni's primary to the same node, so neither arm is free to spread work as it
    otherwise might. What the chart measures is how much of the cluster each engine
    still uses under that pinning, which is a property of this configuration and
    must be quoted as one.""",
)
def b7_imbalance(ctx: Context) -> Drawn:
    data = _bench_hardware(ctx)
    means = {
        engine: cluster_only(frame).groupby("node")["cpu_busy_pct"].mean()
        for engine, (_, frame) in data.items()
    }
    nodes = sorted({n for series in means.values() for n in series.index})
    fig, ax = new_figure(figsize=(6.2, 4.0))
    x = np.arange(len(nodes))
    width = 0.8 / max(1, len(means))
    stats: dict = {}
    for index, (engine, series) in enumerate(means.items()):
        ax.bar(x + (index - (len(means) - 1) / 2) * width,
               [float(series.get(n, np.nan)) for n in nodes], width,
               color=COLOR[engine], label=LABEL[engine])
        # Population CV: these five nodes are the whole cluster, not a sample of one.
        cv = float(series.std(ddof=0) / series.mean()) if series.mean() else float("nan")
        stats[f"{engine}_cpu_imbalance_cv"] = round(cv, 3)
        stats[f"{engine}_busiest_node"] = str(series.idxmax())
    ax.set_xticks(x, nodes, rotation=20, ha="right")
    ax.set_ylabel("mean CPU busy over the sweep (%)")
    ax.set_title("Is the work shared across the cluster?")
    ax.legend()
    return Drawn(stats, save(ctx, "B7", fig, ax, [run for run, _ in data.values()]))


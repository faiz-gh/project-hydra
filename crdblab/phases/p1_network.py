"""Phase I: characterisation of the network substrate.

Every latency figure in Phases II-V is bounded by facts this phase establishes,
so it runs first and its output is an input to later pre-flight checks rather
than a decorative appendix. In particular the round-trip matrix determines the
quorum floor: a write committed by a majority of five voting replicas cannot be
faster than the round trip to the second-fastest follower, which converts an
otherwise unfalsifiable latency measurement into one with a physical lower bound
(see ``core/preflight.py``, D8).

The probe is issued from each node in parallel, one SSH session per source, with
all destinations batched into a single remote script. Ping output is parsed by
matching labelled quantities (``time=`` and ``packet loss``) rather than by field
position, for the reasons set out in ``core/workload.py``.
"""

from __future__ import annotations

import re
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..config import Profile, Settings
from ..core import ssh
from ..core.recorder import (
    NETWORK_COLUMNS,
    Manifest,
    MetricsWriter,
    RunDirectory,
    new_run_id,
    utcnow,
)
from ..topology import Node, Topology

#: Enough samples for a stable p99 without making the phase tedious: at 0.1 s
#: spacing, 100 samples is ten seconds per source and every source runs
#: concurrently.
PING_COUNT = 100
PING_INTERVAL_S = 0.1

_TIME_RE = re.compile(r"time=([0-9.]+)\s*ms")
_LOSS_RE = re.compile(r"([0-9.]+)%\s+packet loss")
_SECTION_RE = re.compile(r"^==PING:(?P<dest>[^=]+)==$")
#: ping's own summary, which carries three decimals irrespective of magnitude.
_SUMMARY_RE = re.compile(
    r"rtt\s+min/avg/max/mdev\s*=\s*"
    r"([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms"
)


@dataclass
class LinkStats:
    samples: int
    loss_pct: float
    rtt_min_ms: float | None
    rtt_mean_ms: float | None
    rtt_p50_ms: float | None
    rtt_p95_ms: float | None
    rtt_p99_ms: float | None
    rtt_max_ms: float | None
    rtt_mdev_ms: float | None
    #: Smallest difference the per-packet output could have expressed, derived
    #: from the decimals actually printed. Quantiles are computed from those
    #: lines, so this is the precision to which they are meaningful.
    rtt_resolution_ms: float | None


@dataclass
class NodeProbe:
    node: str
    mtu: int | None = None
    links: dict[str, LinkStats] = field(default_factory=dict)
    error: str | None = None
    #: Verbatim remote output, retained so a dispute about how these figures were
    #: derived can be settled against the original bytes rather than by re-running
    #: (decision 5). The precision defect corrected in :func:`parse_ping` was found
    #: exactly this way.
    raw: str = ""


def _quantile(ordered: list[float], q: float) -> float:
    """Nearest-rank quantile.

    Stated explicitly because the legacy implementation used
    ``times[int(count * q) - 1]``, which is off by one at the boundaries and
    silently returns the wrong element for small samples. With a named definition
    the figure in the results chapter can be described precisely.
    """
    if not ordered:
        raise ValueError("no samples")
    rank = max(1, min(len(ordered), int(-(-len(ordered) * q // 1))))
    return ordered[rank - 1]


def parse_ping(output: str) -> LinkStats:
    """Summarise one ping run.

    Central summary statistics are read from ping's own ``rtt
    min/avg/max/mdev`` line rather than recomputed from the per-packet lines,
    because that line prints three decimals at any magnitude while the per-packet
    lines do not: on this testbed a 25 ms link prints ``time=25.5 ms`` and a
    186 ms link prints ``time=186 ms``. Recomputing the mean from the latter
    would silently quantise the Asian links to whole milliseconds and, worse,
    report their deviation as exactly ``0.0`` across a hundred samples -- a
    figure that would look like an extraordinarily stable intercontinental path
    rather than the measurement artefact it is.

    Quantiles have no equivalent in the summary line and must come from the
    per-packet values, so the resolution those values were printed at is
    recorded alongside them and the quantiles are not rounded beyond it.

    A destination that produced no replies is recorded as 100% loss with null
    latencies rather than being omitted, so a broken link is visible in the
    matrix instead of merely absent from it.
    """
    tokens = _TIME_RE.findall(output)
    times = [float(t) for t in tokens]
    loss_match = _LOSS_RE.search(output)
    loss = float(loss_match.group(1)) if loss_match else 100.0

    if not times:
        return LinkStats(0, loss, None, None, None, None, None, None, None, None)

    # Coarsest precision any per-packet line was printed at.
    decimals = min(len(t.partition(".")[2]) for t in tokens)
    resolution = 10.0 ** -decimals

    ordered = sorted(times)
    summary = _SUMMARY_RE.search(output)
    if summary:
        r_min, r_avg, r_max, r_mdev = (float(g) for g in summary.groups())
    else:
        # No summary line (truncated output); fall back to the per-packet values
        # and accept their coarser precision rather than dropping the link.
        r_min, r_max = ordered[0], ordered[-1]
        r_avg = statistics.fmean(times)
        r_mdev = statistics.pstdev(times) if len(times) > 1 else 0.0

    return LinkStats(
        samples=len(times),
        loss_pct=loss,
        rtt_min_ms=round(r_min, 3),
        rtt_mean_ms=round(r_avg, 3),
        rtt_p50_ms=round(statistics.median(ordered), decimals),
        rtt_p95_ms=round(_quantile(ordered, 0.95), decimals),
        rtt_p99_ms=round(_quantile(ordered, 0.99), decimals),
        rtt_max_ms=round(r_max, 3),
        rtt_mdev_ms=round(r_mdev, 3),
        rtt_resolution_ms=resolution,
    )


def _remote_script(source: Node, destinations: list[Node]) -> str:
    lines = [
        "echo '==MTU=='",
        "cat /sys/class/net/tailscale0/mtu 2>/dev/null || echo UNKNOWN",
    ]
    for dest in destinations:
        lines.append(f"echo '==PING:{dest.host}=='")
        lines.append(
            f"ping -c {PING_COUNT} -i {PING_INTERVAL_S} -W 1 {dest.host} 2>/dev/null || true"
        )
    return "\n".join(lines)


def probe_node(source: Node, destinations: list[Node]) -> NodeProbe:
    """Run the full destination sweep from one source in a single SSH session."""
    probe = NodeProbe(node=source.host)
    script = _remote_script(source, destinations)
    try:
        proc = subprocess.run(
            ssh.build_command(source, "bash -s"),
            input=script,
            capture_output=True,
            text=True,
            timeout=PING_COUNT * PING_INTERVAL_S * len(destinations) + 120,
        )
    except subprocess.TimeoutExpired:
        probe.error = "probe timed out"
        return probe
    probe.raw = proc.stdout
    if proc.returncode != 0:
        probe.error = (proc.stderr or proc.stdout).strip()[:300]
        return probe

    section: str | None = None
    dest: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if section == "PING" and dest is not None:
            probe.links[dest] = parse_ping("\n".join(buffer))

    for line in proc.stdout.splitlines():
        stripped = line.strip()
        match = _SECTION_RE.match(stripped)
        if match:
            flush()
            section, dest, buffer = "PING", match.group("dest").strip(), []
            continue
        if stripped == "==MTU==":
            flush()
            section, dest, buffer = "MTU", None, []
            continue
        if section == "MTU" and stripped.isdigit():
            probe.mtu = int(stripped)
        elif section == "PING":
            buffer.append(stripped)
    flush()
    return probe


def run(
    settings: Settings,
    profile: Profile,
    topology: Topology | None = None,
) -> tuple[RunDirectory, list[NodeProbe]]:
    """Execute Phase I and record it as an immutable run directory."""
    topo = topology or settings.topology
    nodes = list(topo)

    run_dir = RunDirectory(settings.runs_dir, new_run_id("p1-network"))
    manifest = Manifest(
        run_id=run_dir.path.name,
        phase="p1_network",
        profile=profile.to_dict(),
        topology=[
            {
                "name": n.name,
                "host": n.host,
                "provider": n.provider,
                "region": n.region,
                "locality": n.locality,
                "gateway": n.gateway,
            }
            for n in nodes
        ],
        ssh_options=list(ssh.SSH_OPTIONS),
        generator_command=(
            f"ping -c {PING_COUNT} -i {PING_INTERVAL_S} (all pairs, bidirectional)"
        ),
    )

    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        probes = list(
            pool.map(lambda n: probe_node(n, [d for d in nodes if d.host != n.host]), nodes)
        )

    by_host = {n.host: n for n in nodes}
    stamp = utcnow()
    for probe in probes:
        if probe.raw:
            run_dir.raw(f"{probe.node}.ping.txt").write_text(probe.raw)

    with MetricsWriter(run_dir.network_csv, NETWORK_COLUMNS) as writer:
        for probe in probes:
            if probe.error:
                manifest.note(f"{probe.node}: probe failed: {probe.error}")
                continue
            manifest.note(f"{probe.node}: tailscale MTU {probe.mtu}")
            for dest_host, stats in sorted(probe.links.items()):
                writer.write(
                    {
                        "ts_utc": stamp,
                        "source": probe.node,
                        "destination": dest_host,
                        "source_region": by_host[probe.node].region,
                        "destination_region": by_host[dest_host].region
                        if dest_host in by_host
                        else "",
                        "samples": stats.samples,
                        "loss_pct": stats.loss_pct,
                        "rtt_min_ms": stats.rtt_min_ms,
                        "rtt_mean_ms": stats.rtt_mean_ms,
                        "rtt_p50_ms": stats.rtt_p50_ms,
                        "rtt_p95_ms": stats.rtt_p95_ms,
                        "rtt_p99_ms": stats.rtt_p99_ms,
                        "rtt_max_ms": stats.rtt_max_ms,
                        "rtt_mdev_ms": stats.rtt_mdev_ms,
                        "rtt_resolution_ms": stats.rtt_resolution_ms,
                    }
                )

    manifest.finished_utc = utcnow()
    run_dir.write_manifest(manifest)
    return run_dir, probes


def summarise(probes: list[NodeProbe], topo: Topology) -> dict[str, Any]:
    """Derive the quantities later phases depend on."""
    from ..core.preflight import quorum_floor_ms

    gateway = topo.gateway
    gw = next((p for p in probes if p.node == gateway.host), None)
    if gw is None or not gw.links:
        return {}
    rtts = {d: s.rtt_mean_ms for d, s in gw.links.items() if s.rtt_mean_ms is not None}
    if not rtts:
        return {}
    return {
        "gateway": gateway.host,
        "gateway_rtt_ms": rtts,
        "quorum_floor_ms": round(quorum_floor_ms(rtts, voters=len(topo)), 3),
        "voters": len(topo),
    }

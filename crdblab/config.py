"""Experiment profiles (``profiles/*.yaml``) and runtime settings.

The resolved profile is copied verbatim into every run manifest.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .topology import DEFAULT_TOPOLOGY, Topology

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "runs"

#: Password of the ``root`` role created by ``bootstrap-patroni.tftpl``.
DEFAULT_PG_PASSWORD = "rootpassword"

#: PostgreSQL port on each cluster node (``Node.sql_port`` is CockroachDB's).
PG_SQL_PORT = 5432

#: The client node's pgbouncer, which forwards to HAProxy and on to the primary.
PG_GENERATOR_HOSTPORT = "127.0.0.1:6432"

#: Per-host connect bound in a multi-host DSN. 2 s is libpq's minimum and well
#: above the testbed's worst RTT (~230 ms).
PG_CONNECT_TIMEOUT_S = 2

#: How long an established connection may go unanswered before the kernel drops
#: it. Looser than the probe's 5 s statement timeout; see ``pg_direct_dsn``.
PG_TCP_USER_TIMEOUT_MS = 10_000

#: TCP keepalives, so a black-holed connection is detected rather than waited on.
PG_KEEPALIVE_IDLE_S = 2
PG_KEEPALIVE_INTERVAL_S = 2
PG_KEEPALIVE_COUNT = 3


def pg_generator_dsn(database: str, password: str) -> str:
    """Connection string for the generator: the client node's local pgbouncer.

    ``cockroach workload`` accepts only one URL, and HAProxy makes that URL
    follow a failover. pgbouncer strips the ``allow_unsafe_internals`` startup
    parameter the generator sends, which PostgreSQL would otherwise reject.
    """
    return (
        f"postgresql://root:{quote(password, safe='')}@{PG_GENERATOR_HOSTPORT}"
        f"/{database}?sslmode=disable"
    )


def pg_direct_dsn(topology: Topology, database: str, password: str) -> str:
    """Multi-host DSN for the audit writer and RTO probe, bypassing HAProxy.

    libpq tries each host in turn and keeps the writable one
    (``target_session_attrs=read-write``). The gateway goes first because it is
    the designated primary.

    Connections are bounded at the TCP layer (connect timeout, keepalives,
    ``tcp_user_timeout``) so a partition that black-holes an open socket is
    detected instead of silently stalling the instrument. A tighter statement
    timeout is avoided on purpose: a write that blocks through a failover and
    then commits is the most precise observation of recovery.
    """
    gateway = topology.gateway
    ordered = [gateway] + [n for n in topology.nodes if n.host != gateway.host]
    hosts = ",".join(f"{node.host}:{PG_SQL_PORT}" for node in ordered)
    return (
        f"postgresql://root:{quote(password, safe='')}@{hosts}/{database}"
        "?sslmode=disable&target_session_attrs=read-write"
        f"&connect_timeout={PG_CONNECT_TIMEOUT_S}"
        f"&keepalives=1&keepalives_idle={PG_KEEPALIVE_IDLE_S}"
        f"&keepalives_interval={PG_KEEPALIVE_INTERVAL_S}"
        f"&keepalives_count={PG_KEEPALIVE_COUNT}"
        f"&tcp_user_timeout={PG_TCP_USER_TIMEOUT_MS}"
    )


#: The client node's HAProxy, which follows Patroni's leader. Used for
#: ``DB_URI`` (psql, data loading, ``capture``).
PG_HAPROXY_HOSTPORT = "127.0.0.1:5000"


def default_db_uri(engine: str, topology: Topology, password: str) -> str:
    """Derive ``DB_URI`` for the deployed engine.

    * **cockroachdb**: every cluster member, gateway first, each with ``:26257``.
    * **postgresql**: the client node's HAProxy, with the ``root`` password.
    """
    if engine == "cockroachdb":
        gateway = topology.gateway
        ordered = [gateway] + [n for n in topology.nodes if n.host != gateway.host]
        hosts = ",".join(f"{node.host}:{node.sql_port}" for node in ordered)
        return f"postgresql://root@{hosts}/ycsb?sslmode=disable"
    if engine == "postgresql":
        return (
            f"postgresql://root:{quote(password, safe='')}@{PG_HAPROXY_HOSTPORT}"
            "/ycsb?sslmode=disable"
        )
    raise ValueError(f"unknown engine: {engine!r} (expected 'cockroachdb' or 'postgresql')")


DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None) -> bool:
    """Load the project's ``.env`` into the environment.

    Resolved relative to the package, not the working directory. Existing
    environment variables win. Returns ``True`` if a file was read.
    """
    target = Path(path) if path is not None else DEFAULT_ENV_FILE
    if not target.exists():
        return False
    from dotenv import load_dotenv

    load_dotenv(target, override=False)
    return True


@dataclass
class WorkloadSpec:
    generator: str = "ycsb"
    #: ycsb mix: CUSTOM with an explicit 80/20 read/update split, uniform keys.
    ycsb_workload: str = "CUSTOM"
    read_freq: float = 0.8
    update_freq: float = 0.2
    request_distribution: str = "uniform"
    #: kv only.
    read_percent: int = 80
    duration_s: int = 60
    warmup_s: int = 5
    display_every_s: int = 1
    concurrencies: tuple[int, ...] = (10, 50, 100, 200)
    repetitions: int = 3
    randomise_tier_order: bool = True
    cooldown_s: int = 15

    # ``seed`` and ``insert_count`` must match the values used at load time, or
    # every lookup silently matches no rows.
    seed: int = 42
    insert_count: int = 125_000
    cycle_length: int = 1_000_000
    block_bytes: int = 256

    @property
    def expected_ticks_per_tier(self) -> int:
        return self.duration_s // self.display_every_s


@dataclass
class ChaosSpec:
    duration_s: int = 180
    inject_at_s: int = 60
    concurrency: int = 100
    #: Node leading the write path (leaseholder or Patroni primary). On PostgreSQL
    #: the live primary is resolved at fault time and overrides this.
    target: str = "gcp-1"
    recovery_threshold: float = 0.80
    recovery_hold_s: int = 10
    #: Minimum generator sampling after the fault (after the heal, in ``recover``
    #: mode). ``duration_s`` is extended to cover it, never shortened.
    min_post_fault_s: int = 60
    #: How long pre-flight waits for leaseholders to return to the gateway region
    #: after a previous chaos run (CockroachDB only).
    leaseholder_settle_s: int = 300
    #: RPO audit writer cadence. Its real resolution is bounded by the quorum
    #: write cost (~70 ms), which is why the RTO probe exists.
    audit_interval_s: float = 0.02

    # High-frequency RTO probe (crdblab/core/rto_probe.py). More workers resolve
    # outage edges more finely but add load; the setting is recorded per run.
    probe_enabled: bool = True
    #: Dispatch cadence; the achieved rate is measured per run.
    probe_interval_s: float = 0.002
    #: In-flight canary writes. Resolution is roughly write cost / workers
    #: (~123 ms / 8 = ~21-29 ms from the client node).
    probe_workers: int = 8
    #: Generous on purpose: a write that blocks through a failover and then
    #: commits is the most precise observation of recovery.
    probe_statement_timeout_ms: int = 5000
    probe_connect_timeout_s: float = 2.0
    probe_table: str = "rto_canary"


@dataclass
class HardwareMetricsSpec:
    """Per-node CPU/memory/disk/network polling during Phase II-IV."""

    enabled: bool = True
    #: Polling cadence across all six nodes.
    sample_interval_s: float = 5.0


@dataclass
class Profile:
    name: str
    workload: WorkloadSpec = field(default_factory=WorkloadSpec)
    chaos: ChaosSpec = field(default_factory=ChaosSpec)
    hardware_metrics: HardwareMetricsSpec = field(default_factory=HardwareMetricsSpec)
    tps_ceiling: float = 20_000.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, name_or_path: str) -> Profile:
        path = Path(name_or_path)
        if not path.exists():
            path = DEFAULT_PROFILE_DIR / f"{name_or_path}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"no profile named {name_or_path!r} at {path}")
        raw = yaml.safe_load(path.read_text()) or {}
        workload = WorkloadSpec(**{**asdict(WorkloadSpec()), **raw.get("workload", {})})
        workload.concurrencies = tuple(workload.concurrencies)
        chaos = ChaosSpec(**{**asdict(ChaosSpec()), **raw.get("chaos", {})})
        hardware_metrics = HardwareMetricsSpec(
            **{**asdict(HardwareMetricsSpec()), **raw.get("hardware_metrics", {})}
        )
        return cls(
            name=raw.get("name", path.stem),
            workload=workload,
            chaos=chaos,
            hardware_metrics=hardware_metrics,
            tps_ceiling=float(raw.get("tps_ceiling", 20_000.0)),
        )


@dataclass
class Settings:
    db_uri: str | None = None
    runs_dir: Path = DEFAULT_RUNS_DIR
    topology: Topology = field(default_factory=lambda: DEFAULT_TOPOLOGY)
    #: ``root`` password for PostgreSQL (``PG_PASSWORD`` in ``.env``). Must match
    #: ``terraform/scripts/bootstrap-patroni.tftpl``.
    pg_password: str = DEFAULT_PG_PASSWORD

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_uri=os.environ.get("DB_URI"),
            runs_dir=Path(os.environ.get("CRDBLAB_RUNS_DIR", DEFAULT_RUNS_DIR)),
            pg_password=os.environ.get("PG_PASSWORD", DEFAULT_PG_PASSWORD),
        )

    def require_db_uri(self) -> str:
        if not self.db_uri:
            raise RuntimeError("DB_URI is not set; copy .env.example to .env and populate it")
        return self.db_uri

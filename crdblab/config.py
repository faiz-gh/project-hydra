"""Experiment profiles and runtime settings.

Experimental parameters live in a version-controlled YAML profile rather than
as constants scattered through the scripts, so that the exact sweep used for a
figure is a citable artefact. The profile is copied verbatim into every run
manifest.
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

#: Password of the ``root`` role created by ``bootstrap-patroni.tftpl``. Kept
#: here rather than inline in each DSN so the harness and the provisioning
#: template have exactly one thing to agree on.
DEFAULT_PG_PASSWORD = "rootpassword"

#: PostgreSQL's own port on each cluster node. Distinct from ``Node.sql_port``
#: (26257), which is CockroachDB's and is what that engine's DSNs use.
PG_SQL_PORT = 5432

#: The client node's pgbouncer, which forwards to the local HAProxy, which
#: resolves to whichever node Patroni currently reports as primary (both are
#: installed by ``bootstrap-client.tftpl``).
PG_GENERATOR_HOSTPORT = "127.0.0.1:6432"

#: How long libpq may spend on a single host in a multi-host DSN before moving
#: on. Only ``pg_direct_dsn`` uses it; see that function for why the bound is a
#: measurement decision. libpq's minimum is 2 s (it silently raises anything
#: lower), and the slowest link on this testbed is 230 ms, so 2 s is both the
#: floor and comfortably clear of a healthy-but-distant node.
PG_CONNECT_TIMEOUT_S = 2

#: How long an ESTABLISHED connection may go unanswered before libpq's kernel
#: gives up on it, in milliseconds. Distinct from ``PG_CONNECT_TIMEOUT_S``,
#: which bounds only the *opening* of a connection, and from the probe's
#: ``statement_timeout``, which is server-side and therefore cannot arrive when
#: packets cannot. See ``pg_direct_dsn`` for why this exists and why it is
#: deliberately looser than that statement timeout.
PG_TCP_USER_TIMEOUT_MS = 10_000

#: Keepalive geometry for the same purpose: probe an idle-looking connection so
#: that a black hole is discovered rather than waited on. Idle 2 s so detection
#: begins promptly relative to the ~70 ms writes these clients issue, and the
#: interval/count are what ``PG_TCP_USER_TIMEOUT_MS`` then bounds overall.
PG_KEEPALIVE_IDLE_S = 2
PG_KEEPALIVE_INTERVAL_S = 2
PG_KEEPALIVE_COUNT = 3


def pg_generator_dsn(database: str, password: str) -> str:
    """Connection string for the generator: one host, the local pgbouncer.

    Two hops, each there for a reason the other cannot cover.

    HAProxy is why *one* URL suffices. The generator is
    ``cockroach workload run``, which must be given exactly one URL -- more
    than one and it dials its ``--concurrency`` connections serially, ~2.65 s
    each (see ``bench.py``'s module docstring) -- and HAProxy is what makes a
    single URL follow a failover.

    pgbouncer is why the generator can speak to PostgreSQL **at all**.
    ``cockroach workload`` v26.3.0 sends ``allow_unsafe_internals`` as a
    startup parameter on every connection it opens; PostgreSQL rejects unknown
    startup parameters outright, so both ``workload init`` and ``workload run``
    die at connect with ``FATAL: unrecognized configuration parameter
    "allow_unsafe_internals" (SQLSTATE 42704)``. No flag on the tool suppresses
    it -- the only related knob is a CockroachDB *cluster* setting -- so the
    alternative was to drive the two arms of the comparison with two different
    generator builds, which is a confound in the one component the design
    requires to be identical. pgbouncer's ``ignore_startup_parameters`` drops
    the parameter and passes everything else through; it runs in session
    pooling mode, so it is a passthrough rather than a semantic change.

    The cost is disclosed rather than hidden: the PostgreSQL path carries two
    local proxy hops that the CockroachDB path does not have. Both are on the
    client node's loopback, ahead of the wide-area link the measurement is
    about.
    """
    return (
        f"postgresql://root:{quote(password, safe='')}@{PG_GENERATOR_HOSTPORT}"
        f"/{database}?sslmode=disable"
    )


def pg_direct_dsn(topology: Topology, database: str, password: str) -> str:
    """Connection string for measurement clients: every node, primary selected.

    Deliberately *not* through HAProxy. The RPO audit writer and the RTO probe
    exist to observe the cluster through a fault, and routing both through one
    proxy on the client node makes them observations of the proxy as much as of
    the cluster: a hiccup there is indistinguishable from an outage, its
    ``on-marked-down shutdown-sessions`` drops their in-flight connections at
    every failover, and two measurements the design keeps independent would
    share a single point of failure. libpq's own multi-host support does the
    same job in the client: it tries each host and, with
    ``target_session_attrs=read-write``, keeps the one that is not in recovery
    -- i.e. the primary. This is the direct counterpart of the multi-host DSN
    the CockroachDB branch already uses for these two clients, and it is safe
    for the same reason: these are single connections (or a small worker pool),
    not ``--concurrency``-many, so the serial-dial cost that rules multi-host
    out for the generator does not apply.

    Two details about the host list are load-bearing.

    **The gateway goes first.** libpq walks the list in order, and every host
    it tries before the primary costs a full connect attempt. The gateway is
    the designated Patroni primary (``bootstrap-patroni.tftpl`` pins it and
    ``preflight.check_patroni_primary_placement`` asserts it), so putting it
    first normally makes the very first attempt the winning one. In
    ``topology.nodes`` order it was *last*, behind two Azure nodes measured at
    204-230 ms RTT on 2026-09-09 -- a cost paid twice per tier by the row-match
    probe, 24 times across a thesis sweep, for nothing.

    **``connect_timeout`` is bounded, and that is a measurement decision rather
    than a tuning knob.** In ``recover`` mode the fault is a network partition
    (``tailscale down``), so the partitioned node does not refuse connections,
    it swallows them: without a bound, libpq waits out the OS TCP timeout
    before moving to the next host, and an RTO derived from these clients would
    be reporting the client library's timeout rather than the cluster's
    failover. Bounding it keeps the number a property of the database. It is
    set well above the 230 ms worst-case RTT so a merely-distant node is never
    mistaken for a dead one, and it is disclosed alongside the two proxy hops
    on the generator path.

    **An ESTABLISHED connection is bounded too, at the TCP layer, and that is
    what keeps a silent instrument from reading as a healthy one.**
    ``connect_timeout`` covers only the opening of a connection. In ``recover``
    mode the fault is a partition, and the connections these two clients
    already hold are the ones that matter: ``tailscale down`` does not close
    them, it black-holes them, and a write in flight over a black-holed socket
    never returns. On 2026-09-09 that stopped both instruments dead 3.6 s after
    the fault -- the probe's last attempt completed at offset 28.42 s of a 45 s
    run with **515 of 515 attempts recorded ``ok`` and not one timeout,
    conn_error or refusal**, and the audit writer's last acknowledgement landed
    at 28.49 s and was followed by a single ``ambiguous`` row 48 seconds later.
    Neither had observed anything after the fault, and the harness reported the
    resulting silence as an availability RTO of **0.082 s** and "no
    interruption in served writes was detectable", for an outage the generator
    recorded as two consecutive ticks of ``tps = 0.0``. Understatement in the
    flattering direction, from two instruments that are supposed to be
    independent, agreeing because they had failed the same way.

    **Why TCP and not a tighter statement timeout.** ``rto_probe``'s module
    docstring states a design commitment this must not break: *a blocked write
    is the measurement, not a failed one*. During a lease transfer the INSERT
    waits and then commits, and its completion timestamp is a direct
    observation of the instant service resumed -- a short client deadline would
    abort exactly the write whose return times the recovery, and replace a
    millisecond-accurate edge with a poll at the timeout period. TCP-level
    bounds leave that case alone: a server genuinely working on a query still
    has a live kernel that acknowledges keepalives, so the connection survives
    for as long as the server is reachable. They fire only when the *peer* is
    unreachable, which is the partition and nothing else. ``tcp_user_timeout``
    is set to 10 s, deliberately **looser** than the probe's own 5 s
    server-side ``statement_timeout``, so the client-side bound can only ever
    fire in the case where the server never received the statement or its
    answer never came back -- never in preference to the server's own reply.
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
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None) -> bool:
    """Populate the process environment from the project's ``.env`` file.

    The connection string is held outside version control, so every entry point
    must load it before :meth:`Settings.from_env` is consulted. The file is
    resolved relative to the package rather than the working directory: a
    measurement invoked from an arbitrary directory must not silently fall back
    to a different credential, or worse, to none at all and a confusing failure
    partway through a sweep.

    Returns ``True`` if a file was found and read. Values already present in the
    environment take precedence, so an explicit ``DB_URI=... crdblab ...`` still
    overrides the file.
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
    #: ycsb mix. CUSTOM with an explicit split preserves the original design's
    #: 80/20 read/write ratio so corrected figures stay comparable with the
    #: legacy ones; uniform matches kv's scattered keys rather than CUSTOM's
    #: zipfian default, which would concentrate accesses on a hot subset.
    ycsb_workload: str = "CUSTOM"
    read_freq: float = 0.8
    update_freq: float = 0.2
    request_distribution: str = "uniform"
    #: kv only, retained to reproduce the legacy configuration for comparison.
    read_percent: int = 80
    duration_s: int = 60
    warmup_s: int = 5
    display_every_s: int = 1
    concurrencies: tuple[int, ...] = (10, 50, 100, 200)
    repetitions: int = 3
    randomise_tier_order: bool = True
    cooldown_s: int = 15

    # Working set. 125k ycsb rows is ~205 MB, sized to stay resident in page
    # cache on a 3 GB node so throughput remains CPU- and network-bound and
    # failover is not confounded by storage I/O.
    #
    # ``seed`` must be identical at load time and at run time. The generator
    # defaults to a fresh seed per invocation, which silently decouples the
    # loaded keyspace from the queried one and yields a 0.0 row-match rate
    # (defect D8). It is recorded here, and hence in every run manifest, because
    # a run whose seed is unknown cannot be reproduced or interpreted.
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
    #: For CockroachDB: the node ``lease_preferences`` pins as leaseholder,
    #: asserted by ``preflight.check_leaseholder_placement`` before the fault
    #: fires. For PostgreSQL: the node ``bootstrap-patroni.tftpl`` pins as
    #: primary and ``preflight.check_patroni_primary_placement`` restores by
    #: switchover before the fault fires -- the same node, deliberately, since
    #: where the write path is led from is a property of the deployment rather
    #: than of the engine. It is still never *assumed*:
    #: ``p4_chaos.resolve_patroni_primary`` reads the live cluster and faults
    #: whichever node actually answers as primary, overriding this value if it
    #: disagrees.
    target: str = "gcp-1"
    recovery_threshold: float = 0.80
    recovery_hold_s: int = 10
    #: The generator must keep sampling for at least this long *after* the
    #: fault. ``duration_s`` alone cannot guarantee it: ``inject_at_s`` is
    #: measured from the generator's first sample, so a profile that moved the
    #: injection later without lengthening the run would silently shrink the
    #: post-fault series -- and the post-fault series is the measurement. The
    #: run is extended to ``inject_at_s + min_post_fault_s`` when
    #: ``duration_s`` is shorter than that; it is never shortened.
    #:
    #: In ``recover`` mode it is counted from the instant the partition heals
    #: rather than from the fault, because until then there is no recovery to
    #: observe -- see ``p4_chaos.generator_duration_s``, which adds
    #: ``RECOVER_HEAL_DELAY_S`` for that mode only.
    min_post_fault_s: int = 60
    #: How long pre-flight may wait for ``ycsb``'s leaseholders to return to the
    #: gateway's region before refusing to measure. A chaos run that follows
    #: another chaos run starts against a cluster whose lease placement is still
    #: being restored by the replication queue: Phase III's partition moved both
    #: leaseholders to Linode and Phase IV, starting immediately afterwards,
    #: read that and aborted. The assertion is unchanged -- placement must be
    #: correct before anything is measured -- this only lets the cluster finish
    #: converging first. CockroachDB only -- Patroni's primary is restored by an
    #: explicit switchover rather than by waiting, because it never fails back
    #: on its own (see ``preflight.check_patroni_primary_placement``).
    leaseholder_settle_s: int = 300
    #: Cadence of the RPO audit writer, which writes one sequence at a time on
    #: one connection. It bounds the resolution of the availability RTO derived
    #: from ``audit.csv`` at the cost of a quorum write (~69 ms here), not at this
    #: value. The high-frequency probe below exists because of that bound; this
    #: number is left alone so the RPO series keeps the cadence its recorded runs
    #: were measured at.
    audit_interval_s: float = 0.02

    # --- high-frequency RTO probe ----------------------------------------
    #
    # A second, independent client on a background path, measuring how long the
    # database could not serve a write. It is separate from the RPO audit above
    # rather than a faster setting of it because the two are paced for different
    # questions; see crdblab/core/rto_probe.py.
    #
    # These are profile parameters rather than constants because they are the
    # dial between resolution and perturbation -- more workers observe the outage
    # edges more finely and add more writes to the cluster being measured -- and a
    # run must record which way that dial was set. They land in the manifest with
    # the rest of the profile.
    probe_enabled: bool = True
    #: Dispatch cadence. Sub-5 ms. What the probe *achieves* is bounded by
    #: ``probe_workers`` over the write latency and is measured per run.
    probe_interval_s: float = 0.002
    #: Eight in-flight writes. The gap between observations is the write cost over
    #: the pool size. From the client node -- where the probe now runs -- a canary
    #: write costs ~123 ms, so 8 workers resolve to 21-29 ms at ~59 writes/s.
    #: (From the operator's workstation the same write cost 332 ms and 8 workers
    #: resolved only 64 ms at 21 writes/s, which is why the probe moved.)
    #: Concurrency is the cheap axis here and the dispatch interval is not.
    #: See crdblab/core/rto_probe.py and crdblab/core/remote_probe.py.
    probe_workers: int = 8
    #: Generous on purpose: a write that blocks through a lease transfer and then
    #: commits is the most precise observation of recovery there is, and a tight
    #: timeout would abort it.
    probe_statement_timeout_ms: int = 5000
    probe_connect_timeout_s: float = 2.0
    probe_table: str = "rto_canary"


@dataclass
class HardwareMetricsSpec:
    """Per-node CPU/memory/disk/network polling during Phase II-IV.

    A profile-declared tradeoff, not a hardcoded constant, for the same reason
    ``probe_workers``/``probe_interval_s`` above are: it trades resolution
    against overhead, and a run must record which way that dial was set. It
    lands in the manifest with the rest of the profile via ``Profile.to_dict``.
    """

    enabled: bool = True
    #: Polling cadence across all 6 nodes (5 cluster + client). 5s keeps
    #: aggregate scrape traffic light (6 requests/5s) while resolving load
    #: transitions well inside the 15s LIVENESS_SETTLE_S window
    #: crdblab/analysis/resilience.py already excludes from settling analysis.
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
    def load(cls, name_or_path: str) -> "Profile":
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
    #: Password for the ``root`` role on the PostgreSQL/Patroni deployment.
    #:
    #: Required, unlike CockroachDB's, which runs ``--insecure`` and accepts
    #: ``root`` with no password at all. Patroni's bootstrap writes a `pg_hba`
    #: of ``host all all 0.0.0.0/0 md5``, so *every* connection the harness
    #: makes over TCP -- the generator, the RPO audit writer, the RTO probe
    #: agent, the DDL that creates their tables -- is refused without one. The
    #: default matches the ``root`` user created by
    #: ``terraform/scripts/bootstrap-patroni.tftpl``; change both together, or
    #: set ``PG_PASSWORD`` in ``.env``.
    pg_password: str = DEFAULT_PG_PASSWORD

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_uri=os.environ.get("DB_URI"),
            runs_dir=Path(os.environ.get("CRDBLAB_RUNS_DIR", DEFAULT_RUNS_DIR)),
            pg_password=os.environ.get("PG_PASSWORD", DEFAULT_PG_PASSWORD),
        )

    def require_db_uri(self) -> str:
        if not self.db_uri:
            raise RuntimeError("DB_URI is not set; copy .env.example to .env and populate it")
        return self.db_uri

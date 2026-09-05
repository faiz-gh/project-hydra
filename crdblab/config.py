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

import yaml

from .topology import DEFAULT_TOPOLOGY, Topology

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "runs"
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
    target: str = "linode-2"
    recovery_threshold: float = 0.80
    recovery_hold_s: int = 10
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
    #: Eight in-flight writes. From the workstation a canary write costs ~370 ms,
    #: dominated by the link rather than by the ~70 ms quorum, so the gap between
    #: observations is ~370/workers. Measured live: 8 workers resolve to 125 ms at
    #: 18 writes/s, 24 to 64 ms at 43 writes/s. Concurrency is the cheap axis here
    #: and the dispatch interval is not. See crdblab/core/rto_probe.py.
    probe_workers: int = 8
    #: Generous on purpose: a write that blocks through a lease transfer and then
    #: commits is the most precise observation of recovery there is, and a tight
    #: timeout would abort it.
    probe_statement_timeout_ms: int = 5000
    probe_connect_timeout_s: float = 2.0
    probe_table: str = "rto_canary"


@dataclass
class Profile:
    name: str
    workload: WorkloadSpec = field(default_factory=WorkloadSpec)
    chaos: ChaosSpec = field(default_factory=ChaosSpec)
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
        return cls(
            name=raw.get("name", path.stem),
            workload=workload,
            chaos=chaos,
            tps_ceiling=float(raw.get("tps_ceiling", 20_000.0)),
        )


@dataclass
class Settings:
    db_uri: str | None = None
    runs_dir: Path = DEFAULT_RUNS_DIR
    topology: Topology = field(default_factory=lambda: DEFAULT_TOPOLOGY)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_uri=os.environ.get("DB_URI"),
            runs_dir=Path(os.environ.get("CRDBLAB_RUNS_DIR", DEFAULT_RUNS_DIR)),
        )

    def require_db_uri(self) -> str:
        if not self.db_uri:
            raise RuntimeError("DB_URI is not set; copy .env.example to .env and populate it")
        return self.db_uri

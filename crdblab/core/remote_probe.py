"""Run the RTO probe on the client node and read its observations back.

The probe measures how long the database could not serve a write. Where it runs
from is therefore part of the measurement, not an implementation detail. It used
to run in the harness process on the operator's workstation, and that cost the
figure twice over:

* **Resolution.** Every canary write carried a workstation-to-cluster round trip
  -- 332 ms median over Tailscale on this testbed -- so a pool of eight workers
  achieved 21.4 writes a second against the 500 the profile dispatched, and the
  probe could not resolve an interruption shorter than 64 ms. The nominal
  ``probe_interval_s`` of 2 ms was never the binding constraint; the operator's
  link was.
* **Attribution.** A workstation-side network hiccup inside the fault window is
  indistinguishable from a cluster-side outage. The probe is supposed to be an
  independent witness to the *database's* availability, and a witness sitting on
  a domestic uplink is not independent of it.

Running the probe on ``crdb-client-1`` -- the dedicated generator node, already a
Tailscale peer of every cluster member and already the machine the workload is
driven from -- leaves the resolution bounded by the cost of a quorum write
divided by the worker count, which is the bound the design intends.

**It is the same code.** :mod:`crdblab.core.rto_probe` is copied to the client
node and executed there with ``python3 -m``; nothing is reimplemented for the
remote case. A second implementation would be a second thing to keep in step
with the analysis that reads its output, and two copies of one measurement
drifting apart is the failure this project exists to rule out.

**The two clocks are reconciled by measurement, not assumption.** The agent
reports offsets on its own monotonic clock from its own epoch. Both epochs are
also stamped in UTC, and the conversion between them is their difference. What
makes that legitimate is that both machines run chrony and
``preflight.check_clock_offset`` asserts the NTP offset of the client node
before the run proceeds -- 0.01 ms measured against a 250 ms limit. The residual
offset is recorded alongside the converted attempts as the uncertainty on the
conversion, so the number in the run directory carries its own error bar. This
is D5: a clock whose relationship to the others is stated rather than measured
is how a fault time comes to be drawn against the wrong part of a series.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..topology import Node
from . import ssh
from .rto_probe import (
    AGENT_RESULT_KEY,
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_INTERVAL_S,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    DEFAULT_TABLE,
    DEFAULT_WORKERS,
    ProbeAttempt,
    measure_rto,
    summarise,
)

#: Where the agent's copy of the package lives on the client node. Under /tmp
#: because it is a copy of code that is versioned here, not state: it is
#: rewritten from this checkout before every run, so a stale copy from an
#: earlier revision can never be the thing that runs.
AGENT_ROOT = "/tmp/crdblab-probe-agent"

#: The only files the agent needs. `rto_probe` imports the standard library and
#: `recorder` (itself standard-library only), plus `psycopg` at connect time.
#: Keeping this list explicit rather than shipping the whole package is what
#: keeps the agent's dependency surface small enough to state in one sentence.
AGENT_FILES = (
    "crdblab/__init__.py",
    "crdblab/core/__init__.py",
    "crdblab/core/recorder.py",
    "crdblab/core/rto_probe.py",
)


class RemoteProbeError(RuntimeError):
    """The agent could not be installed or started on the client node."""


def _parse_utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def check_agent_prerequisites(node: Node) -> tuple[bool, str]:
    """Can the client node run the probe at all?

    Checked before the measurement rather than discovered during it: a probe
    that fails to start mid-run costs the whole chaos run, and an agent that
    cannot import ``psycopg`` fails at its first write rather than at launch,
    which would look like a total outage from the first sample onward.
    """
    result = ssh.run(
        node,
        "python3 -c 'import psycopg; print(psycopg.__version__)'",
        timeout=30,
    )
    if result.returncode == 0:
        return True, f"python3 with psycopg {result.stdout.strip()}"
    return False, (
        f"{node.host} cannot import psycopg "
        f"({(result.stderr or result.stdout).strip().splitlines()[-1:] or ['no output']}); "
        "install it with: sudo apt-get install -y python3-psycopg (or pip install psycopg)"
    )


def install_agent(node: Node, package_root: Path) -> None:
    """Copy this revision of the probe onto the client node.

    Copied every run rather than provisioned once by cloud-init: the agent must
    be the code in this working tree, so that a run's observations are
    attributable to the same git revision the manifest records.
    """
    files = [package_root / name for name in AGENT_FILES]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise RemoteProbeError(f"agent source files missing: {', '.join(missing)}")

    tar = subprocess.run(
        ["tar", "-cf", "-", "-C", str(package_root), *AGENT_FILES],
        capture_output=True,
    )
    if tar.returncode != 0:
        raise RemoteProbeError(f"could not archive agent: {tar.stderr.decode().strip()}")

    push = subprocess.run(
        ssh.build_command(node, f"rm -rf {AGENT_ROOT} && mkdir -p {AGENT_ROOT} && tar -xf - -C {AGENT_ROOT}"),
        input=tar.stdout,
        capture_output=True,
        timeout=60,
    )
    if push.returncode != 0:
        raise RemoteProbeError(
            f"could not install agent on {node.host}: {push.stderr.decode().strip()}"
        )


class RemoteRtoProbe:
    """The probe, running on ``node``, presented as the local one.

    Exposes the same surface :class:`crdblab.core.rto_probe.RtoProbe` does where
    :mod:`crdblab.phases.p4_chaos` touches it -- ``attempts``, ``summary()``,
    ``rto()``, ``error``, and the context-manager protocol -- so the phase does
    not branch on where the probe runs.

    Like the local probe, it never raises out of itself into the run: a probe
    that could abort the measurement it is observing would be a new failure mode
    for the phase. Anything fatal lands in :attr:`error` for the caller to
    record.
    """

    def __init__(
        self,
        node: Node,
        dsn: str,
        *,
        package_root: Path,
        duration_s: float,
        table: str = DEFAULT_TABLE,
        interval_s: float = DEFAULT_INTERVAL_S,
        workers: int = DEFAULT_WORKERS,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        epoch_monotonic: float,
        epoch_utc: str,
        log_path: Path | None = None,
    ) -> None:
        self.node = node
        self.dsn = dsn
        self.package_root = package_root
        self.duration_s = float(duration_s)
        self.table = table
        self.interval_s = float(interval_s)
        self.workers = int(workers)
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.connect_timeout_s = float(connect_timeout_s)
        #: The harness's own clock zero, and the UTC instant it corresponds to.
        #: Every offset the agent reports is rebased onto this pair.
        self.epoch_monotonic = float(epoch_monotonic)
        self.epoch_utc = epoch_utc
        self.log_path = log_path

        self.attempts: list[ProbeAttempt] = []
        self.error: str | None = None
        #: Seconds to add to an agent offset to place it on the harness clock.
        #: Measured from the two epochs, not assumed to be zero.
        self.epoch_skew_s: float | None = None
        self.agent_epoch_utc: str | None = None
        #: What the agent computed about itself, kept beside -- never instead of
        #: -- the summary this side derives from the attempts. Two summaries of
        #: one series that disagree is information.
        self.agent_summary: dict[str, Any] | None = None
        self.ticks = 0
        self.dispatch_saturation = 0
        self.ticks_spaced_out = 0

        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._log_handle = None

    # --- lifecycle --------------------------------------------------------

    def _remote_command(self) -> str:
        dsn = self.dsn.replace("'", "'\\''")
        return (
            f"cd {AGENT_ROOT} && PYTHONUNBUFFERED=1 python3 -m crdblab.core.rto_probe "
            f"--dsn '{dsn}' --table {self.table} --interval-s {self.interval_s} "
            f"--workers {self.workers} "
            f"--statement-timeout-ms {self.statement_timeout_ms} "
            f"--connect-timeout-s {self.connect_timeout_s} "
            f"--duration-s {self.duration_s:.3f}"
        )

    def start(self) -> "RemoteRtoProbe":
        install_agent(self.node, self.package_root)
        if self.log_path is not None:
            self._log_handle = open(self.log_path, "w")
        self._proc = subprocess.Popen(
            ssh.build_command(self.node, self._remote_command()),
            stdout=subprocess.PIPE,
            # Kept separate, unlike `ssh.StreamingRemote`, which merges them.
            # The agent's stdout is a data stream; a diagnostic interleaved into
            # it would be an unparseable observation.
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True,
                                        name="rto-probe-remote-read")
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True,
                                               name="rto-probe-remote-err")
        self._stderr_reader.start()
        return self

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            if self._log_handle is not None:
                self._log_handle.write(line + "\n")
            try:
                payload = json.loads(line)
            except ValueError:
                # Not an observation -- an SSH banner, a warning from the remote
                # shell. Dropped rather than guessed at; the agent writes
                # diagnostics to stderr, which is captured separately.
                continue
            if not isinstance(payload, dict):
                continue
            marker = payload.get(AGENT_RESULT_KEY)
            if marker == "start":
                self._on_start(payload)
            elif marker == "stop":
                self._on_stop(payload)
            else:
                self._on_attempt(payload)

    def _on_start(self, payload: dict[str, Any]) -> None:
        self.agent_epoch_utc = str(payload.get("epoch_utc") or "")
        try:
            skew = (
                _parse_utc(self.agent_epoch_utc) - _parse_utc(self.epoch_utc)
            ).total_seconds()
        except ValueError:
            self.error = (
                "the agent did not report a parseable epoch, so its offsets "
                "cannot be placed on the run's clock"
            )
            return
        with self._lock:
            self.epoch_skew_s = skew

    def _on_stop(self, payload: dict[str, Any]) -> None:
        agent_error = payload.get("error")
        if agent_error:
            self.error = f"agent: {agent_error}"
        summary = payload.get("summary")
        if isinstance(summary, dict):
            self.agent_summary = summary
            # The dispatcher's own counters are only observable inside the
            # agent; they describe the instrument, not the database, so they are
            # carried across rather than recomputed.
            self.ticks = int(summary.get("ticks") or 0)
            self.dispatch_saturation = int(summary.get("dispatch_saturation") or 0)
            self.ticks_spaced_out = int(summary.get("ticks_spaced_out") or 0)

    def _on_attempt(self, row: dict[str, Any]) -> None:
        with self._lock:
            skew = self.epoch_skew_s
        if skew is None:
            # An attempt before the epoch line cannot be placed on the run's
            # clock. Dropping it is the honest outcome: a guessed origin is
            # exactly the error the two-clock reconciliation exists to prevent.
            return
        try:
            attempt = ProbeAttempt(
                seq_id=int(row["seq_id"]),
                dispatch_offset_s=float(row["dispatch_offset_s"]) + skew,
                complete_offset_s=float(row["complete_offset_s"]) + skew,
                outcome=str(row["outcome"]),
                worker=int(row["worker"]),
                detail=str(row.get("detail") or ""),
                ts_utc=str(row.get("ts_utc") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return
        with self._lock:
            self.attempts.append(attempt)

    def stop(self, timeout_s: float = 20.0) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # SIGTERM lets the agent's context manager close its pool and emit
            # its stop line; the kill is the fallback if it does not.
            proc.terminate()
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
        for thread in (self._reader, self._stderr_reader):
            if thread is not None:
                thread.join(timeout=timeout_s)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        if self.error is None and not self.attempts:
            tail = "; ".join(self._stderr[-3:]) or "no output on stderr"
            self.error = f"the probe agent produced no observations ({tail})"

    def __enter__(self) -> "RemoteRtoProbe":
        try:
            return self.start()
        except BaseException as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return self

    def __exit__(self, *exc) -> None:
        try:
            self.stop()
        except BaseException as stop_exc:  # noqa: BLE001
            self.error = self.error or f"{type(stop_exc).__name__}: {stop_exc}"

    # --- derived quantities ----------------------------------------------

    def rows(self):
        return (attempt.to_row() for attempt in self.attempts)

    def summary(self) -> dict[str, Any]:
        """Derived here from the attempts, as the local probe's is.

        Deliberately not the agent's own summary: every published figure in this
        project is re-derived from the observations rather than trusted from
        whatever computed them first. The agent's version is kept alongside
        under ``agent_summary`` so the two can be compared.
        """
        summary = summarise(
            self.attempts,
            ticks=self.ticks,
            saturated=self.dispatch_saturation,
            spaced_out=self.ticks_spaced_out,
            interval_s=self.interval_s,
            workers=self.workers,
        )
        summary["ran_on"] = self.node.host
        summary["epoch_skew_s"] = (
            round(self.epoch_skew_s, 6) if self.epoch_skew_s is not None else None
        )
        summary["agent_epoch_utc"] = self.agent_epoch_utc
        summary["agent_summary"] = self.agent_summary
        summary["note_clock"] = (
            "the probe ran on "
            f"{self.node.host}; offsets were rebased onto the run's clock by the "
            "difference between the two epochs' UTC stamps. The residual error is "
            "the NTP offset between the machines, asserted small by "
            "preflight.check_clock_offset before the run"
        )
        if self._stderr:
            summary["agent_stderr"] = self._stderr[-10:]
        return summary

    def rto(self, fault_offset_s: float) -> dict[str, Any]:
        return measure_rto(self.attempts, fault_offset_s)

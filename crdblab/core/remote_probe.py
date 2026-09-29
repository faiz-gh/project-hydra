"""Run the RTO probe on the client node and read its observations back.

The probe runs on ``crdb-client-1`` so each canary write pays only the
client-to-cluster round trip, and so a workstation network hiccup cannot be
mistaken for a database outage. The same :mod:`crdblab.core.rto_probe` code is
copied over and run with ``python3 -m``.

The agent reports offsets from its own epoch; they are rebased onto the
harness clock by the difference between the two epochs' UTC stamps. That is
valid because pre-flight asserts the client node's NTP offset first.
"""

from __future__ import annotations

import json
import subprocess
import threading
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

#: Agent install location on the client node, rewritten from this checkout every run.
AGENT_ROOT = "/tmp/crdblab-probe-agent"

#: The only files the agent needs (stdlib plus ``psycopg``).
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
    """Check the client node can run the probe (python3 with ``psycopg``).

    Checked up front: an agent that cannot import ``psycopg`` would otherwise
    look like a total outage from the first sample.
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
    """Copy this checkout's probe code onto the client node, every run, so the
    agent matches the git revision the manifest records."""
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
    """The probe running on ``node``, with the same interface as ``RtoProbe``.

    Never raises into the run it observes; fatal problems land in :attr:`error`.
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
        #: Harness clock zero and its UTC instant; agent offsets are rebased onto it.
        self.epoch_monotonic = float(epoch_monotonic)
        self.epoch_utc = epoch_utc
        self.log_path = log_path

        self.attempts: list[ProbeAttempt] = []
        self.error: str | None = None
        #: Seconds to add to an agent offset to place it on the harness clock.
        self.epoch_skew_s: float | None = None
        self.agent_epoch_utc: str | None = None
        #: The agent's own summary, kept beside the one derived here for comparison.
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

    def start(self) -> RemoteRtoProbe:
        install_agent(self.node, self.package_root)
        if self.log_path is not None:
            self._log_handle = open(self.log_path, "w")
        self._proc = subprocess.Popen(
            ssh.build_command(self.node, self._remote_command()),
            stdout=subprocess.PIPE,
            # stdout is a data stream, so stderr is kept apart from it.
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
                # Not an observation (e.g. a shell warning); diagnostics go to stderr.
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
            # Dispatcher counters are only observable inside the agent.
            self.ticks = int(summary.get("ticks") or 0)
            self.dispatch_saturation = int(summary.get("dispatch_saturation") or 0)
            self.ticks_spaced_out = int(summary.get("ticks_spaced_out") or 0)

    def _on_attempt(self, row: dict[str, Any]) -> None:
        with self._lock:
            skew = self.epoch_skew_s
        if skew is None:
            # Cannot be placed on the run clock before the agent reports its epoch.
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
            # SIGTERM lets the agent flush its stop line; kill is the fallback.
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

    def __enter__(self) -> RemoteRtoProbe:
        try:
            return self.start()
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return self

    def __exit__(self, *exc) -> None:
        try:
            self.stop()
        except BaseException as stop_exc:
            self.error = self.error or f"{type(stop_exc).__name__}: {stop_exc}"

    def rows(self):
        return (attempt.to_row() for attempt in self.attempts)

    def summary(self) -> dict[str, Any]:
        """Summary re-derived from the attempts; the agent's own is kept under
        ``agent_summary`` for comparison."""
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

    def rto(
        self, fault_offset_s: float, observation_end_s: float | None = None
    ) -> dict[str, Any]:
        return measure_rto(self.attempts, fault_offset_s, observation_end_s)

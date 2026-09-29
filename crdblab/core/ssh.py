"""Centralised SSH invocation.

Every remote command routes through here. Host-key checking is disabled because
the testbed is destroyed and rebuilt, and providers reuse addresses. Output is
streamed line by line (``bufsize=1``) so samples are parsed as they arrive.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..topology import Node

#: Options for every invocation; recorded in each run manifest.
SSH_OPTIONS: tuple[str, ...] = (
    "-q",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=3",
)

#: Prefix for privileged remote commands (several nodes log in as ``ubuntu``).
#: ``-n`` fails fast instead of hanging on a password prompt.
SUDO = "sudo -n"


def build_command(node: Node, remote: str | None = None) -> list[str]:
    cmd = ["ssh", *SSH_OPTIONS, f"{node.user}@{node.host}"]
    if remote is not None:
        cmd.append(remote)
    return cmd


@dataclass
class RemoteResult:
    returncode: int
    stdout: str
    stderr: str


def run(node: Node, remote: str, timeout: float | None = 60.0) -> RemoteResult:
    """Execute a command and wait for it. For short, non-streaming commands."""
    proc = subprocess.run(
        build_command(node, remote),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return RemoteResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass
class StreamingRemote:
    """Line-wise streaming execution of a long-running remote command.

    Lines are yielded as they arrive and, if ``tee`` is given, written to it
    first, so every run keeps the raw generator output.
    """

    node: Node
    remote: str
    tee: object | None = None
    _proc: subprocess.Popen | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> StreamingRemote:
        self._proc = subprocess.Popen(
            build_command(self.node, self.remote),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        return self

    def __iter__(self) -> Iterator[str]:
        assert self._proc is not None and self._proc.stdout is not None
        for line in iter(self._proc.stdout.readline, ""):
            if self.tee is not None:
                self.tee.write(line)
                self.tee.flush()
            yield line

    def __exit__(self, *exc) -> None:
        if self._proc is None:
            return
        if self._proc.stdout is not None:
            self._proc.stdout.close()
        self._proc.wait(timeout=30)


def force_tty(remote: str) -> str:
    """Wrap a command so the generator believes it is writing to a terminal.

    ``cockroach workload run`` prints per-interval lines only to a terminal, so
    the command is wrapped in ``script`` to allocate a pseudo-terminal.
    """
    escaped = remote.replace("'", "'\\''")
    return f"script -qefc '{escaped}' /dev/null"

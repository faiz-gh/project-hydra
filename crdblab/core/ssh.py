"""Centralised SSH invocation.

Every remote command in the project routes through this module so that the
connection policy is declared once and can be described accurately in the
methodology. The host-key options below disable verification; this is a
deliberate and disclosed accommodation for a testbed that is destroyed and
rebuilt between configurations (providers routinely reassign the same address
to a new instance, which would otherwise trigger SSH's man-in-the-middle
refusal), and is not a posture appropriate to a persistent fleet.

``bufsize=1`` with line-wise iteration is mandatory rather than incidental:
buffering the generator's output and processing it afterwards is what allowed
the terminal cumulative-summary block to be mistaken for a per-interval sample
in the legacy tooling.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from ..topology import Node

#: Options applied to every invocation. Kept as a module constant so the run
#: manifest can record the exact policy used.
SSH_OPTIONS: tuple[str, ...] = (
    "-q",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=3",
)


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

    Lines are yielded as they arrive and, if ``tee`` is supplied, written
    verbatim to that file object first. Persisting raw generator output
    alongside every derived CSV means a future dispute about parsing can be
    settled against the original bytes rather than re-run from scratch.
    """

    node: Node
    remote: str
    tee: object | None = None
    _proc: subprocess.Popen | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "StreamingRemote":
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

    ``cockroach workload run`` suppresses its per-interval progress line when
    stdout is a pipe, emitting only cumulative totals at the end. Allocating a
    pseudo-terminal via ``script`` restores the per-second stream over an SSH
    pipe. The parser tolerates either shape, but Phase II-IV require the
    per-interval samples, so this wrapper is applied to benchmark commands and
    the resulting sample count is asserted in pre-flight.
    """
    escaped = remote.replace("'", "'\\''")
    return f"script -qefc '{escaped}' /dev/null"

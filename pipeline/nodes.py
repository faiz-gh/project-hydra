"""The six testbed VMs, reached over SSH via Tailscale.

Resolved from :mod:`crdblab.topology`, the same source ``run-experiment.sh`` reads.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from crdblab.topology import CLIENT_NODE, DEFAULT_TOPOLOGY

#: Matches run-experiment.sh's SSH_OPTS: hosts are rebuilt and addresses reused,
#: and -n stops ssh from swallowing stdin.
SSH_OPTS = [
    "-q", "-n",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
]


@dataclass(frozen=True)
class Vm:
    user: str
    host: str


def vms() -> list[Vm]:
    """Every VM Terraform creates: the five cluster members and the client node."""
    return [Vm(n.user, n.host) for n in DEFAULT_TOPOLOGY.nodes] + [
        Vm(CLIENT_NODE.user, CLIENT_NODE.host)
    ]


def hostnames() -> list[str]:
    return [vm.host for vm in vms()]


def ssh(vm: Vm, command: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """Run ``command`` on ``vm``. Never raises on failure or timeout; check ``returncode``."""
    try:
        return subprocess.run(
            ["ssh", *SSH_OPTS, f"{vm.user}@{vm.host}", command],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([], 124, "", f"timed out after {timeout:.0f}s")

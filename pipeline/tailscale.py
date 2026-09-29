"""Remove the testbed's devices from the tailnet, so a redeploy gets its names back.

Terraform destroys the VMs but not their Tailscale registrations. The devices
stay in the tailnet as offline machines, and the next deployment's
``tailscale up --hostname=crdb-gcp-1`` is renamed ``crdb-gcp-1-1`` because the
name is taken. Every MagicDNS lookup in the harness then resolves the dead
machine.

The ``tailscale`` CLI can only log out the machine it runs on, so this works in
two layers:

* :func:`logout_vms` -- before destroy, each VM logs itself out. Best-effort: a
  VM that is unreachable or already gone is simply skipped.
* :func:`purge_devices` -- after destroy, the Tailscale API deletes every device
  still carrying a testbed hostname, then lists again to prove none remain.
  This is the authoritative step and it fails loudly.

Needs ``TS_API_KEY`` (admin console -> Settings -> Keys -> API access token) and
optionally ``TS_TAILNET`` (default ``-``, the key's own tailnet).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from . import nodes

API = "https://api.tailscale.com/api/v2"

Log = Callable[[str], None]


class TailscaleError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    key = os.environ.get("TS_API_KEY", "").strip()
    if not key:
        raise TailscaleError(
            "TS_API_KEY is not set. Create an API access token in the Tailscale admin "
            "console (Settings -> Keys) and add TS_API_KEY=... to .env"
        )
    return key, os.environ.get("TS_TAILNET", "").strip() or "-"


def _request(method: str, path: str) -> dict:
    key, _ = _credentials()
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace").strip()
        raise TailscaleError(f"tailscale API {method} {path}: HTTP {e.code} {detail}") from e
    except urllib.error.URLError as e:
        raise TailscaleError(f"tailscale API {method} {path}: {e.reason}") from e
    return json.loads(body) if body else {}


def list_devices() -> list[dict]:
    _, tailnet = _credentials()
    return _request("GET", f"/tailnet/{tailnet}/devices").get("devices", [])


def _self_name() -> str:
    """This workstation's MagicDNS name, so it can never be matched by accident."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                             text=True, timeout=10, check=False).stdout
        return (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def matching(devices: Iterable[dict], hosts: Iterable[str], exclude_name: str = "") -> list[dict]:
    """Devices that belong to the testbed.

    A device matches if its hostname is exactly one of ``hosts``, or its
    MagicDNS name's first label is one of ``hosts`` with an optional ``-<n>``
    suffix -- the form Tailscale gives a device whose name was already taken,
    e.g. ``crdb-gcp-1-1``. Nothing broader: ``crdb-gcp-10`` is not ``crdb-gcp-1``.
    """
    patterns = [re.compile(rf"^{re.escape(h)}(-\d+)?$") for h in hosts]
    exact = set(hosts)
    out = []
    for d in devices:
        name = (d.get("name") or "").rstrip(".")
        if exclude_name and name == exclude_name:
            continue
        label = name.split(".", 1)[0]
        if d.get("hostname") in exact or any(p.match(label) for p in patterns):
            out.append(d)
    return out


def testbed_devices() -> list[dict]:
    return matching(list_devices(), nodes.hostnames(), exclude_name=_self_name())


def describe(d: dict) -> str:
    state = "online" if d.get("connectedToControl") else "offline"
    return f"{(d.get('name') or '?').split('.', 1)[0]:<22} {d.get('addresses', ['?'])[0]:<16} {state}"


def purge_devices(log: Log = print) -> int:
    """Delete every testbed device from the tailnet and verify. Returns how many."""
    found = testbed_devices()
    if not found:
        log("no testbed devices in the tailnet")
        return 0
    for d in found:
        _request("DELETE", f"/device/{d.get('nodeId') or d['id']}")
        log(f"deleted  {describe(d)}")
    left = testbed_devices()
    if left:
        raise TailscaleError("devices still present after delete: "
                             + ", ".join(describe(d) for d in left))
    log(f"{len(found)} device(s) removed; hostnames are free for the next deploy")
    return len(found)


def logout_vms(log: Log = print) -> None:
    """Have every reachable VM log itself out of the tailnet. Never raises.

    The logout is detached and delayed by a second because the SSH session runs
    over Tailscale itself: logging out inline would cut the connection that is
    waiting for the command to return.
    """
    cmd = "sudo -n sh -c 'nohup sh -c \"sleep 1; tailscale logout\" >/dev/null 2>&1 &'"

    def one(vm: nodes.Vm) -> str:
        r = nodes.ssh(vm, cmd, timeout=25)
        return f"{vm.host}: " + ("logging out" if r.returncode == 0
                                 else f"skipped ({(r.stderr or 'unreachable').strip()[:60]})")

    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(one, nodes.vms()):
            log(line)


def main(argv: list[str] | None = None) -> int:
    """``python -m pipeline.tailscale [list|purge]`` -- for use outside the TUI."""
    import argparse

    from crdblab.config import load_env_file

    p = argparse.ArgumentParser(description="List or remove the testbed's Tailscale devices")
    p.add_argument("action", choices=["list", "purge"], nargs="?", default="list")
    args = p.parse_args(argv)
    load_env_file()
    try:
        if args.action == "purge":
            purge_devices()
        else:
            found = testbed_devices()
            for d in found:
                print(describe(d))
            print(f"{len(found)} testbed device(s) would be deleted")
    except TailscaleError as e:
        print(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

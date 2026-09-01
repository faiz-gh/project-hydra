#!/usr/bin/env python3
import argparse
import subprocess
from datetime import datetime, timezone

# Testbed Topology Map
TOPOLOGY = {
    "linode-1": {"host": "crdb-linode-1", "user": "root"},
    "linode-2": {"host": "crdb-linode-2", "user": "root"},
    "azure-1": {"host": "crdb-azure-1", "user": "ubuntu"},
    "azure-2": {"host": "crdb-azure-2", "user": "ubuntu"},
    "gcp-1": {"host": "crdb-gcp-1", "user": "ubuntu"}
}

def inject_fault(target_key, action):
    if target_key not in TOPOLOGY:
        print(f"❌ Unknown target: {target_key}")
        return None

    node = TOPOLOGY[target_key]
    # FIXED: Correctly unpacking both keys from the dictionary
    host, user = node["host"], node["user"]

    if action == "dead":
        payload = "killall -9 cockroach"
    elif action == "recover":
        payload = "nohup bash -c 'tailscale down && sleep 45 && tailscale up' >/dev/null 2>&1 &"
    else:
        return None

    cmd = ['ssh', '-q', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', f'{user}@{host}', payload]
    t_fault = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    try:
        subprocess.run(cmd, check=True, timeout=5)
        print(f"💥 CHAOS INJECTED: [{action.upper()}] executed on {host} at {t_fault}")
        return t_fault
    except subprocess.TimeoutExpired:
        print(f"💥 CHAOS INJECTED: [{action.upper()}] executed on {host} (SSH Timeout Confirmed)")
        return t_fault
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Failed to inject fault on {host}: {e}")
        return t_fault

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Distributed Chaos Injector")
    parser.add_argument("--target", required=True, choices=list(TOPOLOGY.keys()), help="Target node for failure")
    parser.add_argument("--action", required=True, choices=["dead", "recover"], help="Type of failure")
    args = parser.parse_args()
    inject_fault(args.target, args.action)

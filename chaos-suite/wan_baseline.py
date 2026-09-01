#!/usr/bin/env python3
import asyncio
import re
import statistics
import csv
from pathlib import Path

# Target Testbed Topology
NODES = [
    "crdb-linode-1", "crdb-linode-2",
    "crdb-azure-1", "crdb-azure-2",
    "crdb-gcp-1"
]

# CockroachDB HLC threshold requirement (500ms = 0.5 seconds)
MAX_CLOCK_SKEW_SEC = 0.5

def get_ssh_user(node_name):
    if "azure" in node_name or "gcp" in node_name:
        return "ubuntu"
    return "root"

# Reusable SSH base command to ignore ephemeral testbed host key warnings
def get_ssh_base_cmd(user, host):
    return [
        'ssh', '-q',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'BatchMode=yes',
        f'{user}@{host}'
    ]

async def probe_node(source):
    ssh_user = get_ssh_user(source)

    bash_payload = """
    echo "==MTU=="
    cat /sys/class/net/tailscale0/mtu 2>/dev/null || echo "UNKNOWN"

    echo "==NTP=="
    chronyc tracking 2>/dev/null || echo "NTP_CHECK_FAILED"
    """

    for dest in NODES:
        if dest != source:
            bash_payload += f"\n    echo '==PING:{dest}=='\n    ping -c 100 -i 0.1 -W 1 {dest} 2>/dev/null\n"

    try:
        cmd = get_ssh_base_cmd(ssh_user, source) + ['bash', '-s']
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(input=bash_payload.encode('utf-8'))
        return source, stdout.decode('utf-8'), stderr.decode('utf-8'), proc.returncode
    except Exception as e:
        return source, "", str(e), 1

async def check_leaseholders(source):
    ssh_user = get_ssh_user(source)

    sql_query = """
    SELECT n.locality, COUNT(r.range_id) as lease_count
    FROM crdb_internal.ranges r
    JOIN crdb_internal.gossip_nodes n ON r.lease_holder = n.node_id
    GROUP BY n.locality
    ORDER BY lease_count DESC;
    """

    bash_payload = f"cockroach sql --insecure --host={source}:26257 --format=csv -e '{sql_query.strip()}'"

    try:
        cmd = get_ssh_base_cmd(ssh_user, source) + ['bash', '-s']
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(input=bash_payload.encode('utf-8'))
        return stdout.decode('utf-8'), stderr.decode('utf-8'), proc.returncode
    except Exception as e:
        return "", str(e), 1

def parse_results(source, stdout):
    data = {'mtu': None, 'clock_offset_sec': None, 'clock_healthy': False, 'pings': {}}
    current_section = None
    current_dest = None
    ping_output = ""

    for line in stdout.split('\n'):
        line = line.strip()
        if line.startswith("=="):
            if current_section == "PING" and current_dest:
                data['pings'][current_dest] = analyze_ping(ping_output)

            section_match = re.match(r'==PING:(.*)==', line)
            if section_match:
                current_section = "PING"
                current_dest = section_match.group(1)
                ping_output = ""
            else:
                current_section = line.strip("=")
                current_dest = None
            continue

        if current_section == "MTU" and line.isdigit():
            data['mtu'] = int(line)

        elif current_section == "NTP" and "System time" in line:
            match = re.search(r'([0-9\.]+) seconds', line)
            if match:
                offset = float(match.group(1))
                data['clock_offset_sec'] = offset
                data['clock_healthy'] = offset < MAX_CLOCK_SKEW_SEC

        elif current_section == "PING" and current_dest:
            ping_output += line + "\n"

    if current_section == "PING" and current_dest:
        data['pings'][current_dest] = analyze_ping(ping_output)

    return data

def analyze_ping(output):
    times = [float(x) for x in re.findall(r'time=([0-9\.]+) ms', output)]
    loss_match = re.search(r'(\d+)% packet loss', output)
    loss = int(loss_match.group(1)) if loss_match else 100

    if not times:
        return {'min': None, 'median': None, 'mean': None, 'p95': None, 'p99': None, 'jitter': None, 'loss': loss}

    times.sort()
    count = len(times)

    if count > 1:
        diffs = [abs(times[i] - times[i-1]) for i in range(1, count)]
        jitter = sum(diffs) / len(diffs)
    else:
        jitter = 0.0

    return {
        'min': round(times[0], 3),
        'median': round(statistics.median(times), 3),
        'mean': round(statistics.mean(times), 3),
        'p95': round(times[int(count * 0.95) - 1 if count > 0 else 0], 3),
        'p99': round(times[int(count * 0.99) - 1 if count > 0 else 0], 3),
        'jitter': round(jitter, 3),
        'loss': loss
    }

async def main():
    print(f"🚀 Initiating parallel probes across {len(NODES)} nodes... (ETA: 12 seconds)")

    tasks = [probe_node(node) for node in NODES]
    results = await asyncio.gather(*tasks)

    aggregated_data = {}
    for node, stdout, stderr, rc in results:
        if rc != 0:
            user = get_ssh_user(node)
            print(f"[!] Warning: Node {node} ({user}) encountered an SSH/execution error.")
            continue
        aggregated_data[node] = parse_results(node, stdout)

    print("\n" + "="*60)
    print(" 🛠️  ENVIRONMENT SANITY CHECKS")
    print("="*60)
    for node in NODES:
        if node not in aggregated_data:
            continue
        d = aggregated_data[node]
        mtu = d.get('mtu', 'UNKNOWN')
        offset = d.get('clock_offset_sec', 'N/A')
        health_mark = "✅" if d.get('clock_healthy') else "❌"
        print(f" {node:<15} | Tailscale MTU: {mtu:<5} | NTP Skew: {offset}s {health_mark}")

    print("\n" + "="*80)
    print(" 🌐 WAN LATENCY MATRIX: MEAN RTT / P99 RTT (ms)")
    print("="*80)

    header = f"{'Source \\ Dest':<15}" + "".join([f"{n:<15}" for n in NODES])
    print(header)
    print("-" * len(header))

    for src in NODES:
        row = f"{src:<15}"
        for dst in NODES:
            if src == dst:
                row += f"{'X':<15}"
            else:
                stats = aggregated_data.get(src, {}).get('pings', {}).get(dst, {})
                mean = stats.get('mean')
                p99 = stats.get('p99')
                if mean is None:
                    row += f"{'ERR/TIMEOUT':<15}"
                else:
                    cell = f"{mean}/{p99}"
                    row += f"{cell:<15}"
        print(row)

    print("\n" + "="*80)
    print(" 📊 COCKROACHDB LEASEHOLDER DISTRIBUTION")
    print("="*80)

    primary_node = NODES[0]
    sql_out, sql_err, sql_rc = await check_leaseholders(primary_node)

    if sql_rc == 0 and sql_out.strip():
        print(f"{'Node Locality (Cloud / Region)':<45} | {'Active Leaseholders'}")
        print("-" * 65)

        lines = sql_out.strip().split('\n')
        if len(lines) > 1:
            for line in lines[1:]:
                if line:
                    # FIXED: Split on only the *last* comma using rsplit
                    locality, count = line.replace('"', '').rsplit(',', 1)

                    warning_flag = " ⚠️ (Should be 0)" if "azure" in locality.lower() else ""
                    print(f"{locality:<45} | {count}{warning_flag}")
        else:
            print("Cluster is up, but no data ranges have been initialized yet.")
    else:
        print(f"❌ Failed to query database on {primary_node}.")
        if sql_err:
            print(f"Error output: {sql_err.strip()}")

    export_dir = Path('exports')
    export_dir.mkdir(exist_ok=True)
    csv_file = export_dir / 'wan_latency_baseline.csv'

    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Destination", "Min_ms", "Median_ms", "Mean_ms", "p95_ms", "p99_ms", "Jitter_ms", "PacketLoss_pct"])
        for src, data in aggregated_data.items():
            for dst, stats in data.get('pings', {}).items():
                writer.writerow([
                    src, dst,
                    stats['min'], stats['median'], stats['mean'],
                    stats['p95'], stats['p99'], stats['jitter'], stats['loss']
                ])

    print(f"\n✅ Diagnostics complete. CSV artifact exported to: {csv_file}")

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
import subprocess
import time
import csv
import os
import threading
import urllib.request
from datetime import datetime, timezone

EXPORT_DIR = 'exports'
os.makedirs(EXPORT_DIR, exist_ok=True)
CSV_FILE = os.path.join(EXPORT_DIR, 'baseline_single_node.csv')
CONCURRENCIES = [10, 50, 100, 200]
DURATION_SEC = "60s"

# SSH user is ubuntu
ssh_base_cmd = [
    'ssh', '-q',
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    f'ubuntu@crdb-local-1'
]

print(f"🔗 Target Node: crdb-local-1")
print("🔍 Fetching Tailscale IP to bypass local DNS split-brain...")
try:
    # Dynamically grab the Tailscale IP directly from the node
    ts_ip = subprocess.check_output(ssh_base_cmd + ["tailscale", "ip", "-4"], text=True).strip()
    print(f"   -> Tailscale IP: {ts_ip}")
except subprocess.CalledProcessError:
    print(f"❌ Failed to get Tailscale IP from crdb-local-1. Is Tailscale running?")
    exit(1)

# DB user MUST be root. We use the Tailscale IP to guarantee connectivity.
DB_URL = f"postgresql://root@{ts_ip}:26257/kv?sslmode=disable"

print("🔄 Initializing KV workload schema (Remote Exec)...")
subprocess.run(ssh_base_cmd + [f"cockroach workload init kv --drop '{DB_URL}'"], check=True)

# ---------------------------------------------------------
# BACKGROUND THREAD: Real-time Prometheus Metrics Scraper
# ---------------------------------------------------------
current_metrics = {'cpu': 0.0, 'iops': 0, 'last_total': 0}
stop_threads = False

def metrics_scraper_worker():
    while not stop_threads:
        try:
            # Scrape using the Tailscale IP directly
            url = f"http://{ts_ip}:8080/_status/vars"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as response:
                data = response.read().decode('utf-8')

            reads, writes, cpu = 0, 0, 0.0
            for line in data.split('\n'):
                if line.startswith('#'): continue
                if line.startswith('sys_cpu_combined_percent_normalized'):
                    cpu = float(line.split()[-1]) * 100.0
                elif line.startswith('sys_host_disk_read_count'):
                    reads = int(float(line.split()[-1]))
                elif line.startswith('sys_host_disk_write_count'):
                    writes = int(float(line.split()[-1]))

            new_total = reads + writes
            if current_metrics['last_total'] > 0:
                current_metrics['iops'] = new_total - current_metrics['last_total']

            current_metrics['cpu'] = cpu
            current_metrics['last_total'] = new_total
        except Exception:
            pass
        time.sleep(1.0)

threading.Thread(target=metrics_scraper_worker, daemon=True).start()

# ---------------------------------------------------------
# MAIN THREAD: Workload Execution
# ---------------------------------------------------------
with open(CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['timestamp', 'concurrency', 'tps', 'p50_latency_s', 'p99_latency_s', 'error_rate_pct', 'cpu_pct', 'ram_pct', 'disk_iops'])

    for c in CONCURRENCIES:
        print(f"\n🚀 --- Starting Concurrency: {c} (Duration: {DURATION_SEC}) ---")

        remote_cmd = (
            f"cockroach workload run kv --read-percent=80 "
            f"--concurrency={c} --duration={DURATION_SEC} --display-every=1s '{DB_URL}'"
        )

        proc = subprocess.Popen(ssh_base_cmd + [remote_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        for line in iter(proc.stdout.readline, ''):
            line = line.strip()
            if not line or line.startswith('_elapsed') or line.startswith('Initializing'):
                continue

            parts = line.split()
            # Explicitly exclude the cumulative summary block
            if len(parts) == 9 and parts[0].endswith('s') and float(parts[0].replace('s', '')) <= float(DURATION_SEC.replace('s', '')):
                try:
                    errors, tps = int(parts[1]), float(parts[2])
                    p50_s, p99_s = float(parts[5]) / 1000.0, float(parts[7]) / 1000.0
                    error_rate = (errors / tps * 100) if tps > 0 else 0.0
                    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

                    cpu_pct = current_metrics['cpu']
                    disk_iops = current_metrics['iops']
                    ram_pct = 0.0

                    writer.writerow([timestamp, c, tps, p50_s, p99_s, error_rate, cpu_pct, ram_pct, disk_iops])
                    f.flush()
                    print(f"[{timestamp}] C={c:<3} | TPS: {tps:<6.0f} | p99: {p99_s:<5.3f}s | CPU: {cpu_pct:>4.1f}% | IOPS: {disk_iops}")
                except ValueError:
                    pass

        proc.wait()
        print("⏳ Cooling down for 10 seconds...")
        time.sleep(10)

stop_threads = True
print(f"\n✅ Single-Node Benchmark complete. CSV exported to: {CSV_FILE}")

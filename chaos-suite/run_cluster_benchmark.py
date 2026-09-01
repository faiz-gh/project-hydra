#!/usr/bin/env python3
import subprocess
import time
import csv
import os
import threading
import psycopg2
import re
import urllib.request
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

base_uri = os.environ.get("DB_URI")
if not base_uri:
    print("❌ Error: DB_URI environment variable is not set.")
    exit(1)

def get_ssh_user(node_name):
    """Dynamically map the SSH user based on the cloud provider in the hostname."""
    if "azure" in node_name or "gcp" in node_name:
        return "ubuntu"
    return "root"

def build_db_uri_multi(base, db_name):
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path=f"/{db_name}"))

def build_db_uri_single(base, db_name):
    parsed = urlparse(base)
    netloc = parsed.netloc
    match = re.match(r'(?:(.*?)@)?(.*?)(?::(\d+))?$', netloc)
    if match:
        userinfo, hosts, port = match.group(1), match.group(2), match.group(3)
        first_host = hosts.split(',')[0]
        new_netloc = f"{userinfo}@" if userinfo else ""
        new_netloc += first_host
        if port: new_netloc += f":{port}"
        return urlunparse(parsed._replace(netloc=new_netloc, path=f"/{db_name}"))
    return base

DB_URL_KV_SINGLE = build_db_uri_single(base_uri, "kv")
DB_URL_BENCH_MULTI = build_db_uri_multi(base_uri, "bench")

gateway_host = urlparse(DB_URL_KV_SINGLE).hostname
ssh_user = get_ssh_user(gateway_host)

EXPORT_DIR = 'exports'
os.makedirs(EXPORT_DIR, exist_ok=True)
CSV_FILE = os.path.join(EXPORT_DIR, 'cluster_cross_cloud_benchmark.csv')
CONCURRENCIES = [10, 50, 100, 200]
DURATION_SEC = "60s"

print(f"🔗 Target Gateway Node: {gateway_host} (via SSH as {ssh_user})")

# Define the base SSH command to run commands remotely
ssh_base_cmd = [
    'ssh', '-q',
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    f'{ssh_user}@{gateway_host}'
]

print("🔄 Initializing distributed KV workload schema (Remote Exec)...")
# Execute the init command remotely. Notice we wrap the DB_URL in single quotes to prevent remote shell globbing.
init_remote_cmd = f"cockroach workload init kv --drop '{DB_URL_KV_SINGLE}'"
subprocess.run(ssh_base_cmd + [init_remote_cmd], check=True)

# =====================================================================
# NEW: Force explicit leaseholder pinning on the newly created KV database
# =====================================================================
print("🔄 Forcing Raft Leaseholders to the US Triangle...")
zone_sql = """
ALTER DATABASE kv CONFIGURE ZONE USING
    num_replicas = 5,
    constraints = COPY FROM PARENT,
    lease_preferences = '[[+region=us-east], [+region=us-east1], [+region=us-west]]';
"""
conn = psycopg2.connect(DB_URL_BENCH_MULTI)
conn.autocommit = True
conn.cursor().execute(zone_sql)
conn.close()

print("⏳ Waiting 15 seconds for CockroachDB to physically migrate Raft leases to the US...")
time.sleep(15)

audit_setup_sql = """
DROP TABLE IF EXISTS rpo_audit;
CREATE TABLE rpo_audit (seq_id INT8 PRIMARY KEY, ts TIMESTAMP DEFAULT now());
"""
print("🔄 Setting up audit table for Chaos Phase...")
conn = psycopg2.connect(DB_URL_BENCH_MULTI)
conn.autocommit = True
conn.cursor().execute(audit_setup_sql)
conn.close()

# ---------------------------------------------------------
# BACKGROUND THREAD 1: RPO Audit Writer (Runs Locally)
# ---------------------------------------------------------
# This thread runs from your laptop. Due to India->US latency, it will naturally
# max out at ~3-4 TPS. This is perfectly fine to generate a sequence for RPO checks.
stop_threads = False
def audit_worker():
    audit_conn = psycopg2.connect(DB_URL_BENCH_MULTI)
    audit_conn.autocommit = True
    cur = audit_conn.cursor()
    seq = 1
    while not stop_threads:
        try:
            cur.execute("INSERT INTO rpo_audit (seq_id) VALUES (%s)", (seq,))
            seq += 1
            time.sleep(0.02)
        except Exception:
            time.sleep(1)
    audit_conn.close()

audit_thread = threading.Thread(target=audit_worker, daemon=True)
audit_thread.start()

# ---------------------------------------------------------
# BACKGROUND THREAD 2: Real-time Metrics Scraper
# ---------------------------------------------------------
current_metrics = {'cpu': 0.0, 'iops': 0, 'last_total': 0}
def metrics_scraper_worker():
    while not stop_threads:
        try:
            url = f"http://{gateway_host}:8080/_status/vars"
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

scraper_thread = threading.Thread(target=metrics_scraper_worker, daemon=True)
scraper_thread.start()

# ---------------------------------------------------------
# MAIN THREAD: Remote Workload Orchestrator
# ---------------------------------------------------------
with open(CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['timestamp', 'concurrency', 'tps', 'p50_latency_s', 'p99_latency_s', 'error_rate_pct', 'cpu_pct', 'ram_pct', 'disk_iops'])

    for c in CONCURRENCIES:
        print(f"\n🚀 --- Starting Cluster Concurrency: {c} (Duration: {DURATION_SEC}) ---")

        # Build the exact command to execute remotely via SSH
        remote_workload_cmd = (
            f"cockroach workload run kv "
            f"--read-percent=80 "
            f"--concurrency={c} "
            f"--duration={DURATION_SEC} "
            f"--display-every=1s "
            f"'{DB_URL_KV_SINGLE}'"
        )

        cmd = ssh_base_cmd + [remote_workload_cmd]

        # Popen now executes SSH locally, which streams the remote workload's stdout directly over the ocean to Python
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        for line in iter(proc.stdout.readline, ''):
            line = line.strip()
            if not line or line.startswith('_elapsed') or line.startswith('Initializing'):
                continue

            parts = line.split()
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
                    print(f"[{timestamp}] C={c:<3} | TPS: {tps:<6.0f} | p50: {p50_s:<5.3f}s | p99: {p99_s:<5.3f}s | CPU: {cpu_pct:>4.1f}% | IOPS: {disk_iops}")
                except ValueError:
                    pass

        proc.wait()
        print("⏳ Stabilizing consensus ring for 10 seconds...")
        time.sleep(10)

stop_threads = True
print(f"\n✅ Distributed Benchmark complete. CSV exported to: {CSV_FILE}")

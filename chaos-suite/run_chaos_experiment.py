#!/usr/bin/env python3
import argparse
import subprocess
import time
import csv
import os
import threading
import psycopg2
import re
import json
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone
from dotenv import load_dotenv

import chaos_injector

# Parse command line arguments
parser = argparse.ArgumentParser(description="CockroachDB Chaos Orchestrator")
parser.add_argument("--mode", required=True, choices=["dead", "recover"], help="The chaos scenario to execute")
args = parser.parse_args()

load_dotenv()
base_uri = os.environ.get("DB_URI")

def build_db_uri_single(base, db_name):
    parsed = urlparse(base)
    netloc = parsed.netloc
    match = re.match(r'(?:(.*?)@)?(.*?)(?::(\d+))?$', netloc)
    userinfo, hosts, port = match.group(1), match.group(2), match.group(3)
    new_netloc = f"{userinfo}@" if userinfo else ""
    new_netloc += hosts.split(',')[0]
    if port: new_netloc += f":{port}"
    return urlunparse(parsed._replace(netloc=new_netloc, path=f"/{db_name}"))

def build_db_uri_multi(base, db_name):
    return urlunparse(urlparse(base)._replace(path=f"/{db_name}"))

DB_URL_KV_SINGLE = build_db_uri_single(base_uri, "kv")
DB_URL_BENCH_MULTI = build_db_uri_multi(base_uri, "bench")

EXPORT_DIR = 'exports'
CSV_FILE = os.path.join(EXPORT_DIR, f'chaos_{args.mode}_timeseries.csv')
JSON_FILE = os.path.join(EXPORT_DIR, f'chaos_{args.mode}_events.json')

# Adjusted Parameters
DURATION_SEC = 180       # 3 Full Minutes
CHAOS_TRIGGER_SEC = 60   # Inject at 1 minute
CHAOS_TARGET = "linode-2"
CHAOS_ACTION = args.mode

timeline = {
    "T_start": None,
    "T_fault_injected": None,
    "T_first_error": None,
    "T_traffic_stabilized": None,
    "baseline_tps_avg": 0
}

print(f"🔄 Resetting database for Chaos Experiment (Mode: {CHAOS_ACTION.upper()})...")
subprocess.run([
    'ssh', '-q', '-o', 'StrictHostKeyChecking=no', 'root@crdb-linode-1',
    f"cockroach workload init kv --drop '{DB_URL_KV_SINGLE}'"
], check=True)

print("🔄 Setting up audit table for Chaos Phase...")
conn = psycopg2.connect(DB_URL_BENCH_MULTI)
conn.autocommit = True
conn.cursor().execute("DROP TABLE IF EXISTS rpo_audit; CREATE TABLE rpo_audit (seq_id INT8 PRIMARY KEY, ts TIMESTAMP DEFAULT now());")
conn.close()

# 1. RPO Audit Thread
stop_threads = False
def audit_worker():
    conn = psycopg2.connect(DB_URL_BENCH_MULTI)
    conn.autocommit = True
    cur = conn.cursor()
    seq = 1
    while not stop_threads:
        try:
            cur.execute("INSERT INTO rpo_audit (seq_id) VALUES (%s)", (seq,))
            seq += 1
            time.sleep(0.02)
        except Exception:
            time.sleep(0.5)
    conn.close()

threading.Thread(target=audit_worker, daemon=True).start()

# 2. Main Chaos Loop
tps_history = []
elapsed = 0

with open(CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['timestamp', 'tps', 'p50_latency_s', 'p99_latency_s', 'error_rate_pct'])

    print(f"\n🚀 --- Starting Chaos Benchmark (Duration: {DURATION_SEC}s) ---")
    timeline["T_start"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    cmd = [
        'ssh', '-q', '-o', 'StrictHostKeyChecking=no', 'root@crdb-linode-1',
        f"cockroach workload run kv --read-percent=80 --concurrency=100 --duration={DURATION_SEC}s --display-every=1s '{DB_URL_KV_SINGLE}'"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    for line in iter(proc.stdout.readline, ''):
        line = line.strip()
        if not line or line.startswith('_elapsed') or line.startswith('Initializing'): continue

        parts = line.split()
        if len(parts) == 9 and parts[0].endswith('s') and float(parts[0].replace('s', '')) <= DURATION_SEC:
            elapsed += 1
            errors, tps = int(parts[1]), float(parts[2])
            p50_s, p99_s = float(parts[5]) / 1000.0, float(parts[7]) / 1000.0
            error_rate = (errors / tps * 100) if tps > 0 else 0.0
            ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            writer.writerow([ts, tps, p50_s, p99_s, error_rate])
            f.flush()

            if elapsed < CHAOS_TRIGGER_SEC:
                tps_history.append(tps)
                print(f"[{ts}] STEADY STATE | TPS: {tps:<6.0f} | p99: {p99_s:<5.3f}s | Errors: {errors}")

            elif elapsed == CHAOS_TRIGGER_SEC:
                timeline["baseline_tps_avg"] = sum(tps_history[-20:]) / 20 if tps_history else tps
                print(f"\n{'='*60}\n⚠️ INITIATING CHAOS ({CHAOS_ACTION.upper()}): Baseline TPS at {timeline['baseline_tps_avg']:.0f}\n{'='*60}")

                def trigger():
                    try:
                        timeline["T_fault_injected"] = chaos_injector.inject_fault(CHAOS_TARGET, CHAOS_ACTION)
                    except Exception as e:
                        print(f"⚠️ Error in chaos injector thread: {e}")
                        timeline["T_fault_injected"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

                threading.Thread(target=trigger).start()
                print(f"[{ts}] CHAOS TRIGGERED | Waiting for cluster reaction...")

            elif elapsed > CHAOS_TRIGGER_SEC:
                status = "DEGRADED"

                if errors > 0 and not timeline["T_first_error"]:
                    timeline["T_first_error"] = ts
                    status = "CRASHING"

                if timeline["T_fault_injected"] and not timeline["T_traffic_stabilized"]:
                    if elapsed > (CHAOS_TRIGGER_SEC + 10) and tps >= (timeline["baseline_tps_avg"] * 0.85):
                        timeline["T_traffic_stabilized"] = ts
                        status = "RECOVERED ✅"

                if timeline["T_traffic_stabilized"]:
                    status = "STABLE"

                print(f"[{ts}] {status:<12} | TPS: {tps:<6.0f} | p99: {p99_s:<5.3f}s | Errors: {errors}")

    proc.wait()

stop_threads = True

with open(JSON_FILE, 'w') as json_file:
    json.dump(timeline, json_file, indent=4)

print(f"\n✅ Chaos Experiment Concluded.")
print(f"📊 Timeseries data: {CSV_FILE}")
print(f"⏱️  Event Timeline: {JSON_FILE}")

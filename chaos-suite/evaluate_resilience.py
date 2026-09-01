#!/usr/bin/env python3
import argparse
import pandas as pd
import json
import psycopg2
import matplotlib.pyplot as plt
import seaborn as sns
import os
import re
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv

# Parse mode
parser = argparse.ArgumentParser(description="Resilience Data Evaluator")
parser.add_argument("--mode", required=True, choices=["dead", "recover"], help="The chaos scenario to evaluate")
args = parser.parse_args()

load_dotenv()
base_uri = os.environ.get("DB_URI")

def build_db_uri_multi(base, db_name):
    return urlunparse(urlparse(base)._replace(path=f"/{db_name}"))

DB_URL_BENCH_MULTI = build_db_uri_multi(base_uri, "bench")

EXPORT_DIR = 'exports/graphs'
CSV_FILE = os.path.join(EXPORT_DIR, f'chaos_{args.mode}_timeseries.csv')
JSON_FILE = os.path.join(EXPORT_DIR, f'chaos_{args.mode}_events.json')
PLOT_FILE = os.path.join(EXPORT_DIR, f'resilience_recovery_{args.mode}_plot.png')

if not (os.path.exists(CSV_FILE) and os.path.exists(JSON_FILE)):
    print(f"❌ Error: Required artifact files for mode '{args.mode}' not found in {EXPORT_DIR}/")
    exit(1)

# ==============================================================================
# 1. LOAD & CLEAN ARTIFACTS
# ==============================================================================
df = pd.read_csv(CSV_FILE)
df['timestamp'] = pd.to_datetime(df['timestamp'])

# DATA CLEANING: Drop the final cumulative summary leak (Anything > 20,000 TPS is impossible here)
df = df[df['tps'] < 20000].copy()

# SIGNAL SMOOTHING: Apply a 5-second rolling average to smooth network jitter
df['tps_smoothed'] = df['tps'].rolling(window=5, min_periods=1).mean()

with open(JSON_FILE, 'r') as f:
    events = json.load(f)

T_fault_injected = pd.to_datetime(events['T_fault_injected'])

# ==============================================================================
# 2. RTO (RECOVERY TIME OBJECTIVE) CALCULATION
# ==============================================================================
# Baseline: Median of the smoothed TPS 30 seconds prior to fault
baseline_mask = (df['timestamp'] >= (T_fault_injected - pd.Timedelta(seconds=30))) & (df['timestamp'] < T_fault_injected)
TPS_baseline = df.loc[baseline_mask, 'tps_smoothed'].median()
TPS_recovery_threshold = TPS_baseline * 0.80

df_post = df[df['timestamp'] > T_fault_injected].copy()

# Condition: Smoothed TPS >= 80% baseline AND Error Rate < 1%
df_post['is_recovered'] = (df_post['tps_smoothed'] >= TPS_recovery_threshold) & (df_post['error_rate_pct'] < 1.0)

# Rolling window to find 10 consecutive seconds of stability
rolling_recovery = df_post['is_recovered'].rolling(window=10).sum()

T_recovered = None
RTO_seconds = None

valid_recovery_indices = rolling_recovery[rolling_recovery == 10].index
if not valid_recovery_indices.empty:
    idx_recovered_start = valid_recovery_indices[0] - 9
    T_recovered = df.loc[idx_recovered_start, 'timestamp']
    RTO_seconds = (T_recovered - T_fault_injected).total_seconds()

p99_spike = df_post['p99_latency_s'].max()

# ==============================================================================
# 3. RPO (RECOVERY POINT OBJECTIVE) VALIDATION
# ==============================================================================
print("🔄 Connecting to cluster to validate sequence continuity (RPO)...")
rpo_status = "UNKNOWN"
try:
    rpo_query = """
    WITH sequence_check AS (
        SELECT seq_id, lead(seq_id) OVER (ORDER BY seq_id) as next_seq
        FROM rpo_audit
    )
    SELECT COUNT(*) FROM sequence_check WHERE next_seq != seq_id + 1;
    """
    conn = psycopg2.connect(DB_URL_BENCH_MULTI)
    cur = conn.cursor()
    cur.execute(rpo_query)
    gap_count = cur.fetchone()[0]
    conn.close()

    if gap_count == 0:
        rpo_status = "0 (ZERO DATA LOSS) ✅"
    else:
        rpo_status = f"FAILED ❌ ({gap_count} gaps detected)"
except Exception as e:
    rpo_status = f"ERROR / NOT FOUND (Table likely dropped) - {e}"

# ==============================================================================
# 4. DATA VISUALIZATION (3-PANEL MATPLOTLIB)
# ==============================================================================
sns.set_theme(style="whitegrid")
fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
fig.suptitle(f'CockroachDB Chaos Resilience Report | Mode: {args.mode.upper()}', fontsize=16, fontweight='bold')

df['relative_sec'] = (df['timestamp'] - T_fault_injected).dt.total_seconds()
rel_T_fault = 0.0
rel_T_recov = RTO_seconds if RTO_seconds else None

# PANEL 1: Throughput (TPS)
ax = axes[0]
sns.lineplot(data=df, x='relative_sec', y='tps_smoothed', ax=ax, color='#1f77b4', linewidth=2, label='Smoothed TPS')
sns.scatterplot(data=df, x='relative_sec', y='tps', ax=ax, color='#1f77b4', alpha=0.3, s=15, label='Raw TPS')
ax.axhline(TPS_baseline, ls='--', color='gray', label=f'Baseline ({TPS_baseline:.0f} TPS)')
ax.axhline(TPS_recovery_threshold, ls='--', color='orange', label=f'Recovery Threshold (80%)')
ax.axvline(rel_T_fault, color='red', linewidth=2, label='Fault Injected')
if rel_T_recov:
    ax.axvspan(rel_T_fault, rel_T_recov, color='red', alpha=0.1, label=f'RTO Window ({RTO_seconds:.1f}s)')
ax.set_ylabel('Throughput (TPS)')
ax.set_ylim(0, df['tps_smoothed'].max() * 1.3) # Dynamic visual padding
ax.legend(loc='upper right')

# PANEL 2: Latency (Linear Scale for Better Readability)
ax = axes[1]
sns.lineplot(data=df, x='relative_sec', y='p99_latency_s', ax=ax, color='#d62728', label='p99 Latency', linewidth=1.5)
sns.lineplot(data=df, x='relative_sec', y='p50_latency_s', ax=ax, color='#ff7f0e', label='p50 Latency', linewidth=1)
ax.axvline(rel_T_fault, color='red', linewidth=2)
ax.set_ylabel('Latency (Seconds)')
ax.set_ylim(0, max(df['p99_latency_s'].max() * 1.1, 1.0)) # Cap dynamically, minimum 1.0s
ax.legend(loc='upper right')

# PANEL 3: Error Rate
ax = axes[2]
sns.lineplot(data=df, x='relative_sec', y='error_rate_pct', ax=ax, color='#9467bd', linewidth=2)
ax.axvline(rel_T_fault, color='red', linewidth=2)
ax.set_ylabel('Error Rate (%)')
ax.set_xlabel(f'Time relative to fault injection (Seconds)')
ax.set_ylim(-0.5, max(df['error_rate_pct'].max() * 1.2, 5))

plt.tight_layout()
plt.savefig(PLOT_FILE, dpi=300, bbox_inches='tight')

# ==============================================================================
# 5. MARKDOWN SCORECARD OUTPUT
# ==============================================================================
tps_post = df_post.loc[df_post['timestamp'] > (T_recovered if T_recovered else df_post['timestamp'].max()), 'tps_smoothed'].median() if T_recovered else 0.0

print("\n" + "="*70)
print(f" 📊 SITE RELIABILITY SCORECARD | SCENARIO: {args.mode.upper()}")
print("="*70)
print(f"🔹 Baseline Throughput (TPS):    {TPS_baseline:.0f}")
print(f"🔹 Recovery Threshold (TPS):     {TPS_recovery_threshold:.0f} (80%)")
print(f"🔹 Post-Failure Stable TPS:      {tps_post:.0f}")
print(f"🔹 Peak p99 Latency Spike:       {p99_spike:.2f} seconds")
print("-"*70)
if RTO_seconds:
    print(f"⏱️  Recovery Time Objective (RTO): {RTO_seconds:.1f} Seconds ✅")
else:
    print(f"⏱️  Recovery Time Objective (RTO): FAILED TO RECOVER ❌")
print(f"🛡️  Recovery Point Objective (RPO): {rpo_status}")
print("="*70)
print(f"📈 Resilience plot saved to: {PLOT_FILE}")

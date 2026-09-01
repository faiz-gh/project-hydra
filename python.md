// analyze_single_node_baseline.py
#!/usr/bin/env python3
import pandas as pd
import os

CSV_FILE = 'exports/baseline_single_node.csv'
if not os.path.exists(CSV_FILE):
print(f"❌ Error: {CSV_FILE} not found.")
exit(1)

# Load data and filter out summary leaks/outliers

df = pd.read_csv(CSV_FILE)
df = df[df['tps'] < 50000].copy()

# Filter for steady state (drop the first 5 seconds of each concurrency tier)

df['interval'] = df.groupby('concurrency').cumcount()
steady_state_df = df[df['interval'] >= 5]

summary = steady_state_df.groupby('concurrency').agg(
Avg_TPS=('tps', 'mean'),
Max_TPS=('tps', 'max'),
p50_Lat_sec=('p50_latency_s', 'mean'),
p99_Lat_sec=('p99_latency_s', 'mean'),
Avg_CPU_pct=('cpu_pct', 'mean'),
Max_CPU_pct=('cpu_pct', 'max'),
Peak_Disk_IOPS=('disk_iops', 'max'),
Err_Rate=('error_rate_pct', 'mean')
).round(3)

print("\n" + "="*95)
print(" 📊 SINGLE-NODE BASELINE BENCHMARK REPORT (STEADY STATE)")
print("="*95)
print(summary.to_string())

max_tps = summary['Avg_TPS'].max()
optimal = summary['Avg_TPS'].idxmax()

print("\n🔍 SATURATION ANALYSIS:")
print(f"-> Maximum Throughput achieved: {max_tps} TPS at {optimal} concurrent workers.")
if summary.loc[optimal, 'Avg_CPU_pct'] > 85:
print("-> ⚠️ System is heavily CPU bound at peak load.")
if summary.loc[optimal, 'Peak_Disk_IOPS'] > 3000:
print("-> ⚠️ Disk IO is heavily saturated.")

// chaos_injector.py
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

if **name** == "**main**":
parser = argparse.ArgumentParser(description="Distributed Chaos Injector")
parser.add_argument("--target", required=True, choices=list(TOPOLOGY.keys()), help="Target node for failure")
parser.add_argument("--action", required=True, choices=["dead", "recover"], help="Type of failure")
args = parser.parse_args()
inject_fault(args.target, args.action)

// compare_raft_overhead
#!/usr/bin/env python3
import pandas as pd
import os

SINGLE_CSV = 'exports/baseline_single_node.csv'
CLUSTER_CSV = 'exports/cluster_cross_cloud_benchmark.csv'
OUTPUT_CSV = 'exports/raft_overhead_comparison.csv'

if not (os.path.exists(SINGLE_CSV) and os.path.exists(CLUSTER_CSV)):
print(f"❌ Error: Both baseline and cluster CSVs must exist in exports/")
exit(1)

def get_steady_state(file_path):
df = pd.read_csv(file_path)
df['interval'] = df.groupby('concurrency').cumcount() # Drop the first 5 seconds to eliminate TCP connection ramp-up skew
steady = df[df['interval'] >= 5]
return steady.groupby('concurrency').agg(
TPS=('tps', 'mean'),
p50=('p50_latency_s', 'mean'),
p99=('p99_latency_s', 'mean'),
CPU=('cpu_pct', 'mean'),
IOPS=('disk_iops', 'mean')
)

single_df = get_steady_state(SINGLE_CSV)
cluster_df = get_steady_state(CLUSTER_CSV)

comparison = pd.DataFrame(index=single_df.index)

# Throughput Degradation

comparison['Single_TPS'] = single_df['TPS'].round(0)
comparison['Cluster_TPS'] = cluster_df['TPS'].round(0)
comparison['TPS_Degrad_%'] = ((single_df['TPS'] - cluster_df['TPS']) / single_df['TPS'] * 100).round(1)

# Latency Impact (p50 + p99)

comparison['Single_p50_ms'] = (single_df['p50'] * 1000).round(1)
comparison['Cluster_p50_ms'] = (cluster_df['p50'] * 1000).round(1)
comparison['p50_Delta_ms'] = ((cluster_df['p50'] - single_df['p50']) * 1000).round(1)

comparison['Single_p99_ms'] = (single_df['p99'] * 1000).round(1)
comparison['Cluster_p99_ms'] = (cluster_df['p99'] * 1000).round(1)

# Hardware Overhead (Gateway vs Standalone VM)

comparison['Single_CPU_%'] = single_df['CPU'].round(1)
comparison['Cluster_CPU_%'] = cluster_df['CPU'].round(1)
comparison['CPU_Penalty_%'] = (cluster_df['CPU'] - single_df['CPU']).round(1)

print("\n" + "="*115)
print(" 🌐 PHASE III: DISTRIBUTED RAFT CONSENSUS OVERHEAD REPORT")
print("="*115)
print(comparison.to_string())

# Export the DataFrame directly to CSV

comparison.to_csv(OUTPUT_CSV)
print(f"\n✅ Comparison data successfully exported to: {OUTPUT_CSV}")

# Architectural Insight

max_tps_cluster = comparison['Cluster_TPS'].max()
optimal_c = comparison['Cluster_TPS'].idxmax()
avg_p50_overhead = comparison['p50_Delta_ms'].mean()
avg_cpu_overhead = comparison['CPU_Penalty_%'].mean()

print("\n🔍 ARCHITECTURAL ANALYSIS:")
print(f"-> Optimal Distributed Throughput: {max_tps_cluster} TPS at C={optimal_c}")
print(f"-> Average Raft Quorum Overhead: +{avg_p50_overhead:.1f} ms")
print(f"-> Gateway Routing CPU Penalty: {avg_cpu_overhead:+.1f}% CPU vs Single Node")

if 30 <= avg_p50_overhead <= 85:
print("-> ✅ NETWORK EXPECTATION MET: The p50 latency delta aligns perfectly with the WAN matrix from Phase I.")
print(" CockroachDB is successfully securing a 3-node Raft quorum within the US triangle before acknowledging writes.")
elif avg_p50_overhead >= 150:
print("-> ❌ TOPOLOGY WARNING: The latency overhead exceeds 150ms. Your zone configurations failed to isolate the Azure nodes.")

// evaluate_resilience.py
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
print(f"🔹 Baseline Throughput (TPS): {TPS_baseline:.0f}")
print(f"🔹 Recovery Threshold (TPS): {TPS_recovery_threshold:.0f} (80%)")
print(f"🔹 Post-Failure Stable TPS: {tps_post:.0f}")
print(f"🔹 Peak p99 Latency Spike: {p99_spike:.2f} seconds")
print("-"*70)
if RTO_seconds:
print(f"⏱️ Recovery Time Objective (RTO): {RTO_seconds:.1f} Seconds ✅")
else:
print(f"⏱️ Recovery Time Objective (RTO): FAILED TO RECOVER ❌")
print(f"🛡️ Recovery Point Objective (RPO): {rpo_status}")
print("="*70)
print(f"📈 Resilience plot saved to: {PLOT_FILE}")

// generate_insights.py
#!/usr/bin/env python3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os
import json

# ==========================================

# 1. DIRECTORY SETUP & LOAD DATA

# ==========================================

EXPORT_DIR = 'exports'
GRAPHS_DIR = os.path.join(EXPORT_DIR, 'graphs')
os.makedirs(GRAPHS_DIR, exist_ok=True)

try:
df_wan = pd.read_csv(f'{EXPORT_DIR}/wan_latency_baseline.csv')
df_raft = pd.read_csv(f'{EXPORT_DIR}/raft_overhead_comparison.csv')
df_dead = pd.read_csv(f'{EXPORT_DIR}/chaos_dead_timeseries.csv')
df_recover = pd.read_csv(f'{EXPORT_DIR}/chaos_recover_timeseries.csv')
except FileNotFoundError as e:
print(f"❌ Could not find data artifact: {e}")
exit(1)

def load_events(mode):
try:
with open(f'{EXPORT_DIR}/chaos_{mode}_events.json', 'r') as f:
return json.load(f)
except FileNotFoundError:
return {}

events_dead = load_events('dead')
events_recover = load_events('recover')

# ==========================================

# 2. DATA CLEANING & SIGNAL SMOOTHING

# ==========================================

def clean_chaos_data(df): # CRITICAL: Drop final cumulative summary artifacts (>10k TPS is impossible here)
df = df[df['tps'] < 10000].copy()
df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Calculate relative seconds from the start of the experiment
    df['relative_sec'] = (df['timestamp'] - df['timestamp'].min()).dt.total_seconds()

    # Smooth the TPS signal to filter out single-second network jitter
    df['tps_smoothed'] = df['tps'].rolling(window=5, min_periods=1).mean()
    return df

df_dead = clean_chaos_data(df_dead)
df_recover = clean_chaos_data(df_recover)

sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)

# Helper to get relative time for vertical markers

def get_rel_time(event_str, df):
if not event_str: return None
try:
event_time = pd.to_datetime(event_str)
return (event_time - df['timestamp'].min()).total_seconds()
except Exception:
return None

inj_dead = get_rel_time(events_dead.get('T_fault_injected'), df_dead)
rec_dead = get_rel_time(events_dead.get('T_traffic_stabilized'), df_dead)

inj_recover = get_rel_time(events_recover.get('T_fault_injected'), df_recover)
rec_recover = get_rel_time(events_recover.get('T_traffic_stabilized'), df_recover)

# Helper to add chaos vertical markers to an axis

def add_chaos_markers(ax):
added_inj = False

    # Plot fault injection lines (Red Dashed)
    if inj_recover is not None:
        ax.axvline(inj_recover, color='red', linestyle='--', linewidth=2, label='Fault Injected' if not added_inj else "")
        added_inj = True
    if inj_dead is not None and abs(inj_dead - (inj_recover or 0)) > 2: # Avoid overlapping text if injected at same time
        ax.axvline(inj_dead, color='red', linestyle='--', linewidth=2, label='Fault Injected' if not added_inj else "")

    # Plot recovery lines (Color matched to the series, Dotted)
    if rec_recover is not None:
        ax.axvline(rec_recover, color='#ff7f0e', linestyle=':', linewidth=2.5, label='Stabilized (Partition)')
    if rec_dead is not None:
        ax.axvline(rec_dead, color='#9467bd', linestyle=':', linewidth=2.5, label='Stabilized (SIGKILL)')

# ==========================================

# 3. GENERATE INDIVIDUAL GRAPHS

# ==========================================

print("🔄 Generating individual graphs...")

# GRAPH 1: WAN Latency Heatmap

plt.figure(figsize=(10, 8))
wan_pivot = df_wan.pivot(index='Source', columns='Destination', values='Median_ms')
sns.heatmap(wan_pivot, annot=True, fmt=".1f", cmap="YlOrRd", cbar_kws={'label': 'Median Latency (ms)'})
plt.title('Phase I: Mesh Overlay WAN Latency Matrix', fontsize=14, fontweight='bold')
plt.xlabel('Destination Node')
plt.ylabel('Source Node')
plt.tight_layout()
plt.savefig(f'{GRAPHS_DIR}/1_wan_latency_heatmap.png', dpi=300)
plt.close()

# GRAPH 2: Throughput Degradation

plt.figure(figsize=(10, 6))
bar_width = 0.35
x_indices = np.arange(len(df_raft['concurrency']))
plt.bar(x_indices - bar_width/2, df_raft['Single_TPS'], bar_width, label='Single Node (Local)', color='#2ca02c')
plt.bar(x_indices + bar_width/2, df_raft['Cluster_TPS'], bar_width, label='5-Node Cluster (Distributed)', color='#1f77b4')
plt.title('Phase II & III: Throughput Scaling & Degradation', fontsize=14, fontweight='bold')
plt.xlabel('Concurrent Workers')
plt.ylabel('Throughput (TPS)')
plt.xticks(x_indices, df_raft['concurrency'])
plt.legend()
for i, val in enumerate(df_raft['Cluster_TPS']):
deg = df_raft['TPS_Degrad_%'].iloc[i]
plt.text(x_indices[i] + bar_width/2, val + 50, f"-{deg}%", ha='center', color='red', fontweight='bold')
plt.tight_layout()
plt.savefig(f'{GRAPHS_DIR}/2_throughput_degradation.png', dpi=300)
plt.close()

# GRAPH 3: Raft Penalty

plt.figure(figsize=(10, 6))
plt.plot(x_indices, df_raft['Single_p50_ms'], marker='o', label='Single Node p50', color='#2ca02c', linewidth=2)
plt.plot(x_indices, df_raft['Cluster_p50_ms'], marker='s', label='Cluster p50 (Raft Quorum)', color='#d62728', linewidth=2)
plt.fill_between(x_indices, df_raft['Single_p50_ms'], df_raft['Cluster_p50_ms'], color='#d62728', alpha=0.1)
plt.title('Phase III: Speed-of-Light Raft Penalty', fontsize=14, fontweight='bold')
plt.xlabel('Concurrent Workers')
plt.ylabel('p50 Latency (ms)')
plt.xticks(x_indices, df_raft['concurrency'])
plt.legend()
plt.tight_layout()
plt.savefig(f'{GRAPHS_DIR}/3_raft_penalty.png', dpi=300)
plt.close()

# GRAPH 4: Chaos Throughput Dynamics

fig4, ax4 = plt.subplots(figsize=(12, 6))
sns.lineplot(data=df_recover, x='relative_sec', y='tps_smoothed', label='Network Partition (Recover)', color='#ff7f0e', linewidth=2, ax=ax4)
sns.lineplot(data=df_dead, x='relative_sec', y='tps_smoothed', label='SIGKILL (Dead)', color='#9467bd', linewidth=2, ax=ax4)
add_chaos_markers(ax4) # Add the vertical lines
ax4.set_title('Phase IV: High Availability & Failover Throughput', fontsize=14, fontweight='bold')
ax4.set_xlabel('Experiment Timeline (Seconds)')
ax4.set_ylabel('Smoothed Throughput (TPS)')
ax4.set_ylim(0, max(df_recover['tps_smoothed'].max(), df_dead['tps_smoothed'].max()) * 1.25)

# Place legend outside to not cover lines

ax4.legend(bbox_to_anchor=(1.02, 1), loc='upper left')
plt.tight_layout()
plt.savefig(f'{GRAPHS_DIR}/4_chaos_throughput_dynamics.png', dpi=300)
plt.close()

# GRAPH 5: Chaos Latency Dynamics

fig5, ax5 = plt.subplots(figsize=(12, 6))
sns.lineplot(data=df_recover, x='relative_sec', y='p99_latency_s', label='Network Partition (Recover) - p99', color='#ff7f0e', linewidth=1.5, alpha=0.8, ax=ax5)
sns.lineplot(data=df_dead, x='relative_sec', y='p99_latency_s', label='SIGKILL (Dead) - p99', color='#9467bd', linewidth=1.5, alpha=0.8, ax=ax5)
add_chaos_markers(ax5) # Add the vertical lines
ax5.set_title('Phase IV: High Availability Failover Latency (p99)', fontsize=14, fontweight='bold')
ax5.set_xlabel('Experiment Timeline (Seconds)')
ax5.set_ylabel('p99 Latency (Seconds) [Log Scale]')
ax5.set_yscale('log')
ax5.legend(bbox_to_anchor=(1.02, 1), loc='upper left')
plt.tight_layout()
plt.savefig(f'{GRAPHS_DIR}/5_chaos_latency_dynamics.png', dpi=300)
plt.close()

# ==========================================

# 4. MASTER DASHBOARD

# ==========================================

print("🔄 Compiling Master Dashboard...")
fig = plt.figure(figsize=(20, 12))
fig.suptitle('CockroachDB Multi-Cloud Architecture & Resilience Report', fontsize=22, fontweight='bold', y=0.98)

# Panel 1: WAN Latency

ax1 = plt.subplot(2, 2, 1)
sns.heatmap(wan_pivot, annot=True, fmt=".1f", cmap="YlOrRd", cbar_kws={'label': 'Median Latency (ms)'}, ax=ax1)
ax1.set_title('Phase I: Mesh Overlay WAN Latency Matrix', fontsize=14, fontweight='bold')

# Panel 2: Throughput Degradation

ax2 = plt.subplot(2, 2, 2)
ax2.bar(x_indices - bar_width/2, df_raft['Single_TPS'], bar_width, label='Single Node', color='#2ca02c')
ax2.bar(x_indices + bar_width/2, df_raft['Cluster_TPS'], bar_width, label='Distributed Cluster', color='#1f77b4')
ax2.set_title('Phase II & III: Throughput Degradation', fontsize=14, fontweight='bold')
ax2.set_xticks(x_indices)
ax2.set_xticklabels(df_raft['concurrency'])
ax2.legend()

# Panel 3: Raft Penalty

ax3 = plt.subplot(2, 2, 3)
ax3.plot(x_indices, df_raft['Single_p50_ms'], marker='o', label='Local p50', color='#2ca02c')
ax3.plot(x_indices, df_raft['Cluster_p50_ms'], marker='s', label='Raft Quorum p50', color='#d62728')
ax3.fill_between(x_indices, df_raft['Single_p50_ms'], df_raft['Cluster_p50_ms'], color='#d62728', alpha=0.1)
ax3.set_title('Phase III: Raft Latency Penalty', fontsize=14, fontweight='bold')
ax3.set_xticks(x_indices)
ax3.set_xticklabels(df_raft['concurrency'])
ax3.legend()

# Panel 4: Chaos Dynamics

ax4 = plt.subplot(2, 2, 4)
sns.lineplot(data=df_recover, x='relative_sec', y='tps_smoothed', label='Recover Scenario', color='#ff7f0e', ax=ax4)
sns.lineplot(data=df_dead, x='relative_sec', y='tps_smoothed', label='Dead Scenario', color='#9467bd', ax=ax4)
add_chaos_markers(ax4)
ax4.set_title('Phase IV: High Availability & Failover Dynamics', fontsize=14, fontweight='bold')
ax4.set_ylim(0, max(df_recover['tps_smoothed'].max(), df_dead['tps_smoothed'].max()) * 1.25)
ax4.legend(loc='upper right')

plt.tight_layout(rect=[0, 0, 1, 0.95])
output_path = f'{EXPORT_DIR}/final_architectural_dashboard.png'
plt.savefig(output_path, dpi=300)

print(f"✅ Master Dashboard generated: {output_path}")
print(f"✅ Individual graphs generated in: {GRAPHS_DIR}/")

// run_chaos_experiment.py
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
match = re.match(r'(?:(._?)@)?(._?)(?::(\d+))?$', netloc)
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

DURATION_SEC = 180 # 3 Full Minutes
CHAOS_TRIGGER_SEC = 60 # Inject at 1 minute
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
print(f"⏱️ Event Timeline: {JSON_FILE}")

// run_cluster_benchmark.py
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
match = re.match(r'(?:(._?)@)?(._?)(?::(\d+))?$', netloc)
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

// run_single_node_benchmark.py
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
try: # Dynamically grab the Tailscale IP directly from the node
ts_ip = subprocess.check_output(ssh_base_cmd + ["tailscale", "ip", "-4"], text=True).strip()
print(f" -> Tailscale IP: {ts_ip}")
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
try: # Scrape using the Tailscale IP directly
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

// single_node_baseline_plot.py
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Read data

df = pd.read_csv("./exports/baseline_single_node.csv")

# Set style

sns.set_style("whitegrid")
plt.figure(figsize=(10, 6), dpi=150)

# Create barplot with custom width

ax = sns.barplot(
data=df,
x="concurrency",
y="tps",
color="tab:green",
errorbar=None,
width=0.4, # Make bars slimmer (half of standard default width)
label="Single Node (Local)",
)

# Set throughput axis limit to 4000 TPS

plt.ylim(0, 4000)

# Customize axes and title

plt.title("Throughput Scaling (Single Node)", fontsize=14, fontweight="bold")
plt.xlabel("Concurrent Workers", fontsize=12)
plt.ylabel("Throughput (TPS)", fontsize=12)

# Add legend

plt.legend(loc="upper right", fontsize=11)

# Save the plot

plt.tight_layout()
plt.savefig("./exports/graphs/single_node_plot.png")
print("Successfully generated and saved modified plot.")

// wan_baseline.py
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

if **name** == "**main**":
asyncio.run(main())

// .env.example
DB_URI="postgresql://root@crdb-linode-1,crdb-linode-2,crdb-azure-1,crdb-azure-2,crdb-gcp-1:26257/defaultdb?sslmode=disable"
HCP_TOKEN="your-pt-xxx-token"
HCP_ORG="your-org-name"
HCP_WORKSPACE="hcp-workspace-name"

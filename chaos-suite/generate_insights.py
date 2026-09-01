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
def clean_chaos_data(df):
    # CRITICAL: Drop final cumulative summary artifacts (>10k TPS is impossible here)
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

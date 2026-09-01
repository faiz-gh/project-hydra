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
    df['interval'] = df.groupby('concurrency').cumcount()
    # Drop the first 5 seconds to eliminate TCP connection ramp-up skew
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
print(f"-> Gateway Routing CPU Penalty:  {avg_cpu_overhead:+.1f}% CPU vs Single Node")

if 30 <= avg_p50_overhead <= 85:
    print("-> ✅ NETWORK EXPECTATION MET: The p50 latency delta aligns perfectly with the WAN matrix from Phase I.")
    print("      CockroachDB is successfully securing a 3-node Raft quorum within the US triangle before acknowledging writes.")
elif avg_p50_overhead >= 150:
    print("-> ❌ TOPOLOGY WARNING: The latency overhead exceeds 150ms. Your zone configurations failed to isolate the Azure nodes.")

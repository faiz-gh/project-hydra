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

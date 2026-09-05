# Results data: Deployment C

Kept as its own deployment, exactly as Deployment A is kept separate from
Deployment B elsewhere in this project's documentation
(`docs/dissertation-verification.md`, `docs/gaps-resolution.md`). **Nothing
here is merged with, backfilled from, or averaged against Deployment B's
numbers.** Every value is read from one of five retained run directories or
computed by the project's own `crdblab analyze` commands against them, run in
this session against the current code.

**Run ids (Deployment C):**

| Phase | Run id |
|---|---|
| I | `20260905T202859Z_p1-network` |
| II | `20260905T203010Z_p2_baseline` |
| III | `20260905T210130Z_p3_cluster` |
| IV (recover) | `20260905T213539Z_p4-chaos-recover` |
| IV (dead) | `20260905T213941Z_p4-chaos-dead` |

---

## 1. Network matrix and quorum floor

Source: `runs/20260905T202859Z_p1-network/network.csv`. Gateway is
`crdb-gcp-1` (`TOPOLOGY-DELTA.md` § 1).

| Source | Destination | `rtt_mean_ms` | `rtt_p95_ms` | `rtt_p99_ms` | loss % |
|---|---|---|---|---|---|
| crdb-gcp-1 | crdb-linode-1 | 24.792 | 26.1 | 27.1 | 0.0 |
| crdb-gcp-1 | crdb-linode-2 | 69.662 | 69.9 | 70.3 | 0.0 |
| crdb-gcp-1 | crdb-azure-2 | 197.807 | 198.0 | 198.0 | 0.0 |
| crdb-gcp-1 | crdb-azure-1 | 219.218 | 219.0 | 220.0 | 0.0 |

Full all-pairs matrix (20 directed pairs, all 100/100 samples, 0% loss) is in
the raw CSV; the four rows above are the ones the gateway's own quorum floor
depends on.

**Quorum floor** (`preflight.json` → `derived.quorum_floor_ms`, 5 voters,
leader + 2 follower acks → second-fastest of the four follower RTTs):

```
69.662 ms
```

No committed write on this deployment can be faster than this. It is asserted
against, not merely reported: every `write_latency_floor` pre-flight check in
Phase III and every Phase IV run compares its tier's median against this exact
value (§ 3).

---

## 2. Phase II — unreplicated baseline, steady state

Source: `crdblab analyze steady-state 20260905T203010Z_p2_baseline --json`.
Warmup: declared 5.0 s, first retained interval at 6.0 s (already trimmed at
write time). Peak throughput: **2,674.912 ops/s at C=5**.

| C | reps | mean total tps | sd tps | ±95% CI | weighted p50 (ms) | N/X implied (ms) | errors |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 1285.196 | 48.352 | 120.123 | 0.762 | 0.779 | 0 |
| 2 | 3 | 2382.658 | 41.741 | 103.699 | 0.770 | 0.840 | 0 |
| 5 | 3 | 2674.912 | 54.225 | 134.714 | 1.748 | 1.870 | 0 |
| 10 | 3 | 2620.915 | 39.661 | 98.531 | 3.616 | 3.816 | 0 |
| 50 | 3 | 2610.619 | 14.438 | 35.869 | 18.424 | 19.153 | 0 |
| 100 | 3 | 2448.930 | 69.796 | 173.397 | 41.788 | 40.856 | 0 |
| 200 | 3 | 2268.984 | 92.113 | 228.840 | 90.584 | 88.242 | 0 |

Per-operation latency, never pooled (throughput summed across op types,
latency reported per type — `crdblab.analysis.steady_state.aggregation`):
`{"throughput_across_op_types": "summed", "latency_across_op_types": "never
pooled; reported per operation type"}`

| C | op | p50 (ms) | p95 (ms) | p99 (ms) | pmax (ms) |
|---:|---|---:|---:|---:|---:|
| 1 | read | 0.502 | 0.727 | 1.068 | 2.624 |
| 1 | update | 1.804 | 2.556 | 3.291 | 4.859 |
| 2 | read | 0.512 | 1.041 | 1.644 | 5.024 |
| 2 | update | 1.807 | 2.908 | 4.028 | 7.639 |
| 5 | read | 1.321 | 3.315 | 4.853 | 11.996 |
| 5 | update | 3.451 | 6.093 | 8.582 | 13.848 |
| 10 | read | 2.935 | 7.112 | 9.450 | 17.159 |
| 10 | update | 6.321 | 11.178 | 14.990 | 21.185 |
| 50 | read | 17.048 | 30.501 | 36.087 | 48.341 |
| 50 | update | 23.885 | 42.995 | 51.942 | 62.758 |
| 100 | read | 36.866 | 58.394 | 67.542 | 82.222 |
| 100 | update | 61.347 | 92.340 | 105.235 | 118.210 |
| 200 | read | 82.650 | 116.058 | 130.984 | 147.191 |
| 200 | update | 121.949 | 173.017 | 191.776 | 204.979 |

---

## 3. Phase III — five-node cluster, steady state

Source: `crdblab analyze steady-state 20260905T210130Z_p3_cluster --json`.
Same warmup window. Peak throughput: **2,696.710 ops/s at C=100**.

| C | reps | mean total tps | sd tps | ±95% CI | weighted p50 (ms) | N/X implied (ms) | errors |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 65.119 | 1.778 | 4.418 | 16.782 | 15.364 | 0 |
| 2 | 3 | 133.235 | 1.486 | 3.693 | 15.868 | 15.012 | 0 |
| 5 | 3 | 331.318 | 2.175 | 5.402 | 15.688 | 15.092 | 0 |
| 10 | 3 | 656.161 | 6.615 | 16.434 | 15.517 | 15.241 | 0 |
| 50 | 3 | 2359.017 | 40.196 | 99.860 | 20.871 | 21.199 | 0 |
| 100 | 3 | 2696.710 | 14.943 | 37.123 | 36.784 | 37.083 | 0 |
| 200 | 3 | 2651.177 | 69.373 | 172.346 | 76.521 | 75.472 | 0 |

Per-operation latency:

| C | op | p50 (ms) | p95 (ms) | p99 (ms) | pmax (ms) |
|---:|---|---:|---:|---:|---:|
| 1 | read | 0.519 | 1.019 | 1.326 | 1.599 |
| 1 | update | 76.849 | 77.595 | 78.571 | 78.571 |
| 2 | read | 0.521 | 0.992 | 1.312 | 1.717 |
| 2 | update | 73.922 | 75.271 | 76.213 | 76.213 |
| 5 | read | 0.523 | 1.112 | 1.615 | 2.969 |
| 5 | update | 74.438 | 76.709 | 78.001 | 79.341 |
| 10 | read | 0.651 | 1.521 | 2.451 | 4.524 |
| 10 | update | 72.751 | 76.897 | 80.585 | 82.744 |
| 50 | read | 3.732 | 11.843 | 17.034 | 38.404 |
| 50 | update | 88.650 | 104.665 | 112.759 | 120.608 |
| 100 | read | 13.005 | 31.402 | 42.407 | 84.055 |
| 100 | update | 131.221 | 170.128 | 184.204 | 201.461 |
| 200 | read | 52.457 | 82.322 | 94.755 | 130.821 |
| 200 | update | 171.792 | 215.455 | 231.880 | 245.244 |

Every update p50 above sits **at or above the 69.662 ms quorum floor** (§ 1),
including the lightest tier, C=1 at 76.849 ms — a committed quorum write cannot
be faster than the floor, and none is.

---

## 4. Little's law: implied mean latency (N/X) vs. weighted p50

For a closed workload of `N` concurrent workers, `N = X · R`, so `N/X` is the
implied mean residence time — an upper bound on the true mean, since idle
workers inflate it. The comparator is the **frequency-weighted** median
(`sum(share_op · p50_op)`), not the slowest operation
(`crdblab/analysis/validation.py::check_littles_law` docstring). Agreement
below is `|N/X − weighted p50| / weighted p50`, the same formula
`raft_overhead.py`'s own `littles_law_agreement` field uses.

| C | Phase II N/X | Phase II weighted p50 | agreement | Phase III N/X | Phase III weighted p50 | agreement |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.779 | 0.762 | 2.23% | 15.364 | 16.782 | 8.45% |
| 2 | 0.840 | 0.770 | 9.09% | 15.012 | 15.868 | 5.39% |
| 5 | 1.870 | 1.748 | 6.98% | 15.092 | 15.688 | 3.80% |
| 10 | 3.816 | 3.616 | 5.53% | 15.241 | 15.517 | 1.78% |
| 50 | 19.153 | 18.424 | 3.96% | 21.199 | 20.871 | 1.57% |
| 100 | 40.856 | 41.788 | 2.23% | 37.083 | 36.784 | 0.81% |
| 200 | 88.242 | 90.584 | 2.59% | 75.472 | 76.521 | 1.37% |

Every tier in both phases holds `check_littles_law`'s ≥0.9 lower-bound
tolerance by a wide margin (worst case 9.09%, Phase II C=2); `crdblab validate`
independently confirms this for every tier (§ 7).

*Note on precision:* `raft_overhead.py`'s own `lightest_load_write_latency`
block recomputes N/X for the C=1 tiers directly from raw per-tick data rather
than reusing `steady_state`'s already-rounded tier table, and reads 0.778 ms
(Phase II) where the table above reads 0.779 ms — a one-thousandth-of-a-
millisecond difference from where in the pipeline rounding happens, not a
disagreement about the measurement. Both are quoted in this document, each
against the command that produced it, rather than silently reconciled to one.

---

## 5. Replication cost — all four framings

Source: `crdblab analyze raft-overhead --baseline 20260905T203010Z_p2_baseline
--cluster 20260905T210130Z_p3_cluster --json`, **no**
`--accept-hardware-difference` (confirmed unnecessary in
`HARDWARE-COMPARABILITY.md`). `comparability: {"ok": true, "findings": []}`.
`server_config`:

```json
{
  "phase_ii": "2400 cockroach start-single-node --insecure --store=/var/lib/cockroach --listen-addr=100.100.93.75:26257 --http-addr=100.100.93.75:8080 --cache=0.25 --max-sql-memory=0.25",
  "phase_iii": "2549 cockroach start --insecure --store=/var/lib/cockroach --listen-addr=100.121.13.30:26257 --advertise-addr=100.121.13.30:26257 --locality=cloud=gcp,region=us-east1 --cache=0.25 --max-sql-memory=0.25 --join=crdb-gcp-1,crdb-azure-1,crdb-azure-2,crdb-linode-1,crdb-linode-2"
}
```

Saturation: both phases flattened (`saturated: true` for both; Phase II final-tier
gain −7.35%, Phase III −1.69%), so both peaks above are genuine capacities, not
lower bounds.

### 5a. Matched throughput (both phases measured near the same load)

Comparable range: phase II 1285.2–2674.9 ops/s, phase III 65.1–2696.7 ops/s —
they overlap, so this framing is available (unlike a sweep whose ranges do not
overlap, where the CLI reports `NOT AVAILABLE` rather than extrapolating).

| Throughput (ops/s) | Phase II p50 (ms) | Phase III p50 (ms) | overhead ×  | utilisation gap |
|---:|---:|---:|---:|---:|
| 1285.2 | 1.804 | 78.624 | **43.58×** | 0.004 |
| 2269.0 | 1.807 | 87.809 | 48.61× | 0.007 |
| 2359.0 | 1.807 | 88.650 | 49.07× | 0.007 |
| 2382.7 | 1.807 | 91.630 | 50.71× | 0.007 |
| 2448.9 | 2.180 | 99.985 | 45.87× | 0.007 |
| 2610.6 | 3.089 | 120.368 | 38.96× | 0.008 |
| 2620.9 | 3.147 | 121.666 | 38.66× | 0.008 |
| 2651.2 | 3.317 | 125.481 | 37.83× | 0.008 |
| 2674.9 | 3.451 | 128.473 | 37.23× | 0.008 |

**Least-confounded point: 43.58× at 1285.2 ops/s** (utilisation gap 0.004 — the
smallest in the table), not the largest ratio in the table — quoting the
largest is exactly the error the analysis code's own comment warns against,
since the ratio inflates with the utilisation gap. Values off a phase's own
measured tiers are linearly interpolated between bracketing tiers; the true
curve is convex near saturation, so an interpolated latency is an
underestimate there.

### 5b. Matched utilisation (phases at different throughputs by construction)

| Utilisation | Phase II tps | Phase III tps | Phase II p50 | Phase III p50 | overhead × |
|---:|---:|---:|---:|---:|---:|
| 48% | 1285.2 | 1295.7 | 1.804 | 78.722 | 43.63× |
| 84.8% | 2269.0 | 2287.5 | 1.807 | 87.982 | 48.70× |
| 87.5% | 2339.9 | 2359.0 | 1.807 | 88.650 | 49.07× |
| 89.1% | 2382.7 | 2402.1 | 1.807 | 94.078 | 52.07× |
| 91.6% | 2448.9 | 2468.9 | 2.180 | 102.500 | 47.03× |
| 97.6% | 2610.6 | 2631.9 | 3.089 | 123.050 | 39.83× |
| 98.0% | 2620.9 | 2642.3 | 3.147 | 124.359 | 39.51× |
| 98.3% | 2629.7 | 2651.2 | 3.197 | 125.481 | 39.25× |
| 100% | 2674.9 | 2696.7 | 3.451 | 131.221 | 38.03× |

Peak capacities: Phase II 2674.9 ops/s, Phase III 2696.7 ops/s. Caveat, quoted
verbatim from the tool's own output: "the two phases are compared at different
throughputs by construction, so a ratio here is not the cost of replication at
any single offered load; capacity is each phase's own measured peak, which is
a lower bound if that phase had not saturated" — not the case here, since both
saturated.

### 5c. Lightest-load write median (both single-worker, unqueued)

```
Phase II: C=1, update p50 = 1.804 ms, offered load 1285.2 ops/s
Phase III: C=1, update p50 = 76.849 ms, offered load 65.1 ops/s
ratio: 42.59×
both_unqueued: true
```

Caveat, verbatim: "both medians are single-worker measurements, so exactly one
operation was outstanding in each and neither median contains queueing
(Little's law corroborates to 8.5%). The throughputs differ (1285 vs 65 ops/s)
as a consequence of the latency difference, not as a confound in it. This is
the least confounded replication-cost figure the experiment produces."

### 5d. Same-concurrency delta — **NOT A RESULT**

Reproduced in full because the tool marks it as such and the reason is itself
part of the record, not because it should be cited as a finding:

| C | Phase II tps | Phase III tps | throughput ratio × | read p50 ratio × | update p50 ratio × |
|---:|---:|---:|---:|---:|---:|
| 1 | 1285.2 | 65.1 | 19.74 | 1.03 | 42.59 |
| 2 | 2382.7 | 133.2 | 17.88 | 1.02 | 40.91 |
| 5 | 2674.9 | 331.3 | 8.07 | 0.40 | 21.57 |
| 10 | 2620.9 | 656.2 | 3.99 | 0.22 | 11.51 |
| 50 | 2610.6 | 2359.0 | 1.11 | 0.22 | 3.71 |
| 100 | 2448.9 | 2696.7 | 0.91 | 0.35 | 2.14 |
| 200 | 2269.0 | 2651.2 | 0.86 | 0.63 | 1.41 |

Reason, verbatim: "concurrency fixes the worker count, not the offered load, so
the two phases sit at different points on their own throughput-latency curves.
In this data the cluster's read median is *lower* than the single node's at
C=10 because the single node is carrying five times the load at the same
worker count — an artefact in the direction that flatters the cluster." Use:
"Chapter 5 error case study only; never as a results table."

---

## 6. Phase IV — both fault classes

Source: `crdblab analyze resilience <run> --network-run
runs/20260905T202859Z_p1-network/network.csv --json` for each run, this
session, current code (i.e. including the two corrections in `NEW-DEFECTS.md`
D14–D15).

### 6a. Recover (`20260905T213539Z_p4-chaos-recover`)

| Quantity | Value |
|---|---|
| Clock alignment | measured; generator zero 4.282 s after epoch, constant to ±0.072 s over 360 intervals |
| Fault offset | 60.005 s (wall clock) |
| **Availability RTO (audit log)** | not measurable — 0.331 s observed, below the 0.421 s audit-cadence resolution; claim: "no write interruption detectable at 0.42 s resolution" |
| **Availability RTO (RTO probe)** | not measurable — no gap after the fault exceeded the 474 ms detection threshold (longest healthy gap + one 109 ms sampling period); `resolution_s` achieved: **109.176 ms** |
| Performance RTO | 32.276 s (throughput sustainably back above 2,220.26 ops/s floor; baseline 2,775.33 ops/s) |
| Quorum geometry | floor rises 69.66 ms → 197.81 ms (×2.84) with `linode-2` down; a quorum survives, so this is a latency change, not an outage |
| RPO | **0 violations of 430 acknowledged** |
| Probe load added | 18.24 writes/s achieved (3,370 attempts, 100% served) |

### 6b. Dead (`20260905T213941Z_p4-chaos-dead`)

| Quantity | Value |
|---|---|
| Clock alignment | measured; generator zero 5.549 s after epoch, constant to ±0.054 s over 360 intervals |
| Fault offset | 60.004 s (wall clock) |
| **Availability RTO (audit log)** | not measurable — 0.366 s observed, below the 0.590 s audit-cadence resolution |
| **Availability RTO (RTO probe) — see below** | *not* below resolution, but *not* quotable; see § 6c |
| Performance RTO | 26.543 s (throughput sustainably back above 2,207.06 ops/s floor; baseline 2,758.82 ops/s) |
| Quorum geometry | identical shape to recover: ×2.84, floor 69.66 → 197.81 ms |
| RPO | **0 violations of 296 acknowledged** |
| Probe load added | 11.65 writes/s achieved (2,174 attempts, 100% served) |

### 6c. The dead run's probe RTO — the case the task asks to be stated explicitly

The dead-fault run is the one case in Deployment C where the probe's raw
statistic is **not below its own resolution** — it is a genuine candidate
measured value, not a bound — and it must be reported precisely rather than
either quoted at face value or silently dropped:

```
observed gap:            869.283 ms, spanning wall offset 100.220s–101.089s
resolution achieved:      223.915 ms   (869.283 ms clears this; below_resolution: false)
naive RTO (fault→edge):   41,085.058 ms  (41.085 s)
```

**It is still not quotable, for a different reason than resolution.** The
exceedance-rate test this session's fix introduced
(`crdblab.core.rto_probe.tail_attribution`, `NEW-DEFECTS.md` D14) finds this
gap **not attributable to the fault**:

```json
{
  "testable": true,
  "reference_s": 0.223915,
  "pre_fault_gaps": 710,
  "post_fault_gaps": 1463,
  "pre_fault_exceedances": 35,
  "post_fault_exceedances": 30,
  "expected_post_fault_exceedances": 72.1,
  "exceedance_rate_ratio": 0.42,
  "heavier_after_fault": false
}
```

Large gaps (over the pre-fault 95th percentile) occurred **less** often per
observation after the fault than before it (30 against an expected 72.1 —
ratio 0.42, not ≥1.5). The post-fault window merely held more than twice as
many observations (1,463 against 711), so its maximum is larger for that
reason alone. `quotable_value_s` is `null`; the reported claim is: *"a 869 ms
gap in served writes occurred 40.2s after the fault, but it is NOT
distinguishable from this probe's own tail... Do not quote it as a recovery
time."*

**This differs in kind from Deployment B's result, and the difference is that
Deployment B has no result to compare against at this resolution at all.**
Re-running `crdblab analyze resilience` against Deployment B's own dead-fault
run (`20260903T004024Z_p4-chaos-dead`) under the current code:

```json
"probe_rto": {
  "available": false,
  "detail": "20260903T004024Z_p4-chaos-dead predates the high-frequency RTO probe. Its availability RTO comes from the RPO audit log alone and is bounded by that client's cadence, not by the probe's"
}
```

Deployment B's own audit-log-based availability RTO for that run is likewise a
non-measurable bound (the same shape as § 6a/6b's audit-log rows here) — so the
comparison is not "Deployment B measured X ms, Deployment C measured Y ms"; it
is that Deployment C is the first deployment in this project's history capable
of asking the exceedance-rate question at all, and the answer it gave, on the
one fault class where the question was live, was "no."

---

## 7. Validation and pre-flight, this deployment

See `VALIDATION-STATUS.md` for the full table; summarised here for
completeness: all five Deployment C runs pass `crdblab validate` (Phase I has
no `metrics.csv` and is checked via `preflight.json` alone, per the tool's own
documented behaviour), and both Phase IV runs additionally pass
`validate_probe()` on `rto_probe.csv` (`{"ok": true, "findings": []}` for both).

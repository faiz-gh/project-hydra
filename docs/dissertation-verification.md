# Dissertation verification against repository and retained artefacts

Independent check of the dissertation's factual and numerical claims against the
`crdblab` code and the run directories retained under `runs/`.

**Rule applied throughout:** no value in this document is estimated, inferred, or
reconstructed. Where a figure cannot be read from an artefact or recomputed from
code, it is recorded as `NOT FOUND`. Derived values are given unrounded as well
as rounded.

---

## 0. Provenance of this check

### Revision inspected

| | |
|---|---|
| Working tree inspected | `119e2448a839f4a2e746afc46b83ea4b687cdf76` (HEAD, `master`), committed 2026-09-04T03:36:52+05:30 |
| Working tree state | `docs/resolution.md` deleted (unstaged), `docs/gaps-resolution.md` untracked. No source file modified. |
| Revision recorded in **every** run manifest | `793162b20ba125dd128c2cdd9f5d53156a2d0075`, committed 2026-09-01T23:24:25+05:30 |

`git rev-parse HEAD` / `git log --format="%H %cI %s" -3`.

**The recorded revision differs from the inspected one, and differs far more than
a version bump.** At `793162b2` the repository contained only:

```
$ git ls-tree --name-only 793162b20ba125dd128c2cdd9f5d53156a2d0075
.gitignore  LICENSE  README.md  chaos-suite  python.md  terraform.md  terraform

$ git ls-tree -r --name-only 793162b20ba125dd128c2cdd9f5d53156a2d0075 -- crdblab tests
(empty)
```

The `crdblab` package — every phase script, the recorder, the analysis layer —
**did not exist at the revision every manifest names.** `recorder.py:142`
captures the revision with `git rev-parse HEAD`, which returns the last *commit*;
the harness itself was uncommitted working-tree state at measurement time. See
Part 3 § 2.

### Artefact status

`runs/` is **present and complete**: 22 run directories, all readable, all five
Deployment B runs the dissertation reports among them. `figures/` holds 6 PNG and
6 PDF. Every claim below is answerable from artefacts; nothing was skipped for
want of data.

`crdblab.egg-info` is present and `import crdblab` succeeds in `.venv`
(pandas 3.0.5), so the project's own analysis layer was used in preference to
ad-hoc computation wherever it exposes the quantity.

### Deployment map

Three deployments, identified by the CockroachDB `--listen-addr` recorded in each
manifest's `server:` note. Address sets are disjoint (Part 3 § 7).

| | Deployment 1 | **Deployment A** (2) | **Deployment B** (3) |
|---|---|---|---|
| Window (UTC) | 2026-09-02 02:14–02:40 | 2026-09-02 15:25–22:37 | 2026-09-02 23:32 – 09-03 00:43 |
| Baseline node addr | `100.103.70.41` | `100.70.55.65` | `100.96.175.102` |
| Gateway node addr | `100.97.1.104` | `100.125.217.116` | `100.70.90.51` |
| Phase I | `20260902T021404Z_p1-network` | `20260902T152535Z_p1-network` | `20260902T233208Z_p1-network` |
| Phase II | `20260902T021525Z_p2_baseline` (smoke) | `20260902T175621Z_p2_baseline` | `20260902T233336Z_p2_baseline` |
| Phase III | `20260902T021648Z_p3_cluster` (smoke) | `20260902T195644Z_p3_cluster` | `20260903T000438Z_p3_cluster` |
| Phase IV | `…022406Z_p4-chaos-dead`, `…024023Z_…recover` | `…165959Z_…recover`, `…170444Z_…dead` | `…003646Z_…recover`, `…004024Z_…dead` |

"Deployment A" and "Deployment B" are used below as the task uses them:
A = deployment 2 (quorum floor 66.925 ms), B = deployment 3 (quorum floor
67.054 ms). **Deployment B is the reported deployment** — it is what every figure
footer stamps.

### Commands used

```
.venv/bin/python -m crdblab analyze steady-state <run_id> --json
.venv/bin/python -m crdblab analyze raft-overhead --baseline <p2> --cluster <p3> [--accept-hardware-difference] [--json]
.venv/bin/python -m crdblab analyze resilience <run_id> --json
.venv/bin/python -m pytest --collect-only -q ; .venv/bin/python -m pytest -q
```

Direct reads of `manifest.json`, `preflight.json`, `network.csv`, `audit.csv`,
`events.json`. The project's analysis modules (`crdblab.analysis.steady_state`,
`.loader`) were imported directly **only** where the CLI exposes no such view:
the two Little's-law methods, per-tier CPU, and the within-sweep drift
regression. Each such case is labelled below. Figure caption text was extracted
from the PDF content streams by decoding the embedded font `/Differences`
encodings — exact glyph strings, not OCR.

---

## Part 1 — Claims table

### Phase I / network

Source run: `runs/20260902T233208Z_p1-network/` (Deployment B).

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 1.1 | Quorum floor, Deployment B | 67.054 ms | **CONFIRMED** | `67.054` | `runs/20260902T233208Z_p1-network/preflight.json` → `derived.quorum_floor_ms`. Cross-checked in `runs/20260903T000438Z_p3_cluster/manifest.json` → `validation.preflight.checks[quorum_floor_available].quorum_floor_ms = 67.054` |
| 1.2 | What that figure is | mean RTT gateway → `crdb-linode-2` | **CONFIRMED** | `rtt_mean_ms = 67.054` for row `crdb-linode-1 → crdb-linode-2` | `runs/20260902T233208Z_p1-network/network.csv`. `preflight.quorum_floor_ms` (`crdblab/core/preflight.py:515-539`) sorts the four gateway RTTs and returns `ordered[voters//2 - 1] = ordered[1]`, i.e. the second-fastest follower. Sorted: 18.324, **67.054**, 198.183, 200.051 |
| 1.3 | Mean RTT gateway → gcp-1 | 18.3 ms | **CONFIRMED** | `18.324` | `network.csv`, row `crdb-linode-1,crdb-gcp-1`, column `rtt_mean_ms` |
| 1.4 | Mean RTT gateway → linode-2 | 67.1 ms | **CONFIRMED** | `67.054` → 67.1 | `network.csv`, row `crdb-linode-1,crdb-linode-2` |
| 1.5 | Mean RTT gateway → azure-1 | 198.2 ms | **CONFIRMED** | `198.183` → 198.2 | `network.csv`, row `crdb-linode-1,crdb-azure-1` |
| 1.6 | Mean RTT gateway → azure-2 | 200.1 ms | **CONFIRMED** | `200.051` → 200.1 | `network.csv`, row `crdb-linode-1,crdb-azure-2` |
| 1.7 | Mean RTT azure-1 ↔ azure-2 | 78.8 ms | **CONFIRMED** | a1→a2 `78.761`; a2→a1 `78.829`. Both round to 78.8 | `network.csv`, both directional rows. The claim is directionless and true in both directions |
| 1.8 | Path MTU, all links | 1280 uniformly | **CONFIRMED (value); description imprecise** | 1280 on all five nodes | `runs/20260902T233208Z_p1-network/manifest.json` → `notes`, five entries "`<host>: tailscale MTU 1280`". **This is the `tailscale0` *interface* MTU**, read by `cat /sys/class/net/tailscale0/mtu` (`crdblab/phases/p1_network.py:159`), not a per-link path-MTU discovery. No per-link PMTU was measured. "Interface MTU 1280 on all five nodes" is what the artefact supports |
| 1.9 | Packet loss, all links | 0.0% | **CONFIRMED** | `loss_pct` unique values = `[0.0]` across all 20 rows, `samples = 100` each | `network.csv`; `pandas` `df.loss_pct.unique()` |
| 1.10 | Largest clock offset any node | 0.31 ms | **CONFIRMED** | `linode-2: 0.31 ms` (`offset_s = 0.000314149`). Others: 0.06, 0.00, 0.00, 0.00 | `runs/20260902T233208Z_p1-network/preflight.json` → `checks[name=clock_offset]` |
| 1.11 | Clock offset tolerance | 250 ms | **CONFIRMED** | "(limit 250 ms)" in all five check details | same file |
| 1.12 | Leaseholder placement | 2/2 in gateway's own region | **CONFIRMED** | `"2/2 ycsb leaseholders in 'us-east'"`, `distribution = {"cloud=linode,region=us-east": 2}` | same file, `checks[name=leaseholder_placement]` |
| 1.13 | Phase I pre-flight | 6 of 6 passed | **CONFIRMED** | 6 checks, 6 passed, `ok = true` (5× `clock_offset` + 1× `leaseholder_placement`) | same file |
| 1.14 | Post-fault floor with linode-2 down | 198.2 ms, factor 2.96 | **CONFIRMED** | `surviving_quorum_floor_ms = 198.18`, `floor_ratio_x = 2.96`. Unrounded 198.183 / 67.054 = **2.9555731201718016** | `crdblab analyze resilience 20260903T004024Z_p4-chaos-dead --json` → `quorum_geometry`. Identical in the `recover` run |

### Phase II / III steady state

Source runs: `20260902T233336Z_p2_baseline` (Phase II) and
`20260903T000438Z_p3_cluster` (Phase III), Deployment B.

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 2.1 | Phase II peak | 3,563.3 ops/s at C=50 | **CONFIRMED** | `{"concurrency": 50, "mean_total_tps": 3563.335}` | `crdblab analyze steady-state 20260902T233336Z_p2_baseline --json` → `peak_throughput` |
| 2.2 | Phase III peak | 1,849.5 ops/s at C=100 | **CONFIRMED** | `{"concurrency": 100, "mean_total_tps": 1849.548}` | `crdblab analyze steady-state 20260903T000438Z_p3_cluster --json` → `peak_throughput` |
| 2.3 | Phase II update p50 at C=1 | 1.42 ms | **CONFIRMED** | `1.4157575757575758` → 1.42 | Phase II `--json` → `latency_by_op`, `{concurrency:1, op:"update"}` |
| 2.4 | Phase II update p50 at C=2 | 1.48 ms (unrounded 1.48485) | **CONFIRMED** | `1.4848484848484846` → 1.48485 → 1.48 | same, `{concurrency:2, op:"update"}` |
| 2.5 | Phase III update p50 at C=1, 2, 5 | 71.33, 71.35, 71.33 ms | **CONFIRMED** | `71.32545454545455`, `71.3509090909091`, `71.32545454545455` | Phase III `--json` → `latency_by_op` |
| 2.6 | Phase III read p50 at C=1, 2, 5 | 0.70, 0.74, 0.76 ms | **CONFIRMED** | `0.7018181818181818`, `0.7375757575757577`, `0.7648484848484848` | same |
| 2.7 | Phase II 95% CI at C=1, 2, 5 | ±523.0, ±812.1, ±524.7 | **CONFIRMED** | `522.977`, `812.092`, `524.652` | Phase II `--json` → `tiers[].ci95_half_width_tps`. Student's t over 3 repetition means, `steady_state.confidence_interval` |
| 2.8 | Phase II 95% CI at C=100, 200 | ±97.3, ±110.3 | **CONFIRMED** | `97.344`, `110.344` | same |
| 2.9 | Errors across both sweeps | 0 | **CONFIRMED** | `errors_cum = 0` in all 7 tiers of both runs (14/14) | both `--json` → `tiers[].errors_cum` |
| 2.10 | Little's law, Phase II, per-tier range | 0.3% to 6.7% | **CONFIRMED** | **0.2990% (C=2) to 6.7114% (C=5)** | Computed by importing `crdblab.analysis.steady_state.per_tier`; the CLI exposes the two columns but not their ratio. Per tier: `abs(implied_mean_latency_ms − mean_weighted_p50_ms) / mean_weighted_p50_ms`. Full set: C=1 1.9868%, C=2 0.2990%, C=5 6.7114%, C=10 5.9154%, C=50 1.3119%, C=100 0.5374%, C=200 1.6703% |
| 2.11 | Little's law, Phase II at C=1, per-tier method | 1.99% → 2.0% | **CONFIRMED** | **1.9868%** → 1.99% → 2.0%. (`implied_mean_latency_ms = 0.592`, `mean_weighted_p50_ms = 0.604`) | as 2.10 |
| 2.12 | Little's law, Phase III at C=1, **per-tier method** | 6.2% | **CONFIRMED** | **6.2080%** (unrounded 0.062080…). `implied_mean_latency_ms = 14.791`, `mean_weighted_p50_ms = 15.770` | as 2.10 |
| 2.13 | Little's law, Phase III at C=1, tier-mean method | give the value; does it also round to 6.2%? | **CONFIRMED — yes** | **6.2084%**. Computed as `abs(C/mean_total_tps×1000 − mean_weighted_p50_ms)/mean_weighted_p50_ms` = `abs(14.790930 − 15.770)/15.770`. This is the method `raft_overhead.lightest_load_write_latency` uses; it reports `littles_law_agreement: 0.0621` and prints "Little's law corroborates to 6.2%" | `crdblab analyze raft-overhead … --json` → `lightest_load_write_latency.phase_iii.littles_law_agreement`; method at `crdblab/analysis/raft_overhead.py:437-458` |
| 2.14 | Both phases recorded as saturated | yes | **CONFIRMED** | `phase_ii: saturated=true, final_tier_gain=0.0136`; `phase_iii: saturated=true, final_tier_gain=−0.0922`. Threshold `SATURATION_TOLERANCE = 0.05` | `raft-overhead --json` → `saturation` |
| 2.15 | Phase II pre-flight | 22 of 22 | **CONFIRMED** | 22 checks, 22 passed, `ok = true` (1 `clock_offset` + 21 `row_match`) | `runs/20260902T233336Z_p2_baseline/preflight.json` |
| 2.16 | Phase III pre-flight | 45 of 45 | **CONFIRMED** | 45 checks, 45 passed, `ok = true` (1 `clock_offset`, 1 `leaseholder_placement`, 1 `quorum_floor_available`, 21 `write_latency_floor`, 21 `row_match`) | `runs/20260903T000438Z_p3_cluster/preflight.json` |

**Note on 2.11 vs 2.12/2.13.** The two methods are genuinely different and the
dissertation is right to distinguish them, but only because Phase III happens to
agree to four decimal places. In Phase II they diverge materially: C=1 is 1.9868%
per-tier against **3.0245%** tier-mean, and the tier-mean method is what the
`raft-overhead` caveat string reports (`phase_ii.littles_law_agreement: 0.0302`).
Quoting "2.0%" for Phase II C=1 and "6.2%" for Phase III C=1 mixes the two
methods unless the per-tier method is stated for both.

### Replication cost

`crdblab analyze raft-overhead --baseline 20260902T233336Z_p2_baseline --cluster
20260903T000438Z_p3_cluster --accept-hardware-difference [--json]`.
The flag is required for this pair; see Part 3 § 5.

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 3.1 | Unqueued write-latency ratio | 50.3797 → 50.38 / 50.4 | **CONFIRMED** | `ratio_x = 50.38`. Unrounded **50.37970890410959** = 71.32545454545455 / 1.4157575757575758 | `raft-overhead --json` → `lightest_load_write_latency.ratio_x`, computed from `_p50_exact` on both sides (`raft_overhead.py:461-463`) |
| 3.2 | Equal-concurrency throughput ratio, C=1 and C=100 | 25.26x, 1.86x | **CONTRADICTED (C=1)** | C=1: reported **25.26**, correct **25.25** (1707.266/67.609 = 25.252052). C=100: **1.86** — correct. Also C=2: reported 22.42, correct 22.43 | `raft-overhead --json` → `same_concurrency_delta.rows`. The 25.26 is an artefact of `raft_overhead.py:520-523`, which rounds both throughputs to 1 dp and then divides: 1707.3/67.6 = 25.2559. See Part 3 § 1 |
| 3.3 | Equal-concurrency update p50 ratio, C=1 and C=200 | 50.38x, 2.92x | **CONFIRMED** | `update_p50_ratio_x`: C=1 `50.38`, C=200 `2.92`. Computed from unrounded medians (`raft_overhead.py:528`), so unaffected by the defect above | same |
| 3.4 | Read p50 ratio at C=1 | 1.75x | **CONFIRMED** | `read_p50_ratio_x = 1.75` (0.7018181818181818 / 0.4012121212121212) | same |
| 3.5 | Matched-throughput overlap band | 1,707–1,850 ops/s | **CONFIRMED** | `overlap_tps = [1707.3, 1849.5]` | `raft-overhead --json` → `matched_throughput.overlap_tps` |
| 3.6 | Overhead at 1707, 1742, 1850 | 73.75x, 74.40x, 112.38x | **CONFIRMED** | `73.75`, `74.4`, `112.38` | → `matched_throughput.points[].overhead_x` |
| 3.7 | Utilisation gaps in that band | 0.44, 0.45, 0.48; narrowest 0.444 | **CONFIRMED** | `0.444`, `0.453`, `0.481`. Narrowest = 0.444 at 1707.3 ops/s, flagged `least_confounded`. Unrounded: **0.44395175309444834**, 0.45304315489808533, 0.4809502895461696 | → `matched_throughput.points[].utilisation_gap` and `.least_confounded`; unrounded recomputed from `throughput_latency_curve` peaks |
| 3.8 | Matched-utilisation overhead range | 67.31x at 84% to 8.16x at 100% | **CONFIRMED** | `67.31` at `utilisation = 0.843`; `8.16` at `utilisation = 1.0`. Eight points, non-monotonic in between (47.92, 41.92, 46.27, 48.31, 48.55, 31.07) | → `matched_utilisation.points` |
| 3.9 | Full span across all four framings | 8.16 to 112.38 | **CONFIRMED** | min 8.16 (matched utilisation, 100%); max 112.38 (matched throughput, 1849.5 ops/s). Lightest-load 50.38, same-concurrency 2.92–50.38 all inside | all four blocks of `raft-overhead --json` |
| 3.10 | Deployment A: Phase II peak, Phase III peak | 2,565 and 1,791 ops/s | **CONFIRMED** | `2564.603` (C=50) and `1791.463` (C=100) | `crdblab analyze steady-state 20260902T175621Z_p2_baseline --json` and `… 20260902T195644Z_p3_cluster --json` → `peak_throughput` |
| 3.11 | Deployment A: write p50 @C=1 both phases | 2.18 and 72.67 ms | **CONFIRMED** | `2.175757575757576` and `72.67454545454545` | same, `latency_by_op`, `{concurrency:1, op:"update"}` |
| 3.12 | Replication-cost range across deployments | 33.4x to 50.4x | **CONFIRMED** | A: `ratio_x = 33.4`, unrounded **33.40194986072423**. B: `ratio_x = 50.38`, unrounded 50.37970890410959 | `crdblab analyze raft-overhead --baseline 20260902T175621Z_p2_baseline --cluster 20260902T195644Z_p3_cluster --json` → `lightest_load_write_latency.ratio_x`; and the Deployment B run above |
| 3.13 | Cluster reproduced across redeployment to within | 3.6% | **CONFIRMED (as a rounded figure); not a strict bound** | Largest per-tier throughput deviation **3.6485185185185185%** at C=200 (1620.0 → 1679.106). Peak-to-peak 3.2422%. All seven: C=1 2.777, C=2 2.351, C=5 1.751, C=10 3.577, C=50 0.079, C=100 3.242, C=200 **3.649** | `per_tier` of both Phase III runs via `crdblab.analysis.steady_state`. 3.649% rounds to 3.6%, but as a *bound* the honest statement is "within 3.7%" |
| 3.14 | Baseline moved by up to | 58% | **CONFIRMED** | Largest per-tier change **57.806216060080885%** at C=1 (1081.875 → 1707.266). Others: C=2 44.757, C=5 44.480, C=10 42.346, C=50 38.943, C=100 37.907, C=200 44.357 | `per_tier` of both Phase II runs |

### Phase IV

Source runs: `20260903T003646Z_p4-chaos-recover`, `20260903T004024Z_p4-chaos-dead`
(Deployment B). `crdblab analyze resilience <run> --json`.

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 4.1 | recover: avail RTO, resolution, write gap | 0.272 s, 0.40 s, 0.380 s | **CONFIRMED** | `availability_rto_s = 0.272`, `resolution_s = 0.401` (printed as "0.40 s resolution"), `write_gap_s = 0.38` | `resilience --json` → `availability_rto`. `source: "re-derived from audit.csv"` |
| 4.2 | dead: avail RTO, resolution, write gap | 0.113 s, 0.47 s, 0.401 s | **CONFIRMED** | `availability_rto_s = 0.113`, `resolution_s = 0.467` (printed "0.47 s"), `write_gap_s = 0.401` | same |
| 4.3 | Performance RTO, recover and dead | 4.4 s and 12.0 s | **CONFIRMED** | recover `rto_s = 4.389`, claim "sustainably back within 4.4 s"; dead `rto_s = 12.0`. Both `defined: true`, `agrees_with_recorded: true`, `recompute_delta_s = 0.0` | → `performance_rto` |
| 4.4 | RPO, recover and dead | 0 of 445, 0 of 385 | **CONFIRMED** | recover `rpo_violations = 0`, `acknowledged = 445`, `present_in_table = 445`; dead `0` of `385`, `present_in_table = 385` | → `rpo` |
| 4.5 | Ambiguous / refused, both runs | 0 / 0 | **CONFIRMED** | `ambiguous = 0`, `refused = 0`, `ambiguous_but_committed = 0`, `lost_seq_ids = []` in both | same |
| 4.6 | Acknowledged writes after fault | 300 and 244 | **CONFIRMED** | `writes_acknowledged_after_fault`: 300 (recover), 244 (dead) | → `availability_rto` |
| 4.7 | Clock alignment, recover | 4.396 s, constant within 0.199 s, 360 intervals | **CONFIRMED** | `method: "measured"`, `generator_start_offset_s = 4.396`, `spread_s = 0.199`, detail names "360 intervals". Bounds [4.393, 4.592] | → `clock_alignment` |
| 4.8 | Clock alignment, dead | 3.979 s, constant within 0.215 s, 360 intervals | **CONFIRMED** | `generator_start_offset_s = 3.979`, `spread_s = 0.215`, "360 intervals". Bounds [3.974, 4.189] | same |
| 4.9 | Both alignments measured, not bounded | yes | **CONFIRMED** | `method: "measured"`, `uncertainty_s: 0.0` in both | same |
| 4.10 | Fault injected at | 60 s, C=100 | **CONFIRMED** | `requested_at_s = 60`; actual `wall_offset_s = 60.005` (recover) / `60.003` (dead); profile `chaos.concurrency = 100` | → `fault`; `manifest.json` → `profile.chaos`. Note: on the *generator's* clock the fault lands at `generator_elapsed_s` 55.609 / 56.024 — the harness clock is the one the 60 s refers to, and both figures plot on it |
| 4.11 | Recovery threshold and hold | 80% of pre-fault, held 10 s | **CONFIRMED** | `recovery_threshold = 0.8`, `recovery_hold_s = 10.0` | → `performance_rto`; `profiles/thesis-extended.yaml` `chaos:` |
| 4.12 | Chaos pre-flight, each run | 3 of 3 | **CONFIRMED** | Both runs: 3 checks, 3 passed, `ok = true` (2× `clock_offset`, 1× `leaseholder_placement`) | `runs/…003646Z…/preflight.json`, `runs/…004024Z…/preflight.json` |

### The C=50 "failed to recover" run

Run `20260902T022406Z_p4-chaos-dead` (Deployment 1).

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 5.1 | Pre-fault baseline | 1,766.52 ops/s | **CONFIRMED** | `baseline_tps = 1766.52` | `runs/20260902T022406Z_p4-chaos-dead/events.json`. This is the value recorded *at measurement time*, on the harness clock |
| 5.2 | Recovery floor | 1,413.22 vs analysis layer's 1395 | **BOTH CORRECT — different frames** | see below | see below |
| 5.3 | Settled throughput after fault | 1,195 ops/s | **CONFIRMED** | `post_fault_state.mean_tps = 1194.8` (sd 71.3, max 1314.9, CV 0.0597, 15 intervals, `settled: true`) | `crdblab analyze resilience 20260902T022406Z_p4-chaos-dead --json` → `performance_rto.post_fault_state` |
| 5.4 | That as a fraction of pre-fault | 67.6% | **CONTRADICTED** | The analysis layer reports **`fraction_of_baseline = 0.6663`** → **66.63%** (1194.8 / 1793.25 = 0.6662763139551094). 67.6% is 1194.8 / 1766.52 = 0.6763580372710187 — the analysis layer's settled mean divided by `events.json`'s measurement-time baseline. Two different baselines | `resilience --json` → `post_fault_state.fraction_of_baseline`. `resilience.py:487-488` takes the baseline as the mean of the last 20 pre-fault ticks with `fault_at = upper` bound (15.005 s), giving 1793.25 |
| 5.5 | Recorded `rto_s` | `None` | **CONFIRMED** | `events.json`: `"rto_s": null`, `"t_recovered_offset_s": null`. Analysis: `performance_rto.defined = false`, `rto_s = null`, `classification = "degraded_steady_state"` | `events.json`; `resilience --json` |
| 5.6 | Concurrency, profile, schema version | C=50, `smoke`, schema 2.0 | **CONFIRMED** | `profile.name = "smoke"`, `profile.chaos.concurrency = 50`, `schema_version = "2.0"`. Generator command carries `--concurrency=50` | `runs/20260902T022406Z_p4-chaos-dead/manifest.json`. Note this run's `chaos.inject_at_s = 15` and `duration_s = 45`, **not** the 60 s / 180 s of claim 4.10 |
| 5.7 | Clock alignment for this run | bounded, not measured | **CONFIRMED** | `method: "bounded"`, `generator_start_offset_s: null`, `lower_s = 0.0`, `upper_s = 5.046`, `uncertainty_s = 5.046` | `resilience --json` → `clock_alignment` |

**5.2 — which is correct, and why they differ.** Both are correct; they are 80%
of two different baselines, computed over two different windows.

| Value | = 0.8 × | Window | Where |
|---|---|---|---|
| **1413.22** | 1766.52 | harness's own pre-fault window at measurement time | `events.json` → `recovery_floor_tps` |
| **1395.26** | 1744.08 | last 20 ticks before `fault_at_s = 9.959` (generator clock, **lower** bound) | `resilience --json` → `performance_rto.recomputed[0].floor_tps` |
| **1434.60** | 1793.25 | last 20 ticks before `fault_at_s = 15.005` (generator clock, **upper** bound) | `performance_rto.recomputed[1].floor_tps` |

The run's clock alignment is *bounded*, not measured, so `resilience.performance`
re-runs the recovery search at both ends of the interval
(`resilience.py:369-389`) and produces two floors. The verdict string is built at
`resilience.py:425` from `results[0]["floor_tps"]` — **unconditionally the first
element**, i.e. always the lower fault time — which is why it says 1395.

So: the analysis layer's 1395 ops/s is the floor under the earliest fault
position consistent with the unmeasured clock offset; the 1413.22 in `events.json`
is the floor the harness actually used at measurement time. Neither is wrong, and
the discrepancy is a direct consequence of defect D10 (the unrelated clocks) on a
schema-2.0 run. The safe statement is the one the analysis layer's own
`classification` makes: the cluster settled below **every** candidate floor
(1194.8 < 1395.26 < 1413.22 < 1434.60), so the verdict does not depend on the
choice. **Quoting either number alone without the bound is the misleading form**;
the 1413.22 has the additional problem that it is not reproducible from the
analysis layer.

### Configuration and provenance

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 6.1 | Workload parameters, both phases | ycsb / CUSTOM / read 0.8 / update 0.2 / uniform / seed 42 / insert_count 125000 / 60 s / 5 s warmup | **CONFIRMED** | `generator: ycsb`, `ycsb_workload: CUSTOM`, `read_freq: 0.8`, `update_freq: 0.2`, `request_distribution: uniform`, `seed: 42`, `insert_count: 125000`, `duration_s: 60`, `warmup_s: 5` | `profiles/thesis-extended.yaml`, copied verbatim into `runs/20260902T233336Z_p2_baseline/manifest.json` and `runs/20260903T000438Z_p3_cluster/manifest.json` → `profile.workload`. Both manifests' `generator_command` carries the same flags |
| 6.2 | Tiers and repetitions | 1, 2, 5, 10, 50, 100, 200; 3 reps | **CONFIRMED** | `concurrencies: [1,2,5,10,50,100,200]`, `repetitions: 3`, `randomise_tier_order: true`, `cooldown_s: 15`. Realised order identical in both manifests' `tier order:` note (21 tiers) | same |
| 6.3 | CockroachDB version, Phases II–IV | v26.3.0 | **CONFIRMED** | `cockroach_version: "v26.3.0"` in all 19 p2/p3/p4 manifests | every `runs/*/manifest.json` |
| 6.4 | Phase I records no version — and why | `p1_network` never calls `capture_server_config` | **CONFIRMED** | `cockroach_version: null` in all three `*_p1-network` manifests. `grep -rn capture_server_config crdblab/` → defined at `core/preflight.py:108`, called only at `phases/bench.py:365` and `phases/p4_chaos.py:395`. `phases/p1_network.py` imports only `quorum_floor_ms` from `preflight` (line 294) | `runs/*_p1-network/manifest.json`; `grep` above |
| 6.5 | Baseline host CPU model and MemTotal | Intel Xeon @ 2.80 GHz; 4,007,012 kB | **CONFIRMED (for Deployment B only)** | `cpus=2 mem_total_kb=4007012 cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz` | `runs/20260902T233336Z_p2_baseline/manifest.json` → `notes`, the `host:` entry. **This is the only baseline host in the whole artefact set with a hardware reading** — see Part 3 § 4 |
| 6.6 | Gateway host CPU model and MemTotal | AMD EPYC 7713; 4,005,704 kB | **CONFIRMED** | `cpus=2 mem_total_kb=4005704 cpu_model=AMD EPYC 7713 64-Core Processor` | `runs/20260903T000438Z_p3_cluster/manifest.json` → `notes`. Deployment A's gateway records `4005712` kB (8 kB different), same model |
| 6.7 | Memory flags on every node | `--cache=0.25 --max-sql-memory=0.25` | **CONFIRMED in code; artefact evidence covers 1 node per run** | `terraform/scripts/bootstrap.tftpl:95-96` and `bootstrap-local.tftpl:58-59` both set them. `bootstrap.tftpl` is the single template for all five cluster members. Recorded in the `server:` note of every p2/p3/p4 manifest — but that note is captured for exactly one node (`capture_server_config(target.exec_node)`), so no artefact carries the flags for the four non-gateway members | files above; `runs/*/manifest.json` → `notes` |
| 6.8 | All six instances | 2 vCPU, ~3.8 GiB | **NOT FOUND** | Recorded for **2 of 6** hosts: baseline `cpus=2, 4007012 kB` (3.821 GiB) and gateway `cpus=2, 4005704 kB` (3.820 GiB). Nothing records the CPU count or memory of `linode-2`, `azure-1`, `azure-2` or `gcp-1`. The committed `terraform/terraform.tfvars.example` declares `g6-dedicated-2`, `Standard_B2ls_v2`, `n2-standard-2` (us-central1); `terraform/plan.json` (dated 2026-08-07, before the study) says `e2-medium`; the deployed topology puts gcp-1 in `us-east1`. The real `terraform.tfvars` is not committed | `runs/*/manifest.json` `host:` notes; `terraform/terraform.tfvars.example`; `terraform/plan.json` |
| 6.9 | Working set on disk | 179 MiB vs 205 MB | **NOT FOUND — neither is measured** | Both are declared constants; no artefact anywhere records an on-disk or in-memory size for the loaded table. **205 MB**: `crdblab/config.py:70`, `profiles/thesis.yaml:26`, `docs/defects.md:362`, `terraform/scripts/bootstrap.tftpl:83`, `crdblab/core/preflight.py:115`, `crdblab/analysis/validation.py:256`, `tests/test_analysis.py:281`, `tests/test_workload_parser.py:12`, `instructions.md:329,583`. **179 MiB**: `instructions.md:175`, `crdblab/analysis/validation.py:272`, `:441`. Note `validation.py` uses **both**, 16 lines apart. They are not the same quantity restated: 179 MiB = 187.7 MB, 205 MB = 195.5 MiB. **I decline to adjudicate** — see Part 4 | `grep -rn "205 MB\|179" --exclude-dir=.git --exclude-dir=.venv .` |
| 6.10 | Test suite count | 99 vs 100 | **CONFIRMED — 100 is current; D3 regression tests exist** | `100 tests collected`, `100 passed in 1.16s`. By file: `test_analysis.py` 43, `test_workload_parser.py` 16, `test_preflight.py` 13, `test_chaos.py` 11, `test_validation.py` 9, `test_network_probe.py` 8. The 99 in `docs/defect-resolution.md:7` predates commit `119e244`, which added one test (`test_the_unqueued_ratio_is_computed_before_rounding`); `docs/gaps-resolution.md:479` says 100 and is current. **D3 regression tests: yes, three** — `test_summary_block_is_classified_not_measured` (`tests/test_workload_parser.py:120`, under the header "Defect 3"), `test_summary_rows_never_reach_the_tick_stream` (:127), and `test_timed_ticks_discard_summary_blocks_like_group_ticks_does` (:245, docstring cites D3) | `.venv/bin/python -m pytest --collect-only -q` and `-q`; `git show --stat 119e2448` |

### Utilisation

Underlying column `gateway_cpu_pct` in `metrics.csv`. The CLI exposes no
utilisation view, so these were computed by reading `metrics.csv` with pandas and
de-duplicating to one row per `(concurrency, repetition, elapsed_s)` interval —
the table carries one row *per operation type* per interval, so a naive mean over
rows would double-count. This is the D1 arithmetic the project exists to avoid.

| # | Claim | Claimed | Verdict | Actual value | Provenance |
|---|---|---|---|---|---|
| 7.1 | Phase II gateway CPU across saturated tiers | 76–82% | **CONTRADICTED** | No principled definition of "saturated tiers" yields 76–82%. Per-tier means for tiers ≥90% of peak (C=5,10,50,100,200): **74.564–79.750%**. Per-interval min/max over those tiers: **68.800–81.797%**. Tiers ≥99% of peak (C=10,50): per-tier 74.564–74.697%, per-interval 69.048–79.597%. **76–82% matches only C=200 taken alone** (its three repetition values are 81.503, 75.949, 81.797 → 75.9–81.8) | `runs/20260902T233336Z_p2_baseline/metrics.csv`; tier membership from `steady_state.per_tier`. The claim's source is `docs/gaps-resolution.md:291` |
| 7.2 | Which tiers count as "saturated" for 7.1 | define precisely | **NOT FOUND** | **No definition exists in the code.** `raft_overhead._saturation` (`:85-116`) classifies a *phase* as saturated using `SATURATION_TOLERANCE = 0.05` applied to the gain from the second-highest to the highest concurrency; it reports `peak_concurrency` (50 for Phase II) but attaches no per-tier saturated flag. Candidate definitions and their per-tier-mean CPU ranges: ≥90% of peak → {5,10,50,100,200}, 74.564–79.750%; ≥95% → same set, same range; ≥99% → {10,50}, 74.564–74.697%; peak tier only (C=50) → 74.564% | `crdblab/analysis/raft_overhead.py:60-116`; computation as 7.1 |
| 7.3 | Phase II gateway CPU full range | 40.9–81.8% | **CONFIRMED** | **40.850–81.797%** over all 1,155 intervals | as above |
| 7.4 | Phase III gateway CPU full range | 8.8–74.2% | **CONFIRMED** | **8.750–74.206%** over all 1,154 intervals | `runs/20260903T000438Z_p3_cluster/metrics.csv` |
| 7.5 | Phase III gateway CPU at C=1, 2, 5 | give values | **ANSWERED** | Per-tier means: C=1 **11.300%**, C=2 **12.016%**, C=5 **21.965%**. Per repetition — C=1: 15.250, 9.900, 8.750; C=2: 11.898, 12.250, 11.900; C=5: 22.199, 20.246, 23.450 | as 7.4 |
| 7.6 | Non-null intervals, Phase II / III | 1,155 / 1,154, all three columns | **CONFIRMED as a count; materially misleading — see below** | Phase II 2,310 rows / 2 ops = **1,155** intervals; Phase III 2,308 / 2 = **1,154**. `gateway_cpu_pct`, `gateway_disk_iops`, `gateway_rss_bytes` all have **zero** nulls in both. Ranges: Phase II RSS 2.087–2.630 GB, disk 0–14,985 iops; Phase III RSS 2.120–2.541 GB, disk 0–3,215 iops | as above. **But**: each column has exactly **one distinct value per `(concurrency, repetition)`** — 21 distinct values across 1,155 rows. See Part 3 § 8, finding 1 |
| 7.7 | Underlying metric | `sys_cpu_combined_percent_normalized`, host-wide not process-scoped | **CONFIRMED** | `_CPU_METRIC = "sys_cpu_combined_percent_normalized"` at `crdblab/phases/bench.py:60`; scraped from `http://<host>:8080/_status/vars` and multiplied by 100 (`bench.py:135-136`). Host-wide scope stated at `docs/gaps-resolution.md:285`: "It is host-wide, not process-wide. In Phase II the generator runs on the same node as the server, so the figure includes both." And at `:287`: in Phase III it is the gateway only, not a cluster aggregate | files above |

### Defect-record figures (Chapter 5)

**Class 1** = reproducible from a retained validated run.
**Class 2** = contemporaneous diagnostic measurement of a configuration no longer
retained. The legacy exports named in `docs/defects.md`
(`baseline_single_node.csv`, `cluster_cross_cloud_benchmark.csv`,
`raft_overhead_comparison.csv`) are **not present anywhere in the repository**,
and the `chaos-suite/` tree at `793162b2` contains only `.py` files — no CSVs.
Every figure that rests on them is therefore Class 2 by necessity.

| # | Claim | Claimed | Verdict | Class | Actual value | Provenance |
|---|---|---|---|---|---|---|
| 8.1 | Lease-preference defect cost | 12.3x throughput, 110x read latency | **CONFIRMED** | **Class 2** | "34.8 ops/s at a read median of 209.7 ms … 428.0 ops/s at a read median of 1.9 ms, a factor of 12.3 in throughput and 110 in read latency." No run directory holds the misconfigured cluster; `leaseholder_placement` passes in every retained run | `docs/defects.md:205-210` (D7) |
| 8.2 | Seed-mismatch overstatement | ~20x throughput, ~25x latency | **CONFIRMED** | **Class 2** | Table gives update throughput 2,809.7 → 135.3 ops/s and update p50 3.1 → 75.5 ms; prose states "a factor of twenty … a factor of twenty-five". No retained run has a seed mismatch — `row_match` passes at ≥0.99 in every one | `docs/defects.md:296-304` (D8) |
| 8.3 | Cache-asymmetry revision | 18.3x → 12.8x | **CONFIRMED as stated in code** | **Class 2** | `raft_overhead.py:41-43`: "inflated the apparent write-latency overhead from 12.8x to 18.3x". `validation.py:260`: "from 18.3x to 12.8x, a 43% revision". D9 itself gives the affected pair as 3.90 ms vs 71.30 ms at C=10 (= 18.28x) and explicitly says the corrected amount "was not determined, because the runs were discarded rather than corrected". **The 12.8x has no artefact behind it in this repository** | `crdblab/analysis/raft_overhead.py:41-43`, `crdblab/analysis/validation.py:260`, `docs/defects.md:363-370` |
| 8.4 | Baseline shift across redeployment | 22% | **CONFIRMED** | **Class 1** | 3,505 → 2,720 ops/s at C=10, smoke profile, 15 s tiers. Recomputed: **3505.358 → 2719.842**, drop **22.409009293772563%**. Both runs retained and load cleanly | `crdblab.analysis.steady_state.per_tier` on `runs/20260902T021525Z_p2_baseline` and `runs/20260902T152712Z_p2_baseline`; narrative at `docs/defects.md:456-463` |
| 8.5 | Within-sweep drift | −0.4% across 21 tiers, r = −0.12 | **CONFIRMED — reproduced exactly** | **Class 1** | Normalising each tier's throughput by its across-repetition mean and regressing on position in the recorded randomised order: slope **−0.019715%/tier**, **−0.3943%** across 21 tiers, **r = −0.1232**. (`docs/defects.md:477` states −0.02%/tier, −0.4%, r = −0.12.) The comparison `thesis` sweep `20260902T161418Z_p2_baseline` gives −0.2247%/tier, r = −0.4781 against the document's −0.23% and −0.48 | Recomputed on `runs/20260902T175621Z_p2_baseline` from `steady_state.per_repetition` + the `tier order:` note in its manifest, with `numpy.polyfit`. **But see Part 3 § 8, finding 2 — it does not hold for Deployment B** |
| 8.6 | Flushed windows in reported Phase III | 1 of 21 corroborated, 1 partial-window fallback, 19 clean | **CONTRADICTED — wrong run** | **Class 1** | The **reported** Phase III `20260903T000438Z_p3_cluster` has **21 of 21 `window: "interval"` — no flush at all**. The 19 / 1 / 1 profile belongs to **Deployment A's** `20260902T195644Z_p3_cluster`: 19 `interval`, 1 `"post-flush partial"`, 1 `"flushed; corroborated by quorum floor"` | `runs/20260903T000438Z_p3_cluster/preflight.json` and `runs/20260902T195644Z_p3_cluster/preflight.json`, `checks[name=row_match][].window` |
| 8.7 | Summary-row inflation (D3) | ~1,500 ops/s per affected tier | **CONFIRMED** | **Class 2** | "Each affected tier's mean throughput was inflated by approximately 1,500 ops/sec." Signature: 3 rows in `baseline_single_node.csv` and 2 in `cluster_cross_cloud_benchmark.csv` at 119,508–197,680 ops/sec. Neither file exists in this repository | `docs/defects.md:83-85` |
| 8.8 | Chaos clock defect (D4) | 60 s intended, fired at 34.5 s | **CONFIRMED** | **Class 2** | "With `CHAOS_TRIGGER_SEC = 60`, the retained event timelines give `T_fault_injected − T_start` of 34.5 s (dead) and 43.6 s (recover)." The legacy event timelines are not in this repository; every retained `events.json` shows `at_offset_s` within 5 ms of the requested time | `docs/defects.md:118-122` |
| 8.9 | Total defect count | 12, D11a a sub-item not a thirteenth | **CONFIRMED** | — | `docs/defects.md` has twelve `##` defect headings, D1–D12. D11a is a `###` heading (`:565`, "the check fired on the first pair it was applied to") nested under `## D11` (`:454`), before `## D12` (`:595`). `docs/defect-resolution.md:7` says "All twelve are closed" | `grep -n "^##" docs/defects.md` |
| 8.10 | Deployments run in total | 3 | **CONFIRMED** | **Class 1** | Three disjoint overlay address sets across 22 run directories: `100.103.70.41`/`100.97.1.104`, `100.70.55.65`/`100.125.217.116`, `100.96.175.102`/`100.70.90.51` | `--listen-addr` extracted from the `server:` note of every `runs/*/manifest.json`. See Part 3 § 7 |

---

## Part 2 — Figure captions

Caption text below is the **exact** string rendered in the figure, recovered by
decompressing each PDF's content stream and decoding the embedded font
`/Differences` encodings. `{fl}` marks the `fl` ligature glyph (rendered as
"fl"); "ﬀ" is the `ff` ligature in "offered"/"offset". Widths are read from the
PNG IHDR chunk.

| File | Width × height (px) | PDF MediaBox (pt) |
|---|---|---|
| `fig1_network_matrix.png` | **3979 × 3312** | 411.59 × 342.54 |
| `fig2_throughput_sweep.png` | **3992 × 2930** | 377.28 × 276.90 |
| `fig3_latency_by_operation.png` | **3975 × 2568** | 421.57 × 272.31 |
| `fig4_raft_overhead.png` | **3983 × 3066** | 401.56 × 309.24 |
| `fig5_resilience_timeline.png` | **3977 × 2764** | 416.16 × 289.26 |
| `fig6_resilience_timeline_recover.png` | **3977 × 2764** | 416.16 × 289.26 |

`figures.py:83` declares `EXPORT_WIDTH_PX = 3840` as a **minimum**, and derives
the DPI from the tight bounding box before saving (`:155-159`). Because
`savefig(bbox_inches="tight")` recomputes that box at the new DPI, every export
overshoots by 3.5–4.0%. All six clear 3840; none is exactly 3840. If the
dissertation states "exported at 3840 px", that is the declared constant, not the
delivered width.

### fig1_network_matrix

- **Title:** `Inter-node round-trip time`
- **Subtitle (second title line):** `quorum floor 67.1 ms: no committed write can be faster`
- **Axis labels:** `destination` (x), `source` (y); colourbar `mean RTT (ms)`
- **Cell values rendered:** row-major, `-` on the diagonal — 79, 222, 198, 227 / 79, 199, 200, 155 / 222, 199, 18, 73 / 198, 200, 18, 67 / 227, 154, 72, 67
- **Footer:** `source: 20260902T233208Z_p1-network`

### fig2_throughput_sweep

- **Title:** `Steady-state throughput by concurrency` (no subtitle)
- **Axis labels:** `offered concurrency (workers)`, `throughput (ops/s), summed across operation types`
- **Legend:** `p2_baseline`, `p3_cluster`
- **Footer:** `source: 20260902T233336Z_p2_baseline  20260903T000438Z_p3_cluster`

### fig3_latency_by_operation

- **Suptitle:** `Latency by operation type (never pooled across types)` (no subtitle)
- **Panel titles:** `read`, `update`; legend `p50`, `p99`
- **Footer:** `source: 20260903T000438Z_p3_cluster`

### fig4_raft_overhead

- **Title:** `Cost of Raft replication, as a throughput-latency curve`
- **Subtitle:** `points at equal concurrency are NOT at equal load`
- **In-plot annotations:** `C=1`, `C=50`, `C=200` (phase II); `C=1`, `C=100`, `C=200` (phase III); band caption `comparable at matched throughput (1707-1850 ops/s)`; threshold label `quorum floor 67 ms`
- **Legend:** `phase II single node`, `phase III cluster`
- **Footer:** `source: 20260902T233336Z_p2_baseline  20260903T000438Z_p3_cluster`

### fig5_resilience_timeline

- **Title:** `Throughput through a dead fault on linode-2`
- **Subtitle:** `clock offset measured; fault located exactly`
- **Axis labels:** `time since run start (harness clock, s)`, `throughput (ops/s)`
- **Legend:** `throughput`, `fault (dead)`, `performance RTO 12.0 s`; threshold label `recovery floor 1513 ops/s`
- **Footer:** `source: 20260903T004024Z_p4-chaos-dead`

### fig6_resilience_timeline_recover

- **Title:** `Throughput through a recover fault on linode-2`
- **Subtitle:** `clock offset measured; fault located exactly`
- **Axis labels:** as fig5
- **Legend:** `throughput`, `fault (recover)`, `performance RTO 4.4 s`; threshold label `recovery floor 1589 ops/s`
- **Footer:** `source: 20260903T003646Z_p4-chaos-recover`

### Specifically flagged

**Any caption stating the quorum floor as 66.9 ms.** **None.** `fig1` reads
`quorum floor 67.1 ms`; `fig4` reads `quorum floor 67 ms` (its `{:.0f}` format,
`figures.py:416`). No other figure states a floor. The risk the task names is
real but currently lives in prose, not captions: `66.9` appears at
`docs/defects.md:513` (Deployment A's floor in the A/B table) and `:598`
(D12's narrative, a Deployment A sweep). `docs/gaps-resolution.md:171-178`
already records the collision — 66.900 ms is Deployment B's *median* RTT on that
link (`network.csv` `rtt_p50_ms`) **and** 66.925 ms is Deployment A's floor, so
"66.9" is simultaneously the right answer to a different question and the right
answer for a different deployment. As this deployment's floor it is wrong twice
over. The correct value is **67.054 ms** (67.1 to one decimal).

**`fig6_resilience_timeline_recover`.** **It exists** (PNG + PDF, mtime
2026-09-04 03:30, later than the other five at 2026-09-03 06:16). It sources
**`20260903T003646Z_p4-chaos-recover`** per its footer, and it draws the fault as
a **line**, not a band — its legend entry is `fault (recover)`, which
`figures.py:473-477` emits only on the `alignment.exact` branch; the band branch
(`:478-483`) would have produced `fault, located to within N s`. That run's
alignment is `measured`, so the line is correct.

**However, `fig6` cannot be produced by the CLI.** `figures.render_all`
(`figures.py:516-537`) calls `resilience_timeline` exactly once, and that function
hard-codes its output filename as `fig5_resilience_timeline.png`
(`figures.py:513`). There is no `fig6` path anywhere in the codebase. `fig6` must
have been produced by a direct call to `figures.resilience_timeline` with a
different `out_dir`, or by renaming a `fig5` render. Its content is consistent
with the run it names, but its *provenance* is not reproducible by
`crdblab report figures`, which is the guarantee `figures.py:1-8` claims for every
figure in the directory.

**`fig5`.** **Yes on both counts.** It sources `20260903T004024Z_p4-chaos-dead`
and draws the fault as a **line** (`fault (dead)`). Note that this required an
explicit `--chaos` argument: `cli.py:519` defaults the chaos pick to
`_latest_run(runs, "p4-chaos-recover")`, which would have selected the *recover*
run. So neither Phase IV figure in `figures/` is what the default invocation
produces.

---

## Part 3 — Open questions

### 1. Rounding defects — does the D3/D4/D5 pattern survive in the analysis layer?

**No, it does not remain in the specific place the project fixed — but it
survives in four others, and one of them changes a number the dissertation
quotes.**

There are 67 `round()` calls across `crdblab/analysis/`. Most are terminal:
their output goes into a dict for display or serialisation and is never read
back. The following are the exceptions — a `round()` whose output feeds a further
arithmetic operation.

**(a) `crdblab/analysis/raft_overhead.py:520-523` — live, and it changes a
reported figure.**

```python
"phase_ii_tps":  round(float(a.loc[concurrency, "mean_total_tps"]), 1),
"phase_iii_tps": round(float(b.loc[concurrency, "mean_total_tps"]), 1),
...
row["throughput_ratio_x"] = round(row["phase_ii_tps"] / row["phase_iii_tps"], 2)
```

The two throughputs are rounded to one decimal **for display**, and the ratio is
then computed from the rounded values. Effect on the reported pair:

| C | exact ratio | from rounded inputs | reported | correct |
|---|---|---|---|---|
| **1** | 25.252052 | 25.255917 | **25.26** | **25.25** |
| **2** | 22.428743 | 22.421642 | **22.42** | **22.43** |
| 5 | 10.537429 | 10.536101 | 10.54 | 10.54 |
| 10, 50, 100, 200 | — | — | agree | agree |

This is the exact pattern commit `119e244` fixed twelve lines further up in the
same file, for `ratio_x`. It was not applied to the family. Claim 3.2 quotes the
defective value.

**(b) `raft_overhead.py:359-366` — live, affects the matched-utilisation table.**

```python
levels = sorted({round(float(t) / peak_a, 3) for t in a["mean_total_tps"]} | {...})
...
ta, tb = u * peak_a, u * peak_b
```

Each utilisation level is rounded to three decimals and the rounded value is then
multiplied back by the peak to obtain the throughput at which the curve is
interpolated. This is why the output reports `phase_ii_tps: 3003.9` at
`utilisation: 0.843` when the C=2 tier actually measured 3004.532 ops/s: the
level 3004.532/3563.335 = 0.8432… was rounded to 0.843 and multiplied back to
3003.89. The consequence is that a point that ought to be *measured* on the
Phase II side is instead *interpolated* — a 0.6 ops/s displacement here, but the
mechanism is unbounded in principle. Claim 3.8's "67.31x at 84%" comes from this
path.

**(c) `raft_overhead.py:437-458` — live, negligible magnitude.** `weighted` and
`tps` are read out of `per_tier()`, which rounds both to 3 dp
(`steady_state.py:64-71`, `:134-138`); `implied = lightest / tps * 1000.0` and
`littles_law_agreement` are then computed from them. The rounding is at 3 dp
against values of order 10³, so the effect is below the reported precision — but
it is the pattern.

**(d) `resilience.py:370` and `:425-427` — live, negligible magnitude.**
`fault_points = sorted({round(lower, 3), round(upper, 3)})` produces rounded
fault times which are then used as `fault_at` in `rto_s = round(recovered −
fault_at, 3)`. And `floor = results[0]["floor_tps"]`, itself `round(…, 2)`, is
compared against `settled["mean_tps"]`, itself `round(…, 1)`, to choose the
verdict branch. Both at 3 dp / 2 dp against values where the decision margin is
hundreds of ops/s.

**Not defects.** `raft_overhead.py:528` (`update_p50_ratio_x`) divides unrounded
medians straight out of `latency_by_op`; `resilience.py:563` (`floor_ratio_x`)
divides unrounded floors; `steady_state.py:111` divides unrounded per-repetition
means; `validation.py:179-181` rounds only into the finding's payload.

**Recommendation:** (a) is the one that matters. It should be fixed the same way
`ratio_x` was — divide the unrounded values, round the quotient — and claim 3.2
corrected to 25.25x. Until then, no `throughput_ratio_x` in the
`same_concurrency_delta` table should be quoted to two decimals.

### 2. Provenance drift

**The two revisions:**

- Recorded in every run manifest: **`793162b20ba125dd128c2cdd9f5d53156a2d0075`**, "currently working testbed, happy testing, will now switch to a cli app instead of random python files", 2026-09-01T23:24:25+05:30.
- Inspected here: **`119e2448a839f4a2e746afc46b83ea4b687cdf76`**, 2026-09-04T03:36:52+05:30. Intermediate commit: `a63b930b9c433bfd2252f28005f7efe01c2b0ddc`, 2026-09-03T07:45:05+05:30.

**The `raft_overhead.py` fix does postdate the runs, and no measurement changed —
only a derived ratio. Confirmed.** The fix is the whole of `raft_overhead.py`'s
diff between `a63b930` and `119e244` (8 insertions, 1 deletion):

```diff
+            "_p50_exact": float(row["p50_ms"]),
             "p50_ms": round(float(row["p50_ms"]), 3),
...
         out["ratio_x"] = round(
-            out["phase_iii"]["p50_ms"] / out["phase_ii"]["p50_ms"], 2
+            out["phase_iii"]["_p50_exact"] / out["phase_ii"]["_p50_exact"], 2
         )
```

It touches one output key, `ratio_x`, and moves it from 50.37 to 50.38. It reads
`metrics.csv`; it does not write it. Every measured column — `tps`, `p50_ms`,
`p95_ms`, `p99_ms`, `pmax_ms`, `errors_cum`, `elapsed_s`, `wall_offset_s` — was
written by `phases/bench.py` at measurement time and is byte-identical before and
after. Re-running `analyze steady-state` at either revision returns the same
tiers. So the claim's substance holds: **a derived ratio changed, no measurement
did.**

**But the framing understates the drift by a wide margin.** The manifests do not
record a revision at which `raft_overhead.py` was merely older — they record a
revision at which **the entire `crdblab` package did not exist**. `git ls-tree -r
793162b -- crdblab tests` returns nothing; that tree holds `chaos-suite/`
(the legacy scripts), `terraform/`, and three markdown files. `recorder.py:142`
captures `git rev-parse HEAD`, which reports the last *commit*, and the harness
was uncommitted working-tree state throughout the study. **No run in `runs/` can
be tied to the code that produced it by its recorded revision.** A dissertation
stating that each run records the revision of the harness that produced it would
be wrong; the accurate statement is that each run records the revision the
repository was *last committed at*, which is not the same thing and in this case
is not close.

### 3. Audit cadence

Computed from `audit.csv` (`wall_offset_s`, `seq_id`, `outcome`), differencing
consecutive acknowledged writes. Fault position from `events.json`
`injected.at_offset_s`: 60.005 s (recover), 60.003 s (dead). All 445 and 385
attempts respectively are `ack`; there are no `ambiguous` or `refused` rows.

| Window | recover — median gap | dead — median gap | dead vs recover |
|---|---|---|---|
| **whole run** | **0.401000 s** (n=444) | **0.466950 s** (n=384) | +16.446% |
| **pre-fault** | **0.393150 s** (n=144) | **0.404750 s** (n=140) | **+2.951%** |
| **post-fault** | **0.403500 s** (n=299) | **0.480200 s** (n=243) | **+19.009%** |

Means and extremes, for completeness: recover whole-run mean 0.412030 s
[0.2954, 0.6424]; dead whole-run mean 0.476859 s [0.3049, 0.7109].

**The dissertation's claim holds.** The two runs' pre-fault cadences agree to
**2.951%**, inside the claimed 3%, and they diverge only after the fault, to
**19.0%**. The whole-run medians (0.401 / 0.467) are exactly the `resolution_s`
values the resilience analysis reports for each run, which cross-checks the
derivation.

Two things worth stating alongside it. First, the divergence is in the expected
direction and magnitude: losing `linode-2` raises the write floor from 67.1 ms to
198.2 ms (claim 1.14), and the `dead` fault — which the cluster never fully
recovers from within the audit window in the same way — leaves the audit client
paying the longer quorum for the rest of the run. Second, both pre-fault medians
(~0.4 s) are twenty times the profile's nominal `audit_interval_s = 0.02`, which
is exactly the point the analysis layer's `sampling_note` makes: the cadence is
bounded by the cost of a quorum write, not by the requested interval.

### 4. Hardware capture coverage

`host:` note present in the manifest — 6 of 22 runs:

| Run | `host:` note |
|---|---|
| `20260902T192117Z_p3_cluster` | `cpus=2 mem_total_kb=4005712 cpu_model=AMD EPYC 7713 64-Core Processor` |
| `20260902T195644Z_p3_cluster` (Dep. A Phase III) | `cpus=2 mem_total_kb=4005712 cpu_model=AMD EPYC 7713 64-Core Processor` |
| `20260902T233336Z_p2_baseline` (Dep. B Phase II) | `cpus=2 mem_total_kb=4007012 cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz` |
| `20260903T000438Z_p3_cluster` (Dep. B Phase III) | `cpus=2 mem_total_kb=4005704 cpu_model=AMD EPYC 7713 64-Core Processor` |
| `20260903T003646Z_p4-chaos-recover` | `cpus=2 mem_total_kb=4005704 cpu_model=AMD EPYC 7713 64-Core Processor` |
| `20260903T004024Z_p4-chaos-dead` | `cpus=2 mem_total_kb=4005704 cpu_model=AMD EPYC 7713 64-Core Processor` |

**No `host:` note — 16 of 22 runs:** all three `*_p1-network` runs (Phase I never
calls `capture_server_config`, claim 6.4), and `…021525Z_p2_baseline`,
`…021648Z_p3_cluster`, `…022406Z_p4-chaos-dead`, `…024023Z_p4-chaos-recover`,
`…152535Z_p1-network`, `…152712Z_p2_baseline`, `…152848Z_p3_cluster`,
`…153058Z_p4-chaos-recover`, `…154940Z_p2_baseline`, `…161418Z_p2_baseline`,
`…163721Z_p3_cluster`, `…165959Z_p4-chaos-recover`, `…170444Z_p4-chaos-dead`,
**`…175621Z_p2_baseline`**.

**Deployment A's Phase II `20260902T175621Z_p2_baseline` has none. Confirmed.**
`grep '"host: ' runs/20260902T175621Z_p2_baseline/manifest.json` returns nothing;
its `notes` array contains only the target line, the tier order, and the `server:`
line.

**This has a consequence the defect record does not survive.** `docs/defects.md`
at D11 (`:515-518`) states:

> "…and the hardware capture reports an *identical* `cpu_model` (Intel Xeon @
> 2.80 GHz) and an identical `MemTotal` (4,007,012 kB) on both baseline hosts.
> The instances are not merely the same type; they are indistinguishable in every
> field the artefact records."

**There is only one baseline host reading in the entire artefact set.**
Deployment B's, above. Deployment A's Phase II has no hardware capture, so there
is no counterpart to compare and the sentence "identical … on both baseline
hosts" is not supported by anything in `runs/`. The comparability check knows
this and says so: comparing Deployment A's pair emits

> `host hardware is unrecorded for p2_baseline, so the two runs cannot be shown
> to have run on comparable machines; this is the condition under which the
> unexplained 22% Phase II baseline shift of 2026-09-02 became undiagnosable
> after the fact`

D11's own conclusion — that the between-deployment variance is in delivered CPU
rather than in provisioned hardware — therefore rests on an unrecorded
measurement, which is precisely the failure mode D11 exists to document.

### 5. `--accept-hardware-difference`

**Confirmed: it downgrades the refusal to a recorded warning rather than
suppressing it — but the warning does not appear in the default output.**

Without the flag, the comparison **refuses**:

```
$ .venv/bin/python -m crdblab analyze raft-overhead \
    --baseline 20260902T233336Z_p2_baseline --cluster 20260903T000438Z_p3_cluster
refusing to compare: p2_baseline and p3_cluster were measured on different hardware
(cpu_model: 'Intel(R) Xeon(R) CPU @ 2.80GHz' vs 'AMD EPYC 7713 64-Core Processor');
a throughput difference between them is not attributable to the variable under study.
If this difference is a known limitation of the study rather than a mistake, say so
explicitly rather than comparing anyway

These two runs differ in more than replication, so their difference is not
replication cost (see docs/defects.md, D9).
```

(exit 1, `raft_overhead.NotComparable` at `:564-567`.)

With the flag, `comparability.ok` becomes `true` and the finding is retained at
`severity: "warning"` with `accepted: true`. **Warning text emitted for the
reported pair, verbatim:**

> `p2_baseline and p3_cluster were measured on different hardware (cpu_model: 'Intel(R) Xeon(R) CPU @ 2.80GHz' vs 'AMD EPYC 7713 64-Core Processor'); this was explicitly accepted by the caller and the comparison proceeds. Latency ratios on a path bounded by network round trips are the least affected; absolute throughput and any CPU-bound quantity are the most`

with

```json
"differing_hardware": {"cpu_model": ["Intel(R) Xeon(R) CPU @ 2.80GHz",
                                     "AMD EPYC 7713 64-Core Processor"]},
"accepted": true
```

**The qualification.** The warning is recorded in the `--json` payload under
`comparability.findings`. It is **not printed in the default text output**:
`cli.py:359-445` renders `curves`, `saturation`, `matched_throughput`,
`matched_utilisation`, `lightest_load_write_latency` and
`same_concurrency_delta`, and never touches `result["comparability"]`.
`grep -ci "hardware\|warning\|accepted"` on the captured text output returns 0.
So "appears in the analysis output" is true of the machine-readable output only.
Anyone reading, pasting or screenshotting the human-readable output of the
reported comparison sees no indication that a hardware difference was accepted —
which is the same class of silence D9 was about.

### 6. Idempotency

**Confirmed: no apply wall-time, re-apply result, drift detection or teardown
timing exists anywhere in the repository or the artefacts.**

- The manifest schema (`crdblab/core/recorder.py`) has no provisioning field. Its keys are `run_id`, `phase`, `schema_version`, `started_utc`, `finished_utc`, `git_revision`, `profile`, `topology`, `clock_epoch_utc`, `cockroach_version`, `generator_command`, `ssh_options`, `client_platform`, `notes`, `generator_totals`, `validation`. `started_utc`/`finished_utc` bracket the *measurement phase*, not any provisioning step.
- `grep -rl "apply\|teardown\|destroy\|idempot\|drift" runs/` returns nothing.
- No Terraform state, plan output or apply log is retained. `terraform/` contains `main.tf`, `providers.tf`, `variables.tf`, `modules/`, `scripts/`, `terraform.tfvars.example` and `plan.json` — and `plan.json` is dated 2026-08-07, roughly a month before the study, so it is not an artefact of any of the three deployments.
- The project already records this. `docs/research-gaps.md:191`: "No provisioning time, re-apply result, idempotency check, drift detection or teardown time was recorded anywhere. The only evidence bearing on RQ1 is that the whole protocol was executed twice across a full teardown and redeployment." `docs/gaps-resolution.md:411-417` says the same and recommends splitting RQ1 into reproducibility (evidenced) and idempotency (not).

Any claim of idempotency in the strict sense — a repeated apply producing no
changes — is unsupported. Reproducibility across teardown *is* evidenced, by the
three deployments.

### 7. Overlay addresses

**Three deployments with disjoint address sets: confirmed for the addresses that
were recorded, which is two nodes per deployment, not six.**

The only addresses in any artefact are the `--listen-addr` / `--advertise-addr`
in each manifest's `server:` note, captured by `capture_server_config` for the
single node the phase targeted (`bench.py:365` passes `target.exec_node`).

| | Deployment 1 | Deployment A (2) | Deployment B (3) |
|---|---|---|---|
| **Baseline node** (`crdb-local-1`) | **`100.103.70.41`** | **`100.70.55.65`** | **`100.96.175.102`** |
| Gateway (`crdb-linode-1`) | `100.97.1.104` | `100.125.217.116` | `100.70.90.51` |

Six addresses, all six distinct; no address recurs across deployments; the sets
are pairwise disjoint. Within a deployment the address is stable across every run
of that deployment (e.g. `100.125.217.116` appears in `…152848Z_p3_cluster`,
`…153058Z_p4-chaos-recover`, `…163721Z_p3_cluster`, `…165959Z_p4-chaos-recover`,
`…170444Z_p4-chaos-dead`, `…192117Z_p3_cluster`, `…195644Z_p3_cluster`).

**Baseline node's address in each deployment** — the row asked for — is the first
row above: `100.103.70.41`, `100.70.55.65`, `100.96.175.102`.

**Limit.** The other four cluster members (`linode-2`, `azure-1`, `azure-2`,
`gcp-1`) have no recorded address in any deployment; the `--join=` list is by
MagicDNS hostname (`crdb-linode-1,crdb-linode-2,…`), which is stable across
redeployments by design. A claim that all *fifteen* addresses across three
deployments were disjoint cannot be checked. What can be said is that the two
nodes that matter for the comparison — the baseline host and the gateway — were
different machines on the overlay in all three.

### 8. Anything else

Ordered by how badly a dissertation drawing on these artefacts would be misled.

**1. `gateway_cpu_pct`, `gateway_rss_bytes` and `gateway_disk_iops` are one
sample per tier, not one per interval.** Each column has exactly **one distinct
value per `(concurrency, repetition)` group** in both Phase II and Phase III —
21 groups, 21 distinct values, 1,155 rows. Verified with
`df.groupby(["concurrency","repetition"])[col].nunique()`: min 1, max 1, for all
three columns in both runs.

The cause is in `bench.py::_run_tier`. `HostSampler` scrapes `/_status/vars` once
a second on a background thread and stores the result in `self._sample`. But the
loop that writes rows —

```python
timed = list(group_timed_ticks(arrivals))     # built AFTER the stream closed
for arrived, tick in timed:
    ...
    host = sampler.current                    # bench.py:246
```

— runs **after** the `with ssh.StreamingRemote(...)` block has exited. All 55
intervals of a tier are written in a tight loop in a few milliseconds, so all 55
read the same `self._sample`: whatever the background thread last scraped, at or
just after the moment the generator stopped.

Consequences a write-up must not get wrong: (i) there are **21 host samples per
phase, not 1,155** — claim 7.6's interval count is a cell count, and overstates
the sampling by a factor of 55; (ii) no within-tier CPU variance exists in this
data, so no error bar, standard deviation or time series may be drawn from these
columns; (iii) the sample is taken at tier *end*, not over the steady-state
window, so it is not the mean utilisation during the measurement; (iv) the
per-interval "range" quoted in claims 7.3 and 7.4 (40.9–81.8%, 8.8–74.2%) is in
fact the range across 21 tier-end samples, which is a true statement about a
different quantity. The values are individually plausible, which is exactly the
failure mode this project's second contribution is about.

**2. D11's drift argument does not hold for Deployment B.** `docs/defects.md:531`
argues that "within-sweep drift is −0.4% across 21 tiers, so the variation is
between deployments rather than during one." Reproducing that regression on both
Phase II sweeps:

| Run | slope | across sweep | r |
|---|---|---|---|
| `20260902T175621Z_p2_baseline` (Dep. A) | −0.0197%/tier | **−0.394%** | **−0.1232** |
| `20260902T233336Z_p2_baseline` (Dep. B) | **+0.6409%/tier** | **+12.818%** | **+0.6298** |

Deployment B's baseline sweep drifts **upward by roughly 13% across its 21
tiers**, with a correlation of +0.63 — a real trend, not noise. This is visible
directly in its C=1 repetitions, whose positions in the randomised order are 2,
16 and 18: 1464.34 (early), 1836.20, 1821.25 ops/s. That single fact explains the
±523 ops/s interval at C=1 and the ±812 at C=2 that claim 2.7 reports, and it
means those intervals are measuring a within-sweep trend, not repetition
variance. The claim that "the variation is between deployments rather than during
one" is supported by Deployment A and contradicted by Deployment B, and a
dissertation quoting it as a general finding would be wrong.

**3. Claim 3.2 quotes the table the code labels "NOT A RESULT".** The
`same_concurrency_delta` block is documented at `raft_overhead.py:504-510` and
printed at `cli.py:442` as `"same-concurrency delta -- NOT A RESULT (Chapter 5
error case study only; never as a results table)"`. Claims 3.2, 3.3 and 3.4 all
draw from it. That is legitimate *if* the surrounding prose is the Chapter 5
error case study; it is a serious error if any of them appears in a results
table. The values themselves are fine (modulo the rounding defect in 3.2).

**4. Stale hardware facts in the code that a write-up would inherit.**
`crdblab/topology.py:113-118` states, in a comment addressed to whoever performs
the Raft-overhead comparison: "this host has 7 GB of RAM against the cluster
members' 3 GB". `docs/defects.md:372` says "the baseline had 7.8 GiB against the
cluster members' 3.8 GiB". Both are contradicted by the Deployment B capture:
baseline `4,007,012 kB` (3.821 GiB) against gateway `4,005,704 kB` (3.820 GiB) —
a difference of 1,308 kB, or 0.03%. The instance sizes were normalised at some
point between the D9 diagnosis and Deployment B and neither comment was updated.
A methodology chapter citing `topology.py`'s caveat would assert a 2× memory
asymmetry that the reported runs do not have.

**5. Stale RTT figures in the code, all Deployment 1 or A values.**
`topology.py:83` gives the triangle as "0, 24.7 and 70.6 ms" and Azure at "191
and 200 ms"; `preflight.py:526-528` gives followers at "24.7, 70.6, 191.3 and
200.5 ms" and the floor as 70.6 ms; `raft_overhead.py:36` gives "~70.6 ms".
Deployment B measured 18.324, 67.054, 198.183, 200.051 and a floor of 67.054 ms.
These docstrings are cited in the analysis layer's own printed caveats and in
`cli.py`'s help text, so they can reach a reader who thinks they are reading the
reported deployment.

**6. `topology.py:88-91` records an unresolved conflict with Chapter 3.** "Chapter
3 of the dissertation describes the Azure and GCP members as uk-south, eu-west
and us-central. The deployed testbed is centralindia, eastasia and us-east1,
which changes the WAN latency argument materially. The prose, not this file, is
what needs correcting (Stage 7)." As of `119e2448` the note is still there. The
manifests confirm the deployed localities: `cloud=azure,region=centralindia`,
`cloud=azure,region=eastasia`, `cloud=gcp,region=us-east1`. Separately,
`terraform/terraform.tfvars.example` declares the GCP node in `us-central1` /
`us-central1-a`, a third answer.

**7. `raft_overhead.matched_utilisation` line 346 is dead code.**
`hi = min(1.0, 1.0)` — always 1.0. Whatever the second bound was meant to be
(each phase's own attainable maximum, presumably), it was lost. Harmless as
written, because 1.0 is the correct ceiling when both phases saturated, but it
would silently do the wrong thing for a phase that had not.

**8. `raft_overhead.py:15-21` cites numbers from a discarded deployment as
"the measured data".** "At C=10 the cluster's read median is 0.93 ms against the
single node's 1.97 ms… The single node is carrying 3,505 ops/s at that worker
count and the cluster 647." The 3,505 figure is Deployment 1's smoke run (claim
8.4's "before" value). In Deployment B the same tiers are 3,550.6 and 633.8. The
*qualitative* point survives — Deployment B's C=10 read ratio is 0.49, so the
cluster does still read "faster" — but the figures do not.

**9. `crdblab/analysis/validation.py` uses two different working-set sizes.**
205 MB at `:256`, 179 MiB at `:272` and `:441` — same file, same module docstring
region. See claim 6.9.

**10. `20260902T192117Z_p3_cluster` and `20260902T154940Z_p2_baseline` are
retained but unusable**, and the loader correctly refuses both:
`"failed pre-flight and must not be used for figures: no statements against
'usertable' were recorded during the window; the workload may not have run at
all"` — the D12 signature. `…192117Z` is the immediate predecessor of Deployment
A's reported Phase III. Their presence in `runs/` alongside 20 valid runs is a
trap for anyone selecting runs by timestamp rather than through the loader.

**11. Deployment A's two Phase III runs disagree on `mem_total_kb`** with
Deployment B's: `4,005,712` vs `4,005,704` kB, an 8 kB difference on the same
`AMD EPYC 7713`. Below any threshold that matters, but it means the two gateway
hosts are *not* byte-identical in the recorded fields, which a claim of
"indistinguishable in every field the artefact records" would have to accommodate.

**12. Phase IV runs record no host metrics at all.** `gateway_cpu_pct`,
`gateway_disk_iops` and `gateway_rss_bytes` are empty for all 360 intervals of
every chaos run (`p4_chaos.py:541` writes `""`). Any utilisation statement about
the fault window is unavailable.

---

## Part 4 — Summary

### Counts

Part 1 has **90** numbered rows (14 + 16 + 14 + 12 + 7 + 10 + 7 + 10). Every one
is given a verdict; none is omitted.

| Verdict | Count | Rows |
|---|---|---|
| **CONFIRMED** | **81** | 1.1–1.14 (14), 2.1–2.16 (16), 3.1 and 3.3–3.14 (13), 4.1–4.12 (12), 5.1/5.3/5.5/5.6/5.7 (5), 6.1–6.7 and 6.10 (8), 7.3/7.4/7.6/7.7 (4), 8.1–8.5 and 8.7–8.10 (9) |
| **CONTRADICTED** | **4** | 3.2, 5.4, 7.1, 8.6 |
| **NOT FOUND** | **3** | 6.8, 6.9, 7.2 |
| **Answered, not a verification** | **1** | 7.5 (the row asks for values, not for a check) |
| **Split — both figures correct in their own frames** | **1** | 5.2 |
| | **90** | |

Qualified confirmations, restated so they are not read as unqualified:
**1.8** (the value 1280 is right; it is an interface MTU, not a path MTU);
**3.13** (3.6485% rounds to 3.6% but is not bounded by it — "within 3.7%" is the
sound form); **6.7** (true in the templates; artefacts evidence one node per run);
**7.6** (the counts are right; the sampling behind them is 55× coarser than the
count implies); **8.3** (confirmed as a statement in the code, with no artefact
behind the 12.8x); **8.5** (reproduces exactly, but does not generalise — Part 3
§ 8 finding 2).

### CONTRADICTED rows, ordered by how badly they would mislead a reader

**1. Claim 8.6 — flushed windows attributed to the wrong run.** Correct value:
the reported Phase III `20260903T000438Z_p3_cluster` has **21 of 21 clean
`interval` row-match windows; no window was flushed**. The "19 clean / 1
corroborated / 1 partial" profile is **Deployment A's**
`20260902T195644Z_p3_cluster`. This is the worst of the four because it is a
provenance claim about the run every figure is drawn from, and it attributes to
that run a weakness it does not have (one tier's row-match evidence resting on
quorum-floor corroboration rather than a direct count). A reader auditing the
reported run against this claim would find the claim unsupported and reasonably
conclude the whole chain was unreliable. The corrected statement is *stronger*
than the claim, which makes the error gratuitous.

**2. Claim 7.1 — "76–82% gateway CPU across saturated tiers".** Correct value:
**74.564–79.750%** as per-tier means over tiers within 90% of peak
(C=5,10,50,100,200), or **68.800–81.797%** per interval over the same tiers. The
quoted band matches only C=200 in isolation. The claim is load-bearing — it is
what the argument "the baseline is CPU-bound" rests on, per
`docs/gaps-resolution.md:290`. The argument survives the correction (75% is still
high for a 2-vCPU host), but the number as printed is not a range over saturated
tiers. Compounding it: per finding 1 of Part 3 § 8, the underlying column has 21
samples per phase, not 1,155, so *no* range over "intervals" is available at all.

**3. Claim 5.4 — "67.6% of pre-fault".** Correct value: **66.63%**
(`fraction_of_baseline = 0.6663`, unrounded 0.6662763139551094). 67.6% is
1194.8/1766.52 — the analysis layer's settled mean over `events.json`'s
measurement-time baseline, which is a different window on a different clock. It
is a small numerical error but a structural one: it silently mixes the two
timelines whose non-relation is defect D10, in the one run in the study whose
clock alignment is *bounded rather than measured*. Quoting the analysis layer's
own 66.63% avoids the mixture entirely.

**4. Claim 3.2 — "25.26x" equal-concurrency throughput ratio at C=1.** Correct
value: **25.25x** (1707.266 / 67.609 = 25.252052). Caused by a live instance of
the round-then-divide defect at `raft_overhead.py:520-523`. Least misleading of
the four in magnitude — 0.04% — but the most awkward in context: it is the same
defect class the dissertation's second contribution is *about*, in the same file
where the project fixed one instance of it twelve lines earlier, in a number the
dissertation quotes. C=2 is also affected (reported 22.42, correct 22.43).

### What I decline to state from these artefacts

**The working-set size.** Neither 179 MiB nor 205 MB is a measurement. No
artefact in `runs/`, no preflight check, and no manifest field records the loaded
table's size on disk or in memory. Both figures are declared constants in source
comments, they are not the same quantity in different units (179 MiB = 187.7 MB;
205 MB = 195.5 MiB), and `crdblab/analysis/validation.py` asserts both, sixteen
lines apart. I can report that the project is internally inconsistent about it. I
will not pick one, and I will not compute a third from row count times record
size — that is precisely the plausible reconstruction the task forbids, and it
would land on neither published figure.

**The hardware of the four non-gateway cluster members.** Two of six hosts have a
recorded `cpus`/`mem_total_kb`/`cpu_model`. The committed
`terraform.tfvars.example` is an example, `terraform/plan.json` predates the study
by a month and disagrees with it, and the real `terraform.tfvars` is not
committed. "All six instances are 2 vCPU, ~3.8 GiB" may well be true; nothing here
establishes it.

**Any per-interval statement about host utilisation.** Per finding 1 of Part 3
§ 8, `gateway_cpu_pct`, `gateway_rss_bytes` and `gateway_disk_iops` carry one
observation per tier, replicated across that tier's 55 rows. I will not report a
mean, variance, interval, time series or "range across intervals" for these
columns, and neither should the dissertation, beyond "21 tier-end samples per
phase" with the sampling stated.

**A definition of "saturated tiers".** None exists in the code. `_saturation`
classifies a phase, not a tier. Any range quoted "across saturated tiers" needs
the threshold stated with it; I have given the candidates and their values in
row 7.2 rather than choose one and present its range as the answer.

**That any run in `runs/` was produced by the code at its recorded revision.**
The recorded revision predates the existence of the `crdblab` package. The runs
are internally consistent and the analysis layer reproduces the recorded
derivations to within the deltas noted above, which is good evidence that the
code and the data belong together — but it is inference from agreement, not
provenance, and the manifests do not supply provenance.

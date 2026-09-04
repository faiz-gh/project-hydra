# Within-sweep drift in the reported Phase II sweep, and two corrections

Follow-on to `docs/dissertation-verification.md`, taking its Part 3 § 8 finding 2
as the starting point. Deployment map, run naming and "reported" all mean what
they mean there.

**Rule applied throughout:** no value here is estimated, inferred or
reconstructed. `NOT FOUND` is used where an artefact does not carry the
quantity. Values obtained by removing a fitted trend are **diagnostic** and are
labelled as such on every appearance; none of them is a measurement and none may
be quoted as one. Derived values are given unrounded as well as rounded.

---

## 0. Provenance

### Revisions

| | |
|---|---|
| Started from | `119e2448a839f4a2e746afc46b83ea4b687cdf76` (HEAD of `master`), 2026-09-04T03:36:52+05:30 |
| Working tree at start | `docs/resolution.md` deleted (unstaged); `docs/dissertation-verification.md`, `docs/gaps-resolution.md` untracked. No source file modified. |
| Finished at | **`79be60c25a95ddc57f7537b9dbe6d006beb38b13`** on branch `drift-and-corrections` — every code and test change in Parts 3 and 4, and the revision at which every number below was produced. This document is committed on top of it; a commit cannot state its own hash, so `git log --oneline master..drift-and-corrections` is the authority for that one |
| Revision recorded in every run manifest | `793162b20ba125dd128c2cdd9f5d53156a2d0075` — still the pre-`crdblab` tree; unchanged and unchangeable from here |

Nothing under `runs/` was written, moved or touched. `git status --short` lists
no path under `runs/` at any point.

### Analysis layer versus ad-hoc computation

Every tier statistic below comes from the project's own layer —
`crdblab.analysis.steady_state.per_repetition`, `.per_tier`,
`.confidence_interval`, `crdblab.analysis.loader.Run.latency_by_op` — never from
a re-implementation. The CLI exposes no regression view, so the regression
itself is ad-hoc, written as a thin wrapper over those functions and reproduced
in full below so that every number in Parts 1, 2 and 5 can be re-derived.

```python
# scratchpad/drift.py -- the only ad-hoc code used.
import ast
import numpy as np
from crdblab.analysis.loader import load_run
from crdblab.analysis import steady_state as ss

def tier_order(run):
    """Realised (concurrency, repetition) order from the manifest note."""
    for note in run.manifest.get("notes", []) or []:
        if "tier order:" in note:
            return [tuple(t) for t in ast.literal_eval(
                note.split("tier order:", 1)[1].strip())]
    raise KeyError("no tier order note")

def positions(run):                      # 0-based sweep position
    return {k: i for i, k in enumerate(tier_order(run))}

def series(run_id, quantity):
    run = load_run(run_id)
    pos = positions(run)
    if quantity == "throughput":
        d = ss.per_repetition(run)[["concurrency", "repetition", "mean_total_tps"]]
        d = d.rename(columns={"mean_total_tps": "value"})
    elif quantity == "weighted_p50":
        d = ss.per_repetition(run)[["concurrency", "repetition",
                                    "mean_weighted_p50_ms"]]
        d = d.rename(columns={"mean_weighted_p50_ms": "value"})
    else:                                 # e.g. "update:p50_ms"
        op, q = quantity.split(":")
        lat = run.latency_by_op()
        d = lat[lat["op"] == op][["concurrency", "repetition", q]] \
                .rename(columns={q: "value"})
    d = d.copy()
    d["position"] = [pos[(int(c), int(r))] for c, r in zip(d.concurrency, d.repetition)]
    d["normalised"] = d["value"] / d.groupby("concurrency")["value"].transform("mean")
    return d.sort_values("position", ignore_index=True)

def drift(run_id, quantity, drop_first=0):
    d = series(run_id, quantity)
    d = d[d.position >= drop_first]
    slope, intercept = np.polyfit(d.position, d.normalised, 1)
    r = float(np.corrcoef(d.position, d.normalised)[0, 1])
    span = d.position.max() - d.position.min()
    return dict(slope_pct_per_tier=slope * 100.0, total_pct=slope * 100.0 * span,
                r=r, n=int(d.position.size)), d
```

**Conventions.** Positions are **0-based**, matching
`docs/dissertation-verification.md` (it records C=1 at "2, 16 and 18"). Each
value is normalised by its own tier's across-repetition mean, so the regression
sees position and not concurrency. "Total change across the sweep" is
`slope × (max position − min position)`, i.e. `slope × 20` for a full 21-point
fit. The reference fit reproduces the previous session exactly: slope
`0.6408835490690825`%/tier, total `12.81767098138165`%, r `0.62975485481248`.

### CLI commands used

```
.venv/bin/python -m crdblab analyze steady-state <run_id> --json
.venv/bin/python -m crdblab analyze raft-overhead --baseline <p2> --cluster <p3> \
    --accept-hardware-difference --json
.venv/bin/python -m crdblab analyze resilience <run_id> --json
.venv/bin/python -m crdblab report figures
.venv/bin/python -m pytest -q ; .venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m ruff check crdblab tests
```

Figure caption strings were recovered from the PDF content streams by resolving
each page's indirect `/Font` dictionary and decoding every `BT…ET` block through
the selected font's `/Differences` array, tracking font switches inside a block
(matplotlib puts each ligature in its own one-glyph subset). Exact glyph
strings, not OCR. PNG widths are read from the IHDR chunk.

---

# Part 1 — The shape and reach of the within-sweep trend

## 1.1 Shape

### The full ordered series

`runs/20260902T233336Z_p2_baseline/`. Order from `manifest.json` → `notes[1]`
(`tier order: [(100, 3), (100, 1), (1, 3), …]`); tier values from
`steady_state.per_repetition`.

| pos | C | rep | tier mean tps (this repetition) | tier mean across 3 reps | normalised |
|---:|---:|---:|---:|---:|---:|
| 0 | 100 | 3 | 3408.878182 | 3441.593939 | 0.990494 |
| 1 | 100 | 1 | 3485.018182 | 3441.593939 | 1.012617 |
| 2 | 1 | 3 | 1464.343636 | 1707.266061 | **0.857713** |
| 3 | 50 | 1 | 3396.821818 | 3563.335152 | 0.953270 |
| 4 | 5 | 2 | 3262.643636 | 3502.241212 | 0.931587 |
| 5 | 200 | 2 | 3440.401818 | 3488.407879 | 0.986238 |
| 6 | 2 | 3 | 2629.494545 | 3004.532121 | 0.875176 |
| 7 | 50 | 3 | 3545.701818 | 3563.335152 | 0.995051 |
| 8 | 200 | 1 | 3528.041818 | 3488.407879 | 1.011362 |
| 9 | 100 | 2 | 3430.885455 | 3441.593939 | 0.996889 |
| 10 | 10 | 3 | 3222.214545 | 3550.581212 | 0.907517 |
| — | | | | | *— step —* |
| 11 | 10 | 2 | 3740.000000 | 3550.581212 | 1.053349 |
| 12 | 2 | 1 | 3155.121818 | 3004.532121 | 1.050121 |
| 13 | 50 | 2 | 3747.481818 | 3563.335152 | 1.051678 |
| 14 | 5 | 1 | 3582.754545 | 3502.241212 | 1.022989 |
| 15 | 5 | 3 | 3661.325455 | 3502.241212 | 1.045424 |
| 16 | 1 | 2 | 1836.201818 | 1707.266061 | **1.075522** |
| 17 | 2 | 2 | 3228.980000 | 3004.532121 | 1.074703 |
| 18 | 1 | 1 | 1821.252727 | 1707.266061 | 1.066766 |
| 19 | 10 | 1 | 3689.529091 | 3550.581212 | 1.039134 |
| 20 | 200 | 3 | 3496.780000 | 3488.407879 | 1.002400 |

### Is it continuous, or a step? — **A step.**

Regression, normalisation held fixed (each value keeps the divisor computed from
all three of its tier's repetitions; only points are dropped):

| fit | n | slope %/tier | total % across the fitted span | r |
|---|---:|---:|---:|---:|
| (a) all 21 positions | 21 | `0.6408835490690825` → **+0.641** | `12.81767098138165` → **+12.818** | **+0.6298** |
| (b) excluding first 2 | 19 | `0.8695460064952704` → **+0.870** | `15.651828116914867` → **+15.652** | **+0.7363** |
| (c) excluding first 4 | 17 | `0.7637458825634043` → **+0.764** | `12.219934121014468` → **+12.220** | **+0.6576** |

**Dropping the early positions does not weaken the trend; it strengthens it.**
Whatever this is, it is not two or four bad tiers at the start.

But a straight line is the wrong model. An exhaustive single-breakpoint search
over all 18 admissible splits (minimising within-group sum of squares on the
normalised series) puts the break **between position 10 and position 11**:

| model | parameters | SS residual | R² |
|---|---:|---:|---:|
| linear in position | 2 | `0.048119` | **0.3966** |
| single step at position 11 | 2 | `0.035377` | **0.5564** |
| (total SS about the mean) | | `0.079745` | |

The two groups **do not overlap at all**:

| group | n | mean | min | max |
|---|---:|---:|---:|---:|
| positions 0–10 | 11 | `0.956174` | `0.857713` | **`1.012617`** |
| positions 11–20 | 10 | `1.048208` | **`1.002400`** | `1.075522` |

Step size `0.092034` in normalised units, i.e. **`9.6253%`** → +9.63%. Every one
of the first eleven tiers is below every one of the last ten. On the harness
clock the break sits between the last sample of position 10
(`wall_offset_s` 955.803, 23:49:32.302 UTC) and the first sample of position 11
(`wall_offset_s` 989.704, 23:50:06.203 UTC) — about sixteen minutes into a
thirty-one-minute sweep.

The step is **not** uniform across tiers. Splitting each tier's repetitions at
the same breakpoint (`crdblab.analysis.steady_state.per_repetition`, grouped by
position < 11):

| C | reps before | reps after | mean before | mean after | step |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 2 | 1464.3436 | 1828.7273 | **+24.884%** |
| 2 | 1 | 2 | 2629.4945 | 3192.0509 | **+21.394%** |
| 5 | 1 | 2 | 3262.6436 | 3622.0400 | +11.016% |
| 10 | 1 | 2 | 3222.2145 | 3714.7645 | +15.286% |
| 50 | 2 | 1 | 3471.2618 | 3747.4818 | +7.957% |
| 100 | **3** | **0** | 3441.5939 | — | **NOT MEASURED** |
| 200 | 2 | 1 | 3484.2218 | 3496.7800 | **+0.360%** |

The effect falls monotonically with concurrency, from a quarter at C=1 to
essentially nothing at C=200, and **the C=100 tier has no post-step measurement
at all** — all three of its repetitions sit at positions 0, 1 and 9. That single
fact is why C=100 has by far the narrowest interval in the sweep (±97.3 ops/s
against ±523 at C=1) and why it dips below both its neighbours.

There is no within-tier component. Regressing each tier's 55 per-second
`total_tps` values on `elapsed_s` gives |r| ≤ 0.44 with no sign preference, and
the correlation between sweep position and within-tier slope is **`−0.0379`**.
The change happens *between* tiers, not inside them.

### Wall-clock start times, and collinearity

`ts_utc` in `metrics.csv` is **not** the sample time: all 55 rows of a tier carry
timestamps within ~5 ms of each other, because `bench.py::_run_tier` writes them
in one loop after the stream closes (the same mechanism as Part 3 § 8 finding 1
of the verification). The sample time is `wall_offset_s`, on the harness clock
whose zero is `manifest.json` → `clock_epoch_utc` = `2026-09-02T23:33:36.499580Z`.

| pos | C | rep | first sample `wall_offset_s` | last sample | first sample UTC | last sample UTC |
|---:|---:|---:|---:|---:|---|---|
| 0 | 100 | 3 | 18.930 | 72.923 | 23:33:55.429 | 23:34:49.422 |
| 1 | 100 | 1 | 107.017 | 161.015 | 23:35:23.516 | 23:36:17.514 |
| 2 | 1 | 3 | 194.576 | 248.572 | 23:36:51.075 | 23:37:45.071 |
| 3 | 50 | 1 | 281.716 | 335.746 | 23:38:18.215 | 23:39:12.245 |
| 4 | 5 | 2 | 370.096 | 424.181 | 23:39:46.595 | 23:40:40.680 |
| 5 | 200 | 2 | 458.442 | 512.439 | 23:41:14.941 | 23:42:08.938 |
| 6 | 2 | 3 | 546.489 | 600.492 | 23:42:42.988 | 23:43:36.991 |
| 7 | 50 | 3 | 635.183 | 689.331 | 23:44:11.682 | 23:45:05.830 |
| 8 | 200 | 1 | 724.640 | 778.636 | 23:45:41.139 | 23:46:35.135 |
| 9 | 100 | 2 | 814.261 | 868.264 | 23:47:10.760 | 23:48:04.763 |
| 10 | 10 | 3 | 901.804 | 955.803 | 23:48:38.303 | 23:49:32.302 |
| 11 | 10 | 2 | 989.704 | 1043.705 | 23:50:06.203 | 23:51:00.204 |
| 12 | 2 | 1 | 1078.370 | 1132.186 | 23:51:34.869 | 23:52:28.685 |
| 13 | 50 | 2 | 1166.571 | 1220.549 | 23:53:03.070 | 23:53:57.048 |
| 14 | 5 | 1 | 1254.062 | 1308.059 | 23:54:30.561 | 23:55:24.558 |
| 15 | 5 | 3 | 1342.941 | 1396.948 | 23:55:59.440 | 23:56:53.447 |
| 16 | 1 | 2 | 1432.345 | 1486.318 | 23:57:28.844 | 23:58:22.817 |
| 17 | 2 | 2 | 1520.289 | 1574.292 | 23:58:56.788 | 23:59:50.791 |
| 18 | 1 | 1 | 1607.659 | 1661.737 | 00:00:24.158 | 00:01:18.236 |
| 19 | 10 | 1 | 1696.159 | 1750.299 | 00:01:52.658 | 00:02:46.798 |
| 20 | 200 | 3 | 1785.526 | 1839.334 | 00:03:22.025 | 00:04:15.833 |

**Position and elapsed wall time are collinear to the point of being the same
variable.** `corr(position, tier start on the harness clock) = 0.9999989115775013`.
Tier-to-tier spacing is `88.330 s` mean, sd `0.743 s`, range `87.140`–`89.621 s`.
Nothing in this sweep can distinguish "the twelfth tier" from "sixteen minutes
in"; they are the same statement.

### Is any early tier an outlier by the project's own standards?

**No — the project has no outlier standard, and the run passes every check it
does have.**

`crdblab.analysis.validation.validate` on `runs/20260902T233336Z_p2_baseline`
returns `ok = True` with **zero findings**. Its checks are `check_plausibility`
(a 20,000 ops/s ceiling for cumulative rows leaking into the sample stream),
`check_quantile_ordering`, `check_littles_law`, `check_sample_cadence`,
`check_op_coverage`, `check_error_monotonicity` and `check_run_comparability`
(`crdblab/analysis/validation.py:64-260`). **None of them is an outlier test on a
tier mean, a repetition mean, or a residual.** `crdblab analyze steady-state`
reports a Student's t interval over repetition means and no outlier flag;
`raft_overhead._saturation` classifies a *phase*, not a tier.

So the honest answer is the second one: **the whole series is tilted — or, more
precisely, split.** No single early tier carries it. Position 2 (C=1 rep 3) is
the lowest normalised value in the sweep at `0.857713`, but removing it and
position 3 raises the slope rather than lowering it (fit (b) above), and its
tier-mates at positions 16 and 18 sit at `1.075522` and `1.066766`, which is the
step, not an isolated bad reading.

## 1.2 Does latency drift the same way as throughput?

**It drifts hard, in the opposite direction, and the write path carries all of
it.** Same regression, same run, same positions.

| quantity | source | slope %/tier | total % across 21 tiers | r |
|---|---|---:|---:|---:|
| throughput (summed) | `per_repetition.mean_total_tps` | `0.640884` → **+0.641** | `12.8177` → **+12.818** | **+0.6298** |
| **update p50** | `Run.latency_by_op`, `op="update"` | `-1.555668` → **−1.556** | `-31.1134` → **−31.113** | **−0.6190** |
| read p50 | `Run.latency_by_op`, `op="read"` | `0.063981` → **+0.064** | `1.2796` → **+1.280** | **+0.1275** |
| update p95 | same | `-1.479047` → **−1.479** | `-29.5809` → **−29.581** | **−0.6210** |
| update p99 | same | `-1.392790` → **−1.393** | `-27.8558` → **−27.856** | **−0.6597** |
| frequency-weighted p50 (Little's law input) | `per_repetition.mean_weighted_p50_ms` | `-0.655698` → **−0.656** | `-13.1140` → **−13.114** | **−0.6025** |

**Stated plainly: latency is not flat.** Update latency drifts *downward* across
the sweep by roughly 31% at the median and by 28–30% at p95 and p99, with a
correlation as strong as the throughput trend's and of the opposite sign. Read
latency is flat (+1.3%, r = 0.13). The frequency-weighted blend that the
Little's-law check uses drifts −13.1%, which is what you would expect from a
20%-weighted update component moving −31% against a flat read component.

Whatever changed sixteen minutes into this sweep changed the **write service
time**, not the read path and not the machine's capacity: the step is +24.9% in
throughput at C=1, where throughput is the reciprocal of service time, and
+0.36% at C=200, where the system is capacity-bound. Update p50 mirrors it:
−36.9% at C=1 falling to −0.6% at C=200.

### Specifically at C=1

`runs/20260902T233336Z_p2_baseline`; latency from `Run.latency_by_op` (mean over
the tier's 55 per-interval medians, per operation type, never pooled);
throughput from `steady_state.per_repetition`; intervals from
`steady_state.confidence_interval` (Student's t over the three repetition means,
n = 3, t = 4.303).

| pos | rep | update p50 (ms) | update p95 | update p99 | throughput (ops/s) |
|---:|---:|---:|---:|---:|---:|
| 2 | 3 | **`1.8781818181818182`** | `2.332727` | `2.989091` | **`1464.343636`** |
| 16 | 2 | **`1.1709090909090911`** | `1.516364` | `1.956364` | **`1836.201818`** |
| 18 | 1 | **`1.198182 (1.198181818181818)`** | `1.583636` | `1.994545` | **`1821.252727`** |

The three throughput values the previous session gives — 1464.34, 1836.20,
1821.25 — are **confirmed exactly**.

| quantity | tier mean (unrounded) | rounded | sd | 95% CI half-width | interval |
|---|---|---|---|---|---|
| update p50 @ C=1 | `1.4157575757575758` | **1.416 ms** | `0.401` | **`0.995` ms** | **[0.421, 2.411] ms** |
| throughput @ C=1 | `1707.2660606060606` | **1707.266 ops/s** | `210.510` | **`522.977` ops/s** | **[1184.29, 2230.24] ops/s** |

The reported denominator of the headline ratio, 1.4157575757575758 ms, is the
mean of one pre-step reading 60% larger than the two post-step readings. Its
own 95% interval spans a factor of **5.7** from end to end.

## 1.3 Is the trend confined to Phase II of this deployment?

Identical regression, identical code path, all four sweeps.

| sweep | run | throughput slope %/tier | throughput total % | r | update p50 slope %/tier | update p50 total % | r |
|---|---|---:|---:|---:|---:|---:|---:|
| **B Phase II (reported)** | `20260902T233336Z_p2_baseline` | **+0.64088** | **+12.818** | **+0.6298** | **−1.55567** | **−31.113** | **−0.6190** |
| B Phase III (reported) | `20260903T000438Z_p3_cluster` | −0.01142 | −0.228 | −0.0559 | −0.02056 | −0.411 | −0.1101 |
| A Phase III | `20260902T195644Z_p3_cluster` | +0.07483 | +1.497 | +0.3146 | −0.06907 | −1.381 | −0.3243 |
| A Phase II | `20260902T175621Z_p2_baseline` | −0.01971 | −0.394 | −0.1232 | +0.05846 | +1.169 | +0.2519 |

Read p50 and the weighted p50 for completeness:

| sweep | read p50 total % (r) | weighted p50 total % (r) |
|---|---|---|
| B Phase II | +1.280 (+0.1275) | **−13.114 (−0.6025)** |
| B Phase III | +1.971 (+0.1441) | +0.060 (+0.0103) |
| A Phase III | −1.484 (−0.1829) | −2.047 (−0.3555) |
| A Phase II | −0.405 (−0.1371) | +0.249 (+0.0778) |

**The reported Phase II sweep is alone.** The other three move by less than 2%
across 21 tiers on every quantity, against its 12.8% and 31.1%. Deployment A's
Phase II — the −0.4% case `docs/defects.md:531` generalises from — is flat on
throughput *and* flat on latency.

### What this does to the "cluster is network-bound and stable, baseline is
### processor-bound and variable" argument

**It supports it — and more specifically than the dissertation currently claims,
which is why the current wording should be tightened rather than kept.**

The evidence is now two-sided rather than one-sided. Both cluster sweeps are
flat within ±2% on both throughput and write latency; both are drawn from the
same 21-tier plan, in the same realised order (§ 5.4), on machines of the same
recorded model. The two baseline sweeps disagree with *each other*: one is flat,
one moves 12.8% up and 31.1% down inside half an hour. A quantity floored by a
67 ms round trip has little room to drift; a quantity set by local write service
time on a 2-vCPU host has a great deal.

Three qualifications the write-up must carry:

1. The argument is now about **stability**, not about **the value**. Deployment
   A's Phase II is as stable as either cluster sweep. "The baseline is variable"
   is true of one of the two baseline sweeps in this artefact set, and the one
   it is true of is the reported one.
2. The drift is in the **write path only** — read p50 is flat in every sweep
   including this one. "Processor-bound" as an explanation has to account for
   why reads did not move at all while writes moved a third.
3. It is a **step**, not a gradual degradation or a warm-up ramp. Nothing in
   `runs/` says what changed at 23:50 UTC on 2026-09-02. I decline to name a
   mechanism (§ 6).

## 1.4 What was drifting

Regressed on the **21 tier-end samples**, one per `(concurrency, repetition)`,
not the 1,155 duplicated rows. Confirmed first that the duplication is total:
`df.groupby(["concurrency","repetition"])[col].nunique()` returns min 1, max 1
for all three columns in both runs, so `groupby(...).first()` loses nothing.

### Reported Phase II — raw

| column | slope per tier | total across 21 tiers | r | monotonically increasing? |
|---|---:|---:|---:|---|
| `gateway_cpu_pct` | `+0.0783117` pp | `+1.56623` pp | **+0.0405** | no |
| `gateway_rss_bytes` | `+1.23522e+07` B | `+2.47045e+08` B (+247 MB) | **+0.6697** | **no** |
| `gateway_disk_iops` | `−68.5156` | `−1370.31` | **−0.0803** | no |

### Reported Phase III — raw

| column | slope per tier | total across 21 tiers | r | monotonically increasing? |
|---|---:|---:|---:|---|
| `gateway_cpu_pct` | `−1.40664` pp | `−28.1328` pp | −0.3204 | no |
| `gateway_rss_bytes` | `+6.99899e+06` B | `+1.3998e+08` B (+140 MB) | **+0.4861** | **no** |
| `gateway_disk_iops` | `+61.1779` | `+1223.56` | +0.2884 | no |

**RSS rises with position in both runs, and it is not monotonic in either.**
Phase II's 21 values run 2.087, 2.457, 2.433, 2.484, 2.509, 2.546, 2.581, 2.528,
2.525, 2.528, 2.594, 2.546, 2.630, 2.559, 2.569, 2.599, 2.626, 2.578, 2.570,
2.599, 2.612 GB: a jump between the first and second tier, then a band. Dropping
position 0 raises r to `0.8028`; dropping the first two leaves `0.7664`.

**What that is evidence for:** the server process's resident set grew during the
first tier and continued to creep afterwards, consistent with a cache and
memtable filling under `--cache=0.25 --max-sql-memory=0.25` on a 4,007,012 kB
host. That is a description of the process, and it is real.

**What it is not evidence for:** the throughput trend. Phase III's RSS rises with
position on the same plan in the same order (r = +0.4861, +140 MB) **and Phase
III has no throughput trend at all** (r = −0.056, −0.23%). A variable that rises
in both runs cannot explain a step that occurs in one. It also has the wrong
shape: RSS's rise is front-loaded on position 0–1, and the throughput step is at
position 11.

**Null result, stated as one:** neither `gateway_cpu_pct` nor
`gateway_disk_iops` correlates with position in the reported Phase II sweep
(r = **+0.0405** and **−0.0803**). Nothing in the three host columns tracks the
throughput step. If the cause was visible to the host sampler, these 21 samples
did not see it.

Normalising each host column by its own tier's mean — which removes the
concurrency confound the same way the throughput regression does — gives
`gateway_cpu_pct` **+10.278%** across the sweep at r = **+0.5580** in Phase II.
That is the *response*: CPU per tier rose because the tier delivered more work.
It is not an explanation, and Phase III's normalised CPU rises by a comparable
+12.478% (r = +0.3758) while its throughput does not move at all. I report it and
draw nothing from it.

`gateway_disk_iops` cannot be normalised in Phase III: **12 of its 21 tier-end
samples are exactly 0.0**, and all three of the C=2 tier's samples are, so that
tier's divisor is zero and the normalisation is undefined. Reported as undefined
rather than as a number. (Phase II has eight zeros among its 21 and no all-zero
tier, which is why its normalised figure above exists at all — and at r = −0.067
it says nothing.)

---

# Part 2 — Exposure of the reported figures

**Every "detrended" figure in this Part is a diagnostic estimate produced by
dividing each repetition value by a fitted trend and re-averaging. It is not a
measurement, it was never measured, and it must not appear in a results table.**
Two trend models are used where they disagree: `lin` = the linear fit of § 1.1(a);
`step` = the two-group means of § 1.1.

| # | Quantity | Reported value | Touched? | Exposure |
|---|---|---|---|---|
| 2.1 | Phase II update p50 @ C=1 | `1.4157575757575758` ms | **Yes, heavily** | Mean of `1.878182` (pre-step), `1.170909`, `1.198182`. 95% CI **±`0.995` ms**, interval [0.421, 2.411]. Diagnostic linear detrend: `1.4434185823342116` ms (+1.95%) — but the detrend *understates* it, because the spread is a step, not a slope. |
| 2.2 | Unqueued write-latency ratio | `50.37970890410959` | **Yes** — see below | Range across the three C=1 repetitions taken individually: **`37.97579864472411` to `60.91459627329191`**, i.e. **37.98× to 60.91×** |
| 2.3 | Phase II peak throughput | 3563.335 ops/s at C=50 | **Marginally, and in the direction that helps** | Peak survives both detrends at C=50. See below. |
| 2.4 | Phase II throughput @ C=1 | `1707.2660606060606` ops/s | **Yes** | CI ±`522.977`. Diagnostic: linear `1681.3725` (−1.52%), step `1673.5682` (−1.98%) |
| 2.5 | Saturation, Phase II | `saturated: true`, `final_tier_gain 0.0136` | **Sign flips, classification does not** | Diagnostic gain: linear `−0.035145`, step `−0.016134`. All three are below `SATURATION_TOLERANCE = 0.05`, so `saturated: true` holds under every model. |
| 2.6 | Matched-utilisation table | denominator = Phase II peak `3563.335` | **Yes, via 2.3** | The denominator is the C=50 tier mean, whose three repetitions sit at positions 3, 7 and 13 — straddling the step (+7.96%). Every utilisation level in the table is a ratio to a number that is itself position-dependent. |
| 2.7 | Matched-throughput overlap band | `[1707.3, 1849.5]` | **Yes, at the lower bound** | `matched_throughput` sets `lo = max(min tps of each phase)` (`raft_overhead.py:206`). Phase II's minimum tier **is** C=1, so **the band's lower bound is exactly the figure of 2.4**. The upper bound 1849.5 is Phase III's peak and is untouched (§ 1.3). |
| 2.8 | Little's law, Phase II @ C=1 | `1.9868%` (per-tier method) | **No — it is insensitive to the trend** | Per repetition: pos 2 `0.017412`, pos 16 `0.021727`, pos 18 `0.018341`. Range 1.74–2.17%, all rounding to 2%. Both the implied latency and the weighted median move together, so their ratio does not. |

### 2.3 / 2.5 — is the peak positional?

**No. The confound exists, it is real, and it runs the *other* way.**

Mean sweep position against tier throughput:

| C | positions | mean position | measured tps | detrended (linear) **[DIAGNOSTIC]** | change |
|---:|---|---:|---:|---:|---:|
| 1 | 2, 16, 18 | 12.000 | `1707.2661` | `1681.3725` | −1.52% |
| 2 | 6, 12, 17 | 11.667 | `3004.5321` | `2968.0700` | −1.21% |
| 5 | 4, 14, 15 | 11.000 | `3502.2412` | `3477.9895` | −0.69% |
| 10 | 10, 11, 19 | 13.333 | `3550.5812` | `3475.5741` | −2.11% |
| **50** | 3, 7, 13 | **7.667** | **`3563.3352`** | **`3616.1222`** | **+1.48%** |
| 100 | 0, 1, 9 | **3.333** | `3441.5939` | `3597.8865` | **+4.54%** |
| 200 | 5, 8, 20 | 11.000 | `3488.4079` | `3471.4404` | −0.49% |

`corr(mean position, tier throughput) = −0.28943621530339647` — **negative**. The
tiers that produced the peak sat *early*, not late, and the tier that produced
the low end (C=1, mean position 12.0) sat *late*. The randomisation put the two
highest-concurrency tiers at the front: `corr(mean position, log C) = −0.5829`.

**So the recorded order does confound position with concurrency, but in the
direction that makes the measured curve a conservative one.** The trend was
working against the high-concurrency tiers and in favour of the low ones. Removing
it does not create the peak; it enlarges it.

**Does the peak survive detrending? Yes, under both models.**

| | measured | linear detrend **[DIAGNOSTIC]** | step detrend **[DIAGNOSTIC]** |
|---|---|---|---|
| peak concurrency | **C=50** | **C=50** | **C=50** |
| peak value | `3563.3352` | `3616.1222` | `3611.9539` |
| `final_tier_gain` (C=200 vs C=100) | `+0.013602` | `−0.035145` | `−0.016134` |
| `saturated` at tolerance 0.05 | **true** | **true** | **true** |

**One thing does not survive, and it should be flagged.** The measured gap
between the peak (C=50, 3563.335) and C=100 (3441.594) is **3.54%**. Detrended it
is **0.51%** (3616.12 against 3597.89). C=100 is the one tier whose three
repetitions are *all* on the pre-step side of the sweep. The statement "throughput
peaks at C=50 and falls at C=100" is largely a statement that C=100 was measured
early. The statement "throughput saturates at C≈50 and does not rise thereafter"
survives everything. **The dip is positional; the plateau is not.**

### 2.2 — is the headline ratio exposed?

**Phase II latency is not flat under § 1.2 — it moves −31.1% at r = −0.62 — so
this is not the short answer.**

The ratio is `71.32545454545455 / <Phase II C=1 update p50>`, computed from
`_p50_exact` on both sides (`raft_overhead.py:471-473`). The numerator is Phase
III's C=1 update p50 tier mean; § 1.3 shows Phase III's update p50 does not drift
(−0.41% across 21 tiers, r = −0.11), so the numerator is sound. The whole
exposure is in the denominator.

Taking the three Phase II C=1 repetitions individually against the same numerator:

| Phase II rep | sweep position | update p50 (ms) | ratio (unrounded) | rounded |
|---:|---:|---|---|---:|
| 3 | 2 (**pre-step**) | `1.8781818181818182` | `37.97579864472411` | **37.98×** |
| 2 | 16 (post-step) | `1.1709090909090911` | `60.91459627329191` | **60.91×** |
| 1 | 18 (post-step) | `1.198181818181818` | `59.528072837632784` | **59.53×** |
| — | mean of the three (as reported) | `1.4157575757575758` | `50.37970890410959` | **50.38×** |

**The ratio would span 37.98× to 60.91× across the three repetitions taken
individually — a factor of 1.60 from end to end.** The reported 50.38× is the
ratio to the mean of a pre-step and two post-step readings; it is not a value any
single repetition produced, and it is closer to the midpoint of the two regimes
than to either.

For completeness, the diagnostic linear detrend gives `49.41425544772412` →
**49.41×** [DIAGNOSTIC ONLY]. That is a 1.9% shift and it badly understates the
exposure, because averaging a step into a slope hides the bimodality. **The range
above, not the detrended point, is the honest statement of exposure.**

---

# Part 3 — Corrections to the analysis layer

Both fixes follow the pattern `raft_overhead.py:471-473` established for
`ratio_x`: compute from unrounded values, round the quotient once, keep the
rounded forms for display only. Neither reads or writes anything under `runs/`.
Neither touches a measured column.

**Invariance check, run for each fix and again for both together:**

```
$ .venv/bin/python -m crdblab analyze steady-state 20260902T233336Z_p2_baseline --json > after.json
$ diff -q before.json after.json && echo IDENTICAL
IDENTICAL
```

Identical for `20260903T000438Z_p3_cluster` as well. Every `tiers[]` record —
`mean_total_tps`, `sd_total_tps`, `ci95_half_width_tps`, `mean_weighted_p50_ms`,
`implied_mean_latency_ms`, `errors_cum` — is byte-identical before and after.

## 3.1 `throughput_ratio_x` computed from 1 dp display values

### Diff

```diff
--- a/crdblab/analysis/raft_overhead.py
+++ b/crdblab/analysis/raft_overhead.py
@@ -515,12 +525,19 @@ def same_concurrency_delta(baseline: Run, cluster: Run) -> dict[str, Any]:

     rows: list[dict[str, Any]] = []
     for concurrency in shared:
+        # Divide the throughputs, then round the quotient -- never the reverse.
+        # The 1 dp forms below exist to be displayed; dividing them instead put
+        # the display rounding into the ratio and reported 25.26x at C=1 for a
+        # quantity whose value is 25.25x. This is the same defect the unqueued
+        # ``ratio_x`` was fixed for, in the same module.
+        phase_ii_tps = float(a.loc[concurrency, "mean_total_tps"])
+        phase_iii_tps = float(b.loc[concurrency, "mean_total_tps"])
         row: dict[str, Any] = {
             "concurrency": int(concurrency),
-            "phase_ii_tps": round(float(a.loc[concurrency, "mean_total_tps"]), 1),
-            "phase_iii_tps": round(float(b.loc[concurrency, "mean_total_tps"]), 1),
+            "phase_ii_tps": round(phase_ii_tps, 1),
+            "phase_iii_tps": round(phase_iii_tps, 1),
         }
-        row["throughput_ratio_x"] = round(row["phase_ii_tps"] / row["phase_iii_tps"], 2)
+        row["throughput_ratio_x"] = round(phase_ii_tps / phase_iii_tps, 2)
         for op in sorted(set(la["op"]) & set(lb["op"])):
```

### The whole `same_concurrency_delta` table, re-emitted

`crdblab analyze raft-overhead --baseline 20260902T233336Z_p2_baseline --cluster
20260903T000438Z_p3_cluster --accept-hardware-difference --json` →
`same_concurrency_delta.rows`.

| C | `phase_ii_tps` | `phase_iii_tps` | `throughput_ratio_x` **old** | **new** | `read_p50_ratio_x` | `update_p50_ratio_x` |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1707.3 | 67.6 | **25.26** | **25.25** ← | 1.75 | 50.38 |
| 2 | 3004.5 | 134.0 | **22.42** | **22.43** ← | 1.58 | 48.05 |
| 5 | 3502.2 | 332.4 | 10.54 | 10.54 | 0.82 | 24.24 |
| 10 | 3550.6 | 633.8 | 5.6 | 5.6 | 0.49 | 14.14 |
| 50 | 3563.3 | 1742.2 | 2.05 | 2.05 | 0.74 | 5.38 |
| 100 | 3441.6 | 1849.5 | 1.86 | 1.86 | 1.09 | 3.67 |
| 200 | 3488.4 | 1679.1 | 2.08 | 2.08 | 1.8 | 2.92 |

**Two values changed: C=1 from 25.26 to 25.25, C=2 from 22.42 to 22.43.** Every
other cell in the table, including all seven `read_p50_ratio_x` and all seven
`update_p50_ratio_x` (which were already computed from unrounded medians at
`raft_overhead.py:545`), is unchanged. This table remains labelled `"comparable":
false`, `"use": "Chapter 5 error case study only; never as a results table"`.

### Regression test

`tests/test_analysis.py::test_the_same_concurrency_throughput_ratio_is_computed_before_rounding`
— asserts the emitted ratio equals `round(exact, 2)` computed from
`steady_state.per_tier`, and separately that it **differs** from the ratio of the
two displayed values. Verified to fail on the unpatched module
(`git stash push crdblab/analysis/raft_overhead.py` → `1 failed`).

## 3.2 Utilisation levels rounded before being multiplied back

### Diff

```diff
--- a/crdblab/analysis/raft_overhead.py
+++ b/crdblab/analysis/raft_overhead.py
@@ -355,12 +355,22 @@ def matched_utilisation(
             "points": [],
         }

-    levels = sorted(
-        {round(float(t) / peak_a, 3) for t in a["mean_total_tps"]}
-        | {round(float(t) / peak_b, 3) for t in b["mean_total_tps"]}
-    )
+    # A level is *identified* by its displayed 3 dp value, so the set of points
+    # is the set of measured tiers on either side; but the arithmetic below uses
+    # the exact ratio. Rounding a level and multiplying it back by the peak
+    # displaces the throughput it names, which silently converts a *measured*
+    # point into an interpolated one: 0.843 x 3563.335 = 3003.89 ops/s, where
+    # the C=2 tier it came from measured 3004.532. The displacement here is
+    # 0.6 ops/s; the mechanism is unbounded, and it also drops the lowest level
+    # altogether whenever rounding pushes it below ``lo``.
+    levels: dict[float, float] = {}
+    for peak, frame in ((peak_a, a), (peak_b, b)):
+        for t in frame["mean_total_tps"]:
+            exact = float(t) / peak
+            levels.setdefault(round(exact, 3), exact)
     points: list[dict[str, Any]] = []
-    for u in levels:
+    for key in sorted(levels):
+        u = levels[key]
         if not (lo <= u <= hi):
             continue
         ta, tb = u * peak_a, u * peak_b
```

The dedupe key stays the 3 dp display value, so the *set* of points is still one
per measured tier per phase; only the arithmetic changes.

### The whole `matched_utilisation` table, re-emitted

`utilisation_range` is `[0.479, 1.0]` before and after. Point count **8 → 9**.

| `utilisation` | `phase_ii_tps` old → new | `phase_iii_tps` old → new | `phase_ii_latency_ms` old → new | `phase_iii_latency_ms` old → new | `overhead_x` old → new |
|---:|---|---|---|---|---|
| **0.479** | — → **1707.3** | — → **886.2** | — → **1.416** | — → **79.63** | — → **56.25** |
| **0.843** | 3003.9 → **3004.5** | 1559.2 → **1559.5** | 1.485 → 1.485 | 99.946 → **99.956** | **67.31 → 67.32** |
| 0.908 | 3235.5 → **3235.0** | 1679.4 → **1679.1** | 2.162 → **2.16** | 103.575 → **103.566** | **47.92 → 47.95** |
| 0.942 | 3356.7 → **3356.6** | 1742.3 → **1742.2** | 2.517 → **2.516** | 105.495 → **105.472** | 41.92 → 41.92 |
| 0.966 | 3442.2 → **3441.6** | 1786.7 → **1786.4** | 2.767 → **2.765** | 128.029 → **127.875** | **46.27 → 46.24** |
| 0.979 | 3488.5 → **3488.4** | 1810.7 → 1810.7 | 2.903 → 2.903 | 140.236 → **140.21** | 48.31 → 48.31 |
| 0.983 | 3502.8 → **3502.2** | 1818.1 → **1817.8** | 2.966 → **2.943** | 143.991 → **143.855** | **48.55 → 48.88** |
| 0.996 | 3549.1 → **3550.6** | 1842.1 → **1842.9** | 5.027 → **5.093** | 156.198 → **156.593** | **31.07 → 30.74** |
| 1.0 | 3563.3 → 3563.3 | 1849.5 → 1849.5 | 19.606 → 19.606 | 159.953 → 159.953 | 8.16 → 8.16 |

Every value that changed is in bold. Summary: **one new row; 21 cells changed
across the other eight rows; the 1.0 row is untouched.**

**Does "67.31x at 84%" move? Yes — to `67.32×`.**

**Does the 84% row now land exactly on the C=2 tier? Yes.** Before the fix,
`0.843 × 3563.335 = 3003.89` ops/s, so the Phase II side was interpolated between
C=1 and C=2. After it, `phase_ii_tps = 3004.5`, which is
`per_tier(baseline).loc[2, "mean_total_tps"] = 3004.532` rounded, and
`phase_ii_latency_ms = 1.485`, which is
`latency_by_op(baseline)` at `{concurrency: 2, op: "update"}` = `1.4848484848484846`
rounded. Both sides of that point are now read off the tier, not interpolated
towards it.

Three other rows also became measured on the Phase II side: `0.983` now carries
the C=5 tier (3502.2 / 2.943) and `0.996` the C=10 tier (3550.6 / 5.093). The
`0.966` and `0.979` rows name the C=100 and C=200 throughputs but still report
interpolated latencies — correctly, because `_interpolate` truncates at the peak
and only interpolates the rising branch (`raft_overhead.py:129-136`), and both of
those tiers sit past C=50. That is documented pre-existing behaviour, not a
consequence of this fix.

**The new row is the more consequential change.** `lo` is
`max(1707.266/3563.335, 67.609/1849.548) = 0.4791204…`. Rounding that to `0.479`
put it *below* `lo`, so the `lo <= u <= hi` guard rejected the very level that
defines the lower end of the comparable range. The table therefore advertised
`utilisation_range: [0.479, 1.0]` while starting at 0.843. It now starts where it
says it starts, at Phase II's C=1 tier, with an overhead of **56.25×**.

Note that this new row inherits every exposure of § 2.1/2.4: its Phase II side
*is* the C=1 tier.

### Regression test

`tests/test_analysis.py::test_a_matched_utilisation_level_is_not_rounded_before_it_is_used`
— asserts that for each of two measured Phase II tiers, the emitted point at that
tier's level carries the tier's own throughput and its own measured update
median; and that the lowest emitted level equals `utilisation_range[0]`. Verified
to fail on the unpatched module (`StopIteration` — the level is absent).

## 3.3 Re-audit of the `round()`-feeds-arithmetic pattern across `crdblab/`

`grep -rn "round(" crdblab/` → **100 call sites** (verification's Part 3 § 1
counted 67 across `crdblab/analysis/` alone; the wider sweep adds
`phases/bench.py` 7, `phases/p4_chaos.py` 16, `phases/p1_network.py` 8,
`core/preflight.py` 3). Reading each: the great majority write into a dict for
display or serialisation and are never read back. The exceptions, after the two
fixes above:

| # | site | live? | changes a value at reported precision? |
|---|---|---|---|
| 1 | `steady_state.py:64-71` — `confidence_interval` rounds `mean`/`sd`/`ci95_half_width` to 3 dp; `per_tier.mean_total_tps` is that rounded mean, and `raft_overhead` divides it | **yes** | **No.** Checked exhaustively against fully-unrounded `per_repetition` means: all seven `throughput_ratio_x` agree (C=1 `25.252019` vs `25.252052`, both → 25.25; C=2 `22.428678` vs `22.428743`, both → 22.43; …), and both `final_tier_gain` values agree (II `0.013602` → 0.0136; III `−0.092154` → −0.0922) |
| 2 | `steady_state.py:134-138` — `mean_weighted_p50_ms` and `implied_mean_latency_ms` rounded to 3 dp, then divided | **yes** | **Yes, for one quantity — see below** |
| 3 | `raft_overhead.py:447-467` (was `:437-458`) — `weighted` and `tps` read out of the 3 dp `per_tier`, then `implied` and `littles_law_agreement` computed | **yes** | **Borderline.** Phase II C=1 emits `0.0302`; fully unrounded it is `0.02972603642733469` → `0.0297`. The 4 dp JSON value changes; the printed form does not — the caveat string's `{:.1%}` says `3.0%` either way. Phase III is unchanged (`0.0621` both). |
| 4 | `resilience.py:370` — `fault_points = sorted({round(lower, 3), round(upper, 3)})`, then used as `fault_at` in `rto_s = round(recovered − fault_at, 3)` | **yes, but not for the reported runs** | **No.** Both reported Phase IV runs have `clock_alignment.method = "measured"`, so `alignment.exact` is true and `fault_points = [float(wall)]` unrounded (`resilience.py:365-367`). Confirmed: `recomputed[].fault_at_s` = `[60.005]` and `[60.003]`, single-valued. The branch fires only for `20260902T022406Z_p4-chaos-dead` (`bounded`, points `[9.959, 15.005]`), where 3 dp on seconds is far below the reported 0.1 s. |
| 5 | `resilience.py:425-427` — `floor = results[0]["floor_tps"]` (2 dp) compared against `settled["mean_tps"]` (1 dp) | **yes** | **No.** The only run reaching this branch settles at `1194.8` against a floor of `1395.26`; the margin is 200 ops/s. |
| 6 | `phases/p4_chaos.py:423 → :470-475, :508` — `injected["at_offset_s"] = round(…, 3)` is then passed to `find_recovery` and used in `performance_rto_s = round(recovered_at − fault_offset, 3)` | **yes** | **No.** Reported RTOs are quoted to 0.1 s (4.4 s, 12.0 s) and the analysis layer re-derives both independently: `performance_rto.agrees_with_recorded = true`, `recompute_delta_s = 0.0`. |

**So the two the verification flagged as negligible are *not* the only ones left,
on two counts.**

**(i) The audit was scoped to `crdblab/analysis/`. Site 6 is in `phases/`** — it
is a measurement-time instance of the same pattern, and unlike the analysis-layer
ones it writes a number into `events.json` that cannot be recomputed from
anything but the code. It happens to be harmless at the reported precision.

**(ii) Site 2 changes a number the dissertation quotes.** The per-tier Little's
law agreement of verification claims 2.10/2.11 is computed by dividing two
columns that `per_tier` has already rounded to 3 dp:

| Phase II C=1, per-tier method | value | rounded |
|---|---|---|
| from `per_tier`'s 3 dp columns (`implied 0.592`, `weighted 0.604`) | `0.01986754966887419` | **1.9868% → 1.99% → 2.0%** |
| from the unrounded `per_repetition` means (`0.5921916258768825`, `0.6036767023133948`) | `0.019025210667397777` | **1.9025% → 1.90% → 1.9%** |

**At one decimal place this is 2.0% against 1.9%.** Verification claim 2.11
quotes 2.0%. Phase III is unaffected (`6.2080%` against `6.2081%`).

I have **not** fixed site 2, and I say so explicitly rather than leaving it
implied: the task authorises the two named fixes, and changing `per_tier`'s
rounding would change the `tiers[]` block that `analyze steady-state` emits —
exactly what rule 5 forbids. The correct remedy is the `_p50_exact` pattern
(carry an unrounded companion key alongside the display column), which is a
larger change to `steady_state.py` than this task's scope. Until then, **the
Phase II C=1 Little's-law agreement should be quoted as "about 2%", not as 2.0%,
and the two methods should not be reported to two significant figures.**

Two further notes, for completeness rather than as defects:

- `p1_network.py:145-151` rounds each measured RTT to 3 dp **at write time**, and
  `resilience.quorum_geometry` then divides those stored values
  (`198.183 / 67.054 = 2.9555731201718016` → `floor_ratio_x 2.96`). This is not
  round-then-divide: the 3 dp value in `network.csv` *is* the retained
  measurement, and the underlying per-ping samples exist nowhere. It sets a
  precision floor, not an error.
- `core/preflight.py:507` rounds `match_rate` to 6 dp before it is compared
  against a 0.99 threshold. Six decimals against a two-decimal threshold.

## Test suite

```
$ .venv/bin/python -m pytest --collect-only -q ; .venv/bin/python -m pytest -q
```

| | before | after |
|---|---:|---:|
| collected | **100** | **102** |
| passed | 100 | **102** |

`.venv/bin/python -m ruff check crdblab tests` reports the same 57 pre-existing
errors before and after the change set (diffed by rule and file; no new
violation introduced).

---

# Part 4 — Figure provenance

## The change

`figures.resilience_timeline` hard-coded `fig5_resilience_timeline.png` and
`figures.render_all` called it exactly once, so **only one Phase IV timeline
could ever be produced by `crdblab report figures`** — and the second one, drawn
by hand, would have overwritten the first if it had been produced through the
module at all. Three edits, one per link in that chain.

```diff
--- a/crdblab/report/figures.py
+++ b/crdblab/report/figures.py
@@ -431,6 +431,28 @@ def raft_overhead_curve(
+#: Output filename per fault class. Phase IV runs one fault of each class and
+#: the two timelines are different figures, so the name is keyed on the class
+#: rather than fixed: rendering a second run through a single hard-coded
+#: ``fig5`` filename silently overwrote the first, which is why
+#: ``fig6_resilience_timeline_recover.png`` existed in ``figures/`` with no path
+#: through this module that could produce it. The names are constants, not
+#: derived from the run id, so a caption citing fig5 or fig6 keeps meaning the
+#: same figure across a re-render.
+_RESILIENCE_FIGURES = {
+    "dead": "fig5_resilience_timeline.png",
+    "recover": "fig6_resilience_timeline_recover.png",
+}
+
+
+def _resilience_filename(mode: str | None) -> str:
+    """Filename for one fault class, distinct for any class not yet named."""
+    if mode in _RESILIENCE_FIGURES:
+        return _RESILIENCE_FIGURES[mode]
+    slug = "".join(c if c.isalnum() else "_" for c in str(mode or "unknown"))
+    return f"fig5_resilience_timeline_{slug}.png"
+
+
 # --- Phase IV ---------------------------------------------------------------
@@ -450,6 +472,7 @@ def resilience_timeline(run: Run, out_dir: Path) -> Path:
     fault = resilience.fault_offsets(run, alignment)
+    mode = (run.events or {}).get("mode")
@@ -473,7 +496,7 @@
-            label=f"fault ({run.events.get('mode')})",
+            label=f"fault ({mode})",
@@ -505,12 +528,12 @@
-        f"Throughput through a {run.events.get('mode')} fault on "
+        f"Throughput through a {mode} fault on "
@@
-    return _finish(fig, ax, [run.run_id], out_dir / "fig5_resilience_timeline.png")
+    return _finish(fig, ax, [run.run_id], out_dir / _resilience_filename(mode))

@@ -518,9 +541,15 @@ def render_all(
-    chaos: Run | None = None,
+    chaos: Run | Sequence[Run] | None = None,
 ) -> list[Path]:
-    """Render every figure whose inputs are available."""
+    """Render every figure whose inputs are available.
+
+    ``chaos`` accepts a sequence because Phase IV produces one timeline per
+    fault class and they are separate figures. Calling
+    :func:`resilience_timeline` once here was the other half of the fig6
+    provenance gap: even with both runs loaded, only one could be drawn.
+    """
@@ -533,5 +562,7 @@
     if chaos is not None:
-        written.append(resilience_timeline(chaos, out_dir))
+        runs = [chaos] if isinstance(chaos, Run) else list(chaos)
+        for run in runs:
+            written.append(resilience_timeline(run, out_dir))
     return written
```

```diff
--- a/crdblab/cli.py
+++ b/crdblab/cli.py
@@ -516,10 +516,22 @@ def _cmd_report(args: argparse.Namespace) -> int:
         "cluster": args.cluster or _latest_run(runs, "p3_cluster"),
-        "chaos": args.chaos or _latest_run(runs, "p4-chaos-recover"),
     }
+    # One Phase IV figure per fault class, so the default is the most recent run
+    # of *each* class. Defaulting to the recover run alone left the dead-fault
+    # timeline unreachable without an explicit argument, and the figure of it in
+    # ``figures/`` therefore had no invocation that reproduced it.
+    chaos_picks = args.chaos or [
+        run_id
+        for run_id in (
+            _latest_run(runs, "p4-chaos-recover"),
+            _latest_run(runs, "p4-chaos-dead"),
+        )
+        if run_id
+    ]
     for role, run_id in picks.items():
         print(f"  {role:9} {run_id or '(none found)'}")
+    print(f"  {'chaos':9} {'  '.join(chaos_picks) or '(none found)'}")
@@ -527,7 +539,7 @@
-            chaos=load_run(picks["chaos"], runs) if picks["chaos"] else None,
+            chaos=[load_run(run_id, runs) for run_id in chaos_picks],
@@ -754,7 +766,12 @@ def build_parser() -> argparse.ArgumentParser:
-    figs.add_argument("--chaos", help="Phase IV run id (default: most recent recover run)")
+    figs.add_argument(
+        "--chaos",
+        action="append",
+        help="Phase IV run id; repeatable (default: the most recent run of each "
+        "fault class, one figure per class)",
+    )
```

The existing filenames are kept as the constants, so `fig5` still means the dead
fault and `fig6` still means the recover fault; a caption citing either keeps
meaning the same figure. `--chaos` is now repeatable and still accepts a single
value (verified: `--chaos 20260903T004024Z_p4-chaos-dead` renders five figures,
with `fig5` the dead one and no `fig6`).

## Regeneration

```
$ .venv/bin/python -m crdblab report figures
  network   20260902T233208Z_p1-network
  baseline  20260902T233336Z_p2_baseline
  cluster   20260903T000438Z_p3_cluster
  chaos     20260903T003646Z_p4-chaos-recover  20260903T004024Z_p4-chaos-dead

  wrote figures/fig1_network_matrix.png
  wrote figures/fig2_throughput_sweep.png
  wrote figures/fig3_latency_by_operation.png
  wrote figures/fig4_raft_overhead.png
  wrote figures/fig6_resilience_timeline_recover.png
  wrote figures/fig5_resilience_timeline.png
```

The five defaults **are** the five reported Deployment B runs; no argument was
needed. All six PNGs are **byte-identical (SHA-256) to the files that were in
`figures/` beforehand**, which are the ones the previous session inspected.

| file | title | subtitle | footer run ids | PNG width × height | fault drawn as |
|---|---|---|---|---:|---|
| `fig1_network_matrix.png` | `Inter-node round-trip time` | `quorum floor 67.1 ms: no committed write can be faster` | `20260902T233208Z_p1-network` | **3979 × 3312** | n/a |
| `fig2_throughput_sweep.png` | `Steady-state throughput by concurrency` | *(none)* | `20260902T233336Z_p2_baseline`  `20260903T000438Z_p3_cluster` | **3992 × 2930** | n/a |
| `fig3_latency_by_operation.png` | `Latency by operation type (never pooled across types)` | *(none)* | `20260903T000438Z_p3_cluster` | **3975 × 2568** | n/a |
| `fig4_raft_overhead.png` | `Cost of Raft replication, as a throughput-latency curve` | `points at equal concurrency are NOT at equal load` | `20260902T233336Z_p2_baseline`  `20260903T000438Z_p3_cluster` | **3983 × 3066** | n/a |
| `fig5_resilience_timeline.png` | `Throughput through a dead fault on linode-2` | `clock offset measured; fault located exactly` | `20260903T004024Z_p4-chaos-dead` | **3977 × 2764** | **line** |
| `fig6_resilience_timeline_recover.png` | `Throughput through a recover fault on linode-2` | `clock offset measured; fault located exactly` | `20260903T003646Z_p4-chaos-recover` | **3977 × 2764** | **line** |

Footers are rendered as `source: <ids>`; the ids are given above without that
prefix. In fig2 and fig4 the two ids are separated by two spaces, as rendered.
The `ff` in "offset"/"offered" is the `uniFB00` ligature glyph.

Other rendered strings, for the record: fig1 axis labels `destination` / `source`
and colourbar `mean RTT (ms)`, cells row-major `- 79 222 198 227 / 79 - 199 200
155 / 222 199 - 18 73 / 198 200 18 - 67 / 227 154 72 67 -`; fig2 axes
`offered concurrency (workers)` and `throughput (ops/s), summed across operation
types`, legend `p2_baseline` / `p3_cluster`; fig3 panels `read` / `update`, axes
`concurrency` / `latency (ms)`, legend `p50` / `p99`; fig4 annotations `C=1`,
`C=50`, `C=200`, `C=1`, `C=100`, `C=200`, band caption `comparable at matched
throughput (1707-1850 ops/s)`, threshold ` quorum floor 67 ms`, legend `phase II
single node` / `phase III cluster`; fig5 and fig6 axes `time since run start
(harness clock, s)` / `throughput (ops/s)`, thresholds ` recovery floor 1513
ops/s ` and ` recovery floor 1589 ops/s `, legends `throughput` / `fault (dead)`
/ `performance RTO 12.0 s` and `throughput` / `fault (recover)` / `performance
RTO 4.4 s`.

### Specifically confirmed

**No caption states the quorum floor as 66.9 ms.** Grepping every extracted glyph
string across all six PDFs for `66.9` returns nothing. The only floor statements
in any figure are `quorum floor 67.1 ms: no committed write can be faster`
(fig1) and ` quorum floor 67 ms` (fig4, `{:.0f}` at `figures.py:416`). The two
`recovery floor` labels are throughput thresholds, not quorum floors.

**`fig5` still sources the `dead` run and `fig6` the `recover` run.** fig5's
footer reads `source: 20260903T004024Z_p4-chaos-dead` and its legend `fault
(dead)`; fig6's footer reads `source: 20260903T003646Z_p4-chaos-recover` and its
legend `fault (recover)`.

**Both draw the fault as a line, and that is correct.** The legend string
`fault (<mode>)` is emitted only on the `alignment.exact` branch
(`figures.py:496-500`); the band branch would read `fault, located to within N s`.
Both runs report `clock_alignment.method = "measured"`, `uncertainty_s = 0.0`.

### The default chaos pick

**It no longer selects only the recover run.** `cli.py:521-530` now takes the
latest run of each fault class. Before this change,
`args.chaos or _latest_run(runs, "p4-chaos-recover")` picked
`20260903T003646Z_p4-chaos-recover` alone, so — as the verification observed —
*neither* Phase IV figure in `figures/` was what a default invocation produced:
fig5 required an explicit `--chaos`, and fig6 had no code path at all.

**The regenerated set now matches what a default invocation produces, exactly.**
It is what a default invocation produced: the table above was generated by
`crdblab report figures` with no arguments, and every byte of all six PNGs
matches the previously delivered set.

---

# Part 5 — Residuals

## 5.1 Deployment A Phase II drift, latency

`runs/20260902T175621Z_p2_baseline`, `drift("20260902T175621Z_p2_baseline",
"update:p50_ms")`:

| quantity | slope %/tier | total % across 21 tiers | r |
|---|---:|---:|---:|
| throughput | `-0.019714681186675395` → **−0.0197** | `-0.3942936237335079` → **−0.394** | **−0.1232** |
| **update p50** | `0.05845834...` → **+0.0585** | `1.1691668...` → **+1.169** | **+0.2519** |
| read p50 | −0.0203 | −0.405 | −0.1371 |
| weighted p50 | +0.0125 | +0.249 | +0.0778 |

**Deployment A's baseline is flat on both quantities.** Its update p50 drifts
*upward* by 1.17% across the whole sweep at r = +0.25 — a twenty-seventh of the
reported sweep's −31.1%, and in the opposite direction. Side by side:

| | throughput | update p50 |
|---|---|---|
| A Phase II | −0.394%, r −0.123 | +1.169%, r +0.252 |
| **B Phase II (reported)** | **+12.818%, r +0.630** | **−31.113%, r −0.619** |

The two baselines are not two samples of the same behaviour. One is stable on
both axes; the other is the only sweep in the artefact set that moves at all.

## 5.2 Repetition independence

**No. Within the reported Phase II sweep the three repetition means are not
independent of one another, and the data says so without appeal to principle.**

`steady_state.per_tier`'s docstring justifies the interval over three repetition
means rather than over pooled per-second samples: "Successive samples within one
run are not independent -- they share a process, a cache state and a thermal
state." The same objection now applies one level up.

**In all seven tiers, the earliest-positioned repetition is the lowest of the
three.** Normalised values by within-tier position rank:

| C | rank 1 (earliest) | rank 2 | rank 3 (latest) |
|---:|---:|---:|---:|
| 1 | **0.857713** | 1.075522 | 1.066766 |
| 2 | **0.875176** | 1.050121 | 1.074703 |
| 5 | **0.931587** | 1.022989 | 1.045424 |
| 10 | **0.907517** | 1.053349 | 1.039134 |
| 50 | **0.953270** | 0.995051 | 1.051678 |
| 100 | **0.990494** | 1.012617 | 0.996889 |
| 200 | **0.986238** | 1.011362 | 1.002400 |

Under exchangeability the earliest repetition is the smallest with probability
1/3, independently per tier, so seven for seven has probability
`(1/3)^7 = 1/2187 = 0.000457`. A two-sided permutation test on the pooled
regression (200,000 shuffles of the normalised values against position) gives
**p = 395/200000 = 0.001975** for |r| ≥ 0.6298.

The same test on the other three sweeps finds nothing: earliest-is-lowest in
1/7, 1/7 and 4/7 tiers for A Phase II, B Phase III and A Phase III respectively;
Spearman of value rank against within-tier position rank −0.231, −0.222, +0.260,
against **+0.732** for the reported sweep.

**Consequence for the reported intervals.** The ±523 at C=1 and ±812 at C=2 are
not repetition variance; they are the width of a step measured once on each side
of it. Where a tier's three repetitions straddle position 11 the half-width is
large (C=1 ±523, C=2 ±812, C=10 ±709, C=50 ±437); where they do not, it collapses
(C=100 ±97, all three pre-step). The interval is doing its job — it is reporting
that the three repetitions disagree — but a reader who takes it as sampling
error around a stable tier mean will draw the wrong conclusion, because the
disagreement is systematic in time.

## 5.3 Cooldown

**Recorded as applied; not independently measured. The gaps are consistent with
it and vary by 2.481 s.**

`runs/20260902T233336Z_p2_baseline/manifest.json` → `profile.workload.cooldown_s`
= **15**, copied verbatim from `profiles/thesis-extended.yaml:47`. The harness
sleeps on a monotonic deadline between tiers (`bench.py:402-407`), skipping it
after the last tier.

Gaps between consecutive tiers, on the harness clock (`wall_offset_s`; `ts_utc`
is the row-write time, § 1.1):

| measure | n | min | max | mean | sd | spread |
|---|---:|---:|---:|---:|---:|---:|
| last retained sample → next tier's first retained sample | 20 | **33.144 s** | **35.625 s** | 34.318 s | — | **2.481 s** |
| generator start → next generator start | 20 | **87.140 s** | **89.621 s** | 88.330 s | 0.743 s | **2.481 s** |

Phase III, same plan and order, for comparison: sample-to-sample gap
35.651–39.360 s (spread 3.709 s); start-to-start 89.669–93.245 s, mean 91.182 s,
sd 0.858 s.

Every gap is **more than twice** the declared cooldown, and there is no artefact
that decomposes it. Between the last retained sample of one tier and the first of
the next lie: the tail of the final generator interval, the row-match probe and
its SQL count, the 15 s cooldown, generator start-up, and the 6 s of the next
tier's warmup that is discarded at write time. **The cooldown's own duration is
`NOT FOUND` as a measured quantity.** What the artefacts support: the cooldown is
recorded in the profile, the harness code applies it, and every observed inter-tier
gap is comfortably longer than it — which is consistent with 15 s having elapsed
and does not, on its own, prove it.

Gaps vary by **2.481 s** across the sweep (Phase II) and **3.709 s** (Phase III),
about 2.8% and 4.1% of the mean. The variation is unstructured with respect to
position (§ 1.1's start-time collinearity of 0.99999891 is what it leaves behind).

## 5.4 Phase II tier order versus Phase III tier order

**They are the same sequence — and so is every other sweep in the study.**

```python
tier_order(load_run("20260902T233336Z_p2_baseline")) \
    == tier_order(load_run("20260903T000438Z_p3_cluster"))     # True
tier_order(load_run("20260902T175621Z_p2_baseline")) \
    == tier_order(load_run("20260902T195644Z_p3_cluster"))     # True
tier_order(load_run("20260902T233336Z_p2_baseline")) \
    == tier_order(load_run("20260902T175621Z_p2_baseline"))    # True
```

All four manifests record the identical 21-element note:

```
[(100, 3), (100, 1), (1, 3), (50, 1), (5, 2), (200, 2), (2, 3), (50, 3),
 (200, 1), (100, 2), (10, 3), (10, 2), (2, 1), (50, 2), (5, 1), (5, 3),
 (1, 2), (2, 2), (1, 1), (10, 1), (200, 3)]
```

This is not coincidence and it is not a defect. `bench.py:179-180` shuffles with
`random.Random(spec.seed)` and `spec.seed` is the profile's `seed: 42`
(`config.py:79`, `profiles/thesis-extended.yaml`), so the "randomised" order is
deterministic and identical for every sweep run from that profile.

**This makes the § 1.3 comparison much stronger than it would otherwise be.**
The four sweeps share the plan, the order, the position-to-concurrency mapping,
the cooldown and the workload. Position 11 is the same tier in all four. A trend
present in one and absent in three therefore cannot be an artefact of the
ordering — the ordering is a constant. It is a property of that sweep.

It also means the § 2.3 confound (`corr(mean position, log C) = −0.5829`, C=100
entirely in the first half) is **structural, not incidental**: every sweep in
this study measures C=100 in the first half and C=1 in the second, so any future
sweep from this profile will inherit exactly the same vulnerability. That is
worth saying in a limitations section, because "we randomised the tier order" is
true only in the sense that one draw was randomised, once, and then frozen.

## 5.5 Anything else bearing on quoting the tier means as tier means

1. **The sweep passes every check the project has.** `validate()` returns
   `ok = True` with zero findings; `preflight.json` reports 22 of 22 passed. The
   step is invisible to both. This is the project's own thesis about itself —
   individually plausible numbers, no check that looks for structure across
   tiers — and it now has an instance in the reported data rather than only in
   `docs/defects.md`.
2. **The step is not a warm-up ramp and cannot be trimmed by a longer warmup.**
   The within-tier slope is uncorrelated with position (r = −0.0379) and no
   single tier's internal trend exceeds |r| = 0.44. Position 0 through 10 are
   internally stable and 9.6% below position 11 through 20, which are also
   internally stable. A larger `warmup_s` would have changed nothing.
3. **C=100 is measured on one side of the step only.** All three repetitions at
   positions 0, 1, 9. Its tier mean is the only one in the sweep that is not an
   average across the two regimes, which is why its interval is 5× narrower than
   its neighbours' and why its value sits below both. No repetition of C=100
   exists after position 9, so **the post-step throughput at C=100 is
   `NOT FOUND`** — it was not measured.
4. **The host columns cannot corroborate or contradict any of this.** They carry
   21 samples per phase, one per tier, taken at tier end (verification Part 3 § 8
   finding 1, re-confirmed here: `nunique()` is 1 for all three columns in both
   runs). There is no within-tier host observation to place against the step.
5. **Phase IV records no host metrics at all**, so the two chaos runs — which ran
   after this sweep on the same deployment — cannot be used to say whether
   whatever changed at 23:50 UTC was still in effect.
6. **There is no second, independent throughput figure to test the step
   against.** `manifest.json` → `generator_totals` is present but **empty**
   (`{}`) in both reported sweeps, and the workload-parsing rules
   (`crdblab/core/workload.py`, D3) keep the generator's summary block out of
   the tick stream by design. `metrics.csv` is the only record of per-tier
   throughput that exists.
7. **Nothing in `runs/` records the state of the host between tiers.** No
   compaction log, no store statistics, no `cockroach` internal timeseries, no
   second process's CPU. `grep -rlE "compaction|rebalance|teardown" runs/`
   matches no file. The step is unexplained and, from these artefacts,
   unexplainable.

---

# Part 6 — Output

## Verdict

**The reported Phase II sweep's tier means are partly measurements of position in
the sweep, and how badly depends entirely on the tier.** The sweep contains a
single, sharp, unexplained step between its eleventh and twelfth tiers — sixteen
minutes into thirty-one — after which throughput is 9.6% higher and update
latency about a third lower, with the two groups not overlapping at any tier. The
step's size falls monotonically with concurrency, from +24.9% throughput and
−36.9% update p50 at C=1 to +0.4% and −0.6% at C=200, which is the signature of a
change in write service time rather than in capacity. So the **high-concurrency
tier means are quotable as measurements of concurrency**: C=200 moved by 0.36%
across the step and its interval (±110) is honest sampling variation; the
saturation classification, the peak's location at C=50, the plateau, and the
Phase III comparison at matched throughput all survive both detrending models
unchanged. The **low-concurrency tier means are not quotable as tier means
without their intervals and a statement of what the intervals contain**: the C=1
throughput mean averages one pre-step reading with two post-step readings, its
±523 ops/s is a step width and not a sampling error, and its update p50 —
`1.4157575757575758` ms, the denominator of the headline 50.38× ratio — is the
mean of `1.878` ms and two readings near `1.18` ms, values that would individually
yield ratios of 37.98× and 60.91×. Position and elapsed time are collinear at
r = 0.9999989 in this sweep, so there is no way to separate the two from the data;
and because the tier order is seeded at 42 and identical in all four sweeps, the
one place the confound bites hardest — C=100 measured entirely before the step —
is a structural property of the profile rather than an unlucky draw. My
recommendation: quote C=50 and above as measurements; quote C=1 and C=2 only with
the interval, the three repetition values and their sweep positions given
alongside; and quote the 50.38× ratio only with the 37.98×–60.91× range attached.

## Figures whose reported value changes as a result of Parts 1–3

Changes to *values*. Everything else in this document changes what a number
means, not what it is.

| # | figure | old | new | cause |
|---|---|---|---|---|
| 1 | Equal-concurrency throughput ratio, C=1 (`same_concurrency_delta`) | **25.26×** | **25.25×** | Part 3.1. Unrounded `25.252019183362467` |
| 2 | Equal-concurrency throughput ratio, C=2 | **22.42×** | **22.43×** | Part 3.1. Unrounded `22.428678070695323` |
| 3 | Matched-utilisation overhead at 84% | **67.31×** | **67.32×** | Part 3.2 |
| 4 | Matched-utilisation overhead at 90.8% | **47.92×** | **47.95×** | Part 3.2 |
| 5 | Matched-utilisation overhead at 96.6% | **46.27×** | **46.24×** | Part 3.2 |
| 6 | Matched-utilisation overhead at 98.3% | **48.55×** | **48.88×** | Part 3.2 |
| 7 | Matched-utilisation overhead at 99.6% | **31.07×** | **30.74×** | Part 3.2 |
| 8 | Matched-utilisation point count | **8 points, 0.843–1.0** | **9 points, 0.479–1.0** | Part 3.2 — the level defining `utilisation_range[0]` was being rounded below its own lower bound |
| 9 | Matched-utilisation overhead range | **"67.31× at 84% to 8.16× at 100%"** | **"56.25× at 47.9% to 8.16× at 100%", with 67.32× at 84.3% the maximum** | Part 3.2. The 8.16× minimum is unchanged; the new lowest-utilisation point is not the maximum |
| 10 | Phase II side of the 84.3% utilisation point | **3003.9 ops/s, interpolated** | **3004.5 ops/s, the measured C=2 tier** | Part 3.2 |
| 11 | Full span across all four framings | **8.16 to 112.38** | **unchanged, 8.16 to 112.38** — but the matched-utilisation contribution now spans 8.16–67.32 rather than 8.16–67.31 | Part 3.2 |
| 12 | Within-sweep drift, "−0.4% across 21 tiers, r = −0.12" as a general finding (`docs/defects.md:477, :531`) | **−0.4%, r −0.12** | **True of `20260902T175621Z_p2_baseline` only. The reported sweep is +12.818%, r +0.630 on throughput and −31.113%, r −0.619 on update p50** | Part 1.3 |
| 13 | Little's law, Phase II @ C=1, per-tier method (verification claim 2.11) | **1.9868% → 2.0%** | **`1.9868%` from the 3 dp columns; `1.9025%` unrounded → 1.9%. Quote as "about 2%"** | Part 3.3, site 2 |
| 14 | Phase IV figure provenance | **fig6 not producible by `crdblab report figures`; neither Phase IV figure produced by a default invocation** | **Both producible; both produced by the no-argument default; regenerated set byte-identical to the delivered one** | Part 4 |
| 15 | Test suite size | **100** | **102** | Parts 3.1, 3.2 |

Values explicitly checked and **unchanged**: the unqueued ratio `50.38` and its
unrounded `50.37970890410959`; every `read_p50_ratio_x` and `update_p50_ratio_x`;
the matched-throughput block in full (`overlap_tps [1707.3, 1849.5]`, overheads
73.75 / 74.4 / 112.38, utilisation gaps 0.444 / 0.453 / 0.481, `least_confounded`
at 1707.3); `saturation` for both phases; Phase II peak `3563.335` at C=50 and
Phase III peak `1849.548` at C=100; every field of `analyze steady-state` for both
runs; every resilience output; all six figure images.

## What I decline to state from these artefacts

- **Any cause for the step.** Nothing in `runs/` records the host's state between
  tiers — no compaction activity, no store statistics, no second process. The
  three host columns do not track it (§ 1.4), and `gateway_rss_bytes` rises the
  same way in Phase III, which has no step. Naming a mechanism would be a
  plausible reconstruction, which is exactly the move this project exists to
  document; "the store had finished compacting" is the kind of sentence that
  reads as a finding and is worth nothing.
- **A detrended tier mean, ratio, peak or interval as a measurement.** Every such
  figure in Part 2 is labelled `[DIAGNOSTIC]` and belongs in an exposure
  discussion, never in a results table. In particular the detrended headline ratio
  of 49.41× is *worse* than useless as a point estimate, because it averages
  across a step and lands where nothing was measured.
- **The post-step throughput or latency at C=100.** All three of its repetitions
  are pre-step. `NOT FOUND`.
- **The cooldown's actual duration.** 15 s is recorded in the profile and the code
  applies it; the artefacts give only a composite 33.1–35.6 s inter-tier gap that
  contains it along with four other unrecorded intervals. `NOT FOUND`.
- **Which of the two baseline sweeps is representative.** They disagree, the
  disagreement is large, and there are two of them. `docs/defects.md`'s claim that
  "the variation is between deployments rather than during one" is contradicted by
  the reported sweep, but two sweeps do not establish which behaviour is typical
  and I will not assert one.
- **That the step is confined to this sweep in time.** Phase III started
  `2026-09-03T00:04:38.430737Z`, **12.6 s** after Phase II finished
  (`2026-09-03T00:04:25.812180Z`), on the same deployment, and shows no drift —
  but Phase III measures a different system on a different host (the AMD gateway,
  not the Intel baseline node), so its flatness says nothing about whether the
  baseline host stayed in its post-step state. Phase IV records no host metrics
  at all. Unanswerable from `runs/`.
- Everything the previous session declined (working-set size, non-gateway hardware,
  per-interval host utilisation, a definition of "saturated tiers", run-to-code
  provenance) still stands declined, for the same reasons.

## Revisions and test count

| | |
|---|---|
| Started from | `119e2448a839f4a2e746afc46b83ea4b687cdf76` (`master`) |
| Finished at | **`79be60c25a95ddc57f7537b9dbe6d006beb38b13`** (branch `drift-and-corrections`) — all code and test changes, and the revision every number here was produced at. This document is the commit on top of it, whose hash it cannot state |
| Files changed | `crdblab/analysis/raft_overhead.py`, `crdblab/cli.py`, `crdblab/report/figures.py`, `tests/test_analysis.py` |
| Files under `runs/` changed | **none** |
| Tests before | **100 collected, 100 passed** |
| Tests after | **102 collected, 102 passed** |
| `ruff check crdblab tests` | 57 errors before, 57 after — identical set, none introduced |
| `analyze steady-state` tiers | byte-identical before and after, both reported runs |

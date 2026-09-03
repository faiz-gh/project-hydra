# Resolutions to `research-gaps.md`

One answer per gap, in the register's own order. Every numeric answer was
recomputed from the retained run artefacts rather than taken from any prose
source; the command or file that produced it is named.

**Six gaps are now closed that the register recorded as open or blocking**, three
of them because the material the register was given was incomplete rather than
because a judgement was needed:

| ID | Was | Now |
|---|---|---|
| D1 | Blocking — three values for the quorum floor | **Closed.** One value; the other two are different statistics |
| D2 | Blocking — two Little's-law figures | **Closed.** Two legitimate computations; one nominated |
| D3 | Blocking — three unqueued ratios | **Closed.** Code defect found and fixed; one value |
| D4 | Blocking — tables disagree | **Closed.** Same measurement, double-rounded |
| D5 | Reconcile — two cluster peaks | **Closed.** Data pack correct, other material wrong |
| D6 | Reconcile — audit resolution differs | **Closed.** The difference is a *result*, not an inconsistency |
| D7 | Reconcile — no database version in Phase I | **Closed.** Not collected by that phase |
| A1 | Blocking — verdict has no source | **Closed.** Source identified: a retained run |
| A2 | Blocking — RQ1 has no evidence | **Partly closed.** Non-trivial evidence exists; timings do not |
| A3 | Blocking — Chapter 5 figures unavailable | **Decision given** (option 1, with a provenance rule) |
| B2 | Constrains — no utilisation data | **Substantially wrong.** Full gateway CPU/RSS/IOPS series exist |
| C2 | Constrains — no partition figure | **Closed.** Figure generated |

---

# Chapter 3

## C1 — Section 3.6 has almost no source material

**Your call, and the register's handling is right.** No threat model, security
testing or attack-surface analysis was performed, and none can be reconstructed.

Three further facts are available and may be worth adding, all of which are
design decisions rather than evaluations:

- The overlay is WireGuard, so node-to-node traffic is encrypted and
  authenticated in transit even though CockroachDB itself runs `--insecure`.
- CockroachDB binds `--listen-addr` and `--advertise-addr` **exclusively** to the
  overlay address, so the SQL and RPC ports are not reachable on any provider's
  public interface. This is verifiable from the recorded server start command in
  every manifest, e.g. `--listen-addr=100.96.175.102:26257`.
- SSH uses `StrictHostKeyChecking=no`, deliberately, because the testbed is
  destroyed and rebuilt repeatedly and overlay addresses are reused. It is
  disclosed as a testbed-only posture.

That takes 3.6 to roughly 250 words of defensible statement. It still evaluates
nothing, and the closing sentence should say so.

---

# Chapter 4

## C3 — Four phases presented as five

**Correct as handled.** No change. The constraint stands for Chapters 6 and 7.

## D6 — Audit resolution differs between the two chaos runs

**Closed, and the difference supports §4.6 rather than undermining it.**

Recomputed from `audit.csv` in each run (445 and 385 acknowledged writes):

| Run | Median gap, whole run | Pre-fault | Post-fault |
|---|---|---|---|
| recover (partition) | 401.0 ms | 392.6 ms | 403.5 ms |
| dead (process kill) | 467.0 ms | 404.7 ms | 480.2 ms |

**The two runs have effectively identical pre-fault cadence** — 392.6 ms against
404.7 ms, within 3%. They diverge *after* the fault, and only in the `dead` run,
where the gap rises 18.7%.

The reason is the quorum geometry. Killing `crdb-linode-2` removes the
second-fastest follower permanently, so the write path's floor rises from
67.1 ms to 198.2 ms and every subsequent audit write costs more. The partition
run heals after 45 s and the node rejoins, so its cadence barely moves.

The reported per-run resolution is computed over the whole run, which is why the
`dead` figure is pulled up to 0.47 s.

So §4.6's claim is *confirmed*: the cadence is bounded by the cost of a quorum
write, and when the quorum geometry changes the cadence changes with it. This is
worth one sentence in 4.6 and one in 6.4 — it is a small piece of positive
evidence that the audit instrument behaves as described.

## D7 — Phase I records no database version

**Not a gap in the record; the phase does not collect it.**
`crdblab/phases/p1_network.py` never calls `capture_server_config`, which is the
function that records `cockroach_version`. Every other phase does.

Note the probe *does* query the database — it asserts leaseholder placement — so
"the network phase does not touch the database" would be wrong. The accurate
statement is that the version is not captured by that phase.

All four other runs of the same deployment record **v26.3.0**, and all five nodes
were provisioned by one Terraform apply from one image specification. 4.2 can
state the version for the deployment and note it is recorded in the Phase II–IV
manifests rather than the Phase I one.

---

# Chapter 5

## A3 — Chapter 5's figures are not in the results data

**Option 1, with a provenance rule attached.** Treat `defects.md` as authoritative
for Chapter 5. But the reason matters, and it is not the one the register gives.

The register's justification — "its figures were generated from the same run
artefacts by the same analysis layer" — is **true of only some of them**. The
defect figures fall into two classes, and the distinction should be stated in the
text because this is a dissertation about provenance:

**Class 1 — derived from retained, validated artefacts.** The within-sweep drift
regression (−0.4% across 21 tiers), row-match and statement-execution counts, and
the reproducibility comparison. These are recomputable on demand and are of the
same standing as anything in the data pack.

**Class 2 — diagnostic observations of configurations that were never retained
as validated runs.** The 12.3× leaseholder cost, the ~20× seed-mismatch
overstatement, the 18.3× → 12.8× cache-asymmetry revision. These were measured
*while the system was misconfigured*. Those runs were, correctly, never promoted
to results: they fail pre-flight by construction. **They cannot be regenerated,
because the configurations that produced them were fixed.**

Class 2 is not weaker evidence for the argument Chapter 5 makes — it is the
*only possible* evidence for it, since the whole claim is that a defective
configuration produced attractive numbers. But it should be introduced as what it
is: contemporaneous diagnostic measurement, recorded at the time of discovery,
not a validated run.

Suggested wording for the first use in 5.3: *"Figures in this section describing
defective configurations are contemporaneous diagnostic measurements taken at the
time of discovery. The configurations that produced them were corrected, so
unlike the results of Chapter 6 they are not reproducible from a retained run;
they are reported here because the argument concerns precisely what a defective
configuration reports."*

That converts a sourcing weakness into a demonstration of the chapter's own
thesis.

## B5 — Row-match corroboration does not cover reads

**Correct as stated.** Two additions worth carrying:

- It applied to **one tier out of 21** in the reported Phase III run
  (`window: "flushed; corroborated by quorum floor"`), and one further tier used
  the partial-window fallback. The other 19 were measured over a clean interval.
- The asymmetry is in the physics, not in the tolerance: an unreplicated baseline
  has no quorum round trip to check a write latency against, which is why the
  corroboration is unavailable in Phase II and the assertion stays fatal there.

Quantifying it as 1-in-21 makes it a bounded limitation rather than an open one.

---

# Chapter 6

## D1 — Three values for the quorum floor

**Closed. There is one value; the other two are different quantities.**

From `runs/20260902T233208Z_p1-network/preflight.json` and `network.csv`:

| Figure | What it actually is |
|---|---|
| **67.054 ms** | **The derived quorum floor.** The *mean* RTT from the gateway to `crdb-linode-2`, the second-fastest follower |
| 67.1 ms | The same figure to one decimal place |
| 66.900 ms | The *median* RTT of that same link — a different statistic, not a competing floor |
| 66.925 ms | **Deployment A's** quorum floor. This is the source of "66.9" in older material |

**Use 67.054 ms as the derived figure and 67.1 ms in prose.** The figure captions
reading "67.1 ms" and "67 ms" are both correct roundings and need no regeneration;
only make sure no caption says 66.9.

Any occurrence of 66.9 ms as *this deployment's* floor is a Deployment A value and
is wrong.

## D2 — Little's law agreement reported twice, differently

**Closed. Both are correct; they are different computations of the same idea.**

For Phase II at one worker:

- **1.99% (≈2.0%)** — the per-tier table. Computes each repetition's own
  `N/X` and weighted median, then averages. Same method as all 14 rows.
- **3.02% (≈3.0%)** — the unqueued section. Computes `N/X` from the *tier-mean*
  throughput against the tier-mean weighted median.

These differ because the mean of a ratio is not the ratio of the means. Neither
is wrong.

**Recommendation: quote 2.0%**, and use the per-tier method wherever the figure
appears, because that is the method behind every other tier in the table and a
reader comparing rows will otherwise find one row computed differently. Phase III
at one worker on the same method is available on request if you need it beside
the Phase II figure.

## D3 — Unqueued write-latency ratio reported three ways

**Closed — and this one was a real defect in the analysis code, now fixed.**

The exact ratio is **50.3797**, from update p50 medians of 71.32545 ms and
1.41576 ms.

- `50.38x` — correct, 2 dp of the exact ratio.
- `50.37x` — **wrong.** `lightest_load_write_latency` rounded both medians to
  3 dp for display and then divided the *rounded* values, carrying the rounding
  error into the result.
- `50.4x` — correct, 1 dp.

Fixed in `crdblab/analysis/raft_overhead.py`: the ratio is now computed from the
unrounded medians, with a regression test pinning it. The function now returns
`50.38`, agreeing with the equal-concurrency table.

**Use 50.4x in prose and 50.38x where two decimals are wanted.** The headline
range 33.4x–50.4x is unaffected.

## D4 — Matched-utilisation table disagrees with the tier table

**Closed. Same measurement, presented through two roundings.**

The 84% row of the matched-utilisation table *is* the C = 2 tier. Interpolation
lands within 0.6 ops/s and 0.0002 ms of the measured tier:

| | Utilisation | Throughput | Update p50 |
|---|---|---|---|
| C = 2 tier (measured) | 0.84318 | 3004.532 ops/s | 1.48485 ms |
| Matched-utilisation row | 0.843 | 3003.9 ops/s | 1.485 ms |

The 1.49 arose from rounding 1.48485 to 1.485 for display and then rounding that
to two decimals. **The correct two-decimal value is 1.48 ms.** The data pack has
been corrected.

## D5 — Deployment A cluster peak differs across files

**Closed. The data pack is right; the other material is wrong.**

Exact value: **1791.463 ops/s** → 1,791 at zero decimals, 1,791.5 at one. The
1,792 appearing elsewhere is the same double-rounding error as D4.

**Use 1,791 ops/s.**

> D3, D4 and D5 share one root cause: a value rounded for display and then used
> in a further computation or rounded again. It is worth noting that this is the
> same class of error the dissertation is about — a defect that produces a
> plausible number — and it was caught only because a reader compared two tables.
> If Chapter 5 wants a live example, this is one.

## D8 — Matched-throughput values are interpolated

**Correct as stated, and the qualification is narrower than it looks.** Of the
three points in the overlap band, the values at 1,742 and 1,850 ops/s are *at*
measured Phase III tiers; only the Phase II side is interpolated, and Phase II's
update median is nearly flat across that range (1.42 ms at every point). The
qualification should still travel with every quoted figure, but the practical
exposure is small and can be said so.

## B1 — Availability RTO below instrument resolution

**Correct as handled.** See D6 for why the two resolutions differ; that
strengthens rather than weakens the treatment.

## B2 — No resource-utilisation data exists

**This is substantially wrong for the reported deployment, and Chapter 6 can have
a utilisation series.**

The register's statement is true of the *legacy* exports, where `ram_pct` was a
constant zero. It is not true of the runs being reported. Verified directly from
`metrics.csv`:

| Phase | Intervals | `gateway_cpu_pct` | `gateway_rss_bytes` | `gateway_disk_iops` |
|---|---|---|---|---|
| II | 1,155 | 40.9–81.8% | 2.09–2.63 GB | 0–14,985 |
| III | 1,154 | 8.8–74.2% | 2.12–2.54 GB | 0–3,215 |

All three columns are non-null in **every** interval of both runs. The metric is
CockroachDB's own `sys_cpu_combined_percent_normalized`.

Two caveats, both real and both statable:

1. **It is host-wide, not process-wide.** In Phase II the generator runs on the
   same node as the server, so the figure includes both. This is why the column
   is named `gateway_*`.
2. **In Phase III it is the gateway only**, not a cluster aggregate. It says
   nothing about the four non-gateway nodes.

**Consequence:** the claim that the baseline is CPU-bound can now rest on a
measurement — Phase II sits at 76–82% host CPU across its saturated tiers — and
not only on the shape of the throughput curve. The claim that the *cluster* is
network-bound is supported by its gateway sitting far lower at the same tiers,
and by the write median being invariant at 71.33 ms from C = 1 to C = 5.

I would treat this as one of the more valuable corrections in this document.

## C2 — No figure for the healed-partition run

**Closed. The figure has been generated** from the retained run directory:
`figures/fig6_resilience_timeline_recover.png` (and `.pdf`), 4K, sourced from
`20260903T003646Z_p4-chaos-recover`.

It renders the fault as a **line** rather than a band, because that run has a
measured clock alignment, and marks the 4.4 s performance RTO. Chapter 6 can now
present both fault classes as figures.

---

# Chapter 7

## A1 — The "failed to recover" verdict has no source

**Closed. It is candidate 2 in the register's list, and it has a retained source:
`runs/20260902T022406Z_p4-chaos-dead`.**

This is a real measured outcome in this project, not legacy output and not an
unshared document. A `dead` fault against `crdb-linode-2` at **C = 50**:

| Quantity | Value |
|---|---|
| Pre-fault baseline | 1,766.52 ops/s |
| Recovery floor (80%) | 1,413.22 ops/s |
| Recorded `rto_s` | `None` — no recovery within the run |
| Throughput after the fault | settled at a stable **1,195 ops/s**, ≈ 67.6% of baseline |

The analysis layer's own verdict on that run: *"performance RTO is undefined for
this fault: throughput settled at a stable 1195 ops/s, below the 1395 ops/s
floor, and stayed there. This is a new stable state, not a slow recovery — the
metric does not apply while the node is down."*

**The reconciliation 7.3 should make:** the verdict was correct as an
observation and wrong as a description. Killing a member of the low-latency
triangle raises the write path's floor from 67.1 ms to 198.2 ms — a factor of
2.96 — because the next-fastest surviving replica is in South Asia. Whether
aggregate throughput regains an 80% threshold then depends on how much of that
added latency the offered concurrency can hide. At C = 50 it could not; at
C = 100 it could, recovering in 12.0 s. Both outcomes were measured **on the same
fault against the same target**.

So the cluster did not fail to recover. It moved to a new stable state at a
higher write-path floor, never stopped accepting writes, and lost no acknowledged
write. Performance RTO is *undefined* there rather than infinite, and reporting
it as "failed to recover" describes a healthy cluster as a broken one.

Two caveats to carry: that run is from an earlier deployment and used the `smoke`
profile, so it is a supporting observation rather than a headline Phase IV result;
and it is schema 2.0, so its clock alignment is bounded rather than measured.
Neither affects the argument, which turns on the throughput plateau and the
quorum geometry.

## B3 — Deployment A's baseline hardware never captured

**Correct as stated, and it is the right thing to flag as the weakest link.**
Confirmed: `20260902T175621Z_p2_baseline`'s manifest has no `host:` note. The
matching processor model and memory come from a live probe recorded in a session
log, not in a run artefact.

The honest form: *Deployment B records the machine on both sides; Deployment A's
Phase II manifest does not, because the capture was added in response to that
gap. A probe taken before the instance was destroyed returned the same processor
model and total memory, but that is weaker evidence than a manifest entry.*

## B4 — Window length confounded with time of day

**Correct as stated. Not separable from the retained data.** No change.

## B6 — Two deployments bound the variance loosely

**Correct as stated.** One addition available if useful: the *within-sweep* drift
was measured and is negligible — regressing each tier's throughput on its
position in the randomised order gives −0.4% across 21 tiers. So the variance is
demonstrably *between* deployments rather than drift during one, which is a
stronger statement than "two draws" alone.

## B7 — Compared phases ran on different processor architectures

**Correct as stated**, with one precision worth adding: they differ **because
they run at different providers**. The baseline is a GCP instance (Intel Xeon
@ 2.80 GHz) and the cluster gateway a Linode one (AMD EPYC 7713). This is
recorded in both Deployment B manifests.

Note the baseline node's locality label is `region=self-hosted`, which describes
its role as an isolated single-node server, not its location. If Chapter 3's
topology table gives a provider for that node it must say GCP.

---

# Chapter 8

## A2 — RQ1 has no quantitative evidence

**Partly closed. There is more evidence than the register found, though no
timings.**

**What exists.** Three complete, independent deployments from the same Terraform
codebase, each provisioning genuinely new instances — evidenced by three Phase I
runs with three disjoint sets of overlay addresses (the baseline node was
`100.103.70.41`, then `100.70.55.65`, then `100.96.175.102`). Each deployment
produced a cluster that passed the *same* pre-flight assertions: five nodes live,
clocks within tolerance, `lease_preferences` non-empty and naming the intended
localities, and leaseholders resident in the gateway's own region. The final
deployment passed 22/22 pre-flight checks in Phase II and 45/45 in Phase III.

That is a reproducibility claim with an assertion behind it, not an anecdote: the
bootstrap fails closed, exiting non-zero if the zone configuration does not
apply, so "it came up correctly three times" is a checked property rather than an
impression.

**What does not exist, and cannot be recovered.** Apply wall-time, a re-apply
producing no changes, drift detection, teardown time. State is held in HCP
Terraform and the testbed is destroyed; no local `.tfstate` or plan artefacts
remain in the repository.

**Recommendation.** Answer RQ1 on *reproducibility*, which is evidenced, and
state explicitly that *idempotency* in the strict sense — a repeated apply
converging with no changes — was not measured. That is an honest split and it
gives RQ1 a result rather than only a limitation. Chapter 8 should list the
timing measurements as future work.

## C4 — No cost, scale or alternative-topology data

**Correct as stated.** No change.

---

# References

## E1 — Three citations cannot be sourced academically

Handling is right. On the third item specifically: **do not cite a NIST SP 800-34
revision number from memory.** The RTO/RPO definitions are also given in
ISO 22301 and in ISO/IEC 27031, and which your institution prefers may matter.
Whichever you choose, take the revision, year and section from the document
itself.

For the two vendor sources, cite the specific page rather than the product root,
and record the access date — this project's own argument about provenance makes
a vague vendor citation conspicuous.

## E2 — Bibliographic detail outstanding

**Correct, and the reasoning is exactly right.** Complete volume, issue and pages
from the PDFs.

On the ambiguous year: cite the **issue** year for a work with both an
online-first and an issue date, unless your institution's style says otherwise,
and give the DOI so the online-first version resolves regardless.

## E3 — Referencing style not fully confirmed

Institutional question; nothing in the project material bears on it.

---

# Ordered actions

1. **A3** — take option 1, and adopt the two-class provenance wording. Chapter 5
   is unblocked.
2. **A1** — use `20260902T022406Z_p4-chaos-dead`. Chapter 7.3 is unblocked.
3. **D1–D5** — settled above. Authoritative values: quorum floor **67.054 ms**
   (67.1 in prose); Little's law Phase II @ C=1 **2.0%**; unqueued ratio
   **50.38x** (50.4x in prose); C = 2 update p50 **1.48 ms**; Deployment A
   cluster peak **1,791 ops/s**.
4. **B2** — reconsider Chapter 6. A gateway CPU, memory and I/O series exists for
   both reported phases and supports the CPU-bound claim directly.
5. **C2** — figure generated; add it to Chapter 6.
6. **D6, D7** — one sentence each in Chapter 4; D6 strengthens §4.6.
7. **A2** — split RQ1 into reproducibility (evidenced) and idempotency (not
   measured).
8. **C1** — your supervisor's call on the security section's scope.
9. **E1–E3** — before the final pass.

## Changes made to the project while resolving these

- `crdblab/analysis/raft_overhead.py` — the unqueued ratio is computed from
  unrounded medians. Returns 50.38 rather than 50.37. Regression test added;
  100 tests pass.
- `figures/fig6_resilience_timeline_recover.png` / `.pdf` — generated.
- The data pack's §3.2 and §3.4 corrected for the double-rounding in D3 and D4.

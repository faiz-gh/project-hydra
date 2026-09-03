# Defect record: the legacy measurement pipeline

Written for §5.3 of the dissertation. Each defect is stated with its mechanism,
its observable signature, and the structural change that prevents its recurrence.

D1–D5 were identified in the retained exports of the legacy pipeline. D6 onwards
were found afterwards, by the rebuilt harness, on its successive contacts with the
re-provisioned testbed: D6 in the parser, D7 in the cluster's zone configuration,
D8 in the generator's invocation, D9 in the asymmetry between the baseline node
and the cluster members, D10 in the relationship between a run's two clocks, D11
in what the run manifest failed to record about the machine, and D12 in the
interaction between a pre-flight check and the database's own housekeeping.

They fall into four classes, and each needs a different kind of defence:
**parsing** (D1, D2, D3, D6), **timing** (D4, D10), **a misconfigured system
producing perfect data** (D7, D8, D9, D11), and **instrumentation interfering
with the measurement** (D12).

They are recorded here because they bear directly on the same argument — that a
measurement which looks plausible, and even one which is internally consistent,
is not thereby correct. D7, D8 and D9 sharpen it in three distinct ways. D7 and
D8 produce data that passes every check in `analysis/validation.py`, because the
arithmetic relating the recorded quantities is sound and it is the system, or the
operation behind them, that is wrong. D9 goes further still: both of the
measurements it affects are individually correct, and the error lies only in the
inference drawn from their difference — which no property of a single run can
expose, and which no amount of validating one run against itself would catch.

## D1 — Operation-type lines treated as independent samples

**Mechanism.** `cockroach workload run kv` emits one line per operation type per
interval when the workload reports reads and writes separately. The parser
accepted each line as a complete observation and wrote it as its own CSV row.
The analysis layer then took the arithmetic mean over rows within a concurrency
tier, so the reported throughput was the mean of the read and write *rates*
rather than their sum, and the reported latency was the mean of two distinct
distributions' quantiles.

**Signature.** Every tier of `baseline_single_node.csv` and
`cluster_cross_cloud_benchmark.csv` contains 121 rows for a 60-second run, i.e.
two rows per second, in a stable 80.7 : 19.3 ratio matching `--read-percent=80`.

**Consequence.** Reported throughput is approximately half the true offered
load. Reported p50 and p99 are not quantiles of any distribution.

**Prevention.** `core/workload.py` retains the operation-type label as an
explicit dimension; `analysis/validation.py::check_littles_law` fails any run
whose throughput-implied mean residence time falls below its observed median
latency.

## D2 — Latency columns bound to the wrong header positions

**Mechanism.** The operation-type label occupies a trailing column that the
generator's header does not name. A periodic line is therefore nine fields wide
while its header declares eight columns. The parser, indexing positionally,
read `fields[5]` as p50 and `fields[7]` as p99; against the true layout
(`elapsed, errors, ops/sec(inst), ops/sec(cum), p50, p95, p99, pMax, <op>`)
these are p95 and pMax.

**Signature.** After correcting D1, the throughput-implied mean latency remains
below the recorded "p50" by a factor of 1.6–3.7 across all four tiers, which is
not physically realisable for a right-skewed distribution.

**Consequence.** Every latency figure in the results chapter is one quantile
too high and mislabelled.

**Prevention.** Columns are bound by name from the header; a field count that
does not match the active header raises rather than silently reinterpreting.

## D3 — Cumulative summary block admitted as a one-second sample

**Mechanism.** The terminal summary block is also nine fields wide, and its
elapsed value equals the run duration, so it satisfied both the field-count
check and the `elapsed <= duration` guard. Its `ops(total)` column was read as
an instantaneous rate.

**Signature.** Three rows in `baseline_single_node.csv` (C = 10, 50, 100) and two
in `cluster_cross_cloud_benchmark.csv` (C = 50, 100) at 119,508–197,680 ops/sec.
`analyze_single_node_baseline.py` filtered these with `tps < 50000`;
`compare_raft_overhead.py`, which produced `raft_overhead_comparison.csv` and
hence the results tables, did not.

**Consequence.** Each affected tier's mean throughput was inflated by
approximately 1,500 ops/sec.

## Interaction: why the corruption was not obvious

D1 halves the reported throughput; D3 adds roughly 1,500 ops/sec to any tier in
which a summary row survived. On this hardware the two effects are of similar
magnitude and opposite sign, so at C = 10, 50 and 100 they very nearly cancelled:

| Concurrency | Reported | Corrected | Summary row present |
|---|---|---|---|
| 10 | 3,095 | 3,059 | yes |
| 50 | 3,243 | 3,207 | yes |
| 100 | 3,336 | 3,311 | yes |
| 200 | 1,583 | 3,183 | **no** |

At C = 200 no summary row leaked, so D1 was left uncompensated and the tier was
reported at half its true value. The apparent "throughput collapse beyond 100
workers" in the original results is therefore an artefact of two errors failing
to cancel, not a property of the system under test. The corrected single-node
profile is a flat saturation curve plateauing near 3,200 ops/sec, consistent
with a CPU-bound two-vCPU instance.

A parallel cancellation occurs in the latency dimension: halving the throughput
doubles the implied mean residence time, which moves it closer to the
mis-bound p95 and thereby weakens the very inconsistency that would have
exposed D2.

## D4 — Chaos injection clock advanced per line, not per interval

**Mechanism.** `run_chaos_experiment.py` incremented `elapsed` once per parsed
line. Under D1 two lines arrive per interval, so the counter advanced at
approximately twice wall-clock.

**Signature.** With `CHAOS_TRIGGER_SEC = 60`, the retained event timelines give
`T_fault_injected - T_start` of 34.5 s (dead) and 43.6 s (recover).

**Consequence.** Faults were injected before the intended steady-state window
had elapsed, and the same counter gated the recovery-detection hold, so the
reported RTOs of 6.0 s and 5.2 s are bounded by that guard and cannot be
interpreted as measurements.

**Prevention.** Wall-clock scheduling uses `time.monotonic()`, independent of
the sample stream.

## D5 — Definitional inconsistencies

- The recovery threshold is 85 % in `run_chaos_experiment.py` and 80 % in
  `evaluate_resilience.py`; the two now share one profile value.
- `error_rate_pct` divided a cumulative error count by an instantaneous rate,
  which is dimensionally meaningless. Only `errors_cum` is now recorded, with
  the rate derived by interval differencing in the analysis layer.
- `ram_pct` was written as a constant 0.0 in every row.
- `disk_iops` in Phase III is a gateway-local counter, not a cluster aggregate,
  and must be labelled accordingly.

## D6 — Cumulative and periodic blocks declare the operation-type column differently

Unlike D1–D5, this defect was never present in the legacy exports; it was found
in the rebuilt parser on its first contact with the live testbed, and is recorded
here because it is the direct vindication of the decision to capture real
generator output before writing analysis code.

**Mechanism.** CockroachDB v26.3.0 emits three block shapes, not two. The
periodic header stops at `pMax(ms)` and leaves the operation-type label
*unheaded*, so a periodic line carries nine fields against an eight-column
header. The cumulative header instead *declares* that column, ending
`..._pMax(ms)__total`, so a summary line carries ten fields against a ten-column
header. A third, terminal block ends `__result` and emits a blank label denoting
the cross-operation aggregate. Because the summary block's field count equals its
header's column count, a parser binding by width alone treats it as an unlabelled
row and zips the header token `total` onto the value `read`.

**Signature.** The first capture against the provisioned testbed aborted with
`ValueError: could not convert string to float: 'read'` while binding the summary
block. The exception propagated out of the streaming read loop, truncating the
teed raw log before the `write` and `__result` rows, which is itself diagnostic:
the fixture ended mid-block.

**Consequence.** None on retained results, since the defect was reached before
any measurement was recorded. Had the parser instead coerced or skipped the
offending row, the cumulative totals — the only independent cross-check on the
periodic stream — would have been silently unavailable, removing the check that
detects dropped lines in the SSH pipe.

**Why the synthetic fixtures concealed it.** The hand-written fixtures encoded an
*assumed* summary header without the trailing `__total` token, giving a
nine-column header against a nine-field row. They therefore exercised a shape the
generator does not emit and passed. This is the same failure mode as D1–D3 in
miniature: a belief about the data's layout, tested only against itself.

**Prevention.** A trailing header token naming no known measurement is recognised
as the operation-type column and removed, so both block types present one shape
to the binding logic. A trailing field that parses as a number, and any token
that fails numeric conversion, now raise `WorkloadParseError` naming the offending
*column* rather than surfacing a bare `ValueError`. `tests/test_workload_parser.py`
pins all three block shapes against real captures.

## D7 — Lease preferences silently absent, placing leaseholders off-continent

**Mechanism.** `scripts/bootstrap.tftpl` applies two zone-configuration
statements in sequence: `num_replicas = 5`, then a `lease_preferences` list
naming one locality per region of the intended low-latency triangle. The primary
node waits for its own SQL interface to respond but not for its peers to join.
CockroachDB rejects a constraint matching no currently-live node, so on a cluster
whose GCP member joined five seconds later the second statement failed with
`constraint "+region=us-east1" matches no existing nodes within the cluster`.
Under `set -e` the script aborted, leaving the replication factor applied and the
lease preference empty.

**Signature.** `SHOW ZONE CONFIGURATION FOR RANGE default` returns
`num_replicas = 5` alongside `lease_preferences = '[]'`. The `kv` range's
leaseholder was resident on the `cloud=azure,region=centralindia` node while the
generator executed on the `us-east` gateway.

**Consequence.** Every read crossed to South Asia. Measured against the
misconfigured cluster, the mixed workload sustained 34.8 ops/s at a read median
of 209.7 ms and a write median of 402.7 ms; with the preference applied and the
leaseholder local, the same invocation sustained 428.0 ops/s at a read median of
1.9 ms, a factor of 12.3 in throughput and 110 in read latency. A read-only run
reaches 3,230 ops/s, consistent with the corrected single-node plateau near
3,200 ops/s derived independently in the D1/D3 interaction analysis above.

The defect is silent by construction: the cluster reports five live nodes and
full health, and the workload returns internally consistent numbers. Little's law
holds on the misconfigured capture — an implied mean residence time of 238.7 ms
against a generator-reported mean of 240.6 ms, a ratio of 0.992 — so no
consistency check in `analysis/validation.py` can detect it. The measurement is
valid; it is the system under test that is misconfigured. This distinction is the
reason placement must be asserted in pre-flight rather than inferred from the
data afterwards.

**Bearing on the original results.** This mechanism is a candidate explanation
for the unexplained Phase III C = 10 anomaly (545 ops/s against a 3,059 ops/s
single-node baseline) recorded in the dissertation. An arbitrarily placed
leaseholder reproduces a throughput deficit of the observed order, and the legacy
tooling never recorded replica or lease placement, so the original runs cannot be
excluded from having been affected.

**Prevention.** The bootstrap now blocks until the expected node count is live
before configuring zones, and asserts a non-empty `lease_preferences` afterwards,
exiting non-zero rather than reporting success on a partial configuration.
Leaseholder placement is to be verified in pre-flight before each measurement
phase, per Stage 6.

## D8 — Generator key seed defaults to a fresh value on every invocation

Found on 2026-09-02 while re-provisioning the testbed. Like D6 this defect was
never in the retained exports, but unlike D6 it almost certainly *was* present in
the runs that produced them, because the legacy scripts invoked the generator in
exactly the configuration that triggers it.

The defect has two distinct forms, one per generator. They are recorded together
because they share a signature — operations that complete without touching data —
but they have different causes and only one of them is fixable.

**Mechanism (ycsb).** `ycsb` derives its keys from a pseudo-random sequence
controlled by `--seed`, and the flag's own help states that the default "changes
in each run". A table populated by `cockroach workload init` is therefore
addressed by a different keyspace than the subsequent `cockroach workload run`
consults. Every point read and every point update matches zero rows. The table is
fully populated and the queries are well-formed; they simply address keys that
were never inserted. Passing an identical `--seed` to both phases resolves it
completely.

**Mechanism (kv).** `kv` is not fixable this way, and its cause is different: its
reads address only keys written by the *same run process*. A read-only run
against 100,000 rows loaded with a matching `--seed` still returns 33,449 empty
reads out of 33,449 — pre-loaded rows are unreachable irrespective of `--seed`,
`--cycle-length`, `--insert-count`, or a replayed `--write-seq`, each of which
was tested. In a mixed run the effective read working set is therefore whatever
that run has written so far: it starts empty, grows over the measurement window,
and grows *faster at higher concurrency*. The working set is thus a function of
throughput, which is the dependent variable, and differs systematically between
concurrency tiers. This is the reason the project moved to `ycsb`.

**Signature.** Measured with `crdb_internal.statement_statistics`, which counts
rows actually read rather than operations attempted:

| Configuration | Statement executions | Rows matched |
|---|---|---|
| `ycsb` workload C, all defaults, 10,000 rows loaded | 128,685 | 0 |
| `ycsb` CUSTOM 80/20, `--insert-count` matched, 125,000 rows | 94,502 | 0 |
| `ycsb` update-only, `--insert-count` matched | ~40,000 | 0 |
| `ycsb` workload C, **`--seed` matched** | 25,331 | 25,331 |

Corroborated independently: 29,674 updates against a 125,000-row table left all
1,000 sentinel-marked rows unmodified, where uniform selection predicts roughly
210 overwrites.

**Consequence.** An operation that matches no row does no work and returns
almost immediately. On this topology the effect is large and, critically,
flattering:

| Metric | Seed mismatched | Seed matched |
|---|---|---|
| Update throughput | 2,809.7 ops/s | 135.3 ops/s |
| Update p50 | 3.1 ms | 75.5 ms |
| Rows matched per update | 0.0000 | 1.0000 |

The defective configuration overstates write throughput by a factor of twenty
and understates write latency by a factor of twenty-five. Because it errs
towards a better result, it does not invite the scepticism that a poor result
would. The corrected 75.5 ms agrees with the independently measured 70.6 ms
round-trip to the second-fastest follower, which is the floor Raft quorum
imposes on any committed write in this topology.

**Why no existing check detects it.** Little's law holds throughout. On the
defective update-only run the implied mean residence time is
`N/X = 10/2809.7 = 3.56 ms` against a generator-reported mean of 3.6 ms; on the
corrected run it is `10/135.3 = 73.9 ms` against 73.6 ms. Both are internally
consistent to better than 1%. Every check in `analysis/validation.py` passes in
both cases, because the arithmetic relating the recorded quantities is sound in
both cases. What differs is not the consistency of the measurement but the
semantics of the operation being measured. This is the sharpest illustration in
this project of the distinction the validation layer cannot make: consistency is
necessary and not sufficient, and no amount of internal cross-checking
substitutes for confirming that the workload is doing the work it claims.

**Bearing on the original results.** The legacy scripts used `kv`, and called
`cockroach workload init kv --drop` with no `--insert-count`, so the table began
empty. Their reads were therefore *not* empty lookups — in a mixed 80/20 run the
in-process write window populates quickly, and a like-for-like capture shows only
a 3.3% miss rate. The defect in their case is the kv form above: the read working
set was whatever each run had written by that point, so it was small, entirely
cache-resident, and systematically larger in the higher-concurrency tiers. The
C = 10 and C = 200 tiers were not reading comparable datasets, which undermines
the tier-to-tier comparison that the throughput curve depends on.

This compounds rather than competes with D1–D3: those defects concern how the
recorded numbers were parsed and aggregated, whereas this one concerns what the
operations behind them were actually doing.

**Prevention.** `seed` and `insert_count` are profile fields (`profiles/*.yaml`),
copied into every run manifest, and passed unconditionally by `cli.py` to both
generators. Two pre-flight assertions are required before any measurement phase
is trusted, neither yet implemented:

1. *Row-match assertion.* After a short warmup, read the row-match rate for the
   workload's statement fingerprints from `crdb_internal.statement_statistics`
   and abort unless it is 1.0. This is the direct test and is cheap.
2. *Write-latency floor.* A committed write cannot be faster than the round trip
   to the follower that completes quorum. Phase I already measures inter-node
   RTT; asserting `write_p50 >= second_fastest_follower_rtt` turns D8's
   signature into a physical impossibility rather than a plausible result.

Note also that `crdb_internal.reset_sql_stats()` does not clear these counters
on v26.3.0. Statement statistics must be differenced across an interval rather
than read absolutely; reading them absolutely produces a running mean over the
whole session and was itself a source of three misleading intermediate readings
while this defect was being diagnosed.

## D9 — Baseline and cluster configured with different block cache sizes

Found on 2026-09-02 while normalising instance sizes for the Raft-overhead
comparison. Like D7 this is a defect in the testbed rather than in the code, and
like D7 it produces data that is internally consistent and passes every check.

**Mechanism.** The Phase II baseline exists to measure the workload *without*
replication, so the comparison between it and Phase III is only interpretable if
replication is the sole difference between them. It was not.
`scripts/bootstrap-local.tftpl` started the baseline with `--cache=0.25` and
`--max-sql-memory=0.25`, while `scripts/bootstrap.tftpl` set neither and every
cluster member therefore took CockroachDB's default `--cache=128MiB`. On the
instance sizes in use this is a block cache of roughly 1 GiB against 128 MiB, a
factor of about fifteen.

**Signature.** Visible only by comparing the two templates, or by reading the
process arguments on the hosts:

```
crdb-linode-1: cockroach start --insecure --store=... --join=...
crdb-local-1:  cockroach start-single-node --insecure --store=... --cache=0.25 --max-sql-memory=0.25
```

Nothing in the recorded artefacts distinguished the two configurations, because
the run manifest described the client side -- profile, generator command,
topology -- and the server side not at all.

**Consequence.** The declared working set is 205 MB. The baseline's cache
therefore held the entire dataset while no cluster member's did, so the Phase II
measurement was served from cache and the Phase III measurement was not. The
first paired runs gave, at C=10, 4,125 ops/s against 660 ops/s and an update
median of 3.90 ms against 71.30 ms. Some of that difference is Raft replication,
which is the quantity being measured; an unknown part of it is cache residency,
which is not. The comparison overstates replication cost by an amount that was
not determined, because the runs were discarded rather than corrected.

The instance sizes compounded it: the baseline had 7.8 GiB against the cluster
members' 3.8 GiB, so `--cache=0.25` resolved to a larger absolute figure as well
as being set at all.

**Why no check detects it.** Both configurations are healthy and both produce
measurements that satisfy every invariant in `analysis/validation.py` and every
assertion in `core/preflight.py`. The defect is not in either measurement but in
the inference drawn from their difference, which no property of a single run can
expose.

**Prevention.** Both templates now set `--cache=0.25` and `--max-sql-memory=0.25`
explicitly, with a comment in each stating that the values must be kept
identical and why. Stating them explicitly is also preferable to relying on the
default irrespective of parity: an unstated cache size cannot be reported in a
methodology chapter and changes between CockroachDB versions.

More generally, `core/preflight.py::capture_server_config` now records the
server's own process arguments and version into every run manifest. The
underlying failure was not that the caches differed but that nothing in the
artefact said how the server had been started, so an asymmetry of this class
could only be found by someone who already suspected it. The raw argument list is
recorded rather than a parsed subset, on the assumption that the next such
confound will involve a flag not anticipated here.

## D10 — The two clocks in a run directory were never related to each other

Found on 2026-09-02 while building the Stage 5 resilience analysis. Unlike D1–D5
this defect corrupted no recorded value: every number in the affected runs is
correct on the clock it was measured on. What was missing was the relationship
between those clocks, without which two files in the same run directory cannot
be drawn on one axis.

**Mechanism.** A Phase IV run produces two timelines. `events.json` records
offsets on the harness's monotonic clock, whose zero is taken when the phase
starts. `metrics.csv` carried only the generator's own `elapsed` column, whose
zero is when the generator begins issuing operations — later, by the cost of
opening the SSH session and starting the process. Nothing in either artefact
stated the difference, so the natural figure (throughput against time, with the
fault marked) silently placed the fault marker at the wrong point on the
throughput series.

**Signature.** In `20260902T024023Z_p4-chaos-recover` the fault is recorded at
60.005 s on the harness clock; the run occupied 185.45 s of wall clock while the
generator reported 180 intervals, so the generator's zero falls up to 5.45 s
after the run's epoch and the fault sits somewhere in 54.56–60.01 s of
generator time. The measured performance RTO for that run is 9.3 s, so the
displacement is over half the quantity being reported. The `dead` run is bounded
identically at 5.05 s against a 45 s run.

**Consequence.** None on the recorded scalars: the performance RTO was computed
entirely on the harness clock, from the same series the fault was scheduled
against, and is unaffected. The defect would have entered the dissertation
through a *figure* — the one plot in which the fault and the throughput curve
appear together, which is the plot a failover chapter is built around.

**Why it is the same class as D4.** D4 was a clock that ran at the wrong rate;
this is two clocks whose origins were never compared. Both arise from treating a
timeline as self-evident rather than as something to be measured. D4 is the
reason the *rate* agreement is now checked and reported rather than assumed:
`clock_offsets` returns the spread of the per-interval offsets alongside their
median, because a constant offset means the clocks differ only in origin, which
is what licenses converting between them, while a drifting one means no single
conversion exists.

**Prevention.** Schema 2.1 adds `wall_offset_s` to `COLUMNS`: the harness-clock
offset at which each interval's first line was read. `Manifest.clock_epoch_utc`
records the wall-clock instant of the shared origin. Both phases stamp samples
as they arrive from the pipe, through one shared grouping helper
(`core/workload.py::group_timed_ticks`) rather than two copies — Phase IV had
carried its own near-duplicate of the interval-grouping rule, which is the
structure that produced D1.

For runs recorded before this, `analysis/resilience.py::align` returns a
**bounded** alignment rather than an estimate: the generator cannot have started
before the run's epoch, and the run's wall-clock envelope must contain its whole
elapsed span, which brackets the offset without assuming anything further. Every
timing derived across the two clocks is then reported as an interval of that
width — the recover run's performance RTO comes back as 9.4–11.0 s rather than
as a point — and `fault_offsets` returns bounds rather than a position, so a
figure cannot draw a band as a line. Substituting the observed ~5.4 s for the
measurement that was not made would have been the same move as recording an
unmeasured `ram_pct` as a constant 0.0 (D5).

## D11 — The manifest recorded how the server was started, but never what it ran on

**Symptom.** The Phase II single-node baseline fell from **3,505 ops/s** to
**2,720 ops/s** across the testbed redeployment of 2026-09-02 — a 22% drop —
comparing like with like, both figures from the `smoke` profile at C=10 with
15-second tiers. Every field either manifest records is identical: profile name,
`seed=42`, `insert_count=125000`, generator, workload mix, request distribution,
`cockroach_version=v26.3.0`, and both server flags
(`--cache=0.25 --max-sql-memory=0.25`, verified byte-identical in the recorded
`start-single-node` argument lists). A further ~6% decline follows across the
afternoon (2,720 → ~2,556 mean at C=10 on 60-second tiers).

**What was ruled out, and how.**

*Configuration drift.* Ruled out by direct comparison of the recorded server
argument lists and profiles. This is the check `capture_server_config` was added
for after D9, and here it did its job — it is why the search moved on quickly
rather than stalling on a suspected cache asymmetry.

*Cumulative degradation within a sweep* — LSM growth or MVCC garbage accumulating
across a 21-tier sweep of updates. Ruled out quantitatively. Normalising each
tier's throughput against its own across-repetition mean (which removes the tier
effect) and regressing on position in the randomised sweep order gives a slope of
**−0.02% per tier, −0.4% across the whole extended sweep, r = −0.12**; the
earlier `thesis` sweep gives −0.23% per tier, −2.7% overall, r = −0.48. Neither
is remotely a 22% effect. The decline is *between* runs, not within one. This
measurement is only possible because tier order is randomised and recorded
(decision 11): under the original sequential ordering, drift and concurrency are
perfectly confounded and this question cannot be asked at all.

*Measurement-window length.* Not separable from time-of-day with the retained
data. The 15-second and 60-second tiers were also run in that order, so the ~6%
afternoon decline is confounded between window length and elapsed time. Recorded
as unresolved rather than attributed to either.

**What could not be ruled out, and why that is the defect.** The obvious
remaining candidate — that the redeployment placed the instance on different
hardware — **cannot be tested against the artefacts at all**, because nothing in
a run directory describes the machine. `capture_server_config` recorded the
process (`pgrep -a cockroach`) and the binary (`cockroach version`). It did not
record the host. Probing the live node after the fact establishes only the
*current* machine (`n2-custom-2-4096`, 2 vCPU Intel Xeon @ 2.80 GHz, 3.82 GiB
`MemTotal`); there is no counterpart reading for the pre-redeployment host and
there cannot be one now, because that instance no longer exists.

The resident-set figures are suggestive — 1.26–1.62 GiB before the redeployment
against 2.29–2.47 GiB after — but they are **not** admissible as evidence of a
memory-size change: RSS is still climbing during a 15-second tier and is
non-monotonic across tiers within a single post-redeployment run (2.29 then 1.77
GiB). An inference from them would be exactly the kind of plausible reconstruction
this project exists to avoid.

**Status: narrowed as far as this testbed allows, by repeating the experiment.**
The whole four-phase protocol was run again across a second full teardown and
redeployment on 2026-09-03, this time *with* the hardware capture in place. That
settled what a single deployment could not:

| Quantity | Deployment A | Deployment B | change |
|---|---|---|---|
| quorum floor | 66.9 ms | 67.1 ms | +0.3% |
| Phase III write p50 @ C=1 | 72.67 ms | 71.33 ms | −1.8% |
| Phase III peak | 1,792 ops/s | 1,850 ops/s | +3.2% |
| **Phase II write p50 @ C=1** | 2.18 ms | **1.42 ms** | **−35%** |
| **Phase II peak** | 2,565 ops/s | **3,563 ops/s** | **+39%** |

**The replicated cluster reproduced to within 3.6% at every tier; the
unreplicated baseline moved by up to 58%** — and the hardware capture reports an
*identical* `cpu_model` (Intel Xeon @ 2.80 GHz) and an identical `MemTotal`
(4,007,012 kB) on both baseline hosts. The instances are not merely the same
type; they are indistinguishable in every field the artefact records.

So this is not a one-off "shift" at all. It is **between-deployment variance in
the CPU actually delivered by a shared host**, invisible from inside the guest,
affecting the CPU-bound baseline and not the network-bound cluster. Three
observations support that over a harness fault: the cluster is stable across
exactly the same redeployment; the *faster* deployment is the *noisier* one
(Phase II repetitions disagreeing by up to 20%, ±523 ops/s at C=1, against ±49.7
before), which is what intermittent contention looks like and not what a
systematically faster machine looks like; and within-sweep drift is −0.4% across
21 tiers, so the variation is between deployments rather than during one.

**Consequence for the results:** the replication-cost figure is reported as a
**range, 33.4x–50.4x**, with the uncertainty entirely in the denominator. A
single-deployment study would have quoted either endpoint with equal confidence
and no way to know it was one draw. The residual — *why* two identically
provisioned instances deliver different CPU — is a property of the provider's
scheduling and is not observable from this testbed.

**Prevention.** `capture_server_config` now also reads `nproc`, the CPU model
from `/proc/cpuinfo` and `MemTotal` from `/proc/meminfo` in the same round trip,
and every phase writes a `host:` note beside the existing `server:` note.
`analysis/validation.py::host_hardware` reads it back, and
`check_run_comparability` raises an **error** when two runs being compared differ
in CPU count, CPU model or total memory, and a **warning** when either run
predates the capture — the same two-tier treatment the server flags already get.

Those three files rather than the cloud provider's machine-type metadata: this
topology spans four providers exposing it through four different endpoints, while
these exist on all of them, and it is the core count, clock and memory that bound
a measurement rather than the label attached to the bundle.

**Memory is compared even though the flags already are, and that is the point.**
`--cache` and `--max-sql-memory` are *fractions* of total memory. Two runs can
carry byte-identical flags and still have absolute cache sizes differing by any
factor the machines differ by — D9 in a form that the flag comparison, on its own,
is structurally incapable of detecting.

The docstring of `capture_server_config` had predicted that "the next confound
will involve a flag this function's author did not think to parse." It came true
one level down: not a flag, but the machine.

### D11a — the check fired on the first pair it was applied to

Running the new capture against both measured hosts on 2026-09-03:

| | Phase II baseline (`crdb-local-1`) | Phase III gateway (`crdb-linode-1`) |
|---|---|---|
| CPU | Intel Xeon @ 2.80 GHz (GCP N2) | AMD EPYC 7713 64-Core |
| vCPU | 2 | 2 |
| `MemTotal` | 4,007,012 kB | 4,005,712 kB |

**The two phases of the headline replication-cost comparison run on different CPU
architectures**, and did so throughout every measurement in this project. Nothing
recorded it, so nothing could flag it. This is D9's structure exactly — an
asymmetry between the compared systems that is invisible in the data and visible
only in how they were provisioned — and it was found by the check written for D11
on the first pair of runs it was applied to.

**How much it matters, stated by component rather than assumed either way.** The
cluster's write path is bounded by a ~67 ms quorum round trip (Phase I), which
is network and not CPU, so the write-latency ratio — the headline figure — is the
*least* exposed of the quantities. Throughput and read latency are
the most exposed: both are CPU- and cache-bound on the baseline, and the
single-node plateau near 2,500 ops/s is a property of the Intel host specifically.

This is **not** grounds for withdrawing the replication-cost figure; it is grounds
for stating the hardware alongside it, which the manifest now does. It also means
a Phase II baseline re-measured on matched hardware is the single most valuable
remaining measurement, and that the memory tolerance above (5%) must not be
loosened to cover a CPU difference — the CPU model is compared exactly, on purpose.

## D12 — A statistics flush landing after a tier ended was reported as the workload never running

**Symptom.** A 21-tier Phase III sweep on 2026-09-03 completed every tier with
physically coherent data — update medians of 71.5–239.9 ms against a 66.9 ms
quorum floor, Little's law holding to 0.3% at C=1 — and was rejected outright.
Twenty of the twenty-one `row_match` probes reported a match rate of 0.9999 or
better. The twenty-first reported:

> `no statements against 'usertable' were recorded during the window; the
> workload may not have run at all`

The tier it condemned was C=10 rep 3, which had just sustained **611.7 ops/s over
55 one-second intervals** and passed its own quorum-floor check at 74.965 ms. The
message was not merely unhelpful; it asserted something the same run's own data
contradicted.

**Cause.** `RowMatchProbe` differences `crdb_internal.statement_statistics`, an
**in-memory** view, across the tier. `sql.stats.flush.interval` (10 minutes, with
15% jitter) zeroes it. D11's predecessor fix handled a flush landing *mid-tier*:
the delta goes non-positive while the absolute counter is still positive, so the
probe falls back to the absolute counters over a narrowed but still attributable
window. This flush landed *after* the tier's workload had stopped and before the
closing sample, so the absolute counter was zero too. The fallback's guard —
`executions <= 0 and c1 > 0` — is false, and control fell through to a branch
whose message assumed the only remaining explanation was an idle workload.

`crdb_internal.statement_statistics_persisted` is not a usable fallback: queried
against the live cluster it returns 0 for the same fingerprint pattern while the
in-memory view returns 1.12M.

**The obvious fix is wrong and is recorded here so it is not tried again.**
Raising `sql.stats.flush.interval` for the duration of a sweep removes the race
entirely. It is rejected because a flush writes to `system.statement_statistics`,
which is background I/O on a 2 vCPU host already carrying a saturated workload:
suppressing it would raise the throughput being measured and make runs before and
after the change incomparable. The measurement is not adjusted to suit its own
instrumentation.

**Fix.** Two changes, deliberately separate.

*The classification was wrong, so the message was wrong.* A non-zero count at
`start()` with zero at `finish()` means statements **were** recorded and the view
was then flushed — the evidence was moved, not never created. Genuine idleness is
`c0 == c1 == 0`. These are now distinct branches with distinct messages. A test
asserting the old reading ("reset counters and an idle workload are
distinguishable") encoded the mistake and has been corrected rather than deleted.

*A flushed window can be corroborated, in exactly one phase.* Under D8 an update
matching no rows commits an empty transaction — there is nothing to replicate —
and returned 3.1 ms. A write median above the quorum floor is therefore positive,
independent evidence that a tier's updates performed real cross-region quorum
writes, and it rules out the seed mismatch D8 names, which breaks reads and
updates together. Where the floor check passes, a flushed window is recorded as
`window: "flushed; corroborated by quorum floor"` and the run proceeds.

Three limits are enforced rather than assumed:

1. **Corroboration applies only where there is no evidence.** A window that
   produced a measurement is asserted on regardless of the floor check. An escape
   hatch there would disable D8's only direct detector.
2. **It does not cover reads**, which are 80% of the mix. The check records that
   it was corroborated rather than measured, and the run carries the distinction.
3. **Phase II cannot use it at all.** An unreplicated baseline has no quorum floor,
   so an uncorroborated flush stays fatal there — the asymmetry is in the physics,
   not in the tolerance.

The unmeasured rate is recorded as `None`, not `0.0` (which would read as a total
mismatch) and not `NaN` (which serialises into the manifest as a bare `NaN` token
and is not valid JSON).

**Ordering.** `check_write_latency_floor` now runs before the row-match probe and
returns its verdict, so the corroboration is available when needed. The two checks
remain independent in every other case.

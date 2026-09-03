# Defect resolution map

One row per defect: what it was, and what was changed so it cannot recur. The
full account of each — how it was found, what it cost, and why it was invisible —
is in `defects.md`. This file is the index.

All twelve are closed. Every fix is exercised by the test suite (99 tests).

---

## Summary

| ID | Defect | Resolution | Where |
|---|---|---|---|
| D1 | Operation-type lines treated as independent samples | Operation type retained as an explicit dimension; throughput summed across types, latency never pooled | `core/workload.py`, `analysis/loader.py` |
| D2 | Latency columns bound to the wrong header positions | Columns bound by header *name*; an unexpected field count raises | `core/workload.py` |
| D3 | Cumulative summary block admitted as a one-second sample | Blocks classified as `PERIODIC` or `SUMMARY`; only periodic samples reach a metrics row | `core/workload.py` |
| D4 | Chaos injection clock advanced per line, not per interval | Scheduling moved to `time.monotonic()`, never a sample counter | `phases/p4_chaos.py` |
| D5 | Definitional inconsistencies (4 items) | Single profile value for the threshold; derived and unmeasured columns removed; host-local counters renamed | `config.py`, `core/recorder.py`, `profiles/*.yaml` |
| D6 | Periodic and summary blocks declare the operation-type column differently | A trailing header token naming no known measurement is recognised as the op label | `core/workload.py` |
| D7 | Lease preferences silently absent, leaseholders off-continent | Bootstrap blocks on the full node inventory, then asserts the preference is non-empty and exits non-zero; also asserted in pre-flight | `terraform/scripts/bootstrap.tftpl`, `core/preflight.py` |
| D8 | Generator key seed defaults to a fresh value on every invocation | `seed` and `insert_count` are profile fields passed explicitly; a row-match probe asserts operations touch rows | `cli.py`, `core/preflight.py`, `profiles/*.yaml` |
| D9 | Baseline and cluster configured with different block cache sizes | Both templates set the same memory flags; the server's start command is captured into every manifest and compared before any cross-run comparison | `terraform/scripts/*.tftpl`, `core/preflight.py`, `analysis/validation.py` |
| D10 | The two clocks in a run directory were never related | Schema 2.1 records both clocks per interval; runs predating it get a *bounded* alignment and every cross-clock timing is reported as an interval | `core/recorder.py`, `analysis/resilience.py` |
| D11 | The manifest recorded how the server was started, but never what it ran on | Pre-flight captures CPU count, CPU model and total memory into every manifest; unlike machines refuse comparison | `core/preflight.py`, `analysis/validation.py` |
| D11a | The hardware check fired on the first pair it was applied to | Difference recorded as a stated limitation; an explicit `--accept-hardware-difference` downgrades the refusal to a logged warning rather than suppressing it | `analysis/validation.py`, `cli.py` |
| D12 | A statistics flush after a tier ended read as "the workload never ran" | Flush distinguished from idleness; a flushed window is accepted only where the quorum-floor check independently covers that tier | `core/preflight.py`, `phases/bench.py` |

---

## Detail

### D1 — Operation-type lines treated as independent samples
The generator emits one line per operation type per interval. Each was written as
its own row and averaged, which halved throughput and produced a latency figure
that was a quantile of nothing.

**Resolution.** Operation type is an explicit column in the schema. The
aggregation policy lives in exactly one place — `loader.Run.ticks` sums
throughput across types, `Run.latency_by_op` keeps latency separate — so no
module can quietly choose otherwise.

### D2 — Latency columns bound to the wrong header positions
Positional indexing read p95 while believing it read p50, and pMax while
believing p99, because a periodic line is one field wider than its header.

**Resolution.** `WorkloadParser` binds every column by name from the generator's
own header line. A data line arriving before a header, or a field count that
cannot be reconciled with one, raises rather than guessing.

### D3 — Cumulative summary block admitted as a one-second sample
The terminal totals line has the same width as a periodic line and an elapsed
value equal to the run duration, so it passed both guards and was recorded as a
single interval at 150,000–200,000 ops/s.

**Resolution.** Blocks are classified as `PERIODIC` or `SUMMARY` at parse time
and the two are never interchangeable. Only periodic samples are written to
`metrics.csv`. A plausibility ceiling in `analysis/validation.py` catches the
signature if one ever leaks.

### D4 — Chaos injection clock advanced per line, not per interval
The injector incremented its clock once per parsed line. Two operation-type lines
arrive per interval, so it ran at twice wall-clock and a fault intended for 60 s
fired at 34.5 s.

**Resolution.** Fault scheduling runs on a timer thread reading
`time.monotonic()`, which never touches the sample stream. Sample cadence is
checked independently in validation.

### D5 — Definitional inconsistencies
Four separate items, each resolved:

- **Recovery threshold** was 85% in the runner and 80% in the evaluator. Now one
  profile field (`chaos.recovery_threshold`), read by both.
- **`error_rate_pct`** divided a cumulative count by an instantaneous rate, which
  is dimensionally meaningless. Removed from the schema; only `errors_cum` is
  recorded and the rate is derived by interval differencing in the analysis layer.
- **`ram_pct`** was written as a constant `0.0` in every row. Removed rather than
  defaulted — recording an unmeasured quantity as a value is worse than omitting
  it, and `MetricsWriter` now rejects any row that does not match `COLUMNS`
  exactly, so a column cannot be silently filled.
- **`disk_iops`** was a gateway-local counter presented as a cluster aggregate.
  Renamed `gateway_disk_iops`, alongside `gateway_cpu_pct` and
  `gateway_rss_bytes`, so the scope is in the name.

### D6 — Periodic and summary blocks declare the operation-type column differently
Found on first contact with the live testbed. The summary header declares the
operation column and the periodic header does not, so width-based binding zipped
header token `total` onto value `read` and attempted `float("read")`.

**Resolution.** A trailing header token naming no known measurement is recognised
as the operation label. Test fixtures are now verbatim captures from the live
testbed; the hand-written fixtures that concealed this were deleted, because they
encoded an assumed header shape and so could not have caught it.

### D7 — Lease preferences silently absent, placing leaseholders off-continent
The bootstrap applied `num_replicas` then failed on the lease preference, because
it waited only for its own SQL interface rather than for peers to join. The
constraint matched no live node, `set -e` aborted mid-sequence, and the cluster
ran with leaseholders in `centralindia` while the generator ran in `us-east`.
Cost: 12.3× throughput and 110× read latency. **Little's law held throughout.**

**Resolution.** The bootstrap blocks until the full node inventory is live, then
asserts `lease_preferences` is non-empty and exits non-zero rather than reporting
success on a partial configuration. Placement is also asserted in pre-flight
before every phase.

### D8 — Generator key seed defaults to a fresh value on every invocation
A table loaded by one process is addressed by a different keyspace than the next
process queries. Every lookup matches zero rows and returns in ~3 ms. The broken
configuration reports roughly twenty times the throughput at a twenty-fifth of
the latency — it fails *flatteringly*.

**Resolution.** `seed` and `insert_count` are profile fields, passed explicitly on
every invocation. `RowMatchProbe` differences statement statistics across each
tier and refuses the run if operations are not touching rows. A write-latency
floor check independently asserts that a committed write has not outrun quorum.

### D9 — Baseline and cluster configured with different block cache sizes
The baseline ran with `--cache=0.25` (~1 GiB) while cluster members took the
128 MiB default, against a 205 MB working set. **Both runs are individually
correct**; only the inference from their difference is wrong, which no property
of a single run can expose. Correcting it moved apparent write overhead from
18.3× to 12.8×.

**Resolution.** Both Terraform templates set `--cache=0.25` and
`--max-sql-memory=0.25`. `capture_server_config` records the server's actual
start command into every manifest, and `check_run_comparability` refuses to
compare two runs whose server flags, workload parameters or version differ.

### D10 — The two clocks in a run directory were never related to each other
A run carries the generator's own elapsed counter, on which throughput is
reported, and the harness's monotonic clock, on which faults are scheduled. Their
origins differ by SSH and process startup — measured between 3.9 s and 5.4 s. A
figure drawn from both displaced the fault by more than half the reported RTO.

**Resolution.** Schema 2.1 adds `wall_offset_s`, recording both clocks per
interval, and `Manifest.clock_epoch_utc` records the shared origin. Both phases
stamp samples through one shared grouping helper rather than two copies. Runs
predating the column get a **bounded** alignment: every cross-clock timing is
reported as an interval, and figures draw the fault as a band rather than a line,
because collapsing an interval to a point asserts a measurement nobody made.

### D11 — The manifest recorded how the server was started, but never what it ran on
`capture_server_config` recorded the process and the binary, so a 22% baseline
shift across a redeployment could not be diagnosed: the instance no longer
existed and nothing had recorded what it was.

**Resolution.** Pre-flight now also reads `nproc`, the CPU model from
`/proc/cpuinfo` and `MemTotal` from `/proc/meminfo` in the same round trip, and
every phase writes a `host:` note beside the existing `server:` note.
`check_run_comparability` errors on differing CPU count, model or memory, and
warns when either run predates the capture. Memory is compared even though the
cache flags already are, because those flags are *fractions* of it: identical
flags on unlike machines give unlike caches.

> **Scope of the fix.** The *defect* — an artefact that could not answer a
> question about its own hardware — is closed. The *underlying variability* is
> not: repeating the whole protocol on a second deployment showed the
> network-bound cluster reproducing to within 3.6% while the CPU-bound baseline
> moved by up to 58%, on hardware indistinguishable in every recorded field. That
> is delivered-CPU variance on a shared host, which is a property of the provider
> and not observable from inside the guest. It is reported as a stated limitation,
> and the replication-cost figure is given as a range rather than a point.

### D11a — The hardware check fired on the first pair it was applied to
Running the new capture against both hosts showed the two phases had always been
on different CPU models — the baseline a GCP instance (Intel Xeon @ 2.80 GHz),
the gateway a Linode one (AMD EPYC 7713). D9's structure exactly: an asymmetry
invisible in the data and visible only in provisioning.

**Resolution.** Recorded as a stated limitation rather than chased, bounded by
component: the cluster's write path is dominated by a network round trip no CPU
difference can touch, so write-latency ratios are least exposed and absolute
throughput most. `analyze raft-overhead` refuses the comparison unless
`--accept-hardware-difference` is passed, which downgrades the refusal to a
*recorded warning* rather than suppressing it. The override is scoped to hardware
only — a workload or seed mismatch still refuses.

### D12 — A statistics flush after a tier ended read as "the workload never ran"
The row-match probe differences an in-memory statistics view that CockroachDB
flushes every ten minutes. A flush landing *during* a tier was already handled;
one landing after the tier's workload stopped left nothing to assert on, and
rejected an entire 21-tier run in which every tier was sound.

**Resolution.** Two changes, deliberately separate. First, the classification was
wrong: a non-zero count at `start()` with zero at `finish()` means the view was
flushed, not that the workload was idle, and the two now produce different
messages. Second, a flushed window is accepted only where an independent detector
covers the same tier — the quorum-floor check, since an update matching no rows
commits an empty transaction and returns in ~3 ms. That corroboration is
deliberately unavailable to Phase II, which has no quorum floor, so the assertion
stays fatal there.

> **Rejected fix, recorded so it is not retried.** Raising
> `sql.stats.flush.interval` for the duration of a sweep removes the race
> entirely. It also removes background disk I/O on a two-core host under a
> saturated workload, which would raise the throughput being measured and make
> runs before and after the change incomparable. The measurement is not adjusted
> to suit its own instrumentation.

---

## What "closed" means here

Each fix is structural rather than a corrected value: the defect is prevented by
the shape of the code, not by remembering to avoid it. Three properties recur.

**Parsing defects are prevented by binding on names and refusing the unexpected**
(D1, D2, D3, D6). The parser raises rather than guessing, and raw generator output
is retained beside every derived file so any dispute is settleable against the
original bytes.

**Configuration defects are caught before the measurement, not after** (D7, D8,
D9, D11). Validation asks whether recorded numbers are consistent with each other;
pre-flight asks whether the system was fit to be measured. These are different
questions, and the run whose pre-flight failed is exactly the run whose numbers
look fine. `analysis/loader.py::load_run` is the only entrance to a run and gates
on both.

**Timing defects are prevented by recording rather than assuming** (D4, D10).
Nothing derives a time from a sample count, and where a relationship between two
clocks was not measured it is reported as an interval rather than estimated.

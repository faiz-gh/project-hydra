# Data schema

What gets written where, and the exact column/field layout of every
artifact this project produces. Source of truth is
`crdblab/core/recorder.py` (CSV schemas + manifest), `crdblab/core/preflight.py`
(pre-flight report shape) and each phase module's `events.json` construction
(`crdblab/phases/p4_chaos.py`); this document is a reader's map onto that
code, not a replacement for it — if the two disagree, the code is right and
this file is stale.

## Where the data lives

Every measured phase (`net probe`, `bench`, `chaos run`) writes one
**run directory** under `runs/` (`CRDBLAB_RUNS_DIR` in `.env`, default
`<repo>/runs/`), named `<UTC timestamp>_<phase suffix>`, e.g.
`20260909T171455Z_p4-chaos-recover`. `runs/` is **gitignored** — it is a data
directory, not source, and nothing under it is version-controlled. A run
directory is immutable once created (`RunDirectory.__init__` raises
`FileExistsError` if the target path already exists) and self-describing:
every artifact inside it can be read without consulting anything outside the
directory except this schema and the code.

```
runs/
  <run_id>/
    manifest.json        # always present — see "Manifest" below
    preflight.json        # always present — pre-flight checks + their observed values
    metrics.csv            # bench and chaos runs — per-interval throughput/latency
    network.csv             # net probe runs only — RTT matrix
    audit.csv                # chaos runs only — RPO audit-writer attempt log
    rto_probe.csv             # chaos runs only — high-frequency RTO probe attempts
    rto_probe.log              # chaos runs only — probe connection-lifecycle events (JSONL)
    hardware_metrics.csv        # bench and chaos runs, from 2026-09-10 onward —
                                 # per-node CPU/memory/disk/network utilisation
    events.json            # chaos runs only — fault timing, RTO/RPO summary
    raw/                    # raw generator/ping stdout, one file per tier/probe/fault
  _logs/
    experiment-<timestamp>.log   # run-experiment.sh's own console transcript,
                                  # spanning every phase of one invocation
```

Which files exist depends on which phase wrote the run:

| Phase (CLI)          | Run-id suffix         | Files beyond manifest/preflight                                   |
|-----------------------|------------------------|---------------------------------------------------------------------|
| `crdblab net probe`     | `_p1-network`            | `network.csv`, `raw/*.ping.txt`                                       |
| `crdblab bench`          | `_bench_cluster`          | `metrics.csv`, `hardware_metrics.csv`\*, `raw/c<C>_rep<R>.txt`           |
| `crdblab chaos run --mode recover` | `_p4-chaos-recover` | `metrics.csv`, `audit.csv`, `rto_probe.csv`, `rto_probe.log`, `events.json`, `hardware_metrics.csv`\*, `raw/chaos_recover.txt` |
| `crdblab chaos run --mode dead`    | `_p4-chaos-dead`    | same as above, `raw/chaos_dead.txt`                                     |

\* `hardware_metrics.csv` exists only for runs recorded from 2026-09-10
onward (when node-level metrics collection was added), and only when the
profile's `hardware_metrics.enabled` was true (default). Absent from every
older run.

Two now-removed phases from the project's original (pre-rearchitecture)
design — an unreplicated single-node baseline (`_p2_baseline`) compared
against a replicated cluster (`_p3_cluster`) — used the same `manifest.json`
+ `metrics.csv` shape as `bench_cluster` but predate the `engine` manifest
field entirely (both were CockroachDB-only). No code in this repo produces
these run-id suffixes any more.

## `manifest.json`

One JSON object per run, `dataclasses.asdict()` of `recorder.Manifest`.
Every field:

| Field | Type | Notes |
|---|---|---|
| `run_id` | str | The run directory's own name. |
| `phase` | str | `"p1_network"`, `"bench_cluster"`, or `"p4_chaos"` (covers both chaos modes — see `events.json`'s `mode` for which). |
| `schema_version` | str | Currently `"2.1"`. |
| `engine` | str | `"cockroachdb"` or `"postgresql"`. Defaults to `"cockroachdb"` for runs recorded before this field existed (2026-09-08), since that is what all of them were. |
| `started_utc` / `finished_utc` | str \| null | ISO-8601 UTC. `finished_utc` is null until the run completes. |
| `git_revision` | str \| null | `git rev-parse HEAD` at the moment the run started — the code that produced this run. |
| `profile` | dict | The full `Profile` (workload + chaos + hardware_metrics spec + tps_ceiling) copied verbatim — see `profiles/*.yaml` for field meaning. |
| `topology` | list[dict] | Which node(s) this run's manifest cares about (not always all 5 — e.g. bench records only the generator's exec node). |
| `clock_epoch_utc` | str \| null | Wall-clock instant corresponding to monotonic zero (`t_zero`) — the shared origin for `wall_offset_s` (metrics.csv) and every offset in `events.json`. |
| `server_version` | str \| null | Whatever server was measured, either engine. |
| `cockroach_version` | str \| null | Set for CockroachDB runs; kept alongside `server_version` for backward compatibility with runs recorded before `server_version` existed. |
| `generator_command` | str \| null | The literal `cockroach workload run ...` command line issued. |
| `ssh_options` | list[str] | The SSH flags used to reach every node (e.g. `StrictHostKeyChecking=no`). |
| `client_platform` | str | `platform.platform()` of the machine that ran the harness. |
| `notes` | list[str] | Free-text, timestamped narrative entries — warnings, repairs, anomalies. Read these before trusting a run; several defect classes (D5, D8, D9, the fault-did-not-land banner, the instrument-coverage banner) surface here first. |
| `generator_totals` | dict | The generator's own final cumulative summary block, if the parser recognised one (see the "known gap" note in `CLAUDE.md` — this has been empty on every recorded run to date). |
| `validation` | dict | `{"preflight": <PreflightReport.to_dict()>}` — see below. This is the *pre-flight* gate only; the separate post-hoc `crdblab validate` command does not write back into the run directory. |

## `preflight.json`

`PreflightReport.to_dict()`, optionally with a `"tiers"` key appended (bench
only):

```json
{
  "ok": true,
  "checks": [
    {
      "name": "clock_offset",
      "passed": true,
      "detail": "gcp-1: NTP offset 0.00 ms (limit 250 ms)",
      "...observed kwargs...": "vary per check — e.g. node, offset_s, expected, observed, repaired"
    }
  ],
  "tiers": ["bench runs only — one summary dict per (concurrency, repetition) tier"]
}
```

`ok` is `all(check.passed for check in checks)`. A run whose pre-flight
failed never reaches a measurement — `raise_if_failed()` aborts before any
CSV is written — so every `preflight.json` on disk with `"ok": false` for
`chaos run` means the phase-level pre-flight passed but a *different*,
narrower check inside the run loop still failed (e.g. write-latency-floor,
row-match).

## `metrics.csv` — throughput/latency per interval (`COLUMNS`)

One row per `(interval, operation type)` — deliberately long, not wide, so
the read-vs-write summing/averaging choice is made explicitly in the
analysis layer rather than baked into the schema.

| Column | Meaning |
|---|---|
| `ts_utc` | Wall-clock stamp the interval was recorded (coarse; shared across a tick's rows). |
| `elapsed_s` | The **generator's own** elapsed-time accounting, starting from when it began issuing operations. |
| `wall_offset_s` | Seconds on the harness's monotonic clock from the run's `t_zero` to when this interval was read from the SSH pipe. Differs from `elapsed_s` by connection-setup cost (~5s typical); absent (`""`) on schema-2.0 runs that predate this column. |
| `concurrency` | The tier's `--concurrency`. |
| `repetition` | Which repeat of this tier (1-based). |
| `op` | `"read"` or `"update"` (or generator-specific op names). |
| `tps` | Instantaneous ops/sec for this interval, this op. |
| `tps_cum` | Cumulative ops/sec since the tier started. |
| `errors_cum` | Cumulative error count since the tier started. |
| `p50_ms` / `p95_ms` / `p99_ms` / `pmax_ms` | Latency quantiles for this interval, this op, as the generator reports them. |
| `gateway_cpu_pct` / `gateway_disk_iops` / `gateway_rss_bytes` | CockroachDB's own internal `/_status/vars` metrics for the generator's target node (`bench.py`'s `HostSampler`). **Not populated on any run to date** — `Target.metrics_url` always returns `""`, so these columns are uniformly blank/`nan`. Not to be confused with `hardware_metrics.csv`, below, which is unrelated and does work. |

## `network.csv` — Phase I RTT matrix (`NETWORK_COLUMNS`)

One row per (source, destination) pair, from `ping`.

| Column | Meaning |
|---|---|
| `ts_utc` | When this pair was probed. |
| `source` / `destination` | Node hostnames. |
| `source_region` / `destination_region` | Node regions, for readability without a topology join. |
| `samples` | Ping count sent. |
| `loss_pct` | Packet loss percentage. |
| `rtt_min_ms` / `rtt_mean_ms` / `rtt_max_ms` / `rtt_mdev_ms` | From ping's own summary line (3-decimal precision regardless of magnitude). |
| `rtt_p50_ms` / `rtt_p95_ms` / `rtt_p99_ms` | Computed from the per-packet lines, whose printed precision *decreases* as the RTT grows (ping quirk). |
| `rtt_resolution_ms` | The precision actually available for the quantiles above, so a sub-ms figure is never falsely implied for a distant link. |

## `audit.csv` — RPO audit-writer attempt log (`AUDIT_COLUMNS`)

One row per write the RPO audit client attempted, in sequence order.

| Column | Meaning |
|---|---|
| `wall_offset_s` | Seconds from the run's `t_zero`. |
| `seq_id` | Monotonically increasing sequence number (never retried/reused). |
| `outcome` | `"ack"` (acknowledged committed), `"ambiguous"` (connection failed after the statement was sent — may or may not have committed), or `"refused"` (rejected by a reachable, talking database — not data loss). |

## `rto_probe.csv` — high-frequency RTO probe attempts (`PROBE_COLUMNS`)

One row per canary write the RTO probe dispatched. A separate instrument
from the audit writer above (different cadence, different question: *when*
service resumed, not *whether* a write was lost).

| Column | Meaning |
|---|---|
| `ts_utc` | Microsecond-resolution wall clock. |
| `seq_id` | Attempt sequence number. |
| `dispatch_offset_s` | Seconds from `t_zero` when the probe *sent* the write. |
| `complete_offset_s` | Seconds from `t_zero` when it *returned* — the figure recovery timing is computed from, since a blocked write's completion is a direct observation of when service resumed. |
| `duration_ms` | `complete - dispatch`, in ms. |
| `outcome` | One of `PROBE_OUTCOMES`: `"ok"` (served), `"timeout"` (accepted then no answer within budget — the outage's actual signature), `"conn_error"` (connection broke/couldn't be made), `"refused"` (rejected by a live database — a probe bug, not downtime). |
| `worker` | Which of the probe's worker slots made this attempt. |
| `detail` | Free-text (exception message, etc.), often empty. |

`rto_probe.log` (JSONL, not CSV) supplements this with connection-lifecycle
events — opens/closes the CSV has no column for — and is flushed live, so it
survives a run killed mid-fault.

## `hardware_metrics.csv` — per-node utilisation (`HARDWARE_METRICS_COLUMNS`)

**New 2026-09-10.** One row per `(node, poll)`, polled directly from each
node's node_exporter (`crdblab/core/hardware_metrics.py`) during Phase II-IV,
across all 5 cluster nodes plus the client node. Every rate column is paired
with the raw cumulative counter(s) it was derived from, so a reader can
audit or re-derive the rate rather than trust it blindly.

| Column | Meaning |
|---|---|
| `ts_utc` | Wall-clock stamp of this poll. |
| `wall_offset_s` | Seconds from the run's `t_zero`. |
| `node` | Short node name (`gcp-1`, `client-1`, ...). |
| `host` | Full hostname (`crdb-gcp-1`, ...). |
| `cpu_busy_pct` | `100 * (1 - Δidle/Δtotal)` across all cores, since the previous poll. Empty (`""`) on a node's first poll — no prior scrape to difference against. |
| `cpu_seconds_idle_cum` / `cpu_seconds_total_cum` | Raw cumulative counters `cpu_busy_pct` was derived from. |
| `mem_total_bytes` / `mem_available_bytes` | From `/proc/meminfo`, direct gauges (no differencing needed). |
| `disk_read_bytes_per_s` / `disk_write_bytes_per_s` | Byte-rate since the previous poll, summed across all block devices. Empty on first poll. |
| `disk_busy_pct` | `100 * Δ(io_time_seconds)/Δt`, summed across devices — valid because every node here is single-disk. Empty on first poll. |
| `disk_read_bytes_cum` / `disk_write_bytes_cum` / `disk_io_time_seconds_cum` | Raw cumulative counters the three columns above were derived from. |
| `net_rx_bytes_per_s` / `net_tx_bytes_per_s` | Byte-rate since the previous poll, summed across every non-loopback interface. Empty on first poll. |
| `net_rx_bytes_cum` / `net_tx_bytes_cum` | Raw cumulative counters the two rates above were derived from. |
| `load1` | 1-minute load average, direct gauge. |

## `events.json` — chaos run fault timing and RTO/RPO summary

Chaos runs (`recover`/`dead`) only. Not a declared fixed schema like the
CSVs — assembled incrementally in `p4_chaos.py::run()` — but stable in
practice across every recorded run. Top-level keys:

| Key | Contents |
|---|---|
| `mode` | `"recover"` or `"dead"`. |
| `target` | Chaos target's short node name. |
| `t_start_utc` | Same instant as `manifest.clock_epoch_utc` — the run's `t_zero`. |
| `t_first_error_offset_s` | First generator-reported error, if any. |
| `fault_landed` | `true`/`false`/absent (absent on runs recorded before the fault-authorisation gate existed). `false` means every RTO/RPO figure in the run describes an **undisturbed** cluster. |
| `clock` | Alignment between the generator's own `elapsed_s` clock and the harness's `wall_offset_s` clock: `generator_start_offset_s`, `min_s`/`max_s`/`spread_s` across the run, `samples`. |
| `injected` | The fault itself: `target`, `host`, `mode`, `at_utc`, `at_monotonic`, `detail` (e.g. `"rc=0"`), `landed`, `stderr`, `at_offset_s` (from `t_zero`), `at_steady_state_offset_s` (from the generator's first sample — what `inject_at_s` actually promises). |
| `availability` | The audit-writer-derived availability RTO: `availability_rto_s`, `write_gap_s`, `writes_acknowledged_after_fault`, `resolution_s`, `detection_floor_s`, `outage_observed`, plus coverage-tracking fields (`last_ack_offset_s`, `observation_end_offset_s`, `coverage_gap_s`, `coverage_truncated`) that distinguish "the cluster was quiet" from "this instrument stopped watching." |
| `probe` | The RTO-probe-derived reading: `enabled`, `rto` (its own outage/detection timing), plus its own resolution and sampling-rate figures, `error`, `log`/`attempts_csv` filenames. |
| `baseline_tps` / `recovery_threshold` / `recovery_floor_tps` / `recovery_hold_s` | The performance-RTO inputs, as configured and as measured pre-fault. |
| `t_recovered_offset_s` / `performance_rto_s` | When (if ever) throughput regained `recovery_floor_tps` and held for `recovery_hold_s`; both `null` if it never did within the run. |
| `rpo` | `AuditResult.to_dict()`: `acknowledged`, `ambiguous`, `refused`, `present_in_table`, `rpo_violations`, `lost_seq_ids` (first 50), `first_ack_utc`, `last_ack_utc`. |
| `t_end_utc` | When the run finished. |

## `figures/` — rendered output (separate from `runs/`)

`crdblab report figures` reads validated runs (via
`crdblab/analysis/loader.py::load_run()`) and writes PNG+SVG pairs into
`figures/` (also gitignored), named
`fig<N>_<name>_<engine>_<profile>_<run_id...>.{png,svg}` — engine, profile
and every contributing run id are always in the filename
(`figures.py::_provenance_slug()`), so a figure can never silently
overwrite one from a different engine, profile or run.

---

## Consolidated CSV exports in `runs/legacy-runs/` (added 2026-09-10)

On 2026-09-10, every run directory recorded up to that point (58 directories,
2026-09-05 through 2026-09-09, ~33 MB) was consolidated into per-
`(engine, run-type[, stream])` CSVs, and the original per-run directories
were then deleted to declutter `runs/` — this was a one-time migration, not
an ongoing pipeline. The export was written directly into `runs/` and
subsequently moved by hand into `runs/legacy-runs/`, which is where these
files and `runs-index-data.csv`/`raw-output-archive.tar.gz` (below) now live.
**Runs recorded from 2026-09-10 onward are not part of this export** and
still get their own full `runs/<run_id>/` directory as described above;
nothing about the harness's own recording behaviour changed.

**Naming convention:** `<engine>-<category>[-<stream>]-data.csv`, where
`category` is one of `network`, `bench`, `chaos-recover`, `chaos-dead`,
`legacy-p2-baseline`, `legacy-p3-cluster` (the last two are the two
pre-rearchitecture runs described above), and `-<stream>` is present only
for categories with more than one underlying CSV per run (`chaos-recover`/
`chaos-dead`, which fan out into `-metrics-data.csv`, `-audit-data.csv` and
`-probe-data.csv`).

Every row carries these **provenance columns** first, followed by that
stream's normal columns exactly as documented above (`COLUMNS`,
`NETWORK_COLUMNS`, `AUDIT_COLUMNS` or `PROBE_COLUMNS`):

| Provenance column | Meaning |
|---|---|
| `run_id` | The original run directory's name — the join key back to `runs-index-data.csv` below. |
| `engine` | `cockroachdb` or `postgresql` (defaulted the same way `manifest.engine` is). |
| `category` | Which of the six categories above. |
| `profile_name` | The profile this run used (`smoke`, `thesis`, `thesis-extended`, or empty for the two runs whose `manifest.json` never got written — see `runs-index-data.csv`'s `manifest_present` column). |
| `git_revision` | The code revision that produced the run, where known. |
| `started_utc` / `finished_utc` | From the original manifest. |

**`runs-index-data.csv`** is the top-level index, one row per original run
directory, independent of stream: `run_id`, `engine`, `category`,
`profile_name`, `schema_version`, `git_revision`, `started_utc`,
`finished_utc`, `preflight_ok`, `notes_count`, `manifest_present`, and a row
count per stream (`rows_metrics`, `rows_network`, `rows_audit`,
`rows_probe`) — use it to find which runs exist and whether their pre-flight
passed before diving into the per-stream files.

**What this export does *not* capture:** `hardware_metrics.csv` (no legacy
run predates 2026-09-10, so there was nothing to consolidate), `events.json`
(structured but not columnar — read the original field-by-field description
above if you need a specific chaos run's fault-timing detail; consider
extending the export script if this is needed in bulk), `preflight.json`'s
full per-check detail (only the pass/fail summary made it into
`runs-index-data.csv`), and each run's `raw/` generator/ping stdout, which
is unstructured text rather than tabular data — those were archived
separately as `runs/legacy-runs/raw-output-archive.tar.gz` (one entry per
`<run_id>/raw/<file>`, ~6 MB uncompressed) rather than dropped, since
`CLAUDE.md`'s design commitment is that every figure trace back to retained
raw output.

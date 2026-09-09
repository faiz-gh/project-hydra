# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`crdblab` is a measurement harness for a dissertation experiment: a five-node,
three-provider (GCP/Azure/Linode) database testbed used to compare
CockroachDB against PostgreSQL under Patroni for HA, on identical topology.
It replaces a collection of standalone scripts whose independently maintained
parsing logic diverged and produced silent, "flattering" measurement defects
(referred to by ID, e.g. D6, D8, D9, throughout the code and docs — the
catalogue they were originally recorded in, `docs/defects.md`, has since been
removed from the repo; treat a `DN` citation as a stable label for a known
failure mode, not a working link).
The design goal throughout is **defensibility**: every figure must trace back
to a known git revision, a declared profile (`profiles/*.yaml`), and the
generator's retained raw output.

**Status as of 2026-09-09 (this note goes stale the moment a run completes —
update or delete it then):** **the PostgreSQL arm has completed a full
end-to-end run for the first time in this project** —
`runs/_logs/experiment-20260909T040237Z.log`, smoke profile, all four phases
plus validate, analyse and figures, 13 m 51 s. Every previously unexercised
PostgreSQL path ran: the strengthened switchover-candidate check (it waited out
a `/replica answered 503`, then passed on `streaming on timeline 2`), the
`patronictl switchover` that follows it, **Phase IV following Phase III**, and
`restore_target`'s `dead`-mode PostgreSQL branch (`rejoined 5/5 live after
57s`). Both chaos phases produced real numbers with **untruncated coverage on
both instruments**: recover RTO 34.1 s observed outage (probe) / 33.9 s (audit
writer), dead 28.3 s / 28.5 s, RPO 0 in both.

**Update — the thesis-scale PostgreSQL run happened next, and it is the new
status.** `./run-experiment.sh --engine postgresql` against `profiles/thesis`
(`runs/_logs/experiment-20260909T043036Z.log`): the load, Phase I, Phase II and
Phase III all completed with real numbers for the first time at disk-bound
scale; Phase IV failed on a **ninth** distinct failure mode, one step past the
eighth. All three predictions the previous version of this note made were
confirmed. (1) Performance RTO came back `not regained within the run (needs
>=3810 tps)` on the recover phase, as expected from the primary-relocation
gotcha. (2) The Phase IV candidate wait did have to sit and re-clone — see
below, though it was a different check that ultimately failed it. (3) The load
dominated the wall clock: **4467 s (74.5 min)** of the run's total, loading
3,750,063 rows at 814-873 ops/s instantaneous, settling around 815-865 ops/s
cumulative — noticeably slower than the 125k-row smoke load's rate as the
index outgrew cache, exactly as expected.

**Phase I** recorded a 70.311 ms quorum floor (`linode-2`, unchanged ranking
from prior deployments) and passed every clock-offset check. **Phase II**
swept all 12 tiers clean — the first disk-bound PostgreSQL benchmark this
project has ever produced: C=200 read p50 10.3-12.4 ms / update p50 152.5-
159.4 ms, all 27 pre-flight checks passed. **Phase III** (`recover`,
`runs/20260909T060600Z_p4-chaos-recover`) is the first disk-bound resilience
number on either engine: baseline 4762.3 tps, recovery floor 3809.8 tps (80%
held for 10s), **RTO availability 35.62 s**, write outage 31.68 s between
acknowledged writes, RTO probe outage **31506 ms** (21.7 ms resolution,
detection lag 14170 ms), **RPO 0 acknowledged writes lost of 360** (3
ambiguous, 0 of which committed). Both instruments agree closely and neither
flagged truncated coverage.

**Phase IV failed at the placement-repair switchover, and this is a genuinely
new defect, not a repeat of the eighth.** After Phase III's fault, Patroni
promoted `linode-1`; the repair waited out `gcp-1` answering `/replica` 503,
then declared it ready on `streaming on timeline 6, lag within bounds` — the
exact condition the 2026-09-09 03:13 fix (see cause 7 above) checks for.
`patronictl switchover --leader crdb-linode-1 --candidate crdb-gcp-1 --force`
was issued anyway and still answered `503, Switchover failed`. The cluster
table the failure printed shows why: `crdb-gcp-1` was `Role: Replica` — not
`Quorum Standby`, unlike the other three survivors — despite being on the
correct timeline with **zero lag on both Receive and Replay LSN**. Patroni
tracks `synchronous_standby_names` membership separately from streaming state,
on its own `loop_wait` cadence, so a node can hold the leader's timeline with
no lag for one poll before the leader admits it to the synchronous set — and
`synchronous_mode: quorum` refuses a switchover to a candidate that is not yet
in that set, `--force` notwithstanding. Confirmed against Patroni's own REST
API docs: `GET :8008/quorum` "returns HTTP status code 200 only when this node
is listed as a quorum node in `synchronous_standby_names` on the primary" —
the same fact `patronictl list`'s Role column renders, and the one thing
neither `/replica` nor the member's own `/patroni` document exposes.
**Fixed, and verified live the same day.** `patroni_candidate_ready` now adds
`GET :8008/quorum` as a third gate after streaming+timeline, so the
settle-window wait spans the full convergence rather than ending one poll
early. Two regression tests added
(`test_a_streaming_correctly_timelined_replica_can_still_not_be_a_quorum_member`,
`test_the_wait_holds_for_quorum_membership_after_streaming_is_already_true`),
245 tests passing at the time. **Confirmed against a real cluster on
`experiment-20260909T151320Z.log`** (smoke, 2026-09-09 15:13, 13 m 15 s,
against a redeployed testbed — see below): the log line reads `gcp-1 is a
candidate now: streaming on timeline 2, lag within bounds, quorum member`,
the switchover took, and **Phase IV completed end to end** — fault injected,
RTO measured (availability 30.08 s, probe outage 26.07 s, RPO 0/87), target
restored (`rejoined 5/5 live after 30s`), primary switched back to the
gateway, all four runs validated, all figures rendered. This is the first
smoke run in the project's history to complete all four phases without any
manual intervention.

**The deployment that failed was rebuilt before this smoke run** — the SSH
host key for `crdb-gcp-1` changed between sessions, consistent with a fresh
`terraform apply` rather than a fault (this is expected and already disclosed:
`run-experiment.sh`'s `SSH_OPTS` comment states `StrictHostKeyChecking=no` is
deliberate because "the testbed is destroyed and rebuilt repeatedly and
addresses get reused"). Confirmed live 2026-09-09 ~15:40: `patronictl list`
shows Leader on `gcp-1`,
all four others `Quorum Standby / streaming`, timeline 5, zero lag everywhere;
`shared_buffers` 978 MB, `effective_cache_size` 2934 MB; `synchronous_mode:
quorum` / `synchronous_node_count: 2` / `synchronous_mode_strict: true`
producing `ANY 2 (...)` naming all four standbys; `wal_keep_size: 4GB` and
`remove_data_directory_on_diverged_timelines: true` both present; 45 GB free
of 49 GB on `gcp-1`'s root volume. **The one thing this redeploy has NOT yet
had is the thesis-scale load** — the smoke run above reloaded only 125,063
rows (smoke's own `insert_count`), confirmed live by `SELECT count(*) FROM
usertable` returning `125063`. The thesis run will reload the full 3.75M rows
itself (~70-110 min) the same way every prior thesis run has; this is not a
blocker, just not yet done.

**Still unexercised: Phase IV (`dead`) at disk-bound scale, on either engine.**
Phase I-III have run for real on the PostgreSQL arm at the current profile;
the `dead` fault has not, on either arm, because CockroachDB's thesis-scale
sweep predates the profile's move to 3.75M rows (see below) and PostgreSQL's
first attempt failed before reaching it. The fix above is what stood between
the project and this, and it is now cleared.

**Every recorded run predating this one was historical or incomparable, and
as of 2026-09-10 none of them exist as run directories any more.** They were
taken at `insert_count: 125000` (~205 MB, fully memory-resident);
`profiles/thesis.yaml` and `thesis-extended.yaml` are now **3,750,000**
(~6.15 GB, ~1.5x node RAM), so nothing recorded before that change was
comparable against anything produced at the current profile in the first
place. That included the CockroachDB thesis-scale sweep of 2026-09-08
(`20260908T053558Z_bench_cluster`, ~33 min wall clock, bench 18.5 min against
an 885 s floor) which had been the reference the PostgreSQL arm was held
against. On 2026-09-10, all 58 run directories recorded between 2026-09-05
and 2026-09-09 (this one included) were consolidated into per-engine/phase
CSVs and deleted to declutter `runs/` — see `docs/data-schema.md` for the
full schema and `runs/legacy-runs/*.csv` (plus `runs-index-data.csv` and
`raw-output-archive.tar.gz` for the generator/ping stdout) for the data
itself. A run-id cited anywhere in this file from here on is a historical
label, the same way a `DN` defect citation is — not a path that still
resolves to a directory on disk. Both arms still have to be re-taken at the
current profile, including CockroachDB's `dead` phase, before `analyze
engine-comparison` means anything — that was true before the consolidation
and is unchanged by it.

**It took seven attempts to complete one PostgreSQL run.** The seventh
(2026-09-09 04:02, smoke) is the one that finished; the six before it each
failed later than the last, for eight causes — the fifth attempt produced two,
one that stopped it and one that would have corrupted its result had it
continued, and the sixth did the same. All eight are fixed in code, and the
history is kept because every one of them is a failure mode that produced
*plausible* output rather than an obvious crash:
(1) 2026-09-08 — the deployment never came up: `bootstrap-patroni.tftpl` wrote
its config to `/etc/patroni/patroni.yml` while Ubuntu's packaged
`patroni.service` reads only `/etc/patroni/config.yml`, so every node
provisioned "successfully" with no database process anywhere.
(2) 2026-09-08 19:18 — the cluster came up healthy and the **data load** died
after 6 m 10 s in "creating load generator" with `server conn crashed?
(SQLSTATE 08P01)`. Not a database fault: HAProxy's `timeout server 300s` is an
*idle* timeout and was killing connections opened early in a slow serial
warm-up before the generator ever used them.
(3) The primary was never pinned, so it landed on `crdb-azure-2` (eastasia,
199 ms from the client) — the reason that warm-up was slow enough to trip the
timeout, and a confound in its own right (see the pinning gotcha).
(4) 2026-09-08 22:57 — the sweep aborted at the health gate with "1 healthy
member(s), expected 5" while four replicas answered 503. Nothing was wrong:
503 is what a Patroni member returns while it is still taking its basebackup.
The gate read once; it now waits (see the gotcha).
(5) 2026-09-09 01:16 — Phases 0-III all passed and **Phase IV aborted at the
placement check**: after Phase III's partition the demoted primary `gcp-1`
diverged, `pg_rewind` could not run because `gcp-1` had already recycled the
WAL it needed, and Patroni refused the switchover with "no good candidates have
been found". Structural rather than unlucky — it happens after every Phase III
run against the primary. See the WAL divergence gotcha.
(6) The same run (5) also produced a **Phase III result that was wrong in the
flattering direction**, which nothing would have caught:
both RTO instruments blocked on connections the partition had black-holed,
stopped observing 3.6 s after the fault, and the harness reported their silence
as a 0.082 s RTO for an outage the generator recorded as two consecutive ticks
of zero throughput. See the instrument-coverage gotcha. **This run's artefacts
were retained deliberately as the regression case both fixes are tested
against, and still are** — its `metrics.csv`/`audit.csv`/`rto_probe.csv` rows
survive the 2026-09-10 consolidation inside `runs/legacy-runs/*-data.csv`,
filterable by `run_id`, even though the run's own directory is gone (see the
note above).
(7) 2026-09-09 03:13 — Phases 0-III passed again and **Phase IV aborted at the
placement check again, on a different condition**: the demoted primary did come
back up this time (so (5)'s fix worked as far as it went), but as a `Replica` on
**timeline 1**, unattached, while the rest of the cluster streamed on timeline
2. `/replica` answered 200 for it — a member attached to nothing reports no lag
— so the candidate wait ended after one reading and `patronictl switchover`
answered `503, Switchover failed`. See the candidate-eligibility gotcha.
(8) The same run (7) also made the Phase III measurement **impossible rather
than merely wrong**: the smoke run was 45 s long with the fault at 15 s, and
both instruments were still inside the outage when observation ended. They
reported the RTO as UNMEASURED, correctly. The next run measured that same
outage at 37.6 s. See the recover-run-length gotcha.

**What has never run against a real cluster, as of the smoke run above: only
the disk-bound `dead` fault on PostgreSQL, and CockroachDB's `dead` fault at
the current 3.75M-row profile.** Everything else — including the `/quorum`
gate — has now executed live at least once, smoke-scale on this redeployment
and thesis-scale on the one before it.

Order of work from here: **the deployment is up and confirmed healthy**
(`patronictl list`, `SHOW shared_buffers`/`synchronous_standby_names`,
`wal_keep_size`, disk space all re-checked live 2026-09-09 ~15:40, see above)
— no redeploy needed. (1) **`./run-experiment.sh --engine postgresql`** —
budget **~2.5-3 h**, mostly the 3.75M-row load, since this deployment only has
the smoke-scale 125,063 rows loaded so far. Watch that Phase IV completes:
`gcp-1` should reach `Role: Quorum Standby / streaming` (the log should print
`... quorum member` on the candidate check) before the switchover is
attempted — the smoke run confirms the code path, not the thesis-scale timing
of it, since a re-clone at 6 GB behaves differently from one at ~200 MB. (2)
Re-take the CockroachDB arm at the current profile, including its `dead`
phase, which is no longer optional. (3) `crdblab analyze engine-comparison
--crdb <run> --pg <run>` — which **currently refuses every cross-engine pair**
and needs the flag-gate decision first (see the gotcha).

**Timing at the current profile is dominated by the load, not the sweep.** The
bench sweep is a fixed 12 tiers x 60 s plus cooldowns and is unchanged. The
PostgreSQL load is not: it runs through the generator insert-only at
`LOAD_CONCURRENCY=64` against a ~70 ms synchronous-replication commit, so
~900 rows/s and falling as the index outgrows cache — **70-110 min** for
3.75M rows, against ~2 min at 125k. The load is latency-bound rather than
CPU-bound, so raising `LOAD_CONCURRENCY` scales it nearly linearly (192 would
put it near ~23 min, and `max_connections` is 500), but that path has broken
three times historically, so treat the change as deliberate rather than
routine.

Check `tailscale status` before assuming the cluster is reachable — this
development machine has intermittently been unable to resolve `crdb-gcp-1`.

**Update — 2026-09-10, code-only changes, none of it exercised against a live
cluster yet.** Three things landed, prompted by the recover/dead throughput
settling far below baseline (~9%/~4%) on the 2026-09-09 thesis-scale
PostgreSQL run and not being classifiable as recovered, degraded, or still
trending within the recorded window.

(1) **Chaos runs observe longer after the fault, so
`resilience.post_fault_steady_state`'s CV<0.25 settling test can actually
resolve which of those three the tail is**, rather than running out of
recorded series first. `profiles/thesis.yaml`'s `chaos.min_post_fault_s` is
now 450 (was 60; `recover` runs ~9.25 min, `dead` ~8.5 min);
`profiles/thesis-extended.yaml`'s is 900 (`recover` ~16.75 min, `dead`
~16 min) — deliberately different, thesis-extended's chosen to clear the
~800-850-post-settle-tick point where the CV crossed 0.25 when the 2026-09-09
run's own recorded series was replayed with a longer synthetic tail,
thesis.yaml's set to half that by request since it is the canonical
cross-engine comparability profile and its own run budget matters more.
Neither value has been verified live yet — the smallest real check is a
standalone `crdblab --engine postgresql chaos run --profile thesis-extended
--mode recover` (and `--mode dead`) against the already-loaded thesis-scale
table, ~35-40 min for both, no need to repeat the load or Phase II first. See
the profiles' own `chaos:` comments for the full derivation.

(2) **Per-node CPU/memory/disk/network utilisation is now collected during
Phase II-IV**, polled directly from each node's node_exporter
(`crdblab/core/hardware_metrics.py`, new), across all 5 cluster nodes plus
the client node, written to a new `hardware_metrics.csv` per run (schema in
`docs/data-schema.md`). `terraform/scripts/bootstrap-{cockroachdb,patroni,
client}.tftpl` now install/verify `prometheus-node-exporter` on every node —
**this needs a redeploy to take effect** (cloud-init only runs at first
boot), so it will not appear on the currently-running deployment until the
next `terraform apply`. No firewall changes were needed anywhere: like
Patroni's `:8008` REST API, node_exporter's `:9100` rides the Tailscale mesh
that already carries every other cross-node service.

(3) **`runs/` was decluttered.** All 58 run directories recorded before this
date were consolidated into per-engine/phase CSVs and deleted — see the note
under "Every recorded run predating this one..." above and
`docs/data-schema.md` for what moved where. `figures/` was cleared too
(everything under it deleted by hand); the next `report figures` invocation
regenerates from whatever runs exist at that time. Nothing about how a *new*
run is recorded changed — `crdblab bench`/`chaos run`/`net probe` still write
a full `runs/<run_id>/` directory exactly as described in
`docs/data-schema.md`'s "Where the data lives" section, and that document is
the reference for artifact layout going forward, superseding any ad-hoc
description in this file.

**This project was rearchitected from a different design.** It originally
compared the five-node CockroachDB cluster against a separate *unreplicated*
single-node baseline to isolate replication cost, with the generator running
directly from the CockroachDB gateway (`crdb-gcp-1`). That has been fully
replaced by a same-topology, cross-engine comparison: the identical five-node
cluster is stood up once per engine (CockroachDB, then PostgreSQL/Patroni —
two separate `terraform apply`s, selected by `var.database_engine`) and driven
from a dedicated **client node** (`CLIENT_NODE`, `crdb-client-1`) that is not
itself a cluster member. The old unreplicated baseline node, the
`raft-overhead` analysis command, and the `bench single`/`bench cluster`
CLI split are gone; `crdblab analyze engine-comparison --crdb <run> --pg <run>`
is the only cross-run comparison now, and `--engine {cockroachdb,postgresql}`
(default `cockroachdb`) is a **top-level** flag that must precede the
subcommand, e.g. `crdblab --engine postgresql bench --profile ...`.

`README.md` documents the harness's design commitments (why validation and
pre-flight are separate gates, why concurrency isn't load, why seeds must
match, etc.) — read it before changing any analysis or pre-flight code, since
several of these constraints exist specifically to prevent a defect that
already happened once. `instructions.md` is the full operator runbook
(provisioning through teardown) and is the place to check for **why** a given
CLI flag or ordering constraint exists.

**Phase numbering (current, post-rearchitecture):** Phase I = `net probe`
(network substrate); Phase II = `bench` (the benchmark, one run per engine);
Phase III = `chaos run --mode recover`; Phase IV = `chaos run --mode dead`.
This is consistent throughout `README.md`, `instructions.md`,
`run-experiment.sh`'s step labels, and `crdblab/cli.py`'s help text. It is
**not** consistent with on-disk naming: run directories are still suffixed
`_bench_cluster`, `_p4-chaos-recover`, `_p4-chaos-dead` (i.e. `recover` is
still a "p4" run id even though it's Phase III in prose), and
`p4_chaos.py`/`phase="p4_chaos"` in the manifest cover both Phase III and IV.
Renaming those would touch glob patterns in `cli.py` and `run-experiment.sh`
and invalidate existing run directories, so it was deliberately left alone —
don't assume the run-id suffix tells you the current phase number. Historical
prose describing the *removed* pre-rearchitecture design (an unreplicated
single-node "Phase II baseline" compared against a "Phase III cluster") was
deliberately left unrenumbered where it's describing an incident that
happened under the old scheme (mostly in `preflight.py`, `validation.py`,
`workload.py` docstrings) — a `DN`-cited defect story about the old baseline
is not the same claim as "Phase II" today, and renumbering it would misstate
history rather than clarify it.

## Commands

```bash
# Setup (venv lives at .venv/, not repo root; deps come from pyproject.toml)
python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"

# Tests
.venv/bin/python -m pytest tests/ -q                    # full suite
.venv/bin/python -m pytest tests/test_topology.py -q    # single file
.venv/bin/python -m pytest tests/test_analysis.py::test_name -q  # single test

# Lint
.venv/bin/ruff check .

# CLI entry point (installed as console script `crdblab`)
.venv/bin/crdblab --help
```

There is no build step; this is a pure-Python CLI package (`crdblab = "crdblab.cli:main"`).

### End-to-end experiment flow (for context, not something you'll normally run)

```bash
./run-experiment.sh              # full sweep against a live testbed; at the
                                 # current profile budget ~2.5-3 h, most of it
                                 # the 3.75M-row load, not the 12x60s sweep
./run-experiment.sh --smoke      # harness self-test (~14 min; still 125k rows,
                                 # so it does NOT exercise the disk-bound path)
./run-experiment.sh --engine postgresql   # the deployed engine is PostgreSQL/Patroni
```

This drives `terraform` (provisioning), then the CLI phases in order:
`crdblab net probe` → `crdblab bench` → `crdblab chaos run` → `crdblab validate`
→ `crdblab analyze ...` → `crdblab report figures`. The ordering is
load-bearing (each phase consumes artefacts the previous one produced), not
conventional — see `instructions.md` §6 before reordering anything.
`run-experiment.sh` takes `--engine {cockroachdb,postgresql}` (default
`cockroachdb`, also asked for by its interactive prompt when it is run with no
arguments on a TTY). It **names the engine already deployed** on the testbed —
it does not deploy anything, since switching engines is a `terraform apply
-var="database_engine=..."` that replaces every cluster node. One invocation
measures one engine; run it once per engine and then compare with `crdblab
analyze engine-comparison --crdb <run> --pg <run>`, which the script never runs
itself. The flag is spliced in *before* the subcommand (`crdblab --engine ...
bench`) because it is top-level, and is given to the three commands that write a
run — `net probe`, `bench`, `chaos run` — but not to `validate`, `analyze` or
`report figures`, which take run ids that already name their engine.
It also branches the script's own pre-flight and repair steps, which are
otherwise CockroachDB-only: node-liveness and `lease_preferences` become a poll
of every member's Patroni REST API (`:8008/health`, plus an assertion that
exactly one answers `:8008/primary` — *which* node is repaired rather than
asserted there, by a `patronictl switchover` step, so a primary that merely
drifted after a chaos run is fixed instead of aborting the sweep); the row count
switches from `cockroach sql --url` to `psql`, which cannot be skipped because
`cockroach sql` is not a general postgres client; and the dead-mode restore
backstop becomes `sudo -n systemctl start patroni` swept across every member
rather than aimed at `chaos.target`, because for PostgreSQL the fault target is
resolved live and is only recorded in the run's `events.json`.
It also `export PYTHONUNBUFFERED=1`s before running anything, and `load_data`/
`count_rows` inside it build their own list of single-host candidate URIs from
`crdblab/topology.py` rather than trusting `DB_URI`'s host segment verbatim —
see "Known gotchas" below for why both exist.

## Architecture

**`crdblab/topology.py`** — single source of truth for the testbed's node
inventory (host, provider, region, locality, which node is the gateway). All
legacy per-script copies of this data were deleted because divergence between
them was a source of real experimental error (see the module docstring for
the gateway-move history — it matters for interpreting old runs).
`DEFAULT_TOPOLOGY` is the five cluster nodes; `CLIENT_NODE` (`crdb-client-1`,
GCP) is the dedicated node the workload generator and audit/probe clients run
from — not a cluster member, and not the gateway (`crdb-gcp-1`, still a
`DEFAULT_TOPOLOGY` member and still where the leaseholder is preferred). There
is no `BASELINE_NODE` any more.

**`crdblab/config.py`** — `Profile` (workload + chaos parameters, loaded from
`profiles/*.yaml`, copied verbatim into every run manifest) and `Settings`
(reads `DB_URI` / `CRDBLAB_RUNS_DIR` from `.env`, resolved via
`load_env_file()` relative to the package root, not cwd). A `Profile` fixes
the generator seed/insert-count together deliberately — a load and a sweep
using different seeds silently address different keyspaces, which is the
project's most dangerous failure mode (fails *flatteringly*, not loudly).

**`crdblab/core/`** — mechanics shared across phases:
- `ssh.py` — remote command execution against testbed nodes. Also home to
  `SUDO` (`sudo -n`), since both `core.preflight` and the `phases` need it and
  `core` may not import `phases`.
- `workload.py` — parses the CockroachDB generator's stdout. Column binding
  is by the generator's own header line, never positional, on purpose.
- `preflight.py` — asks "was the system fit to be measured?" *before* a
  measurement runs (seed/insert-count agreement, quorum floor achievability,
  leaseholder placement, hardware fingerprint). Distinct from `validate`
  (below), which asks a different question after the fact — see README.md's
  design commitments for why both are needed and neither substitutes for the
  other. Also holds `resolve_patroni_primary` (the single live reading of which
  node is PostgreSQL primary, re-exported from `p4_chaos`) and
  `check_patroni_primary_placement`, the PostgreSQL counterpart to
  `check_leaseholder_placement` — the one check here that *repairs* rather than
  only asserting, because Patroni never fails back on its own.
- `recorder.py` — writes the run directory (`manifest.json`, `metrics.csv`,
  `preflight.json`, raw generator stdout under `raw/`, and since 2026-09-10
  `hardware_metrics.csv` where the profile enables it).
- `rto_probe.py` — the independent high-frequency canary-write client used
  during chaos runs to time recovery at sub-second resolution; separate from
  both the generator and the RPO audit writer because those sample too
  coarsely to answer "when did writes resume."
- `hardware_metrics.py` (new 2026-09-10) — polls every node's node_exporter
  (`:9100/metrics`) directly over HTTP during Phase II-IV, the same idiom
  `preflight.py` already uses for Patroni's `:8008`, not a standalone
  Prometheus server. Parses only the handful of metric families this project
  reads and differences consecutive scrapes into rates (CPU busy%, disk/net
  bytes-per-sec); a node's first poll has no prior scrape to diff against, so
  those columns are `""`, never `0`, on the first row (D5). See
  `docs/data-schema.md` for the full column schema.

**`crdblab/phases/`** — one module per measurement phase:
- `p1_network.py` — RTT matrix, clock offsets, derives the quorum floor
  other phases assert against. Takes `engine=` and records it in the manifest:
  the measurement does not depend on the engine, but the deployment does (a
  redeploy replaces every cluster node), and it is what names the figure.
- `bench.py` — throughput/latency sweep across concurrency tiers. `Target`
  carries an `engine` (`"cockroachdb"` or `"postgresql"`); `Target.db_uri` is
  a **single** connection string in both cases — for CockroachDB, the gateway
  (`crdb-gcp-1`); for PostgreSQL, `127.0.0.1:5000` on the client node, where
  HAProxy (installed by `bootstrap-client.tftpl`) fronts the Patroni cluster.
  It briefly connected to every cluster member at once for CockroachDB; don't
  reintroduce that — `cockroach workload run`, given more than one URL, dials
  its `--concurrency` connections *serially* against the list rather than in
  parallel (~2.65s each measured on this topology: 0.83s total at C=200 with
  one URL, 9 min with five), and nothing during a benchmark sweep needs the
  multi-host tolerance that would buy. Pre-flight checks that are
  CockroachDB-specific (leaseholder placement, server-config capture) are
  skipped when `engine == "postgresql"`, which instead gets its own branch
  asserting clock offset and `check_patroni_primary_placement` — the sweep is
  exactly where a mislocated primary does its damage, since the generator dials
  `--concurrency` connections serially and pays the client→primary RTT on every
  one. There is a single
  `cluster_target(settings, database, engine)`; no `single_target()`.
- `p4_chaos.py` — fault injection + RTO/RPO measurement, runs the RTO probe
  alongside. The fault payload is chosen by `get_payload(mode, engine)`
  (`killall -9 cockroach` vs `killall -9 patroni postgres`); DSNs and the
  audit-table admin connection branch the same way `bench.py` does. The
  generator connects to a single node that is never the fault target (same
  multi-host-serial-dial reason as `bench.py` — this one is not hypothetical:
  a completed run had its fault fire ~192s before the generator's first
  sample because of it). The RPO audit connection and the RTO probe *do* use
  a multi-host DSN deliberately — they're single psycopg connections (or a
  small worker pool), not `--concurrency`-many, and need to keep writing
  through the fault target's death, which is the point of measuring RPO at
  all. For PostgreSQL the run first calls
  `preflight.check_patroni_primary_placement`, which switches the primary back
  onto the gateway if a previous phase's failover moved it — that has to happen
  *before* the target is chosen, or Phase IV would fault a different node than
  Phase III did and than either CockroachDB phase did. Only then does
  `resolve_patroni_primary()` query every node's `:8008/primary` live and target
  whichever one actually answers. The pin makes the answer predictable; the live
  read is what makes it true, and the profile's static `chaos.target` is still
  overridden if the two disagree.

**`crdblab/analysis/`** — everything that turns a run directory into
numbers. `loader.py::load_run()` is the **only** sanctioned entry point into
a run's data; it enforces that the run passed both pre-flight and `validate`
before any analysis can see it. `steady_state.py` (per-run throughput/
latency), `validation.py` (internal-consistency checks: plausibility
ceiling, quantile ordering, Little's law, sample cadence, error
monotonicity), `resilience.py` (RTO/RPO with their measurement limits),
`engine_comparison.py` — CockroachDB vs PostgreSQL on the *same* replicated
five-node topology, gated on `validation.check_run_comparability` (asserts
hardware/flags/version/workload match before comparing), in three
load-explicit framings (throughput-latency curve, matched-throughput scalars,
matched-utilisation scalars) plus a lightest-load write-median comparison and
a same-concurrency delta explicitly labelled **NOT A RESULT**. Wired up via
`crdblab analyze engine-comparison --crdb <run> --pg <run>`. There is no
`raft_overhead.py` any more — it was the pre-rearchitecture equivalent
against the unreplicated baseline node, deleted along with that node.

**`crdblab/report/figures.py`** — renders the dissertation's figures from
validated runs only (goes through `loader.load_run()`), stamping source run
ids into each figure's footer.

**`crdblab/cli.py`** — argparse wiring for all subcommands
(`capture`, `net probe`, `bench`, `chaos run`, `probe rto`, `analyze
{steady-state,engine-comparison,resilience}`, `report figures`,
`validate`, `profile`). Each `_cmd_*` function is a thin adapter over the
modules above. A top-level `--engine {cockroachdb,postgresql}` flag
(default `cockroachdb`) is parsed on the root parser and read via
`args.engine` in `_cmd_bench`, `_cmd_chaos` and `_cmd_net_probe` — the three
commands that *write* a run, each of which records it in the manifest.
`validate`, `analyze` and `report figures` deliberately don't take it: they are
given run ids, and every run already states its own engine. **It must precede
the subcommand** (`crdblab --engine postgresql bench --profile ...`) — argparse
rejects it after the subcommand name, since `bench`'s own subparser doesn't
declare `--engine`.

**`terraform/`** — provisions the six-machine testbed (5 cluster nodes across
GCP, Azure and Linode, joined over Tailscale, plus 1 dedicated client/
generator node) via cloud-init, driven by `var.database_engine`
(`cockroachdb` or `postgresql`, plumbed into every `*_node` module — a
redeploy for the other engine means editing this variable and re-applying,
which replaces every cluster node; it is not both engines running at once).
`terraform/modules/*_node/` are per-provider cluster node modules;
`terraform/modules/client_node/` provisions the client node (GCP). There is
no `local_node` module any more. `terraform/scripts/` has three cloud-init
templates: `bootstrap-cockroachdb.tftpl` and `bootstrap-patroni.tftpl`
(selected per cluster node by `database_engine`) and `bootstrap-client.tftpl`
(installs Tailscale, chrony, HAProxy, `psql`, and the `cockroach` client
binary — no server — on `CLIENT_NODE`). This is infrastructure for the live
testbed, not something touched by most code changes to `crdblab/`.

## Known gotchas (found and fixed — don't reintroduce)

- **Never pass `cockroach workload run`/`workload init`/`cockroach sql --url`
  more than one connection string, in any form.** Given multiple positional
  URL arguments, `cockroach workload run` dials its `--concurrency`
  connections *serially* against the list, at ~2.65s each measured on this
  topology — 0.83s total at C=200 with one URL, **9 minutes** with five. Given
  a single URL with a comma-separated host list in the *authority* segment
  (`postgresql://root@host1:26257,host2:26257/db`), it's worse: the tool
  doesn't split on the comma at all, it hands the whole string to Go's DNS
  resolver verbatim and fails with `no such host`. `bench.py` and
  `p4_chaos.py` both briefly connected to every cluster member at once for
  CockroachDB (`Target.db_uris`, a since-deleted plural property); both now
  use exactly one host (`Target.db_uri` for bench; a node other than the
  fault target for chaos). This is not hypothetical: it once made a `thesis`
  sweep take 3x its estimate, and separately made a chaos run's fault fire
  ~192s *before* the generator's first sample (the timer starts before the
  generator's connection-setup phase, so a multi-minute setup silently
  outraces `chaos.inject_at_s`). `bench.py::_run_tier` and `p4_chaos.py::run`
  both now print a `WARNING` and a manifest note if connection setup ever
  again exceeds 10s, specifically to catch a regression of this class loud
  rather than silently produce an unusable run. The RPO audit connection and
  the RTO probe *are* still built with multi-host DSNs deliberately — they're
  single psycopg/libpq connections (or a small worker pool), a different and
  much lighter code path that doesn't exhibit this, and multi-host tolerance
  is the entire point of measuring RPO through a fault.
- **`DB_URI` (`.env`) is not read by any measured phase.** It's used only by
  `crdblab capture` and by `run-experiment.sh`'s data-loading step, both of
  which shell out to `cockroach workload init`/`cockroach sql --url` — so the
  multi-host caveat above applies to it too. `run-experiment.sh` handles this
  by extracting just the user/database/query parts out of `DB_URI` (which
  parse fine regardless of what's in the host segment) and rebuilding a list
  of single-host candidate URIs from `crdblab/topology.py`'s node list,
  trying each over SSH until one succeeds — so `DB_URI` can safely be
  multi-host (or even a malformed one, in the sense above) for admin/loading
  purposes without editing `.env`. `crdblab capture`, invoked directly rather
  than through `run-experiment.sh`, has no such fallback and will hit the
  same DNS error if `DB_URI` isn't a single valid host — that's acceptable
  since `capture` pins a layout against one specific `--node` anyway.
- **Piping `crdblab`'s output through anything switches Python from
  line-buffered to block-buffered stdout**, since it's no longer attached to
  a terminal. `run-experiment.sh` pipes everything through `tee` for its log
  file, so without an explicit fix, live per-tier/per-tick progress prints
  queue up invisibly and all appear at once when a buffer fills or the
  process exits — indistinguishable from a hang, and how the connection-setup
  bug above first got noticed. Fixed two ways, both present and both worth
  keeping: `run-experiment.sh` sets `export PYTHONUNBUFFERED=1`, and the hot
  per-tier/per-tick prints in `bench.py`/`p4_chaos.py` additionally pass
  `flush=True` directly.
- **Every chaos payload needs `sudo -n`; the SSH user is not root.**
  `crdb-gcp-1` and both Azure nodes are reached as `ubuntu` (only the Linode
  nodes are `root` — see `topology.py`), while `cockroach`/`patroni` run as
  root and `tailscale down` needs the daemon socket. Unprefixed,
  `killall -9 cockroach` returns `Operation not permitted` and rc=1, and the
  target serves uninterrupted for the whole run. This is not hypothetical:
  the 2026-09-07 and 2026-09-08 `dead` runs both recorded
  `"injected": {"detail": "rc=1"}` and their target's `cockroach` pid was
  unchanged afterwards — yet they produced complete run directories that
  passed `validate` and reported "no write interruption detectable", which
  reads as an excellent resilience result and is a measurement of an
  undisturbed cluster. `recover` is worse: its payload is backgrounded
  (`nohup … &`), so its exit status only reports that the shell forked and a
  denied `tailscale down` is *structurally* invisible in rc. Three defences
  now exist and all three are worth keeping: `preflight.check_fault_authorisation`
  runs a harmless same-privilege probe (`killall -0` / `tailscale status`)
  **before** the measurement and refuses the run if the fault would not land;
  `inject_fault` records `landed`/`stderr` rather than a bare `rc=N`; and a
  `landed is False` run is stamped with a manifest note and a
  `*** THE FAULT DID NOT LAND ***` banner in `analyze resilience`. Old runs
  have no `fault_landed` key and correctly stay silent rather than
  false-alarming.
- **Every PostgreSQL connection needs a password; no CockroachDB one does.**
  The CockroachDB nodes run `--insecure` and accept `root` with no credential,
  so every DSN in this harness was written without one. Patroni bootstraps
  `pg_hba` as `host all all 0.0.0.0/0 md5`, which refuses all of them: the
  generator, the RPO audit writer, the RTO probe agent, and the `psql` that
  creates their tables. `Settings.pg_password` (env `PG_PASSWORD`, default
  `rootpassword` to match `bootstrap-patroni.tftpl`) is the single source;
  `Target.password` carries it into `bench.py`'s DSN and `p4_chaos.run` builds
  the audit/probe DSNs and the `PGPASSWORD=` prefix from it. It is URL-quoted,
  since a `@` or `/` in a password would otherwise re-parse the DSN into a
  different host. `run-experiment.sh` refuses to start when `--engine
  postgresql` meets a `DB_URI` without one, rather than failing mid-load.
- **`cockroach workload init ycsb` defaults to `--families=true`, which is
  CockroachDB DDL.** It puts each column in its own `COLUMN FAMILY`;
  PostgreSQL rejects the statement outright, so the load fails before any row
  is written. `run-experiment.sh` passes `--families=false` on the PostgreSQL
  path only. The flag changes the table's physical layout and nothing the
  workload can observe -- not the rows, the keyspace or the seed -- so the two
  engines' working sets stay comparable.
- **A PostgreSQL `dead` fault has to be arranged around systemd, or it heals
  itself.** `patroni.service` ships `Restart=on-failure`, so a SIGKILL is a
  *failure* by systemd's definition and the unit is back within `RestartSec`
  (~100 ms). CockroachDB has no unit at all -- cloud-init starts it with
  `--background` -- so `killall -9 cockroach` is simply the end of it. Left
  alone, a PostgreSQL `dead` run would have measured systemd's restart loop
  instead of Patroni's failover, and reported a *better* RTO than CockroachDB
  on a fault that was never the same fault. The payload installs `Restart=no` as a **drop-in file**
  (`/etc/systemd/system/patroni.service.d/99-crdblab-chaos.conf`) plus a
  `daemon-reload`, then delivers the kill with `systemctl kill --kill-who=all
  --signal=SIGKILL patroni.service`; `restore_target` deletes the drop-in,
  reloads, and starts the unit. The drop-in is not stylistic: `systemctl
  set-property patroni.service Restart=no`, which this used first, fails with
  "Cannot set property Restart, or unknown property" -- `set-property` only
  takes properties settable on a running unit, essentially the cgroup knobs.
  Measured against crdb-azure-1 on 2026-09-08: rc=1, the `&&` short-circuited,
  and the primary served on untouched (the harness reported the fault as not
  landed, which is what that check is for). **Verified live** with the drop-in:
  patroni went to `failed` and stayed there, postgres processes went to zero,
  Patroni promoted crdb-linode-1 about 30 s later, and `restore_target` brought
  the node back in 29.5 s with `Restart=on-failure` in place and the drop-in
  gone. Signalling the unit's
  cgroup is also the only reliable way to hit Patroni: it runs as
  `/usr/bin/python3 /usr/bin/patroni`, so its `comm` is `python3` and
  `killall -9 patroni` matches nothing, while a `pkill -f patroni` written to
  work around that matches the SSH command carrying it. The postmaster goes
  down with the cgroup, being Patroni's child.
- **`net probe` must skip the leaseholder check on PostgreSQL.** It reads
  placement with `cockroach sql` on the gateway, so against Patroni it does not
  merely not apply -- it fails, and takes Phase I with it. This became
  reachable only when `net probe` gained `--engine`; `bench.py` already skipped
  it for the same reason. Patroni's primary placement *is* now asserted, but by
  its own check (`preflight.check_patroni_primary_placement`, which reads
  `:8008/primary` over HTTP), never by `cockroach sql`.
- **The pre-flight gates now run on both arms of the comparison, and the
  comparison itself no longer refuses to run.** Four related fixes, 2026-09-08:
  (1) `preflight.capture_server_config(node, engine=...)` captures the server's
  argv, version and *hardware* for PostgreSQL too. It was CockroachDB-only, so
  every pg run lacked the `server:` and `host:` manifest notes that
  `validation.check_run_comparability` reads -- meaning the cross-engine
  result, the whole point of the project, was always drawn between a run whose
  machine was recorded and one whose was not. The flags and version are
  *expected* to differ across engines; the machine is not, and it is what D9
  and the unexplained 22% shift of 2026-09-02 turned on. The PostgreSQL probe
  bracket-classes the binary path (`bin/[p]ostgres`) in **both** the `pgrep`
  pattern and the version glob, because `pgrep -f` searches full command lines
  and the whole probe travels as one: unbracketed, the capture came back naming
  this harness's own `bash -c pgrep ...` as the server. (2) `Manifest` gained
  `server_version`, set for both engines; `cockroach_version` is still set for
  CockroachDB so runs recorded before it stay readable. (3)
  `check_run_comparability` compares versions **within** an engine only. Across
  engines a version difference is the variable under study, and treating it as
  an error made `analyze engine-comparison` refuse every comparison it exists to
  produce (`different server versions (v26.3.0 vs None)`); it is now reported as
  a warning naming both builds. (4) `preflight.PostgresRowMatchProbe` +
  `row_match_probe(engine, ...)` give D8 a detector on the PostgreSQL side,
  differencing `pg_stat_user_tables`'s **index** scan and fetched-row counters
  across each tier -- a workload addressing an empty keyspace still scans on
  every operation and fetches nothing, so the rate goes to zero while throughput
  goes *up*. Verified live on all three cases: 200 matching PK lookups gave
  1.0000, 200 lookups matching nothing gave 0.0000 and failed the check, and a
  sequential scan failed it for its own reason. `bench.py` runs the probe **and**
  `check_write_latency_floor` for both engines: Patroni waits for two standby
  acks (`ANY 2 (...)`, which it derives itself from `synchronous_node_count: 2`
  in `synchronous_mode: quorum` -- see that gotcha), the same geometry as a
  3-of-5 Raft quorum, so Phase I's floor bounds both. **The floor
  half of that was claimed here before it was true** and was fixed 2026-09-09:
  `quorum_floor` was computed inside the `engine == "cockroachdb"` branch, so on
  the PostgreSQL arm it stayed `None` and the per-tier
  `check_write_latency_floor` was skipped every time. The thesis-scale pg run
  `20260908T230430Z_bench_cluster` recorded twelve `row_match` checks and zero
  `write_latency_floor`, against twelve of each on the CockroachDB run it is
  compared with -- the two arms were not held to the same gate. It is now
  derived by one `_resolve_quorum_floor()` used by both branches. `n_tup_upd` is deliberately not added to the fetched-row count --
  an UPDATE's index scan already counted the row it found -- and **`seq_tup_read`
  is deliberately not counted as a match**, which is the difference between a
  working detector and a decorative one: it counts rows *read* by a sequential
  scan, not rows matched, so twenty scans matching nothing reported 100,000 rows
  against a 5,000-row table and yielded a "match rate" of 5000, sailing past the
  0.99 minimum on exactly the failure the check exists to catch (measured on
  this testbed). `seq_scan` is read as a separate signal instead: this workload
  addresses rows by primary key, so a sequential scan of its table means the
  plan is not the one being measured, and the tier fails for that.
- **The generator goes through HAProxy; the measurement clients must not.**
  `config.pg_generator_dsn` (one host, `127.0.0.1:6432` — the client node's
  pgbouncer, which forwards to the local HAProxy on `:5000`; it was
  `pg_haproxy_dsn` pointing straight at `:5000` before pgbouncer was needed) is
  for `cockroach workload run`, which has to be given exactly one URL.
  `config.pg_direct_dsn` (every node at 5432, gateway first,
  `target_session_attrs=read-write`, `connect_timeout=2`) is for the RPO audit
  writer and the RTO probe. Both went through HAProxy
  before, which made two deliberately independent measurements share one
  component on the client node: a hiccup there would be indistinguishable from
  a cluster outage, and `on-marked-down shutdown-sessions` drops their
  in-flight connections at every failover. libpq resolves the primary for them
  instead -- the direct counterpart of the multi-host DSN the CockroachDB
  branch already used for these two clients, and safe for the same reason
  (single connections, not `--concurrency`-many, so the serial-dial cost that
  rules multi-host out for the generator does not apply).
  Two things about that host list are load-bearing. **The gateway goes first**
  -- libpq walks the list in order and every host tried before the primary is a
  wasted connect, and in plain `topology.nodes` order the gateway was *last*,
  behind two Azure nodes at 204-230 ms, a cost the row-match probe paid twice
  per tier and 24 times per sweep. **`connect_timeout=2` is a measurement
  decision, not a tuning knob** -- in `recover` mode the fault is a network
  partition, so the partitioned node does not refuse connections, it swallows
  them, and without a bound an RTO derived from these clients would be
  reporting libpq's OS-level TCP timeout rather than the cluster's failover. 2 s
  is also libpq's own minimum (it silently raises anything lower) and is well
  clear of the 230 ms worst-case RTT, so a merely-distant node is never mistaken
  for a dead one. Disclose it alongside the two proxy hops.
- **HAProxy's `timeout server`/`timeout client` on the client node must be far
  longer than a tier, because they are IDLE timeouts and the generator's
  warm-up is serial.** `cockroach workload run` opens its `--concurrency`
  backend connections one at a time (measured ~5.9 s each through
  pgbouncer→HAProxy to an eastasia primary, so a 64-connection load spent
  **6 m 10 s** in "creating load generator"), and every connection opened in
  roughly the first five minutes of that window sits with **zero traffic** --
  the generator has not started issuing operations on any of them yet. At
  `timeout server 300s` HAProxy silently killed them; the first real query then
  landed on an already-dead socket and pgbouncer reported `server conn crashed?
  (SQLSTATE 08P01)`, which killed the whole load because it does not run with
  `--tolerate-errors`. The tell is in `/var/log/postgresql/pgbouncer.log`:
  **every** crashed connection died at exactly `age=305s`, and Patroni's log
  showed uninterrupted leadership with no HAProxy backend transition -- i.e.
  nothing was wrong with the database. Now 3600s. Pinning the primary to `gcp-1`
  shrinks the warm-up enormously (1 ms vs 199 ms per dial) but does not make the
  bound safe to reduce: the largest tier in `profiles/` is C=200.
- **`cockroach workload init` cannot run against PostgreSQL at all, so the
  PostgreSQL working set is loaded by the generator itself.** Its first
  statement is `CREATE DATABASE IF NOT EXISTS <db>` -- CockroachDB syntax that
  PostgreSQL rejects with `syntax error at or near "NOT"` -- and nothing
  suppresses it (`--data-loader NONE`, which only creates the schema, issues it
  too). `--drop` is separately unusable (it asks the server to DROP DATABASE the
  connection is inside) and `--families` is CockroachDB DDL. `run-experiment.sh`
  therefore creates `usertable` with `psql` and then loads the rows by running
  the **generator** insert-only (`--insert-freq=1 --read-freq=0 --update-freq=0
  --insert-count=0 --insert-start=0 --max-ops=$INSERT_COUNT`). The last part is
  the load-bearing one: YCSB keys are derived by the generator from the row
  index (`user10092439283625390464`), so a hand-written loader would have to
  reimplement that derivation, and a keyspace that differs from the one the
  sweep addresses is D8 exactly -- every operation matches nothing and the run
  reports its best-ever throughput. Letting the generator insert its own keys
  makes the two keyspaces the same object. Verified on the testbed: a sweep over
  a table loaded this way reports a row-match rate of 1.0000 (54,646/54,646 at
  C=100). `--max-ops` overshoots by up to `--concurrency` rows because
  operations in flight still complete (5,063 for a requested 5,000 at C=64);
  those rows have indices at or above `--insert-count`, so the sweep never
  addresses them, and the real count is printed rather than silently accepted.
- **The generator reaches PostgreSQL only through pgbouncer.**
  `cockroach workload` v26.3.0 sends `allow_unsafe_internals` as a pgwire
  *startup parameter* on every connection; PostgreSQL rejects unknown startup
  parameters, so both `init` and `run` died at connect with `FATAL:
  unrecognized configuration parameter "allow_unsafe_internals" (SQLSTATE
  42704)`. No flag on the tool suppresses it -- the only related knob is a
  CockroachDB *cluster* setting -- and the alternative was two different
  generator builds across the two arms, a confound in the one component the
  comparison requires to be identical. `bootstrap-client.tftpl` installs
  pgbouncer in front of HAProxy with
  `ignore_startup_parameters = allow_unsafe_internals,...`; `config.pg_generator_dsn`
  points at it (`127.0.0.1:6432`). Two details are load-bearing:
  `pool_mode = session`, so it is a passthrough rather than a semantic change,
  and **`server_reset_query = DISCARD ALL`** -- emptying it, on the theory that
  a passthrough should change nothing, left prepared statements on a recycled
  server connection and the generator died at C=64 with `prepared statement
  "scan" already exists (SQLSTATE 42P05)`. The cost is disclosed rather than
  hidden: the PostgreSQL path carries two local proxy hops (pgbouncer, HAProxy)
  that the CockroachDB path does not, both on the client node's loopback ahead
  of the wide-area link the measurement is about. `pg_direct_dsn`'s clients --
  the RPO audit writer, the RTO probe, `psql` -- speak plain libpq and go
  straight to the cluster, so they are unaffected.
- **Never put a backtick inside an unquoted cloud-init heredoc.** The
  bootstrap templates write their config files with `cat <<EOF > ...`, which is
  subject to shell expansion, so prose comments containing backticked words ran
  as commands: the client node's cloud-init reported
  `line 63: last,libc,none: command not found`, printed HAProxy's usage text
  (from a backticked `haproxy -c`), wrote a corrupted config, and failed
  provisioning -- on a deployment whose five database nodes were perfect. The
  same thing silently deleted three words from Patroni's config comments. All
  three config heredocs are now quoted (`cat <<'EOF'`), which is safe because
  none of them needs *shell* expansion: terraform's own `${...}` is substituted
  when the template is rendered, before the shell ever sees the file.
- **Patroni 3.x ignores `bootstrap.users`, so the `root` role has to be created
  by hand.** It creates only the `superuser` and `replication` roles named under
  `postgresql.authentication`. Observed on the 2026-09-08 deployment:
  `pg_roles` held exactly `postgres` and `replicator`, and every connection the
  harness makes -- all of them as `root`, to match the CockroachDB side --
  failed with "password authentication failed for user root", on a cluster
  whose five members were all healthy. The template now creates `root` and
  `admin` explicitly after bootstrap, as the local `postgres` superuser over the
  unix socket (`auth-local: trust`), and creates `ycsb`/`bench` owned by `root`.
  The `bootstrap.users:` block is kept as documentation of intent, with a note
  saying it does nothing.
- **The databases are created by whichever node actually answers
  `:8008/primary`, which is verified rather than assumed even though the
  bootstrap ordering now makes it deterministically `gcp-1`.** Naming a node in
  advance was a guess and it failed: on 2026-09-08 the primary was
  `crdb-azure-1` while `PEERS[0]` is `crdb-gcp-1`, which left gcp-1 waiting out
  its primary-poll and failing provisioning while `ycsb` was never created at
  all. Every node polls its own `:8008/primary` and stops as soon as it sees a
  peer has won -- exactly one node can answer, so exactly one creates. Keep the
  live check: the cost of verifying is one HTTP request and the cost of
  assuming wrongly is a testbed with no database on it.
- **The bootstrap DDL commits with `synchronous_commit=local`, and it must.**
  The primary can be elected as soon as etcd reaches 3-of-5 quorum, which says
  nothing about whether the two streaming standbys the quorum requires have
  joined -- the other four nodes are still mid `apt-get` on their own cloud
  provider's schedule, and they do not boot in lockstep. `synchronous_commit:
  on` with too few standbys attached blocks a commit **indefinitely** (there is
  no timeout), so an unmodified session would hang the primary's cloud-init
  forever the first time it won the race early. **`synchronous_mode_strict:
  true` makes this mandatory rather than merely prudent**: strict mode is
  precisely the promise not to quietly drop the requirement when standbys are
  missing, which is the situation every bootstrap starts in. The `CREATE ROLE`/`CREATE
  DATABASE` statements therefore force `synchronous_commit=local` via
  `PGOPTIONS`. This is a one-time administrative step and has no bearing on the
  measured workload's durability, which is governed by the standing
  `postgresql.conf`, not by the bootstrap script.
- **HAProxy on the client node needs `init-addr last,libc,none`.** It resolves
  every `server` hostname once at startup and refuses to start if any fails, and
  at first boot those are Tailscale MagicDNS names that often do not resolve
  yet, because the node joins the mesh in the same cloud-init run. The
  2026-09-08 deployment came up with haproxy dead ("Start request repeated too
  quickly") while the identical config validated cleanly minutes later. The
  template now waits for the names, starts haproxy, and **verifies something is
  listening on :5000** before reporting success -- without which the failure
  surfaces much later as `connection refused` on `127.0.0.1:5000` in a measured
  phase.
- **Patroni's config must be at `/etc/patroni/config.yml`, and a skipped
  systemd condition is not an error.** Ubuntu's packaged `patroni.service`
  declares `ConditionPathExists=/etc/patroni/config.yml` and
  `ExecStart=/usr/bin/patroni /etc/patroni/config.yml`.
  `bootstrap-patroni.tftpl` wrote `/etc/patroni/patroni.yml`, so the condition
  failed -- and a failed condition makes `systemctl start` **exit 0** while
  starting nothing, which `set -e` cannot catch. Every node then finished
  cloud-init with `status: done` and printed "✅ Patroni node provisioned
  successfully" over a testbed with no database process anywhere: the only
  visible traces were `journalctl -u patroni` reporting "Condition check
  resulted in ... being skipped" and two `psql: connection refused` lines
  buried in `/var/log/cloud-init-output.log`, both followed by a green
  checkmark. The template now writes `config.yml`, `chmod 640
  root:postgres` (it carries the superuser and replication passwords), and
  **polls `:8008/health` before declaring success** rather than sleeping 20 s
  -- a node that cannot answer its own REST API now fails provisioning, because
  the alternative is that the failure surfaces later as a measurement result.
  The primary's `CREATE DATABASE ycsb` likewise waits for `:8008/primary` to
  answer 200 instead of assuming `PEERS[0]` won the election, and verifies the
  database afterwards, since its `|| true` cannot tell "already exists" from
  "never created".
- **Every `ssh` inside a `while read ... done <<< "$LIST"` loop needs `-n`.**
  ssh forwards its own stdin to the remote command, so it consumes the rest of
  the here-string: the loop body runs once and every entry after the first is
  silently skipped. `run-experiment.sh`'s Patroni health poll checked only
  `crdb-gcp-1` and then reported "0 healthy member(s), expected 5" for the
  whole cluster. `SSH_OPTS` now carries `-n`; nothing run through `remote()`
  feeds anything on stdin. (The same run also printed `000000` for one node's
  status: `curl -w '%{http_code}'` already prints `000` when it cannot connect,
  so a `|| echo 000` fallback appends a second token. `patroni_code()` is the
  single place that reads those endpoints now.)
- **The chaos injection timer is anchored to the generator's first sample, not
  to the harness epoch.** `inject_at_s` means "seconds of measured steady
  state before the fault", and it cannot mean that if it counts from a
  `t_zero` taken before `cockroach workload run` has finished
  `creating load generator`. That setup phase ranged from 0.2s to **4m28s**
  across recorded runs (it scales with concurrency and with the client→target
  link), so a 60s `inject_at_s` fired *before the first sample existed*: no
  pre-fault intervals, `baseline_tps` 0.0, recovery floor 0, and
  `performance_rto_s` null. That is what the 2026-09-07 (268s setup) and
  2026-09-08 (65s setup) chaos runs recorded. This does **not** reintroduce
  D4 — the offset is still timed on the monotonic clock and never by counting
  samples; only the *origin* moved from "harness started" to "generator
  started emitting". The wait is bounded by `chaos.duration_s`, after which
  the run reports that the fault was never injected instead of hanging.
  `events.json` carries both `at_offset_s` (from the epoch, unchanged) and
  the new `at_steady_state_offset_s`.
- **`Manifest.engine` exists, and every figure filename carries engine,
  profile and run id.** Nothing recorded which engine produced a run before
  this -- `manifest.cockroach_version` being null and a note reading
  "engine: postgresql (patroni HA)" were the only signals, and `report figures`
  used neither, so a PostgreSQL run's `fig2_throughput_sweep.png` would
  silently overwrite a CockroachDB run's figure of the same name. `Manifest`
  now carries an explicit `engine` field (set by `bench.py`/`p4_chaos.py` at
  construction; defaults to `"cockroachdb"` for every run written before the
  field existed, since that's what all of them were), exposed as `Run.engine`
  in the loader. An earlier fix put only a `_postgresql` suffix in the
  filename, blank for CockroachDB; that closed the cross-engine collision but
  not the two others of the same shape -- a `smoke` render and a thesis-scale
  render of the same engine still produced identical names, as did two runs of
  the same profile. **`figures.py::_provenance_slug()` replaced it** and always
  names all three, e.g.
  `fig2_throughput_sweep_cockroachdb_thesis_20260908T053558Z_bench_cluster.png`;
  `cockroachdb` is no longer the blank case, so an old caption citing a bare
  `fig2_throughput_sweep.png` no longer matches a file the current code writes.
  Where several runs disagree the component is `mixed-engine`/`mixed-profile`
  rather than a guess, and the run ids that follow name all of them. It reads
  the manifest rather than `Run.engine`/`Run.profile` because `NetworkRun`
  exposes neither. `fig1_network_matrix` is named the same way as the rest, and
  `p1_network.run` now takes `engine=` (wired from the top-level `--engine` in
  `_cmd_net_probe`) so that the name is reporting something recorded rather than
  a field default. Ping does not care which database is listening, but a
  redeploy to the other engine replaces every cluster node, so a Phase I run
  belongs to exactly one deployment the same way a benchmark does. Phase I runs
  recorded before this have no `engine` key and read back as `cockroachdb`,
  which is what all of them were. `--cluster` and
  `--network` still accept one run id each, so comparing both engines means
  invoking `report figures` twice into the same `--out` directory; the slug is
  what keeps that safe instead of `--cluster`/`--chaos` needing to become
  multi-valued.
- **Figures are written as PNG + SVG. There is no PDF any more**
  (`EXPORT_VECTOR_EXT`, at the user's request -- SVG opens in a browser and in
  every vector editor without a conversion step). Both files come out of one
  `_finish()` call; `_written_formats()` is what makes `report figures` report
  both rather than listing only the raster half.
- **`resilience.write_latency_recovery()` is a second, independent recovery
  axis from `performance()`, on the write operation's own p50 latency rather
  than aggregate TPS.** Added because this workload is 80% reads served
  locally by the leaseholder: aggregate throughput can fully recover after a
  fault that permanently changes the write path's floor, and on this testbed
  it does -- a `dead` run against `gcp-1` settled write (`update`) p50 at
  209.7ms against a 151.5ms baseline (1.38x, held for the rest of the run)
  while `performance()` reported a clean 8.0s recovery on the same data. Both
  figures are correct; they answer different questions, and reporting only one
  would either hide a structural degradation (TPS-only) or misreport a healthy
  read-dominated workload as unrecovered (latency-only). Settling is judged the
  same way `post_fault_steady_state` judges throughput -- coefficient of
  variation over a window excluding `LIVENESS_SETTLE_S` -- and a settled value
  within `LATENCY_SHIFT_TOLERANCE` (15%) of baseline is `returned_to_baseline`;
  outside it, `structural_latency_shift`.
  **`quorum_geometry()` had a matching bug, fixed the same day.** It computed
  RTTs via `gateway_rtts()`, which returns round trips *from the gateway
  node*. Every chaos profile's target is `gcp-1`, which is also the gateway,
  so `before` already had the target's row removed as "self" before `after`'s
  target-filter ran -- that filter then had nothing left to remove, and
  `before == after` on every single dead/recover run in this project,
  regardless of what actually happened. It reported "the write path is
  unaffected" on the same run `write_latency_recovery` measured a 1.38x
  settled shift on -- that disagreement is what caught it. Fixed by branching
  on `target.host == gateway.host` (`leaseholder_displaced`): when the target
  is a follower, the original single-value computation is unchanged and still
  correct; when the target *is* the leaseholder, there is no "its row minus
  one entry" to compute, since the leaseholder itself is gone, so every
  surviving node is evaluated as a candidate leader from its own RTT row and
  the result is reported as a range (`surviving_quorum_floor_range_ms`,
  `candidate_floors_ms`) rather than a single value pretending to predict
  which survivor CockroachDB's allocator will actually promote.
  Both figures above are from the `smoke` profile (C=10); reproduced at
  thesis scale (C=100) the same day with the same shape and closer numbers
  than smoke's noise would suggest: write p50 236.4ms vs a 168.6ms baseline
  (1.40x, `structural_latency_shift`) against a `quorum_geometry` range of
  150.2-192.9ms (2.16-2.77x) -- still agreeing, still no contradiction.
- **The chaos generator runs with `--tolerate-errors`; the bench sweep must
  not.** Without it `cockroach workload run` *exits* on its first failed
  statement, and during a chaos run the first failed statement is the fault.
  The 2026-09-08 `dead` run aborted 8 s after injection with
  `result is ambiguous ... connection refused (SQLSTATE 40003)`, leaving three
  zero-throughput samples and then nothing: 7.1 s of post-fault series out of
  the 120 s the profile allowed. Recovery is unobservable when the observer
  dies with the cluster, so `t_recovered_offset_s` and `performance_rto_s`
  could never be anything but null, and the figures looked like a permanent
  collapse. Do **not** add the flag to `bench.py` -- nothing is supposed to
  fault during a benchmark, so there an error must fail the run loudly instead
  of being absorbed into a throughput average.
- **The generator's run length is `generator_duration_s(chaos, mode)`, not
  `chaos.duration_s`, and in `recover` mode it is counted from the HEAL rather
  than from the fault.** `inject_at_s` is measured from the generator's first
  sample, so `duration_s` alone guarantees nothing about how much series follows
  the fault; the run is extended to `inject_at_s + min_post_fault_s`
  (default 60 s) when it would otherwise be shorter, and never shortened.
  **That was not sufficient for `recover`, and the gap made the smoke self-test
  incapable of measuring anything.** `smoke` ran 45 s with the fault at 15 s;
  observed 2026-09-09 (`experiment-20260909T031334Z.log`), both independent
  instruments were still inside the outage when observation ended and the
  harness reported the RTO as UNMEASURED -- the coverage check of that same day
  working exactly as designed, on a run that could not have produced a number
  however well the instruments behaved. The next run, at the corrected length,
  measured that outage at **37.6 s** after the fault: the old window was short
  by about eight seconds.
  **Be precise about why the bound is what it is, because the obvious reason is
  wrong.** Recovery is *not* gated on the partition lifting. The fault isolates
  one node, the other four keep quorum and elect a new primary, and writes
  resume at failover -- measured at 64.1 s on
  `20260909T040914Z_p4-chaos-recover`, **7.4 s before** that run's partition
  healed at 71.5 s. The heal delay earns its place as a *conservative upper
  bound* (failover has consistently beaten it) and because Phase IV needs the
  demoted node back on the network in time to finish rewinding and be a
  switchover candidate at all. The
  heal delay is now the single constant `p4_chaos.RECOVER_HEAL_DELAY_S`, read by
  **both** the payload's `sleep` and the run length so the two cannot drift
  apart (there is a test pinning that), and `mode` is a parameter of
  `generator_duration_s`. `smoke`'s `min_post_fault_s` went 20 -> 45, which
  makes its recover run 105 s and its dead run 60 s -- 45 rather than 20 so that
  the window covers the settling after failover and not merely the instant it
  completes. `thesis` and `thesis-extended` are unchanged at 180 s, which
  already clears `60 + 45 + 60 = 165`.
  **Verified live** on 2026-09-09 04:02: the recover phase ran 105 s, both
  instruments reported `coverage_truncated: false`, and the outage came out at
  34.1 s (probe) / 33.9 s (audit writer) where the 45 s run had been able to
  report only UNMEASURED.
- **`dead` mode now restarts the target itself, after the measurement.** This
  reverses the earlier "the fault is real, and restarting is an operator
  action" stance, at the user's request: the node stayed down, the cluster
  declared it dead, and the testbed was left unfit for the next run.
  `restore_target()` is called only after every artefact is derived, and
  records the restart in `events.json` under `restore`, so a reader can always
  separate what was measured from what was repaired. `recover` mode never
  comes through it -- its payload heals itself after 45 s, and restarting a
  node that was never stopped would be a second fault. Two details are
  load-bearing: the restart needs `sudo -n` (the store is root-owned while the
  SSH user is `ubuntu`), and liveness must be polled from a **survivor** --
  `run-experiment.sh` asked `cockroach node status` of `$GW_HOST`, which *is*
  the chaos target on this testbed, so it reported "has not rejoined (0 live)"
  after every dead-mode run whether or not the node was back.
- **The RTO probe runs on `crdb-client-1`, not in the harness process.**
  Where it runs is part of the measurement. From the operator's workstation a
  canary write cost 332 ms median over Tailscale, so eight workers achieved
  21.4 writes/s against the 500/s the profile dispatched and the probe resolved
  only 64 ms -- `probe_interval_s` was never the binding constraint, the
  operator's uplink was -- and a hiccup on that uplink during the fault window
  was indistinguishable from a cluster outage. From the client node the same
  code costs 123 ms and achieves 58.8/s, resolving 21 ms; what remains is the
  cluster's own cross-region quorum cost (~69 ms floor), not the operator's
  link. `crdblab/core/remote_probe.py` copies `rto_probe.py` + `recorder.py`
  (both stdlib-only) to `/tmp/crdblab-probe-agent` every run and executes
  `python3 -m crdblab.core.rto_probe` there; the agent streams one JSON object
  per attempt on stdout. **It is the same module, not a reimplementation** --
  a second copy of the probe would be a second thing to keep in step with the
  analysis that reads it. The agent's offsets are on *its* monotonic clock, and
  are rebased onto the run's by the difference between the two epochs' UTC
  stamps; that is legitimate only because both nodes run chrony and
  `preflight.check_clock_offset` asserts the client's offset (0.01 ms measured,
  250 ms limit) before the run -- the skew actually applied is recorded in
  `events.json` as `probe.epoch_skew_s`. `psycopg` (v3) must be present on the
  client node: Ubuntu 22.04 has no `python3-psycopg` package, so
  `bootstrap-client.tftpl` pip-installs it, and `check_agent_prerequisites`
  fails pre-flight rather than letting a missing driver look like a total
  outage from the first sample onward.
- **Leaseholder placement gets a settle window before a chaos run, not before
  a bench run.** `check_leaseholder_placement` takes `settle_timeout_s`
  (default 0 = one reading, so bench and `net probe` still fail fast); only the
  chaos phases pass one, from `chaos.leaseholder_settle_s` (default 300s).
  This is not a loosening -- the condition that must hold is unchanged and
  still gates the run. It exists because a chaos run can follow another chaos
  run: Phase III's partition moved both `ycsb` leaseholders to Linode and
  Phase IV, which starts as soon as Phase III returns, read that and aborted.
  ~75 s of post-heal time was not enough for `lease_preferences` to pull them
  back; they did return on their own given longer.
  **`thesis` and `thesis-extended` now set `leaseholder_settle_s: 900`
  explicitly**, against the 300 s default that `smoke` keeps. On the PostgreSQL
  arm this window is what Phase IV spends waiting for the demoted primary to
  rewind or re-clone, and the two scales are not comparable: the whole cycle
  took ~57 s against a 205 MB working set on 2026-09-09, while the thesis
  profile is ~6.15 GB and the leader it clones from may be in eastasia. The wait
  exits the instant the candidate reports streaming, so a healthy cluster pays
  nothing for the larger ceiling, and a genuinely stuck node still fails the
  check rather than hanging.
- **`bench.py`'s per-tier setup time is measured from `tier_start`, not
  `t_zero`.** `t_zero` is the sweep-wide epoch and must stay that way --
  `wall_offset_s` and `generator_start_offset_s` exist to make ticks from
  different tiers orderable against each other. Measuring *connection setup*
  from it instead reported everything since the sweep began, growing by one
  tier's duration plus cooldown each iteration (18.8s to 1042.5s across 12
  tiers, ~92 s per step) and firing the >10 s warning on every tier, against a
  true setup cost of 0.2-4.8 s. The per-tier figure is also recorded as
  `connection_setup_s`.
- **Patroni's leader IS pinned to `gcp-1` now, and where the primary sits is a
  confound, not a preference.** It used to be an unbiased etcd election among
  all five nodes, and that is not a neutral default: the workload is driven
  from `crdb-client-1` (GCP us-east1) and CockroachDB's `lease_preferences`
  puts its leaseholder on `gcp-1`, 1 ms away, so an election that landed the
  PostgreSQL primary on `crdb-azure-2` (Azure **eastasia**) made the PostgreSQL
  arm pay a **199 ms** client→primary hop that the CockroachDB arm never paid.
  Measured 2026-09-09: a fresh connection cost **1.02 s** to azure-2 against
  **0.05 s** to gcp-1, a 20x penalty on every one of the `--concurrency`
  connections the generator dials serially. A "CockroachDB is faster" figure
  drawn across that would be measuring cloud geography. Pinning also aligns the
  write floors, which is the equivalence the comparison rests on: RTTs from
  gcp-1 are linode-1 18 ms, linode-2 70 ms, azure-1 230 ms, azure-2 204 ms, so
  quorum-mode `ANY 2 (...)` commits on the two fastest acks at
  ~70 ms — against the 68.8 ms 3-of-5 Raft quorum floor recorded from gcp-1.
  (This is also why `synchronous_mode` must be `quorum` and not `true`: `true`
  names specific standbys, so the pair Patroni happens to choose gates every
  commit, and an Azure pair would cost 211-215 ms instead of ~70 ms.)
  Three pieces, all needed:
  (1) `bootstrap-patroni.tftpl` starts Patroni on `gcp-1` **first** and makes
  every other node wait for it to hold the leader lock before starting, so they
  can only join as replicas — `failover_priority` biases elections but does not
  govern the initial bootstrap race, which is decided by whoever takes the lock
  first. (2) `failover_priority: 100` on gcp-1, `1` elsewhere. Deliberately
  **not** `nofailover: true` on the others: that would forbid the failover
  Phase IV exists to measure. (3) `preflight.check_patroni_primary_placement`
  restores placement by `patronictl switchover` before each measured phase, and
  `run-experiment.sh` does the same between phases. The repair is required
  because **Patroni never fails back**: `failover_priority` biases who *wins* an
  election but never *starts* one, so after a chaos run the primary stays where
  the failover left it, indefinitely. `check_leaseholder_placement`'s settle
  window works for CockroachDB only because `lease_preferences` actively pulls
  the lease back; waiting for Patroni to do the same would be waiting for
  something that cannot happen.
  None of this makes the primary *assumed*:
  `p4_chaos.py::resolve_patroni_primary` still queries every node's
  `:8008/primary` live immediately before scheduling the fault, and still
  refuses outright (rather than guessing) if zero or more than one node answers
  200. The pin makes the answer predictable; the live read makes it true. It
  now lives in `core/preflight.py` (with `SUDO`, moved to `core/ssh.py`) and is
  re-exported from `p4_chaos` — `core` may not import `phases`, and there is one
  implementation, not a copy per side.

- **Patroni runs in `synchronous_mode: quorum`, and `true` would be wrong.**
  Getting a Raft-equivalent write path takes two separate things, and only one
  of them was present until 2026-09-09. The PostgreSQL-level setting was right:
  `synchronous_commit: on` (remote *fsync*, the rung above `remote_write` and
  below `remote_apply`) plus `synchronous_standby_names: 'ANY 2 (*)'` makes
  every COMMIT durable on the primary and two standbys -- 3 of 5 nodes, exactly
  3-of-5 Raft quorum -- and it demonstrably worked: measured write p50 75.5 ms
  against a 69.6 ms Phase I floor and CockroachDB's 68.8 ms from the same node.
  **The Patroni-level setting was missing.** `synchronous_mode` was never set,
  so it defaulted to `false`, and with it off Patroni does not couple
  synchronous replication to leader election: at failover it promotes the most
  advanced replica by LSN with **no requirement that it was one of the two
  synchronous standbys**. Raft's core safety property is precisely that a new
  leader already holds every committed entry. So the guarantee held for every
  ordinary commit and was not enforced across the one event Phases III and IV
  exist to measure -- a PostgreSQL RPO drawn under that config would have been
  reporting Patroni's default promotion policy, not PostgreSQL HA, and the
  Phase III run's "RPO 0 acknowledged writes lost of 260" was luck rather than
  a guarantee.
  **Use `quorum`, never `true`.** Both are synchronous; they differ in *which*
  standbys must ack. `true` names specific nodes (`FIRST n (node,node)`), so
  whichever two Patroni picks gate every commit -- land on the Azure pair and
  the cluster pays 211-215 ms per commit instead of ~69 ms, which destroys the
  write-floor equivalence the entire cross-engine comparison rests on. `quorum`
  emits `ANY n (...)`, so the two *fastest* acks win (linode-1 24.6 ms,
  linode-2 69.1 ms from gcp-1) and the floor is preserved while promotion is
  restricted to nodes that were in the quorum.
  In quorum mode **Patroni owns `synchronous_standby_names`** and derives it
  from `synchronous_node_count: 2`; the hand-written value was removed from
  `parameters` and must not be re-added. `synchronous_mode_strict: true` is set
  so that losing standbys blocks writes instead of silently shrinking the
  requirement and committing asynchronously -- non-strict is the flattering
  failure this project exists to catch, since writes would get *faster* at the
  exact moment durability disappeared and no artefact would record it.
  Two consequences are expected rather than bugs: the post-bootstrap DDL still
  works because it runs with `synchronous_commit=local` via `PGOPTIONS`, which
  bypasses the wait before any standby exists; and Phase IV's RTO now includes
  the time for a *second* standby to become synchronous-eligible, which is the
  correct quantity (when durable writes are possible again) and the like-for-like
  counterpart of a Raft group regaining quorum. One fault against five nodes
  always leaves three standbys, so a single injected fault cannot wedge it.
  `maximum_lag_on_failover` is deliberately left at 1048576 rather than zeroed:
  in quorum mode the anti-loss guarantee comes from quorum membership, not from
  that number, and zero would demand byte-exactness against a DCS-read leader
  position that is routinely stale after a crash -- which would block failover
  entirely and silently disable the phase.
  **`bootstrap.dcs` is read only at first bootstrap.** It is written into etcd
  then and the template is never consulted again, so changing any of this on a
  live cluster needs `patronictl -c /etc/patroni/config.yml edit-config`, not a
  template edit. Verify after provisioning with `patronictl show-config` and
  `SHOW synchronous_standby_names` (expect `ANY 2 (...)`).

- **Linode caps `user_data` at 16384 bytes decoded, so its module sends the
  bootstrap gzipped.** `bootstrap-patroni.tftpl` renders to ~18.9 kB and a plain
  `base64encode` of it is rejected at create time with `[400]
  [metadata.user_data] decoded user_data must not exceed 16384 bytes`, failing
  both Linode instances in an otherwise clean apply. Only Linode binds — GCP
  allows 256 kB and Azure's `custom_data` 64 kB — and only on the PostgreSQL
  path, since `bootstrap-cockroachdb.tftpl` is ~6.5 kB and fits either way.
  `modules/linode_node/main.tf` now uses **`base64gzip`**: 7244 bytes on the
  wire, 56% of the budget free, and cloud-init detects the gzip magic bytes and
  decompresses, so the script that executes is byte-identical to what the other
  providers get. This is a transport encoding, not a second bootstrap. The
  alternative was deleting comments until it fit, which was rejected — comments
  are 62% of that template and they are where the reasons live. If a Linode node
  ever comes up with no Patroni process, check `/var/log/cloud-init-output.log`
  there before assuming the script is wrong: empty or binary output is the gzip
  path failing, and the fallback is to trim the template.

- **The working set now exceeds RAM, so disk capacity and disk IOPS are
  first-order variables and must be declared.** `profiles/thesis.yaml` moved
  from 125k rows (~205 MB, fully memory-resident, storage invisible) to
  **3,750,000** (~6.15 GB, ~1.5x the measured 4,007,004 kB node RAM and 6.3x
  each engine's ~978 MB declared cache). Two consequences that are not optional
  to think about. **Capacity:** every node holds a full copy — ~8.2 GB for
  PostgreSQL (table, index, WAL) and ~9.2-12.3 GB for CockroachDB (replica plus
  LSM compaction headroom) — while the GCP `boot_disk` and Azure `os_disk`
  previously set no size at all and inherited the image default, 10 GB on GCP.
  Both now set 50 GB explicitly (`size` / `disk_size_gb`); Linode's
  `g6-dedicated-2` ships 80 GB and needs nothing. **IOPS:** both clouds sell
  IOPS by capacity — GCP pd-ssd is ~30 IOPS/GB (a default 10 GB volume is ~300;
  50 GB is ~1500) and Azure Premium_LRS is tiered by size (30 GB = P4 = 120
  IOPS, 64 GB = P6 = 240). Undersized, the figure produced is a cloud storage
  tier rather than a database, and the three providers' tiers differ enough to
  distort the quorum geometry that Phase I's floor and the write-path
  equivalence rest on. It does **not** break the cross-engine comparison — both
  arms run on the same hardware — but the actual provisioned IOPS should be
  recorded and disclosed alongside the RTT matrix, because it is now part of
  what is being measured. Also watch `duration_s: 60` and `warmup_s: 5`: the
  first tier after a load starts on a cold cache, and if `validate`'s
  steady-state CV check flags tiers as unsettled the fix is a longer tier, not a
  looser check.

- **An RTO is the LARGEST gap in the write stream after the fault, never the
  first one over the noise floor.** Both instruments got this wrong and both
  were fixed 2026-09-09: `rto_probe.measure_rto` took the first qualifying gap,
  and `p4_chaos.availability_rto` took the interval to the first write
  acknowledged after the fault. Two things break that. The noise floor is
  calibrated on *pre-fault* gaps, while post-fault gaps are drawn from a worse
  distribution (failover in progress, clients reconnecting, the pool completing
  in bursts), so ordinary post-fault jitter clears it routinely. And **the fault
  does not take effect when the injection command returns** -- `tailscale down`
  exits 0 while established flows keep working for seconds -- so the interval
  right after the fault is often still healthy, and its jitter is exactly what a
  first-match latches onto. Measured on
  `runs/20260908T232245Z_p4-chaos-recover` (Phase III, PostgreSQL, fault at
  72.076 s): **eleven** post-fault gaps cleared the 0.132 s floor; the first was
  0.152 s, opening 0.196 s after the fault while writes were still flowing, and
  ten of the eleven were noise between 0.146 s and 0.307 s. The real
  interruption was 68.96 s, opening 3.53 s after the fault. The run reported
  `rto_s` **0.348 s** and `availability_rto_s` **0.039 s** for an outage of
  ~70 s -- a 208x understatement, in the flattering direction, and the two
  independent instruments corroborated each other's understatement instead of
  catching it. Re-derived from the retained raw artefacts with the fix: probe
  72.49 s, audit writer 82.51 s (coarser: 0.38 s cadence, 1.28 s floor). Both
  now also record what the maximum was chosen against -- `qualifying_gaps_s`
  and `qualifying_gap_count` on the probe, `detection_floor_s` and
  `outage_observed` on the audit writer -- so a floor that is barely separating
  signal from noise is visible rather than inferred. **Any RTO figure from a run
  recorded before 2026-09-09 is suspect and should be re-derived**; the raw
  `rto_probe.csv` and `audit.csv` are retained precisely so it can be.

- **A `recover` fault against the primary diverges its timeline, and
  `use_pg_rewind: true` alone does NOT get it back.** Phase III partitions the
  primary, so the demoted node keeps WAL the new timeline never saw. On
  2026-09-09 `gcp-1` forked at `0/157C7D10` while its own checkpoint had reached
  `0/157C7E40`. Patroni ran `pg_rewind` three times and it failed identically
  each time:

  ```
  pg_rewind: servers diverged at WAL location 0/157C7D10 on timeline 1
  pg_rewind: error: could not open file
    "/var/lib/postgresql/16/data/pg_wal/00000001000000000000000C": No such file
  pg_rewind: error: could not find previous WAL record at 0/CFFFCF8
  ```

  pg_rewind reads the **target's own** WAL backwards from the divergence point
  to the last common checkpoint, and `gcp-1` had already recycled those
  segments -- the checkpoint that completed 30 s before the fault recycled 12 of
  them (~192 MB) because `wal_keep_size` defaults to 0. The node parked at
  `start failed` and was still there hours later; `patronictl switchover`
  correctly answered "no good candidates have been found", the placement
  pre-flight aborted, and Phase IV never ran. This is **structural, not bad
  luck**: it happens after every Phase III run against the primary, so before
  this fix Phase IV could never follow Phase III on the PostgreSQL arm at all.
  Two settings in `bootstrap-patroni.tftpl`, both needed:
  `parameters.wal_keep_size: '4GB'` is the fast path that lets rewind succeed,
  and `remove_data_directory_on_diverged_timelines: true` is the guarantee --
  when rewind still fails, Patroni re-clones from the leader instead of parking
  the node forever. Sized against the measured ~192 MB/checkpoint churn with
  headroom for the thesis profile, on the 50 GB disks. Remember that
  **`bootstrap.dcs` is read only at first bootstrap**: this fixes the next
  deployment and does nothing to a running cluster, which needs `patronictl -c
  /etc/patroni/config.yml edit-config`.
  There is a harness half too. Re-cloning is ~6 GB across a WAN link at thesis
  scale, and Phase IV starts the instant Phase III returns, so the switchover
  would still be asking for a handover to a member mid-basebackup.
  `check_patroni_primary_placement` now takes `settle_timeout_s` (wired from
  `chaos.leaseholder_settle_s`, default 0 so `bench` and `net probe` still fail
  fast) and waits for the **candidate** to become eligible before issuing the
  switchover. That is not the same as waiting for the primary to drift back,
  which remains something Patroni will never do; the repair is still the
  explicit `patronictl switchover`. **What counts as eligible was wrong here
  until 2026-09-09** -- the wait asked `:8008/replica` for a 200 and this note
  claimed that was "the same condition `switchover --candidate` itself tests".
  It is not; see the next gotcha.

- **After a fault on the pinned primary, the PostgreSQL performance RTO is
  structurally undefined, and the reason is client-to-primary distance, not the
  write path.** Patroni promotes a survivor, and on this topology the survivor
  is in another region; the generator reaches PostgreSQL through HAProxy, which
  *follows the primary*, so every operation moves with it -- reads included, and
  reads are 80% of the workload. Measured on
  `20260909T040914Z_p4-chaos-recover` after `azure-2` (eastasia) was promoted:
  **read p50 went 0.92 ms -> 209.7 ms** and update p50 75.5 -> 369.1 ms, and
  throughput settled at ~43 ops/s, which is C=10 divided by a ~230 ms round trip
  -- arithmetic, not recovery. Aggregate throughput cannot regain 80% of a
  baseline taken against a primary 1 ms away, at any run length, so
  `performance_rto_s` will be null on both chaos phases of every PostgreSQL run
  and lengthening the run does not change it. Report the availability RTO and
  the probe RTO, which are unaffected and were 34.1 s / 28.3 s.
  **This is the asymmetry to be careful about in the write-up**, because
  CockroachDB does not pay it in the same form: its client talks to the gateway,
  and its reads are served by the leaseholder, so a leaseholder move is not a
  228x change on 80% of the operations. The comparison is still fair -- both
  arms are faulted on the same node of the same topology -- but "PostgreSQL did
  not recover its throughput" is a statement about where the new primary landed,
  and must be quoted with the read p50 beside it or not at all.
  `analysis/resilience.py::quorum_geometry` used to make this worse: it printed
  "the read share, which is unaffected regardless of which candidate takes the
  lease" on *both* arms, explaining a 228x effect with a 2.1x write-floor cause,
  and it called a Patroni primary "the leaseholder" promoted by "CockroachDB".
  Its consequence text and vocabulary now branch on `run.engine`; the floor
  computation deliberately does **not**, because leader-plus-two-fastest-acks is
  3-of-5 Raft quorum and Patroni's `ANY 2 (...)` alike. The dict keys kept their
  original names (`leaseholder_displaced`) so old artefacts stay readable.

- **`/replica` answering 200 does NOT mean Patroni will accept the node as a
  switchover candidate.** The endpoint answers 200 for a member that is up, in
  recovery, not tagged `noloadbalance`, and within `maximum_lag_on_failover` --
  and the lag half of that is the trap, because lag is measured against a
  position an *unattached* member cannot advance. A replica connected to nothing
  therefore reports no lag and reads as perfectly healthy. Observed 2026-09-09
  (`experiment-20260909T031334Z.log`): after Phase III's partition `gcp-1` came
  back up, answered `/replica` 200, the candidate wait ended on its first
  reading with "running replica, lag within bounds", and `patronictl switchover`
  then failed with `503, Switchover failed`. The cluster table printed with that
  failure is unambiguous -- `gcp-1` was `Role: Replica` (not `Quorum Standby`),
  `State: running` (not `streaming`), **`TL 1`** and `Receive LSN: unknown`,
  while the leader and the other three members were streaming on **`TL 2`**.
  Phase IV never ran, on a cluster that was otherwise entirely healthy, and it
  had a 300 s window it exited after one reading. `patroni_candidate_ready` now
  reads the member's own `/patroni` document as a second gate and requires
  `replication_state == "streaming"` and, when the leader's timeline could be
  read, the same `timeline` as the leader -- the observable form of "already
  holds the current history and is receiving the rest". The leader's timeline is
  read once before the wait rather than per poll, and a `/patroni` that cannot
  be read is *not ready* rather than assumed ready: falling back to `/replica`
  alone would silently reinstate the bug. The check degrades safely without the
  timeline (the streaming requirement alone catches the observed failure), so a
  missing field delays a phase rather than failing it.
  `run-experiment.sh`'s own between-phase switchover backstop got the same wait
  (`patroni_streaming()`), because it was asking for the same refusal, and there
  the consequence was only a confusing warning rather than an aborted phase.
  **Verified live** on 2026-09-09 04:02: the check waited out a
  `/replica answered 503`, passed on `streaming on timeline 2, lag within
  bounds`, and the switchover took — Phase IV then ran end to end for the first
  time in the project.

  **Streaming on the leader's timeline with zero lag still isn't sufficient,
  and this is the failure one step past the one above.** At thesis scale
  (`experiment-20260909T043036Z.log`, 2026-09-09 04:30) `gcp-1` cleared both
  gates -- `streaming on timeline 6, lag within bounds` -- and the switchover
  still answered `503, Switchover failed`. The cluster table again names it:
  `crdb-gcp-1` was `Role: Replica`, not `Quorum Standby` like the other three
  survivors, with **zero lag on both Receive and Replay LSN**. In
  `synchronous_mode: quorum`, Patroni tracks membership in
  `synchronous_standby_names` separately from streaming state, reconciled on
  its own `loop_wait` cadence -- a node can hold the leader's timeline
  unlagged for one poll before the leader admits it to the synchronous set,
  and the switchover is refused until it does, `--force` notwithstanding.
  Patroni's own REST API documents the fourth gate needed:
  `GET :8008/quorum` "returns HTTP status code 200 only when this node is
  listed as a quorum node in `synchronous_standby_names` on the primary" --
  the same fact `patronictl list`'s Role column shows, and the one thing
  neither `/replica` nor the candidate's own `/patroni` document exposes.
  `patroni_candidate_ready` now checks it as a third gate after streaming and
  timeline; a candidate that fails it is *waited* for, the same as the other
  two. **Verified live** on `experiment-20260909T151320Z.log` (smoke,
  2026-09-09 15:13, against a redeployed testbed): the candidate check printed
  `gcp-1 is a candidate now: streaming on timeline 2, lag within bounds,
  quorum member`, the switchover took, and Phase IV ran end to end for the
  first time a smoke run has ever completed all four phases unattended.

- **A client blocked on a black-holed socket reports as a healthy cluster, and
  the fix is a TCP bound, not a shorter statement timeout.** On 2026-09-09 both
  RTO instruments stopped observing 3.6 s after a `recover` fault and the
  harness reported the silence as recovery: `availability_rto_s` **0.082 s** and
  "no interruption in served writes was detectable", for a fault where the
  generator recorded `tps = 0.0` on both post-fault ticks and `performance_rto`
  never came back. The evidence is unambiguous in the artefacts. Fault at
  harness offset 24.792 s; the probe's last attempt completes at **28.42 s**
  (`span_s: 20.335` of a 45 s run) with **515 of 515 attempts `ok` and zero
  timeouts, conn_errors or refusals**; the audit writer's last ack at
  **28.49 s**, then a single `ambiguous` row at **76.78 s** -- one write blocked
  for 48 seconds. `tailscale down` does not close established connections, it
  swallows them, and `probe_statement_timeout_ms: 5000` is **server-side**, so
  it cannot arrive when packets cannot. `connect_timeout=2` bounds only *new*
  connections. With `probe_workers: 2` that was 100% of probe capacity. It is
  **nondeterministic, not smoke-specific**: the 2026-09-08 thesis run with 8
  workers recorded exactly 8 `conn_error`s, shed the sockets, reconnected and
  sampled the full 182.9 s -- same code, same fault, opposite outcome.
  Note this is a *different* defect from the max-gap fix above, which was
  present and working correctly (noise floor 0.2695 s, no qualifying gaps); it
  simply had no post-fault data to work on. Two fixes:
  (1) `config.pg_direct_dsn` now carries `keepalives=1` and
  `tcp_user_timeout=10000`. One change covers both instruments because they
  share the DSN. **Do not replace this with a tighter `statement_timeout`** --
  `rto_probe`'s design turns on *a blocked write being the measurement*, since
  its completion timestamp is a direct observation of the instant service
  resumed, and a short client deadline aborts exactly the write worth keeping. A
  server that is merely busy still has a live kernel that acknowledges
  keepalives, so TCP bounds fire only when the peer is unreachable. 10 s is
  deliberately **looser** than the probe's own 5 s server-side statement
  timeout, so it can never pre-empt the server's own reply.
  (2) Both instruments now record coverage. `availability_rto` takes
  `observation_end` and `measure_rto` takes `observation_end_s`; each emits
  `coverage_gap_s` / `coverage_truncated`, and when coverage ends early **and**
  no outage was detected they return `None` with a claim saying the outage is
  UNMEASURED rather than absent. `analyze resilience` prints an
  `*** AN INSTRUMENT STOPPED OBSERVING ***` banner, the same shape as the
  fault-did-not-land one. Coverage on the audit side is judged on the last
  **acknowledgement**, not the last attempt: a writer blocked inside one
  `cur.execute` keeps attempting, so "did it try recently" answers yes across a
  window in which it observed nothing. Runs recorded before these keys existed
  read back as `None` and stay silent rather than false-alarming. Verify against
  the two retained runs: `20260909T012233Z_p4-chaos-recover` must show the
  banner and refuse both RTOs, while `20260908T232245Z_p4-chaos-recover` (full
  coverage) must be untouched and still report 82.51 s audit / 72.49 s probe.

- **`patronictl` addresses members by HOSTNAME, not by `Node.name`.**
  `bootstrap-patroni.tftpl` sets Patroni's `name:` to `${hostname}`
  (`crdb-gcp-1`), while this harness's `Node.name` is the short label (`gcp-1`)
  that `profiles/*.yaml` uses for `chaos.target`. The two differ on every node.
  `check_patroni_primary_placement` built its switchover from `.name`, so it
  named members that do not exist: on 2026-09-08 Phase IV ran `switchover
  --leader linode-2 --candidate gcp-1`, Patroni answered "Member linode-2 is not
  the leader of cluster postgres-cluster", the placement check failed and
  **Phase IV never ran at all** -- on a healthy cluster where the switchover it
  was asking for was entirely possible. Now uses `.host` on both. The unit test
  asserted `--candidate gcp-1` and so locked the bug in; it now asserts the
  hostname and that the short name is absent. `run-experiment.sh`'s own
  switchover was always correct -- it passes `$PG_PRIMARY`/`$GW_H`, which are
  hosts -- which is why the failure only ever appeared from inside the harness.

- **The Patroni health gate is a bounded wait, not a single reading.** A member
  answers `:8008/health` with 503 for as long as it is still coming up -- taking
  its `pg_basebackup`, replaying WAL, catching up as a streaming replica -- and
  on a fresh deployment that is normal, not a fault: the five nodes boot on
  three clouds' schedules and the four replicas cannot start their basebackup
  until the designated primary has taken the leader lock. Read once, the gate
  caught the cluster mid-bootstrap and aborted the sweep
  (`experiment-20260908T225729Z.log`: "1 healthy member(s), expected 5", all
  four replicas 503, on a deployment that was coming up correctly). It now polls
  every 10 s up to `PG_HEALTH_WAIT_S` (600 s), costs nothing when the cluster is
  already up, still fails hard rather than hanging, and names the offending
  host:code in the failure. Do not turn it back into a single reading.

- **`analyze engine-comparison` still refuses every cross-engine comparison, and
  this is NOT yet fixed.** `validation._MATCHED_SERVER_FLAGS` is
  `("--cache", "--max-sql-memory")` -- both CockroachDB-only flags. PostgreSQL's
  postmaster argv contains neither, so they read as `unset` and always mismatch,
  and the gate errors with "were started with different --cache (0.25 vs unset)"
  for *every* CockroachDB-vs-PostgreSQL pair. This is the same half-fixed shape
  as the version check: that one was made engine-aware (a cross-engine version
  difference is the variable under study, so it warns), the flag comparison was
  not. Fixing it needs a measurement decision rather than a code change --
  what the PostgreSQL counterpart of `--cache` is -- and it is load-bearing,
  because the gate exists to stop D9.

- **The two engines' cache budgets are matched, as fractions of measured RAM.**
  `bootstrap-cockroachdb.tftpl` starts every node with `--cache=0.25
  --max-sql-memory=0.25`; `bootstrap-patroni.tftpl` set no memory parameters at
  all until 2026-09-09, so PostgreSQL ran on the packaged `shared_buffers`
  default of **128 MB** against CockroachDB's **~978 MB** on these 4 GB nodes --
  an eightfold asymmetry in the one resource that decides how much of the
  working set is served from memory. It favoured CockroachDB, so it does not
  explain the pg arm's higher throughput on the 2026-09-08 runs, but it is D9's
  exact shape and the headline comparison should not rest on it. **It matters
  far more now than it did then.** Those runs used a 205 MB working set that fit
  entirely in memory on both engines, so the cache asymmetry had little to bite
  on; at the current 6.15 GB the working set is 6.3x either engine's cache and
  ~1.5x total RAM, so cache size directly governs the miss rate and therefore
  the result. Matching it is no longer a tidiness fix, it is load-bearing. The template
  now derives `shared_buffers` as a quarter of `/proc/meminfo`'s `MemTotal`
  (978 MB on these nodes, against `--cache=0.25`'s 978.3 MB) and
  `effective_cache_size` as three quarters. It is computed as a **fraction, not
  written as `1GB`**, for the same reason `--cache` is one: identical literals on
  unlike machines give unlike caches, which is D9 in the form the flag
  comparison cannot see. Two things are deliberately *not* matched, and both
  should stay that way: `--max-sql-memory` has no counterpart (PostgreSQL's
  `work_mem` is per-node-per-query, not a global pool, and raising it with
  `max_connections: 500` is how a 4 GB node gets OOM-killed -- and YCSB point
  lookups draw on neither budget), and the OS page cache backs both engines, so
  neither is isolated to its declared cache. The substitution is done by `sed`
  after the quoted heredoc, because a quoted heredoc cannot compute anything and
  unquoting it is what corrupted two config files in this project; the script
  greps for a surviving placeholder and fails provisioning rather than letting
  Patroni start on a config PostgreSQL will reject.

- **The generator is ruled out as the cause of the cross-engine throughput
  gap** (checked 2026-09-09 against the 2026-09-08 thesis runs; don't re-litigate
  it without new evidence). Five independent grounds. (1) The recorded
  `generator_command` in the two manifests is byte-identical apart from the DSN
  -- same `cockroach workload run ycsb` build, `--seed=42`,
  `--insert-count=125000`, `--request-distribution=uniform`,
  `--read-freq=0.8 --update-freq=0.2`, same durations; no `--tolerate-errors`
  and no `--families` on either sweep. (2) Same client node, same binary: the pg
  arm sustained 7,493 ops/s on `crdb-client-1`, so that client can demonstrably
  drive >7,000 ops/s, and CockroachDB's ~2,000 ops/s ceiling therefore cannot be
  a client limit. (3) **Little's Law closes to 95-105% of the stated concurrency
  on every tier of both engines** (C=10/50/100/200): all N client slots always
  had a request outstanding, so the generator was saturated *waiting on the
  server* and never starved -- the two arms differ only in how fast those
  requests came back. (4) The executed op mix is identical, 80/20 read/update on
  both (CockroachDB 1600/400, PostgreSQL 5987/1505 at C=200), so the pg arm is
  not getting its throughput by doing proportionally more of the cheap
  operation. (5) The server counted the work itself: `pg_stat_user_tables`
  recorded 414k-461k index scans in the C=200 tiers against the ~450k ops the
  generator claimed, so the throughput is not client-side fiction. The client
  path *asymmetry* runs the other way -- PostgreSQL pays two extra loopback hops
  (pgbouncer, HAProxy) that CockroachDB does not, and neither caches query
  results, so every read still crossed the WAN hop to gcp-1; PostgreSQL's 0.9 ms
  read p50 bounds that hop below 0.9 ms and shows CockroachDB's 1.6 ms is
  dominated by server-side work, not the network. What the tiers actually show
  is a **saturation-point difference**: CockroachDB's read throughput is flat at
  ~1,600 ops/s from C=50 to C=200 while its read p50 climbs 1.6 -> 7.1 -> 21 ->
  75.5 ms (textbook queueing), so under load its reads grow expensive enough to
  compete with writes for the fixed concurrency budget -- at C=200 reads occupy
  121 of 200 slots on CockroachDB against 33 of 200 on PostgreSQL. Quote it as
  "CockroachDB's knee is at C~=50 on 2 vCPU, PostgreSQL's is beyond C=200", never
  as a flat "3.7x faster".

- **`bench.py`'s `generator_totals` is empty (`{}`) in every recorded manifest,
  and this has never been investigated.** It's built from
  `{s.op: s.values for s in samples if s.kind == SUMMARY}` (the generator's
  final cumulative block) at `bench.py:335,383` — noticed twice in this
  project's history, flagged both times, chased neither. Candidate causes
  nobody has checked: the `SUMMARY` block may not survive
  `--tolerate-errors` or the `script`-based TTY wrapper (`ssh.force_tty`), or
  `WorkloadParser` may not be classifying it as `SUMMARY` for the generator
  version in use (v26.3.0). Whoever looks at this next should start by
  grepping a raw bench output file (`runs/*_bench_cluster/raw/*.txt`) for
  whatever line the generator prints as its final cumulative summary and
  checking whether `crdblab/core/workload.py` recognises it.

## Working with this codebase

- **Runs are immutable and self-describing.** Never write code that mutates a
  run directory after the fact except through `recorder.py`'s own writers —
  analysis and figures must always be reproducible from what's on disk.
- **No positional column parsing.** If you touch `workload.py` or any CSV
  reader, bind by header name, not column index — this is a direct fix for a
  prior defect (D6).
- Changes to `analysis/loader.py`'s gating (what counts as a valid run) or to
  `preflight.py`/`validation.py`'s checks are high-stakes: they're the layer
  that is supposed to catch a misconfigured measurement before it reaches a
  figure. If you loosen a check, explain why in the commit — the defect IDs
  cited in nearby comments/docstrings (e.g. D9, D11) indicate it exists
  because of a specific prior defect.
- `runs/` and `figures/` are gitignored data directories, not source; don't
  try to "clean up" their contents as part of unrelated changes.

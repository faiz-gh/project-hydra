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
./run-experiment.sh              # full sweep (~75 min) against a live testbed
./run-experiment.sh --smoke      # harness self-test (~8 min)
```

This drives `terraform` (provisioning), then the CLI phases in order:
`crdblab net probe` → `crdblab bench` → `crdblab chaos run` → `crdblab validate`
→ `crdblab analyze ...` → `crdblab report figures`. The ordering is
load-bearing (each phase consumes artefacts the previous one produced), not
conventional — see `instructions.md` §6 before reordering anything.
`run-experiment.sh` only ever benchmarks the default engine (CockroachDB) —
it takes no `--engine` flag; to measure PostgreSQL you invoke `crdblab
--engine postgresql bench ...`/`chaos run ...` by hand per `instructions.md`.
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
- `ssh.py` — remote command execution against testbed nodes.
- `workload.py` — parses the CockroachDB generator's stdout. Column binding
  is by the generator's own header line, never positional, on purpose.
- `preflight.py` — asks "was the system fit to be measured?" *before* a
  measurement runs (seed/insert-count agreement, quorum floor achievability,
  leaseholder placement, hardware fingerprint). Distinct from `validate`
  (below), which asks a different question after the fact — see README.md's
  design commitments for why both are needed and neither substitutes for the
  other.
- `recorder.py` — writes the run directory (`manifest.json`, `metrics.csv`,
  `preflight.json`, raw generator stdout under `raw/`).
- `rto_probe.py` — the independent high-frequency canary-write client used
  during chaos runs to time recovery at sub-second resolution; separate from
  both the generator and the RPO audit writer because those sample too
  coarsely to answer "when did writes resume."

**`crdblab/phases/`** — one module per measurement phase:
- `p1_network.py` — RTT matrix, clock offsets, derives the quorum floor
  other phases assert against.
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
  skipped when `engine == "postgresql"`. There is a single
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
  all. For PostgreSQL, `resolve_patroni_primary()` queries every node's
  Patroni REST API (`:8008/primary`) live, immediately before scheduling the
  fault, and targets whichever one actually answers as primary — nothing
  pins Patroni's leader to a specific node the way CockroachDB's
  `lease_preferences` does, so the profile's static `chaos.target` cannot be
  trusted for that engine.

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
`args.engine` in `_cmd_bench`/`_cmd_chaos`. **It must precede the
subcommand** (`crdblab --engine postgresql bench --profile ...`) — argparse
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

## Known gotchas (found and fixed this session — don't reintroduce)

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
- **`Manifest.engine` and figure filenames are engine-aware.** Nothing
  recorded which engine produced a run before this -- `manifest.cockroach_version`
  being null and a note reading "engine: postgresql (patroni HA)" were the only
  signals, and `report figures` used neither, so a PostgreSQL run's
  `fig2_throughput_sweep.png` would silently overwrite a CockroachDB run's
  figure of the same name. `Manifest` now carries an explicit `engine` field
  (set by `bench.py`/`p4_chaos.py` at construction; defaults to
  `"cockroachdb"` for every run written before the field existed, since that's
  what all of them were), exposed as `Run.engine` in the loader.
  `figures.py`'s `_engine_suffix()` reads it and appends `_postgresql` to
  fig2/fig3/fig5/fig6's filenames -- blank for `cockroachdb`, so every
  filename and caption written before Postgres runs existed keeps meaning the
  same figure. `fig1_network_matrix` is deliberately never suffixed: Phase I
  measures the network substrate, which doesn't change between engine runs on
  the same infrastructure. `--cluster` and `--network` still accept one run id
  each, so comparing both engines means invoking `report figures` twice (once
  per engine's cluster/chaos run ids) into the same `--out` directory; the
  suffix is what keeps that safe instead of `--cluster`/`--chaos` needing to
  become multi-valued.
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
- **The generator's run length is `generator_duration_s(chaos)`, not
  `chaos.duration_s`.** `inject_at_s` is measured from the generator's first
  sample, so `duration_s` alone guarantees nothing about how much series
  follows the fault; the run is extended to
  `inject_at_s + min_post_fault_s` (default 60 s) when it would otherwise be
  shorter, and never shortened. `smoke` sets `min_post_fault_s: 20`
  explicitly, so the self-test staying short is a decision rather than an
  accident of the default.
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
- **`bench.py`'s per-tier setup time is measured from `tier_start`, not
  `t_zero`.** `t_zero` is the sweep-wide epoch and must stay that way --
  `wall_offset_s` and `generator_start_offset_s` exist to make ticks from
  different tiers orderable against each other. Measuring *connection setup*
  from it instead reported everything since the sweep began, growing by one
  tier's duration plus cooldown each iteration (18.8s to 1042.5s across 12
  tiers, ~92 s per step) and firing the >10 s warning on every tier, against a
  true setup cost of 0.2-4.8 s. The per-tier figure is also recorded as
  `connection_setup_s`.
- **Patroni's leader is not pinned to any node.** CockroachDB's
  `lease_preferences` deliberately biases the leaseholder onto `gcp-1`
  (asserted by `preflight.check_leaseholder_placement`), but nothing in
  `bootstrap-patroni.tftpl` does the equivalent for Patroni — its leader is
  decided by etcd-based election among all five nodes at bootstrap, so a
  profile's static `chaos.target: gcp-1` cannot be trusted to name the
  PostgreSQL primary. `p4_chaos.py::resolve_patroni_primary` queries every
  node's `:8008/primary` live, immediately before scheduling the fault, and
  refuses outright (rather than guessing) if zero or more than one node
  answers 200.

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

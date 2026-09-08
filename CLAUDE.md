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

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

**Status as of 2026-09-08 (update or delete this note once the PostgreSQL
phase has actually been run — it will go stale the moment that happens):**
the CockroachDB phase has been run end-to-end at thesis scale (C=100, full
`net probe` → `bench` → `chaos run --mode recover` → `chaos run --mode dead`
→ `validate` → `analyze` → `report figures`) with every fix in `git log`
verified against that run, not just against the `smoke` profile. The
PostgreSQL phase has **not been run at all** in this project's history yet.
Testing it needs, in order: (1) `terraform apply -var="database_engine=postgresql"`
in `terraform/` — this **replaces every cluster node**, it is not both engines
running at once; (2) either `./run-experiment.sh --engine postgresql` or the
manual command sequence in `instructions.md` §6;
(3) `crdblab analyze engine-comparison --crdb <run> --pg <run>` to compare.
The first attempt at (1) was made on 2026-09-08 and **the deployment did not
come up**: `bootstrap-patroni.tftpl` wrote its config to
`/etc/patroni/patroni.yml` while Ubuntu's packaged `patroni.service` reads only
`/etc/patroni/config.yml`, so every node provisioned "successfully" with no
database process running at all (see the gotcha below). The template is fixed;
the fix has **not been through a `terraform apply` yet**, and the live testbed
still has the config under the wrong name. Two things are implemented but
specifically **untested** because of all this:
`p4_chaos.py::restore_target`'s `engine == "postgresql"` branch
(`systemctl start patroni`, polling the target's `:8008/health`) has no real
Patroni cluster to have exercised it against, and `resolve_patroni_primary`
likewise. Watch both closely on the first PostgreSQL `chaos run`. Separately,
at the time of this note, this development machine could not reach the
testbed at all (`ssh: Could not resolve hostname crdb-gcp-1`) — check
`tailscale status` before assuming the cluster is reachable or run anything
against it.

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
exactly one answers `:8008/primary` — nothing pins Patroni's leader, so *which*
node is not asserted); the row count switches from `cockroach sql --url` to
`psql`, which cannot be skipped because `cockroach sql` is not a general
postgres client; and the dead-mode restore backstop becomes `sudo -n systemctl
start patroni` swept across every member rather than aimed at `chaos.target`,
because for PostgreSQL the fault target is resolved live and is only recorded in
the run's `events.json`.
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
  it for the same reason, and Patroni has no equivalent to assert (its leader
  is an unbiased etcd election, which is why `chaos run` resolves the primary
  live).
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
  sequential scan failed it for its own reason. `bench.py` now runs the probe **and** `check_write_latency_floor` for
  both engines: Patroni's `synchronous_standby_names: ANY 2 (*)` waits for two
  standby acks, the same geometry as a 3-of-5 Raft quorum, so Phase I's floor
  bounds both. `n_tup_upd` is deliberately not added to the fetched-row count --
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
  `config.pg_haproxy_dsn` (one host, `127.0.0.1:5000`) is for
  `cockroach workload run`, which has to be given exactly one URL.
  `config.pg_direct_dsn` (every node at 5432, `target_session_attrs=read-write`)
  is for the RPO audit writer and the RTO probe. Both went through HAProxy
  before, which made two deliberately independent measurements share one
  component on the client node: a hiccup there would be indistinguishable from
  a cluster outage, and `on-marked-down shutdown-sessions` drops their
  in-flight connections at every failover. libpq resolves the primary for them
  instead -- the direct counterpart of the multi-host DSN the CockroachDB
  branch already used for these two clients, and safe for the same reason
  (single connections, not `--concurrency`-many, so the serial-dial cost that
  rules multi-host out for the generator does not apply).
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
- **The databases are created by whichever node won the election, not by
  `PEERS[0]`.** Nothing pins Patroni's leader, so naming a node in advance is a
  guess: on 2026-09-08 the primary was `crdb-azure-1` while `PEERS[0]` is
  `crdb-gcp-1`, which left gcp-1 waiting out its primary-poll and failing
  provisioning while `ycsb` was never created at all. Every node now polls its
  own `:8008/primary` and stops as soon as it sees a peer has won -- exactly one
  node can answer, so exactly one creates.
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

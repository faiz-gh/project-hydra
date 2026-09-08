# Reproducing the experiment

End-to-end instructions for provisioning the testbed and reproducing every
measurement in *Empirical Evaluation of a Self-Healing Multi-Cloud Database
Failover System*.

**Assumed already in place:** an HCP Terraform workspace with all provider
credentials and input variables configured. Appendix A lists exactly what those
are. This document does not cover obtaining the credentials themselves.

**Total wall time:** about 1 h 15 min, of which ~55 min is unattended sweeping.

## The short version

Once the testbed is provisioned (§2) and `.env` is set (§4), the entire
measurement is one command:

```bash
./run-experiment.sh
```

It checks the workstation and the testbed, loads the working set, runs the
network-substrate phase and the benchmark, injects both chaos fault classes and
restores the node killed by the `dead` run, validates every run, prints the
analysis and renders the figures. It stops at the first failure rather than
continuing with a testbed that is not fit to be measured.

```bash
./run-experiment.sh --smoke        # ~8 min end-to-end harness self-test
./run-experiment.sh --skip-load    # working set already loaded
./run-experiment.sh --no-chaos     # network substrate and benchmark only
```

`run-experiment.sh` does not itself take an `--engine` flag; it always drives
`crdblab bench` with the default engine, CockroachDB. To measure the
PostgreSQL/Patroni side of the comparison, invoke `crdblab` directly with
`--engine postgresql` per §6, "Benchmark" — the top-level flag has to precede
the subcommand.

The rest of this document explains what each step does and how to run them by
hand, which is what you want when something fails or when you are changing the
protocol.

---

## 0. What you need on the workstation

| Requirement | Why |
|---|---|
| Python 3.11+ | the harness |
| Terraform CLI, logged in to HCP (`terraform login`) | provisioning |
| Tailscale, joined to the same tailnet as the nodes (authenticate with the same account or auth key the nodes use; check with `tailscale status`) | every node is addressed by its MagicDNS name (`crdb-linode-1`, …), never by public IP |
| An SSH key matching `ssh_public_key` | the harness runs the generator *on the client node*, not the workstation |

The workstation never touches the database over the WAN. It orchestrates over
SSH and writes CSV; the load generator runs on the dedicated client node
(`crdb-client-1`), never on a node that is itself part of the system under
test. This is deliberate — a client-side round trip from the workstation would
dominate and mask the consensus latency being measured (§4.4).

---

## 1. Clone and install

```bash
git clone https://github.com/faiz-gh/project-hydra.git
cd project-hydra

python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

> **The virtualenv is `.venv/`, and dependencies come from `pyproject.toml`.**
> An older workflow used `python3 -m venv .` (installing into the repository root)
> with a `requirements.txt`. Neither applies: `python3 -m venv .` scatters `bin/`,
> `lib/` and `pyvenv.cfg` through the repository, and there is no
> `requirements.txt` — the dependency set is declared in `pyproject.toml`, and
> `-e ".[dev]"` is what puts the `crdblab` console script on disk.

Confirm the harness is importable and its own tests pass before touching cloud
resources:

```bash
.venv/bin/python -m pytest tests/ -q      # expect: all passing (count grows over time)
.venv/bin/crdblab --help
```

The console script lives at `.venv/bin/crdblab`. Every command below uses that
path explicitly rather than assuming an activated virtualenv.

---

## 2. Provision the testbed

```bash
cd terraform
terraform init
terraform plan -out plan.out
terraform apply "plan.out"
cd ..
```

`plan.out` is a build artefact referencing a specific remote run; it is not
source and should not be committed.

To tear the testbed down later — **destroys everything, no confirmation**:

```bash
cd terraform && terraform destroy -auto-approve
```

This creates six machines — five cluster nodes and one dedicated client/
generator node — each 2 vCPU / ~3.8 GiB, joins them over Tailscale, and
bootstraps the database engine selected by `var.database_engine`
(CockroachDB or PostgreSQL/Patroni) through cloud-init.

**Wait for cloud-init to finish before continuing.** `terraform apply` returns as
soon as the provider APIs acknowledge the instances; the bootstrap continues on
the machines for another two to three minutes. Watch the primary:

```bash
ssh root@crdb-linode-1 'tail -f /var/log/cloud-init-output.log'
```

> `crdb-linode-1` is the *bootstrap* primary — the first entry in
> `cluster_join_nodes`, which is the node cloud-init elects to run `cockroach
> init` and apply the zone configuration. It is **not** the harness gateway, and
> it is not where measurements run from either. The gateway is `crdb-gcp-1`
> (`crdblab/topology.py`), which is the CockroachDB member the leaseholder is
> preferred onto; the generator and audit clients instead run from the separate,
> dedicated client node `crdb-client-1` (`crdblab.topology.CLIENT_NODE`), which is
> not a cluster member and carries no replica.

You are waiting for the zone-configuration step to report success. It logs
`Only 1/5 nodes live` → … → `All 5 nodes are live`, then applies
`num_replicas = 5` and `lease_preferences`. **The bootstrap deliberately exits
non-zero if the lease preference does not apply**, rather than reporting success
on a partial configuration — an earlier version did the latter and left the
leaseholder in `centralindia`, costing 12.3x throughput while the cluster
reported full health (§3.3).

Verify placement took:

```bash
ssh ubuntu@crdb-gcp-1 "cockroach sql --insecure --host=crdb-gcp-1:26257 \
  -e 'SHOW ZONE CONFIGURATION FROM DATABASE ycsb;'"
```

`num_replicas` must be `5`. An empty `lease_preferences` means the bootstrap
raced; re-run `terraform apply` or re-apply the zone config by hand.

**`lease_preferences` must name `us-east1` first — this is a required manual step
after `terraform apply`.** The bootstrap writes
`[[+region=us-east], [+region=us-east1], [+region=us-west]]`, which put the
leaseholder on `crdb-linode-1` back when that was the gateway. The gateway is now
`crdb-gcp-1` in `us-east1`, and a leaseholder in `us-east` would put an ~20 ms
wide-area hop on *every* operation the generator issues — on a write path whose
quorum floor is ~70 ms, a ~30% inflation that would be read as replication cost.
Nothing about the cluster looks unhealthy when this happens; it is D7's shape with
a smaller constant. Re-order the list on the live cluster:

```bash
ssh ubuntu@crdb-gcp-1 "cockroach sql --insecure --host=crdb-gcp-1:26257 -e \
  \"ALTER RANGE default CONFIGURE ZONE USING lease_preferences =
    '[[+region=us-east1], [+region=us-east], [+region=us-west]]';\""
```

Leases transfer within a few seconds. `run-experiment.sh` refuses to start unless
the gateway's region heads the list, and `crdblab net probe` asserts the placement
that actually resulted, so a forgotten re-order aborts the sweep rather than
reaching a figure.

`terraform/scripts/bootstrap.tftpl` still writes the old order and is outside this
repository's change scope (it is applied manually). Until that line is re-ordered
there, **every fresh `terraform apply` needs the statement above re-applied.**

---

## 3. Load the working set — **required**

`run-experiment.sh` does this for you, reading `--seed` and `--insert-count`
**from the profile it is about to sweep with** so the two cannot drift apart. To
do it by hand, or to understand what the script is doing:

The bootstrap creates the `ycsb` database but no tables. Load the cluster,
from the client node:

```bash
# CockroachDB
ssh ubuntu@crdb-client-1 "cockroach workload init ycsb --drop \
  --seed=42 --insert-count=125000 \
  'postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable'"

# PostgreSQL/Patroni, through the client node's local HAProxy
ssh ubuntu@crdb-client-1 "cockroach workload init ycsb --drop \
  --seed=42 --insert-count=125000 \
  'postgresql://root@127.0.0.1:5000/ycsb?sslmode=disable'"
```

Load whichever engine (or both) you intend to sweep with; each engine's
working set is independent, so a CockroachDB run and a PostgreSQL run can
coexist without reloading between them.

> **`--seed` and `--insert-count` must equal the values in the profile you are
> about to sweep with.** This is the single most dangerous parameter in the
> project. The generator's default seed changes on *every invocation*, so a
> mismatch silently addresses a different keyspace than the one loaded: every
> read and update matches zero rows, returns in ~3 ms, and reports roughly
> twenty times the throughput at a twenty-fifth of the latency. It does not
> error. It looks like the best result the testbed has ever produced
> (D8).

The seed must agree with the profile you intend to sweep with, which is why the
script derives it from that profile rather than hardcoding it — a second copy of
this number is exactly how it would drift. Pre-flight asserts the agreement
before every tier, so a mismatch aborts the sweep rather than reaching a figure,
but getting it right here is much cheaper than discovering it after a tier.

The database must be named literally `ycsb`; `workload init` rejects a URI
naming anything else.

Each load writes 125,000 rows ≈ 179 MiB and takes about a minute.

---

## 4. Point the harness at the cluster

Create `.env` in the repository root — one line naming whichever engine's
entrypoint you intend to load and capture against (see `.env.example`):

```bash
# CockroachDB
DB_URI=postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable
# PostgreSQL/Patroni, instead: DB_URI=postgresql://postgres:postgres@127.0.0.1:5000/ycsb?sslmode=disable
CRDBLAB_RUNS_DIR=runs
```

> **`DB_URI` is for data loading and `crdblab capture` only; the measured
> phases (`bench`, `chaos run`) resolve their own connection string from
> `crdblab/topology.py` and `--engine`, and never read `DB_URI`.** A stale or
> mismatched `DB_URI` therefore does not affect a measurement's correctness,
> but it does mean `crdblab capture` pins the generator's column layout
> against a different node (or a different engine's SQL dialect entirely)
> than the one about to be swept — keep it pointed at the engine you are
> about to benchmark.

Those are the only two variables `crdblab` reads. An older `.env.example` also
carried `HCP_TOKEN`, `HCP_ORG` and `HCP_WORKSPACE`; nothing in this harness uses
them.

**One property of `DB_URI` is load-bearing, and `run-experiment.sh` asserts it
before it will start: it must name the `ycsb` database, not `defaultdb`.**
`cockroach workload init ycsb` refuses any other database name, so a URI
pointing elsewhere cannot have a loaded working set behind it — every
operation would match zero rows, which fails *flatteringly* (§3).

A multi-host `DB_URI` is fine, and deliberately supported: since `DB_URI` is
never read on a measured path (above), a comma-separated host list does not
put the wide-area network on anything being timed. It is what lets loading
and `crdblab capture` survive the primary being down, e.g.:

```bash
DB_URI=postgresql://root@crdb-gcp-1:26257,crdb-linode-1:26257,crdb-linode-2:26257,crdb-azure-1:26257,crdb-azure-2:26257/ycsb?sslmode=disable
```

**Give every host its own `:26257`.** The PostgreSQL URI form also allows a
single trailing port applying only to hosts that don't specify their own
(`host1,host2:26257`), which silently defaults every *other* listed host to
port 5432 — not CockroachDB's `26257`. Repeating the port after each host
avoids relying on that fallback.

---

## 5. Smoke test the harness against the live testbed

Before committing an hour to the full sweep, confirm the generator's output
format still parses and the pre-flight assertions pass:

```bash
.venv/bin/crdblab capture --node gcp-1 --pty --duration 15
```

`--node` defaults to `gcp-1`, the gateway, so it can be omitted; a measured
sweep instead runs the generator from the dedicated client node, but the
captured column layout depends only on the deployed CockroachDB version, not
on which cluster member ran the capture. If it raises `WorkloadParseError`,
the generator's output format has changed and the parser needs updating
before any measurement is trustworthy — that is the intended behaviour, not a
bug (D6).

Then a two-tier end-to-end pass, about four minutes:

```bash
.venv/bin/crdblab net probe    --profile smoke
.venv/bin/crdblab bench --profile smoke
# PostgreSQL/Patroni instead: crdblab --engine postgresql bench --profile smoke
```

`bench` ends with `all N pre-flight checks passed`; `net probe` prints a `[PASS]`
line per assertion and reports the derived quorum floor. Any `[FAIL]`, or a
non-zero exit, means the testbed is not fit to measure — fix it before sweeping
rather than re-running and hoping.

---

## 6. The measurement phases

Run in this order. Each phase consumes artefacts from the one before, so the
ordering is load-bearing rather than conventional.

### Phase I — network substrate (~3 min)

```bash
.venv/bin/crdblab net probe --profile thesis-extended
```

Produces the all-pairs RTT matrix, MTU, clock offsets and leaseholder placement,
and derives the **quorum floor** — the round trip to the second-fastest
follower, which bounds every committed write. The benchmark and chaos phases
read that floor from the most recent Phase I run to assert their write
latencies are physically achievable.

Expect a floor near 67 ms and the ordering
`gcp-1 < linode-2 << azure-1 < azure-2`.

**The ordering is what matters; the absolute values are not stable.** Across
three deployments of this topology `gcp-1` has measured 18.3–24.7 ms and
`azure-1` 180.1–198.2 ms — up to 23% apart — while `linode-2`, which sets the
quorum floor, has stayed within 67.1 ± 4 ms. Every downstream assertion and the
quorum geometry depend on the *rank* of the links, not their magnitudes, so a
deployment whose ordering is unchanged is comparable even when its latencies are
not identical. A deployment whose ordering has changed is a different experiment
and its benchmark runs must not be pooled with earlier ones.

### Phase II — benchmark, five-node cluster, per engine (~26 min each)

```bash
.venv/bin/crdblab bench --profile thesis-extended
.venv/bin/crdblab --engine postgresql bench --profile thesis-extended
```

Run once per engine you want in the comparison. `--engine` is a top-level flag
and must precede the subcommand; passing it after `bench` is rejected by
argparse rather than silently ignored.

Sweeps C = 1, 2, 5, 10, 50, 100, 200 with three repetitions each, in an
order shuffled from the **profile seed** rather than the wall clock, so the
realised order is reproducible and is recorded in the manifest. Randomisation is
not cosmetic: it is what allows drift across a sweep to be separated from a
difference between tiers, and §7.3's elimination of cumulative degradation is
only possible because of it.

Run both engines with the **same profile**. The analysis layer refuses to
compare two runs whose generator, mix, distribution, seed, insert count,
duration or warmup differ. Concurrency is deliberately *not* one of those keys.

> Sweeping down to C = 1 is what makes the primary result possible. At one
> worker a closed-loop generator has exactly one operation outstanding, so the
> measured median contains no queueing at all and the two engines can be
> compared without either being confounded by load (§6.4.1).

### Phases III–IV — fault injection (~4 min each)

Phase III is the `recover` fault, Phase IV is `dead`:

```bash
.venv/bin/crdblab chaos run --mode recover --profile thesis-extended   # Phase III
.venv/bin/crdblab chaos run --mode dead    --profile thesis-extended   # Phase IV
# PostgreSQL/Patroni instead: crdblab --engine postgresql chaos run --mode ...
```

`recover` severs the overlay network of the fault target for 45 s and restores
it; `dead` kills the process outright. The target is the **primary** — the
node genuinely coordinating writes, not a peripheral member — because failing
a node outside the write path would be a far weaker test.

For CockroachDB, `profiles/*.yaml` name the target explicitly
(`chaos.target: gcp-1`, the node `lease_preferences` pins the leaseholder to;
`preflight.check_leaseholder_placement` asserts that placement before the
fault fires). For PostgreSQL/Patroni, that static name cannot be trusted —
nothing pins which node wins Patroni's leader election — so
`crdblab/phases/p4_chaos.py::resolve_patroni_primary` queries every node's
Patroni REST API (`:8008/primary`) immediately before scheduling the fault
and targets whichever one actually answers as primary, overriding
`chaos.target` if it names a different node. The manifest and `events.json`
record whichever node was actually faulted.

Each chaos run now also carries a **high-frequency RTO probe**. It is a third
client, independent of both the generator and the RPO audit writer: its own
threads, its own connections, its own table (`bench.rto_canary`), started and
stopped with the run. It writes canary rows continuously and records when the
database stopped and resumed serving them, which is the one question neither of
the other two can answer at a useful resolution — the generator samples once a
second, and the audit writer is serialised at the cost of one quorum write.

It leaves two files in the run directory:

| File | What it is |
|---|---|
| `rto_probe.csv` | One row per canary write: dispatch and completion offsets, duration, and the outcome (`ok`, `timeout`, `conn_error`, `refused`). Written under its own declared schema and checked by `crdblab validate`. |
| `rto_probe.log` | JSON per line, flushed as it happens: every failure, every connection opened or lost, every successful reconnect, with microsecond timestamps. It survives a run that is killed mid-fault, which the CSV does not. |

Three things to know before quoting a number from it.

- **Read `observed_outage_s`, not just `rto_s`.** The probe writes from your
  workstation, 376 ms round trip from the gateway, so every timestamp it takes
  carries about 188 ms of link. That offset is identical on both edges of an
  outage and cancels in the interval between two of the probe's own observations;
  it does not cancel against the injector's fault timestamp.
- **`detection_lag_s` is not part of the recovery.** A five-voter cluster losing
  one member keeps committing until it notices, ~6 s here. The probe reports the
  detection interval separately rather than folding it into the RTO — which is
  precisely the conflation that made the legacy pipeline's 6.0 s and 5.2 s
  "RTOs" measurements of its own guard interval.
- **`resolution_s` is what the figure may be quoted to,** and it is measured per
  run rather than taken from `probe_interval_s`. The dispatch cadence is 2 ms;
  the achieved cadence is bounded by the pool size over the write cost, and from
  this workstation a canary write costs ~370 ms — the link, not the quorum. Two
  60 s runs on 2026-09-05 measured **125 ms at the default eight workers** and
  **64 ms at twenty-four**. A reported outage shorter than that is
  indistinguishable from no interruption, and the probe says so instead of
  printing the smaller number. `resolution_s` is the 95th percentile of the gap
  between served writes, not the median: compare it against `gap_p50_s`, and if
  they differ by orders of magnitude the pool was sampling in bursts and the
  coarser number is the real one.

The probe adds 18 writes/s to a cluster already serving ~371 — about 5%, measured
— and both figures are recorded (`events.json` → `probe`) so the perturbation can
be checked rather than assumed. Raising `chaos.probe_workers` buys resolution
sub-linearly and load linearly (24 workers: 64 ms, 43 writes/s, ~12%), so raise it
when the outage being timed is short enough to need it and read `resolution_s`
back afterwards. `chaos.probe_enabled: false` turns it off; the RPO audit and
every other Phase III/IV figure are unaffected either way.

If you need single-digit-millisecond resolution, the lever is *where the probe
runs*, not the pool size: a client on the gateway pays ~70 ms a write instead of
~370 ms. This probe runs from the workstation deliberately — it matches the RPO
audit writer, keeps the log on the machine that analyses it, and survives the node
under test going away — but that choice is what sets the ceiling.

To run the probe on its own, with no benchmark anywhere — to check it reaches the
cluster and see its achieved rate against the live link, or to time an outage
this harness did not cause:

```bash
.venv/bin/crdblab probe rto --duration 60
.venv/bin/crdblab probe rto --duration 300 --workers 16   # finer, more load
```

It writes a normal run directory (`runs/<stamp>_p4-probe/`) with a manifest, so
its numbers are traceable like any other measurement.

**A `dead` run leaves the node down. The harness does not restore it** — the
fault is real, and restarting is a deliberate operator action.

For CockroachDB, the process is launched by cloud-init with `--background`
rather than as a systemd unit, so there is no service to start and a reboot
will not bring it back either. Replay the start command — since the target is
now `gcp-1` itself by default, `--join` must name a *different* live peer, not
the node being restarted:

```bash
ssh ubuntu@crdb-gcp-1 'TS_IP=$(tailscale ip -4); cockroach start --insecure \
  --store=/var/lib/cockroach \
  --listen-addr=$TS_IP:26257 --advertise-addr=$TS_IP:26257 \
  --locality=cloud=gcp,region=us-east1 \
  --cache=0.25 --max-sql-memory=0.25 \
  --join=crdb-linode-1:26257 --background'

sleep 20
ssh ubuntu@crdb-linode-1 "cockroach node status --insecure --host=crdb-linode-1:26257"
```

(If your profile names a different `chaos.target`, substitute that node's own
user/host/locality above, and any *other* live node for `--join`.)

All five nodes must show `is_available = true` again before the next
measurement.

> `--cache=0.25 --max-sql-memory=0.25` are **not optional here**. Every node in
> the comparison must be started with identical memory flags. Omitting them
> takes CockroachDB's 128 MiB default — a roughly fifteen-fold smaller block
> cache against a 205 MB working set — and the resulting difference would
> conflate replication cost with cache residency, which is exactly the defect
> D9 records. Restarting one node with different flags silently reintroduces
> it.

For PostgreSQL/Patroni, `dead` kills `patroni` and `postgres` on the target
(`terraform/scripts/bootstrap-patroni.tftpl` enables and starts `patroni` as a
systemd unit, unlike CockroachDB). Whether the unit's own restart policy
brings it back on its own, or a manual `systemctl start patroni` on the target
is needed, has not been characterised here as precisely as the CockroachDB
path above — check `systemctl status patroni` on the target and Patroni's own
cluster state (`patronictl list`) before trusting the node is fully rejoined,
rather than assuming the CockroachDB timeline applies.

---

## 7. Validate every run

```bash
for m in runs/*/metrics.csv; do
  d=$(dirname "$m"); echo "== $d"; .venv/bin/crdblab validate "$d"
done
```

Every run must report `PASS`. The glob is on `metrics.csv` rather than on the
directories because a Phase I run records `network.csv` under a different schema
and has no workload samples to check — its assertions live in `preflight.json`. Validation checks internal consistency —
plausibility ceiling, quantile ordering, Little's law, sample cadence, operation
coverage, error monotonicity.

**Validation and pre-flight ask different questions and neither substitutes for
the other.** Validation asks whether the recorded numbers are consistent with
each other; pre-flight asks whether the system was fit to be measured. The
defects that mattered most in this project produced perfectly consistent data
from a misconfigured system, so the run whose pre-flight failed is exactly the
run whose numbers look fine. The analysis layer refuses a run that fails either.

---

## 8. Analysis

```bash
# Per-phase steady state
.venv/bin/crdblab analyze steady-state <crdb-run-id>
.venv/bin/crdblab analyze steady-state <pg-run-id>

# Replication cost and engine comparison, all three framings
.venv/bin/crdblab analyze engine-comparison \
  --crdb <crdb-run-id> --pg <pg-run-id>

# RTO and RPO with their measurement limits
.venv/bin/crdblab analyze resilience <chaos-recover-run-id>
.venv/bin/crdblab analyze resilience <chaos-dead-run-id>
```

Run ids are the directory names under `runs/`. Add `--json` to any of these for
machine-readable output.

`--accept-hardware-difference` **should not normally be needed.** The
CockroachDB and PostgreSQL/Patroni runs are measured on the same five-node
topology — every node is the same machine type per provider
(`n2-custom-2-4096`, `g6-dedicated-2`, `Standard_B2ls_v2`), so the two runs
should differ in engine and nothing else the harness can see, and
`engine-comparison` should refuse only if that stops being true (a
redeployment onto different instance types, or a mid-comparison edit to
`terraform/variables.tf`). If it refuses, read the refusal, which names
exactly what differs, before reaching for the flag — the flag downgrades the
refusal to a recorded warning rather than fixing the underlying mismatch.

`run-experiment.sh` does not run `analyze engine-comparison` itself: it
benchmarks one engine per invocation (§ "The short version"), so run it once
per engine and then invoke `engine-comparison` by hand with both run ids.

`engine-comparison` prints the same-concurrency delta under a **NOT A RESULT**
banner. That is intentional: refuting the intuitive comparison is more useful
than omitting it.

---

## 9. Figures

```bash
.venv/bin/crdblab report figures                    # newest run of each phase
```

`report figures` writes up to five PNGs (network matrix, throughput sweep,
latency by operation, and one resilience timeline per fault class) at ≥4K
with a vector PDF beside each, into `figures/`. The throughput-sweep and
latency-by-operation figures are drawn from whichever single benchmark run is
picked — CockroachDB or PostgreSQL, whichever the `--cluster` run id names or
was most recently benchmarked — not from both engines at once; there is no
per-figure engine comparison yet, only `analyze engine-comparison`'s tabular
output (§8). To pin specific runs rather than the newest:

```bash
.venv/bin/crdblab report figures \
  --network <p1-run> --cluster <bench-run> --chaos <p4-run>
```

Every figure resolves its inputs through the analysis loader, so a run without a
manifest, or one failing validation or pre-flight, cannot reach a figure at all.

---

## 10. Tear down

```bash
cd terraform && terraform destroy -auto-approve   # no confirmation prompt
```

`runs/` and `figures/` are gitignored and survive. Each run directory is
self-describing — manifest, metrics, pre-flight report and the generator's raw
stdout — so the analysis and figures can be regenerated with no testbed at all.

---

## Profiles

| Profile | Tiers | Reps | Duration | Use |
|---|---|---|---|---|
| `smoke` | 10, 50 | 1 | 15 s | harness self-test, ~4 min |
| `thesis` | 10, 50, 100, 200 | 3 | 60 s | the original sweep |
| `thesis-extended` | 1, 2, 5, 10, 50, 100, 200 | 3 | 60 s | **the dissertation's sweep** |

Inspect a resolved profile before running it:

```bash
.venv/bin/crdblab profile thesis-extended
```

All three share `seed: 42` and `insert_count: 125000`, matching §3's load
command. `smoke` shares them deliberately: `workload init ycsb` insists the
database be named `ycsb`, so a smoke working set and a thesis working set cannot
coexist.

---

## Troubleshooting

These recur across redeployments. None is a code change; all have been hit more
than once.

**Every Tailscale IP changes on redeploy, the localities do not.** `topology.py`
matches on locality, so it needs no edit. Anything caching an IP does.

**`Access to crdb_internal and system is restricted` (SQLSTATE 42501).** The
harness already prefixes `SET allow_unsafe_internals = true` to its one
introspection query, scoped to that invocation and never to the workload's own
connections. If you hit this running SQL by hand, add the same prefix.

**A pre-flight `row_match` failure.** Almost always the seed or insert-count in
§3 not matching the profile. Re-load the working set with the profile's values.

**A tier reporting `the statement-statistics view was flushed after this tier
ended`.** CockroachDB flushes its in-memory statistics view every 10 minutes,
which zeroes the counters this check differences. On a 26-minute sweep this hits
one or two tiers. It is handled — the run continues if the quorum-floor check
independently corroborates that tier — and needs no action.

Do **not** raise `sql.stats.flush.interval` to avoid it. Flushing costs
background disk I/O on a two-core host under a saturated workload, so
suppressing it would raise the throughput being measured and make runs before
and after the change incomparable (§5.4).

**A node fails to connect to itself by its own hostname.** MagicDNS resolves
the node's own hostname to an interface the database is not bound to. Every
bootstrap script pins the overlay address into `/etc/hosts`; if that step was
skipped on a given node, add it:

```bash
ssh ubuntu@crdb-client-1 'TS_IP=$(tailscale ip -4); \
  grep -qxF "$TS_IP $(hostname)" /etc/hosts || echo "$TS_IP $(hostname)" | sudo tee -a /etc/hosts'
```

**A sweep aborts on an SSH timeout.** Pre-flight control commands allow 60 s.
That is a hang detector, not a latency budget — it bounds no measurement — so a
genuine timeout means the link or the node is unwell, not that the budget is
tight.

**Results differ from the dissertation's.** Absolute throughput is a property of
the hardware and the day. Between two deployments of nominally identical
instances this project measured a 22% baseline shift with every recorded
parameter identical (§7.3). Compare *ratios* and *recovery behaviour*, and
compare only runs the harness agrees are comparable — `engine-comparison`
refuses outright if the two runs' server flags, workload parameters, versions
or hardware differ.

---

## The RTO probe

Both `crdblab chaos run` and `crdblab probe rto` produce `rto_probe.csv` and
`rto_probe.log`; §6 covers what they are and how to read them. Two operational
notes:

- `crdblab validate <run>` checks the probe log alongside `metrics.csv` and fails
  the run if the two offset columns disagree, a sequence number repeats, an
  outcome is unrecognised, or nothing was ever served. `analysis/loader.py`
  applies the same gate, so a corrupt probe log cannot reach a figure.
- The canary table is dropped and recreated at the start of each run. Pass
  `--keep-table` to `probe rto` when probing a cluster you would rather not issue
  DDL against.

---

## What a run directory contains

```
runs/20260902T195644Z_bench_cluster/
├── manifest.json    git revision, resolved profile, topology, generator
│                    command, server start command, host CPU/memory,
│                    realised tier order, clock epoch
├── metrics.csv      long format: one row per (interval, operation type)
├── preflight.json   every assertion with its observed value
├── audit.csv        Phases III/IV only: one row per RPO audit write attempt
├── rto_probe.csv    Phases III/IV only: one row per canary write the RTO probe
│                    dispatched, with dispatch and completion offsets
├── rto_probe.log    Phases III/IV only: JSON per line, flushed as it happens —
│                    every probe failure, connection loss and reconnect
├── events.json      Phases III/IV only: fault timeline, both RTO figures, RPO
└── raw/             the generator's verbatim stdout, per tier
```

`raw/` exists so any parsing dispute is settleable against the original bytes
rather than against a derived file. Three of this project's defects were parser
bugs whose output looked entirely plausible; keeping the input is what made them
findable.

---

## Appendix A. HCP Terraform configuration

Everything below is set in the HCP Terraform workspace, not in this repository.
The repository contains no credentials.

### Environment variables (mark all as sensitive)

```
LINODE_TOKEN          = String
ARM_CLIENT_ID         = String
ARM_CLIENT_SECRET     = String
ARM_TENANT_ID         = String
ARM_SUBSCRIPTION_ID   = String
GOOGLE_CREDENTIALS    = String     # the service-account JSON, as one string
```

### Terraform variables

```hcl
ssh_public_key        = "ssh-rsa AAAAB3N..."
azure_subscription_id = "00000000-0000-0000-0000-000000000000"
gcp_project_id        = "my-gcp-chaos-project"
tailscale_auth_key    = "tskey-auth-xxxxxx-xxxxxx"
database_engine       = "cockroachdb"  # or "postgresql", one deployment per engine

cluster_join_nodes = "crdb-gcp-1,crdb-azure-1,crdb-azure-2,crdb-linode-1,crdb-linode-2"

linode_config = {
  nodes = {
    node1 = { enabled = true, region = "us-east", type = "g6-dedicated-2", hostname = "crdb-linode-1" }
    node2 = { enabled = true, region = "us-west", type = "g6-dedicated-2", hostname = "crdb-linode-2" }
  }
}

azure_config = {
  nodes = {
    node1 = { enabled = true, region = "centralindia", vnet_cidr = "10.3.0.0/16", subnet_cidr = "10.3.1.0/24", vm_size = "Standard_B2ls_v2", hostname = "crdb-azure-1" }
    node2 = { enabled = true, region = "eastasia",     vnet_cidr = "10.4.0.0/16", subnet_cidr = "10.4.1.0/24", vm_size = "Standard_B2ls_v2", hostname = "crdb-azure-2" }
  }
}

gcp_config = {
  nodes = {
    node1 = { enabled = true, region = "us-east1", zone = "us-east1-d", vpc_cidr = "10.5.0.0/16", machine_type = "n2-custom-2-4096", hostname = "crdb-gcp-1" }
  }
}

client_config = {
  nodes = {
    node1 = { enabled = true, region = "us-east1", zone = "us-east1-d", vpc_cidr = "10.6.0.0/16", machine_type = "n2-custom-2-4096", hostname = "crdb-client-1" }
  }
}
```

`database_engine` selects, per `terraform apply`, which engine's bootstrap
script (`bootstrap-cockroachdb.tftpl` or `bootstrap-patroni.tftpl`) every
cluster node runs; the client node's own bootstrap
(`bootstrap-client.tftpl`) is unaffected and always installs both the
`cockroach` client binary and `psql`. Comparing the two engines therefore
takes two separate deployments — apply with `database_engine = "cockroachdb"`,
measure, then edit the variable and re-apply (which replaces every cluster
node) for `"postgresql"` — not one deployment running both at once.

Three things about this configuration are load-bearing rather than incidental:

- **Every node is 2 vCPU / ~4 GiB.** `g6-dedicated-2`, `Standard_B2ls_v2` and
  `n2-custom-2-4096` are all that size, `client_config.node1` included. The
  sizes were normalised deliberately: two phases once differed in memory, and
  because `--cache` is a *fraction* of total memory that gave them block caches
  differing fifteen-fold against a 205 MB working set, inflating apparent
  replication cost by 43% (D9). Changing one node's size reintroduces that.
- **`client_config` provisions the dedicated generator node**, a GCP instance
  that is not a member of either engine's cluster and carries no data. The
  generator and audit clients run from it (`crdblab.topology.CLIENT_NODE`)
  rather than from a node under test, so that neither engine's measurement
  includes the cost of a client sharing a machine with a replica.
- **Every node is `enabled` here.** Each is wrapped in a `count` driven by that
  flag, so a subset can be provisioned by editing a value — but the quorum
  arithmetic assumes five voters. Disabling a cluster node changes the quorum
  floor and invalidates the comparison.

---

## Appendix B. Visualising the plan (optional)

Neither is needed to reproduce the measurement.

```bash
cd terraform
terraform show -json plan.out > plan.json

# ASCII summary
terraform show plan.out | inkdrop

# Interactive graph at http://localhost:9000
docker run --rm -it -p 9000:9000 \
  -v $(pwd)/plan.json:/src/plan.json \
  im2nguyen/rover:latest -planJSONPath=plan.json
```

`plan.json` is gitignored. `plan.out` is not, and it is a build artefact
referencing a specific remote run — worth adding to `.gitignore`.

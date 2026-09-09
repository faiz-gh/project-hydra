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
./run-experiment.sh --smoke        # ~14 min end-to-end harness self-test
./run-experiment.sh --skip-load    # working set already loaded
./run-experiment.sh --no-chaos     # network substrate and benchmark only
./run-experiment.sh --engine postgresql   # the deployed engine is PostgreSQL/Patroni
```

Run with no arguments on a terminal, it asks for the profile, the engine, and
whether to load data and run chaos, instead of taking them as flags.

`--engine {cockroachdb,postgresql}` (default `cockroachdb`) tells the script
**which engine is currently deployed** — it does not deploy or switch anything.
Changing engines is `terraform apply -var="database_engine=..."` (§2), which
replaces every cluster node; pointing `--engine` at an engine that is not
actually running just fails the checks below. One invocation measures one
engine, so the comparison is two invocations (one per deployment) followed by
`crdblab analyze engine-comparison --crdb <run> --pg <run>` by hand (§6).

The flag also changes what the script checks and repairs around the phases,
since several of those steps are CockroachDB-specific: with
`--engine postgresql` the five-live-nodes and `lease_preferences` checks are
replaced by a poll of every member's Patroni REST API on `:8008/health` plus an
assertion that exactly one member answers `:8008/primary`; the row count is
taken with `psql` rather than `cockroach sql --url`; and the post-`dead`
restore sweeps every member with `sudo -n systemctl start patroni` instead of
restarting `chaos.target`, because the node that was actually killed is
resolved at fault time and recorded only in `events.json` (§6, "Chaos"). The
script then restores the primary to the gateway by `patronictl switchover`,
which CockroachDB does not need because `lease_preferences` fails back on its
own and Patroni never does.

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

This no longer needs re-applying by hand.
`terraform/scripts/bootstrap-cockroachdb.tftpl` (renamed from the former
`bootstrap.tftpl` when the PostgreSQL path was added) now writes
`us-east1` first itself, and then asserts the zone configuration actually took,
failing provisioning rather than reporting success with leaseholders placed
arbitrarily. The statement above is kept as the manual repair for a cluster
whose configuration has since been changed.

### Verify a PostgreSQL deployment before loading anything

Do these four checks on `crdb-gcp-1` the moment `terraform apply` finishes. Each
one has failed silently at least once in this project's history, and each is
much cheaper to catch now than after a 70–110 minute load.

```bash
# 1. Exactly one primary, and it is the gateway.
for h in crdb-gcp-1 crdb-linode-1 crdb-linode-2 crdb-azure-1 crdb-azure-2; do
  printf '%-16s %s\n' "$h" "$(curl -s -o /dev/null -w '%{http_code}' http://$h:8008/primary)"
done            # expect 200 on gcp-1 only, 503 elsewhere

# 2. Raft-equivalent synchronous replication is actually in force.
sudo -u postgres psql -c "SHOW synchronous_standby_names"   # expect: ANY 2 (...)
patronictl -c /etc/patroni/config.yml show-config | grep -A2 synchronous
                # expect synchronous_mode: quorum, synchronous_node_count: 2,
                #        synchronous_mode_strict: true

# 3. The cache budgets match CockroachDB's --cache=0.25.
sudo -u postgres psql -c "SHOW shared_buffers"    # expect ~978MB, NOT 128MB

# 4. All five members healthy (503 just means still bootstrapping — wait).
patronictl -c /etc/patroni/config.yml list

# 5. A demoted primary will be able to rejoin after Phase III.
patronictl -c /etc/patroni/config.yml show-config | grep -E 'wal_keep_size|diverged'
                # expect wal_keep_size: 4GB and
                #        remove_data_directory_on_diverged_timelines: true
```

A 503 on `/health` is **not** a fault on a fresh deployment: it is what a member
returns while it is still taking its `pg_basebackup`. `run-experiment.sh` waits
up to 600 s for all five rather than aborting, so give it the same courtesy here.

If a **Linode** node has no Patroni process at all, check
`/var/log/cloud-init-output.log` on it before suspecting the script. Linode caps
`user_data` at 16384 bytes decoded and the template renders to ~26 kB, so it is
sent gzipped (`base64gzip`, ~9.9 kB, 60% of the budget); empty or binary output
in that log means
cloud-init did not decompress it, and the fallback is to trim the template.

None of this is optional paranoia — `synchronous_mode` defaulted to `false`
until 2026-09-09, which meant failover could promote a standby that was never
in the synchronous set, and no artefact would have recorded it.

---

## 3. Load the working set — **required**

`run-experiment.sh` does this for you, reading `--seed` and `--insert-count`
**from the profile it is about to sweep with** so the two cannot drift apart. To
do it by hand, or to understand what the script is doing:

The bootstrap creates the `ycsb` database but no tables. Load the cluster,
from the client node:

```bash
# CockroachDB -- bulk init, straight at the gateway.
ssh ubuntu@crdb-client-1 "cockroach workload init ycsb --drop \
  --seed=42 --insert-count=3750000 \
  'postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable'"
```

**PostgreSQL cannot be loaded this way at all.** `cockroach workload init`
issues `CREATE DATABASE IF NOT EXISTS` as its first statement, which PostgreSQL
rejects with `syntax error at or near "NOT"`, and nothing suppresses it
(`--data-loader NONE` issues it too). `--drop` is separately unusable, and
`--families` defaults to CockroachDB-only DDL. The PostgreSQL working set is
therefore created with `psql` and loaded by running the **generator itself**
insert-only, which is what `run-experiment.sh` does:

```bash
# PostgreSQL/Patroni: create the table, then let the generator write the rows.
ssh ubuntu@crdb-client-1 "psql 'postgresql://root:rootpassword@127.0.0.1:5000/ycsb' \
  -v ON_ERROR_STOP=1 -c '<usertable DDL -- see run-experiment.sh>'"

ssh ubuntu@crdb-client-1 "cockroach workload run ycsb --workload=CUSTOM \
  --insert-freq=1 --read-freq=0 --update-freq=0 \
  --request-distribution=uniform \
  --seed=42 --insert-count=0 --insert-start=0 \
  --concurrency=64 --max-ops=3750000 --duration=0 \
  --display-every=30s 'postgresql://root:rootpassword@127.0.0.1:6432/ycsb?sslmode=disable'"
```

Letting the generator write its own keys is the load-bearing part: YCSB keys
are derived by the generator from the row index
(`user10092439283625390464`), so a hand-written loader would have to
reimplement that derivation, and a keyspace that differs from the one the sweep
addresses is D8 exactly. `--max-ops` overshoots by up to `--concurrency` rows
because operations in flight still complete; those rows have indices at or above
`--insert-count`, so the sweep never addresses them.

Note the two DSNs are different on purpose: the DDL goes through HAProxy
(`:5000`) but the generator must go through **pgbouncer** (`:6432`), which
strips the `allow_unsafe_internals` startup parameter PostgreSQL would
otherwise reject at connect.

Only one engine is ever deployed at a time — switching is a `terraform apply
-var="database_engine=..."` that replaces every cluster node — so load whichever
engine is currently on the testbed. The two arms cannot coexist.

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

Each load writes 3,750,000 rows ≈ 6.15 GB — about **1.5x the nodes' 4 GB of
RAM**, deliberately, so that the sweep measures the storage engines rather than
page-cache residency. Budget accordingly:

| | CockroachDB | PostgreSQL |
|---|---|---|
| load mechanism | `workload init` (bulk) | generator, insert-only @ C=64 |
| load time | minutes | **70-110 min** (~900 rows/s, falling as the index outgrows cache) |
| on-disk per node | ~9.2-12.3 GB (replica + LSM compaction) | ~8.2 GB (table + index + WAL) |

The PostgreSQL load is latency-bound rather than CPU-bound — 64 concurrent
against a ~70 ms synchronous-replication commit — so raising `LOAD_CONCURRENCY`
in `run-experiment.sh` scales it close to linearly (192 puts it near ~23 min,
and `max_connections` is 500). That path has broken three times historically,
so change it deliberately rather than as a matter of course.

Every cluster node needs the disk for a full copy. The GCP `boot_disk` and
Azure `os_disk` set 50 GB explicitly; Linode's `g6-dedicated-2` ships 80 GB.
Do not drop those back to the image default (10 GB on GCP) — the load will fill
it, and on both clouds IOPS is sold by capacity, so an undersized volume also
turns the measurement into a report on a storage tier.

---

## 4. Point the harness at the cluster

Create `.env` in the repository root — one line naming whichever engine's
entrypoint you intend to load and capture against (see `.env.example`):

```bash
# CockroachDB
DB_URI=postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable
# PostgreSQL/Patroni, instead:
# DB_URI=postgresql://root:rootpassword@127.0.0.1:5000/ycsb?sslmode=disable
# PG_PASSWORD=rootpassword
CRDBLAB_RUNS_DIR=runs
```

> **The PostgreSQL working set is loaded differently, and has to be.**
> `cockroach workload init` cannot run against PostgreSQL at all — its first
> statement is `CREATE DATABASE IF NOT EXISTS`, which is CockroachDB syntax —
> so `run-experiment.sh` creates `usertable` with `psql` and then loads the rows
> by running the *generator* insert-only, capped with `--max-ops`. That is not a
> convenience: YCSB keys are derived by the generator from the row index, so a
> hand-written loader would be a second implementation of that derivation, and a
> keyspace that differs from the one the sweep addresses is D8 exactly. The
> generator also reaches PostgreSQL only through pgbouncer, which strips the
> CockroachDB-only startup parameter it sends; both are set up by
> `bootstrap-client.tftpl`.

> **Pre-flight runs on both engines.** The row-match probe (D8's detector) and
> the write-latency floor check were CockroachDB-only until 2026-09-08; they now
> run for PostgreSQL too — `pg_stat_user_tables`'s scan and fetched-row counters
> differenced across each tier, and the same Phase I quorum floor, which applies
> because Patroni waits for two standby acks just as a 3-of-5 Raft quorum does.
> Patroni runs in `synchronous_mode: quorum` with `synchronous_node_count: 2`
> and derives `synchronous_standby_names: ANY 2 (...)` itself — do not set that
> parameter by hand, and use `quorum` rather than `true`, which would name
> specific standbys and could put the commit path on the 211-215 ms Azure pair. A seed/insert-count mismatch is
> therefore caught on both arms of the comparison rather than only one.

> **PostgreSQL needs a password everywhere; CockroachDB needs none.** The
> CockroachDB nodes run `--insecure` and accept `root` with no credential.
> Patroni bootstraps `pg_hba` as `host all all 0.0.0.0/0 md5`, so every TCP
> connection the harness makes — the generator, the RPO audit writer, the RTO
> probe agent, the DDL creating their tables — is refused without one.
> `DB_URI` carries it for loading and `capture` (`run-experiment.sh` refuses to
> start without it); the measured phases build their own DSNs and read
> `PG_PASSWORD`, which defaults to the `rootpassword` that
> `terraform/scripts/bootstrap-patroni.tftpl` creates. Change the template and
> you must set `PG_PASSWORD` to match.

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
# PostgreSQL/Patroni instead:
#   crdblab --engine postgresql net probe --profile smoke
#   crdblab --engine postgresql bench     --profile smoke
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
# PostgreSQL/Patroni instead: crdblab --engine postgresql net probe --profile ...
```

Run it once per deployment, with the `--engine` of whichever one is up. The
measurement itself does not depend on the engine — ping does not care what is
listening on 26257 — but the machines do: switching engines replaces every
cluster node, so a matrix taken against the CockroachDB deployment describes a
different set of hosts than one taken against the PostgreSQL deployment.
Recording the engine is what lets the two matrices sit in `figures/` under
distinct names (§9) instead of one silently replacing the other. Phase I runs
recorded before this flag existed carry no engine and read back as
`cockroachdb`, which is what all of them were.

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
argparse rather than silently ignored. `net probe` (§6, Phase I) and
`chaos run` take it the same way, and all three record it in the run manifest —
which is what puts the engine in each figure's filename (§9). `validate`,
`analyze` and `report figures` do not take it: they are given run ids, and every
run already says which engine produced it.

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
it; `dead` kills the process outright.

**A `recover` run is therefore longer than its profile's `duration_s`, and has
to be.** `min_post_fault_s` is counted from `inject_at_s + 45 s` rather than
from the fault, and `p4_chaos.generator_duration_s` extends the run accordingly
— the extension is recorded as a manifest note, so the run states its own
length. Before 2026-09-09 it was counted from the fault, which left the `smoke`
profile unable to measure anything: a 45 s run with the fault at 15 s had both
instruments still inside the outage when observation ended, and both correctly
reported the RTO as UNMEASURED rather than as zero. The next run, at the
corrected length, measured that outage at **37.6 s** after the fault.

Note what the bound is *not*. **Recovery does not wait for the partition to
lift.** The fault isolates one node; the other four keep quorum and elect a new
primary, so writes resume at failover — measured at 64.1 s on
`20260909T040914Z_p4-chaos-recover`, 7.4 s *before* that run's partition healed
at 71.5 s. The heal delay is used because it is a conservative upper bound on
that failover, and because Phase IV needs the demoted node back on the network
in time to finish rewinding and become a switchover candidate. So a `recover`
RTO is a measurement of Patroni's failover, not of how long the network was
down — do not describe it as the latter. The target is the **primary** — the
node genuinely coordinating writes, not a peripheral member — because failing
a node outside the write path would be a far weaker test.

For CockroachDB, `profiles/*.yaml` name the target explicitly
(`chaos.target: gcp-1`, the node `lease_preferences` pins the leaseholder to;
`preflight.check_leaseholder_placement` asserts that placement before the
fault fires). For PostgreSQL/Patroni the target is the same node, by design —
where the write path is led from is a property of the deployment rather than of
the engine, so letting the two arms differ would put cloud geography into the
comparison. `bootstrap-patroni.tftpl` pins the primary onto `gcp-1` at
bootstrap and `preflight.check_patroni_primary_placement` restores it by
`patronictl switchover` before each measured phase, because Patroni never fails
back on its own the way `lease_preferences` does.

It is still never *assumed*.
`crdblab/phases/p4_chaos.py::resolve_patroni_primary` queries every node's
Patroni REST API (`:8008/primary`) immediately before scheduling the fault
and targets whichever one actually answers as primary, overriding
`chaos.target` if it names a different node. The pin is what makes the answer
predictable; the live read is what makes it true. The manifest and
`events.json` record whichever node was actually faulted.

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

Four things to know before quoting a number from it.

- **Check `coverage_truncated` FIRST, before reading any other key.** The probe
  answers "was there an outage" by looking for a gap in its own stream of writes,
  so a probe that stops writing produces no gap and reports a clean run. On
  2026-09-09 a `recover` fault black-holed the connections its workers already
  held; a server-side `statement_timeout` cannot arrive when packets cannot, so
  the workers blocked in `recv()` and neither completed nor failed. The recorded
  artefact was 515 attempts, **515 `ok`, zero timeouts and zero conn_errors**,
  spanning 20.3 s of a 45 s run and ending 3.6 s after the fault — and the run
  reported "no interruption in served writes was detectable" for an outage of
  roughly 70 seconds. The probe now records how much of the run it watched and
  returns `rto_s: None` with an UNMEASURED claim where its coverage ends early,
  and `crdblab analyze resilience` prints an
  `*** AN INSTRUMENT STOPPED OBSERVING ***` banner. The signature to recognise by
  eye, in `events.json` → `probe`: every attempt `ok`, no failures of any kind,
  and `span_s` well short of the run's length.
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
> cache against the 205 MB working set of the day — and the resulting difference
> would conflate replication cost with cache residency, which is exactly the
> defect D9 records. **This matters more now, not less:** the working set is
> 6.15 GB, ~6.3x either engine's cache and ~1.5x node RAM, so cache size governs
> the miss rate directly rather than merely deciding how comfortably everything
> fits. The PostgreSQL counterpart is `shared_buffers`, which
> `bootstrap-patroni.tftpl` derives as the same quarter of measured RAM
> (~978 MB, against `--cache=0.25`'s 978.3 MB); it was unset until 2026-09-09
> and PostgreSQL ran on the 128 MB default, an eightfold asymmetry in
> CockroachDB's favour. Verify it with `SHOW shared_buffers` after provisioning. Restarting one node with different flags silently reintroduces
> it.

For PostgreSQL/Patroni, `dead` kills `patroni` and `postgres` on the target
(`terraform/scripts/bootstrap-patroni.tftpl` enables and starts `patroni` as a
systemd unit, unlike CockroachDB). Whether the unit's own restart policy
brings it back on its own, or a manual `systemctl start patroni` on the target
is needed, has not been characterised here as precisely as the CockroachDB
path above — check `systemctl status patroni` on the target and Patroni's own
cluster state (`patronictl list`) before trusting the node is fully rejoined,
rather than assuming the CockroachDB timeline applies.

> **After a `recover` fault against the PostgreSQL primary, check that the
> demoted node actually rejoins — historically it could not.** The partition
> leaves the old primary holding WAL the promoted node's timeline never saw, so
> it must be rewound before it can stream again. On 2026-09-09 `pg_rewind` failed
> because `gcp-1` had already recycled the WAL segment it needed to read back
> (`could not open file ".../pg_wal/00000001000000000000000C"`), the node parked
> at `start failed` and stayed there for hours, and Phase IV aborted with
> Patroni's "no good candidates have been found" — on a cluster whose other four
> members were perfectly healthy. This happens after *every* Phase III run
> against the primary, not occasionally.
>
> `bootstrap-patroni.tftpl` now sets `wal_keep_size: '4GB'` so the rewind can
> normally succeed, and `remove_data_directory_on_diverged_timelines: true` so a
> failed rewind falls back to a fresh basebackup instead of a dead node. Both
> live in `bootstrap.dcs`, which Patroni reads **only at first bootstrap**: on a
> cluster that is already running they do nothing until you apply them with
> `patronictl -c /etc/patroni/config.yml edit-config`. Confirm with
> `patronictl show-config` rather than by reading the template.
>
> What you should see after Phase III: the demoted node goes to `Replica` and
> then back to `Quorum Standby / streaming` on its own, within a basebackup's
> time — seconds at smoke scale, minutes at thesis scale, where the clone is
> ~6 GB across a WAN link. `check_patroni_primary_placement` waits for it
> (`chaos.leaseholder_settle_s`, default 300 s) before attempting the switchover
> back to the gateway, so a slow re-clone delays Phase IV rather than aborting
> it. A node still at `start failed` after that window is the failure above, and
> the remedy is to stop Patroni there, delete its data directory and let it
> re-clone.
>
> **`Replica / running` is not the same state as `Quorum Standby / streaming`,
> and only the second one can be handed leadership.** On 2026-09-09 the fix
> above worked as far as it went — `gcp-1` came back up rather than parking at
> `start failed` — and Phase IV still failed, because the node was `Role:
> Replica` on **timeline 1** with `Receive LSN: unknown` while the leader and
> the other three members were streaming on **timeline 2**. It was up, in
> recovery, and reporting no lag, so Patroni's `/replica` endpoint answered 200
> and the candidate wait ended after one reading; the switchover it then asked
> for failed with `503, Switchover failed`. The lesson is that `/replica`
> reports lag against a position an unattached member cannot advance, so a
> member attached to nothing looks perfectly healthy through it. The wait now
> reads the member's own `/patroni` document and requires
> `replication_state: streaming` on the leader's timeline, which is what
> `patronictl list` is showing you when it prints `Quorum Standby / streaming`.
> If you are checking by hand, read that column and the `TL` column — not the
> `State` column alone.
>
> If you need to unstick one by hand:
>
> ```bash
> ssh ubuntu@crdb-gcp-1 'sudo -n systemctl stop patroni \
>   && sudo -n rm -rf /var/lib/postgresql/16/data \
>   && sudo -n systemctl start patroni'
> # then watch it re-clone, and switch the primary back:
> patronictl -c /etc/patroni/config.yml list
> patronictl -c /etc/patroni/config.yml switchover \
>   --leader <current-leader-hostname> --candidate crdb-gcp-1 --force
> ```
>
> Note the hostnames: `patronictl` addresses members by their Patroni `name:`,
> which is the full hostname (`crdb-gcp-1`), never the short `Node.name`
> (`gcp-1`) that `profiles/*.yaml` uses for `chaos.target`.

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
measures one engine per invocation (§ "The short version"), so run it once per
deployment — `./run-experiment.sh` for CockroachDB, `./run-experiment.sh
--engine postgresql` after the redeploy — and then invoke `engine-comparison`
by hand with both run ids.

`engine-comparison` prints the same-concurrency delta under a **NOT A RESULT**
banner. That is intentional: refuting the intuitive comparison is more useful
than omitting it.

---

## 9. Figures

```bash
.venv/bin/crdblab report figures                    # newest run of each phase
```

`report figures` writes up to five figures (network matrix, throughput sweep,
latency by operation, and one resilience timeline per fault class) into
`figures/`, each as a PNG at ≥4K and an SVG beside it. Filenames carry their
own provenance —
`fig2_throughput_sweep_cockroachdb_thesis_20260908T053558Z_bench_cluster.png` —
so a smoke render, a thesis render and a PostgreSQL render coexist in one
directory instead of overwriting each other. Phase I's matrix is named the same
way: ping does not care which database is listening, but switching engines
replaces every cluster node, so the matrix still belongs to one deployment —
`net probe` records `--engine` in its manifest for that reason.

These names changed on 2026-09-08 (they were previously `fig2_throughput_sweep.png`
and so on, with a `_postgresql` suffix as the only variation), so a caption
citing a figure by filename needs re-checking against what `report figures`
now writes. The figure *numbers* did not move: fig2 is still the throughput
sweep, fig5 still the `dead` timeline, fig6 still `recover`.

The throughput-sweep and
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

All three share `seed: 42`. `thesis` and `thesis-extended` share
`insert_count: 3750000`, matching §3's load command — they are meant to differ
in the concurrency ladder, not in the data, or an extended sweep would not be
comparable with the sweep it extends.

**`smoke` deliberately stays at `insert_count: 125000`.** It is a harness
self-test whose value is being fast, and a 70-110 minute load would defeat that.
The consequence is that smoke no longer exercises the disk-bound path at all —
it verifies wiring, both chaos modes and the switchover repair, not storage
behaviour. It reloads its own count, and its keyspace is a subset of the thesis
one, so alternating between them is safe; but the table must be reloaded when
moving from smoke to a thesis sweep, because `run-experiment.sh` asserts the row
count is at least the profile's and will refuse to start otherwise.

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

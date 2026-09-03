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

It checks the workstation and the testbed, loads the working set, runs all four
phases in order, restores the node killed by the chaos run, validates every run,
prints the analysis and renders the figures. It stops at the first failure rather
than continuing with a testbed that is not fit to be measured.

```bash
./run-experiment.sh --smoke        # ~8 min end-to-end harness self-test
./run-experiment.sh --skip-load    # working set already loaded
./run-experiment.sh --no-chaos     # phases I-III only
```

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
| An SSH key matching `ssh_public_key` | the harness runs the generator *on* the nodes |

The workstation never touches the database over the WAN. It orchestrates over
SSH and writes CSV; the load generator runs on the node. This is deliberate — a
client-side round trip from the workstation would dominate and mask the
consensus latency being measured (§4.4).

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
.venv/bin/python -m pytest tests/ -q      # expect: 99 passed
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

This creates six machines — five cluster nodes and one unreplicated baseline —
each 2 vCPU / ~3.8 GiB, joins them over Tailscale, and bootstraps CockroachDB
through cloud-init.

**Wait for cloud-init to finish before continuing.** `terraform apply` returns as
soon as the provider APIs acknowledge the instances; the bootstrap continues on
the machines for another two to three minutes. Watch the primary:

```bash
ssh root@crdb-linode-1 'tail -f /var/log/cloud-init-output.log'
```

You are waiting for the zone-configuration step to report success. It logs
`Only 1/5 nodes live` → … → `All 5 nodes are live`, then applies
`num_replicas = 5` and `lease_preferences`. **The bootstrap deliberately exits
non-zero if the lease preference does not apply**, rather than reporting success
on a partial configuration — an earlier version did the latter and left the
leaseholder in `centralindia`, costing 12.3x throughput while the cluster
reported full health (§3.3).

Verify placement took:

```bash
ssh root@crdb-linode-1 "cockroach sql --insecure --host=crdb-linode-1:26257 \
  -e 'SHOW ZONE CONFIGURATION FROM DATABASE ycsb;'"
```

`lease_preferences` must be `[[+region=us-east], [+region=us-east1], [+region=us-west]]`
and `num_replicas` must be `5`. An empty `lease_preferences` means the bootstrap
raced; re-run `terraform apply` or re-apply the zone config by hand.

---

## 3. Load the working set — **required**

`run-experiment.sh` does this for you, reading `--seed` and `--insert-count`
**from the profile it is about to sweep with** so the two cannot drift apart. To
do it by hand, or to understand what the script is doing:

The bootstrap creates the `ycsb` database but no tables. Load both tiers:

```bash
# Cluster (via the gateway)
ssh root@crdb-linode-1 "cockroach workload init ycsb --drop \
  --seed=42 --insert-count=125000 \
  'postgresql://root@crdb-linode-1:26257/ycsb?sslmode=disable'"

# Unreplicated baseline
ssh ubuntu@crdb-local-1 "cockroach workload init ycsb --drop \
  --seed=42 --insert-count=125000 \
  'postgresql://root@crdb-local-1:26257/ycsb?sslmode=disable'"
```

> **`--seed` and `--insert-count` must equal the values in the profile you are
> about to sweep with.** This is the single most dangerous parameter in the
> project. The generator's default seed changes on *every invocation*, so a
> mismatch silently addresses a different keyspace than the one loaded: every
> read and update matches zero rows, returns in ~3 ms, and reports roughly
> twenty times the throughput at a twenty-fifth of the latency. It does not
> error. It looks like the best result the testbed has ever produced
> (`docs/defects.md`, D8).

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

Create `.env` in the repository root:

```bash
DB_URI=postgresql://root@crdb-linode-1:26257/ycsb?sslmode=disable
CRDBLAB_RUNS_DIR=runs
```

Those are the only two variables `crdblab` reads. An older `.env.example` also
carried `HCP_TOKEN`, `HCP_ORG` and `HCP_WORKSPACE`; nothing in this harness uses
them.

**Two properties of `DB_URI` are load-bearing, and `run-experiment.sh` asserts
both before it will start.**

- **It names exactly one host.** A multi-host URI —
  `root@crdb-linode-1,crdb-linode-2,…` — puts the wide-area network back on the
  client's path, which is exactly the latency that running the generator *on the
  gateway* exists to exclude. It would inflate every measured latency and mask
  the consensus overhead being measured.
- **It names the `ycsb` database, not `defaultdb`.** `cockroach workload init
  ycsb` refuses any other database name, so a URI pointing elsewhere cannot have
  a loaded working set behind it — every operation would match zero rows, which
  fails *flatteringly* (§3).

---

## 5. Smoke test the harness against the live testbed

Before committing an hour to the full sweep, confirm the generator's output
format still parses and the pre-flight assertions pass:

```bash
.venv/bin/crdblab capture --node linode-1 --pty --duration 15
```

This pins the column layout against the deployed CockroachDB version. If it
raises `WorkloadParseError`, the generator's output format has changed and the
parser needs updating before any measurement is trustworthy — that is the
intended behaviour, not a bug (`docs/defects.md`, D6).

Then a two-tier end-to-end pass, about four minutes:

```bash
.venv/bin/crdblab net probe    --profile smoke
.venv/bin/crdblab bench single --profile smoke
.venv/bin/crdblab bench cluster --profile smoke
```

`bench` ends with `all N pre-flight checks passed`; `net probe` prints a `[PASS]`
line per assertion and reports the derived quorum floor. Any `[FAIL]`, or a
non-zero exit, means the testbed is not fit to measure — fix it before sweeping
rather than re-running and hoping.

---

## 6. The four measurement phases

Run in this order. Each phase consumes artefacts from the one before, so the
ordering is load-bearing rather than conventional.

### Phase I — network substrate (~3 min)

```bash
.venv/bin/crdblab net probe --profile thesis-extended
```

Produces the all-pairs RTT matrix, MTU, clock offsets and leaseholder placement,
and derives the **quorum floor** — the round trip to the second-fastest
follower, which bounds every committed write. Phases II–IV read that floor from
the most recent Phase I run to assert their write latencies are physically
achievable.

Expect a floor near 67 ms and the ordering
`gcp-1 < linode-2 << azure-1 < azure-2`.

**The ordering is what matters; the absolute values are not stable.** Across
three deployments of this topology `gcp-1` has measured 18.3–24.7 ms and
`azure-1` 180.1–198.2 ms — up to 23% apart — while `linode-2`, which sets the
quorum floor, has stayed within 67.1 ± 4 ms. Every downstream assertion and the
quorum geometry depend on the *rank* of the links, not their magnitudes, so a
deployment whose ordering is unchanged is comparable even when its latencies are
not identical. A deployment whose ordering has changed is a different experiment
and its Phase II/III runs must not be pooled with earlier ones.

### Phase II — unreplicated baseline (~26 min)

```bash
.venv/bin/crdblab bench single --profile thesis-extended
```

### Phase III — five-node cluster (~26 min)

```bash
.venv/bin/crdblab bench cluster --profile thesis-extended
```

Both sweep C = 1, 2, 5, 10, 50, 100, 200 with three repetitions each, in an
order shuffled from the **profile seed** rather than the wall clock, so the
realised order is reproducible and is recorded in the manifest. Randomisation is
not cosmetic: it is what allows drift across a sweep to be separated from a
difference between tiers, and §7.3's elimination of cumulative degradation is
only possible because of it.

Run both phases with the **same profile**. The analysis layer refuses to compare
two runs whose generator, mix, distribution, seed, insert count, duration or
warmup differ. Concurrency is deliberately *not* one of those keys.

> Sweeping down to C = 1 is what makes the primary result possible. At one
> worker a closed-loop generator has exactly one operation outstanding, so the
> measured median contains no queueing at all and the two phases can be compared
> without either being confounded by load (§6.4.1).

### Phase IV — fault injection (~4 min each)

```bash
.venv/bin/crdblab chaos run --mode recover --profile thesis-extended
.venv/bin/crdblab chaos run --mode dead    --profile thesis-extended
```

`recover` severs the overlay network to `crdb-linode-2` for 45 s and restores it;
`dead` kills the process outright. The target is a fast-triangle member that is
genuinely coordinating writes — failing a node outside the write path would be a
far weaker test.

**A `dead` run leaves the node down. The harness does not restore it** — the
fault is real, and restarting is a deliberate operator action. CockroachDB is
launched by cloud-init with `--background` rather than as a systemd unit, so
there is no service to start and a reboot will not bring it back either. Replay
the start command:

```bash
ssh root@crdb-linode-2 'TS_IP=$(tailscale ip -4); cockroach start --insecure \
  --store=/var/lib/cockroach \
  --listen-addr=$TS_IP:26257 --advertise-addr=$TS_IP:26257 \
  --locality=cloud=linode,region=us-west \
  --cache=0.25 --max-sql-memory=0.25 \
  --join=crdb-linode-1:26257 --background'

sleep 20
ssh root@crdb-linode-1 "cockroach node status --insecure --host=crdb-linode-1:26257"
```

All five nodes must show `is_available = true` again before the next
measurement.

> `--cache=0.25 --max-sql-memory=0.25` are **not optional here**. Every node in
> the comparison must be started with identical memory flags. Omitting them
> takes CockroachDB's 128 MiB default — a roughly fifteen-fold smaller block
> cache against a 205 MB working set — and the resulting Phase II/III difference
> would conflate replication cost with cache residency, which is exactly the
> defect D9 records. Restarting one node with different flags silently
> reintroduces it.

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
.venv/bin/crdblab analyze steady-state <phase-II-run-id>
.venv/bin/crdblab analyze steady-state <phase-III-run-id>

# Replication cost, all three framings
.venv/bin/crdblab analyze raft-overhead \
  --baseline <phase-II-run-id> --cluster <phase-III-run-id> \
  --accept-hardware-difference

# RTO and RPO with their measurement limits
.venv/bin/crdblab analyze resilience <phase-IV-recover-run-id>
.venv/bin/crdblab analyze resilience <phase-IV-dead-run-id>
```

Run ids are the directory names under `runs/`. Add `--json` to any of these for
machine-readable output.

`--accept-hardware-difference` is required in this topology and is not a
formality. The comparison refuses outright when the two runs were measured on
different CPU models or memory sizes, because a throughput difference between
unlike machines is not attributable to replication. Here the baseline is an Intel
Xeon and the cluster gateway an AMD EPYC — a known, documented limitation of the
study (§7.3) rather than a mistake — so the difference is *accepted explicitly*
and recorded as a warning in the output rather than being silently ignored. Drop
the flag and read the refusal at least once; it names exactly what differs.

`raft-overhead` prints the same-concurrency delta under a **NOT A RESULT**
banner. That is intentional: refuting the intuitive comparison is more useful
than omitting it, and the table is a Chapter 5 exhibit rather than a Chapter 6
one.

---

## 9. Figures and the dissertation

```bash
.venv/bin/crdblab report figures                    # newest run of each phase
.venv/bin/python tools/make_docx.py                 # → Faiz_Ghanchi_Dissertation_crdblab.docx
```

`report figures` writes five PNGs at ≥4K with a vector PDF beside each, into
`figures/`. To pin specific runs rather than the newest:

```bash
.venv/bin/crdblab report figures \
  --network <p1-run> --baseline <p2-run> --cluster <p3-run> --chaos <p4-run>
```

Every figure resolves its inputs through the analysis loader, so a run without a
manifest, or one failing validation or pre-flight, cannot reach a figure at all.

`tools/make_docx.py` renders `docs/dissertation.md` — the local copy of the
dissertation — into a Word document with the five figures as Appendix B. Edit
the markdown, re-run the script.

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

**Phase II fails to connect to `crdb-local-1` by name.** MagicDNS resolves the
node's own hostname to an interface CockroachDB is not bound to. The bootstrap
pins the overlay address into `/etc/hosts`; if that step was skipped, add it:

```bash
ssh ubuntu@crdb-local-1 'TS_IP=$(tailscale ip -4); \
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
compare only runs the harness agrees are comparable — `raft-overhead` refuses
outright if the two runs' server flags, workload parameters, versions or
hardware differ.

---

## What a run directory contains

```
runs/20260902T195644Z_p3_cluster/
├── manifest.json    git revision, resolved profile, topology, generator
│                    command, server start command, host CPU/memory,
│                    realised tier order, clock epoch
├── metrics.csv      long format: one row per (interval, operation type)
├── preflight.json   every assertion with its observed value
├── audit.csv        Phase IV only: one row per audit write attempt
├── events.json      Phase IV only: fault timeline
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

cluster_join_nodes = "crdb-linode-1,crdb-linode-2,crdb-azure-1,crdb-azure-2,crdb-gcp-1"

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

local_config = {
  nodes = {
    node1 = { enabled = true, region = "us-east1", zone = "us-east1-d", vpc_cidr = "10.5.0.0/16", machine_type = "n2-custom-2-4096", hostname = "crdb-local-1" }
  }
}
```

Three things about this configuration are load-bearing rather than incidental:

- **Every node is 2 vCPU / ~4 GiB.** `g6-dedicated-2`, `Standard_B2ls_v2` and
  `n2-custom-2-4096` are all that size. The sizes were normalised deliberately:
  the baseline and the cluster once differed in memory, and because `--cache` is
  a *fraction* of total memory that gave them block caches differing fifteen-fold
  against a 205 MB working set, inflating apparent replication cost by 43%
  (`docs/defects.md`, D9). Changing one node's size reintroduces that.
- **`local_config` provisions the Phase II baseline, and it is a GCP instance**
  despite its `region=self-hosted` locality label. That label says it is an
  isolated single-node server rather than a cluster member; it does not describe
  where it runs. This is why the two phases differ in CPU model — Intel Xeon on
  GCP against AMD EPYC on Linode — which is a stated limitation of the study
  (§7.3 of the dissertation).
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

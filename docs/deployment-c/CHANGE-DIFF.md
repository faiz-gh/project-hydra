# Change diff: gateway shift and RTO probe

Written for someone with no other context on this conversation, updating an
existing dissertation. **Rule applied throughout, matching
`docs/dissertation-verification.md`: no value here is estimated, inferred, or
recalled from memory of the change being made.** Every diff is quoted verbatim
from `git diff` against the working tree; every measured number is read from a
retained run artefact or from a live query issued in this session, and is
labelled with which.

---

## 0. Provenance of this document

| | |
|---|---|
| Repository | `project-hydra` |
| `git rev-parse HEAD` | `f4e8357c79a5ac7a69f21cf0b607ef79f3c48eec` |
| Working tree state | 22 files modified/untracked, all uncommitted (see § 6) |
| Session in which the changes were made | this conversation, 2026-09-05 to 2026-09-06 |

**The code that produced every Deployment C run is *not* the code named in that
run's own manifest.** `manifest.json`'s `git_revision` field is captured with
`git rev-parse HEAD` (`crdblab/core/recorder.py`), which names the last
*commit*. Every change described in this document — the gateway shift in
`crdblab/topology.py`, the entire `crdblab/core/rto_probe.py` module, and
everything that wires it in — is uncommitted working-tree state layered on top
of `f4e8357c`. Every Deployment C manifest therefore records `f4e8357c` while
having actually been produced by the tree described below. This is exactly the
situation `docs/dissertation-verification.md` Part 0 documents for the
project's own prior history ("the harness itself was uncommitted working-tree
state at measurement time") — it recurred here, for the same reason: the
change was validated by running it, not by committing it first.

---

## 1. Gateway shift — files changed, verbatim before/after

### `crdblab/topology.py` — the load-bearing change

Before (the entry in `DEFAULT_TOPOLOGY.nodes`):

```python
        Node("linode-1", "crdb-linode-1", "root", "linode", "us-east",
             "cloud=linode,region=us-east", gateway=True),
```

After:

```python
        Node("linode-1", "crdb-linode-1", "root", "linode", "us-east",
             "cloud=linode,region=us-east"),
```

(the `gateway=True` flag removed from `linode-1`), and:

Before (`gcp-1`'s entry):

```python
        Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1",
             "cloud=gcp,region=us-east1"),
```

After:

```python
        Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1",
             "cloud=gcp,region=us-east1", gateway=True),
```

Nothing else in the `Node` tuple for either node changed — same `host`, same
`user`, same `provider`, same `region`, same `locality` string. This is the only
functional edit `topology.py` needed; the rest of that file's diff is doc
comments explaining the move (see `crdblab/topology.py:88-114` in the working
tree).

### `crdblab/cli.py` — the one other functional reference to a hardcoded gateway

```diff
-    cap.add_argument("--node", default="linode-1", help="node on which to run the generator")
+    cap.add_argument(
+        "--node",
+        default="gcp-1",
+        help="node on which to run the generator; defaults to the gateway, which "
+        "is where every measured workload runs (crdblab.topology)",
+    )
```

This is `crdblab capture`'s node argument. Every other command (`bench`,
`chaos`, `net probe`, `probe rto`) resolves the gateway from
`settings.topology.gateway` and needed no code change at all — that indirection
is the reason the shift was a one-`Node` edit rather than a search-and-replace.

### `run-experiment.sh` — three additions, no removals of substance

1. Topology resolution now also captures the gateway's region, and asserts
   `.env`'s `DB_URI` names the same host as the resolved gateway:

   ```diff
   -read -r GW_USER GW_HOST BL_USER BL_HOST < <("$PY" - <<'PYEOF'
   +read -r GW_USER GW_HOST GW_REGION BL_USER BL_HOST < <("$PY" - <<'PYEOF'
    from crdblab.topology import DEFAULT_TOPOLOGY as t, BASELINE_NODE as b
    g = t.gateway
   -print(g.user, g.host, b.user, b.host)
   +print(g.user, g.host, g.region, b.user, b.host)
    PYEOF
    ) || die "could not resolve topology from crdblab.topology"
   -ok "gateway $GW_USER@$GW_HOST   baseline $BL_USER@$BL_HOST"
   +ok "gateway $GW_USER@$GW_HOST ($GW_REGION)   baseline $BL_USER@$BL_HOST"
   +
   +DB_HOST="$(printf '%s' "$DB_URI" | sed -E 's|^[a-z]+://||; s|^[^@/]*@||; s|[:/?].*$||')"
   +[ "$DB_HOST" = "$GW_HOST" ] || die "DB_URI names '$DB_HOST' but the gateway is '$GW_HOST'. ..."
   +ok "DB_URI names the gateway"
   ```

2. The lease-preference check now requires the gateway's own region to head the
   list, distinguishing "empty" (the pre-existing D7 failure mode) from "present
   but ordered for a different gateway" (new):

   ```diff
    case "$LEASE" in
   -  *"[[+region="*) ok "lease preferences applied  ${LEASE#*= }" ;;
   +  *"[[+region=$GW_REGION]"*)
   +    ok "lease preferences applied, headed by $GW_REGION  ${LEASE#*= }" ;;
   +  *"[[+region="*)
   +    die "lease_preferences is applied but does not name the gateway's region first. ..." ;;
      *) die "lease_preferences is empty or unreadable on database 'ycsb'. ..." ;;
    esac
   ```

3. `--accept-hardware-difference` is no longer passed to `raft-overhead`, and its
   absence is now enforced by a `die` on refusal rather than by omission alone:

   ```diff
    "$CRDBLAB" analyze raft-overhead --baseline "$P2" --cluster "$P3" \
   -    --accept-hardware-difference
   +  || die "raft-overhead refused to compare Phase II with Phase III. ..."
   ```

   Whether this actually holds against real data is confirmed in
   `HARDWARE-COMPARABILITY.md`, not asserted here.

Also added, and load-bearing for run time rather than for correctness: the
node-restart step now detaches the remote CockroachDB process's stdio
(`--background </dev/null >/dev/null 2>&1`) so the SSH session returns
immediately instead of blocking until connection timeout — see `NEW-DEFECTS.md`
D13 for why this mattered (a ~50-minute stall) and how it was found.

### `terraform/scripts/bootstrap.tftpl` — one line, applied by the user

**This edit was made by the user directly, following the exact change
described to them, not by any tool call in this conversation.** It is recorded
here because it is now part of the working tree that produced every Deployment
C run and its content is load-bearing for that data's validity.

```diff
         # 2. Pin leaseholders (write coordinators) to the low-latency US triangle
         cockroach sql --insecure --host=$TS_IP:26257 \
-            -e "ALTER RANGE default CONFIGURE ZONE USING lease_preferences = '[[+region=us-east], [+region=us-east1], [+region=us-west]]';"
+            -e "ALTER RANGE default CONFIGURE ZONE USING lease_preferences = '[[+region=us-east1], [+region=us-east], [+region=us-west]]';"
```

Same three regions (`us-east`, `us-east1`, `us-west`), same `num_replicas = 5`
elsewhere in the file (unchanged) — only the **order** changed, putting
`us-east1` (the new gateway's region) first. See `TOPOLOGY-DELTA.md` § 2 for
why the order and not the membership was the load-bearing part, and for the
live confirmation that it applied.

### `.env` — changed by the user; I could not read it

The user reported setting
`DB_URI=postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable`. This
conversation's sandbox permissions refuse every command that reads or diffs
`.env` directly, in this document-writing session as in the original
change-making one, so **I cannot quote its contents and have not**. What I did
verify, live, in this session, without reading the file's contents — only the
host `crdblab.config.Settings` resolves from it:

```
DB_URI host (only the host, not the full credential string): crdb-gcp-1
gateway: crdb-gcp-1
MATCH
```

### `.env.example` — modified; not by any tool call in this conversation

`git status --short` shows `M .env.example`. I could not read or diff this
file either, for the same sandbox reason. I made no edit to it via any tool
call in either the change-making session or this one; the modification, if
intentional, is the user's.

### `profiles/*.yaml` — no lines changed for the gateway shift

`git diff --stat profiles/` shows only *additions* (the probe's `probe_*` keys;
see § 2). `chaos.target` is unchanged at `linode-2` in `thesis.yaml` and
`thesis-extended.yaml` — the chaos target was never the gateway in either
direction of the move, and `p4_chaos.run()` refuses at run time if it ever is.

### Documentation-only files touched (prose, no executable behaviour)

`README.md`, `instructions.md` — nodes named in example commands, the
`--accept-hardware-difference` paragraph, the Appendix A bootstrap notes. Not
reproduced verbatim here since they carry no numeric claim; every numeric claim
they now make is independently checked against a live artefact in
`TOPOLOGY-DELTA.md` and `HARDWARE-COMPARABILITY.md` rather than trusted from the
prose.

### What did **not** change: the Terraform-managed cluster inventory itself

```
$ git diff --stat -- terraform/terraform.tfvars.example terraform/variables.tf terraform/modules/
(no output — zero changes)
```

`cluster_join_nodes` and every node's `hostname` / `region` / `machine_type` in
`terraform.tfvars.example` are byte-identical to before this change. The five
cluster nodes and the baseline node are the same six machines, at the same
providers, in the same regions, at the same instance sizes, as before. See
§ 4 for the precise scope statement this bears on.

---

## 2. New files for the RTO probe

### Created

| File | What it is |
|---|---|
| `crdblab/core/rto_probe.py` | The probe: `RtoProbe` (the client), `ProbeAttempt` (one observation), `measure_rto` / `summarise` / `tail_attribution` (the analysis, importable independent of a live probe so `crdblab.analysis.resilience` can re-derive from a CSV) |
| `tests/test_rto_probe.py` | Unit and property tests for the probe and its analysis, including two regressions pinned to defects found against live Deployment C data (`NEW-DEFECTS.md` D14, D15) |
| `tests/test_topology.py` | Regression tests for the gateway shift (not the probe): asserts the gateway is `gcp-1`, shares a provider with the baseline, and sits inside the lease-preference region set |

### Modified to wire the probe in

| File | What changed |
|---|---|
| `crdblab/core/recorder.py` | Added `PROBE_COLUMNS`, `PROBE_OUTCOMES`, `utcnow_us()`; added `RunDirectory.probe_csv` / `.probe_log` properties |
| `crdblab/config.py` | Added `probe_enabled`, `probe_interval_s`, `probe_workers`, `probe_statement_timeout_ms`, `probe_connect_timeout_s`, `probe_table` to `ChaosSpec` |
| `crdblab/phases/p4_chaos.py` | `run()` starts an `RtoProbe` alongside the existing `AuditWriter`, writes `rto_probe.csv`, and records a `probe` summary in `events.json` |
| `crdblab/analysis/resilience.py` | Added `probe_availability()`, re-deriving from `rto_probe.csv`; wired into `summarise()` as a `probe_rto` key alongside the existing `availability_rto` |
| `crdblab/analysis/validation.py` | Added `validate_probe()` and its constituent checks (`check_probe_ordering`, `check_probe_outcomes`, `check_probe_sequence`) |
| `crdblab/analysis/loader.py` | `load_run()` now also gates on `rto_probe.csv` passing `validate_probe()`, refusing a run whose probe log is corrupt the same way it already refused one whose `metrics.csv` was |
| `crdblab/cli.py` | Added the `probe rto` subcommand (runs the probe standalone, with no generator); `chaos run`'s and `analyze resilience`'s printed summaries now report the probe's finding beside the audit-log one |
| `profiles/thesis.yaml`, `profiles/thesis-extended.yaml`, `profiles/smoke.yaml` | Added the `probe_*` keys under `chaos:` (pure addition, no existing key changed) |

### Achieved loop cadence and timestamp resolution — measured, not targeted

The configured dispatch cadence is `probe_interval_s = 0.002` (2 ms) with
`probe_workers = 8`. That is a **target**, and it is not what the probe
achieves — the design assumes writes cost roughly 370 ms from the workstation
that runs the harness, so eight workers in flight are expected to complete
roughly every 46 ms, not every 2 ms. What Deployment C's two Phase IV runs
actually measured (`events.json` → `probe`, both runs, live cluster):

| | recover run | dead run |
|---|---|---|
| Run id | `20260905T213539Z_p4-chaos-recover` | `20260905T213941Z_p4-chaos-dead` |
| Configured dispatch interval | 2 ms | 2 ms |
| Configured workers | 8 | 8 |
| Attempts recorded | 3,370 | 2,174 |
| **Achieved rate** | **18.24 writes/s** | **11.65 writes/s** |
| Median canary-write cost | 398.736 ms | 575.344 ms |
| Median gap between served writes (`gap_p50_s`) | 53.2 ms | 76.4 ms |
| **Resolution achieved (`resolution_s`, the 95th-percentile gap — see `NEW-DEFECTS.md` D13 for why the 95th percentile and not the median)** | **109.2 ms** | **223.9 ms** |
| Dispatch ticks that found every worker busy | 6.33% | 11.76% |

The dead-fault run's writes cost noticeably more (575 ms median against 399 ms)
and its resolution is correspondingly worse (224 ms against 109 ms) — the probe
runs against whatever the link and the cluster's own load happen to be at the
time, and reports what it actually achieved rather than the configured target,
which is the entire point of recording both numbers per run rather than only
the configured one.

### Log file schema, and one real sample of each

`rto_probe.csv` — one row per canary write, columns exactly (from
`crdblab.core.recorder.PROBE_COLUMNS`):

```
ts_utc, seq_id, dispatch_offset_s, complete_offset_s, duration_ms, outcome, worker, detail
```

One real row (`runs/20260905T213539Z_p4-chaos-recover/rto_probe.csv`):

```
2026-09-05T21:35:45.549674Z,1,0.002276,1.633773,1631.497,ok,0,
```

`rto_probe.log` — JSON, one object per line, flushed as each event happens
(`crdblab/core/rto_probe.py`, `_EventLog`). The first and last lines of the same
run (`probe_start` and `probe_stop` — every event in between is a per-worker
`connect` in this particular run, because neither Deployment C Phase IV run
recorded a single `timeout` or `conn_error` outcome; there is no `attempt_failed`
or `reconnect` line in either retained log to show a real sample of, and that
absence is stated rather than papered over with a fabricated one):

```json
{"ts_utc": "2026-09-05T21:35:43.916612Z", "offset_s": 0.0, "event": "probe_start", "epoch_utc": "2026-09-05T21:35:43.915930Z", "table": "rto_canary", "interval_s": 0.002, "workers": 8, "statement_timeout_ms": 5000, "connect_timeout_s": 2.0}
```

```json
{"ts_utc": "2026-09-05T21:38:48.725455Z", "offset_s": 184.812627, "event": "probe_stop", "attempts": 3370, "outcomes": {"ok": 3370, "timeout": 0, "conn_error": 0, "refused": 0}, "dispatch_interval_s": 0.002, "workers": 8, "ticks": 92199, "dispatch_saturation": 5835, "dispatch_saturation_pct": 6.33, "ticks_spaced_out": 82994, "achieved_rate_per_s": 18.24, "served_rate_per_s": 18.24, "resolution_s": 0.104841, "gap_p50_s": 0.053216, "gap_max_s": 0.365152, "median_write_ms": 398.736, "span_s": 184.805}
```

(The `probe_stop` line's own `resolution_s`/`gap_p50_s` are the summary computed
*at measurement time*; `analysis/resilience.py::probe_availability()`
independently re-derives the same quantities from `rto_probe.csv` when a figure
or a `crdblab analyze` invocation asks for them, per the project's existing rule
that a published number must be recomputable from the observations behind it,
not only readable from a cached summary.)

---

## 3. What actually ran, per phase (Deployment C)

| Phase | Run id | `generator_command` (verbatim from `manifest.json`) |
|---|---|---|
| I | `20260905T202859Z_p1-network` | `ping -c 100 -i 0.1 (all pairs, bidirectional)` |
| II | `20260905T203010Z_p2_baseline` | `cockroach workload run ycsb --workload=CUSTOM --seed=42 --insert-count=125000 --request-distribution=uniform --read-freq=0.8 --update-freq=0.2 --concurrency=200 --duration=60s --display-every=1s 'postgresql://root@crdb-local-1:26257/ycsb?sslmode=disable'` (shown for the last tier executed; the sweep covers concurrencies 1/2/5/10/50/100/200 × 3 repetitions, randomised order) |
| III | `20260905T210130Z_p3_cluster` | identical generator flags, targeting `postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable'` |
| IV recover | `20260905T213539Z_p4-chaos-recover` | `... --concurrency=100 --duration=180s ... 'postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable'` |
| IV dead | `20260905T213941Z_p4-chaos-dead` | same, `--mode=dead` |

---

## 4. Terraform-managed topology vs. harness target selection — precise answer

**The Terraform-provisioned infrastructure did not change.** Six machines exist
under Terraform's management both before and after this change: five cluster
nodes (`crdb-linode-1`, `crdb-linode-2`, `crdb-azure-1`, `crdb-azure-2`,
`crdb-gcp-1`) and one unreplicated baseline (`crdb-local-1`). Every one of
`cluster_join_nodes`, every node's `hostname`, `region`, and machine type
(`g6-dedicated-2`, `Standard_B2ls_v2`, `n2-custom-2-4096`) in
`terraform/terraform.tfvars.example` is byte-identical to before (§ 1,
confirmed by an empty `git diff --stat`).

**What changed is entirely inside the harness's own declaration of which
already-provisioned node it treats as the gateway** — one boolean flag,
`gateway=True`, moved from the `Node` entry for `linode-1` to the entry for
`gcp-1` in `crdblab/topology.py`. `gateway` is not a Terraform concept at all;
Terraform has no variable, resource, or output named anything like it. The flag
exists solely so that `crdblab.topology.Topology.gateway` — the single property
every phase script, every pre-flight check, and every CLI command reads to find
"the node the generator and the audit/probe clients connect to" — has exactly
one answer.

**One consequence reached outside the harness, into a Terraform-managed
artefact, and it is the one line in § 1.** Once the gateway moved to `gcp-1`
(region `us-east1`), the CockroachDB zone-configuration statement
`bootstrap.tftpl` issues at `terraform apply` time needed to name `us-east1`
first in `lease_preferences`, or the new gateway's own writes would pay a
wide-area hop to a leaseholder still pinned near the old gateway (`us-east`).
This is a content change to a value inside an existing, unchanged resource — it
adds no node, removes no node, and does not touch `cluster_join_nodes` or any
node's `hostname`/`region`/`machine_type` — and it was applied by the user
directly on their own `terraform apply`, not by any tool call available to me
in this conversation.

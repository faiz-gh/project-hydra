# Hardware comparability: Deployment C

This is the specific claim the gateway shift was made to establish: that
`crdblab analyze raft-overhead` can compare Phase II against Phase III **without**
`--accept-hardware-difference`, because the two phases now run on identical
hardware rather than on an Intel Xeon baseline against an AMD EPYC gateway
(`docs/defects.md` D11a). This document confirms or denies that from the
captured fields directly, not from the intent of the change.

---

## 1. `capture_server_config()` output, side by side

Captured live, in this session, by calling
`crdblab.core.preflight.capture_server_config()` against the running gateway and
baseline nodes — the same function `crdblab/phases/bench.py` and
`crdblab/phases/p4_chaos.py` call at measurement time and record into each run's
manifest. Both readings below are also independently present, verbatim, in
Deployment C's own retained manifests (cited per row), so this is a
cross-check, not the only evidence.

| | Gateway (`crdb-gcp-1`) | Baseline (`crdb-local-1`) |
|---|---|---|
| `cpus` | 2 | 2 |
| `mem_total_kb` | 4,007,012 | 4,007,004 |
| `cpu_model` | `Intel(R) Xeon(R) CPU @ 2.80GHz` | `Intel(R) Xeon(R) CPU @ 2.80GHz` |
| CockroachDB version | v26.3.0 | v26.3.0 |
| `start_command` | `cockroach start --insecure --store=/var/lib/cockroach --listen-addr=100.121.13.30:26257 --advertise-addr=100.121.13.30:26257 --locality=cloud=gcp,region=us-east1 --cache=0.25 --max-sql-memory=0.25 --join=crdb-gcp-1,crdb-azure-1,crdb-azure-2,crdb-linode-1,crdb-linode-2` | `cockroach start-single-node --insecure --store=/var/lib/cockroach --listen-addr=100.100.93.75:26257 --http-addr=100.100.93.75:8080 --cache=0.25 --max-sql-memory=0.25` |

Manifest cross-check (identical values, recorded independently at each phase's
own measurement time rather than in this later live query):

```
runs/20260905T210130Z_p3_cluster/manifest.json  → notes: "host: cpus=2 mem_total_kb=4007012 cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz"
runs/20260905T203010Z_p2_baseline/manifest.json → notes: "host: cpus=2 mem_total_kb=4007004 cpu_model=Intel(R) Xeon(R) CPU @ 2.80GHz"
```

**Difference: 8 kB of memory (0.0002%), zero difference in CPU model, zero
difference in vCPU count.** For comparison, Deployment B's own baseline-vs-gateway
memory split, *before* this change (`docs/dissertation-verification.md`
§ 6.5–6.6: baseline 4,007,012 kB, gateway 4,005,704 kB), was 1,308 kB — 0.033%,
itself within `validation.MEMORY_TOLERANCE` and not the reason that comparison
needed the override. What Deployment B could not get past without the flag was
the **CPU model**: `Intel(R) Xeon(R) CPU @ 2.80GHz` (baseline) against
`AMD EPYC 7713 64-Core Processor` (gateway). That is the field Deployment C's
table above shows as now identical on both sides.

---

## 2. Does `check_run_comparability` pass without the override?

**Yes — confirmed from the actual JSON `raft-overhead` produced, not asserted
from the intent of the change.**

```bash
.venv/bin/crdblab analyze raft-overhead \
  --baseline 20260905T203010Z_p2_baseline \
  --cluster  20260905T210130Z_p3_cluster \
  --json
```

was run with **no** `--accept-hardware-difference` flag present. Its
`comparability` block, read directly from that output:

```json
"comparability": {
  "ok": true,
  "findings": []
}
```

`findings: []` is the load-bearing fact: it is not merely that the comparison
did not refuse — `check_run_comparability` returns a *warning*-severity finding
(not an error) when `--accept-hardware-difference` is passed over a genuine
difference, so a non-empty findings list downgraded to a warning would still
have been visible here had one been produced. There is none. Nothing about
CPU model, vCPU count, memory, CockroachDB version, or server flags triggered
any finding at any severity.

The command's own exit and printed output confirm the same thing at the CLI
level — no `refusing to compare` message, no "differing hardware" warning line
anywhere in its text output, and the sweep script (`run-experiment.sh`) invoked
the identical comparison without the flag as part of producing this same run
pair (`CHANGE-DIFF.md` § 1), which is the condition under which the flag's
removal was tested end-to-end rather than only unit-tested.

**For contrast, re-run against Deployment B's own run pair** (pre-shift,
gateway `crdb-linode-1`, baseline `crdb-local-1`), with the same omission:

```
$ crdblab analyze raft-overhead --baseline 20260902T233336Z_p2_baseline --cluster 20260903T000438Z_p3_cluster
refusing to compare: p2_baseline and p3_cluster were measured on different hardware (cpu_model:
'Intel(R) Xeon(R) CPU @ 2.80GHz' vs 'AMD EPYC 7713 64-Core Processor'); a throughput difference
between them is not attributable to the variable under study. If this difference is a known
limitation of the study rather than a mistake, say so explicitly rather than comparing anyway

These two runs differ in more than replication, so their difference is not replication cost
(see docs/defects.md, D9).
```

Same code path, same check, no flag either time — one pair refuses, the other
does not, and the only variable between them is which node the gateway role
was assigned to. That is the confirmation, not an assertion that the change
"should" have worked.

---

## 3. What this does and does not establish

**Established:** the two run-level facts `check_run_comparability` is able to
see — CPU model and vCPU count (exact match), memory (within tolerance),
CockroachDB version and server flags (exact match, per `server_config` in the
`raft-overhead` JSON, quoted in `RESULTS-DATA-DEPLOYMENT-C.md` § 5) — no longer
differ between Phase II and Phase III. The Raft-overhead comparison is
single-variable on every axis this check inspects.

**Not established, and not claimed:** that the two phases are free of every
confound a human reader might imagine. `matched_throughput`'s own output still
reports a `utilisation_gap` per point and names the least-confounded one
explicitly rather than the largest, because matching throughput does not match
utilisation, and that is a fact about queueing, not about hardware — no
hardware check could see it either way. See
`RESULTS-DATA-DEPLOYMENT-C.md` § 5 for that reasoning applied to Deployment C's
actual numbers.

# Validation status: Deployment C

Every count below is read directly from each run's own `preflight.json` and
from `crdblab validate`'s own output, run in this session against the current
code. Nothing is summarised from an earlier report.

---

## 1. Pre-flight and validation, per run

| Phase | Run id | Pre-flight | `crdblab validate` | Probe log validation |
|---|---|---|---|---|
| I | `20260905T202859Z_p1-network` | **6/6 passed** | n/a — no `metrics.csv`; Phase I asserts via `preflight.json` alone, exactly as `crdblab validate` itself states when pointed at a network run | n/a |
| II | `20260905T203010Z_p2_baseline` | **22/22 passed** | `PASS: no consistency errors detected` (`{"ok": true, "findings": []}`) | n/a — no probe on Phase II |
| III | `20260905T210130Z_p3_cluster` | **45/45 passed** | `PASS: no consistency errors detected` (`{"ok": true, "findings": []}`) | n/a — no probe on Phase III |
| IV recover | `20260905T213539Z_p4-chaos-recover` | **3/3 passed** | `PASS: no consistency errors detected (workload and probe logs)` (`{"ok": true, "findings": []}`) | `{"ok": true, "findings": []}` |
| IV dead | `20260905T213941Z_p4-chaos-dead` | **3/3 passed** | `PASS: no consistency errors detected (workload and probe logs)` (`{"ok": true, "findings": []}`) | `{"ok": true, "findings": []}` |

**Every check, in every run, passed. Zero warnings, zero errors, at any
severity, anywhere in Deployment C.**

Pre-flight check breakdown (what the 6/22/45/3/3 counts are actually made of,
per `preflight.json` → `checks[].name`):

| Run | `clock_offset` | `leaseholder_placement` | `row_match` | `write_latency_floor` | `quorum_floor_available` |
|---|---:|---:|---:|---:|---:|
| Phase I | 5 | 1 | — | — | — |
| Phase II | 1 | — | 21 | — | — |
| Phase III | 1 | 1 | 21 | 21 | 1 |
| Phase IV recover | 2 | 1 | — | — | — |
| Phase IV dead | 2 | 1 | — | — | — |

One `row_match` check in Phase III reports a flushed statistics window
(`"the statement-statistics view was flushed after this tier ended, so its
row-match evidence is unrecoverable; the tier's write median cleared..."`) —
this is D12's documented, handled case: the check corroborates from the
quorum-floor evidence instead and still passes, it does not silently skip.
It is counted as passed above because the check itself reports `[PASS]` for
it, exactly as D12's fix specifies.

Every `leaseholder_placement` check across all five runs reports
`2/2 ycsb leaseholders in 'us-east1'` — the gateway's own region, in every
phase including both Phase IV runs where a different node is faulted
(`TOPOLOGY-DELTA.md` § 2).

---

## 2. What `crdblab analyze` additionally confirms

Beyond the pass/fail gate, `analyze raft-overhead`'s own `comparability` block
(`HARDWARE-COMPARABILITY.md` § 2) is a *third* independent check layered on
top of the per-run validations above — it compares server flags, workload
parameters, CockroachDB version, and hardware *between* Phase II and Phase III,
which no single run's own pre-flight or validation can do. That block also
reports `{"ok": true, "findings": []}`.

---

## 3. Deployment B — confirmed untouched, not superseded

**Deployment B's five run directories were not read, modified, deleted, or
re-analysed as part of producing this package**, beyond the read-only
comparison queries this documentation itself cites (`TOPOLOGY-DELTA.md`,
`HARDWARE-COMPARABILITY.md` § 2, `RESULTS-DATA-DEPLOYMENT-C.md` § 6c) — each of
which is a `crdblab analyze` invocation that reads a run and writes nothing
back to it.

Confirmed present, this session:

```
runs/20260902T233208Z_p1-network        present
runs/20260902T233336Z_p2_baseline       present
runs/20260903T000438Z_p3_cluster        present
runs/20260903T003646Z_p4-chaos-recover  present
runs/20260903T004024Z_p4-chaos-dead     present
```

**Deployment B's own figures were not regenerated, backfilled, or altered by
this package.** `docs/dissertation-verification.md`'s account of Deployment B
remains the authoritative record of that deployment; nothing here amends it.
`FIGURES-DEPLOYMENT-C/` is a new, separate directory
(`docs/deployment-c/FIGURES-DEPLOYMENT-C/`) — it does not write into or
overwrite the project's own `figures/` directory, which is where the
project's currently-reported figure set lives (regenerated from Deployment C
data in the course of ordinary use of `run-experiment.sh`, as recorded
separately, but that regeneration is not an action this documentation package
itself performed).

**Deployment B is not superseded by Deployment C for any claim this package
does not itself make.** This package documents two specific changes and their
consequences; it does not re-litigate, re-derive, or recommend replacing any
number Deployment B's own retained documentation reports. Where a comparison
between the two deployments appears above (`TOPOLOGY-DELTA.md`,
`HARDWARE-COMPARABILITY.md`, `RESULTS-DATA-DEPLOYMENT-C.md` § 6c), it is
presented as exactly that — a comparison — not as a correction to Deployment
B's own record.

---

## 4. Runs referenced but explicitly out of scope for this package

Two further categories of run exist in `runs/` and are deliberately excluded
from every table above:

- **A first, superseded post-shift sweep**
  (`20260905T181037Z_p1-network` … `20260905T192035Z_p4-chaos-dead`), affected
  by the SSH-hang defect (`NEW-DEFECTS.md` D16) and, at the time its figures
  were drawn, the stale-figure defect (D17). All five of its runs individually
  validate — the defects were operational (script runtime, figure selection),
  not measurement corruption — but this package reports Deployment C
  (the second, corrected sweep) as the current data, and does not double-count
  the first sweep's numbers anywhere above.
- **Deleted diagnostic runs** (`20260905T172748Z_p4-probe` and three others),
  never part of a validated Deployment, whose sole remaining trace is
  `NEW-DEFECTS.md` D13's Class 2 record.

Both categories are named here for completeness, not folded into any count in
§ 1.

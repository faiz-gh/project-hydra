# Topology delta: Deployment B → Deployment C

Every value below is read from a retained run artefact — Deployment B's from
`runs/20260902T233208Z_p1-network/` (the deployment `docs/dissertation-verification.md`
labels **Deployment B** and reports as the dissertation's data), Deployment C's
from `runs/20260905T202859Z_p1-network/` (this session's sweep). Nothing is
computed by hand; ratios and rankings below are derived arithmetic on numbers
quoted from those two files, shown so the derivation is checkable.

---

## 1. Node role table, side by side

| Role | Deployment B (before) | Deployment C (after) |
|---|---|---|
| Gateway (generator, RPO audit, RTO probe) | `crdb-linode-1` (`linode`, `us-east`) | `crdb-gcp-1` (`gcp`, `us-east1`) |
| Phase II baseline | `crdb-local-1` (`local` role label; provisioned in GCP — see `crdblab/topology.py` `BASELINE_NODE` comment) | unchanged: `crdb-local-1` |
| Cluster member (fast triangle) | `crdb-linode-2` (`linode`, `us-west`) | unchanged: `crdb-linode-2` (`linode`, `us-west`) |
| Cluster member (fast triangle) | `crdb-gcp-1` (`gcp`, `us-east1`) — **now the gateway** | `crdb-linode-1` (`linode`, `us-east`) — **the former gateway, still a cluster member** |
| Cluster member (outside the triangle) | `crdb-azure-1` (`azure`, `centralindia`) | unchanged: `crdb-azure-1` (`azure`, `centralindia`) |
| Cluster member (outside the triangle) | `crdb-azure-2` (`azure`, `eastasia`) | unchanged: `crdb-azure-2` (`azure`, `eastasia`) |
| Chaos target | `crdb-linode-2` | unchanged: `crdb-linode-2` |
| Voter count | 5 | unchanged: 5 |

Source: `manifest.json` → `topology` in each deployment's `p1-network` run
(quoted in full in `CHANGE-DIFF.md` § 1 and reproduced for Deployment B below):

```
Deployment B: {'name': 'linode-1', ..., 'gateway': True}   {'name': 'gcp-1', ..., 'gateway': False}
Deployment C: {'name': 'linode-1', ..., 'gateway': False}  {'name': 'gcp-1', ..., 'gateway': True}
```

The two nodes that swap roles are exactly `linode-1` and `gcp-1`; every other
node's role is byte-identical between the two manifests.

---

## 2. Is `crdb-gcp-1` still inside the lease-preference low-latency group?

**Yes, and it now heads it.** The group itself — `us-east`, `us-east1`,
`us-west` — is unchanged; `us-east1` is `gcp-1`'s own region, so it was already
a member of the fast triangle before the shift, as a non-gateway cluster node.
What changed is the *order* CockroachDB's zone configuration lists them in
(`terraform/scripts/bootstrap.tftpl`, `CHANGE-DIFF.md` § 1):

```
Deployment B: lease_preferences = '[[+region=us-east], [+region=us-east1], [+region=us-west]]'
Deployment C: lease_preferences = '[[+region=us-east1], [+region=us-east], [+region=us-west]]'
```

Same three regions, same `num_replicas = 5` (unchanged elsewhere in the file;
not reproduced here since no diff touches it) — `us-east1` moved from second to
first. **This reorder was required, and did happen, and is verified live rather
than assumed:** every one of Deployment C's five run directories records
`leaseholder_placement: 2/2 ycsb leaseholders in 'us-east1'` in its own
`preflight.json`, i.e. both ranges of the `ycsb` database are led from the
gateway's own region in every phase, including Phase IV where the fault is
injected against a *different* node (§ 4 covers what happens to that
placement when the fast triangle itself is degraded, which is a separate
question from whether it started out correctly placed).

Had the reorder not been applied, the leaseholder would have stayed pinned
toward `us-east` (`linode-1`, the *former* gateway) while the generator ran on
`gcp-1`, adding a wide-area hop to every operation with no failing health
check to catch it — the same shape of silent misattribution as D7, at the
smaller constant this topology produces (§ 3).

---

## 3. Did the all-pairs RTT ranking change, not just its magnitudes?

**Yes — the relative order of the two Azure members, as seen from the
gateway, is different between the two deployments.** This is stated
explicitly because the project's own rule (`crdblab/topology.py` docstring, and
`docs/defects.md` D7/D9) is that a changed link ordering is not the same
experiment as before, independent of whether any absolute magnitude also
moved.

Mean RTT from the gateway to every other node, both deployments (`ts_utc`
identical within each file; one measurement instant per deployment):

| Deployment B — from `crdb-linode-1` | ms | Deployment C — from `crdb-gcp-1` | ms |
|---|---|---|---|
| → `crdb-gcp-1` (us-east1) | 18.324 | → `crdb-linode-1` (us-east) | 24.792 |
| → `crdb-linode-2` (us-west) | 67.054 | → `crdb-linode-2` (us-west) | 69.662 |
| → `crdb-azure-1` (centralindia) | **198.183** | → `crdb-azure-2` (eastasia) | **197.807** |
| → `crdb-azure-2` (eastasia) | 200.051 | → `crdb-azure-1` (centralindia) | 219.218 |

Source: `network.csv`, `rtt_mean_ms` column, both deployments' `p1-network`
runs (`runs/20260902T233208Z_p1-network/network.csv`,
`runs/20260905T202859Z_p1-network/network.csv`).

Reading the two right-hand columns as an ordering rather than as four
independent numbers: in Deployment B, `azure-1` (centralindia) is the
*nearer* of the two Azure members to the gateway (198.2 ms vs 200.1 ms); in
Deployment C, `azure-2` (eastasia) is the nearer one (197.8 ms vs 219.2 ms).
**The relative order of `azure-1` and `azure-2` inverts between the two
deployments.** This is a geometric fact about where the gateway sits, not a
defect: `us-east1` (Virginia-area) and `us-east` (also US East Coast) are
close enough to each other that their distances to South/East Asia differ by
only a few milliseconds and the ordering is not robust to which of the two US
nodes is doing the asking.

The role that does **not** reorder: `linode-2` (us-west) is the second-nearest
node to the gateway in both deployments, which is exactly the property the
lease-preference triangle depends on (§ 2) and is why the triangle's
*membership* did not need to change even though the gateway did.

**Consequence for the quorum floor.** Because five voters need the leader plus
two follower acknowledgements, the floor is the round trip to the
second-nearest follower in each deployment — which is `linode-2` in both, so
the floor is comparable in kind, and differs only in the small amount by which
`gcp-1`→`linode-2` (69.662 ms) exceeds `linode-1`→`linode-2` (67.054 ms):

```
Deployment B quorum floor: 67.054 ms   (runs/20260902T233208Z_p1-network/preflight.json → derived.quorum_floor_ms)
Deployment C quorum floor: 69.662 ms   (runs/20260905T202859Z_p1-network/preflight.json → derived.quorum_floor_ms)
```

A 2.608 ms / 3.9% increase — not from a defect, but from `gcp-1` sitting
marginally further from `us-west` than `linode-1` does. Every write-latency
floor check in Deployment C is asserted against **this run's own** recomputed
value (69.662 ms), not against the Deployment B constant, so nothing downstream
silently compares against the wrong number.

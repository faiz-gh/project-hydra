# The testbed, at a glance

What's deployed, where each machine sits, and how a query, a fault, and a
measurement actually travel through the system — for both engines. This is a
**current-state reference**, not a log: it describes what the code says
should be true right now, kept in sync as the architecture changes. For the
history of _why_ it ended up this shape — the incidents, the defects, the
dated narrative — see `docs/history.md`. For step-by-step reproduction
instructions, see `instructions.md`. For design commitments and CLI usage,
see `README.md`. For the exact on-disk artifact schema, see
`docs/data-schema.md`.

> **Deployment status is not tracked here.** Only one engine is ever deployed
> at a time (`terraform apply -var="database_engine=..."` replaces every
> cluster node), and which one that currently is drifts independently of this
> file. Check live with `tailscale status` (which hostnames answer) and the
> most recent entry in `runs/_logs/` (`grep '^\[1mcrdblab' runs/_logs/*.log |
tail -1`, or just open the newest file — its banner line names the engine
> and profile). Everything below describes the code's current design, which
> is what the _next_ deployment will produce — not necessarily what is live
> this minute if a change hasn't been applied yet (see "Code vs. deployed"
> at the bottom).

---

## The six machines

Three cloud providers, five cluster nodes plus one dedicated client/generator
node that is **not** a cluster member.

| Node (`Node.name`) | Hostname        | Provider | Region       | Shape                                               | Role                                                                                                                                                                                                               |
| ------------------ | --------------- | -------- | ------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `gcp-1`            | `crdb-gcp-1`    | GCP      | us-east1     | `c2d-highcpu-4` (4 vCPU / 8 GiB)                    | **The gateway.** `Node.gateway=True`. CockroachDB leaseholder pinned here (`lease_preferences`); Patroni primary pinned here (`failover_priority: 100`, bootstrap ordering). ~1 ms from the client node.           |
| `linode-1`         | `crdb-linode-1` | Linode   | us-east      | `g6-dedicated-4` (4 vCPU / 8 GiB)                   | Cluster voter. Bootstrap primary (first in `cluster_join_nodes`, runs `cockroach init`/wins the initial Patroni election race) — distinct from the harness gateway. ~24 ms from gcp-1. HAProxy failover candidate. |
| `linode-2`         | `crdb-linode-2` | Linode   | us-west      | `g6-dedicated-4` (4 vCPU / 8 GiB)                   | Cluster voter. Sets the quorum floor (second-fastest follower from gcp-1, ~68-72 ms). HAProxy failover candidate.                                                                                                  |
| `azure-1`          | `crdb-azure-1`  | Azure    | centralindia | `Standard_B4als_v2` (4 vCPU / 8 GiB, **burstable**) | Cluster voter. ~205-230 ms from gcp-1. Never the chaos target, leaseholder, or Patroni primary. HAProxy backup-of-last-resort only.                                                                                |
| `azure-2`          | `crdb-azure-2`  | Azure    | eastasia     | `Standard_B4als_v2` (4 vCPU / 8 GiB, **burstable**) | Cluster voter. ~153-212 ms from gcp-1. Same disclosed limitations as azure-1. HAProxy backup-of-last-resort only.                                                                                                  |
| `client-1`         | `crdb-client-1` | GCP      | us-east1     | `c2d-highcpu-4` (4 vCPU / 8 GiB)                    | **`CLIENT_NODE`, not a cluster member, no replica.** Runs the generator, both HAProxy frontends, the copied RTO-probe agent. Co-located with gcp-1 (~1 ms) deliberately.                                           |

All six report `nproc=4`, `MemTotal` ≈ 8.36 GB (confirmed live, <0.1% spread).
Boot disks: GCP/Azure/client explicitly 100 GB; Linode ships 160 GB. Azure's
two nodes are burstable B-series because no non-burstable 4 vCPU/8 GiB SKU is
available on this subscription (`standardDLSv5Family` quota is 0) — disclosed
beside the RTT matrix, never the basis of a CPU-bound claim.

**Also involved, not a testbed machine:** the harness process itself — wherever
`crdblab` / `run-experiment.sh` is invoked (the operator's workstation in
practice). Every `ssh` in this codebase, and every direct network connection
the RPO audit writer / hardware-metrics poller make, originates _here_. No
testbed node ever SSHes into another.

---

## Network

Every node reaches every other node over a **Tailscale mesh** — MagicDNS
names (`crdb-gcp-1`, …), never public IPs or cloud-internal DNS (the latter
is a proven footgun: GCP's own internal DNS zone resolves ahead of the
tailnet's on some nodes, which is why every self-referential `--host=`
lookup in this codebase resolves `tailscale ip -4` explicitly rather than
trusting a bare hostname). No cloud firewall rule was ever needed for any
cross-node service — everything rides the tailnet.

| Port    | Service                                                                     | Bound on                                   | Reached from                                                                                  |
| ------- | --------------------------------------------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `26257` | CockroachDB SQL (pgwire)                                                    | all 5 cluster nodes, on their Tailscale IP | generator (via local HAProxy, below), audit writer, capture, manual admin                     |
| `8080`  | CockroachDB HTTP (admin UI, `/health?ready=1`)                              | all 5 cluster nodes, default bind          | the new `crdb_gateway` HAProxy backend's health check only                                    |
| `5432`  | PostgreSQL                                                                  | all 5 cluster nodes                        | Patroni-managed; reached via HAProxy or the audit writer's direct multi-host DSN              |
| `8008`  | Patroni REST API (`/health`, `/primary`, `/replica`, `/patroni`, `/quorum`) | all 5 cluster nodes                        | preflight checks, `patroni_primary` HAProxy backend's health check, `resolve_patroni_primary` |
| `9100`  | node_exporter (`/metrics`)                                                  | all 5 cluster nodes + `client-1`           | harness process, polled directly (no standalone Prometheus)                                   |
| `5000`  | HAProxy `patroni_primary` frontend                                          | `client-1`, all interfaces                 | the generator (`pg_generator_dsn`'s second hop, via pgbouncer)                                |
| `6432`  | pgbouncer                                                                   | `client-1`, loopback only                  | the generator directly (`pg_generator_dsn`'s first hop)                                       |
| `26257` | HAProxy `crdb_gateway` frontend                                             | `client-1`, **loopback only**              | the generator directly (`crdb_generator_dsn`)                                                 |

The two `client-1` HAProxy frontends live in **one process, one config file**
(`/etc/haproxy/haproxy.cfg`) — both are always configured regardless of which
engine is actually deployed on the cluster nodes (the client node's own
bootstrap doesn't take `database_engine`), so the unused one simply sees no
traffic, the same way the unused `cockroach`/`psql` client binary is harmless
when the other engine is running.

---

## CockroachDB: how a query travels

```
harness --SSH--> crdb-client-1 --launches--> `cockroach workload run`
                                                    |
                                                    v
                                     postgresql://root@127.0.0.1:26257/ycsb
                                                    |
                                                    v
                               HAProxy `crdb_gateway` (client-1, loopback :26257)
                               PINNED, not load-balanced -- gcp-1 is the only
                               regular server; linode-1/linode-2/azure-1/azure-2
                               are all flat-tier HAProxy `backup` servers, used
                               only once gcp-1 fails /health?ready=1.
                                                    |
                                          (normally: gcp-1 only)
                                                    |
                                                    v
                              CockroachDB's own internal routing forwards to
                              whichever node actually holds the leaseholder --
                              always gcp-1, per `lease_preferences`, which
                              never changes during a run.
```

Why pinned rather than load-balanced: CockroachDB's `/health?ready=1`
answers 200 for **any** live, joined node — it cannot distinguish "the
preferred gateway" from "a node that merely happens to also be able to route
this query," unlike Patroni's `/primary`, which only the actual primary ever
answers. Load-balancing by health check alone would therefore spread traffic
across 3 nodes _even while gcp-1 is perfectly healthy_, paying an avoidable
internal RPC hop (~24-68 ms) on every connection that lands elsewhere.
`backup` reproduces Patroni's single-active-target behaviour explicitly
instead — see `docs/history.md`'s 2026-09-11 entry for the full reasoning and
the two alternatives that were tried and rejected first.

**Fault injection** is a separate, direct path — no client-node involvement:

```
harness --SSH--> gcp-1 (the fault target, live-pinned by chaos.target)
                    |
                    +-- dead:    sudo -n killall -9 cockroach
                    +-- recover: sudo -n tailscale down && sleep 45 && tailscale up
```

Once `gcp-1` fails its health check, HAProxy's failover to a backup happens
on its own `inter`/`fall` cadence (up to ~9 s) — this is now a real,
production-realistic failover, not a hardcoded pre-chosen alternate, and its
detection latency is deliberately allowed to show up in the generator-derived
"RTO, performance" figure (a real client behind a real load balancer would
experience exactly that delay).

**RPO audit writer and RTO probe both bypass HAProxy entirely, on purpose:**

```
RPO audit writer:  a Python thread INSIDE the harness process itself
                    (not on client-1) -- direct psycopg connection,
                    multi-host DSN naming all 5 cluster nodes:
                    postgresql://root@crdb-gcp-1:26257,crdb-linode-1:26257,.../rpo_audit

RTO probe:          copied (tar-over-SSH) to client-1, executed there as
                    `python3 -m crdblab.core.rto_probe`, using the SAME
                    multi-host DSN as the audit writer above.
```

They bypass the proxy so that a hiccup in HAProxy itself can never be
mistaken for a database outage — these two instruments exist specifically to
observe the cluster _through_ the fault, independent of any single component
that could itself become a point of failure.

**After the measurement (`dead` mode only):** harness SSHes to `gcp-1` to
restart it, and separately to a survivor to poll cluster liveness (polling
the just-killed node itself would trivially report "0 live"). `recover` mode
heals itself — its payload includes its own `sleep 45 && tailscale up`.

---

## PostgreSQL/Patroni: how a query travels

Same shape, two differences forced by Patroni's single-primary model.

```
harness --SSH--> crdb-client-1 --launches--> `cockroach workload run`
                                                    |
                                                    v
                                 postgresql://root:***@127.0.0.1:6432/ycsb
                                                    |
                                                    v
                              pgbouncer (client-1, loopback :6432) -- strips
                              the `allow_unsafe_internals` startup parameter
                              PostgreSQL would otherwise reject with a FATAL
                                                    |
                                                    v
                         HAProxy `patroni_primary` (client-1, :5000, all
                         interfaces) -- health-checked via each node's
                         :8008/primary; only the actual primary ever answers
                         200, so this is a failover proxy, not a load
                         balancer, even though it lists 3 regular candidates
                                                    |
                                                    v
                       whichever of gcp-1/linode-1/linode-2 IS currently
                       primary (azure-1/azure-2: backup only)
```

Two proxy hops here, not one — pgbouncer exists purely because of the
startup-parameter rejection above; both hops are local loopback on
`crdb-client-1`, ahead of the wide-area link. Because exactly one backend is
ever actually reachable through `/primary`'s health check, this "3 regular +
2 backup" config is really a failover proxy with a 3-candidate pool, not true
load-balancing the way CockroachDB's backend (before the 2026-09-11 pinning
fix) briefly was.

**Fault injection** — harness SSHes directly to whichever node
`resolve_patroni_primary()` determines is the _live_ primary (queried fresh
via `:8008/primary` immediately before scheduling; `chaos.target: gcp-1` in
the profile is a pin/expectation the live read can override, not a
substitute for it):

```
harness --SSH--> <live primary, normally gcp-1>
                    |
                    +-- dead:    systemd drop-in (Restart=no) + daemon-reload,
                    |            then `systemctl kill --kill-who=all
                    |            --signal=SIGKILL patroni.service`
                    |            (plain killall/pkill don't work -- Patroni's
                    |            comm is `python3`, not `patroni`)
                    +-- recover: identical tailscale down/up payload as
                                 CockroachDB
```

**Repair, before the fault target is even chosen:**
`check_patroni_primary_placement` runs first and switches the primary back
to `gcp-1` via `patronictl switchover` if a prior phase's failover left it
elsewhere — Patroni never fails back on its own. Only after that does
`resolve_patroni_primary()` do its live read.

**RPO audit writer / RTO probe** — architecturally identical to the
CockroachDB path: audit writer as a harness-process thread, RTO probe copied
to and run on `client-1`, both using `pg_direct_dsn` — a multi-host DSN
naming all 5 nodes with `target_session_attrs=read-write` (so libpq
specifically seeks the _writable_ node) plus TCP keepalives and
`tcp_user_timeout=10000` (added after an incident where a black-holed
`recover`-mode socket was mistaken for instant recovery). Both bypass
HAProxy.

---

## Monitoring: two collectors, both from the harness process

Neither runs on `client-1` — both are direct, unproxied connections from
wherever `crdblab` itself executes:

- **Hardware metrics** (`crdblab/core/hardware_metrics.py`) — plain HTTP
  `GET :9100/metrics` (node_exporter, no SSH) against all 6 machines every
  ~5 s during Phase II-IV, differencing consecutive counter scrapes into
  rates (CPU/disk/net). Written to each run's `hardware_metrics.csv`. A
  node's _first_ poll has no prior scrape to diff against, so those columns
  are `""`, never `0` (D5).
- **Workload metrics** — the generator's own stdout (throughput, latency
  percentiles per tick) streams back over the SSH connection the harness
  holds open to `client-1` and is parsed by `crdblab/core/workload.py` into
  `metrics.csv`. This is the same channel the generator's query traffic
  travels _out_ through — not a separate collector.

So "where is data actually collected" splits into three genuinely
independent channels in both engines: the generator's SSH-streamed stdout,
the harness's own direct multi-host DB connections (RPO/RTO), and the
harness's own direct HTTP polling (hardware). Only the generator's actual
_query_ traffic goes through a proxy; nothing that measures the system does.

---

## Code vs. deployed

As of 2026-09-11 the code and the last deployment agree: the CockroachDB
HAProxy pinning described above (`crdb_gateway`, the pinned backup-tier
failover with ordered failback) is committed (`56077c2`, `de7e94a`) and has
been confirmed live not just by the smoke pair that caught and validated the
fix, but by a full `thesis-extended` sweep on both engines afterward — check
`runs/_logs/`'s most recent CockroachDB entry's manifest / generator_command
to confirm which path is in force on a given deployment
(`postgresql://root@127.0.0.1:26257/...` means the client-side HAProxy path
described above; a bare `crdb-gcp-1:26257` would mean an older, pre-HAProxy
deployment, which no longer exists on disk). `bootstrap-client.tftpl` only
runs at first boot (cloud-init), so this still only describes deployments
made _after_ the commits above landed — a redeploy from an older checkout
would not have it, hence the general caution in the box at the top of this
file about checking what's actually live rather than assuming the code
matches the deployment. The pin's first version (flat, unordered backups, no
failback) shipped and was live-verified defective the same day it was first
deployed — see the `crdb_gateway` gotcha in `CLAUDE.md` and `docs/history.md`'s
2026-09-11 entries for the full defect, the fix (ordered backup tiers
matching `lease_preferences`, plus `on-marked-up shutdown-backup-sessions`
for failback), and the subsequent thesis-extended-scale confirmation.

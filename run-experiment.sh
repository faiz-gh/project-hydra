#!/usr/bin/env bash
#
# run-experiment.sh — measure all four phases end to end.
#
# Preconditions: `terraform apply` has completed, cloud-init has finished on
# every node, Tailscale is up on this machine, and `.env` names the gateway.
# Everything after that is automated here.
#
# The script is deliberately noisy and deliberately fragile: it stops at the
# first failure rather than continuing with a testbed that is not fit to be
# measured. Every defect this project has on record produced *plausible* output,
# so a run that limps past a failed check is worse than no run at all.
#
#   ./run-experiment.sh                     # full sweep, ~75 min
#   ./run-experiment.sh --smoke             # harness self-test, ~14 min
#   ./run-experiment.sh --skip-load         # working set already loaded
#   ./run-experiment.sh --no-chaos          # phases I-II only, no fault injection
#   ./run-experiment.sh --engine postgresql # measure the PostgreSQL/Patroni deployment
#
# --engine names the engine that is *currently deployed* on the testbed (i.e.
# whatever `terraform apply -var="database_engine=..."` last stood up). It does
# not deploy anything: a redeploy replaces every cluster node, so pointing this
# at an engine that is not actually running just fails the pre-flight checks.
#
set -euo pipefail

PROFILE="thesis-extended"
ENGINE="cockroachdb"
SKIP_LOAD=0
RUN_CHAOS=1
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python"
CRDBLAB="$VENV/bin/crdblab"
LOG_DIR="$REPO/runs/_logs"
LOG=""

# Piping this script's own stdout through `tee` (below) means Python is no
# longer attached to a terminal, so it switches from line-buffered to fully
# block-buffered output by default -- crdblab's live per-tier progress would
# then queue up and appear all at once when a buffer fills or the process
# exits, which reads exactly like "no logs until it's done" even though the
# code is printing the whole time. This forces line buffering regardless.
export PYTHONUNBUFFERED=1

# --- output -----------------------------------------------------------------

if [ -t 1 ]; then
  B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; R=""; G=""; Y=""; D=""; N=""
fi

step()  { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()    { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
warn()  { printf '%s  !!%s  %s\n' "$Y" "$N" "$*"; }
note()  { printf '%s      %s%s\n' "$D" "$*" "$N"; }
die()   { printf '\n%sFAILED:%s %s\n' "$R" "$N" "$*" >&2
          [ -n "$LOG" ] && printf 'log: %s\n' "$LOG" >&2
          exit 1; }

usage() {
  sed -n '3,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}
HAS_ARGS=0
if [ $# -gt 0 ]; then
  HAS_ARGS=1
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)   PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    --engine)    ENGINE="${2:?--engine needs a value}"; shift 2 ;;
    --smoke)     PROFILE="smoke"; shift ;;
    --skip-load) SKIP_LOAD=1; shift ;;
    --no-chaos)  RUN_CHAOS=0; shift ;;
    -h|--help)   usage ;;
    *)           die "unknown argument: $1 (try --help)" ;;
  esac
done

if [ "$HAS_ARGS" -eq 0 ] && [ -t 0 ]; then
  printf "\nInteractive Configuration:\n"
  printf "Select test profile:\n"
  printf "  1) smoke (fast self-test)\n"
  printf "  2) thesis (standard)\n"
  printf "  3) thesis-extended (long run)\n"
  read -p "Choice [1-3, default=3]: " choice
  case "$choice" in
    1) PROFILE="smoke" ;;
    2) PROFILE="thesis" ;;
    *) PROFILE="thesis-extended" ;;
  esac

  printf "\nWhich engine is currently deployed on the testbed?\n"
  printf "  1) cockroachdb (default)\n"
  printf "  2) postgresql  (Patroni HA)\n"
  read -p "Choice [1-2, default=1]: " engine_choice
  case "$engine_choice" in
    2) ENGINE="postgresql" ;;
    *) ENGINE="cockroachdb" ;;
  esac

  read -p "Skip data load? (y/N): " skip_choice
  if [[ "$skip_choice" =~ ^[Yy] ]]; then
    SKIP_LOAD=1
  fi

  read -p "Run Chaos phase? (Y/n): " chaos_choice
  if [[ "$chaos_choice" =~ ^[Nn] ]]; then
    RUN_CHAOS=0
  fi
  printf "\n"
fi

case "$ENGINE" in
  cockroachdb|postgresql) ;;
  *) die "unknown engine: $ENGINE (expected 'cockroachdb' or 'postgresql')" ;;
esac

# --engine is a TOP-LEVEL crdblab flag and argparse rejects it after the
# subcommand name, so it is spliced in before the subcommand rather than
# appended (see CLAUDE.md / crdblab/cli.py). `bench`, `chaos run` and `net
# probe` all read it -- Phase I's ping measurement does not depend on the
# engine, but the deployment it was taken against does, and it is the manifest
# field that names the run's figure. `validate`, `analyze` and `report figures`
# do not read it: they take run ids, and every run already says which engine
# produced it.
ENGINE_ARGS=(--engine "$ENGINE")

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/experiment-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

SSH_OPTS=(-q -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o BatchMode=yes -o ConnectTimeout=20)
# -n (stdin from /dev/null) is load-bearing, not tidiness. ssh reads its own
# stdin and forwards it to the remote command, so an `ssh` inside a
# `while read ... done <<< "$LIST"` loop consumes the rest of the list: the
# loop runs exactly once and every node after the first is silently never
# checked. Observed 2026-09-08, where the Patroni health poll examined only
# crdb-gcp-1 and then reported "0 healthy member(s)" for all five. No command
# run through remote() feeds anything on stdin.
# StrictHostKeyChecking=no is deliberate and disclosed: the testbed is destroyed
# and rebuilt repeatedly and addresses get reused, which otherwise produces
# spurious host-key warnings. It is not a production posture.

remote() {  # remote <user> <host> <command>
  ssh "${SSH_OPTS[@]}" "$1@$2" "$3"
}

# HTTP status from one node's Patroni REST endpoint, asked from the client node
# so that a node unreachable from the workstation is not mistaken for a node
# that is down. Prints exactly one token: curl already prints `000` when it
# cannot connect, so the failure branch only has to cover ssh itself failing
# and printing nothing. An earlier `|| echo 000` appended a second token to
# curl's own, which is how a single unreachable node reported `000000`.
patroni_code() {  # patroni_code <host> <endpoint>
  local code
  code="$(remote "$CL_USER" "$CL_HOST" \
    "curl -s -m 5 -o /dev/null -w '%{http_code}' http://$1:8008/$2" 2>/dev/null)"
  printf '%s' "${code:-000}"
}

# Is <host> a member Patroni would actually accept as a switchover candidate?
#
# NOT the same question as `patroni_code <host> replica` = 200, and the
# difference cost a phase on 2026-09-09: after Phase III's partition, gcp-1
# answered /replica 200 while sitting on timeline 1 with no replication
# connection, and the switchover that followed failed with "503, Switchover
# failed". /replica reports lag against a position an unattached member cannot
# advance, so an unattached member looks perfectly healthy through it. The
# member's own /patroni document is where `replication_state` lives, and
# `streaming` is the observable form of "already holds the current history and
# is receiving the rest". Mirrors preflight.patroni_candidate_ready().
patroni_streaming() {  # patroni_streaming <host>
  remote "$CL_USER" "$CL_HOST" \
    "curl -s -m 5 http://$1:8008/patroni" 2>/dev/null \
    | grep -q '"replication_state": *"streaming"'
}

started_at=$(date -u +%s)

printf '%scrdblab — full experiment%s\n' "$B" "$N"
note "profile $PROFILE   engine $ENGINE   log $LOG"

# --- 1. the workstation -----------------------------------------------------

step "Checking the workstation"

[ -x "$CRDBLAB" ] || die "$CRDBLAB not found. Run:
    python3 -m venv .venv && .venv/bin/python -m pip install -e \".[dev]\""
ok "harness installed"

[ -f "$REPO/.env" ] || die ".env not found in $REPO (copy .env.example and set DB_URI)"

DB_URI="$(grep -E '^\s*DB_URI=' "$REPO/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
[ -n "$DB_URI" ] || die "DB_URI is not set in .env"

# DB_URI is used only for `crdblab capture` and for loading the working set
# (§3/§4) -- never for the measured phases, which resolve their own connection
# strings from crdblab/topology.py and --engine. A multi-host DB_URI is
# therefore fine here: it does not put the wide-area network on any measured
# path, and it is what lets loading and capture survive the primary being
# down (postgres wire-protocol clients, including `cockroach workload`, try
# the listed hosts in order).
#
# It must still name the `ycsb` database. `cockroach workload init ycsb`
# refuses any other name, so a URI pointing at `defaultdb` cannot have a
# loaded working set behind it.
case "$DB_URI" in
  */ycsb\?*|*/ycsb) ok "DB_URI names the ycsb database" ;;
  *) die "DB_URI must name the 'ycsb' database, not '$(echo "$DB_URI" | sed 's|.*/||; s|?.*||')'.
  'cockroach workload init ycsb' rejects any other database name, so a URI
  pointing elsewhere cannot have a loaded working set behind it." ;;
esac

command -v tailscale >/dev/null 2>&1 && {
  tailscale status >/dev/null 2>&1 && ok "tailscale up" || die "tailscale is not up on this machine"
}

# --- 2. resolve the topology from the package, not from a second copy --------

step "Resolving topology"

read -r GW_USER GW_HOST GW_REGION CL_USER CL_HOST < <("$PY" - <<'PYEOF'
from crdblab.topology import DEFAULT_TOPOLOGY as t, CLIENT_NODE as c
g = t.gateway
print(g.user, g.host, g.region, c.user, c.host)
PYEOF
) || die "could not resolve topology from crdblab.topology"
ok "gateway $GW_USER@$GW_HOST ($GW_REGION)   client $CL_USER@$CL_HOST"

# Every cluster member, gateway first, as "user host" pairs. Resolved once here
# because both the PostgreSQL health check (below) and the dead-mode restore
# backstop need to address nodes other than the gateway, and the SSH user
# differs per provider (root on Linode, ubuntu on GCP and Azure) -- pairing them
# at the source is what stops a `ssh $SOME_USER@$OTHER_HOST` mismatch.
CLUSTER_NODES="$("$PY" - <<'NODEEOF'
from crdblab.topology import DEFAULT_TOPOLOGY as t
ordered = [t.gateway] + [n for n in t.nodes if not n.gateway]
for n in ordered:
    print(n.user, n.host)
NODEEOF
)" || die "could not resolve the cluster node list"

# The gateway is declared in crdblab/topology.py, but DB_URI is hand-written in
# .env and nothing else reconciles the two. For PostgreSQL, this will usually point
# to 127.0.0.1 (local HAProxy), while for CockroachDB it will point to the cluster gateway.
DB_HOST="$(printf '%s' "$DB_URI" | sed -E 's|^[a-z]+://||; s|^[^@/]*@||; s|[:/?].*$||')"
ok "DB_URI names host $DB_HOST"

# Not fatal: DB_URI feeds loading and `crdblab capture` only, never a measured
# phase. But for PostgreSQL the only endpoint that follows Patroni's leader is
# the client node's local HAProxy, and a URI pinned at one cluster member will
# start failing writes the moment the leader moves -- which a chaos run makes
# it do on purpose.
if [ "$ENGINE" = "postgresql" ] && [ "$DB_HOST" != "127.0.0.1" ]; then
  warn "engine is postgresql but DB_URI names $DB_HOST, not 127.0.0.1 (the client node's HAProxy)."
  note "loading and 'crdblab capture' will not follow a Patroni failover; expected"
  note "postgresql://root:<password>@127.0.0.1:5000/ycsb?sslmode=disable (see instructions.md section 6)."
fi

# Unlike CockroachDB, which runs --insecure and accepts root with no password,
# Patroni bootstraps a pg_hba of `host all all 0.0.0.0/0 md5`: every TCP
# connection needs one. This is fatal rather than a warning because the
# alternative is discovering it in the middle of the load step, after the
# testbed checks have all passed.
if [ "$ENGINE" = "postgresql" ]; then
  # Asked, not assumed. A DB_URI that merely *has* a password tells us nothing:
  # the one that broke the 2026-09-08 run had one, and it was for a role whose
  # password was something else. This is a warning rather than fatal because
  # loading no longer depends on DB_URI (see the candidates above) -- only
  # `crdblab capture` does, and a sweep should not be blocked by a tool it is
  # not about to run.
  if remote "$CL_USER" "$CL_HOST" \
      "psql '$DB_URI' -tAc 'SELECT 1' >/dev/null 2>&1"; then
    ok "DB_URI authenticates against the cluster"
  else
    warn "DB_URI does not authenticate; 'crdblab capture' will fail (this sweep will not)."
    note "the roles created by bootstrap-patroni.tftpl are root/rootpassword and"
    note "admin/adminpassword; the superuser is postgres/postgrespassword. Expected:"
    note "  DB_URI=postgresql://root:rootpassword@127.0.0.1:5000/ycsb?sslmode=disable"
  fi
fi

# The seed and row count are read from the profile the sweep will actually use.
# Hardcoding them here would create a second source of truth for the one
# parameter whose mismatch is silent and flattering (D8).
read -r SEED INSERT_COUNT < <("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys
w = json.load(sys.stdin)["workload"]
print(w["seed"], w["insert_count"])
') || die "could not read seed/insert_count from profile '$PROFILE'"
ok "profile '$PROFILE': seed $SEED, insert_count $INSERT_COUNT"

CHAOS_TARGET="$("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys; print(json.load(sys.stdin)["chaos"]["target"])')"
# JOIN_HOST is any other cluster member, for the dead-mode restore below: the
# chaos target now defaults to the gateway itself (CHAOS_TARGET == gcp-1), so
# --join can no longer just be $GW_HOST -- that would tell a node being
# restarted to join itself, which is not a real join hint.
read -r CT_USER CT_HOST CT_LOCALITY JOIN_USER JOIN_HOST < <("$PY" - "$CHAOS_TARGET" <<'PYEOF'
import sys
from crdblab.topology import DEFAULT_TOPOLOGY as t
n = t.get(sys.argv[1])
peer = next(p for p in t.nodes if p.name != n.name)
print(n.user, n.host, n.locality, peer.user, peer.host)
PYEOF
) || die "could not resolve chaos target '$CHAOS_TARGET'"
# JOIN_USER is emitted alongside JOIN_HOST and is NOT interchangeable with
# CT_USER: the SSH user differs per provider (root on Linode, ubuntu on GCP
# and Azure), so `ssh $CT_USER@$JOIN_HOST` never connects and every command
# run through it returns nothing. That is exactly how the liveness poll below
# reported "0 live" on 2026-09-08 while the node was in fact already back.
note "chaos target $CT_HOST ($CT_LOCALITY); rejoin via $JOIN_USER@$JOIN_HOST"

# --- 3. the testbed ---------------------------------------------------------

step "Checking the testbed"

remote "$GW_USER" "$GW_HOST" true || die "cannot ssh to the gateway $GW_HOST"
remote "$CL_USER" "$CL_HOST" true || die "cannot ssh to the client $CL_HOST"
ok "ssh to gateway and client"

if [ "$ENGINE" = "cockroachdb" ]; then

# `cockroach` is bound only to its Tailscale IPv4 address
# (bootstrap-cockroachdb.tftpl: --listen-addr=$TS_IP:26257), but the bare
# hostname $GW_HOST does not reliably resolve to that address -- it depends
# on WHERE it is resolved. From this workstation, Tailscale's MagicDNS
# answers it correctly (that's what lets `remote()` ssh to the gateway at
# all). Resolved by the gateway node's OWN OS instead, GCP's
# project-internal search domain (*.c.<project>.internal) is consulted
# ahead of the tailnet's own (*.ts.net) in /etc/resolv.conf and answers
# first, handing back the node's internal RFC1918 address. Every
# self-referential `cockroach ... --host=$GW_HOST` issued BY the gateway
# (over ssh, below) therefore dials an address nothing listens on and
# reports "connection refused" against a cluster that is, in fact, fully
# live -- observed on experiment-20260909T204104Z.log: all 5 nodes healthy
# (`tailscale ip -4` on gcp-1 answers 100.79.193.22, and a node status query
# against that address lists all 5 as is_live=true), while `getent hosts
# crdb-gcp-1` run on that same node answers 10.5.0.2, which cockroach never
# bound. The wait added above for experiment-20260909T202501Z.log was
# correct but insufficient -- the loop waited its full 600s timeout on a
# cluster that was live the whole time, because "not yet 5" and "asking the
# wrong address" print identically. Resolve the gateway's own Tailscale
# address once here rather than trusting it to resolve its own name.
GW_TS_IP="$(remote "$GW_USER" "$GW_HOST" "tailscale ip -4" | tr -d ' \r\n')"
[ -n "$GW_TS_IP" ] || die "could not read $GW_HOST's Tailscale IPv4 address
  ('tailscale ip -4' over ssh returned nothing -- is tailscale up on $GW_HOST?)"

# This is a bounded *wait*, not a single reading -- the same fix applied to
# the Patroni health gate below, and needed for the same reason. A fresh
# `terraform apply` boots five nodes across three clouds on their own
# schedules; `cockroach node status` only lists nodes that have already found
# the cluster, so a cluster mid-bootstrap legitimately reads as fewer than 5
# for as long as the slowest node's cloud-init takes, not because anything is
# wrong. Read once, this failed exactly the way the Patroni gate once did
# (experiment-20260908T225729Z.log, "1 healthy member(s), expected 5"):
# experiment-20260909T202501Z.log died here with "cluster reports 0 node(s),
# expected 5" against a testbed that simply hadn't finished coming up.
# Waiting costs nothing once the cluster is already up (the first pass breaks
# immediately), and the bound still fails the run rather than hanging.
CRDB_HEALTH_WAIT_S=600
CRDB_HEALTH_POLL_S=10
crdb_health_deadline=$(( $(date -u +%s) + CRDB_HEALTH_WAIT_S ))
crdb_waited=0
while :; do
  LIVE=$(remote "$GW_USER" "$GW_HOST" \
    "cockroach node status --insecure --host=$GW_TS_IP:26257 --format=csv 2>/dev/null | tail -n +2 | wc -l" \
    | tr -d ' ')
  [ -n "$LIVE" ] || LIVE=0

  [ "$LIVE" = "5" ] && break
  [ "$(date -u +%s)" -ge "$crdb_health_deadline" ] && break

  if [ "$crdb_waited" = "0" ]; then
    note "cluster reports $LIVE/5 node(s) (still booting?);"
    note "waiting up to ${CRDB_HEALTH_WAIT_S}s for all 5"
    crdb_waited=1
  else
    note "  $LIVE/5 nodes live"
  fi
  sleep "$CRDB_HEALTH_POLL_S"
done

[ "$crdb_waited" = "1" ] && [ "$LIVE" = "5" ] \
  && note "all 5 nodes live after waiting"

[ "$LIVE" = "5" ] || die "cluster reports $LIVE node(s), expected 5, and did
  not reach 5 within ${CRDB_HEALTH_WAIT_S}s of waiting.
  If a previous 'dead' run left a node down, restart it (see instructions.md).
  If this is a fresh 'terraform apply', check cloud-init on the slow node(s)
  ('journalctl -u cloud-init' / /var/log/cloud-init-output.log there)."
ok "5 cluster nodes live"

# D7: the bootstrap can apply num_replicas and then fail on lease_preferences,
# leaving a healthy-looking cluster whose leaseholders are on another continent.
# It costs 12.3x throughput and no consistency check can detect it.
LEASE=$(remote "$GW_USER" "$GW_HOST" \
  "cockroach sql --insecure --host=$GW_TS_IP:26257 -e 'SHOW ZONE CONFIGURATION FROM DATABASE ycsb;' 2>/dev/null \
   | grep -o \"lease_preferences = '[^']*'\" || true")
case "$LEASE" in
  *"[[+region=$GW_REGION]"*)
    ok "lease preferences applied, headed by $GW_REGION  ${LEASE#*= }" ;;
  *"[[+region="*)
    # Not D7 -- the preferences are present, they simply name another region
    # first. That is worse than it looks: the cluster is healthy, every
    # consistency check passes, and the only symptom is that each operation
    # crosses to the leaseholder and back. From crdb-gcp-1 to crdb-linode-1 that
    # is ~20 ms added to a write path whose quorum floor is ~70 ms, which is a
    # ~30% inflation that would be attributed to replication rather than to a
    # misconfiguration.
    die "lease_preferences is applied but does not name the gateway's region first.
  observed: ${LEASE#*= }
  expected: the list to begin [+region=$GW_REGION], because the generator runs on
  $GW_HOST and a leaseholder elsewhere puts a wide-area hop on every operation.
  The provisioning bootstrap orders the fast triangle us-east, us-east1, us-west,
  which suited the previous gateway (crdb-linode-1, us-east). Re-order it on the
  live cluster with:
    cockroach sql --insecure --host=$GW_HOST:26257 -e \\
      \"ALTER RANGE default CONFIGURE ZONE USING lease_preferences =
        '[[+region=$GW_REGION], [+region=us-east], [+region=us-west]]';\"
  then allow a few seconds for the leases to transfer. terraform/scripts/
  bootstrap.tftpl needs the same re-ordering before the next 'terraform apply',
  or a fresh deployment will come back with the old order (instructions.md, section 2)." ;;
  *) die "lease_preferences is empty or unreadable on database 'ycsb'.
  The bootstrap raced: it applied num_replicas and then failed to apply the
  lease preference, so leaseholders may sit outside the fast triangle.
  Re-run 'terraform apply' or apply the zone configuration by hand (D7)." ;;
esac

else

# PostgreSQL/Patroni. `cockroach node status` and `SHOW ZONE CONFIGURATION`
# do not exist here, and the CockroachDB-specific pre-flight checks
# (leaseholder placement, server-config capture) are skipped inside the harness
# too (bench.py). The equivalent question -- "are all five members up, and is
# exactly one of them primary?" -- is answered by Patroni's own REST API on
# :8008, which is also what p4_chaos.resolve_patroni_primary() consults
# immediately before scheduling a fault. Asked from the client node so that a
# node unreachable from the workstation is not mistaken for a node that is down.
# This is a bounded *wait*, not a single reading, and the difference is the
# whole point. A Patroni member answers :8008/health with 503 for as long as it
# is still coming up -- taking its pg_basebackup from the primary, replaying
# WAL, catching up as a streaming replica -- and only flips to 200 once it is a
# healthy member of the cluster. On a freshly provisioned testbed that is a
# perfectly normal transient state, not a fault: the five nodes boot on three
# different clouds' schedules, and the four replicas cannot even begin their
# basebackup until the designated primary has taken the leader lock.
# Read once, the gate caught the cluster mid-bootstrap and aborted the sweep --
# experiment-20260908T225729Z.log died with "1 healthy member(s), expected 5"
# and all four replicas reporting 503, on a deployment that was coming up
# correctly and would have been complete minutes later. Waiting costs nothing
# when the cluster is already up (the first pass breaks immediately) and is the
# difference between a sweep that starts and one that has to be relaunched by
# hand. The bound still fails the run rather than hanging: a node that is
# genuinely dead never reaches 200 and PG_HEALTH_WAIT_S caps the wait.
PG_HEALTH_WAIT_S=600
PG_HEALTH_POLL_S=10
pg_health_deadline=$(( $(date -u +%s) + PG_HEALTH_WAIT_S ))
pg_waited=0
while :; do
  PG_LIVE=0
  PG_PRIMARIES=""
  PG_UNHEALTHY=""
  while read -r _u h; do
    [ -n "$h" ] || continue
    code="$(patroni_code "$h" health)"
    if [ "$code" = "200" ]; then
      PG_LIVE=$((PG_LIVE + 1))
    else
      PG_UNHEALTHY="$PG_UNHEALTHY $h:$code"
    fi
    [ "$(patroni_code "$h" primary)" = "200" ] && PG_PRIMARIES="$PG_PRIMARIES $h"
  done <<< "$CLUSTER_NODES"

  [ "$PG_LIVE" = "5" ] && break
  [ "$(date -u +%s)" -ge "$pg_health_deadline" ] && break

  if [ "$pg_waited" = "0" ]; then
    note "patroni:$PG_UNHEALTHY not healthy yet (503 = still bootstrapping);"
    note "waiting up to ${PG_HEALTH_WAIT_S}s for all 5 members"
    pg_waited=1
  else
    note "  $PG_LIVE/5 healthy;$PG_UNHEALTHY"
  fi
  sleep "$PG_HEALTH_POLL_S"
done

# Report what the wait cost, so a slow bootstrap is visible in the log rather
# than silently absorbed -- a cluster that needed 8 minutes to converge is
# worth knowing about even though it did converge.
[ "$pg_waited" = "1" ] && [ "$PG_LIVE" = "5" ] \
  && note "all 5 members healthy after waiting"

[ "$PG_LIVE" = "5" ] || die "patroni reports $PG_LIVE healthy member(s), expected 5,
  and did not reach 5 within ${PG_HEALTH_WAIT_S}s of waiting.
  Still unhealthy (host:http_code):$PG_UNHEALTHY
  A 503 that never clears is a member stuck in bootstrap -- check
  'journalctl -u patroni' on it; 000 means it is unreachable from the client
  node at all.
  If a previous 'dead' run left a node down, bring it back with
  'sudo -n systemctl start patroni' on that node (see instructions.md).
  If NO member is healthy on a freshly provisioned testbed, check that the
  unit actually started -- 'systemctl status patroni' reporting
  'Condition check resulted in ... being skipped' means the config is not at
  /etc/patroni/config.yml, which is the only path the packaged unit reads."
ok "5 patroni members healthy"

# bootstrap-patroni.tftpl now pins the primary onto the gateway, the same node
# CockroachDB's lease_preferences biases its leaseholder onto. What is asserted
# *here* is only that exactly one member claims to be primary: zero means no
# leader has been elected and every write fails, more than one means the REST
# answers disagree and the fault target cannot be resolved -- the same condition
# resolve_patroni_primary() refuses to guess through. Which node it is is
# repaired rather than asserted, further down ("Restoring the patroni primary
# to the gateway") and again by preflight.check_patroni_primary_placement, so a
# primary that has merely drifted after a chaos run is fixed instead of
# aborting the sweep.
PG_PRIMARY_COUNT="$(printf '%s' "$PG_PRIMARIES" | wc -w | tr -d ' ')"
case "$PG_PRIMARY_COUNT" in
  1) ok "patroni primary:$PG_PRIMARIES" ;;
  0) die "no patroni member answers 200 on :8008/primary -- no leader has been elected.
  Check 'patronictl list' and etcd on the cluster nodes; every write fails in
  this state, and 'crdblab chaos run' would have no fault target to resolve." ;;
  *) die "$PG_PRIMARY_COUNT patroni members answer 200 on :8008/primary:$PG_PRIMARIES
  That is a split brain as far as this harness can tell. Resolve it before
  measuring -- p4_chaos.resolve_patroni_primary() refuses to guess between them,
  and a benchmark taken across two primaries is not a measurement of anything." ;;
esac

fi

# --- 4. working set ---------------------------------------------------------

step "Working set"

# `cockroach workload init`/`cockroach sql --url` do NOT accept a PostgreSQL-
# style comma-separated multi-host URI -- unlike psql/libpq, CockroachDB's own
# CLI tools hand the whole host segment to Go's DNS resolver verbatim, so
# `host1:26257,host2:26257` fails as "no such host" rather than trying each in
# turn. (Observed 2026-09-08: exactly this error against a DB_URI written that
# way.) CockroachDB is a distributed database, so any live cluster member
# answers identically for loading or counting; the fallback here is therefore
# a genuine per-attempt retry across single-host URIs built from
# crdblab/topology.py, not a syntax the tool is trusted to parse itself. It
# only applies to the CockroachDB path -- PostgreSQL's DB_URI already points
# at the client node's local HAProxy (127.0.0.1:5000), which resolves the
# live primary on its own.
if [ "$ENGINE" = "postgresql" ]; then
  # The credentials for loading come from the harness, not from DB_URI, and are
  # therefore identical to the ones the sweep itself will use. Trusting DB_URI
  # here put the load and the measured phases on two separately maintained
  # copies of the same secret, which drifted the first time it mattered: a
  # `.env` carrying the old example line (`postgres:postgres`) passed every
  # check in this script -- including "DB_URI carries a password" -- and then
  # failed 90 s later inside `workload init` with `password authentication
  # failed for user "postgres"`, on a testbed that was entirely healthy.
  # DB_URI is still what `crdblab capture` uses, which is why it is checked
  # below rather than ignored.
  DB_CANDIDATES=("$("$PY" - <<'PYEOF'
from crdblab.config import Settings, pg_generator_dsn
print(pg_generator_dsn("ycsb", Settings.from_env().pg_password))
PYEOF
  )") || die "could not build the PostgreSQL loading DSN"
elif [ "$DB_HOST" = "127.0.0.1" ]; then
  DB_CANDIDATES=("$DB_URI")
else
  read -r DB_USER DB_PATH DB_QUERY < <("$PY" - "$DB_URI" <<'PYEOF'
import sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1])
print(u.username or "root", u.path.lstrip("/") or "ycsb", u.query or "sslmode=disable")
PYEOF
  ) || die "could not parse DB_URI"
  CANDIDATE_HOSTS="$("$PY" - <<'PYEOF'
from crdblab.topology import DEFAULT_TOPOLOGY as t
ordered = [t.gateway] + [n for n in t.nodes if not n.gateway]
print(" ".join(n.host for n in ordered))
PYEOF
  )"
  DB_CANDIDATES=()
  for h in $CANDIDATE_HOSTS; do
    DB_CANDIDATES+=("postgresql://$DB_USER@$h:26257/$DB_PATH?$DB_QUERY")
  done
fi

# Runs a command template against each candidate URI in turn, stopping at the
# first that succeeds. The template contains the literal token URI_PLACEHOLDER
# where the candidate URI goes; substituted with plain bash string replacement
# so no quoting or environment-variable indirection is needed.
URI_PLACEHOLDER="__DB_URI__"

try_each_host() {  # try_each_host <description> <command-template>
  local desc="$1" template="$2" uri cmd out
  for uri in "${DB_CANDIDATES[@]}"; do
    cmd="${template//$URI_PLACEHOLDER/$uri}"
    out="$(remote "$CL_USER" "$CL_HOST" "$cmd")" && { printf '%s' "$out"; return 0; }
    note "$desc against $(printf '%s' "$uri" | sed -E 's|^[a-z]+://[^@]*@||; s|[:/?].*$||') failed, trying next host"
  done
  return 1
}

# `cockroach workload init` cannot be used against PostgreSQL at all. Its first
# statement is `CREATE DATABASE IF NOT EXISTS <db>`, which is CockroachDB
# syntax -- PostgreSQL has no IF NOT EXISTS for CREATE DATABASE and fails with
# `syntax error at or near "NOT"` -- and nothing suppresses it: `--data-loader
# NONE`, which only creates the schema, issues it too. `--drop` is unusable for
# a second, independent reason (it asks the server to DROP DATABASE the
# connection is currently inside), and `--families` for a third (COLUMN FAMILY
# is CockroachDB DDL).
#
# So for PostgreSQL the schema is created here and the rows are loaded by the
# *generator itself*, running insert-only. That last part is the important one:
# the keys are derived by the generator from the row index, so a hand-written
# loader would have to reimplement that derivation, and a keyspace that differs
# from the one the sweep addresses is D8 exactly -- every operation matches
# nothing, and the run reports its best-ever throughput. Letting the generator
# insert its own keys makes the two keyspaces the same object rather than two
# implementations that agree today. Verified against this testbed: a sweep over
# a table loaded this way reported a row-match rate of 1.0000.
#
# `--max-ops` stops the load at the requested count. It overshoots by up to
# --concurrency rows, because operations already in flight still complete --
# 5,063 rows for a requested 5,000 at C=64. Those extra rows have indices at or
# above --insert-count, so the sweep never addresses them; the count is
# reported below rather than silently accepted.
LOAD_CONCURRENCY=64

# The schema `cockroach workload init` would have created: one key column and
# ten value columns, which is what the generator's prepared statements expect
# (`SELECT field8 FROM usertable WHERE ycsb_key = $1`). No COLUMN FAMILY
# clauses, which is the only thing --families=false would have changed.
PG_USERTABLE_DDL="DROP TABLE IF EXISTS usertable;
CREATE TABLE usertable (
  ycsb_key VARCHAR(255) PRIMARY KEY,
  field0 TEXT, field1 TEXT, field2 TEXT, field3 TEXT, field4 TEXT,
  field5 TEXT, field6 TEXT, field7 TEXT, field8 TEXT, field9 TEXT
);"

if [ "$ENGINE" = "postgresql" ]; then
  # DDL and row counting go straight to the primary: psql speaks plain libpq
  # and needs neither pgbouncer nor HAProxy.
  PG_ADMIN_DSN="$("$PY" - <<'PYEOF'
from crdblab.config import Settings, pg_direct_dsn
from crdblab.topology import DEFAULT_TOPOLOGY
print(pg_direct_dsn(DEFAULT_TOPOLOGY, "ycsb", Settings.from_env().pg_password))
PYEOF
  )" || die "could not build the PostgreSQL admin DSN"
fi

load_data() {
  if [ "$ENGINE" = "postgresql" ]; then
    note "creating usertable (workload init cannot run against PostgreSQL)"
    remote "$CL_USER" "$CL_HOST" \
      "psql '$PG_ADMIN_DSN' -v ON_ERROR_STOP=1 -c \"$PG_USERTABLE_DDL\"" >/dev/null \
      || die "could not create usertable"
    note "loading $INSERT_COUNT rows @ seed $SEED with the generator, insert-only"
    remote "$CL_USER" "$CL_HOST" \
      "cockroach workload run ycsb --workload=CUSTOM \
         --insert-freq=1 --read-freq=0 --update-freq=0 \
         --request-distribution=uniform \
         --seed=$SEED --insert-count=0 --insert-start=0 \
         --concurrency=$LOAD_CONCURRENCY --max-ops=$INSERT_COUNT --duration=0 \
         --display-every=30s '${DB_CANDIDATES[0]}'" \
      || die "the insert-only load failed"
    return
  fi

  note "loading $INSERT_COUNT rows @ seed $SEED on database (bulk init; minutes, scales with the row count)"
  try_each_host "workload init" \
    "cockroach workload init ycsb --drop --seed=$SEED --insert-count=$INSERT_COUNT '$URI_PLACEHOLDER'" \
    >/dev/null \
    || die "workload init failed against every candidate host: ${DB_CANDIDATES[*]}"
}

count_rows() {
  # `cockroach sql` is a CockroachDB client; it is not a general postgres
  # client and does not speak to a Patroni cluster. psql is installed on the
  # client node by bootstrap-client.tftpl for exactly this reason, and is what
  # p4_chaos.py already uses to create the RPO audit table on PostgreSQL. The
  # table is `ycsb.usertable` on CockroachDB (database.table) and `usertable`
  # in the public schema of database `ycsb` on PostgreSQL, which the DSN
  # already selects.
  if [ "$ENGINE" = "postgresql" ]; then
    remote "$CL_USER" "$CL_HOST" \
      "psql '$PG_ADMIN_DSN' -tAc 'SELECT count(*) FROM usertable;' 2>/dev/null | tail -1" \
      | tr -d ' \r'
  else
    try_each_host "row count" \
      "cockroach sql --url '$URI_PLACEHOLDER' --format=csv -e 'SELECT count(*) FROM ycsb.usertable;' 2>/dev/null | tail -1" \
      | tr -d ' \r'
  fi
}

if [ "$SKIP_LOAD" -eq 1 ]; then
  warn "--skip-load: not reloading. The seed behind the existing data is NOT verified here;"
  note "pre-flight's row-match probe will catch a mismatch, but only after a tier has run."
else
  load_data
fi

rows=$(count_rows || true)
case "$rows" in
  ''|*[!0-9]*) die "could not count rows on database (got: '$rows'). If it reports the table
is offline, the import is still replicating -- wait and re-run with --skip-load." ;;
  *) [ "$rows" -ge "$INSERT_COUNT" ] \
       && ok "database: $rows rows" \
       || die "database has $rows rows, expected $INSERT_COUNT" ;;
esac

# --- 5. the four phases -----------------------------------------------------

phase() {  # phase <label> <crdblab args...>
  local label="$1"; shift
  step "$label"
  "$CRDBLAB" "$@" || die "$label failed. Nothing after this point has run."
}

phase "Phase I — network substrate"        "${ENGINE_ARGS[@]}" net probe --profile "$PROFILE"
phase "Phase II — benchmark, five-node cluster" "${ENGINE_ARGS[@]}" bench --profile "$PROFILE"

if [ "$RUN_CHAOS" -eq 1 ]; then
  phase "Phase III — heal-able partition"  "${ENGINE_ARGS[@]}" chaos run --mode recover --profile "$PROFILE"

  # `dead` runs last because it leaves the target down. The harness does not
  # restore it: the fault is real, and restarting is an operator action.
  phase "Phase IV — process kill"          "${ENGINE_ARGS[@]}" chaos run --mode dead    --profile "$PROFILE"

  # The chaos phase now restores the target itself, immediately after it has
  # finished deriving every artefact, and records the restart in events.json.
  # This block stays as a backstop for the case where the phase could not do it
  # -- it aborted, or the operator ran `crdblab chaos run` by hand from an older
  # revision -- and is a no-op when the node is already back.
  if [ "$ENGINE" = "cockroachdb" ]; then

    step "Confirming $CT_HOST is back"
    # CockroachDB is started by cloud-init with --background, not as a systemd
    # unit, so there is no service to start and a reboot would not bring it back.
    # The memory flags are NOT optional: omitting them takes the 128 MiB default
    # and silently reintroduces the block-cache asymmetry of D9.
    #
    # The three redirections on the REMOTE side of the command are what stop this
    # step from hanging, and they are not the same thing as the local
    # `>/dev/null 2>&1` after it. `--background` forks cockroach and returns, but
    # the forked process inherits the remote shell's stdout and stderr -- which are
    # the SSH channel itself. ssh does not close a session while any process still
    # holds those pipes open, so it waits for the *database* to exit: the restore
    # blocks until the connection eventually times out, and a sweep that has
    # already finished measuring appears to hang for tens of minutes at the last
    # step. Observed on 2026-09-05, where it added ~50 minutes to a 75-minute run.
    # Redirecting the remote fds detaches the daemon from the channel so ssh can
    # return immediately. Local redirection cannot do this; it only discards what
    # the client prints.
    # `sudo -n` is required, not defensive: /var/lib/cockroach is root-owned and
    # $CT_USER is `ubuntu` on the GCP and Azure nodes, so an unprivileged
    # `cockroach start` cannot open the store. Same omission that made
    # `killall -9 cockroach` a silent no-op before 081437c.
    remote "$CT_USER" "$CT_HOST" "TS_IP=\$(tailscale ip -4); sudo -n cockroach start --insecure \
        --store=/var/lib/cockroach \
        --listen-addr=\$TS_IP:26257 --advertise-addr=\$TS_IP:26257 \
        --locality=$CT_LOCALITY \
        --cache=0.25 --max-sql-memory=0.25 \
        --join=$JOIN_HOST:26257 --background </dev/null >/dev/null 2>&1" >/dev/null 2>&1 || true

    # Now that the restore returns promptly, the poll has to do its own waiting.
    # It previously inherited the hang as an accidental grace period: six
    # back-to-back status calls take about ten seconds, which is less than a node
    # needs to rejoin and be marked live, so without a sleep this would report "has
    # not rejoined" on a node that was merely still starting.
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      sleep 5
      # Asked of a SURVIVOR, never of $GW_HOST. The gateway is the chaos target
      # on this testbed, so polling it asks the node that was just killed whether
      # it is alive: the query fails, `wc -l` returns 0, and the step reported
      # "has not rejoined (0 live)" on every dead-mode run regardless of the
      # truth. Observed 2026-09-08.
      LIVE=$(remote "$JOIN_USER" "$JOIN_HOST" \
        "cockroach node status --insecure --host=$JOIN_HOST:26257 --format=csv 2>/dev/null | tail -n +2 | wc -l" \
        | tr -d ' ' || echo 0)
      [ "$LIVE" = "5" ] && break
    done
    [ "$LIVE" = "5" ] \
      && ok "$CT_HOST rejoined; 5 nodes live" \
      || warn "$CT_HOST has not rejoined ($LIVE live). Restart it before measuring again."

  else

  # PostgreSQL/Patroni. The chaos target should be $CT_HOST now that the primary
  # is pinned there, but this backstop still sweeps every member rather than one
  # named node: p4_chaos.resolve_patroni_primary() resolves the target live and
  # records it only in the run's events.json, so a run that faulted somewhere
  # else -- because a switchover had not taken, or the profile was edited --
  # must still be cleaned up. Sweeping is also what makes it a no-op when the
  # phase already restored the target itself.
  step "Confirming every patroni member is back"
  while read -r u h; do
    [ -n "$h" ] || continue
    code="$(patroni_code "$h" health)"
    [ "$code" = "200" ] && continue
    note "$h: /health returned $code, starting patroni"
    # Unlike CockroachDB (started by cloud-init with --background, no unit),
    # Patroni is a systemd service on these nodes, so there is a unit to start
    # and no daemon to detach from the SSH channel. `sudo -n` is still
    # required: the SSH user is `ubuntu` on the GCP and Azure nodes and the
    # unit is root-owned -- the same omission that made `killall -9 cockroach`
    # a silent no-op before 081437c. This is the identical payload
    # p4_chaos.restore_target() uses for this engine.
    remote "$u" "$h" "sudo -n systemctl start patroni" >/dev/null 2>&1 || true
  done <<< "$CLUSTER_NODES"

  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 5
    PG_LIVE=0
    while read -r _u h; do
      [ -n "$h" ] || continue
      [ "$(patroni_code "$h" health)" = "200" ] && PG_LIVE=$((PG_LIVE + 1))
    done <<< "$CLUSTER_NODES"
    [ "$PG_LIVE" = "5" ] && break
  done
  [ "$PG_LIVE" = "5" ] \
    && ok "all 5 patroni members healthy" \
    || warn "$PG_LIVE/5 patroni members healthy. Restart the rest before measuring again."

  # Put the primary back on the gateway. CockroachDB does this for itself --
  # lease_preferences pulls the leaseholder back to gcp-1, which is what
  # check_leaseholder_placement's settle window waits for -- but Patroni has no
  # equivalent: failover_priority biases who *wins* an election and never
  # starts one, so after a chaos run the primary stays wherever the failover
  # left it, indefinitely. Left alone, the next phase would fault a different
  # node than this one did, and than either CockroachDB phase did.
  #
  # This duplicates preflight.check_patroni_primary_placement deliberately: the
  # harness repairs it too, and would catch this, but repairing here means the
  # cluster is left in the state the next run expects rather than the next run
  # having to fix it -- the same reason the dead-mode restore backstop above
  # exists alongside p4_chaos.restore_target().
  step "Restoring the patroni primary to the gateway"
  # The user is captured with the host, not read from the loop variable after
  # the loop: the SSH user differs per provider (root on Linode, ubuntu on GCP
  # and Azure), so a trailing $u would name the last node's user against the
  # primary's host -- the mismatch CLUSTER_NODES is paired at the source to
  # prevent.
  PG_PRIMARY=""
  PG_PRIMARY_USER=""
  while read -r u h; do
    [ -n "$h" ] || continue
    if [ "$(patroni_code "$h" primary)" = "200" ]; then
      PG_PRIMARY="$h"
      PG_PRIMARY_USER="$u"
    fi
  done <<< "$CLUSTER_NODES"

  GW_LINE="$(printf '%s\n' "$CLUSTER_NODES" | head -1)"
  GW_U="$(printf '%s' "$GW_LINE" | awk '{print $1}')"
  GW_H="$(printf '%s' "$GW_LINE" | awk '{print $2}')"

  if [ -z "$PG_PRIMARY" ]; then
    warn "no patroni member reports itself primary; cannot restore placement."
  elif [ "$PG_PRIMARY" = "$GW_H" ]; then
    ok "patroni primary is already $GW_H"
  else
    note "patroni primary is $PG_PRIMARY, not $GW_H; switching over"
    # Wait for the candidate to be streaming first. After a `recover` fault
    # against the primary the demoted node has to be rewound or re-cloned
    # before it holds the current timeline, and asking for a handover it will
    # refuse achieves nothing but a confusing error. Bounded, and a candidate
    # that never arrives leaves the warning below rather than hanging: this is
    # a backstop, and the next run's pre-flight repairs placement properly.
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      patroni_streaming "$GW_H" && break
      sleep 10
    done
    patroni_streaming "$GW_H" \
      || note "$GW_H is not streaming yet; asking anyway, the pre-flight will retry"
    # A switchover is a controlled handover, not a fault: Patroni demotes the
    # old primary only once the candidate has caught up, so no writes are lost.
    # Named --candidate so Patroni cannot promote some other node instead.
    remote "$PG_PRIMARY_USER" "$PG_PRIMARY" \
      "sudo -n patronictl -c /etc/patroni/config.yml switchover \
         --leader $PG_PRIMARY --candidate $GW_H --force" >/dev/null 2>&1 || true
    sleep 10
    [ "$(patroni_code "$GW_H" primary)" = "200" ] \
      && ok "patroni primary restored to $GW_H" \
      || warn "switchover to $GW_H did not take; the next run's pre-flight will retry."
  fi

  fi
fi

# --- 6. validate ------------------------------------------------------------

step "Validating every run"

# Globbed on metrics.csv rather than on directories: a Phase I run records
# network.csv under a different schema and has no workload samples to check.
failed=0
for m in "$REPO"/runs/*/metrics.csv; do
  [ -e "$m" ] || continue
  d="$(dirname "$m")"
  if "$CRDBLAB" validate "$d" >/dev/null 2>&1; then
    ok "$(basename "$d")"
  else
    printf '%s  FAIL%s  %s\n' "$R" "$N" "$(basename "$d")"
    failed=$((failed + 1))
  fi
done
[ "$failed" -eq 0 ] || die "$failed run(s) failed validation and must not be used for figures"

# --- 7. analysis and figures ------------------------------------------------

latest() { ls -1d "$REPO"/runs/*_"$1" 2>/dev/null | tail -1; }

P1="$(latest p1-network)"; P2="$(latest bench_cluster)"
P4R="$(latest p4-chaos-recover)"; P4D="$(latest p4-chaos-dead)"

step "Analysis"
[ -n "$P2" ] && "$CRDBLAB" analyze steady-state "$P2"
# Note: For dual-engine comparison, run the script for both engines separately,
# then use: crdblab analyze engine-comparison --crdb <CRDB_RUN> --pg <PG_RUN>
[ -n "$P4R" ] && "$CRDBLAB" analyze resilience "$P4R"
[ -n "$P4D" ] && "$CRDBLAB" analyze resilience "$P4D"

step "Figures"
FIG_ARGS=()
[ -n "$P1" ]  && FIG_ARGS+=(--network  "$(basename "$P1")")
[ -n "$P2" ]  && FIG_ARGS+=(--cluster "$(basename "$P2")")
# BOTH fault classes, because there is one timeline figure per class and
# `--chaos` is a pin, not a filter. Passing only the dead run left
# fig6_resilience_timeline_recover untouched, so it kept whatever data the last
# run that did draw it had used -- and after a redeploy that is a figure from a
# different cluster sitting in the same directory as five from this one, with
# nothing about either file saying so. Observed on 2026-09-05: five figures dated
# the 6th beside one dated the 4th, drawn from the pre-move Linode gateway.
[ -n "$P4R" ] && FIG_ARGS+=(--chaos    "$(basename "$P4R")")
[ -n "$P4D" ] && FIG_ARGS+=(--chaos    "$(basename "$P4D")")
"$CRDBLAB" report figures "${FIG_ARGS[@]}"

# --- done -------------------------------------------------------------------

elapsed=$(( $(date -u +%s) - started_at ))
step "Done in $((elapsed / 60))m $((elapsed % 60))s"
# No $P3: the pre-rearchitecture design had a `p3_cluster` run, and the
# variable was never assigned after that phase was folded into `bench`. Under
# `set -u` the stale reference aborted the script at the final summary -- after
# every measurement and figure was already written, so it cost nothing but the
# summary and a non-zero exit. Phases are P1, P2 (bench), P4R and P4D.
for r in "$P1" "$P2" "$P4R" "$P4D"; do
  [ -n "$r" ] && note "$(basename "$r")"
done
note "figures/  (PNG at >=4K, with an SVG beside each; filenames carry engine/profile/run)"
note "log $LOG"

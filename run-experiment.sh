#!/usr/bin/env bash
# run-experiment.sh: measure every phase end to end. Run with --help for usage;
# full documentation is in docs-app/index.html.
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

# stdout is teed to a log, so force Python to line-buffer its live progress.
export PYTHONUNBUFFERED=1

# --- output -----------------------------------------------------------------

# CRDBLAB_COLOR=1 keeps the colours when stdout is a pipe -- the pipeline TUI
# reads this script's output through one and renders the colours itself.
if [ -t 1 ] || [ "${CRDBLAB_COLOR:-0}" = "1" ]; then
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
  cat <<'USAGE'
run-experiment.sh: measure all four phases end to end.

With no arguments on a terminal, this launches the full two-engine pipeline
(pipeline/run_all.py): deploy CockroachDB, measure, destroy and clean the
Tailscale devices, then the same for PostgreSQL/Patroni, then the insights.

With arguments it measures the one engine currently deployed. Preconditions:
`terraform apply` has completed, cloud-init has finished on every node and
Tailscale is up on this machine. DB_URI is derived from --engine (override
with DB_URI_COCKROACHDB / DB_URI_POSTGRESQL in the environment or .env).
It stops at the first failed check.

  ./run-experiment.sh                            # full two-engine pipeline (TUI)
  ./run-experiment.sh --profile thesis-extended  # one engine, full sweep, ~75 min
  ./run-experiment.sh --smoke                    # harness self-test, ~14 min
  ./run-experiment.sh --ask                      # one engine, asks for the options
  ./run-experiment.sh --skip-load                # working set already loaded
  ./run-experiment.sh --no-chaos                 # phases I-II only
  ./run-experiment.sh --engine postgresql        # measure the PostgreSQL deployment

--engine names the engine currently deployed; it does not deploy anything.
USAGE
  exit 0
}
# No arguments on a terminal: run the full pipeline, which calls back with arguments.
if [ $# -eq 0 ] && [ -t 0 ] && [ -t 1 ]; then
  [ -x "$PY" ] || die "$PY not found. Run:
    python3 -m venv .venv && .venv/bin/python -m pip install -e \".[dev]\""
  exec "$PY" "$REPO/pipeline/run_all.py"
fi

ASK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)   PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    --engine)    ENGINE="${2:?--engine needs a value}"; shift 2 ;;
    --smoke)     PROFILE="smoke"; shift ;;
    --skip-load) SKIP_LOAD=1; shift ;;
    --no-chaos)  RUN_CHAOS=0; shift ;;
    --ask)       ASK=1; shift ;;
    -h|--help)   usage ;;
    *)           die "unknown argument: $1 (try --help)" ;;
  esac
done

if [ "$ASK" -eq 1 ] && [ -t 0 ]; then
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

# --engine is a top-level crdblab flag, so it must precede the subcommand.
ENGINE_ARGS=(--engine "$ENGINE")

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/experiment-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

SSH_OPTS=(-q -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o BatchMode=yes -o ConnectTimeout=20)
# -n stops ssh swallowing the rest of a `while read` loop's input. Host keys are
# not checked because the testbed is rebuilt and addresses are reused.

remote() {  # remote <user> <host> <command>
  ssh "${SSH_OPTS[@]}" "$1@$2" "$3"
}

# HTTP status of a Patroni REST endpoint, asked from the client node. Always prints
# one token (`000` when unreachable).
patroni_code() {  # patroni_code <host> <endpoint>
  local code
  code="$(remote "$CL_USER" "$CL_HOST" \
    "curl -s -m 5 -o /dev/null -w '%{http_code}' http://$1:8008/$2" 2>/dev/null)"
  printf '%s' "${code:-000}"
}

# Is <host> streaming from the leader? /replica 200 alone does not prove it.
# Mirrors preflight.patroni_candidate_ready().
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

# DB_URI_<ENGINE> from the environment or .env, else crdblab.config.default_db_uri().
# It is exported, and crdblab never lets .env override the environment.
env_value() {  # env_value <name>: environment first, then .env
  local v="${!1:-}"
  if [ -z "$v" ] && [ -f "$REPO/.env" ]; then
    v="$(grep -E "^\s*$1=" "$REPO/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
  fi
  printf '%s' "$v"
}
DB_URI_VAR="DB_URI_$(printf '%s' "$ENGINE" | tr '[:lower:]' '[:upper:]')"
DB_URI="$(env_value "$DB_URI_VAR")"
if [ -n "$DB_URI" ]; then
  ok "DB_URI from $DB_URI_VAR"
else
  DB_URI="$(PG_PASSWORD="$(env_value PG_PASSWORD)" "$PY" - "$ENGINE" <<'PYEOF'
import os, sys
from crdblab.config import DEFAULT_PG_PASSWORD, default_db_uri
from crdblab.topology import DEFAULT_TOPOLOGY
print(default_db_uri(sys.argv[1], DEFAULT_TOPOLOGY, os.environ.get("PG_PASSWORD") or DEFAULT_PG_PASSWORD))
PYEOF
  )" || die "could not derive DB_URI for engine $ENGINE"
  ok "DB_URI derived for $ENGINE"
fi
if [ -f "$REPO/.env" ] && grep -qE '^\s*DB_URI=' "$REPO/.env"; then
  note "ignoring DB_URI in .env; it is now derived per engine (see .env.example)"
fi
export DB_URI
note "$(printf '%s' "$DB_URI" | sed -E 's|(://[^:@/]*:)[^@]*@|\1***@|')"

# DB_URI is only used for loading and `crdblab capture`, never a measured phase,
# and must name the `ycsb` database (`workload init ycsb` requires it).
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

# Every cluster member, gateway first, as "user host" pairs (the SSH user
# differs per provider).
CLUSTER_NODES="$("$PY" - <<'NODEEOF'
from crdblab.topology import DEFAULT_TOPOLOGY as t
ordered = [t.gateway] + [n for n in t.nodes if not n.gateway]
for n in ordered:
    print(n.user, n.host)
NODEEOF
)" || die "could not resolve the cluster node list"

# Host named by DB_URI: 127.0.0.1 (HAProxy) for PostgreSQL, a cluster node for CockroachDB.
DB_HOST="$(printf '%s' "$DB_URI" | sed -E 's|^[a-z]+://||; s|^[^@/]*@||; s|[:/?].*$||')"
ok "DB_URI names host $DB_HOST"

# Only the client node's HAProxy follows a Patroni failover; warn otherwise.
if [ "$ENGINE" = "postgresql" ] && [ "$DB_HOST" != "127.0.0.1" ]; then
  warn "engine is postgresql but DB_URI names $DB_HOST, not 127.0.0.1 (the client node's HAProxy)."
  note "loading and 'crdblab capture' will not follow a Patroni failover; expected"
  note "postgresql://root:<password>@127.0.0.1:5000/ycsb?sslmode=disable"
fi

if [ "$ENGINE" = "postgresql" ]; then
  # Patroni needs a password on every TCP connection. Only `capture` uses
  # DB_URI, so a failure here is a warning.
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

# Seed and row count come from the profile, so load and sweep cannot disagree.
read -r SEED INSERT_COUNT < <("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys
w = json.load(sys.stdin)["workload"]
print(w["seed"], w["insert_count"])
') || die "could not read seed/insert_count from profile '$PROFILE'"
ok "profile '$PROFILE': seed $SEED, insert_count $INSERT_COUNT"

CHAOS_TARGET="$("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys; print(json.load(sys.stdin)["chaos"]["target"])')"
# JOIN_HOST is another cluster member, for the dead-mode restore (the target
# is the gateway itself, which cannot be its own join hint).
read -r CT_USER CT_HOST CT_LOCALITY JOIN_USER JOIN_HOST < <("$PY" - "$CHAOS_TARGET" <<'PYEOF'
import sys
from crdblab.topology import DEFAULT_TOPOLOGY as t
n = t.get(sys.argv[1])
peer = next(p for p in t.nodes if p.name != n.name)
print(n.user, n.host, n.locality, peer.user, peer.host)
PYEOF
) || die "could not resolve chaos target '$CHAOS_TARGET'"
note "chaos target $CT_HOST ($CT_LOCALITY); rejoin via $JOIN_USER@$JOIN_HOST"

# --- 3. the testbed ---------------------------------------------------------

step "Checking the testbed"

remote "$GW_USER" "$GW_HOST" true || die "cannot ssh to the gateway $GW_HOST"
remote "$CL_USER" "$CL_HOST" true || die "cannot ssh to the client $CL_HOST"
ok "ssh to gateway and client"

if [ "$ENGINE" = "cockroachdb" ]; then

# cockroach listens only on its Tailscale IP, and the gateway may resolve its own
# hostname to an internal address, so commands run on it use the Tailscale IP.
GW_TS_IP="$(remote "$GW_USER" "$GW_HOST" "tailscale ip -4" | tr -d ' \r\n')"
[ -n "$GW_TS_IP" ] || die "could not read $GW_HOST's Tailscale IPv4 address
  ('tailscale ip -4' over ssh returned nothing -- is tailscale up on $GW_HOST?)"

# Wait (bounded) for all 5 nodes: a fresh deploy boots on three clouds' schedules.
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
  If a previous 'dead' run left a node down, restart it (see docs-app/index.html).
  If this is a fresh 'terraform apply', check cloud-init on the slow node(s)
  ('journalctl -u cloud-init' / /var/log/cloud-init-output.log there)."
ok "5 cluster nodes live"

# The bootstrap can apply num_replicas but fail on lease_preferences, leaving
# leaseholders on another continent while the cluster looks healthy.
LEASE=$(remote "$GW_USER" "$GW_HOST" \
  "cockroach sql --insecure --host=$GW_TS_IP:26257 -e 'SHOW ZONE CONFIGURATION FROM DATABASE ycsb;' 2>/dev/null \
   | grep -o \"lease_preferences = '[^']*'\" || true")
case "$LEASE" in
  *"[[+region=$GW_REGION]"*)
    ok "lease preferences applied, headed by $GW_REGION  ${LEASE#*= }" ;;
  *"[[+region="*)
    # Present but not headed by the gateway region: every operation pays an
    # extra hop to the leaseholder.
    die "lease_preferences is applied but does not name the gateway's region first.
  observed: ${LEASE#*= }
  expected: the list to begin [+region=$GW_REGION], because the generator runs on
  $GW_HOST and a leaseholder elsewhere puts a wide-area hop on every operation.
  Re-order it on the live cluster with:
    cockroach sql --insecure --host=$GW_HOST:26257 -e \\
      \"ALTER RANGE default CONFIGURE ZONE USING lease_preferences =
        '[[+region=$GW_REGION], [+region=us-east], [+region=us-west]]';\"
  then allow a few seconds for the leases to transfer, and check the order in
  terraform/scripts/bootstrap-cockroachdb.tftpl before the next 'terraform apply'." ;;
  *) die "lease_preferences is empty or unreadable on database 'ycsb'.
  The bootstrap raced: it applied num_replicas and then failed to apply the
  lease preference, so leaseholders may sit outside the fast triangle.
  Re-run 'terraform apply' or apply the zone configuration by hand." ;;
esac

else

# PostgreSQL/Patroni: all five members must answer :8008/health 200. Bounded wait:
# replicas answer 503 while they take their basebackup on a fresh deploy.
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

[ "$pg_waited" = "1" ] && [ "$PG_LIVE" = "5" ] \
  && note "all 5 members healthy after waiting"

[ "$PG_LIVE" = "5" ] || die "patroni reports $PG_LIVE healthy member(s), expected 5,
  and did not reach 5 within ${PG_HEALTH_WAIT_S}s of waiting.
  Still unhealthy (host:http_code):$PG_UNHEALTHY
  A 503 that never clears is a member stuck in bootstrap -- check
  'journalctl -u patroni' on it; 000 means it is unreachable from the client
  node at all.
  If a previous 'dead' run left a node down, bring it back with
  'sudo -n systemctl start patroni' on that node.
  If NO member is healthy on a freshly provisioned testbed, check that the
  unit actually started -- 'systemctl status patroni' reporting
  'Condition check resulted in ... being skipped' means the config is not at
  /etc/patroni/config.yml, which is the only path the packaged unit reads."
ok "5 patroni members healthy"

# Exactly one primary must exist. Which node it is gets repaired later (and by
# pre-flight), so a primary that drifted after a chaos run is not fatal.
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

# CockroachDB's CLI cannot parse multi-host URIs, so it tries single-host URIs
# from crdblab/topology.py one by one. PostgreSQL loads through pgbouncer.
if [ "$ENGINE" = "postgresql" ]; then
  # Load with the harness's own credentials, the same ones the sweep uses.
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

# Run a command template (URI_PLACEHOLDER marks the URI) against each candidate
# until one succeeds.
URI_PLACEHOLDER="__DB_URI__"

try_each_host() {  # try_each_host <description> <command-template>
  local desc="$1" template="$2" uri cmd out
  for uri in "${DB_CANDIDATES[@]}"; do
    cmd="${template//$URI_PLACEHOLDER/$uri}"
    out="$(remote "$CL_USER" "$CL_HOST" "$cmd")" && { printf '%s' "$out"; return 0; }
    # To stderr: callers capture stdout as the result (e.g. a row count).
    note "$desc against $(printf '%s' "$uri" | sed -E 's|^[a-z]+://[^@]*@||; s|[:/?].*$||') failed, trying next host" >&2
  done
  return 1
}

# `workload init` cannot target PostgreSQL (CockroachDB-only DDL), so the table is
# created here and the generator loads its own keys insert-only.
LOAD_CONCURRENCY=64

# The schema `workload init` would create (key plus ten fields), minus column families.
PG_USERTABLE_DDL="DROP TABLE IF EXISTS usertable;
CREATE TABLE usertable (
  ycsb_key VARCHAR(255) PRIMARY KEY,
  field0 TEXT, field1 TEXT, field2 TEXT, field3 TEXT, field4 TEXT,
  field5 TEXT, field6 TEXT, field7 TEXT, field8 TEXT, field9 TEXT
);"

if [ "$ENGINE" = "postgresql" ]; then
  # DDL and row counts go straight to the primary over libpq.
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
  # psql for PostgreSQL, `cockroach sql` for CockroachDB.
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

  # `dead` runs last: it kills the target, which the phase then restores.
  phase "Phase IV — process kill"          "${ENGINE_ARGS[@]}" chaos run --mode dead    --profile "$PROFILE"

  # Backstop: the chaos phase restores the target itself; this is a no-op then.
  if [ "$ENGINE" = "cockroachdb" ]; then

    step "Confirming $CT_HOST is back"
    # No systemd unit: restart by hand with sudo (root-owned store), matching memory
    # flags, and remote redirects so --background does not hold the SSH session.
    remote "$CT_USER" "$CT_HOST" "TS_IP=\$(tailscale ip -4); sudo -n cockroach start --insecure \
        --store=/var/lib/cockroach \
        --listen-addr=\$TS_IP:26257 --advertise-addr=\$TS_IP:26257 \
        --locality=$CT_LOCALITY \
        --cache=0.25 --max-sql-memory=0.25 \
        --join=$JOIN_HOST:26257 --background </dev/null >/dev/null 2>&1" >/dev/null 2>&1 || true

    # Give the node time to rejoin before declaring it missing.
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      sleep 5
      # Ask a survivor: the gateway is the node that was killed.
      LIVE=$(remote "$JOIN_USER" "$JOIN_HOST" \
        "cockroach node status --insecure --host=$JOIN_HOST:26257 --format=csv 2>/dev/null | tail -n +2 | wc -l" \
        | tr -d ' ' || echo 0)
      [ "$LIVE" = "5" ] && break
    done
    [ "$LIVE" = "5" ] \
      && ok "$CT_HOST rejoined; 5 nodes live" \
      || warn "$CT_HOST has not rejoined ($LIVE live). Restart it before measuring again."

  else

  # Sweep every member: the faulted node is resolved live and may not be $CT_HOST.
  step "Confirming every patroni member is back"
  while read -r u h; do
    [ -n "$h" ] || continue
    code="$(patroni_code "$h" health)"
    [ "$code" = "200" ] && continue
    note "$h: /health returned $code, starting patroni"
    # Same payload as p4_chaos.restore_target() for PostgreSQL.
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

  # Patroni never fails back, so switch the primary back to the gateway now.
  step "Restoring the patroni primary to the gateway"
  # Capture the primary's own SSH user with its host.
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
    # Wait (bounded) for the gateway to stream; a demoted node may need to re-clone.
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      patroni_streaming "$GW_H" && break
      sleep 10
    done
    patroni_streaming "$GW_H" \
      || note "$GW_H is not streaming yet; asking anyway, the pre-flight will retry"
    # A controlled handover; --candidate stops Patroni choosing another node.
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

# Phase I runs have no metrics.csv and are skipped.
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
# Engine comparison needs both engines: crdblab analyze engine-comparison --crdb <run> --pg <run>
[ -n "$P4R" ] && "$CRDBLAB" analyze resilience "$P4R"
[ -n "$P4D" ] && "$CRDBLAB" analyze resilience "$P4D"

step "Figures"
FIG_ARGS=()
[ -n "$P1" ]  && FIG_ARGS+=(--network  "$(basename "$P1")")
[ -n "$P2" ]  && FIG_ARGS+=(--cluster "$(basename "$P2")")
# Both fault classes: each has its own timeline figure.
[ -n "$P4R" ] && FIG_ARGS+=(--chaos    "$(basename "$P4R")")
[ -n "$P4D" ] && FIG_ARGS+=(--chaos    "$(basename "$P4D")")
"$CRDBLAB" report figures "${FIG_ARGS[@]}"

# --- done -------------------------------------------------------------------

elapsed=$(( $(date -u +%s) - started_at ))
step "Done in $((elapsed / 60))m $((elapsed % 60))s"
for r in "$P1" "$P2" "$P4R" "$P4D"; do
  [ -n "$r" ] && note "$(basename "$r")"
done
note "figures/  (PNG at >=4K, with an SVG beside each; filenames carry engine/profile/run)"
note "log $LOG"

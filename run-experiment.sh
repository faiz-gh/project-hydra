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
#   ./run-experiment.sh --smoke             # harness self-test, ~8 min
#   ./run-experiment.sh --skip-load         # working set already loaded
#   ./run-experiment.sh --no-chaos          # phases I-II only, no fault injection
#
set -euo pipefail

PROFILE="thesis-extended"
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
  sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}
HAS_ARGS=0
if [ $# -gt 0 ]; then
  HAS_ARGS=1
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)   PROFILE="${2:?--profile needs a value}"; shift 2 ;;
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

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/experiment-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

SSH_OPTS=(-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o BatchMode=yes -o ConnectTimeout=20)
# StrictHostKeyChecking=no is deliberate and disclosed: the testbed is destroyed
# and rebuilt repeatedly and addresses get reused, which otherwise produces
# spurious host-key warnings. It is not a production posture.

remote() {  # remote <user> <host> <command>
  ssh "${SSH_OPTS[@]}" "$1@$2" "$3"
}

started_at=$(date -u +%s)

printf '%scrdblab — full experiment%s\n' "$B" "$N"
note "profile $PROFILE   log $LOG"

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

# The gateway is declared in crdblab/topology.py, but DB_URI is hand-written in
# .env and nothing else reconciles the two. For PostgreSQL, this will usually point
# to 127.0.0.1 (local HAProxy), while for CockroachDB it will point to the cluster gateway.
DB_HOST="$(printf '%s' "$DB_URI" | sed -E 's|^[a-z]+://||; s|^[^@/]*@||; s|[:/?].*$||')"
ok "DB_URI names host $DB_HOST"

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

LIVE=$(remote "$GW_USER" "$GW_HOST" \
  "cockroach node status --insecure --host=$GW_HOST:26257 --format=csv 2>/dev/null | tail -n +2 | wc -l" \
  | tr -d ' ')
[ "$LIVE" = "5" ] || die "cluster reports $LIVE node(s), expected 5.
  If a previous 'dead' run left a node down, restart it (see instructions.md)."
ok "5 cluster nodes live"

# D7: the bootstrap can apply num_replicas and then fail on lease_preferences,
# leaving a healthy-looking cluster whose leaseholders are on another continent.
# It costs 12.3x throughput and no consistency check can detect it.
LEASE=$(remote "$GW_USER" "$GW_HOST" \
  "cockroach sql --insecure --host=$GW_HOST:26257 -e 'SHOW ZONE CONFIGURATION FROM DATABASE ycsb;' 2>/dev/null \
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
if [ "$DB_HOST" = "127.0.0.1" ]; then
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

load_data() {
  note "loading $INSERT_COUNT rows @ seed $SEED on database (~1-2 min)"
  try_each_host "workload init" \
    "cockroach workload init ycsb --drop --seed=$SEED --insert-count=$INSERT_COUNT '$URI_PLACEHOLDER'" \
    >/dev/null \
    || die "workload init failed against every candidate host: ${DB_CANDIDATES[*]}"
}

count_rows() {
  try_each_host "row count" \
    "cockroach sql --url '$URI_PLACEHOLDER' --format=csv -e 'SELECT count(*) FROM ycsb.usertable;' 2>/dev/null | tail -1" \
    | tr -d ' \r'
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

phase "Phase I — network substrate"        net probe    --profile "$PROFILE"
phase "Phase II — benchmark, five-node cluster" bench --profile "$PROFILE"

if [ "$RUN_CHAOS" -eq 1 ]; then
  phase "Phase III — heal-able partition"  chaos run --mode recover --profile "$PROFILE"

  # `dead` runs last because it leaves the target down. The harness does not
  # restore it: the fault is real, and restarting is an operator action.
  phase "Phase IV — process kill"          chaos run --mode dead    --profile "$PROFILE"

  # The chaos phase now restores the target itself, immediately after it has
  # finished deriving every artefact, and records the restart in events.json.
  # This block stays as a backstop for the case where the phase could not do it
  # -- it aborted, or the operator ran `crdblab chaos run` by hand from an older
  # revision -- and is a no-op when the node is already back.
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
note "figures/  (PNG at >=4K, with a vector PDF beside each)"
note "log $LOG"

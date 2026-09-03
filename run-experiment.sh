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
#   ./run-experiment.sh --no-chaos          # phases I-III only
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

# Two properties of DB_URI are load-bearing and are asserted rather than assumed.
#
# It must name exactly ONE host. A multi-host URI puts the wide-area network
# back on the client's path, which is precisely the latency the gateway-execution
# design exists to exclude -- it would inflate every measured latency and mask
# the consensus overhead being measured.
#
# It must name the `ycsb` database. `cockroach workload init ycsb` refuses any
# other name, so a URI pointing at `defaultdb` cannot have a loaded working set
# behind it.
case "$DB_URI" in
  *,*) die "DB_URI names multiple hosts. Use the single gateway host only:
    DB_URI=postgresql://root@<gateway>:26257/ycsb?sslmode=disable
  A multi-host URI reintroduces the wide-area client latency that running the
  generator on the gateway exists to exclude." ;;
esac
case "$DB_URI" in
  */ycsb\?*|*/ycsb) ok "DB_URI names one host and the ycsb database" ;;
  *) die "DB_URI must name the 'ycsb' database, not '$(echo "$DB_URI" | sed 's|.*/||; s|?.*||')'.
  'cockroach workload init ycsb' rejects any other database name, so a URI
  pointing elsewhere cannot have a loaded working set behind it." ;;
esac

command -v tailscale >/dev/null 2>&1 && {
  tailscale status >/dev/null 2>&1 && ok "tailscale up" || die "tailscale is not up on this machine"
}

# --- 2. resolve the topology from the package, not from a second copy --------

step "Resolving topology"

read -r GW_USER GW_HOST BL_USER BL_HOST < <("$PY" - <<'PYEOF'
from crdblab.topology import DEFAULT_TOPOLOGY as t, BASELINE_NODE as b
g = t.gateway
print(g.user, g.host, b.user, b.host)
PYEOF
) || die "could not resolve topology from crdblab.topology"
ok "gateway $GW_USER@$GW_HOST   baseline $BL_USER@$BL_HOST"

# The seed and row count are read from the profile the sweep will actually use.
# Hardcoding them here would create a second source of truth for the one
# parameter whose mismatch is silent and flattering (docs/defects.md, D8).
read -r SEED INSERT_COUNT < <("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys
w = json.load(sys.stdin)["workload"]
print(w["seed"], w["insert_count"])
') || die "could not read seed/insert_count from profile '$PROFILE'"
ok "profile '$PROFILE': seed $SEED, insert_count $INSERT_COUNT"

CHAOS_TARGET="$("$CRDBLAB" profile "$PROFILE" | "$PY" -c '
import json,sys; print(json.load(sys.stdin)["chaos"]["target"])')"
read -r CT_USER CT_HOST CT_LOCALITY < <("$PY" - "$CHAOS_TARGET" <<'PYEOF'
import sys
from crdblab.topology import DEFAULT_TOPOLOGY as t
n = t.get(sys.argv[1])
print(n.user, n.host, n.locality)
PYEOF
) || die "could not resolve chaos target '$CHAOS_TARGET'"
note "chaos target $CT_HOST ($CT_LOCALITY)"

# --- 3. the testbed ---------------------------------------------------------

step "Checking the testbed"

remote "$GW_USER" "$GW_HOST" true || die "cannot ssh to the gateway $GW_HOST"
remote "$BL_USER" "$BL_HOST" true || die "cannot ssh to the baseline $BL_HOST"
ok "ssh to gateway and baseline"

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
  *"[[+region="*) ok "lease preferences applied  ${LEASE#*= }" ;;
  *) die "lease_preferences is empty or unreadable on database 'ycsb'.
  The bootstrap raced: it applied num_replicas and then failed to apply the
  lease preference, so leaseholders may sit outside the fast triangle.
  Re-run 'terraform apply' or apply the zone configuration by hand (D7)." ;;
esac

# --- 4. working set ---------------------------------------------------------

step "Working set"

load_one() {  # load_one <user> <host>
  local user="$1" host="$2"
  note "loading $INSERT_COUNT rows @ seed $SEED on $host (~1-2 min)"
  remote "$user" "$host" \
    "cockroach workload init ycsb --drop --seed=$SEED --insert-count=$INSERT_COUNT \
     'postgresql://root@$host:26257/ycsb?sslmode=disable'" >/dev/null \
    || die "workload init failed on $host"
}

count_rows() {  # count_rows <user> <host>
  remote "$1" "$2" \
    "cockroach sql --insecure --host=$2:26257 --format=csv \
     -e 'SELECT count(*) FROM ycsb.usertable;' 2>/dev/null | tail -1" | tr -d ' \r'
}

if [ "$SKIP_LOAD" -eq 1 ]; then
  warn "--skip-load: not reloading. The seed behind the existing data is NOT verified here;"
  note "pre-flight's row-match probe will catch a mismatch, but only after a tier has run."
else
  load_one "$GW_USER" "$GW_HOST"
  load_one "$BL_USER" "$BL_HOST"
fi

for pair in "$GW_USER $GW_HOST" "$BL_USER $BL_HOST"; do
  set -- $pair
  rows=$(count_rows "$1" "$2" || true)
  case "$rows" in
    ''|*[!0-9]*) die "could not count rows on $2 (got: '$rows'). If it reports the table
  is offline, the import is still replicating -- wait and re-run with --skip-load." ;;
    *) [ "$rows" -ge "$INSERT_COUNT" ] \
         && ok "$2: $rows rows" \
         || die "$2 has $rows rows, expected $INSERT_COUNT" ;;
  esac
done

# --- 5. the four phases -----------------------------------------------------

phase() {  # phase <label> <crdblab args...>
  local label="$1"; shift
  step "$label"
  "$CRDBLAB" "$@" || die "$label failed. Nothing after this point has run."
}

phase "Phase I — network substrate"      net probe    --profile "$PROFILE"
phase "Phase II — unreplicated baseline" bench single --profile "$PROFILE"
phase "Phase III — five-node cluster"    bench cluster --profile "$PROFILE"

if [ "$RUN_CHAOS" -eq 1 ]; then
  phase "Phase IV — heal-able partition" chaos run --mode recover --profile "$PROFILE"

  # `dead` runs last because it leaves the target down. The harness does not
  # restore it: the fault is real, and restarting is an operator action.
  phase "Phase IV — process kill"        chaos run --mode dead    --profile "$PROFILE"

  step "Restoring $CT_HOST"
  # CockroachDB is started by cloud-init with --background, not as a systemd
  # unit, so there is no service to start and a reboot would not bring it back.
  # The memory flags are NOT optional: omitting them takes the 128 MiB default
  # and silently reintroduces the block-cache asymmetry of D9.
  remote "$CT_USER" "$CT_HOST" "TS_IP=\$(tailscale ip -4); cockroach start --insecure \
      --store=/var/lib/cockroach \
      --listen-addr=\$TS_IP:26257 --advertise-addr=\$TS_IP:26257 \
      --locality=$CT_LOCALITY \
      --cache=0.25 --max-sql-memory=0.25 \
      --join=$GW_HOST:26257 --background" >/dev/null 2>&1 || true

  for _ in 1 2 3 4 5 6; do
    LIVE=$(remote "$GW_USER" "$GW_HOST" \
      "cockroach node status --insecure --host=$GW_HOST:26257 --format=csv 2>/dev/null | tail -n +2 | wc -l" \
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

P1="$(latest p1-network)"; P2="$(latest p2_baseline)"; P3="$(latest p3_cluster)"
P4R="$(latest p4-chaos-recover)"; P4D="$(latest p4-chaos-dead)"

step "Analysis"
[ -n "$P2" ] && "$CRDBLAB" analyze steady-state "$P2"
[ -n "$P3" ] && "$CRDBLAB" analyze steady-state "$P3"

if [ -n "$P2" ] && [ -n "$P3" ]; then
  # --accept-hardware-difference is required on this testbed and is not a
  # formality: the baseline is a GCP instance and the gateway a Linode one, so
  # they differ in CPU model. The flag downgrades the refusal to a recorded
  # warning rather than suppressing it. See instructions.md.
  "$CRDBLAB" analyze raft-overhead --baseline "$P2" --cluster "$P3" \
      --accept-hardware-difference
fi
[ -n "$P4R" ] && "$CRDBLAB" analyze resilience "$P4R"
[ -n "$P4D" ] && "$CRDBLAB" analyze resilience "$P4D"

step "Figures"
FIG_ARGS=()
[ -n "$P1" ]  && FIG_ARGS+=(--network  "$(basename "$P1")")
[ -n "$P2" ]  && FIG_ARGS+=(--baseline "$(basename "$P2")")
[ -n "$P3" ]  && FIG_ARGS+=(--cluster  "$(basename "$P3")")
[ -n "$P4D" ] && FIG_ARGS+=(--chaos    "$(basename "$P4D")")
"$CRDBLAB" report figures "${FIG_ARGS[@]}"

# --- done -------------------------------------------------------------------

elapsed=$(( $(date -u +%s) - started_at ))
step "Done in $((elapsed / 60))m $((elapsed % 60))s"
for r in "$P1" "$P2" "$P3" "$P4R" "$P4D"; do
  [ -n "$r" ] && note "$(basename "$r")"
done
note "figures/  (PNG at >=4K, with a vector PDF beside each)"
note "log $LOG"

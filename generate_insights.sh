#!/usr/bin/env bash
#
# generate_insights.sh — draw the 31-chart insights catalogue from the runs on disk.
#
# Needs no testbed. Wraps `crdblab insights`, which loads every run under runs/
# through the gated loader (a run that fails validation or pre-flight is refused
# and listed, never charted), draws charts A1-E4, and writes them into a fresh
# insights/<stamp>_<profile|all>/ beside insights.md, a self-contained
# dashboard.html, summary.json, summary.csv and chart_status.csv.
#
#   ./generate_insights.sh                            # asks which profile (on a terminal)
#   ./generate_insights.sh --profile thesis-extended  # only runs of that profile
#   ./generate_insights.sh --all                      # every profile, newest run of each kind
#   ./generate_insights.sh --out <dir>                # parent directory (default insights)
#   ./generate_insights.sh --open                     # open the dashboard when done (macOS)
#
# Charts take the newest passing run of each kind per engine within the chosen
# scope. Run with no arguments on a terminal, it lists the profiles found under
# runs/ and asks; with no terminal it renders every profile, like --all.
#
set -euo pipefail

PROFILE=""
ALL=0
OUT=""
OPEN=0
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python"
CRDBLAB="$VENV/bin/crdblab"
LOG_DIR="$REPO/runs/_logs"
LOG=""

# See run-experiment.sh: output is tee'd, so Python would otherwise block-buffer.
export PYTHONUNBUFFERED=1

# --- output -----------------------------------------------------------------

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
  sed -n '3,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

HAS_ARGS=0
[ $# -gt 0 ] && HAS_ARGS=1

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    --all)     ALL=1; shift ;;
    --out)     OUT="${2:?--out needs a directory}"; shift 2 ;;
    --open)    OPEN=1; shift ;;
    -h|--help) usage ;;
    *)         die "unknown argument: $1 (try --help)" ;;
  esac
done

[ -n "$PROFILE" ] && [ "$ALL" -eq 1 ] && die "--profile and --all are mutually exclusive"

[ -x "$CRDBLAB" ] || die "$CRDBLAB not found. Run:
    python3 -m venv .venv && .venv/bin/python -m pip install -e \".[dev]\""

# Profiles that have at least one chartable run, newest first, as
# "<name> <runs> <newest run stamp>". Read from the manifests -- the same field
# `crdblab insights --profile` filters on -- so the menu offers exactly what the
# render can find.
list_profiles() {
  "$PY" - <<'PYEOF'
import json
from crdblab.config import Settings, load_env_file
from crdblab.insights.data import KINDS

load_env_file()
runs = Settings.from_env().runs_dir
seen = {}
if runs.is_dir():
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        stamp, _, suffix = d.name.partition("_")
        if suffix not in KINDS:
            continue
        try:
            m = json.loads((d / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        name = (m.get("profile") or {}).get("name") or "unknown"
        count, _ = seen.get(name, (0, ""))
        seen[name] = (count + 1, stamp)
for name, (count, newest) in sorted(seen.items(), key=lambda kv: kv[1][1], reverse=True):
    print(name, count, newest)
PYEOF
}

if [ "$HAS_ARGS" -eq 0 ] && [ -t 0 ]; then
  PROFILES="$(list_profiles)" || die "could not read the runs directory"
  [ -n "$PROFILES" ] || die "no chartable runs under the runs directory; nothing to draw"

  printf "\nWhich profile's runs should the insights cover?\n"
  printf "  0) all profiles (newest run of each kind, whatever its profile)\n"
  i=0
  while read -r name count newest; do
    i=$((i + 1))
    printf "  %d) %-18s %3d run(s), newest %s\n" "$i" "$name" "$count" "$newest"
  done <<< "$PROFILES"
  read -p "Choice [0-$i, default=0]: " choice
  case "$choice" in
    ''|0) PROFILE="" ;;
    *[!0-9]*) die "not a choice: $choice" ;;
    *) [ "$choice" -le "$i" ] || die "not a choice: $choice"
       PROFILE="$(printf '%s\n' "$PROFILES" | sed -n "${choice}p" | awk '{print $1}')" ;;
  esac
  printf "\n"
fi

[ -n "$OUT" ] || OUT="$REPO/insights"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/insights-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

printf '%scrdblab — insights%s\n' "$B" "$N"
note "profile ${PROFILE:-all}   out $OUT   log $LOG"

step "Drawing the catalogue"

ARGS=(insights --out "$OUT")
[ -n "$PROFILE" ] && ARGS+=(--profile "$PROFILE")
"$CRDBLAB" "${ARGS[@]}" || die "crdblab insights failed"

# The render just written is the newest <stamp>_<scope> directory under $OUT:
# stamps sort as time, the same "sort by name, take the last" idiom
# run-experiment.sh's latest() uses for runs/.
RENDER="$(ls -1d "$OUT"/*_"${PROFILE:-all}" 2>/dev/null | tail -1)"
[ -n "$RENDER" ] || die "crdblab insights reported success but wrote no render under $OUT"

skipped="$(awk -F, 'NR > 1 && $3 == "skipped"' "$RENDER/chart_status.csv" | wc -l | tr -d ' ')"
if [ "$skipped" -eq 0 ]; then
  ok "every chart drawn"
else
  warn "$skipped chart(s) skipped; each says why in chart_status.csv and the report"
fi

step "Done"
note "report     $RENDER/insights.md"
note "dashboard  $RENDER/dashboard.html"
note "log        $LOG"

if [ "$OPEN" -eq 1 ] && command -v open >/dev/null 2>&1; then
  open "$RENDER/dashboard.html"
fi

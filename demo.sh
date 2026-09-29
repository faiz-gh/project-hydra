#!/usr/bin/env bash
#
# demo.sh — replay the recorded thesis-extended experiment as a terminal UI.
#
# The full pipeline -- one ./run-experiment.sh: provision CockroachDB, run all
# four phases, destroy and free the Tailscale names, redeploy as
# PostgreSQL/Patroni, run them again, destroy, draw the insights -- roughly seven
# hours of recorded wall clock played back in about four minutes. Experiment
# output and throughput graphs are the recorded thesis-extended runs; Terraform
# and Tailscale output is the recorded pipeline run of 2026-09-29.
#
#   ./demo.sh                 # ~4 minutes, full-screen (needs >= 90x28; 140x42 looks best)
#   ./demo.sh --minutes 3
#   ./demo.sh --plain         # plain scrolling output, no full-screen UI
#   ./demo.sh --as-recorded   # show the logs unspliced
#   ./demo.sh --ascii         # plain-ASCII UI, for fonts missing box/block characters
#
# Keys: space pause · n skip to the next step · + / - speed · q quit
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$REPO/demo/replay.py" "$@"

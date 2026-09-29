#!/usr/bin/env bash
#
# demo.sh — replay the recorded thesis-extended experiment as a terminal UI.
#
# Provision CockroachDB, run all four phases, destroy, redeploy as
# PostgreSQL/Patroni, run them again: roughly five hours of recorded wall clock
# played back in about four minutes. Experiment output and throughput graphs are
# the recorded runs; Terraform output is reconstructed from terraform/*.tf.
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

#!/usr/bin/env bash
# demo.sh: replay the recorded thesis-extended experiment as a terminal UI.
# Options (--minutes, --plain, --as-recorded, --ascii) are in demo/replay.py --help.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$REPO/demo/replay.py" "$@"

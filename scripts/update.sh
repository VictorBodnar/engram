#!/usr/bin/env bash
# Engram updater — thin wrapper around manage.py repair.
#
#   bash scripts/update.sh          # verify + repair if needed
#   bash scripts/update.sh --check  # just check health (no changes)
#
# With direct hooks, "update" means "repair" — scripts are referenced in-place
# so edits are always live. This just verifies the hooks are still registered.
set -euo pipefail
if [ "${1:-}" = "--check" ]; then
  exec python3 "$(dirname "$0")/manage.py" verify
else
  exec python3 "$(dirname "$0")/manage.py" repair
fi

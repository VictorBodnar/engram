#!/usr/bin/env bash
# Engram uninstaller — thin wrapper around manage.py.
#
#   bash scripts/uninstall.sh          # interactive confirmation
#   bash scripts/uninstall.sh --yes    # skip confirmation
set -euo pipefail
exec python3 "$(dirname "$0")/manage.py" uninstall "$@"

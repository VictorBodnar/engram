#!/usr/bin/env bash
# Engram installer — thin wrapper around manage.py.
#
#   bash scripts/install.sh          # install with direct hooks
#   bash scripts/install.sh --copy   # (legacy flag, ignored — always direct now)
#
# This script exists for backwards compatibility. The real logic lives in
# manage.py which is pure Python stdlib, portable across macOS/Linux/WSL.
set -euo pipefail
exec python3 "$(dirname "$0")/manage.py" install "$@"

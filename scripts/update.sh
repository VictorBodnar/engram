#!/usr/bin/env bash
# Engram updater — sync source files into the plugin cache so changes take
# effect without a full reinstall cycle.
#
#   bash scripts/update.sh          # sync, then remind to /reload-plugins
#   bash scripts/update.sh --check  # just show what's out of date
set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
INSTALLED="$CLAUDE_DIR/plugins/installed_plugins.json"

# --- Find the cache path from installed_plugins.json --------------------------

if [ ! -f "$INSTALLED" ]; then
  echo "error: $INSTALLED not found — is Engram installed?"
  exit 1
fi

CACHE_DIR=$(python3 -c "
import json, sys
d = json.load(open('$INSTALLED'))
for key, entries in d.get('plugins', {}).items():
    if 'engram' in key:
        for e in entries:
            p = e.get('installPath', '')
            if p:
                print(p)
                sys.exit(0)
sys.exit(1)
" 2>/dev/null) || true

if [ -z "$CACHE_DIR" ] || [ ! -d "$CACHE_DIR" ]; then
  echo "error: engram cache directory not found — is the plugin installed?"
  echo "  Install with: /plugin marketplace add ~/claude-memory-plugin"
  echo "                /plugin install engram"
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# --- Check mode ---------------------------------------------------------------

if [ "${1:-}" = "--check" ]; then
  changes=$(diff -rq "$SOURCE_DIR" "$CACHE_DIR" \
    --exclude='.git' --exclude='__pycache__' --exclude='.claude-plugin' \
    --exclude='.claude' --exclude='dist' 2>/dev/null || true)
  if [ -z "$changes" ]; then
    echo "up to date — cache matches source"
  else
    echo "out of date:"
    echo "$changes" | sed 's/^/  /'
  fi
  exit 0
fi

# --- Sync ---------------------------------------------------------------------

rsync -a --delete \
  --exclude='.git' --exclude='__pycache__' --exclude='.claude-plugin' \
  --exclude='.claude' --exclude='dist' \
  "$SOURCE_DIR/" "$CACHE_DIR/"

echo "synced $SOURCE_DIR → $CACHE_DIR"
echo
echo "Run /reload-plugins in Claude Code to pick up the changes."

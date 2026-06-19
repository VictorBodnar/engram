#!/usr/bin/env bash
# Engram uninstaller — removes the plugin, its data, and its settings in one step.
#
#   bash scripts/uninstall.sh          # shows what it will do, then asks
#   bash scripts/uninstall.sh --yes    # skip the confirmation prompt
set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
STORE="${CLAUDE_MEMORY_HOME:-$CLAUDE_DIR/memory-store}"
SETTINGS="$CLAUDE_DIR/settings.json"
INSTALLED="$CLAUDE_DIR/plugins/installed_plugins.json"
KNOWN_MKT="$CLAUDE_DIR/plugins/known_marketplaces.json"
COMMAND_FILE="$CLAUDE_DIR/commands/engram.md"

# --- Discover what's installed ---------------------------------------------------

store_size="(not present)"
[ -e "$STORE" ] && store_size="$(du -sh "$STORE" 2>/dev/null | cut -f1)"

has_plugin=false
cache_path=""
if [ -f "$INSTALLED" ] && python3 -c "
import json, sys
d = json.load(open('$INSTALLED'))
sys.exit(0 if any('engram' in k for k in d.get('plugins', {})) else 1)
" 2>/dev/null; then
  has_plugin=true
  cache_path="$(python3 -c "
import json
d = json.load(open('$INSTALLED'))
for k, entries in d.get('plugins', {}).items():
    if 'engram' in k:
        for e in entries:
            p = e.get('installPath', '')
            if p:
                print(p)
                break
        break
")"
fi

has_marketplace=false
if [ -f "$KNOWN_MKT" ] && python3 -c "
import json, sys
d = json.load(open('$KNOWN_MKT'))
sys.exit(0 if any('engram' in k for k in d) else 1)
" 2>/dev/null; then
  has_marketplace=true
fi

has_command=false
[ -f "$COMMAND_FILE" ] && has_command=true

has_env=false
if [ -f "$SETTINGS" ] && grep -q "CLAUDE_CODE_DISABLE_AUTO_MEMORY" "$SETTINGS" 2>/dev/null; then
  has_env=true
fi

# --- Show plan -------------------------------------------------------------------

echo "Engram uninstall"
echo "================"
echo

if $has_plugin; then
  echo "  [x] Remove engram from installed plugins     ($INSTALLED)"
else
  echo "  [-] Plugin entry not found                    (nothing to remove)"
fi

if [ -n "$cache_path" ]; then
  if [ -L "$cache_path" ]; then
    echo "  [x] Remove cache symlink                      ($cache_path)"
  elif [ -d "$cache_path" ]; then
    echo "  [x] Delete cache directory                    ($cache_path)"
  fi
fi

if $has_marketplace; then
  echo "  [x] Remove engram-local marketplace           ($KNOWN_MKT)"
else
  echo "  [-] Marketplace entry not found                (nothing to remove)"
fi

if $has_command; then
  echo "  [x] Remove /engram slash command               ($COMMAND_FILE)"
else
  echo "  [-] Slash command not found                    (nothing to remove)"
fi

if [ -e "$STORE" ]; then
  echo "  [x] Delete data store ($store_size)            ($STORE)"
else
  echo "  [-] Data store not present                     (nothing to delete)"
fi

if $has_env; then
  echo "  [x] Remove CLAUDE_CODE_DISABLE_AUTO_MEMORY   ($SETTINGS)"
  echo "       (re-enables Claude Code's native auto-memory)"
else
  echo "  [-] Auto-memory env var not found              (nothing to change)"
fi

echo

# Nothing to do?
if ! $has_plugin && ! $has_marketplace && ! $has_command && ! $has_env && [ ! -e "$STORE" ]; then
  echo "Nothing to clean up — Engram is not installed."
  exit 0
fi

# --- Confirm ---------------------------------------------------------------------

if [ "${1:-}" != "--yes" ]; then
  printf "Proceed? [y/N] "
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *)
      echo "Cancelled."
      exit 0
      ;;
  esac
  echo
fi

# --- Execute ---------------------------------------------------------------------

if $has_plugin; then
  python3 -c "
import json
path = '$INSTALLED'
with open(path) as f:
    d = json.load(f)
keys = [k for k in d.get('plugins', {}) if 'engram' in k]
for k in keys:
    del d['plugins'][k]
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
  echo "  Removed plugin entry."
fi

# Remove cache: symlink → unlink; directory → rm -rf; never follow into source
if [ -n "$cache_path" ]; then
  if [ -L "$cache_path" ]; then
    rm "$cache_path"
    echo "  Removed cache symlink."
  elif [ -d "$cache_path" ]; then
    rm -rf "$cache_path"
    echo "  Deleted cache directory."
  fi
fi

if $has_marketplace; then
  python3 -c "
import json
path = '$KNOWN_MKT'
with open(path) as f:
    d = json.load(f)
keys = [k for k in d if 'engram' in k]
for k in keys:
    del d[k]
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
  echo "  Removed marketplace entry."
fi

if $has_command; then
  rm -f "$COMMAND_FILE"
  echo "  Removed /engram slash command."
fi

if [ -e "$STORE" ]; then
  rm -rf "$STORE"
  echo "  Deleted data store."
fi

if $has_env; then
  python3 -c "
import json
path = '$SETTINGS'
with open(path) as f:
    d = json.load(f)
env = d.get('env', {})
env.pop('CLAUDE_CODE_DISABLE_AUTO_MEMORY', None)
if not env:
    d.pop('env', None)
else:
    d['env'] = env
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
  echo "  Removed auto-memory env var from settings."
fi

echo
echo "Done. Engram is fully uninstalled."
echo
echo "Note: if you have a running Claude Code session, restart it to"
echo "pick up the settings change."

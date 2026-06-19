#!/usr/bin/env bash
# Engram installer — one command, fully installed, no manual steps.
#
#   bash scripts/install.sh          # install with symlink (dev mode, edits go live)
#   bash scripts/install.sh --copy   # install with a static cache copy
#
# For end-users installing from the plugin menu, this script is not needed —
# just:  /plugin marketplace add https://github.com/VictorBodnar/engram
#        /plugin install engram
#
# This script is for local/dev installs where you want source edits to be
# reflected immediately without reinstalling.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_DIR="${HOME}/.claude"
CACHE_BASE="$CLAUDE_DIR/plugins/cache/engram/engram"
INSTALLED="$CLAUDE_DIR/plugins/installed_plugins.json"
KNOWN_MKT="$CLAUDE_DIR/plugins/known_marketplaces.json"
SETTINGS="$CLAUDE_DIR/settings.json"
COMMAND_DIR="$CLAUDE_DIR/commands"
PLUGIN_JSON="$SOURCE_DIR/.claude-plugin/plugin.json"

USE_SYMLINK=true
[ "${1:-}" = "--copy" ] && USE_SYMLINK=false

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
ok()   { printf '  ok   %s\n' "$1"; }
fail() { printf '  ERR  %s\n' "$1"; exit 1; }

# --------------------------------------------------------------------------- #
# 1. Validate — catch config errors before they reach Claude Code
# --------------------------------------------------------------------------- #
echo "1. validating configuration"

[ -f "$PLUGIN_JSON" ] || fail ".claude-plugin/plugin.json not found"
ok "plugin.json exists"

# Pass paths via env vars, not string interpolation, to avoid injection
ENGRAM_PLUGIN_JSON="$PLUGIN_JSON" python3 -c '
import json, os, sys
d = json.load(open(os.environ["ENGRAM_PLUGIN_JSON"]))
errs = []
if not d.get("name"):
    errs.append("missing name")
if not d.get("version"):
    errs.append("missing version")
if "hooks" in d:
    errs.append("explicit hooks field — remove it; hooks/hooks.json is auto-discovered")
for e in errs:
    print(f"  ERR  {e}")
sys.exit(1 if errs else 0)
' || fail "plugin.json validation failed — fix errors above"
ok "plugin.json is valid"

HOOKS_JSON="$SOURCE_DIR/hooks/hooks.json"
if [ -f "$HOOKS_JSON" ]; then
  ENGRAM_HOOKS_JSON="$HOOKS_JSON" python3 -c '
import json, os; json.load(open(os.environ["ENGRAM_HOOKS_JSON"]))
' 2>/dev/null || fail "hooks/hooks.json is not valid JSON"
  ok "hooks/hooks.json parses"
fi

# --------------------------------------------------------------------------- #
# 2. Read version from plugin.json
# --------------------------------------------------------------------------- #
VERSION=$(ENGRAM_PLUGIN_JSON="$PLUGIN_JSON" python3 -c '
import json, os; print(json.load(open(os.environ["ENGRAM_PLUGIN_JSON"]))["version"])
')
CACHE_DIR="$CACHE_BASE/$VERSION"
GIT_SHA=$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")

echo ""
echo "2. registering plugin (v${VERSION})"

# --------------------------------------------------------------------------- #
# 3. Ensure base directories exist
# --------------------------------------------------------------------------- #
mkdir -p "$CLAUDE_DIR/plugins" "$COMMAND_DIR"

# --------------------------------------------------------------------------- #
# 4. Register marketplace
# --------------------------------------------------------------------------- #
ENGRAM_MKT_PATH="$KNOWN_MKT" ENGRAM_SOURCE_DIR="$SOURCE_DIR" python3 -c '
import json, os, sys
from datetime import datetime, timezone

path = os.environ["ENGRAM_MKT_PATH"]
source = os.environ["ENGRAM_SOURCE_DIR"]
d = {}
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)

d["engram"] = {
    "source": {"source": "directory", "path": source},
    "installLocation": source,
    "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
}
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
'
ok "marketplace registered"

# --------------------------------------------------------------------------- #
# 5. Register plugin
# --------------------------------------------------------------------------- #
ENGRAM_INSTALLED="$INSTALLED" ENGRAM_CACHE_DIR="$CACHE_DIR" \
ENGRAM_VERSION="$VERSION" ENGRAM_GIT_SHA="$GIT_SHA" python3 -c '
import json, os, sys
from datetime import datetime, timezone

path = os.environ["ENGRAM_INSTALLED"]
cache = os.environ["ENGRAM_CACHE_DIR"]
version = os.environ["ENGRAM_VERSION"]
sha = os.environ["ENGRAM_GIT_SHA"]

d = {"version": 2, "plugins": {}}
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
d.setdefault("plugins", {})["engram@engram"] = [{
    "scope": "user",
    "installPath": cache,
    "version": version,
    "installedAt": now,
    "lastUpdated": now,
    "gitCommitSha": sha,
}]
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
'
ok "plugin registered (installPath=$CACHE_DIR)"

# --------------------------------------------------------------------------- #
# 6. Symlink or copy into cache
# --------------------------------------------------------------------------- #
echo ""
echo "3. setting up cache"

mkdir -p "$(dirname "$CACHE_DIR")"

# Clean up any previous cache (handle both symlink and directory)
if [ -L "$CACHE_DIR" ]; then
  rm "$CACHE_DIR"
elif [ -d "$CACHE_DIR" ]; then
  rm -rf "$CACHE_DIR"
fi

if $USE_SYMLINK; then
  ln -s "$SOURCE_DIR" "$CACHE_DIR"
  ok "cache symlinked → $SOURCE_DIR"
  ok "source edits are live — no reinstall needed"
else
  rsync -a --delete \
    --exclude='.git' --exclude='__pycache__' --exclude='.claude-plugin' \
    --exclude='.claude' --exclude='dist' \
    "$SOURCE_DIR/" "$CACHE_DIR/"
  ok "cache copied (run update.sh after source edits)"
fi

# --------------------------------------------------------------------------- #
# 7. Install /engram command
# --------------------------------------------------------------------------- #
echo ""
echo "4. installing /engram command"

cp "$SOURCE_DIR/commands/engram.md" "$COMMAND_DIR/engram.md"
ok "copied to $COMMAND_DIR/engram.md"

# --------------------------------------------------------------------------- #
# 8. Set env var to disable native auto-memory
# --------------------------------------------------------------------------- #
echo ""
echo "5. configuring settings"

if [ -f "$SETTINGS" ] && grep -q "CLAUDE_CODE_DISABLE_AUTO_MEMORY" "$SETTINGS" 2>/dev/null; then
  ok "CLAUDE_CODE_DISABLE_AUTO_MEMORY already set"
else
  ENGRAM_SETTINGS="$SETTINGS" python3 -c '
import json, os
path = os.environ["ENGRAM_SETTINGS"]
d = {}
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)
d.setdefault("env", {})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
with open(path, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
'
  ok "set CLAUDE_CODE_DISABLE_AUTO_MEMORY=1"
fi

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
echo ""
echo "Engram v${VERSION} installed."
echo ""
echo "Next: restart Claude Code (or run /reload-plugins in an active session)."

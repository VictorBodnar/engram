#!/usr/bin/env bash
# Engram manage.py test — tests install, verify, repair, uninstall lifecycle.
#
# Uses a fake CLAUDE_DIR so it doesn't touch the real ~/.claude installation.
#
#   bash tests/manage.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY=python3
FAKE_HOME="$(mktemp -d)"
export HOME="$FAKE_HOME"

pass=0; fail=0
ok()   { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
assert_exit() {
  if [ "$2" -eq "$3" ]; then ok "$1"; else bad "$1 (exit=$2, expected=$3)"; fi
}
assert_file_exists() {
  if [ -f "$2" ]; then ok "$1"; else bad "$1"; fi
}
assert_file_not_exists() {
  if [ ! -f "$2" ]; then ok "$1"; else bad "$1 (still exists)"; fi
}
assert_grep() {
  if echo "$3" | grep -q "$2"; then ok "$1"; else bad "$1"; fi
}
assert_file_grep() {
  if grep -q "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1"; fi
}
assert_file_not_grep() {
  if ! grep -q "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1"; fi
}

CLAUDE_DIR="$FAKE_HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"

echo "manage.py test (fake home: $FAKE_HOME)"
echo ""

# =========================================================================== #
echo "=== 1. Fresh install ==="
out=$($PY "$REPO/scripts/manage.py" install 2>&1)
assert_exit "install exits 0" $? 0
assert_file_exists "settings.json created" "$SETTINGS"
assert_file_grep "hooks registered in settings" "engram-managed-hook" "$SETTINGS"
assert_file_grep "SessionStart hook present" "session_start.py" "$SETTINGS"
assert_file_grep "UserPromptSubmit hook present" "user_prompt.py" "$SETTINGS"
assert_file_grep "Stop hook present" "stop_distill.py" "$SETTINGS"
assert_file_grep "PreCompact hook present" "precompact.py" "$SETTINGS"
assert_file_grep "SessionEnd hook present" "session_end.py" "$SETTINGS"
assert_file_grep "CLAUDE_CODE_DISABLE_AUTO_MEMORY set" "CLAUDE_CODE_DISABLE_AUTO_MEMORY" "$SETTINGS"
assert_file_exists "engram.json created" "$CLAUDE_DIR/engram.json"
assert_file_exists "command file installed" "$CLAUDE_DIR/commands/engram.md"
echo ""

# =========================================================================== #
echo "=== 2. Verify passes after install ==="
$PY "$REPO/scripts/manage.py" verify > /dev/null 2>&1
assert_exit "verify exits 0" $? 0
echo ""

# =========================================================================== #
echo "=== 3. Idempotent reinstall ==="
$PY "$REPO/scripts/manage.py" install > /dev/null 2>&1
assert_exit "second install exits 0" $? 0
# Count how many engram hooks are in SessionStart (should be exactly 1)
hook_count=$(grep -c "session_start.py" "$SETTINGS")
if [ "$hook_count" -eq 1 ]; then ok "no duplicate hooks after reinstall"; else bad "duplicate hooks ($hook_count)"; fi
echo ""

# =========================================================================== #
echo "=== 4. Repair fixes broken state ==="
# Break things: remove the command file and corrupt a hook
rm "$CLAUDE_DIR/commands/engram.md"
# Remove one hook entry
$PY -c "
import json
with open('$SETTINGS') as f:
    d = json.load(f)
# Remove the Stop hooks entirely
d['hooks'].pop('Stop', None)
with open('$SETTINGS', 'w') as f:
    json.dump(d, f, indent=2)
"
# Verify should now fail
$PY "$REPO/scripts/manage.py" verify > /dev/null 2>&1
assert_exit "verify catches broken state" $? 1

# Repair
$PY "$REPO/scripts/manage.py" repair > /dev/null 2>&1
assert_exit "repair exits 0" $? 0

# Verify should pass again
$PY "$REPO/scripts/manage.py" verify > /dev/null 2>&1
assert_exit "verify passes after repair" $? 0
assert_file_exists "command file restored" "$CLAUDE_DIR/commands/engram.md"
assert_file_grep "Stop hook restored" "stop_distill.py" "$SETTINGS"
echo ""

# =========================================================================== #
echo "=== 5. Uninstall removes everything ==="
$PY "$REPO/scripts/manage.py" uninstall --yes > /dev/null 2>&1
assert_exit "uninstall exits 0" $? 0
assert_file_not_grep "hooks removed" "engram-managed-hook" "$SETTINGS"
assert_file_not_grep "env var removed" "CLAUDE_CODE_DISABLE_AUTO_MEMORY" "$SETTINGS"
assert_file_not_exists "command file removed" "$CLAUDE_DIR/commands/engram.md"
assert_file_not_exists "engram.json removed" "$CLAUDE_DIR/engram.json"
echo ""

# =========================================================================== #
echo "=== 6. Verify fails after uninstall ==="
$PY "$REPO/scripts/manage.py" verify > /dev/null 2>&1
assert_exit "verify fails after uninstall" $? 1
echo ""

# =========================================================================== #
echo "=== 7. Install preserves existing settings ==="
# Pre-populate settings with other content
mkdir -p "$CLAUDE_DIR"
cat > "$SETTINGS" <<'EOF'
{
  "model": "sonnet",
  "hooks": {
    "SessionStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "echo other-tool # other-hook", "timeout": 5}]}
    ]
  }
}
EOF
$PY "$REPO/scripts/manage.py" install > /dev/null 2>&1
# Other hook should still be there
assert_file_grep "other hooks preserved" "other-tool" "$SETTINGS"
assert_file_grep "model setting preserved" "sonnet" "$SETTINGS"
# Engram hooks should be added alongside
assert_file_grep "engram hooks added" "engram-managed-hook" "$SETTINGS"
echo ""

# =========================================================================== #
echo "=== 8. Uninstall preserves other hooks ==="
$PY "$REPO/scripts/manage.py" uninstall --yes > /dev/null 2>&1
assert_file_grep "other hooks still preserved after uninstall" "other-tool" "$SETTINGS"
assert_file_not_grep "engram hooks gone" "engram-managed-hook" "$SETTINGS"
echo ""

# =========================================================================== #
echo "==========================================="
echo "passed: $pass   failed: $fail"
rm -rf "$FAKE_HOME"
[ "$fail" -eq 0 ]

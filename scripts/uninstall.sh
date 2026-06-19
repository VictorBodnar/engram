#!/usr/bin/env bash
# Engram uninstaller — Engram keeps ALL its data in one folder, so discarding it is
# one command plus two things only you can do (a slash command and a settings line),
# both printed below.
#
#   bash scripts/uninstall.sh          # shows what it will do, then asks
#   bash scripts/uninstall.sh --yes    # delete the data folder without prompting
set -u

STORE="${CLAUDE_MEMORY_HOME:-$HOME/.claude/memory-store}"

echo "Engram uninstall"
echo
echo "This removes the single data folder:"
if [ -e "$STORE" ]; then
  echo "  • $STORE  ($(du -sh "$STORE" 2>/dev/null | cut -f1) — all memories, logs, state)"
else
  echo "  • $STORE  (not present — nothing to delete)"
fi
echo
echo "Two steps Engram can't do for you:"
echo "  1. In Claude Code:  /plugin uninstall engram@engram-local   (removes the hooks)"
echo "  2. (optional) delete the \"CLAUDE_CODE_DISABLE_AUTO_MEMORY\": \"1\" line from"
echo "     ~/.claude/settings.json to re-enable Claude Code's native auto-memory."
echo

if [ "${1:-}" != "--yes" ]; then
  printf "Delete the data folder now? [y/N] "
  read -r reply
  case "$reply" in
    y | Y | yes | YES) ;;
    *)
      echo "Left in place. (The two steps above are still yours to run.)"
      exit 0
      ;;
  esac
fi

rm -rf "$STORE"
echo "Removed $STORE — Engram leaves nothing else behind."

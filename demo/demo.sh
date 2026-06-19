#!/usr/bin/env bash
# Engram — narrated, fully offline demo. No network, no live LLM: the distiller reads a
# canned JSON fixture via CLAUDE_MEMORY_FAKE_LLM, exactly like the smoke test.
#
#   bash demo/demo.sh           # pauses between steps (press Enter to advance)
#   bash demo/demo.sh --fast    # no pauses (good for recording a cast)
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$REPO/scripts"
FIX="$REPO/tests/fixtures"
PY=python3
FAST="${1:-}"
export CLAUDE_MEMORY_HOME="$(mktemp -d)"

say()  { printf '\n\033[1;36m%s\033[0m\n' "$1"; }            # cyan bold — section
note() { printf '\033[2m%s\033[0m\n' "$1"; }                 # dim — narration
cmd()  { printf '\033[2m$ %s\033[0m\n' "$1"; }               # dim — the command we run
pause() { [ "$FAST" = "--fast" ] || { printf '\033[2m  (press Enter)\033[0m'; read -r _; }; }

say "Engram demo — memory that survives across sessions"
note "Throwaway store: $CLAUDE_MEMORY_HOME"
note "Everything below is offline — the distiller reads a fixture instead of calling Haiku."
pause

# --------------------------------------------------------------------------- #
say "1) CAPTURE — a Stop hook spawns a DETACHED distiller and returns instantly"
note "The transcript fixture is a chat where the user insists on splitting shell commands."
cmd "echo '{\"session_id\":\"demo\", \"transcript_path\":\"…/transcript.jsonl\"}' | stop_distill.py"
echo "{\"session_id\":\"demo\",\"transcript_path\":\"$FIX/transcript.jsonl\"}" \
  | CLAUDE_MEMORY_FAKE_LLM="$FIX/distilled.json" $PY "$S/stop_distill.py"
note "↑ The hook returned {} in milliseconds. The distiller is still running in the background…"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -f "$CLAUDE_MEMORY_HOME/memories/split-shell-commands.md" ] && break
  sleep 0.3
done
note "…and here is the memory it wrote, as plain markdown:"
cmd "cat memories/split-shell-commands.md"
cat "$CLAUDE_MEMORY_HOME/memories/split-shell-commands.md"
pause

# --------------------------------------------------------------------------- #
say "2) STATUS — what's in the store?"
cmd "engram status"
$PY "$S/memctl.py" status
pause

# --------------------------------------------------------------------------- #
say "3) SEARCH — the SAME integer scorer recall uses, exposed for debugging"
cmd "engram search shell commands"
$PY "$S/memctl.py" search shell commands
pause

# --------------------------------------------------------------------------- #
say "4) RECALL — a fresh prompt; UserPromptSubmit scores memories and injects the winner"
note "Prompt: 'how should I run shell commands in bash'"
cmd "echo '{…\"prompt\":\"how should I run shell commands in bash\"}' | user_prompt.py"
echo "{\"session_id\":\"demo2\",\"cwd\":\"/tmp/proj\",\"prompt\":\"how should I run shell commands in bash\"}" \
  | $PY "$S/user_prompt.py"
note "↑ that <recalled-memories> block is injected into the model's context — deterministically."
pause

# --------------------------------------------------------------------------- #
say "5) THE LOG — every decision is ONE greppable line; scores are hand-recomputable"
cmd "grep -E 'CREATE|RECALL' logs/memory.log"
grep -E 'CREATE|RECALL' "$CLAUDE_MEMORY_HOME/logs/memory.log"
pause

# --------------------------------------------------------------------------- #
say "6) HOUSEKEEPING — clear-logs wipes only the log; memories are untouched"
cmd "engram clear-logs"
$PY "$S/memctl.py" clear-logs
note "Memory still there; the 'state:' line shows session files (GC'd after 7d):"
$PY "$S/memctl.py" status

# --------------------------------------------------------------------------- #
say "Done. Engram keeps it all in one folder — remove the demo with:"
note "rm -rf $CLAUDE_MEMORY_HOME"
rm -rf "$CLAUDE_MEMORY_HOME"

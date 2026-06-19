#!/usr/bin/env bash
# Engram offline end-to-end smoke test. No network, no live LLM — the distiller
# runs in CLAUDE_MEMORY_FAKE_LLM mode against a throwaway store.
#
#   bash tests/smoke.sh
#
# Exercises: async hook->spawn->distill capture, deterministic recall + per-session
# dedup, SessionStart warm, the two loop guards, malformed-stdin resilience,
# memctl, and the WARMUP/RECALL log observability the design promises.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$REPO/scripts"
FIX="$REPO/tests/fixtures"
export CLAUDE_MEMORY_HOME="$(mktemp -d)"
WORK="$(mktemp -d)"
PY=python3

pass=0; fail=0
ok()   { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

log() { cat "$CLAUDE_MEMORY_HOME/logs/memory.log" 2>/dev/null; }

echo "store: $CLAUDE_MEMORY_HOME"

# --------------------------------------------------------------------------- #
echo "1. capture — async hook spawn -> detached distiller (fake LLM)"
out=$(echo "{\"session_id\":\"smoke\",\"transcript_path\":\"$FIX/transcript.jsonl\"}" \
  | CLAUDE_MEMORY_FAKE_LLM="$FIX/distilled.json" $PY "$S/stop_distill.py")
check "Stop hook returns {}"            "[ '$out' = '{}' ]"
# the distiller is detached; poll for the memory to land
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -f "$CLAUDE_MEMORY_HOME/memories/split-shell-commands.md" ] && break
  sleep 0.3
done
check "memory file created by spawned distiller" "[ -f '$CLAUDE_MEMORY_HOME/memories/split-shell-commands.md' ]"
check "INDEX.md rebuilt"                "grep -q split-shell-commands '$CLAUDE_MEMORY_HOME/INDEX.md'"

# --------------------------------------------------------------------------- #
echo "2. recall — deterministic injection + per-session dedup"
PROMPT='{"session_id":"smoke","cwd":"/tmp/anyproj","prompt":"how should I run shell commands in bash"}'
echo "$PROMPT" | $PY "$S/user_prompt.py" > "$WORK/inj1.txt"
check "first prompt injects the memory"  "grep -q split-shell-commands '$WORK/inj1.txt'"
check "wrapped in <recalled-memories>"   "grep -q recalled-memories '$WORK/inj1.txt'"
again=$(echo "$PROMPT" | $PY "$S/user_prompt.py")
check "same prompt does NOT re-inject"    "[ '$again' = '{}' ]"

# --------------------------------------------------------------------------- #
echo "3. warm — SessionStart injects an index"
echo '{"session_id":"smoke2","cwd":"/tmp/anyproj","source":"startup"}' | $PY "$S/session_start.py" > "$WORK/warm.txt"
check "SessionStart injects index"        "grep -q 'Engram memory' '$WORK/warm.txt'"

# --------------------------------------------------------------------------- #
echo "3b. warmup ordering — type priority beats recency (correction > state > knowledge)"
# Pure-function test of build_context: dates are INVERTED vs. policy on purpose —
# the knowledge memory is newest, so a date-only sort would rank it first. Type
# priority must override that. Order in `shown` == truncation survival order.
$PY - <<PY > "$WORK/warm_order.txt"
import sys; sys.path.insert(0, "$S")
import common as c, session_start as ss
mems = [
    c.Memory(slug="m-knowledge-new", type="knowledge", title="fresh fact",
             project="t", keywords=["k"], created="2026-06-10", updated="2026-06-10", body="b"),
    c.Memory(slug="m-state-mid", type="state", title="ambient state",
             project="t", keywords=["k"], created="2026-06-05", updated="2026-06-05", body="b"),
    c.Memory(slug="m-correction-old", type="correction", title="standing default",
             project="t", keywords=["k"], created="2026-01-01", updated="2026-01-01", body="b"),
]
_, shown = ss.build_context(mems, "t")
order = [shown.index(s) for s in ("m-correction-old", "m-state-mid", "m-knowledge-new")]
assert order == sorted(order) and order[0] == 0, shown
print("WARMUP-ORDER-OK")
PY
check "correction/state outrank newer knowledge" "grep -q WARMUP-ORDER-OK '$WORK/warm_order.txt'"

# --------------------------------------------------------------------------- #
echo "4. loop guards — no spawn"
g1=$(echo '{"session_id":"smoke","stop_hook_active":true}' | $PY "$S/stop_distill.py")
g2=$(echo '{"session_id":"smoke"}' | CLAUDE_MEMORY_DISTILLER=1 $PY "$S/stop_distill.py")
check "stop_hook_active guard returns {}"        "[ '$g1' = '{}' ]"
check "CLAUDE_MEMORY_DISTILLER guard returns {}" "[ '$g2' = '{}' ]"
check "guarded Stop logged SKIP not SPAWN"        "log | tail -2 | grep -q 'SKIP hook=Stop reason=guard'"

# --------------------------------------------------------------------------- #
echo "5. resilience — malformed stdin still exits 0"
for h in session_start user_prompt stop_distill precompact session_end; do
  echo 'this is not json' | $PY "$S/$h.py" >/dev/null 2>&1
  check "$h.py exit 0 on garbage stdin" "[ $? -eq 0 ]"
done

# --------------------------------------------------------------------------- #
echo "6. memctl — status / search / forget"
check "status counts the memory"   "$PY '$S/memctl.py' status | grep -q 'memories: 1'"
check "search finds it (shared scorer)" "$PY '$S/memctl.py' search bash shell | grep -q split-shell-commands"
check "search gate: unrelated term → no match" "$PY '$S/memctl.py' search kubernetes | grep -q 'no matches'"
$PY "$S/memctl.py" forget split-shell-commands >/dev/null
check "forget removes it"           "$PY '$S/memctl.py' status | grep -q 'memories: 0'"

# --------------------------------------------------------------------------- #
echo "6b. memctl clear — bulk wipe + dry-run preview"
# seed two throwaway memories via the same model the distiller writes through
$PY - <<PY
import sys; sys.path.insert(0, "$S")
import common as c
for i in (1, 2):
    c.save_memory(c.Memory(slug=f"clear-test-{i}", type="knowledge",
                  title=f"clear test {i}", project="t", keywords=["k"],
                  created="2026-06-14", updated="2026-06-14", body="b"))
c.write_index()
PY
check "seeded two memories"             "$PY '$S/memctl.py' status | grep -q 'memories: 2'"
$PY "$S/memctl.py" clear --dry-run >/dev/null
check "clear --dry-run keeps memories"  "$PY '$S/memctl.py' status | grep -q 'memories: 2'"
$PY "$S/memctl.py" clear >/dev/null
check "clear wipes all memories"        "$PY '$S/memctl.py' status | grep -q 'memories: 0'"
check "clear logs a CLEAR line"         "log | grep -q 'CLEAR memories=2'"

# --------------------------------------------------------------------------- #
echo "7. observability — the log answers the two questions"
check "WARMUP line names the warmed session" "log | grep -q 'WARMUP session=smoke2'"
check "RECALL line shows injected slug+score" "log | grep -Eq 'RECALL .*slugs=\[split-shell-commands:[0-9]+\]'"
check "RECALL line shows a skipped near-miss" "log | grep -q 'skipped=\[split-shell-commands:'"

# --------------------------------------------------------------------------- #
# Runs LAST: clear-logs wipes the log, so it must follow every log assertion.
# 6b emptied the store, so seed one memory here to prove clear-logs leaves it be.
echo "8. memctl clear-logs — log-only reset, memories untouched"
$PY - <<PY
import sys; sys.path.insert(0, "$S")
import common as c
c.save_memory(c.Memory(slug="logclear-survivor", type="knowledge",
              title="survivor", project="t", keywords=["k"],
              created="2026-06-14", updated="2026-06-14", body="b"))
c.write_index()
PY
check "seeded one memory before clear-logs"   "$PY '$S/memctl.py' status | grep -q 'memories: 1'"
$PY "$S/memctl.py" clear-logs --dry-run >/dev/null
check "clear-logs --dry-run keeps the log"    "log | grep -q 'RECALL '"
$PY "$S/memctl.py" clear-logs >/dev/null
check "clear-logs leaves the memory intact"   "$PY '$S/memctl.py' status | grep -q 'memories: 1'"
check "clear-logs drops the distiller trail"  "$PY '$S/memctl.py' status | grep -q 'none logged yet'"
check "clear-logs re-seeds a CLEARLOGS line"  "log | grep -q 'CLEARLOGS lines='"

# --------------------------------------------------------------------------- #
# Runs LAST: housekeep rotates the log (moves memory.log → memory.log.1), so it
# must follow every assertion that reads the log.
echo "9. housekeep — per-session state GC by age + log rotation"
$PY - <<PY > "$WORK/housekeep.txt"
import sys, os, time; sys.path.insert(0, "$S")
import common as c
c.ensure_store()
old = time.time() - (c.STATE_TTL_DAYS + 23) * 86400   # comfortably past the TTL
dirs = (c.cursors_dir(), c.injected_dir(), c.locks_dir())
for d in dirs:
    (d / "old.json").write_text("{}")
    os.utime(d / "old.json", (old, old))   # backdate past the cutoff
    (d / "fresh.json").write_text("{}")     # current mtime → must survive
c.log_path().write_text("x" * (c.LOG_ROTATE_BYTES + 16))   # force rotation
c.housekeep()
print("OLD_GONE"   if all(not (d / "old.json").exists()   for d in dirs) else "OLD_STAYED")
print("FRESH_KEPT" if all((d / "fresh.json").exists()     for d in dirs) else "FRESH_GONE")
print("ROTATED"    if c.log_path().with_suffix(".log.1").exists() else "NOT_ROTATED")
PY
check "housekeep deletes state past the TTL"  "grep -q OLD_GONE '$WORK/housekeep.txt'"
check "housekeep keeps fresh state files"     "grep -q FRESH_KEPT '$WORK/housekeep.txt'"
check "housekeep rotates the oversize log"    "grep -q ROTATED '$WORK/housekeep.txt'"

# --------------------------------------------------------------------------- #
echo ""
echo "passed: $pass   failed: $fail"
rm -rf "$CLAUDE_MEMORY_HOME" "$WORK"
[ "$fail" -eq 0 ]

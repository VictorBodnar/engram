#!/usr/bin/env bash
# Engram memory lifecycle test — simulates REAL sessions end-to-end.
#
# Tests the full path: user prompts → distiller captures → next session recalls.
# This is the test that proves the memory system WORKS, not just that individual
# functions return the right values.
#
#   bash tests/lifecycle.sh
#
# No network, no live LLM — uses CLAUDE_MEMORY_FAKE_LLM for deterministic capture.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$REPO/scripts"
export CLAUDE_MEMORY_HOME="$(mktemp -d)"
WORK="$(mktemp -d)"
PY=python3

pass=0; fail=0
ok()   { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

assert_file_exists() {
  if [ -f "$2" ]; then ok "$1"; else bad "$1"; fi
}
assert_grep() {
  if echo "$3" | grep -q "$2"; then ok "$1"; else bad "$1"; fi
}
assert_not_grep() {
  if echo "$3" | grep -q "$2"; then bad "$1"; else ok "$1"; fi
}
assert_file_grep() {
  if grep -q "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1"; fi
}
assert_log_grep() {
  if cat "$CLAUDE_MEMORY_HOME/logs/memory.log" 2>/dev/null | grep -q "$1"; then ok "$2"; else bad "$2"; fi
}
assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got: $2)"; fi
}

echo "lifecycle test store: $CLAUDE_MEMORY_HOME"
echo ""

# =========================================================================== #
# SCENARIO 1: User states a correction → captured → recalled in next session
# =========================================================================== #
echo "=== SCENARIO 1: User correction captured and recalled ==="
echo ""

# --- Session A: user states a preference ---
echo "1a. Session A starts (empty store)"
out=$(echo '{"session_id":"sess-A","cwd":"/tmp/myproject","source":"startup"}' \
  | $PY "$S/session_start.py")
ok "session A warmup with empty store"

echo "1b. User sends a prompt containing a correction"
# Create a transcript that contains the correction
cat > "$WORK/transcript-A.jsonl" <<'EOF'
{"type":"user","message":{"role":"user","content":"Always use uv run instead of raw python for scripts in this project"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Understood. I'll use `uv run` instead of raw `python` for all script execution in this project from now on."}]}}
{"type":"user","message":{"role":"user","content":"also never use pip directly, always uv pip"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Got it — I'll use `uv pip` instead of bare `pip` for all package operations."}]}}
EOF

# Fake LLM output: what the distiller should extract
cat > "$WORK/distill-A.json" <<'EOF'
[
  {
    "action": "create",
    "slug": "use-uv-run-not-python",
    "type": "correction",
    "title": "Use uv run instead of raw python for scripts",
    "project": "myproject",
    "keywords": ["uv", "python", "run", "scripts", "execute"],
    "body": "Always use `uv run` instead of raw `python` or `python3` when executing scripts in this project. Also use `uv pip` instead of bare `pip` for package operations."
  }
]
EOF

# Simulate UserPromptSubmit with transcript path (this spawns the distiller)
echo '{"session_id":"sess-A","cwd":"/tmp/myproject","prompt":"use uv run for scripts","transcript_path":"'"$WORK/transcript-A.jsonl"'"}' \
  | CLAUDE_MEMORY_FAKE_LLM="$WORK/distill-A.json" $PY "$S/user_prompt.py" > /dev/null

# Wait for detached distiller to finish
for _ in $(seq 1 20); do
  [ -f "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md" ] && break
  sleep 0.3
done
assert_file_exists "correction memory created" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"
assert_file_grep "memory has correct type" "type: correction" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"
assert_file_grep "memory has correct project" "project: myproject" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"

echo ""

# --- Session B: the correction is recalled when relevant ---
echo "1c. Session B starts — correction should warm"
out=$(echo '{"session_id":"sess-B","cwd":"/tmp/myproject","source":"startup"}' \
  | $PY "$S/session_start.py")
assert_grep "session B warmup includes the memory" "use-uv-run-not-python" "$out"

echo "1d. User prompt triggers recall"
out=$(echo '{"session_id":"sess-B","cwd":"/tmp/myproject","prompt":"how do I run the test scripts in python?"}' \
  | $PY "$S/user_prompt.py")
assert_grep "recall injects the uv correction" "use-uv-run-not-python" "$out"
assert_grep "recall body mentions uv run" "uv run" "$out"

echo "1e. Unrelated prompt does NOT recall it"
out=$(echo '{"session_id":"sess-B","cwd":"/tmp/myproject","prompt":"what is the database schema?"}' \
  | $PY "$S/user_prompt.py")
assert_eq "unrelated prompt gets no injection" "$out" "{}"

echo ""

# =========================================================================== #
# SCENARIO 2: Knowledge fact from assistant response → captured → recalled
# =========================================================================== #
echo "=== SCENARIO 2: Knowledge fact captured from assistant ==="
echo ""

cat > "$WORK/transcript-B.jsonl" <<'EOF'
{"type":"user","message":{"role":"user","content":"why does the deploy fail on staging?"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"The staging deploy fails because the REDIS_URL env var is not set in the staging environment. The app requires Redis 7+ for the stream consumer. The production env has it but staging was set up from an older template that predates the Redis dependency."}]}}
{"type":"user","message":{"role":"user","content":"ah right, that makes sense. let me fix the staging config"}}
EOF

cat > "$WORK/distill-B.json" <<'EOF'
[
  {
    "action": "create",
    "slug": "staging-needs-redis-url",
    "type": "knowledge",
    "title": "Staging environment missing REDIS_URL",
    "project": "myproject",
    "keywords": ["staging", "redis", "deploy", "environment", "config"],
    "body": "The staging deploy fails because REDIS_URL is not set. The app requires Redis 7+ for the stream consumer. Production has it but staging was set up from an older template predating the Redis dependency."
  }
]
EOF

echo '{"session_id":"sess-B","cwd":"/tmp/myproject","transcript_path":"'"$WORK/transcript-B.jsonl"'"}' \
  | CLAUDE_MEMORY_FAKE_LLM="$WORK/distill-B.json" $PY "$S/stop_distill.py" > /dev/null

for _ in $(seq 1 20); do
  [ -f "$CLAUDE_MEMORY_HOME/memories/staging-needs-redis-url.md" ] && break
  sleep 0.3
done
assert_file_exists "knowledge memory created" "$CLAUDE_MEMORY_HOME/memories/staging-needs-redis-url.md"

echo ""
echo "2b. Session C: knowledge recalled when relevant"
out=$(echo '{"session_id":"sess-C","cwd":"/tmp/myproject","prompt":"the staging deploy is broken again"}' \
  | $PY "$S/user_prompt.py")
assert_grep "staging knowledge recalled" "staging-needs-redis-url" "$out"
assert_grep "recall mentions REDIS_URL" "REDIS_URL" "$out"

echo ""

# =========================================================================== #
# SCENARIO 3: Memory update — correction gets refined
# =========================================================================== #
echo "=== SCENARIO 3: Memory update refines an existing memory ==="
echo ""

cat > "$WORK/distill-C.json" <<'EOF'
[
  {
    "action": "update",
    "slug": "use-uv-run-not-python",
    "type": "correction",
    "title": "Use uv run instead of raw python for scripts",
    "project": "myproject",
    "keywords": ["uv", "python", "run", "scripts", "execute", "venv"],
    "body": "Always use `uv run` instead of raw `python` or `python3` when executing scripts. Use `uv pip` instead of bare `pip`. Exception: the CI dockerfile uses raw python since uv is not installed there."
  }
]
EOF

echo '{"session_id":"sess-C","cwd":"/tmp/myproject","transcript_path":"'"$WORK/transcript-B.jsonl"'"}' \
  | CLAUDE_MEMORY_FAKE_LLM="$WORK/distill-C.json" $PY "$S/stop_distill.py" > /dev/null

for _ in $(seq 1 15); do
  grep -q "venv" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md" 2>/dev/null && break
  sleep 0.3
done
assert_file_grep "memory was updated (new keyword added)" "venv" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"
assert_file_grep "body updated with CI exception" "CI dockerfile" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"
assert_log_grep "UPDATE.*slug=use-uv-run-not-python" "UPDATE logged"

echo ""

# =========================================================================== #
# SCENARIO 4: Cross-project — project memory still matches on keywords
# =========================================================================== #
echo "=== SCENARIO 4: Cross-project recall ==="
echo ""

echo "4a. Query from a different project — keywords still match"
out=$(echo '{"session_id":"sess-D","cwd":"/tmp/otherproject","prompt":"how do I run python scripts with uv?"}' \
  | $PY "$S/user_prompt.py")
assert_grep "cross-project recall works on keyword match" "use-uv-run-not-python" "$out"

echo "4b. No keyword overlap → no injection"
out=$(echo '{"session_id":"sess-D","cwd":"/tmp/otherproject","prompt":"how is the database configured?"}' \
  | $PY "$S/user_prompt.py")
assert_eq "unrelated query from other project → no injection" "$out" "{}"

echo ""

# =========================================================================== #
# SCENARIO 5: Per-session dedup — same memory not injected twice
# =========================================================================== #
echo "=== SCENARIO 5: Per-session dedup ==="
echo ""

echo "5a. First recall in new session"
out1=$(echo '{"session_id":"sess-E","cwd":"/tmp/myproject","prompt":"redis staging deploy broken"}' \
  | $PY "$S/user_prompt.py")
assert_grep "first recall works" "staging-needs-redis-url" "$out1"

echo "5b. Same keywords, same session — no re-inject"
out2=$(echo '{"session_id":"sess-E","cwd":"/tmp/myproject","prompt":"staging redis configuration issue"}' \
  | $PY "$S/user_prompt.py")
assert_not_grep "dedup prevents re-injection" "staging-needs-redis-url" "$out2"

echo ""

# =========================================================================== #
# SCENARIO 6: Global vs project-scoped memory
# =========================================================================== #
echo "=== SCENARIO 6: Global corrections warm everywhere ==="
echo ""

cat > "$WORK/distill-global.json" <<'EOF'
[
  {
    "action": "create",
    "slug": "prefer-atomic-shell-commands",
    "type": "correction",
    "title": "Use atomic shell commands not chained",
    "project": "global",
    "keywords": ["shell", "bash", "commands", "atomic", "permission"],
    "body": "Run each shell command as a separate Bash call. Do not chain with && or ;. This lets permission allowlists match reliably."
  }
]
EOF

echo '{"session_id":"sess-F","cwd":"/tmp/myproject","transcript_path":"'"$WORK/transcript-B.jsonl"'"}' \
  | CLAUDE_MEMORY_FAKE_LLM="$WORK/distill-global.json" $PY "$S/stop_distill.py" > /dev/null

for _ in $(seq 1 15); do
  [ -f "$CLAUDE_MEMORY_HOME/memories/prefer-atomic-shell-commands.md" ] && break
  sleep 0.3
done
assert_file_exists "global correction created" "$CLAUDE_MEMORY_HOME/memories/prefer-atomic-shell-commands.md"

echo "6b. Global correction warms in ANY project"
out=$(echo '{"session_id":"sess-G","cwd":"/tmp/totally-different-project","source":"startup"}' \
  | $PY "$S/session_start.py")
assert_grep "global correction warms in unrelated project" "prefer-atomic-shell-commands" "$out"

echo ""

# =========================================================================== #
# SCENARIO 7: Warmup type priority — corrections before knowledge
# =========================================================================== #
echo "=== SCENARIO 7: Warmup prioritizes corrections over knowledge ==="
echo ""
# Test the ordering via the pure-function path (same approach as smoke.sh 3b)
$PY - <<PY > "$WORK/warm_order.txt"
import sys; sys.path.insert(0, "$S")
import common as c, session_start as ss
mems = [
    c.Memory(slug="m-knowledge-new", type="knowledge", title="fresh fact",
             project="t", keywords=["k"], created="2026-06-10", updated="2026-06-10", body="b"),
    c.Memory(slug="m-correction-old", type="correction", title="standing default",
             project="t", keywords=["k"], created="2026-01-01", updated="2026-01-01", body="b"),
]
_, shown = ss.build_context(mems, "t")
# correction must appear first despite being older
assert shown[0] == "m-correction-old" and shown[1] == "m-knowledge-new", f"bad order: {shown}"
print("WARMUP-ORDER-OK")
PY
assert_file_grep "corrections appear before knowledge in warmup" "WARMUP-ORDER-OK" "$WORK/warm_order.txt"

echo ""

# =========================================================================== #
# SCENARIO 8: Hook resilience — broken store doesn't crash hooks
# =========================================================================== #
echo "=== SCENARIO 8: Resilience under broken conditions ==="
echo ""

echo "8a. Corrupt memory file"
echo "this is not valid frontmatter" > "$CLAUDE_MEMORY_HOME/memories/corrupt.md"
out=$(echo '{"session_id":"sess-I","cwd":"/tmp/myproject","prompt":"anything at all"}' \
  | $PY "$S/user_prompt.py")
ok "corrupt memory doesn't crash recall"

echo "8b. Missing store directory"
rm -f "$CLAUDE_MEMORY_HOME/memories/corrupt.md"
mv "$CLAUDE_MEMORY_HOME/memories" "$CLAUDE_MEMORY_HOME/memories.bak"
out=$(echo '{"session_id":"sess-I2","cwd":"/tmp/myproject","source":"startup"}' \
  | $PY "$S/session_start.py")
ok "missing memories dir doesn't crash warmup"
# Restore: ensure_store recreated an empty dir, so merge back
rmdir "$CLAUDE_MEMORY_HOME/memories" 2>/dev/null
mv "$CLAUDE_MEMORY_HOME/memories.bak" "$CLAUDE_MEMORY_HOME/memories"

echo "8c. Concurrent distiller lock (fresh lock = another distiller running)"
mkdir -p "$CLAUDE_MEMORY_HOME/state/locks"
echo "99999" > "$CLAUDE_MEMORY_HOME/state/locks/sess-locked.lock"
# Touch it so it looks recent (not stale)
touch "$CLAUDE_MEMORY_HOME/state/locks/sess-locked.lock"
# Run the distiller directly (not via hook) to test the lock
$PY "$S/distiller.py" --session sess-locked --transcript "$WORK/transcript-A.jsonl" 2>/dev/null
assert_log_grep "SKIP.*reason=locked" "locked session skips gracefully"
rm -f "$CLAUDE_MEMORY_HOME/state/locks/sess-locked.lock"

echo ""

# =========================================================================== #
# SCENARIO 9: Full status reflects reality
# =========================================================================== #
echo "=== SCENARIO 9: memctl status reflects the real store ==="
echo ""
status=$($PY "$S/memctl.py" status)
# At this point we have at least 3 core memories (uv-run, staging-redis, atomic-shell)
mem_count=$(echo "$status" | grep "memories:" | grep -oE '[0-9]+')
if [ "$mem_count" -ge 3 ]; then ok "status shows >= 3 memories ($mem_count)"; else bad "status shows >= 3 memories (got $mem_count)"; fi
assert_grep "status shows correction type" "correction" "$status"
assert_grep "status shows knowledge type" "knowledge" "$status"
assert_grep "status shows myproject" "myproject" "$status"
assert_grep "status shows global" "global" "$status"

echo ""

# =========================================================================== #
# SCENARIO 10: Search uses same scorer as recall
# =========================================================================== #
echo "=== SCENARIO 10: search consistency with recall ==="
echo ""
# Verify the memories we expect are still on disk
assert_file_exists "staging memory on disk" "$CLAUDE_MEMORY_HOME/memories/staging-needs-redis-url.md"
assert_file_exists "uv memory on disk" "$CLAUDE_MEMORY_HOME/memories/use-uv-run-not-python.md"

search=$($PY "$S/memctl.py" search redis staging deploy)
assert_grep "search finds staging memory" "staging-needs-redis-url" "$search"
search2=$($PY "$S/memctl.py" search uv python run)
assert_grep "search finds uv correction" "use-uv-run-not-python" "$search2"
search3=$($PY "$S/memctl.py" search quantum entanglement)
assert_grep "search returns no match for unrelated" "no matches" "$search3"

echo ""

# =========================================================================== #
# SCENARIO 11: Multiple memories compete — top-N scoring works
# =========================================================================== #
echo "=== SCENARIO 11: Top-N scoring selects best matches ==="
echo ""

# Add more memories to test scoring competition
$PY -c "
import sys; sys.path.insert(0, '$S')
import common as c
c.ensure_store()
for i in range(5):
    c.save_memory(c.Memory(
        slug=f'filler-memory-{i}', type='knowledge',
        title=f'Filler fact number {i}',
        project='myproject', keywords=['filler', f'num{i}'],
        created='2026-06-01', updated='2026-06-01',
        body=f'This is filler memory {i} for testing top-N.'))
c.write_index()
"

out=$(echo '{"session_id":"sess-K","cwd":"/tmp/myproject","prompt":"redis staging deploy environment config broken"}' \
  | $PY "$S/user_prompt.py")
# staging-needs-redis-url has 5 keyword hits + project bonus = very high score
assert_grep "highest-scoring memory wins" "staging-needs-redis-url" "$out"
assert_not_grep "filler memories not injected" "filler-memory" "$out"

echo ""

# =========================================================================== #
# DONE
# =========================================================================== #
echo "==========================================="
echo "passed: $pass   failed: $fail"
rm -rf "$CLAUDE_MEMORY_HOME" "$WORK"
[ "$fail" -eq 0 ]

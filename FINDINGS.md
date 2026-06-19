# Engram — dogfooding findings & decisions log

A running record of what surfaced while running Engram live across real sessions:
confirmed mechanics, changes shipped, and open gaps with proposed fixes. The canonical
architecture lives in `DESIGN.md`; this file is the lab notebook behind it.

---

## Confirmed mechanics

### Warmup and per-prompt recall are *different selectors*
Two injection paths, two completely different algorithms:

| | **Warmup** (`session_start.py`) | **Recall** (`user_prompt.py`) |
|---|---|---|
| Trigger | SessionStart | every UserPromptSubmit |
| Selects by | scope + recency + budget | keyword score |
| Scope | current-project + global (others → a count) | scored across the store |
| Gate | budget only (`SESSIONSTART_BUDGET = 9000` chars) | `THRESHOLD = 3` score floor |
| Cap | until budget fills | `TOP_N = 3`, `RECALL_BUDGET = 8000` chars |
| Dedup | none (fresh session) | per-session (won't re-inject) |

Warmup **casts wide** ("what might matter in this project"); recall **narrows** ("what matches
what you just typed"). There's no keyword scoring at warmup because there's no prompt yet.

### Recall reads *only the user's current prompt*
- It scores `data["prompt"]` and nothing else — **not** the assistant's replies, **not** your
  earlier prompts. Recall is a pure function of how you phrased *this one turn*.
- **Recall ≠ capture.** Capture (the distiller) reads the whole transcript slice — user *and*
  assistant turns. So the assistant's words can be *stored* as a memory but can never *trigger
  recall* of an existing one.
- **The recall-gap.** A memory whose keywords aren't in the literal prompt won't surface, even
  when highly relevant (`"deploy the lambda"` misses an `aws/cli/sdk` memory). Keyword recall is
  reactive and phrasing-dependent by design.

### INDEX.md is a write-only browse mirror
- Regenerated on every mutation (distiller capture, `forget`/`clear`/`prune`/`reindex`) via
  `write_index()` → `render_index(load_all_memories())`.
- **The runtime never reads it.** Warmup and recall both read the live store via
  `load_all_memories()`. INDEX.md exists only so a human/tool can `cat`/`grep`/open one file.
- Documented at the source (comment + `write_index` docstring in `common.py`) and in `DESIGN.md`,
  so nobody "optimizes" injection to read it — that would couple recall to a cache that lags the
  real memories, the exact staleness bug the current design avoids.

### Per-session state IS garbage-collected by age (not a leak)
- `cursors/`, `injected/`, and `locks/` accumulate one tiny file per `session_id`, but
  `common.housekeep()` (called from `session_end.py`, and now also `session_start.py`) deletes any
  whose `st_mtime` is older than `STATE_TTL_DAYS = 7`, then `rotate_log_if_big()` rotates
  `memory.log` at 2 MB.
- Age-gating (not "delete this session") makes it **resume-safe**: a just-ended session's fresh
  files survive, so `claude --resume` doesn't re-distill from offset 0.
- **Correction:** an earlier note in this session called this an "open leak" — that was wrong; I'd
  grepped the hook files instead of `common.housekeep()`. (The distiller even auto-corrected the
  stored memory about it once the contradiction surfaced.)

### Native auto-memory should stay disabled alongside Engram
- No data corruption (independent stores/files/locks), **but**: (1) double-injection — both
  systems inject the same facts; (2) cross-contamination loop — Engram recalls → native writes
  CLAUDE.md → distiller re-reads it → re-distilled; (3) wrecked attribution during validation.
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` is set (and inherited by the distiller's `claude -p`
  subprocess). Keep it that way while dogfooding.

---

## Changes shipped during dogfooding

### Type-aware warmup — `session_start.py`
- `WARMUP_PRIORITY = {"correction": 0, "state": 1, "knowledge": 2}`; sort key changed from
  `(date, slug)` → `(priority, date, slug)`.
- Corrections/state win budgeted warmup slots over newer knowledge, closing the recall-gap at
  scale: standing defaults stay present even when the budget truncates.
- **No-op at current scale** (everything fits under 9000 chars) — it's insurance for when warmup
  starts printing `… N more on disk`. Covered by smoke test **3b** (inverted dates prove type
  priority overrides recency).

### `clear-logs` command — `memctl.py`
- Clears **only** `memory.log`; memories and state untouched. The narrow counterpart to
  `clear --logs`, which *also* wipes every memory — before this there was no logs-only path.
- Re-seeds a `CLEARLOGS` audit line (filtered out of `status`'s distiller-activity view).
  Supports `--dry-run`. Covered by smoke test section **8**.

### INDEX.md decoupling documented
- Comment block + `write_index` docstring in `common.py`; note in `DESIGN.md`. No behavior change —
  purely making the write-only property an intentional, defended choice.

### State-GC hardening + observability — `session_start.py`, `memctl.py`, `tests/smoke.sh`
- `housekeep()` now also runs at SessionStart (best-effort, guarded), so cleanup still happens if a
  session crashes and `SessionEnd` never fires.
- `/engram status` now reports `state: N session file(s) (GC'd after 7d)` — the previously-invisible
  state directory is now observable.
- Smoke test section **9** seeds backdated state files + an oversize log and asserts `housekeep`
  deletes past-TTL files, keeps fresh ones, and rotates the log.

---

## Known limitations & future work

### Distiller scoping can mis-tag global preferences
- A universal preference ("always use the CLI, not the SDK") can be tagged with the project it was
  first stated in, instead of `global`. In other projects it's invisible to both warmup and recall.
- **Workaround:** hand-edit the memory's `project:` field to `global`.
- **Root cause:** the distiller's scoping heuristic. Preferences/corrections phrased universally
  ("always/never…") should be tagged `global`. The distiller prompt will be tuned for this.

### Standing preferences want *pinning/warming*, not reactive recall
- The right defense against "the assistant is about to do the wrong thing" is for the preference
  to be in context from **turn 1** (warmup), not recalled *after* the fact.
- Because context **accumulates**, a single warmup injection covers the whole session — so a
  correctly-scoped + type-prioritized correction is effectively pinned for the session already.
- Preventive (warm) beats reactive (recall) for always-applicable defaults, full stop.

### PROPOSAL — assistant-turn recall (the reactive catch-all)
- For memories that *weren't* warmed (out of scope, newly relevant, or budget-truncated): in
  `user_prompt.py`, also tokenize the **last assistant message** (from the transcript) and fold
  those tokens into the recall pool, **weighted below** the user's prompt.
- Makes conversation content a recall trigger: assistant says *"I'll use Boto3"* → on the next
  turn the `aws-cli` correction recalls, even if the user only typed *"sure"*.
- **Reactive — one turn late** (the correction lands *after* the assistant already spoke), so it
  *complements*, never replaces, warming. Needs noise control (assistant-token weighting +
  existing `THRESHOLD` + per-session dedup) and its own smoke test.

### Cost dial-down (standing offer, not yet actioned)
- The distiller runs `claude -p` on every `Stop` across all projects. If cost feels heavy, switch
  capture to fire only on `PreCompact`/`SessionEnd` instead of every `Stop`.

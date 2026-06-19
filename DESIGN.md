# Engram — design

Engram splits into two paths that **never mix**:

```
 CAPTURE (async, never blocks)              RECALL (deterministic, in-hook)
 ──────────────────────────────             ───────────────────────────────
 Stop ─┐                                     SessionStart ─► inject budgeted
 PreCompact ─┼─► spawn detached distiller                    index (project + global)
 SessionEnd ─┘        │
                      ▼                      UserPromptSubmit ─► tokenize, score,
        read NEW transcript bytes                 inject top-3 full bodies,
        (per-session byte cursor)                 dedup per session
                      │                                   ▲
                      ▼                                   │ intra-session freshness:
        claude -p (haiku) → JSON actions    ┌─────────────┘ a memory distilled after
                      │                      │ turn N is recallable at turn N+1 —
                      ▼                      │ no daemon, store re-read fresh
        create/update memories/*.md ────────┘
        rebuild INDEX.md
```

The capture path is the *only* non-deterministic part, and it is async, detached, and off
the critical path. Recall is byte-identical given a fixed store.

## Load-bearing invariants

1. **No LLM call inside a hook.** Hooks finish in milliseconds; the LLM lives only in the
   detached `distiller.py`. (This is the lesson from claude-mem's loop bugs.)
2. **Two recursion guards.** `stop_hook_active` (Stop re-entry) and `CLAUDE_MEMORY_DISTILLER=1`
   (the headless `claude -p` child fires its own hooks — the marker makes *every* Engram hook,
   capture and recall, no-op inside that child).
3. **Advance the transcript cursor even on failure.** A lost distillation beats a poison-input
   retry loop. Per-session cursors / locks / injected-state keep concurrent terminals safe.
4. **The scoring gate.** A memory needs ≥1 keyword/title textual hit to be a candidate;
   project/type bonuses only *boost* a hit, never qualify one alone. Without this, every
   same-project correction scores 3 and injects on every prompt.
5. **One global store, project-tagged; plain markdown + grep.** Every hook exits 0 no matter what.

## The store

```
<store>/                                  # ~/.claude/memory-store, or CLAUDE_MEMORY_HOME
├── memories/<slug>.md                    # one memory per file
├── INDEX.md                              # generated browse mirror — never read at runtime
├── state/cursors/<session_id>.json       # byte offset already distilled
├── state/injected/<session_id>.json      # slugs already injected this session
├── state/locks/<session_id>.lock         # distiller overlap guard
└── logs/memory.log                       # one greppable line per event
```

`INDEX.md` is a **human/tool browse mirror only** — regenerated on every mutation so you can
`cat`/`grep`/open one file to see the whole store. The runtime never reads it: warmup and recall
both read the live store via `load_all_memories()`. Don't wire injection to read the mirror —
that would couple recall to a cache that can lag the real memories.

A memory is flat `key: value` frontmatter (hand-rolled ~10-line parser, **no YAML dep**) plus
a markdown body **≤120 words**:

```markdown
---
slug: payments-localstack
type: knowledge          # correction | knowledge | state
title: Integration tests need LOCALSTACK=1
project: payments-api    # or "global"
keywords: tests, integration, localstack, ci
created: 2026-06-14
updated: 2026-06-14
---
The payments-api integration suite silently no-ops unless LOCALSTACK=1 is exported…
```

## Recall scoring (the whole point: a human can reproduce it)

Per prompt, `user_prompt.py` tokenizes (lowercase, stopwords dropped) and scores every memory:

| Match | Points |
|---|---|
| prompt token ∈ memory `keywords` | **+3** each |
| prompt token ∈ memory title | **+2** each |
| memory `project` == cwd project | **+2** |
| memory `type` == `correction` | **+1** |

**Gate:** ≥1 keyword/title hit, or the score is 0. Score **≥3** qualifies; **top 3** by score
then recency inject as `<recalled-memories>` (≤8k chars); injected slugs are recorded so each
memory speaks **once per session**.

### Worked example

Prompt *"the integration tests pass but nothing seems to run"* in `payments-api` →
tokens `{integration, tests, pass, run}`:

| Memory | Scoring | Score |
|---|---|---|
| `payments-localstack` (knowledge·payments-api, kw: tests, integration, localstack) | tests +3, integration +3, project +2 | **8 → injected** |
| `use-uv-run-scripts` (correction·global, title "Always run … via uv") | run→title +2, correction +1 | **3 → just clears** |
| `payments-jwt-rotation` (state·payments-api, kw: jwt, auth, rotation) | no text hit → gate | **0 → stays on disk** |

The scorer is one function in `common.py`, shared by recall and `/engram search`.

### Recall sees only *your current prompt* (the recall-gap)

`user_prompt.py` scores against exactly one string: `data["prompt"]` — the message you just
submitted. It does **not** see the assistant's replies, and it does **not** carry over your
earlier prompts. Recall is a pure function of *how you phrased this one turn*. Two consequences:

- **Recall ≠ capture.** Recall reads only your prompt; **capture** (the distiller) reads the whole
  transcript slice — your turns *and* the assistant's. So something the assistant says can be
  *stored* as a memory, but can never *trigger recall* of an existing one.
- **The recall-gap.** A relevant memory whose keywords don't appear in your literal prompt won't
  surface. *"deploy the lambda"* shares no token with an `aws/cli/sdk` memory → no recall, even
  though the preference is exactly relevant. Keyword recall is reactive and phrasing-dependent **by
  design**.

This is *why* standing preferences/corrections can't lean on recall: they must be **warmed** at
SessionStart (type-aware warmup loads `correction`/`state` first; see below) so they're in context
*before* the assistant acts, not retrieved *after* you happen to name them. Recall is for
topic-triggered knowledge; warming is for always-applicable defaults. Closing the *reactive* half
of the gap (recall off the assistant's last turn) is a tracked proposal — see FINDINGS.md.

## The distiller

`distiller.py` is spawned (never awaited) by the capture hooks with `--session` and
`--transcript`. Steps: **lock** (per-session, stale >10 min broken) → **cursor** (read only new
bytes) → **digest** (parse JSONL to USER/ASSISTANT text + one-line tool notes; a tool-only turn
is *not substantive* → advance cursor, no LLM — a free gate) → **LLM** (`claude -p --model haiku
--output-format json`, 180 s) → **apply** (create/update, union keywords, rebuild INDEX.md) →
**advance cursor even on failure**, release lock.

### A finding worth recording: distiller framing

A plain `claude -p` inherits Claude Code's full agentic-coding system prompt **and** your
`CLAUDE.md`. Empirically this makes the distiller judge *unreliably* — on the exact same
transcript it flip-flopped between `[]` and a correct capture. Two fixes were evaluated:

- `--bare` strips hooks, auto-memory, and CLAUDE.md discovery — but **requires `ANTHROPIC_API_KEY`**
  and fails under OAuth/subscription auth. Rejected as non-portable.
- `--system-prompt "<minimal extractor framing>"` replaces the coding prompt, works on **either**
  auth, costs far fewer tokens, and made capture **3/3 reliable** in testing. **Chosen.**

This is the design's softest spot (see roadmap): the distiller's judgment is the only
non-auditable step. The prompt instructs "[] only when genuinely nothing durable", and the body
is capped at 120 words. Quality here is expected to be tuned empirically against real sessions.

## Safety / determinism checklist

- No LLM call ever runs inside a hook; hooks finish in milliseconds.
- Every hook is try/except-wrapped to exit 0 — memory failures can't break a coding session.
- Two recursion guards: `stop_hook_active` + `CLAUDE_MEMORY_DISTILLER`.
- Concurrent terminals safe: cursors, locks, injected-state all keyed by `session_id`.
- Everything observable: one greppable log line per event; store is plain markdown; INDEX.md
  regenerable from scratch (`/engram reindex`).
- Bounded on disk: `housekeep()` (SessionEnd + SessionStart) deletes per-session
  cursors/injected/locks older than `STATE_TTL_DAYS` (7d) by `st_mtime` — age-gated so a resumed
  session's fresh state survives — and `rotate_log_if_big()` rolls `memory.log` at 2 MB.
- Single-folder footprint: every write routes through `store_root()`/`atomic_write()` (temp file
  in the target dir, never `/tmp`), so all data lives under one deletable folder.

## Growth roadmap (ship v1, keep it debuggable)

The scoring scheme is the first rung of a ladder; each step adds a signal as a plain number,
never an opaque model:

1. **v1 — keyword scoring + the gate.** This build. Ship it.
2. **v2 — recency decay + reciprocal-rank fusion** (SuperBrain-proven; still no vectors).
3. **v3 — trust/importance accrual:** a frontmatter `weight` that rises on re-confirmation,
   decays on contradiction (Hermes's trust model, reduced to one auditable number).
4. **v4 — append-only / versioned store** + read-only "trusted" memories as a prompt-injection
   poisoning defense.

Optional, only if recall ever demands it: a vector signal fused *alongside* keyword, never
replacing it.

## File map

| Path | Role |
|---|---|
| `scripts/common.py` | store paths, frontmatter, tokenize/score+gate, index render, spawn, guards, log |
| `scripts/session_start.py` | SessionStart — warm with budgeted index, `WARMUP` log |
| `scripts/user_prompt.py` | UserPromptSubmit — score, gate, inject top-3, dedup, `RECALL` log |
| `scripts/stop_distill.py` · `precompact.py` · `session_end.py` | capture hooks — guard + detached spawn (+ housekeeping) |
| `scripts/distiller.py` | the detached worker — the only LLM caller |
| `scripts/memctl.py` | `/engram` CLI — status / search / forget / clear / prune / reindex |
| `hooks/hooks.json` | global-install hook registration (`${CLAUDE_PLUGIN_ROOT}`) |
| `.claude/settings.json` | project-scoped dogfood (`${CLAUDE_PROJECT_DIR}` + isolated store) |
| `tests/smoke.sh` + `tests/fixtures/` | offline end-to-end gate |

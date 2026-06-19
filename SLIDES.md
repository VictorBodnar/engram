---
marp: true
title: Engram — deterministic memory for Claude Code
paginate: true
---

# Engram

**Deterministic, hook-based memory for Claude Code.**

Hooks decide *when*. Plain arithmetic decides *what*.
No embeddings. No vector DB. No daemon.

<small>A plain-markdown store you can <code>cat</code>, <code>grep</code>, and delete in one folder.</small>

---

## The problem

Every new Claude Code session starts from zero.

- You re-explain the same preferences ("use the AWS CLI, not the SDK").
- Hard-won facts ("integration tests need `LOCALSTACK=1`") die at session end.
- Native auto-memory is a black box — you can't see *why* something was recalled.

**Memory should be auditable plumbing, not a model's mood.**

---

## The idea: two paths, one store

|  | **Capture** (write) | **Recall** (read) |
|---|---|---|
| Hook | Stop / UserPromptSubmit / PreCompact / SessionEnd | SessionStart + UserPromptSubmit |
| LLM? | yes — **detached** Haiku, off the hot path | **never** — integer scoring |
| Cost | hooks return in ms | in-hook, deterministic |

The expensive, fuzzy part runs in the background. The fast, exact part runs inline.

---

## Capture — async, never blocks

```
Stop / UserPromptSubmit ─► spawn detached distiller ─► return {} in ms
PreCompact / SessionEnd      │
                             ▼  (background)
              read new transcript bytes (byte cursor)
              ask Haiku "what's worth keeping?"
              write markdown memories + rebuild INDEX
```

- The **only** place an LLM runs — and it's never awaited.
- A byte **cursor** means each turn is read once: O(1), not O(n²).
- Crash-safe: cursor advances even on failure; per-session locks prevent overlap.

---

## Recall — deterministic, auditable

Per prompt, score every memory with integers:

| Signal | Points |
|---|---|
| prompt token ∈ keywords | **+3** |
| prompt token ∈ title | **+2** |
| same project | **+2** |
| type is `correction` | **+1** |

Gate at **≥3**, inject the **top 3**, **once per session**.
Every score is hand-recomputable — the log tells you *why this, not that*.

---

## Warmup & the recall-gap

Recall only sees **your current prompt**. So *"deploy the lambda"* won't surface an
`aws/cli/sdk` memory — no shared keyword.

**Fix:** type-aware warmup at SessionStart loads standing defaults first —
`correction` → `state` → `knowledge` — so preferences are present *before* you act,
not retrieved *after* you happen to name them.

<small>Warming is for always-on defaults; keyword recall is for topic-triggered knowledge.</small>

---

## The store: plain markdown, one folder

```
~/.claude/memory-store/
├── memories/<slug>.md      # flat key:value frontmatter + ≤120-word body
├── INDEX.md                # generated browse mirror (never read at runtime)
├── state/{cursors,injected,locks}/
└── logs/memory.log         # one greppable line per event
```

- `cat`, `grep`, `diff`, hand-edit — no opaque format, no DB.
- **Everything** in one folder → trivial to inspect, trivial to discard.

---

## Safety & determinism

- **No LLM ever runs inside a hook** — hooks finish in milliseconds.
- Every hook is `try/except` → exit 0. Memory can't break your session.
- Two recursion guards stop the distiller from triggering itself.
- Concurrent terminals safe — cursors/locks/injected keyed by `session_id`.
- Bounded on disk — state GC'd after 7 days; log rotates at 2 MB.
- Stdlib-only Python; distiller reuses the `claude` CLI's auth (no API key).

---

## Install

```text
/plugin marketplace add https://github.com/VictorBodnar/engram
/plugin install engram
```

Approve the 5 hooks · set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` · `/engram status`.

**Uninstall:** `/plugin uninstall …` + `rm -rf ~/.claude/memory-store`. Nothing left behind.

---

## Roadmap

The integer scorer is the first rung of a ladder — each step adds a signal as a
**plain number**, never an opaque model:

1. keyword + title + project + type  ← *current (v0.2)*
2. recency / decay weighting
3. assistant-turn recall (catch "I say X, you do Y")
4. optional embedding signal, **fused alongside** keywords — never replacing them

---

# Thanks

**Engram** — memory you can read.

`DESIGN.md` · `FINDINGS.md` · `demo/` · MIT © Victor Bodnar

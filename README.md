# Engram

**Deterministic memory for Claude Code.**

Engram gives Claude Code a long-term memory that you can see, search, and trust.
It captures what matters from your sessions — preferences, gotchas, decisions —
and recalls them exactly when the right keywords appear. No embeddings, no vector
databases, no magic. Just keyword scoring you can recompute by hand, and a
`~/.claude/memory-store/` folder full of plain markdown you can grep.

[![CI](https://github.com/VictorBodnar/engram/actions/workflows/ci.yml/badge.svg)](https://github.com/VictorBodnar/engram/actions/workflows/ci.yml)

```
 CAPTURE (async, never blocks)              RECALL (deterministic, in-hook)
 ──────────────────────────────             ───────────────────────────────
 Stop ─┐                                    SessionStart ─► inject budgeted
 UserPromptSubmit ─┼─► spawn detached                       index of memories
 PreCompact ─┤     │   distiller
 SessionEnd ─┘     │                         UserPromptSubmit ─► tokenize prompt,
                   ▼                              score every memory, inject top 3
         read new transcript bytes                        ▲
         (per-session byte cursor)                        │
                   │                          memories are plain .md files —
                   ▼                          recallable the turn after capture,
         claude -p (haiku) → JSON             no restart needed
                   │
                   ▼
         write memories/*.md
         rebuild INDEX.md
```

---

## Install

```
/plugin marketplace add https://github.com/VictorBodnar/engram
/plugin install engram
```

Approve the 5 hooks when prompted. Then:

```
/engram status
```

> **Recommended:** add `"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"` to the `env`
> block in `~/.claude/settings.json` so Engram and the native auto-memory don't
> both capture the same facts.

---

## How it works

**Capture** happens off the hot path. When Claude stops responding (or you submit a
prompt), a detached background process reads the new transcript bytes, asks Haiku
"what's worth keeping?", and writes the answer as a markdown file. Your session
never waits.

**Recall** is pure arithmetic, no LLM. When you type a prompt, Engram tokenizes it
and scores every memory:

| Match | Points |
|---|---|
| Prompt word in memory **keywords** | +3 |
| Prompt word in memory **title** | +2 |
| Memory belongs to **current project** | +2 |
| Memory is a **correction** (preference/habit) | +1 |

Score >= 3 qualifies. Top 3 are injected. Every score is a hand-recomputable
integer logged to `memory.log`, so "why did *this* load and not *that*?" is always
answerable.

### What gets captured

| Type | Example |
|---|---|
| **correction** | "Don't chain shell commands with `&&` — split into separate Bash calls." |
| **knowledge** | "payments-api: integration tests silently no-op unless `LOCALSTACK=1` is set." |
| **state** | "Chose SQS over Kafka for ingest; queue-migration TODO still open." |

---

## Commands

```
/engram status              memory counts, store path, recent distiller activity
/engram search <terms>      rank memories with the same scorer recall uses
/engram forget <slug>       delete a memory and rebuild the index
/engram clear [--all]       wipe all memories; --state, --logs, --all, --dry-run
/engram clear-logs          clear only memory.log; memories untouched
/engram prune               drop empty/untitled orphan memories
/engram reindex             rebuild INDEX.md from memory files
/engram doctor              self-diagnostic: store, cache, hooks, log health
```

---

## The store

Everything lives in `~/.claude/memory-store/`:

```
~/.claude/memory-store/
├── memories/
│   ├── prefer-aws-cli.md          # one file per memory
│   ├── split-shell-commands.md
│   └── payments-localstack.md
├── INDEX.md                        # auto-generated browse mirror
├── state/                          # per-session cursors, locks, injected sets
└── logs/
    └── memory.log                  # structured, greppable audit trail
```

Each memory is a plain markdown file with flat frontmatter:

```markdown
---
slug: split-shell-commands
type: correction
title: Split shell commands instead of chaining with &&
project: global
keywords: shell, bash, commands, chaining, split
created: 2026-06-14
updated: 2026-06-14
---
Prefer single atomic shell commands over compound commands chained
with && or ;. This lets permission allowlists match individual
commands reliably.
```

You can edit these by hand. Engram re-reads from disk on every prompt.

---

## Configuration

| Env var | Effect |
|---|---|
| `CLAUDE_MEMORY_HOME` | Store location (default `~/.claude/memory-store`). Set per-project to isolate stores. |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | Disable native auto-memory (recommended). |
| `CLAUDE_MEMORY_FAKE_LLM=<path>` | Distiller reads canned JSON instead of calling Haiku (for testing). |

---

## Troubleshooting

Start with `/engram doctor` — it checks the store, cache freshness, hooks, log
errors, and stale locks in one shot.

For deeper debugging, grep the log:

```bash
grep WARMUP ~/.claude/memory-store/logs/memory.log   # session warm-up
grep RECALL ~/.claude/memory-store/logs/memory.log   # per-prompt injection
grep ERROR  ~/.claude/memory-store/logs/memory.log   # failures
```

Every event is keyed by `session=` so concurrent terminals stay legible. Capture
events: `SPAWN`, `DISTILL`, `CREATE`, `UPDATE`, `SKIP`, `ERROR`.

| Symptom | Fix |
|---|---|
| 0 memories after install | Normal — first capture happens on the first Stop after a substantive turn. |
| No memories ever appear | Check `claude` CLI is on PATH and authenticated. `grep ERROR` in the log. |
| Duplicate injections | Native auto-memory is still on. Set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. |

---

## Development

```bash
git clone https://github.com/VictorBodnar/engram.git
cd engram
bash scripts/install.sh      # symlinks cache → source; edits are live instantly
bash tests/smoke.sh          # offline end-to-end tests (36 cases, no network)
```

**PR workflow:** every PR runs smoke tests + config validation via GitHub Actions.
On merge to main, the pipeline auto-tags and creates a GitHub release from the
version in `.claude-plugin/plugin.json`. Bump the version in your PR.

---

## Requirements

- Python 3.9+ (stdlib only — zero third-party packages)
- `claude` CLI on PATH, authenticated (reuses Claude Code's own auth — no separate API key)
- Claude Code with plugin support

## License

[MIT](./LICENSE) — Victor Bodnar

# Engram

A **deterministic, hook-based memory system for Claude Code**. Capture and recall
are plumbing, not model whims: hooks decide *when*, plain arithmetic decides *what*.

- **Capture** is asynchronous and never blocks. Stop / PreCompact / SessionEnd hooks
  spawn a *detached* `distiller.py` — the only place an LLM runs — which reads the new
  transcript bytes, asks Haiku "what's worth keeping?", and writes markdown memories.
- **Recall** is deterministic and in-hook. SessionStart injects a budgeted index;
  UserPromptSubmit scores every memory by keyword/title/project/type and injects the
  top 3 full bodies. No embeddings, no vector DB, no daemon.

The store is plain markdown you can `cat`, `grep`, `diff`, and edit by hand. Everything
Engram writes lives in **one folder** (`~/.claude/memory-store/`), so it's trivial to
inspect — and trivial to discard. See [`DESIGN.md`](./DESIGN.md) for the why,
[`FINDINGS.md`](./FINDINGS.md) for the dogfooding lab notes, and [`SLIDES.md`](./SLIDES.md)
for the short pitch.

---

## Requirements

- **Python 3.9+** on `PATH` — stdlib only, zero third-party packages.
- **`claude` CLI** on `PATH`, authenticated. The distiller reuses Claude Code's own auth
  (OAuth/subscription **or** API key) — **no separate `ANTHROPIC_API_KEY` needed**.
- **Claude Code** with plugin + hooks support (any recent version).

## Install (offline)

Engram ships as a self-contained package — no GitHub, no network needed.

1. Unzip the package somewhere stable:
   ```bash
   unzip engram-0.1.0.zip -d ~/        # creates ~/engram-0.1.0/
   ```
2. In Claude Code, register it as a local marketplace and install:
   ```
   /plugin marketplace add ~/engram-0.1.0
   /plugin install engram@engram-local
   ```
3. **Approve the 5 hooks** when Claude Code prompts (SessionStart, UserPromptSubmit, Stop,
   PreCompact, SessionEnd).
4. Recommended — disable native auto-memory so the two systems don't double-capture. Add to
   `~/.claude/settings.json`:
   ```json
   { "env": { "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1" } }
   ```
   (Engram can't set this for you — a plugin can't write `settings.json` `env`. Until it's set,
   Engram prints a one-line reminder at session start.)
5. Confirm:
   ```
   /engram status
   ```

That's it. The store at `~/.claude/memory-store/` **auto-creates on first use**; the first
memory is captured on the first `Stop` after a substantive turn. One global store, each
memory project-tagged.

## How it works (60 seconds)

| | Capture (write) | Recall (read) |
|---|---|---|
| Hook | Stop / PreCompact / SessionEnd | SessionStart (warm) + UserPromptSubmit |
| LLM? | yes — a **detached** Haiku subprocess, off the hot path | **never** — pure integer scoring |
| Latency | hooks return in ms; distill runs in the background | in-hook, deterministic |

Recall scores each memory by keyword (+3), title (+2), project (+2), `correction` type (+1),
gates at score ≥3, and injects the top 3. Every score is a hand-recomputable integer, so the
log is a complete audit trail. Full detail in [`DESIGN.md`](./DESIGN.md).

## `/engram` commands

The command is `/engram` (not `/memory`) so it never collides with Claude Code's built-in
`/memory` — both work side by side after install.

```
/engram status            counts by type/project, state-file count, recent distiller activity
/engram search <terms>    rank memories with the SAME scorer recall uses
/engram forget <slug>     delete a memory + rebuild the index
/engram clear [flags]     wipe ALL memories + reindex; --state, --logs, --all, --dry-run
/engram clear-logs        clear only memory.log; memories + state untouched (--dry-run)
/engram prune             drop empty/untitled (orphaned) memories
/engram reindex           rebuild INDEX.md from the memory files
```

(Or run the CLI directly: `python3 scripts/memctl.py status`.)

## Configuration

| Env var | Effect |
|---|---|
| `CLAUDE_MEMORY_HOME` | Store location. Default `~/.claude/memory-store`. Set per-project to isolate (supports `${CLAUDE_PROJECT_DIR}`). |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | Disable Claude Code's native auto-memory (recommended alongside Engram). |
| `CLAUDE_MEMORY_FAKE_LLM=<file>` | Distiller reads canned JSON instead of calling Haiku (testing/demo). |

**Distiller isolation.** The distiller runs `claude -p --model haiku --system-prompt …`. The
`--system-prompt` flag replaces Claude Code's default agentic-coding prompt with a minimal
"extraction tool" framing — without it the distiller inherits the full coding-agent context
(incl. your `CLAUDE.md`) and judges *unreliably*. We deliberately avoid `--bare` (which would
also strip that context) because it requires `ANTHROPIC_API_KEY` and breaks under
OAuth/subscription auth; `--system-prompt` keeps working on either.

## Troubleshooting

One greppable file: `~/.claude/memory-store/logs/memory.log`. Two questions, two greps:

```bash
# Which memories warmed a fresh session?
grep WARMUP ~/.claude/memory-store/logs/memory.log
#  → WARMUP session=ab12 project=engram memories=3 slugs=[a,b,c]

# Which memory loaded on a prompt, and why not the others?
grep RECALL ~/.claude/memory-store/logs/memory.log
#  → RECALL session=ab12 prompt_tokens=7 injected=2 \
#           slugs=[payments-localstack:8,use-uv-run-scripts:3] \
#           skipped=[payments-jwt-rotation:0]
```

Capture events log too: `SPAWN`, `DISTILL`, `CREATE`, `UPDATE`, `SKIP`, `ERROR` — all keyed by
`session=` so concurrent terminals stay legible.

| Symptom | Likely cause / fix |
|---|---|
| `/engram status` says 0 memories | Normal before the first capture. Memories land on the first `Stop` after a substantive turn. |
| `status` errors about the store path | Store not created yet — fixes itself on the first hook. Or `python3` not on `PATH`. |
| No memories ever appear | Check `claude` is on `PATH` and authed (`grep ERROR …/memory.log`); the distiller needs it. |
| Memories injected twice / duplicated | Native auto-memory is still on — set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. |
| Hooks fire twice per event | A second wiring exists (e.g. user-level hooks in `settings.json` *and* the plugin). Keep one. |

## Uninstall

Engram keeps everything in one folder, so removing it is a folder delete plus two quick steps:

1. **Remove the plugin** (unwires the hooks):
   ```
   /plugin uninstall engram@engram-local
   ```
2. **Delete the data** (all memories, logs, state):
   ```bash
   rm -rf ~/.claude/memory-store
   ```
   Or run the bundled helper, which prints what it'll do and asks first:
   ```bash
   bash scripts/uninstall.sh        # add --yes to skip the prompt
   ```
3. *(Optional)* remove the `"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"` line from
   `~/.claude/settings.json` to re-enable native auto-memory.

Nothing is scattered elsewhere — no dotfiles, no `/tmp` residue.

## Testing

```bash
bash tests/smoke.sh        # offline end-to-end, no network (fake-LLM mode)
# Live round-trip (spends a few cents, needs the claude CLI authed):
CLAUDE_MEMORY_HOME=$(mktemp -d) python3 scripts/distiller.py \
    --session live --transcript tests/fixtures/transcript.jsonl
```

`smoke.sh` covers async hook→spawn→distill capture, deterministic recall + per-session dedup,
SessionStart warm + type-aware ordering, both loop guards, malformed-stdin resilience, memctl,
the WARMUP/RECALL log observability, and state-GC + log rotation. For a narrated, human-readable
walkthrough see [`demo/`](./demo/).

## License

[MIT](./LICENSE) © Victor Bodnar

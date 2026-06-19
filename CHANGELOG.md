# Changelog

All notable changes to Engram are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-06-19

First packaged release: a deterministic, hook-based memory system for Claude Code that
captures durable facts asynchronously and recalls them with a reproducible keyword scorer.

### Added
- **Async capture** — `Stop` / `PreCompact` / `SessionEnd` hooks spawn a detached distiller
  (`claude -p --model haiku`) off the hot path; no LLM ever runs inside a hook.
- **Deterministic recall** — `UserPromptSubmit` scores memories with a hand-reproducible
  integer scorer (keywords +3, title +2, project +2, `correction` +1), a `THRESHOLD=3` gate,
  `TOP_N=3` cap, and per-session dedup.
- **Type-aware warmup** — `SessionStart` injects a budgeted index of the current project +
  global memories, ordered `correction` → `state` → `knowledge` so standing defaults win
  budgeted slots over newer knowledge at scale.
- **Plain-markdown store** — one file per memory with flat `key: value` frontmatter (no YAML
  dependency), a generated `INDEX.md` browse mirror, and a greppable `memory.log`.
- **State garbage collection** — `housekeep()` at `SessionEnd` drops per-session
  cursors/injected/locks older than `STATE_TTL_DAYS=7` and rotates `memory.log` at 2 MB.
- **`/engram` CLI** (`memctl.py`) — `status`, `search`, `forget`, `clear`, `clear-logs`,
  `prune`, `reindex`.
- **Offline-installable plugin** — relocatable `${CLAUDE_PLUGIN_ROOT}` hook wiring, local
  marketplace manifest, single-folder data footprint, one-command uninstall.
- **Offline smoke test** (`tests/smoke.sh`) and **narrated demo** (`demo/demo.sh`) via
  `CLAUDE_MEMORY_FAKE_LLM` — no network or live LLM required.

### Notes
- Stdlib-only Python 3.9+; the distiller inherits the `claude` CLI's auth (no `ANTHROPIC_API_KEY`
  required).
- Recommended alongside install: set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` so Engram and Claude
  Code's native auto-memory don't double-inject.

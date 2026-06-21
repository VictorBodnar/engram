# Changelog

All notable changes to Engram are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-06-21

Major redesign: drop the plugin system in favor of direct hooks for maximum
portability, resilience, and mutability.

### Changed
- **Direct hooks deployment** — Engram now registers hooks directly in
  `~/.claude/settings.json` instead of going through the plugin registry system.
  No more cache directories, symlinks, marketplace registration, or
  `installed_plugins.json` manipulation. Clone anywhere, install once, edits are
  live immediately.
- **Single lifecycle manager** (`scripts/manage.py`) — replaces the three shell
  scripts with one Python script (stdlib only) that handles install, uninstall,
  repair, verify, and migrate. The shell wrappers still work as thin delegates.
- **Self-healing** — `manage.py repair` fixes any broken state (missing hooks,
  stale paths, missing command file) without losing data.
- **Health check** — `manage.py verify` exits 0 if healthy, 1 if not. Suitable
  for CI and scripted checks.
- **Migration path** — `manage.py migrate` converts an existing plugin-based
  install to direct hooks, cleaning all plugin traces.
- **No rsync dependency** — the old `update.sh` required rsync; the new system
  has zero external dependencies beyond Python 3.9 stdlib.
- **No version-tagged paths** — hooks point at a fixed location; no path changes
  on version bumps.
- **Command file robustness** — `/engram` finds its scripts via
  `~/.claude/engram.json` breadcrumb, falling back to `CLAUDE_PLUGIN_ROOT` for
  backwards compatibility.

### Added
- **Lifecycle tests** (`tests/lifecycle.sh`) — 36 end-to-end tests that simulate
  real sessions: user states corrections → distiller captures → next session
  recalls. Covers cross-session memory, updates, dedup, project isolation, and
  resilience under broken conditions.
- **Manage tests** (`tests/manage.sh`) — 30 tests covering install/verify/repair/
  uninstall lifecycle, idempotency, and preservation of existing settings.
- **CI runs all three suites** — smoke (36) + lifecycle (36) + manage (30) =
  102 offline tests on Python 3.9, 3.12, and 3.14.

### Removed
- Plugin registry dependency (`installed_plugins.json`, `known_marketplaces.json`)
- Cache directory system (`~/.claude/plugins/cache/engram/`)
- `rsync` dependency for updates
- Version-tagged install paths

## [0.2.0] — 2026-06-19

Plugin-only distribution, CI pipeline, and security hardening.

### Changed
- **Plugin-only distribution** — install via the plugin marketplace (`/plugin marketplace add`);
  `scripts/install.sh` remains for local dev installs with live-edit symlinks.
- **CI pipeline** — GitHub Actions run smoke tests on Python 3.9 + 3.12, validate plugin/hooks
  JSON, and warn on un-bumped versions. On merge to main, the release workflow auto-tags and
  creates a GitHub release from the version in `plugin.json`.
- **Security hardening** — shell scripts pass file paths via environment variables instead of
  string interpolation; the distiller uses `--system-prompt` to override the default agentic
  framing (works under OAuth auth, unlike `--bare`).
- **Prompt-submit capture** — `UserPromptSubmit` now also spawns the distiller (in addition to
  `Stop`/`PreCompact`/`SessionEnd`), so corrections made mid-conversation are captured sooner.
- **SessionStart GC** — `housekeep()` now also runs at `SessionStart` (best-effort), so stale
  state is cleaned up even when `SessionEnd` never fires.
- **`/engram doctor`** — new self-diagnostic command that checks store writability, cache
  freshness, distiller reachability, hook config, log health, and stale locks.

## [0.1.0] — 2026-06-14

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

#!/usr/bin/env python3
"""Engram control CLI — inspect and manage the store.

    memctl.py status              counts by type/project + recent distiller runs
    memctl.py search <terms...>   rank memories with the SAME scorer as recall
    memctl.py forget <slug>       delete a memory + reindex
    memctl.py clear [flags]       wipe ALL memories + reindex. flags: --state
                                  (cursors/injected/locks), --logs, --all, --dry-run
    memctl.py clear-logs          clear ONLY memory.log; memories/state untouched
    memctl.py prune               drop empty/untitled (orphaned) memories
    memctl.py reindex             rebuild INDEX.md from the memory files
    memctl.py doctor              self-diagnostic: check store, cache, hooks, log health

`search` deliberately calls common.rank — the exact function per-prompt recall
uses — so what you debug here is what runs live.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


def cmd_status(_args):
    mems = c.load_all_memories()
    by_type, by_proj = {}, {}
    for m in mems:
        by_type[m.type] = by_type.get(m.type, 0) + 1
        by_proj[m.project] = by_proj.get(m.project, 0) + 1
    state_n = sum(
        len([p for p in d.iterdir() if p.is_file()])
        for d in (c.cursors_dir(), c.injected_dir(), c.locks_dir()) if d.exists()
    )
    print(f"store:    {c.store_root()}")
    print(f"memories: {len(mems)}")
    print("by type:  " + (", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "—"))
    print("by project: " + (", ".join(f"{k}={v}" for k, v in sorted(by_proj.items())) or "—"))
    print(f"state:    {state_n} session file(s) (GC'd after {c.STATE_TTL_DAYS}d)")
    runs = _recent_log_events(("DISTILL", "CREATE", "UPDATE"), limit=5)
    print("\nrecent distiller activity:")
    print("\n".join("  " + r for r in runs) if runs else "  (none logged yet)")


def cmd_search(args):
    toks = c.tokenize(" ".join(args))
    if not toks:
        print("search: need at least one non-stopword term")
        return
    _, scored = c.rank(toks, c.load_all_memories(), c.project_from_cwd())
    if not scored:
        print("no matches (no memory shares a keyword/title token with the query)")
        return
    print(f"query tokens: {', '.join(toks)}")
    for m, s in scored[:20]:
        mark = "→" if s >= c.THRESHOLD else " "
        print(f"{mark} {s:3d}  {m.slug} ({m.type}·{m.project}) — {m.title}")


def cmd_forget(args):
    if not args:
        print("forget: need a slug")
        return
    slug = args[0]
    p = c.memories_dir() / f"{c._safe(slug)}.md"
    if p.exists():
        p.unlink()
        c.write_index()
        c.log("FORGET", slug=slug)
        print(f"forgot {slug}; reindexed")
    else:
        print(f"no such memory: {slug}")


def cmd_clear(args):
    """Wipe ALL memories — the bulk counterpart to `forget`. Memories only by
    default; opt into state (cursors/injected/locks) and the log with flags.
    NOTE: clearing state resets the transcript cursor, so the next distiller run
    re-reads the whole transcript and may regenerate memories from it."""
    flags = {a for a in args if a.startswith("-")}
    unknown = flags - {"--logs", "--state", "--all", "--dry-run", "-n"}
    if unknown:
        print("clear: unknown flag(s): " + ", ".join(sorted(unknown)))
        print("usage: clear [--state] [--logs] [--all] [--dry-run]")
        return
    do_state = bool(flags & {"--state", "--all"})
    do_logs = bool(flags & {"--logs", "--all"})
    dry = bool(flags & {"--dry-run", "-n"})

    mems = c.load_all_memories()
    state_files = []
    if do_state:
        for d in (c.cursors_dir(), c.injected_dir(), c.locks_dir()):
            if d.exists():
                state_files += [p for p in d.iterdir() if p.is_file()]

    if dry:
        print("clear --dry-run — would remove:")
        print(f"  memories: {len(mems)}")
        if do_state:
            print(f"  state:    {len(state_files)} file(s) in cursors/injected/locks")
        if do_logs:
            print(f"  logs:     {c.log_path()}")
        return

    for m in mems:
        (c.memories_dir() / f"{c._safe(m.slug)}.md").unlink(missing_ok=True)
    c.write_index()
    for p in state_files:
        try:
            p.unlink()
        except OSError:
            pass
    if do_logs:
        try:
            c.log_path().unlink(missing_ok=True)
        except OSError:
            pass

    # Log AFTER wiping, so even a freshly-cleared log records that a reset ran.
    c.log("CLEAR", memories=len(mems), state=len(state_files), logs=do_logs)

    noun = "memory" if len(mems) == 1 else "memories"
    print(f"cleared {len(mems)} {noun}; reindexed → {c.index_path()}")
    if do_state:
        print(f"cleared {len(state_files)} state file(s) — next distiller run "
              "re-reads transcripts from the start")
    if do_logs:
        print("cleared memory.log")


def cmd_clear_logs(args):
    """Clear ONLY memory.log — memories and state are left untouched. This is the
    safe, narrow counterpart to `clear --logs` (which ALSO wipes every memory).
    Supports --dry-run/-n to preview the line count first."""
    dry = bool({a for a in args if a in ("--dry-run", "-n")})
    p = c.log_path()
    try:
        lines = len(p.read_text(encoding="utf-8").splitlines())
    except OSError:
        lines = 0
    if dry:
        print(f"clear-logs --dry-run — would clear {lines} log line(s) at {p}")
        return
    p.unlink(missing_ok=True)
    # Re-seed with a single audit line (same convention as `clear`), so a freshly
    # cleared log still records that a reset happened and status keeps working.
    c.log("CLEARLOGS", lines=lines)
    noun = "line" if lines == 1 else "lines"
    print(f"cleared {lines} log {noun} → {p}")


def cmd_prune(_args):
    removed = []
    for m in c.load_all_memories():
        if not m.body.strip() or not m.title.strip():
            (c.memories_dir() / f"{c._safe(m.slug)}.md").unlink(missing_ok=True)
            removed.append(m.slug)
    c.write_index()
    c.log("PRUNE", removed=removed)
    print("pruned: " + (", ".join(removed) if removed else "nothing"))


def cmd_reindex(_args):
    c.write_index()
    print(f"reindexed {len(c.load_all_memories())} memories → {c.index_path()}")


def _recent_log_events(tags, limit=5):
    try:
        lines = c.log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    hits = [ln for ln in lines if any(f" {t} " in f" {ln} " for t in tags)]
    return hits[-limit:]


def cmd_doctor(_args):
    """Self-diagnostic: check everything that can go wrong in one shot."""
    ok_count, warn_count, err_count = 0, 0, 0

    def ok(msg):
        nonlocal ok_count; ok_count += 1; print(f"  ok   {msg}")
    def warn(msg):
        nonlocal warn_count; warn_count += 1; print(f"  WARN {msg}")
    def err(msg):
        nonlocal err_count; err_count += 1; print(f"  ERR  {msg}")

    print("Engram doctor")
    print("=============")
    print()

    # 1. Store writable
    print("store:")
    store = c.store_root()
    if store.exists():
        ok(f"{store}")
        test_file = store / ".doctor-probe"
        try:
            test_file.write_text("probe")
            test_file.unlink()
            ok("store is writable")
        except OSError as e:
            err(f"store is NOT writable: {e}")
    else:
        try:
            c.ensure_store()
            ok(f"created {store}")
        except OSError as e:
            err(f"cannot create store: {e}")

    # 2. Plugin cache vs source freshness
    print("\nplugin cache:")
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    source_root = Path(__file__).resolve().parent.parent
    if plugin_root and plugin_root.exists() and plugin_root.resolve() != source_root.resolve():
        import filecmp
        stale = []
        for dirpath, _, filenames in os.walk(source_root):
            rel = Path(dirpath).relative_to(source_root)
            if any(p in str(rel) for p in ['.git', '__pycache__', '.claude-plugin', '.claude', 'dist']):
                continue
            for fname in filenames:
                src = Path(dirpath) / fname
                cached = plugin_root / rel / fname
                if not cached.exists():
                    stale.append(str(rel / fname) + " (missing in cache)")
                elif not filecmp.cmp(str(src), str(cached), shallow=False):
                    stale.append(str(rel / fname))
        if stale:
            warn(f"cache is stale — {len(stale)} file(s) differ from source:")
            for f in stale[:10]:
                print(f"         {f}")
            if len(stale) > 10:
                print(f"         … and {len(stale) - 10} more")
            print(f"       fix: bash {source_root}/scripts/update.sh")
        else:
            ok("cache matches source")
    elif plugin_root and plugin_root.exists():
        ok("running from source directly")
    else:
        warn("CLAUDE_PLUGIN_ROOT not set — cannot check cache freshness")

    # 3. Distiller reachable
    print("\ndistiller:")
    distiller = Path(__file__).resolve().parent / "distiller.py"
    if distiller.exists():
        ok(f"{distiller.name} exists")
    else:
        err(f"distiller.py not found at {distiller}")
    import shutil
    if shutil.which("claude"):
        ok("'claude' CLI found on PATH")
    else:
        err("'claude' CLI not found on PATH — distiller cannot run")

    # 4. Hooks configuration
    print("\nhooks:")
    hooks_json = source_root / "hooks" / "hooks.json"
    if hooks_json.exists():
        ok("hooks/hooks.json exists")
        try:
            import json as _json
            hooks_data = _json.loads(hooks_json.read_text())
            hook_count = sum(len(v) for v in hooks_data.get("hooks", {}).values())
            ok(f"{hook_count} hook(s) configured across {len(hooks_data.get('hooks', {}))} event(s)")
        except Exception as e:
            err(f"hooks.json parse error: {e}")
    else:
        err(f"hooks/hooks.json not found at {hooks_json}")

    # 5. Recent errors in log
    print("\nlog health:")
    log_file = c.log_path()
    if log_file.exists():
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            ok(f"{len(lines)} line(s) in memory.log")
            errors = [ln for ln in lines if " ERROR " in ln]
            if errors:
                warn(f"{len(errors)} error(s) in log — most recent:")
                for e_line in errors[-3:]:
                    print(f"         {e_line}")
            else:
                ok("no errors in log")
            skips = [ln for ln in lines[-20:] if " SKIP " in ln and "reason=locked" in ln]
            if len(skips) > 3:
                warn(f"{len(skips)} lock-skips in last 20 entries — distiller may be stuck")
        except OSError as e:
            err(f"cannot read log: {e}")
    else:
        warn("no log file yet — normal before first capture")

    # 6. Stale locks
    print("\nlocks:")
    import time as _time
    locks = list(c.locks_dir().glob("*.lock")) if c.locks_dir().exists() else []
    if locks:
        for lp in locks:
            age = _time.time() - lp.stat().st_mtime
            if age > c.STALE_LOCK_SECS:
                warn(f"stale lock: {lp.name} ({int(age)}s old) — will auto-break on next run")
            else:
                ok(f"active lock: {lp.name} ({int(age)}s old)")
    else:
        ok("no locks held")

    # 7. Commands file
    print("\ncommands:")
    cmd_file = Path.home() / ".claude" / "commands" / "engram.md"
    if cmd_file.exists():
        ok(f"{cmd_file}")
    else:
        warn(f"engram.md not in ~/.claude/commands/ — /engram won't work")

    print()
    total = ok_count + warn_count + err_count
    print(f"{ok_count}/{total} ok, {warn_count} warning(s), {err_count} error(s)")
    if err_count:
        sys.exit(1)


COMMANDS = {
    "status": cmd_status, "search": cmd_search, "forget": cmd_forget,
    "clear": cmd_clear, "clear-logs": cmd_clear_logs,
    "prune": cmd_prune, "reindex": cmd_reindex, "doctor": cmd_doctor,
}


def main():
    c.ensure_store()
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    handler = COMMANDS.get(cmd)
    if not handler:
        print(f"unknown command: {cmd}\nusage: {', '.join(COMMANDS)}")
        sys.exit(2)
    handler(argv[1:])


if __name__ == "__main__":
    main()

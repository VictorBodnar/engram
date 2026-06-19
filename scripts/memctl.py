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

`search` deliberately calls common.rank — the exact function per-prompt recall
uses — so what you debug here is what runs live.
"""
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


COMMANDS = {
    "status": cmd_status, "search": cmd_search, "forget": cmd_forget,
    "clear": cmd_clear, "clear-logs": cmd_clear_logs,
    "prune": cmd_prune, "reindex": cmd_reindex,
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

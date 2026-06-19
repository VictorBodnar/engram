#!/usr/bin/env python3
"""Engram SessionStart hook — warm a fresh session with a budgeted memory index.

Deterministic, in-process, no LLM. Renders current-project + global memories as
one-liners (other projects as a count), injects them via additionalContext, and
logs a WARMUP line naming the exact slugs that warmed the session so the set is
always reconstructable from the log.

ALWAYS exits 0 — a memory failure must never break a coding session.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

# Warmup ordering: which memory types earn a budgeted slot first. corrections and
# state are standing behavioral defaults that per-prompt keyword recall may never
# trigger (e.g. "deploy the lambda" shares no token with an aws/cli/sdk memory),
# so they must out-rank topic-triggered knowledge, which recall reliably re-surfaces
# on demand. Unknown/future types sort last (default 3).
WARMUP_PRIORITY = {"correction": 0, "state": 1, "knowledge": 2}


def build_context(memories, project):
    here, other = [], {}
    for m in memories:
        if m.project == project or m.project == "global":
            here.append(m)
        else:
            other[m.project] = other.get(m.project, 0) + 1

    # Behavioral defaults first, then newest. The budget loop below keeps a PREFIX
    # of this order, so type priority decides who survives truncation: a standing
    # correction outranks newer knowledge, since recall can't be trusted to surface
    # the correction just-in-time but reliably re-finds the knowledge by keyword.
    here.sort(key=lambda m: (WARMUP_PRIORITY.get(m.type, 3),
                             c._neg_date(m.updated), m.slug))

    lines = [
        "# Engram memory",
        f"{len(memories)} memories stored. Full files: {c.memories_dir()}.",
        "Capture is automatic (async); relevant memories are injected per prompt.",
        f"Current project: {project}",
        "",
        "## Relevant now (this project + global)",
    ]
    head = "\n".join(lines) + "\n"
    out, used, shown = [head], len(head), []
    for m in here:
        line = f"- {m.slug} ({m.type}) — {m.title}\n"
        if used + len(line) > c.SESSIONSTART_BUDGET:
            out.append(f"- … ({len(here) - len(shown)} more on disk)\n")
            break
        out.append(line)
        used += len(line)
        shown.append(m.slug)
    if other:
        tail = "\n## Other projects\n" + "".join(
            f"- {p}: {n}\n" for p, n in sorted(other.items())
        )
        if used + len(tail) <= c.SESSIONSTART_BUDGET:
            out.append(tail)
    return "".join(out), shown


def auto_memory_advisory(source: str) -> str:
    """A one-line nudge to disable Claude Code's native auto-memory. A plugin can't
    set settings.json `env` itself, so we recommend it here — but only at real session
    startup and only while it's still unset, so the note disappears once acted on."""
    if source != "startup" or os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"):
        return ""
    return ("\n\n> **Engram:** set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in "
            "`~/.claude/settings.json` so Engram and Claude Code's native auto-memory "
            "don't both capture and inject the same facts.")


def main():
    data = c.read_stdin_json()
    try:
        if c.is_distiller_child():   # the distiller's own `claude -p` gets no recall
            c.emit_empty()
            return
        c.ensure_store()
        try:
            c.housekeep()   # GC stale per-session state even if SessionEnd never fired
        except Exception:
            pass            # best-effort cleanup must never block warmup
        session = data.get("session_id", "unknown")
        project = c.project_from_cwd(data.get("cwd"))
        advisory = auto_memory_advisory(data.get("source", ""))
        memories = c.load_all_memories()
        if not memories:
            c.log("WARMUP", session=session, project=project, memories=0, slugs=[])
            if advisory:
                c.emit_additional_context("SessionStart", advisory.lstrip())
            else:
                c.emit_empty()
            return
        context, shown = build_context(memories, project)
        c.log("WARMUP", session=session, project=project,
              memories=len(shown), slugs=shown)
        c.emit_additional_context("SessionStart", context + advisory)
    except Exception as e:  # never let memory break the session
        c.log("ERROR", hook="session_start", err=repr(e))
        c.emit_empty()


if __name__ == "__main__":
    main()
    sys.exit(0)

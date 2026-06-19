#!/usr/bin/env python3
"""Engram UserPromptSubmit hook — deterministic per-prompt recall.

Tokenize the prompt, score every memory (the gate lives in common.score_memory),
inject the top-3 qualifying full bodies as <recalled-memories>, and never inject
the same memory twice in one session. Logs a RECALL line with the injected
slugs+scores AND the skipped near-misses, so "why did THIS load, not THAT?" is
answerable from the log by a hand-recomputable integer.

ALWAYS exits 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


def main():
    data = c.read_stdin_json()
    try:
        if c.is_distiller_child():   # the distiller's own `claude -p` gets no recall
            c.emit_empty()
            return
        c.ensure_store()
        session = data.get("session_id", "unknown")
        project = c.project_from_cwd(data.get("cwd"))
        prompt = data.get("prompt", "") or ""
        tokens = c.tokenize(prompt)

        memories = c.load_all_memories()
        qualifying, scored = c.rank(tokens, memories, project)

        already = c.read_injected(session)
        # dedup per session BEFORE taking the top-N, so a fresh relevant memory
        # can claim a slot a previously-spoken one would have taken
        fresh = [(m, s) for (m, s) in qualifying if m.slug not in already]

        selected, used = [], 0
        for m, s in fresh:
            block = render_memory(m)
            if used + len(block) > c.RECALL_BUDGET:
                break
            selected.append((m, s, block))
            used += len(block)

        injected_pairs = [f"{m.slug}:{s}" for m, s, _ in selected]
        sel_slugs = {m.slug for m, _, _ in selected}
        skipped = [f"{m.slug}:{s}" for m, s in scored if m.slug not in sel_slugs][:6]
        c.log("RECALL", session=session, project=project,
              prompt_tokens=len(tokens), injected=len(selected),
              slugs=injected_pairs, skipped=skipped)

        if not selected:
            c.emit_empty()
        else:
            body = "<recalled-memories>\n" + "\n".join(b for _, _, b in selected) \
                   + "</recalled-memories>"
            c.add_injected(session, list(sel_slugs))
            c.emit_additional_context("UserPromptSubmit", body)

        if not c.stop_hook_active(data):
            transcript = data.get("transcript_path", "")
            ok = c.spawn_distiller(session, transcript)
            c.log("SPAWN", hook="UserPromptSubmit", session=session, ok=ok)
    except Exception as e:
        c.log("ERROR", hook="user_prompt", err=repr(e))
        c.emit_empty()


def render_memory(m):
    kw = ", ".join(m.keywords)
    return (
        f"<memory slug=\"{m.slug}\" type=\"{m.type}\" project=\"{m.project}\">\n"
        f"{m.title}\n"
        f"keywords: {kw}\n\n"
        f"{m.body}\n"
        f"</memory>\n"
    )


if __name__ == "__main__":
    main()
    sys.exit(0)

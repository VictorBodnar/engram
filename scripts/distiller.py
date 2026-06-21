#!/usr/bin/env python3
"""Engram distiller — the detached worker, and the ONLY place an LLM ever runs.

Spawned (never awaited) by the Stop / PreCompact / SessionEnd hooks. It reads the
NEW bytes of the session transcript via a per-session byte cursor, asks Haiku
"what's worth keeping?", and writes/updates markdown memories.

Invariants:
  * One per-session lock — overlapping runs for the same session can't race;
    the cursor guarantees the next run catches up, so a skipped run loses nothing.
  * The cursor is advanced even when the LLM call or parse FAILS — a lost
    distillation is far cheaper than a poison-input retry loop.
  * CLAUDE_MEMORY_DISTILLER=1 is set on the `claude -p` child so its own hooks
    no-op. Without this we'd recurse forever (the claude-mem bug).

CLAUDE_MEMORY_FAKE_LLM=<path> swaps the live call for a canned JSON file, which
makes the whole pipeline testable offline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #
def acquire_lock(session: str) -> bool:
    lp = c.lock_path(session)
    try:
        if lp.exists() and time.time() - lp.stat().st_mtime > c.STALE_LOCK_SECS:
            lp.unlink()  # break a stale lock from a crashed run
    except OSError:
        pass
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except (FileExistsError, OSError):
        return False


def release_lock(session: str) -> None:
    try:
        c.lock_path(session).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Cursor — read only the new transcript bytes
# --------------------------------------------------------------------------- #
def read_new_bytes(transcript_path: str, cursor: int):
    p = Path(transcript_path) if transcript_path else None
    if not p or not p.exists():
        return b"", cursor
    try:
        size = p.stat().st_size
        if cursor > size:           # transcript was rotated/truncated
            cursor = 0
        with open(p, "rb") as f:
            f.seek(cursor)
            data = f.read()
            return data, f.tell()
    except OSError:
        return b"", cursor


# --------------------------------------------------------------------------- #
# Digest — parse transcript JSONL into USER/ASSISTANT text + one-line tool notes
# --------------------------------------------------------------------------- #
def render_entry(obj: dict):
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    role = (msg.get("role") or obj.get("type") or "").lower()
    content = msg.get("content")
    texts, tools, has_text = [], [], False

    if isinstance(content, str):
        if content.strip():
            texts.append(content.strip())
            has_text = True
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                texts.append(b["text"].strip())
                has_text = True
            elif t == "tool_use":
                tools.append(f"[tool {b.get('name', '?')}]")
            elif t == "tool_result":
                tools.append("[tool result]")

    label = ("USER" if role in ("user", "human")
             else "ASSISTANT" if role in ("assistant", "ai")
             else (role.upper() or "MSG"))
    body = " ".join(texts)[:1500]
    if body:
        return f"{label}: {body}", has_text
    if tools:
        return f"{label}: {' '.join(tools)}", False
    return "", False


def build_digest(raw_bytes: bytes):
    text = raw_bytes.decode("utf-8", "replace")
    entries, substantive = [], False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        rendered, has_text = render_entry(obj)
        if rendered:
            entries.append(rendered)
            substantive = substantive or has_text
    digest = "\n".join(entries)
    if len(digest) > c.DIGEST_CAP:        # keep the most recent content
        digest = digest[-c.DIGEST_CAP:]
    return digest, substantive


# --------------------------------------------------------------------------- #
# The prompt — describes what's worth keeping and pins a strict JSON contract.
# This is the artifact the spec describes but never spells out verbatim.
# --------------------------------------------------------------------------- #
def build_prompt(project: str, digest: str, index_lines: str) -> str:
    return f"""You are Engram's memory distiller for Claude Code. Read the transcript \
excerpt from a coding session and extract the durable facts worth remembering across \
future sessions. Capture sparingly but RELIABLY: if a fact below is present, record it; \
otherwise record nothing. Do not invent or pad.

CAPTURE a memory whenever the excerpt contains one of these:
- correction: a correction or working preference the user stated (commands, style, do/don't,
  tool choices, library preferences). Even a single short sentence counts.
  e.g. "Don't chain shell commands with && — split into separate Bash calls."
  e.g. "i wana use aws cli over sdk" → preference for CLI over SDK.
- knowledge: a non-obvious, durable fact about this codebase or its environment — something a
  future session would waste time rediscovering.
  e.g. "payments-api: integration tests silently no-op unless LOCALSTACK=1 is set."
- state: a project decision or open item worth recalling later.
  e.g. "Chose SQS over Kafka for ingest; queue-migration TODO still open."

A clearly-stated user correction, a non-obvious codebase/environment gotcha, or an explicit \
decision MUST be captured — even if it is only one sentence. Brevity does not mean unimportant. \
A short "use X over Y" or "prefer X" is a durable preference — capture it.

Do NOT capture: routine task steps, chit-chat, things obvious from reading the code, \
secrets/credentials, or anything transient.

Current project: {project}

Existing memories (prefer UPDATE of one of these over creating a near-duplicate):
{index_lines or "(none yet)"}

Return ONLY a JSON array of action objects — no prose, no code fence. Each object:
{{"action": "create" | "update", "slug": "kebab-case-id", "type": "correction|knowledge|state", \
"title": "short title", "project": "{project}|global", "keywords": ["lowercase", "terms"], \
"body": "<=120 words, concrete and self-contained"}}

Rules:
- Return [] ONLY when the excerpt is purely routine task execution with no preferences, decisions, or gotchas.
- Use "update" with an existing slug when refining/correcting it; otherwise "create" a new kebab-case slug.
- project: "{project}" for repo-specific facts, "global" for machine-wide preferences/corrections.
- keywords: the lowercase words a future prompt would use to recall this memory.

Transcript excerpt:
{digest}
"""


# --------------------------------------------------------------------------- #
# LLM call + output parsing
# --------------------------------------------------------------------------- #
# Replace Claude Code's default agentic-coding system prompt (and its CLAUDE.md
# context) with a minimal extractor framing. Without this the distiller inherits
# the full coding-agent prompt and judges unreliably — empirically it flip-flops
# between [] and a correct capture on the SAME input. This framing made capture
# 3/3 reliable in testing, costs far fewer tokens, and — unlike --bare — keeps
# working under OAuth auth (--bare requires ANTHROPIC_API_KEY and breaks on a
# subscription login).
DISTILLER_SYSTEM = (
    "You are a precise information-extraction tool, not a coding assistant. "
    "Follow the user's instructions exactly and output only the JSON array they ask for."
)


def call_llm(prompt: str) -> str:
    fake = os.environ.get("CLAUDE_MEMORY_FAKE_LLM")
    if fake:
        return Path(fake).read_text(encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_MEMORY_DISTILLER"] = "1"   # the child's own hooks must no-op
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json",
         "--system-prompt", DISTILLER_SYSTEM],
        capture_output=True, text=True, timeout=c.LLM_TIMEOUT, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def extract_json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    i, j = text.find("["), text.rfind("]")
    if i == -1 or j == -1 or j < i:
        raise ValueError("no JSON array found in model output")
    return json.loads(text[i:j + 1])


def parse_actions(raw: str):
    text = raw.strip()
    # unwrap the `claude --output-format json` envelope if present.
    # claude -p can emit multiple JSON lines (progress events then result);
    # try each line last-to-first to find the result envelope.
    env = None
    try:
        env = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
                break
            except (ValueError, json.JSONDecodeError):
                continue
    if isinstance(env, dict) and "result" in env:
        text = env["result"]
    actions = extract_json_array(text)
    if not isinstance(actions, list):
        raise ValueError("model output is not a JSON array")
    return actions


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
def apply_actions(actions, session: str, project: str):
    created = updated = 0
    for a in actions:
        if not isinstance(a, dict):
            continue
        slug = c._safe(a.get("slug", "")).strip("_")
        if not slug:
            continue
        mtype = a.get("type", "knowledge")
        if mtype not in c.VALID_TYPES:
            mtype = "knowledge"
        title = (a.get("title") or "").strip()
        proj = (a.get("project") or project or "global").strip()
        kw = a.get("keywords") or []
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.split(",") if k.strip()]
        kw = [str(k).strip().lower() for k in kw if str(k).strip()]
        body = c.clamp_body((a.get("body") or "").strip())

        existing = c.load_memory(slug)
        today = c.today_iso()
        if a.get("action") == "update" and existing:
            existing.body = body or existing.body
            existing.keywords = sorted(set(existing.keywords) | set(kw))
            existing.title = title or existing.title
            existing.type = mtype
            existing.updated = today
            c.save_memory(existing)
            updated += 1
            c.log("UPDATE", session=session, slug=slug, type=mtype, project=existing.project)
        else:
            mem = c.Memory(slug, mtype, title, proj, sorted(set(kw)), today, today, body)
            c.save_memory(mem)
            created += 1
            c.log("CREATE", session=session, slug=slug, type=mtype, project=proj)
    return created, updated


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="unknown")
    ap.add_argument("--transcript", default="")
    args = ap.parse_args()
    session = args.session or "unknown"

    c.ensure_store()
    if not acquire_lock(session):
        c.log("SKIP", worker="distiller", session=session, reason="locked")
        return

    try:
        cursor = c.read_cursor(session)
        data, new_offset = read_new_bytes(args.transcript, cursor)
        if not data:
            c.log("SKIP", worker="distiller", session=session,
                  reason="no_new_bytes", transcript=args.transcript or "(empty)",
                  cursor=cursor)
            return

        digest, substantive = build_digest(data)
        if not substantive:
            c.write_cursor(session, new_offset)   # free gate — no LLM
            c.log("SKIP", worker="distiller", session=session,
                  reason="not_substantive", offset=new_offset)
            return

        project = c.project_from_cwd()
        index_lines = "\n".join(
            f"- {m.slug}: {m.title} (kw: {', '.join(m.keywords)})"
            for m in c.load_all_memories()
        )
        prompt = build_prompt(project, digest, index_lines)

        raw = ""
        try:
            raw = call_llm(prompt)
            actions = parse_actions(raw)
            created, updated = apply_actions(actions, session, project)
            c.write_index()
            c.log("DISTILL", session=session, project=project,
                  actions=len(actions), created=created, updated=updated)
        except Exception as e:
            c.log("ERROR", worker="distiller", session=session, err=repr(e)[:300])
            if raw:
                c.atomic_write(c.store_root() / "logs" / "distiller-last-error.txt", raw)
        finally:
            c.write_cursor(session, new_offset)   # ADVANCE even on failure
    finally:
        release_lock(session)


if __name__ == "__main__":
    main()
    sys.exit(0)

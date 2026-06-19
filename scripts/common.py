#!/usr/bin/env python3
"""Engram — shared core for every hook, the distiller, and memctl.

One module so that *what you debug is what runs*: the scoring function used by
per-prompt recall is the exact same one `/engram search` calls; the frontmatter
parser that reads a memory is the one that writes it.

stdlib only — no third-party deps, for maximum determinism and portability.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Tunable constants (kept here as plain numbers — the scoring ladder grows by
# editing these, never by adding an opaque model in the loop).
# --------------------------------------------------------------------------- #
KW_WEIGHT = 3            # prompt token ∈ memory keywords
TITLE_WEIGHT = 2         # prompt token ∈ memory title
PROJECT_BONUS = 2        # memory.project == current project
CORRECTION_BONUS = 1     # memory.type == "correction"
THRESHOLD = 3            # minimum score to be injected
TOP_N = 3                # max memories injected per prompt

SESSIONSTART_BUDGET = 9000   # chars of additionalContext at SessionStart
RECALL_BUDGET = 8000         # chars of <recalled-memories> per prompt
INDEX_BUDGET = 25000         # chars of generated INDEX.md
DIGEST_CAP = 12000           # chars of transcript digest handed to the LLM
BODY_WORD_CAP = 120          # words per memory body

STALE_LOCK_SECS = 600        # 10 min — break a distiller lock older than this
STATE_TTL_DAYS = 7           # SessionEnd deletes per-session state older than this
LOG_ROTATE_BYTES = 2 * 1024 * 1024   # rotate memory.log at 2 MB
LLM_TIMEOUT = 180            # seconds for the `claude -p` round-trip

VALID_TYPES = ("correction", "knowledge", "state")

# A compact English stopword set. Tuned so that a sentence like
# "the integration tests pass but nothing seems to run" reduces to the
# signal-bearing tokens {integration, tests, pass, run}.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "could", "did", "do", "does", "doing", "done", "for", "from", "had",
    "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "just", "me", "my", "no", "nor",
    "not", "nothing", "now", "of", "on", "only", "or", "our", "out", "over",
    "seem", "seems", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "to", "too", "up", "us", "very", "was", "we", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you",
    "your", "yours", "im", "ive", "dont", "doesnt", "isnt", "youre",
}


# --------------------------------------------------------------------------- #
# Paths — the store lives outside the plugin so it survives reinstalls.
# Default ~/.claude/memory-store, overridable via CLAUDE_MEMORY_HOME.
# --------------------------------------------------------------------------- #
def store_root() -> Path:
    env = os.environ.get("CLAUDE_MEMORY_HOME")
    if not env:
        return Path.home() / ".claude" / "memory-store"
    # Claude Code does NOT variable-expand settings.json `env` *values* (only
    # hook command strings), so CLAUDE_MEMORY_HOME can arrive as the literal
    # "${CLAUDE_PROJECT_DIR}/.engram-store". Expand it here — substituting
    # CLAUDE_PROJECT_DIR explicitly with a cwd fallback — so we can never again
    # create a directory literally named "${CLAUDE_PROJECT_DIR}".
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
    expanded = os.path.expandvars(env.replace("${CLAUDE_PROJECT_DIR}", project_dir))
    return Path(expanded).expanduser()


def memories_dir() -> Path:
    return store_root() / "memories"


def index_path() -> Path:
    return store_root() / "INDEX.md"


def cursors_dir() -> Path:
    return store_root() / "state" / "cursors"


def injected_dir() -> Path:
    return store_root() / "state" / "injected"


def locks_dir() -> Path:
    return store_root() / "state" / "locks"


def log_path() -> Path:
    return store_root() / "logs" / "memory.log"


def ensure_store() -> None:
    """Create the store tree if missing. Cheap; safe to call on every hook."""
    for d in (memories_dir(), cursors_dir(), injected_dir(), locks_dir(),
              log_path().parent):
        d.mkdir(parents=True, exist_ok=True)


def cursor_path(session_id: str) -> Path:
    return cursors_dir() / f"{_safe(session_id)}.json"


def injected_path(session_id: str) -> Path:
    return injected_dir() / f"{_safe(session_id)}.json"


def lock_path(session_id: str) -> Path:
    return locks_dir() / f"{_safe(session_id)}.lock"


def _safe(name: str) -> str:
    """Sanitize an id for use as a filename."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "unknown"))


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def today_iso() -> str:
    return date.today().isoformat()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Atomic write — temp file in the same dir + os.replace, so a concurrent
# reader (or a second session's distiller) never sees a half-written file.
# --------------------------------------------------------------------------- #
def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# The Memory model + hand-rolled flat frontmatter (no YAML dependency).
# --------------------------------------------------------------------------- #
@dataclass
class Memory:
    slug: str
    type: str
    title: str
    project: str
    keywords: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    body: str = ""

    def to_text(self) -> str:
        kw = ", ".join(self.keywords)
        return (
            "---\n"
            f"slug: {self.slug}\n"
            f"type: {self.type}\n"
            f"title: {self.title}\n"
            f"project: {self.project}\n"
            f"keywords: {kw}\n"
            f"created: {self.created}\n"
            f"updated: {self.updated}\n"
            "---\n"
            f"{self.body.rstrip()}\n"
        )


def parse_memory(text: str, slug_fallback: str = "") -> Memory | None:
    """Parse a flat `key: value` frontmatter block + markdown body.

    ~10 lines of real work; deliberately not a YAML parser.
    """
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    # find the closing fence
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    body = "\n".join(lines[end + 1:]).strip()
    kw = [k.strip() for k in meta.get("keywords", "").split(",") if k.strip()]
    return Memory(
        slug=meta.get("slug", slug_fallback),
        type=meta.get("type", "knowledge"),
        title=meta.get("title", ""),
        project=meta.get("project", "global"),
        keywords=kw,
        created=meta.get("created", ""),
        updated=meta.get("updated", ""),
        body=body,
    )


def load_memory(slug: str) -> Memory | None:
    p = memories_dir() / f"{_safe(slug)}.md"
    if not p.exists():
        return None
    try:
        return parse_memory(p.read_text(encoding="utf-8"), slug_fallback=slug)
    except OSError:
        return None


def load_all_memories() -> list[Memory]:
    out: list[Memory] = []
    d = memories_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            mem = parse_memory(p.read_text(encoding="utf-8"), slug_fallback=p.stem)
        except OSError:
            mem = None
        if mem:
            out.append(mem)
    return out


def save_memory(mem: Memory) -> None:
    atomic_write(memories_dir() / f"{_safe(mem.slug)}.md", mem.to_text())


def clamp_body(body: str, cap: int = BODY_WORD_CAP) -> str:
    words = body.split()
    if len(words) <= cap:
        return body.strip()
    return " ".join(words[:cap]).rstrip() + " …"


# --------------------------------------------------------------------------- #
# Tokenize + score. The gate is the load-bearing rule: a memory must earn at
# least one keyword/title textual hit; project/type only *boost* a real hit and
# can never qualify a memory on their own.
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if len(t) >= 2 and t not in STOPWORDS]


def score_memory(prompt_tokens, mem: Memory, cwd_project: str) -> int:
    """Return the integer recall score for one memory against a prompt.

    Anyone can recompute this by hand from the log — that is the whole point.
    """
    ptoks = set(prompt_tokens)
    kw_tokens = set(tokenize(" ".join(mem.keywords)))
    title_tokens = set(tokenize(mem.title))

    s = 0
    text_hits = 0
    for tok in ptoks:
        if tok in kw_tokens:
            s += KW_WEIGHT
            text_hits += 1
        if tok in title_tokens:
            s += TITLE_WEIGHT
            text_hits += 1

    if text_hits == 0:          # THE GATE — no textual hit ⇒ never a candidate
        return 0

    if cwd_project and mem.project == cwd_project:
        s += PROJECT_BONUS
    if mem.type == "correction":
        s += CORRECTION_BONUS
    return s


def rank(prompt_tokens, memories, cwd_project: str):
    """Score every memory; return (qualifying, scored_all), both sorted by score
    then recency, each entry (memory, score). `qualifying` = score ≥ THRESHOLD
    (NOT yet truncated to TOP_N — the caller dedups per-session first, then
    slices). `scored_all` = every score>0, so the RECALL log can show the
    near-miss candidates that were skipped."""
    scored = []
    for mem in memories:
        sc = score_memory(prompt_tokens, mem, cwd_project)
        if sc > 0:
            scored.append((mem, sc))
    # score descending, then most-recent first, then slug for stable ties
    scored.sort(key=lambda ms: (-ms[1], _neg_date(ms[0].updated), ms[0].slug))
    qualifying = [ms for ms in scored if ms[1] >= THRESHOLD]
    return qualifying, scored


def _neg_date(iso: str):
    """Sort key that makes more-recent dates come first."""
    return tuple(-n for n in _date_ints(iso))


def _date_ints(iso: str):
    parts = re.findall(r"\d+", iso or "")
    return tuple(int(p) for p in parts[:3]) or (0,)


# --------------------------------------------------------------------------- #
# Index render — the persistent generated INDEX.md (one line per memory,
# newest first, char-budgeted). Never hand-edited; regenerable with reindex.
#
# IMPORTANT: INDEX.md is a human/tool BROWSE MIRROR, not a runtime input. It is
# write-only from the program's side — regenerated on every mutation (distiller
# capture, forget/clear/prune/reindex) purely so you can cat/grep/open one file to
# see the whole store. The runtime NEVER reads it: warmup (session_start) and
# recall (user_prompt) both read the LIVE store via load_all_memories(). Do NOT
# wire injection to read this file — doing so would couple recall to a cached
# mirror that can lag the real memories, the exact staleness bug this avoids.
# --------------------------------------------------------------------------- #
def index_line(mem: Memory) -> str:
    kw = ", ".join(mem.keywords)
    return f"- {mem.slug} ({mem.type} · {mem.project}) — {mem.title} · kw: {kw}"


def render_index(memories, budget: int = INDEX_BUDGET) -> str:
    mems = sorted(memories, key=lambda m: (_neg_date(m.updated), m.slug))
    header = f"# Engram memory index ({len(mems)} memories)\n\n"
    lines, used = [], len(header)
    for m in mems:
        line = index_line(m) + "\n"
        if used + len(line) > budget:
            lines.append(f"- … ({len(mems) - len(lines)} more not shown)\n")
            break
        lines.append(line)
        used += len(line)
    return header + "".join(lines)


def write_index() -> None:
    """Regenerate the INDEX.md browse mirror from the live store. Called after any
    mutation. Nothing in the runtime reads its output — see the note above."""
    atomic_write(index_path(), render_index(load_all_memories()))


# --------------------------------------------------------------------------- #
# Project detection — git-root basename, else cwd basename, else "global".
# --------------------------------------------------------------------------- #
def project_from_cwd(cwd: str | None = None) -> str:
    start = Path(cwd).expanduser() if cwd else Path.cwd()
    try:
        start = start.resolve()
    except OSError:
        pass
    p = start
    while True:
        if (p / ".git").exists():
            return p.name
        if p.parent == p:
            break
        p = p.parent
    return start.name or "global"


# --------------------------------------------------------------------------- #
# Per-session state: byte cursor + injected-slug set.
# --------------------------------------------------------------------------- #
def read_cursor(session_id: str) -> int:
    try:
        return int(json.loads(cursor_path(session_id).read_text()).get("offset", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def write_cursor(session_id: str, offset: int) -> None:
    atomic_write(cursor_path(session_id), json.dumps({"offset": int(offset)}))


def read_injected(session_id: str) -> set[str]:
    try:
        return set(json.loads(injected_path(session_id).read_text()).get("slugs", []))
    except (OSError, ValueError, json.JSONDecodeError):
        return set()


def add_injected(session_id: str, slugs) -> None:
    cur = read_injected(session_id)
    cur.update(slugs)
    atomic_write(injected_path(session_id), json.dumps({"slugs": sorted(cur)}))


# --------------------------------------------------------------------------- #
# Recursion / loop guards.
# --------------------------------------------------------------------------- #
def is_distiller_child() -> bool:
    """True when we are inside the headless `claude -p` the distiller spawned;
    its own Stop/PreCompact/SessionEnd hooks must no-op or we recurse forever."""
    return os.environ.get("CLAUDE_MEMORY_DISTILLER") == "1"


def stop_hook_active(data: dict) -> bool:
    """Stop-hook re-entry guard — Claude Code sets this when a Stop hook is
    already in flight."""
    return bool(data.get("stop_hook_active"))


# --------------------------------------------------------------------------- #
# Detached spawn — fire-and-forget the distiller. The session NEVER waits.
# --------------------------------------------------------------------------- #
def spawn_distiller(session_id: str, transcript_path: str) -> bool:
    distiller = Path(__file__).resolve().parent / "distiller.py"
    if not distiller.exists():
        log("ERROR", worker="spawn", session=session_id,
            err=f"distiller not found: {distiller}")
        return False
    try:
        subprocess.Popen(
            [sys.executable, str(distiller),
             "--session", str(session_id or ""),
             "--transcript", str(transcript_path or "")],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,           # detach from the hook's process group
            env=os.environ.copy(),
        )
        return True
    except (OSError, ValueError) as e:
        log("ERROR", worker="spawn", session=session_id, err=repr(e)[:200])
        return False


# --------------------------------------------------------------------------- #
# I/O for hooks: never-throwing stdin reader + stdout emit.
# --------------------------------------------------------------------------- #
def read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (ValueError, json.JSONDecodeError, OSError):
        return {}


def emit_additional_context(event_name: str, context: str) -> None:
    """Inject text into the conversation via the documented hook channel."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }))


def emit_empty() -> None:
    print("{}")


# --------------------------------------------------------------------------- #
# Structured, greppable logging — the troubleshooting surface.
# Format: `<ts> <EVENT> key=value key=value ...`
# List values render as [a,b,c] so a single grep answers "which memories
# warmed this session?" (WARMUP) and "which loaded on this prompt?" (RECALL).
# --------------------------------------------------------------------------- #
def _fmt_val(v) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(str(x) for x in v) + "]"
    s = str(v)
    return s.replace("\n", " ")


def log(event: str, **fields) -> None:
    try:
        ensure_store()
        parts = [now_stamp(), event]
        for k, v in fields.items():
            parts.append(f"{k}={_fmt_val(v)}")
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(" ".join(parts) + "\n")
    except OSError:
        pass  # logging must never break a hook


def rotate_log_if_big() -> None:
    p = log_path()
    try:
        if p.exists() and p.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(p, p.with_suffix(".log.1"))
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Shared capture-hook body (Stop / PreCompact / SessionEnd): guard, spawn the
# detached distiller, emit {}. NO LLM runs here — that is the whole point.
# --------------------------------------------------------------------------- #
def run_capture_hook(event_name: str, data: dict) -> None:
    if is_distiller_child() or stop_hook_active(data):
        log("SKIP", hook=event_name, reason="guard")
        emit_empty()
        return
    session = data.get("session_id", "unknown")
    transcript = data.get("transcript_path", "")
    ok = spawn_distiller(session, transcript)
    log("SPAWN", hook=event_name, session=session, ok=ok)
    emit_empty()


def housekeep() -> None:
    """SessionEnd maintenance: drop per-session state older than the TTL and
    rotate the log. Best-effort; never raises."""
    cutoff = time.time() - STATE_TTL_DAYS * 86400
    for d in (cursors_dir(), injected_dir(), locks_dir()):
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    rotate_log_if_big()

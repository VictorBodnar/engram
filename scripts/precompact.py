#!/usr/bin/env python3
"""Engram PreCompact hook — distill before compaction summarizes learnings away.

Same fire-and-forget spawn as Stop; this is the essential safety net so a
correction made just before a compaction is captured first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

if __name__ == "__main__":
    data = c.read_stdin_json()
    try:
        c.run_capture_hook("PreCompact", data)
    except Exception as e:
        c.log("ERROR", hook="precompact", err=repr(e))
        c.emit_empty()
    sys.exit(0)

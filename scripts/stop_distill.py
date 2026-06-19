#!/usr/bin/env python3
"""Engram Stop hook — fire-and-forget the distiller, exit in milliseconds.

NO LLM here. Guards against Stop-hook re-entry (stop_hook_active) and against the
headless `claude -p` child the distiller spawns (CLAUDE_MEMORY_DISTILLER=1).
Returns {} so it never feeds actionable text back into the loop.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

if __name__ == "__main__":
    data = c.read_stdin_json()
    try:
        c.run_capture_hook("Stop", data)
    except Exception as e:
        c.log("ERROR", hook="stop", err=repr(e))
        c.emit_empty()
    sys.exit(0)

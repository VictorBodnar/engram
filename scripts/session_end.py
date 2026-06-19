#!/usr/bin/env python3
"""Engram SessionEnd hook — final catch-up distill + housekeeping.

Same fire-and-forget spawn as Stop (so anything learned in the last turn is
captured), then deletes per-session state older than the TTL and rotates the log.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

if __name__ == "__main__":
    data = c.read_stdin_json()
    try:
        c.run_capture_hook("SessionEnd", data)
        if not c.is_distiller_child():
            c.housekeep()
    except Exception as e:
        c.log("ERROR", hook="session_end", err=repr(e))
        c.emit_empty()
    sys.exit(0)

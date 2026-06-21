#!/usr/bin/env python3
"""Engram lifecycle manager — install, update, uninstall, repair, verify.

The simplest possible deployment: writes hook entries directly into
~/.claude/settings.json pointing at the scripts wherever they live.
No plugin registry, no cache directories, no symlinks, no version-tagged paths.

Edit any script → it's live immediately. Clone anywhere → install once → done.

Usage:
    python3 scripts/manage.py install             # register hooks + command
    python3 scripts/manage.py uninstall [--yes]   # full teardown
    python3 scripts/manage.py repair              # fix broken install in-place
    python3 scripts/manage.py verify              # exit 0 if healthy, 1 if not
    python3 scripts/manage.py migrate             # move from plugin system to direct hooks
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SOURCE_DIR / "scripts"
CLAUDE_DIR = Path.home() / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"
COMMAND_DIR = CLAUDE_DIR / "commands"

# Marker so we can find/update our hooks among others in settings.json
HOOK_MARKER = "# engram-managed-hook"

HOOK_EVENTS = {
    "SessionStart": {"script": "session_start.py", "timeout": 10},
    "UserPromptSubmit": {"script": "user_prompt.py", "timeout": 10},
    "Stop": {"script": "stop_distill.py", "timeout": 10},
    "PreCompact": {"script": "precompact.py", "timeout": 10},
    "SessionEnd": {"script": "session_end.py", "timeout": 10},
}


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def warn(msg: str) -> None:
    print(f"  WARN {msg}")


def fail(msg: str) -> None:
    print(f"  ERR  {msg}")
    sys.exit(1)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def hook_command(script: str) -> str:
    """Build the hook command string for a given script."""
    script_path = SCRIPTS_DIR / script
    return f'python3 "{script_path}" {HOOK_MARKER}'


def is_our_hook(entry: dict) -> bool:
    """Check if a hook entry belongs to Engram."""
    for h in entry.get("hooks", []):
        if HOOK_MARKER in h.get("command", ""):
            return True
    return False


# --------------------------------------------------------------------------- #
# Hook management
# --------------------------------------------------------------------------- #
def install_hooks(settings: dict) -> dict:
    """Add Engram hooks to settings, preserving any existing non-Engram hooks."""
    hooks = settings.setdefault("hooks", {})
    for event, cfg in HOOK_EVENTS.items():
        existing = hooks.get(event, [])
        # Remove any previous Engram hooks
        existing = [e for e in existing if not is_our_hook(e)]
        # Add our hook
        our_entry = {
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": hook_command(cfg["script"]),
                "timeout": cfg["timeout"],
            }],
        }
        existing.append(our_entry)
        hooks[event] = existing
    return settings


def remove_hooks(settings: dict) -> dict:
    """Remove all Engram hooks from settings."""
    hooks = settings.get("hooks", {})
    for event in list(hooks.keys()):
        hooks[event] = [e for e in hooks[event] if not is_our_hook(e)]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


# --------------------------------------------------------------------------- #
# Plugin system cleanup (migration)
# --------------------------------------------------------------------------- #
def remove_plugin_traces() -> list[str]:
    """Remove all traces of the old plugin-based install. Returns list of actions taken."""
    actions = []

    # Remove from installed_plugins.json
    installed = CLAUDE_DIR / "plugins" / "installed_plugins.json"
    if installed.exists():
        d = read_json(installed)
        plugins = d.get("plugins", {})
        removed_keys = [k for k in plugins if "engram" in k]
        for k in removed_keys:
            del plugins[k]
        if removed_keys:
            write_json(installed, d)
            actions.append(f"removed {removed_keys} from installed_plugins.json")

    # Remove from known_marketplaces.json
    known_mkt = CLAUDE_DIR / "plugins" / "known_marketplaces.json"
    if known_mkt.exists():
        d = read_json(known_mkt)
        removed_keys = [k for k in d if "engram" in k]
        for k in removed_keys:
            del d[k]
        if removed_keys:
            write_json(known_mkt, d)
            actions.append(f"removed {removed_keys} from known_marketplaces.json")

    # Remove from extraKnownMarketplaces in settings.json
    settings = read_json(SETTINGS)
    extra = settings.get("extraKnownMarketplaces", {})
    removed_keys = [k for k in extra if "engram" in k]
    for k in removed_keys:
        del extra[k]
    if removed_keys:
        if not extra:
            settings.pop("extraKnownMarketplaces", None)
        write_json(SETTINGS, settings)
        actions.append(f"removed {removed_keys} from extraKnownMarketplaces")

    # Remove from enabledPlugins in settings.json
    settings = read_json(SETTINGS)
    enabled = settings.get("enabledPlugins", {})
    removed_keys = [k for k in enabled if "engram" in k]
    for k in removed_keys:
        del enabled[k]
    if removed_keys:
        if not enabled:
            settings.pop("enabledPlugins", None)
        write_json(SETTINGS, settings)
        actions.append(f"removed {removed_keys} from enabledPlugins")

    # Remove cache directories
    cache_base = CLAUDE_DIR / "plugins" / "cache" / "engram"
    if cache_base.exists():
        shutil.rmtree(cache_base)
        actions.append(f"removed cache directory {cache_base}")

    return actions


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_install(args: list[str]) -> None:
    print("Engram install")
    print("==============\n")

    # Validate scripts exist
    print("1. validating")
    for event, cfg in HOOK_EVENTS.items():
        script = SCRIPTS_DIR / cfg["script"]
        if not script.exists():
            fail(f"missing: {script}")
    ok("all hook scripts present")

    if not (SOURCE_DIR / "commands" / "engram.md").exists():
        fail("commands/engram.md not found")
    ok("command file present")

    # Check Python version
    if sys.version_info < (3, 9):
        fail(f"Python 3.9+ required, found {sys.version}")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    # Remove old plugin traces if present
    print("\n2. cleaning old plugin install (if any)")
    actions = remove_plugin_traces()
    if actions:
        for a in actions:
            ok(f"cleaned: {a}")
    else:
        ok("no old plugin traces found")

    # Install hooks
    print("\n3. registering hooks")
    settings = read_json(SETTINGS)
    settings = install_hooks(settings)
    # Set env var
    settings.setdefault("env", {})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    write_json(SETTINGS, settings)
    for event in HOOK_EVENTS:
        ok(f"{event}")
    ok("CLAUDE_CODE_DISABLE_AUTO_MEMORY=1")

    # Install command file
    print("\n4. installing /engram command")
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    cmd_src = SOURCE_DIR / "commands" / "engram.md"
    cmd_dst = COMMAND_DIR / "engram.md"
    shutil.copy2(str(cmd_src), str(cmd_dst))
    ok(f"{cmd_dst}")

    # Write breadcrumb so scripts can find home without env vars
    print("\n5. writing config")
    engram_conf = CLAUDE_DIR / "engram.json"
    write_json(engram_conf, {"home": str(SOURCE_DIR), "scripts": str(SCRIPTS_DIR)})
    ok(f"{engram_conf}")

    print(f"\nEngram installed. Scripts at: {SCRIPTS_DIR}")
    print("Hooks point directly at source — edits are live immediately.")
    print("\nRestart Claude Code (or /reload-plugins) to activate.")


def cmd_uninstall(args: list[str]) -> None:
    skip_confirm = "--yes" in args
    store_env = os.environ.get("CLAUDE_MEMORY_HOME")
    store = Path(store_env) if store_env else CLAUDE_DIR / "memory-store"

    print("Engram uninstall")
    print("================\n")

    # Show plan
    settings = read_json(SETTINGS)
    has_hooks = any(
        is_our_hook(e)
        for entries in settings.get("hooks", {}).values()
        for e in entries
    )
    has_command = (COMMAND_DIR / "engram.md").exists()
    has_env = "CLAUDE_CODE_DISABLE_AUTO_MEMORY" in settings.get("env", {})
    has_store = store.exists()
    has_conf = (CLAUDE_DIR / "engram.json").exists()

    if has_hooks:
        print("  [x] Remove Engram hooks from settings.json")
    if has_command:
        print("  [x] Remove /engram command")
    if has_env:
        print("  [x] Remove CLAUDE_CODE_DISABLE_AUTO_MEMORY")
    if has_store:
        print(f"  [x] Delete data store ({store})")
    if has_conf:
        print("  [x] Remove engram.json config")

    # Also check for plugin traces
    plugin_traces = []
    installed = CLAUDE_DIR / "plugins" / "installed_plugins.json"
    if installed.exists():
        d = read_json(installed)
        if any("engram" in k for k in d.get("plugins", {})):
            plugin_traces.append("installed_plugins.json entry")
    cache_base = CLAUDE_DIR / "plugins" / "cache" / "engram"
    if cache_base.exists():
        plugin_traces.append(f"cache directory ({cache_base})")
    if plugin_traces:
        for t in plugin_traces:
            print(f"  [x] Remove old plugin trace: {t}")

    if not any([has_hooks, has_command, has_env, has_store, has_conf, plugin_traces]):
        print("Nothing to clean up — Engram is not installed.")
        return

    print()
    if not skip_confirm:
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
        if reply not in ("y", "yes"):
            print("Cancelled.")
            return

    # Execute
    if has_hooks:
        settings = remove_hooks(settings)
        # Also remove env var
        env = settings.get("env", {})
        env.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)
        if not env:
            settings.pop("env", None)
        else:
            settings["env"] = env
        write_json(SETTINGS, settings)
        print("  Removed hooks + env var.")

    if has_command:
        (COMMAND_DIR / "engram.md").unlink(missing_ok=True)
        print("  Removed /engram command.")

    if has_store:
        shutil.rmtree(store)
        print("  Deleted data store.")

    if has_conf:
        (CLAUDE_DIR / "engram.json").unlink(missing_ok=True)
        print("  Removed engram.json.")

    if plugin_traces:
        remove_plugin_traces()
        print("  Removed old plugin traces.")

    print("\nDone. Engram is fully uninstalled.")
    print("Restart Claude Code to pick up the change.")


def cmd_repair(args: list[str]) -> None:
    """Fix a broken install without losing data."""
    print("Engram repair")
    print("=============\n")
    fixed = 0

    # Check scripts exist
    for event, cfg in HOOK_EVENTS.items():
        script = SCRIPTS_DIR / cfg["script"]
        if not script.exists():
            fail(f"missing script: {script} — is the repo intact?")

    # Check/fix hooks in settings.json
    settings = read_json(SETTINGS)
    hooks = settings.get("hooks", {})
    missing_hooks = []
    stale_hooks = []
    for event, cfg in HOOK_EVENTS.items():
        expected_cmd = hook_command(cfg["script"])
        entries = hooks.get(event, [])
        ours = [e for e in entries if is_our_hook(e)]
        if not ours:
            missing_hooks.append(event)
        else:
            for e in ours:
                for h in e.get("hooks", []):
                    if HOOK_MARKER in h.get("command", "") and h["command"] != expected_cmd:
                        stale_hooks.append(event)

    if missing_hooks or stale_hooks:
        settings = install_hooks(settings)
        write_json(SETTINGS, settings)
        for h in missing_hooks:
            ok(f"added missing hook: {h}")
            fixed += 1
        for h in stale_hooks:
            ok(f"updated stale hook path: {h}")
            fixed += 1
    else:
        ok("all hooks correct")

    # Check/fix env var
    settings = read_json(SETTINGS)
    if settings.get("env", {}).get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") != "1":
        settings.setdefault("env", {})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        write_json(SETTINGS, settings)
        ok("set CLAUDE_CODE_DISABLE_AUTO_MEMORY=1")
        fixed += 1
    else:
        ok("CLAUDE_CODE_DISABLE_AUTO_MEMORY set")

    # Check/fix command file
    cmd_src = SOURCE_DIR / "commands" / "engram.md"
    cmd_dst = COMMAND_DIR / "engram.md"
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    if not cmd_dst.exists():
        shutil.copy2(str(cmd_src), str(cmd_dst))
        ok("restored /engram command")
        fixed += 1
    else:
        import filecmp
        if not filecmp.cmp(str(cmd_src), str(cmd_dst), shallow=False):
            shutil.copy2(str(cmd_src), str(cmd_dst))
            ok("updated /engram command")
            fixed += 1
        else:
            ok("/engram command correct")

    # Check/fix engram.json
    engram_conf = CLAUDE_DIR / "engram.json"
    expected_conf = {"home": str(SOURCE_DIR), "scripts": str(SCRIPTS_DIR)}
    current_conf = read_json(engram_conf)
    if current_conf != expected_conf:
        write_json(engram_conf, expected_conf)
        ok("updated engram.json")
        fixed += 1
    else:
        ok("engram.json correct")

    # Clean old plugin traces
    actions = remove_plugin_traces()
    if actions:
        for a in actions:
            ok(f"cleaned: {a}")
            fixed += 1

    noun = "issue" if fixed == 1 else "issues"
    print(f"\n{'Repaired ' + str(fixed) + ' ' + noun if fixed else 'No issues found — installation is healthy'}.")


def cmd_verify(args: list[str]) -> None:
    """Quick health check — exit 0 if healthy, 1 if not."""
    issues = []

    # Check hooks present in settings.json
    settings = read_json(SETTINGS)
    hooks = settings.get("hooks", {})
    for event, cfg in HOOK_EVENTS.items():
        entries = hooks.get(event, [])
        ours = [e for e in entries if is_our_hook(e)]
        if not ours:
            issues.append(f"hook missing: {event}")
        else:
            # Verify the script path is correct
            expected = hook_command(cfg["script"])
            for e in ours:
                for h in e.get("hooks", []):
                    cmd = h.get("command", "")
                    if HOOK_MARKER in cmd and cmd != expected:
                        issues.append(f"hook stale: {event} (path changed)")

    # Check scripts exist
    for event, cfg in HOOK_EVENTS.items():
        script = SCRIPTS_DIR / cfg["script"]
        if not script.exists():
            issues.append(f"script missing: {script}")

    # Check command file
    if not (COMMAND_DIR / "engram.md").exists():
        issues.append("/engram command file missing")

    # Check env var
    if settings.get("env", {}).get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") != "1":
        issues.append("CLAUDE_CODE_DISABLE_AUTO_MEMORY not set")

    # Check Python can import common
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util
        spec = importlib.util.find_spec("common")
        if spec is None:
            issues.append("common.py not importable")
    except Exception as e:
        issues.append(f"import check failed: {e}")

    if issues:
        print("unhealthy:")
        for i in issues:
            print(f"  - {i}")
        print(f"\nRun: python3 {Path(__file__).resolve()} repair")
        sys.exit(1)
    else:
        print(f"healthy — hooks point at {SCRIPTS_DIR}")
        sys.exit(0)


def cmd_migrate(args: list[str]) -> None:
    """Migrate from old plugin-based install to direct hooks."""
    print("Engram: migrate from plugin system to direct hooks")
    print("=" * 52 + "\n")

    # Check if there's an old plugin install
    installed = CLAUDE_DIR / "plugins" / "installed_plugins.json"
    has_old = False
    if installed.exists():
        d = read_json(installed)
        if any("engram" in k for k in d.get("plugins", {})):
            has_old = True

    settings = read_json(SETTINGS)
    has_new = any(
        is_our_hook(e)
        for entries in settings.get("hooks", {}).values()
        for e in entries
    )

    if has_new and not has_old:
        print("Already on direct hooks — nothing to migrate.")
        return

    if not has_old and not has_new:
        print("No Engram install found. Run: python3 scripts/manage.py install")
        return

    print("Found old plugin-based install. Migrating to direct hooks...\n")

    # Remove old plugin machinery
    actions = remove_plugin_traces()
    for a in actions:
        ok(f"cleaned: {a}")

    # Install new direct hooks
    settings = read_json(SETTINGS)
    settings = install_hooks(settings)
    settings.setdefault("env", {})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    write_json(SETTINGS, settings)
    for event in HOOK_EVENTS:
        ok(f"hook: {event}")

    # Refresh command file
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    cmd_src = SOURCE_DIR / "commands" / "engram.md"
    cmd_dst = COMMAND_DIR / "engram.md"
    shutil.copy2(str(cmd_src), str(cmd_dst))
    ok("/engram command refreshed")

    # Write config breadcrumb
    engram_conf = CLAUDE_DIR / "engram.json"
    write_json(engram_conf, {"home": str(SOURCE_DIR), "scripts": str(SCRIPTS_DIR)})
    ok("engram.json written")

    print("\nMigration complete. Data store is unchanged.")
    print("Restart Claude Code to activate the new hooks.")


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #
COMMANDS = {
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "repair": cmd_repair,
    "verify": cmd_verify,
    "migrate": cmd_migrate,
}


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "install"
    handler = COMMANDS.get(cmd)
    if not handler:
        print(f"usage: manage.py [{' | '.join(COMMANDS)}]")
        sys.exit(2)
    handler(argv[1:])


if __name__ == "__main__":
    main()

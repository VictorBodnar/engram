---
description: Inspect and manage Engram memories (status | search <terms> | forget <slug> | clear [--all] | clear-logs | prune | reindex | doctor)
argument-hint: "[status | search <terms> | forget <slug> | clear [--all] | clear-logs | prune | reindex | doctor]"
allowed-tools: Bash(python3:*)
---

Run the Engram control CLI with the user's arguments and report the result
verbatim. Default to `status` when no argument is given.

!`python3 "$(python3 -c "import json,os;p=os.path.join(os.path.expanduser('~'),'.claude','engram.json');print(json.load(open(p))['scripts'] if os.path.exists(p) else os.environ.get('CLAUDE_PLUGIN_ROOT','.'))")/memctl.py" $ARGUMENTS`

If the command above printed an error about the store path, the memory store may
not exist yet — that is normal before the first memory is captured.

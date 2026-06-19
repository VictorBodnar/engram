---
description: Inspect and manage Engram memories (status | search <terms> | forget <slug> | clear | clear-logs | prune | reindex | doctor)
argument-hint: "[status | search <terms> | forget <slug> | clear [--all] | clear-logs | prune | reindex | doctor]"
allowed-tools: Bash(python3:*)
---

Run the Engram control CLI with the user's arguments and report the result
verbatim. Default to `status` when no argument is given.

!`python3 -c "import os,sys;r=os.environ.get('CLAUDE_PLUGIN_ROOT','.');sys.path.insert(0,os.path.join(r,'scripts'));import memctl;memctl.main()" $ARGUMENTS`

If the command above printed an error about the store path, the memory store may
not exist yet — that is normal before the first memory is captured.

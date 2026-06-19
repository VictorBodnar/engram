# Engram demo

A narrated, **fully offline** walkthrough of the capture → recall → housekeeping
loop. No network and no live LLM — the distiller reads a canned JSON fixture
(`tests/fixtures/distilled.json`) via `CLAUDE_MEMORY_FAKE_LLM`, the same mechanism the
smoke test uses. It runs against a throwaway store and cleans up after itself.

## Run it

```bash
bash demo/demo.sh          # pauses between steps — press Enter to advance
bash demo/demo.sh --fast   # no pauses (for recording)
```

## What you'll see

### 1. Capture — a Stop hook spawns a detached distiller

```
 1) CAPTURE — a Stop hook spawns a DETACHED distiller and returns instantly
 The transcript fixture is a chat where the user insists on splitting shell commands.
 $ echo '{"session_id":"demo", ...}' | stop_distill.py
 {}
 ↑ The hook returned {} in milliseconds. The distiller is still running in the background…
 …and here is the memory it wrote, as plain markdown:
 $ cat memories/split-shell-commands.md
 ---
 slug: split-shell-commands
 type: correction
 title: Split shell commands instead of chaining with &&
 project: global
 keywords: shell, bash, commands, chaining, split
 created: 2026-06-19
 updated: 2026-06-19
 ---
 Prefer single atomic shell commands over compound commands chained
 with && or ;. This lets permission allowlists match individual
 commands reliably.
```

### 2. Status — what's in the store

```
 2) STATUS — what's in the store?
 $ engram status
 store:    /tmp/engram-demo.xxxxx
 memories: 1
 by type:  correction=1
 by project: global=1
 state:    2 session file(s) (GC'd after 7d)

 recent distiller activity:
   2026-06-19T10:00:01Z CREATE session=demo slug=split-shell-commands type=correction project=global
```

### 3. Search — the same scorer recall uses

```
 3) SEARCH — the SAME integer scorer recall uses, exposed for debugging
 $ engram search shell commands
 query tokens: shell, commands
 →  11  split-shell-commands (correction·global) — Split shell commands instead of chaining with &&
```

### 4. Recall — deterministic injection

```
 4) RECALL — a fresh prompt; UserPromptSubmit scores memories and injects the winner
 Prompt: 'how should I run shell commands in bash'
 $ echo '{…"prompt":"how should I run shell commands in bash"}' | user_prompt.py
 {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"<recalled-memories>\n<memory slug=\"split-shell-commands\" type=\"correction\" project=\"global\">\nSplit shell commands instead of chaining with &&\nkeywords: shell, bash, commands, chaining, split\n\nPrefer single atomic shell commands over compound commands chained\nwith && or ;. This lets permission allowlists match individual\ncommands reliably.\n</memory>\n</recalled-memories>"}}
 ↑ that <recalled-memories> block is injected into the model's context — deterministically.
```

### 5. The log — every decision is greppable

```
 5) THE LOG — every decision is ONE greppable line; scores are hand-recomputable
 $ grep -E 'CREATE|RECALL' logs/memory.log
 2026-06-19T10:00:01Z CREATE session=demo slug=split-shell-commands type=correction project=global
 2026-06-19T10:00:02Z RECALL session=demo2 project=proj prompt_tokens=7 injected=1 slugs=[split-shell-commands:14] skipped=[]
```

### 6. Housekeeping

```
 6) HOUSEKEEPING — clear-logs wipes only the log; memories are untouched
 $ engram clear-logs
 cleared 8 log lines → /tmp/engram-demo.xxxxx/logs/memory.log
 Memory still there; the 'state:' line shows session files (GC'd after 7d):
 store:    /tmp/engram-demo.xxxxx
 memories: 1
 ...
```

## Record a shareable cast (optional)

The script is asciinema-friendly. To capture a terminal recording:

```bash
# 1. record (uses --fast so there are no manual pauses)
asciinema rec engram-demo.cast --command "bash demo/demo.sh --fast"

# 2. play it back locally
asciinema play engram-demo.cast

# 3. (optional) convert to GIF
agg engram-demo.cast engram-demo.gif
```

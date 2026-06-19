# Engram demo

A narrated, **fully offline** walkthrough of the capture → recall → housekeeping loop. No
network and no live LLM — the distiller reads a canned JSON fixture
(`tests/fixtures/distilled.json`) via `CLAUDE_MEMORY_FAKE_LLM`, the same mechanism the smoke
test uses. It runs against a throwaway store and cleans up after itself.

## Run it

```bash
bash demo/demo.sh          # pauses between steps — press Enter to advance
bash demo/demo.sh --fast   # no pauses (use this when recording)
```

What it shows, in order:

1. **Capture** — a `Stop` hook spawns a detached distiller that writes a markdown memory.
2. **Status** — `/engram status` counts what's stored.
3. **Search** — the same integer scorer recall uses, exposed for debugging.
4. **Recall** — a fresh prompt deterministically injects the matching memory.
5. **The log** — one greppable line per decision, with hand-recomputable scores.
6. **Housekeeping** — `clear-logs` and the `state:` GC line.

## Record a shareable cast (optional)

The script is asciinema-friendly. To capture a terminal recording:

```bash
# 1. record (uses --fast so there are no manual pauses)
asciinema rec engram-demo.cast --command "bash demo/demo.sh --fast"

# 2. play it back locally
asciinema play engram-demo.cast

# 3. (optional) upload / convert to GIF with agg
agg engram-demo.cast engram-demo.gif
```

> **Note:** the runnable script and these instructions ship with the package, but the actual
> `.cast` / `.gif` / screenshots are binary media a human needs to record. Drop them here:
>
> - `demo/engram-demo.cast` — _(record with the command above)_
> - `demo/engram-demo.gif` — _(optional, for embedding in the README)_

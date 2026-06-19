#!/usr/bin/env bash
# Build a self-contained, offline-installable Engram package.
#
# Uses `git archive`, so the zip contains EXACTLY the committed files — everything in
# .gitignore (the runtime store, dist/, __pycache__, …) is excluded automatically. No stray
# state can leak into a release.
#
#   bash scripts/package.sh        # → dist/engram-<version>.zip from HEAD
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VERSION="$(grep -o '"version"[^,]*' .claude-plugin/plugin.json | grep -o '[0-9][0-9.]*')"
OUT="dist/engram-${VERSION}.zip"

mkdir -p dist
git archive --format=zip --prefix="engram-${VERSION}/" -o "$OUT" HEAD

echo "built $OUT"
echo "contents:"
unzip -l "$OUT"

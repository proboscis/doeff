#!/bin/sh
# The body when PACKAGE_JOBS of make test-packages is 2 or more: run up to N per-package pytest sessions at the same time
# (shortening the daily processing stage packages).
# - Usage: run_package_sessions.sh <N> <name>=<path> ...   path is tests/ from the repo root or the root dir.
#   The pytest command is in the environment variable PACKAGE_SESSION_CMD (e.g. "uv run --no-sync pytest") and PACKAGE_SESSION_ARGS (e.g. "-m 'not e2e'").
# - Each session's output is collected in one file, and after all finish it is printed in argument order with `=== Testing <name> ===`
#   (so package boundaries do not get mixed in the daily log). Sessions stay independent per package (same-named test files do not collide).
# - The names of red sessions are printed at the end on one line `test-packages failed: <name> ...`, and it exits 1 if any is red (the same form as the serial loop).
set -u

if [ "${1:-}" = "--one" ]; then
  work=$2
  idx=$3
  spec=$(cat "$work/$idx.spec")
  path=${spec#*=}
  start=$(date +%s)
  eval "$PACKAGE_SESSION_CMD \"\$path\" $PACKAGE_SESSION_ARGS" > "$work/$idx.log" 2>&1
  rc=$?
  printf 'session took %s seconds\n' "$(($(date +%s) - start))" >> "$work/$idx.log"
  printf '%s\n' "$rc" > "$work/$idx.rc"
  exit 0
fi

jobs=$1
shift
work=$(mktemp -d "${TMPDIR:-/tmp}/pkg-sessions.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
total=0
for spec in "$@"; do
  total=$((total + 1))
  printf '%s\n' "$spec" > "$work/$total.spec"
done
self=$0
seq 1 "$total" | xargs -P "$jobs" -I{} sh "$self" --one "$work" {}

failed=""
i=0
for spec in "$@"; do
  i=$((i + 1))
  name=${spec%%=*}
  echo ""
  echo "=== Testing $name ==="
  cat "$work/$i.log"
  if [ "$(cat "$work/$i.rc" 2>/dev/null || echo 1)" != "0" ]; then failed="$failed $name"; fi
done
if [ -n "$failed" ]; then echo ""; echo "test-packages failed:$failed"; exit 1; fi

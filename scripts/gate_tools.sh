#!/bin/sh
# One place declaring the tool dependencies of where the daily full verification (gate.full in .agents/land-queue.toml) runs, and their preparation.
#
# Since 2026-10-10 the daily full verification runs directly where the daily task runs (the doeff-worker Pod on atlas) (ADR-DOE-ENFORCE-001 R10 --
# until then the tree was sent to zeus with remote_check and run there). The tools that tests and checks look for are declared and prepared on the side where they run (in the round of 2026-10-08 8:00
# packages/doeff-events/tests/test_notice_laws_redis.py was skipped in all 14 tests -- there was no redis-server on zeus's PATH).
#
#   sh scripts/gate_tools.sh path
#     Gather every declared tool and write to stdout, in the form "dir:", the dirs to put in front of PATH (empty if there is nothing to add). Order per tool:
#       1. If PATH already has the declared version, add nothing (the declared version the daily task places in HOME's .local/bin -- the head of PATH).
#       2. If the cache dir ($HOME/.cache/<name>-<version>) has the declared version, that dir.
#       3. If docker is available, extract one executable from the declared image (with digest), put it in the cache dir, and that dir.
#     If it cannot be gathered, write the tool's name, version, image, and the PATH searched to stderr and exit 1 (the processing stage goes red -- never silently skipped).
#
#   sh scripts/gate_tools.sh linter
#     Prepare the doeff-linter whose key the baseline file (scripts/hook_finding_baseline/doeff-linter.json) declares in the snapshot store that the baseline comparison
#     (scripts/hook_finding_baseline.py --strict check doeff-linter) searches. Write nothing to stdout.
#     If it cannot be prepared, write the reason to stderr and exit 1 (the processing stage goes red).
#
# Users = the processing stages packages (before make test-packages) and lint (before the baseline comparison) of .agents/land-queue.toml.
# Failure cases = tests/test_gate_tools.py.

# redis-server -- user packages/doeff-events/tests/test_notice_laws_redis.py (starts the PATH redis-server in a temporary dir and checks the delivery laws
# against a real Redis). The version and image are the same values as REDIS_VERSION and REDIS_IMAGE in agora's deploy/verify-worker-image/Dockerfile (the same version as production
# deploy/agora-events redis:7.4.11). When raising the production version, move all 3 places to the same version.
REDIS_VERSION=7.4.11
REDIS_IMAGE=redis:7.4.11-bookworm@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85
REDIS_IN_IMAGE=/usr/local/bin/redis-server
REDIS_CACHE="$HOME/.cache/redis-server-$REDIS_VERSION"

# Whether executable $1 is the declared version of redis-server (the answer of `--version` contains "v=<version> ").
is_declared_redis() {
  "$1" --version 2>/dev/null | grep -q "v=$REDIS_VERSION "
}

# Extract redis-server from the declared image and put it in the cache dir (do not put it there if what was extracted is not the declared version).
fetch_redis() {
  staging=$(mktemp -d "${TMPDIR:-/tmp}/gate-tools.XXXXXX") || return 1
  container=$(docker create "$REDIS_IMAGE") || { rmdir "$staging"; return 1; }
  docker cp "$container:$REDIS_IN_IMAGE" "$staging/redis-server"
  copied=$?
  docker rm "$container" > /dev/null
  if [ "$copied" -eq 0 ] && chmod 755 "$staging/redis-server" && is_declared_redis "$staging/redis-server"; then
    mkdir -p "$REDIS_CACHE" && mv "$staging/redis-server" "$REDIS_CACHE/redis-server" && rmdir "$staging"
    return
  fi
  rm -rf "$staging"
  return 1
}

# Write the dir of redis-server to put in front of PATH (write nothing if PATH has the declared version).
redis_path() {
  found=$(command -v redis-server)
  if [ -n "$found" ] && is_declared_redis "$found"; then
    return
  fi
  if [ -x "$REDIS_CACHE/redis-server" ] && is_declared_redis "$REDIS_CACHE/redis-server"; then
    printf '%s:' "$REDIS_CACHE"
    return
  fi
  if command -v docker > /dev/null && fetch_redis; then
    printf '%s:' "$REDIS_CACHE"
    return
  fi
  printf 'gate_tools: cannot prepare redis-server %s -- no declared version in the searched PATH (%s) or in %s, and it cannot be extracted from image %s either (no docker, or the extraction failed)\n' \
    "$REDIS_VERSION" "$PATH" "$REDIS_CACHE" "$REDIS_IMAGE" >&2
  return 1
}

# doeff-linter -- user = the baseline comparison of processing stage lint. The comparison searches for a binary with the same key as the build-input key the baseline file declares, in the order land-arm
# dev build ($HOME/.local/share/doeff-linter-dev) -> snapshot store ($HOME/.cache/doeff-linter-snapshots), and builds nothing
# (scripts/doeff_linter_locked.py -- the stores are at fixed places under HOME and are not replaced by environment variables). On zeus land-arm and the snapshot store
# existed, but the daily task's HOME is an empty dir for each run and has neither -- without preparing, the stage goes red with --strict's "unmeasurable" (rc 3).
# The baseline's key is not necessarily the same as the key of a commit after the linter changed (2026-10-10: baseline = key of 780ef711a, key at main's tip differs), so what is built is
# not HEAD but the commit that last wrote the baseline file. How to build is in one place, the snapshot tool packages/doeff-linter/scripts/linter_snapshot.py (if the sha
# dir exists, do not build; if a linter with the same key exists, copy it; otherwise build once with cargo -- a cold build takes 3-5 minutes).
# If the caller passes a store that persists across runs via DOEFF_LINTER_SNAPSHOT_DIR (the daily task uses the worker's work root
# daily-verify-cache/doeff-linter-snapshots), the snapshot store is made a symlink to it so the built linter is kept for the next run.
LINTER_BASELINE=scripts/hook_finding_baseline/doeff-linter.json
LINTER_STORE="$HOME/.cache/doeff-linter-snapshots"

# Prepare the doeff-linter of the baseline's key in the snapshot store.
linter_prepare() {
  top=$(cd "$(dirname "$0")/.." && pwd) || return 1
  kept="${DOEFF_LINTER_SNAPSHOT_DIR:-}"
  if [ -n "$kept" ] && [ "$kept" != "$LINTER_STORE" ] && [ ! -e "$LINTER_STORE" ] && [ ! -L "$LINTER_STORE" ]; then
    mkdir -p "$kept" "$(dirname "$LINTER_STORE")" && ln -s "$kept" "$LINTER_STORE" || return 1
  fi
  commit=$(git -C "$top" log -1 --format=%H -- "$LINTER_BASELINE")
  if [ -z "$commit" ]; then
    printf 'gate_tools: no commit that wrote the doeff-linter baseline %s is in the git of %s -- cannot build the linter of the baseline key\n' \
      "$LINTER_BASELINE" "$top" >&2
    return 1
  fi
  if ! DOEFF_LINTER_SNAPSHOT_DIR="$LINTER_STORE" uv run --script "$top/packages/doeff-linter/scripts/linter_snapshot.py" \
      "$top" "$commit" > /dev/null; then
    printf 'gate_tools: cannot prepare doeff-linter from baseline commit %s in store %s (the reason from linter_snapshot.py is above)\n' \
      "$commit" "$LINTER_STORE" >&2
    return 1
  fi
}

case "${1:-}" in
  path) redis_path || exit 1 ;;
  linter) linter_prepare || exit 1 ;;
  *)
    printf 'usage: sh scripts/gate_tools.sh path|linter\n' >&2
    exit 2
    ;;
esac

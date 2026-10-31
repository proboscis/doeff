#!/bin/sh
# make test-packages の PACKAGE_JOBS が 2 以上の時の本体: package ごとの pytest の session を最大 N 本同時に走らせる
# (日次の処理ステージ packages の短縮)。
# - 使い方: run_package_sessions.sh <N> <name>=<path> ...   path は repo の根からの tests/ か根の dir。
#   pytest の命令は環境変数 PACKAGE_SESSION_CMD(例 "uv run --no-sync pytest")と PACKAGE_SESSION_ARGS(例 "-m 'not e2e'")。
# - 各 session の出力は 1 本の file に溜め、全部終わってから引数の順に `=== Testing <name> ===` つきで出す
#   (日次の log で package の区切りが混ざらないように)。session は package ごとに独立のまま(同名の test file が衝突しない)。
# - 赤の session の名を最後に `test-packages failed: <name> ...` の 1 行で出し、1 つでも赤なら 1 で終わる(直列の loop と同じ形)。
set -u

if [ "${1:-}" = "--one" ]; then
  work=$2
  idx=$3
  spec=$(cat "$work/$idx.spec")
  path=${spec#*=}
  start=$(date +%s)
  eval "$PACKAGE_SESSION_CMD \"\$path\" $PACKAGE_SESSION_ARGS" > "$work/$idx.log" 2>&1
  rc=$?
  printf 'session 所要 %s 秒\n' "$(($(date +%s) - start))" >> "$work/$idx.log"
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

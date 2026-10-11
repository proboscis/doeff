#!/bin/bash
# 使い方: run_probe.sh <doeff の木> <probe の script> <出力の file> [probe の引数…] — 木の venv で probe を走らせ、頭に刻・木・版・命令・
# 終了の code を書く(probe の子は同じ python — sys.executable)。
tree=$1; probe=$2; out=$3; shift 3
{
  echo "刻: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "木: $tree"
  echo "版: $(git -C "$tree" rev-parse HEAD) $(git -C "$tree" status --short | wc -l) 個の未 commit の変更"
  echo "命令(cwd $tree): uv run --frozen python $probe $*"
  echo "---"
  (cd "$tree" && uv run --frozen python "$probe" "$@" 2>&1)
  echo "--- 終了の code: $?"
} > "$out"

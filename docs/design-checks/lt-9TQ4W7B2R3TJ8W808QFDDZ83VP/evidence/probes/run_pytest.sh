#!/bin/bash
# 使い方: run_pytest.sh <doeff の木> <出力の file> <pytest の引数…> — packages/doeff-hy で pytest を走らせ、頭に刻・木・版・命令・終了の code を書く。
tree=$1; out=$2; shift 2
{
  echo "刻: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "木: $tree"
  echo "版: $(git -C "$tree" rev-parse HEAD) $(git -C "$tree" status --short | wc -l) 個の未 commit の変更 $(git -C "$tree" status --short | tr '\n' ' ')"
  echo "命令(cwd $tree/packages/doeff-hy): uv run --frozen pytest -q -p no:cacheprovider $*"
  echo "---"
  (cd "$tree/packages/doeff-hy" && timeout 900 uv run --frozen pytest -q -p no:cacheprovider "$@" 2>&1)
  echo "--- 終了の code: $?"
} > "$out"

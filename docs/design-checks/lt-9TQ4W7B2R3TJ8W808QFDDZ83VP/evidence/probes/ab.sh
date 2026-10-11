#!/bin/bash
# 使い方: ab.sh <回数> <log> — 基点の木と直した木で、照らしの正味と読み込みの CPU(normal / always-current)を交互に測り、中央値を出す
n=$1; out=$2; : > "$out"
S=/work/agent-workspaces/a-VD2S8F3KBAB5PMHRCDEGH4PAY1/scripts/net.py
for i in $(seq 1 "$n"); do
  for t in base impl; do
    case $t in base) d=/home/ubuntu/.worktrees/be59-base/agora-controllers;; impl) d=/home/ubuntu/.worktrees/be59/agora-controllers;; esac
    for m in normal always-current; do
      (cd "$d" && echo "$t $(uv run --frozen python $S $m 2>&1 | tail -1)") | tee -a "$out"
    done
  done
done
for t in base impl; do
  for m in normal always-current; do
    for k in cpu check_net; do
      grep "^$t mode=$m " "$out" | sed "s/.* $k=\([0-9.]*\).*/\1/" | sort -n | awk -v t=$t -v m=$m -v k=$k '{a[NR]=$1} END {printf "%s %s %s median %.3f (n %d)\n", t, m, k, (NR%2 ? a[(NR+1)/2] : (a[NR/2]+a[NR/2+1])/2), NR}'
    done
  done
done

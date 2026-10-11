#!/bin/bash
# 使い方: sync_ab.sh <回数> <log> — 直した木で、盲検 A の直し(stat の覚えと歩みの確かめ)を効かせた時と外した時を交互に測る
n=$1; out=$2; : > "$out"
S=/work/agent-workspaces/a-VD2S8F3KBAB5PMHRCDEGH4PAY1/scripts/net.py
d=/home/ubuntu/.worktrees/be59/agora-controllers
for i in $(seq 1 "$n"); do
  (cd "$d" && echo "sync $(uv run --frozen python $S normal 2>&1 | tail -1)") | tee -a "$out"
  (cd "$d" && echo "nosync $(BE59_NOSYNC=1 uv run --frozen python $S normal 2>&1 | tail -1)") | tee -a "$out"
done
for t in sync nosync; do
  for k in cpu check_net; do
    grep "^$t mode=" "$out" | sed "s/.* $k=\([0-9.]*\).*/\1/" | sort -n | awk -v t=$t -v k=$k '{a[NR]=$1} END {printf "%s %s median %.3f (n %d)\n", t, k, (NR%2 ? a[(NR+1)/2] : (a[NR/2]+a[NR/2+1])/2), NR}'
  done
done

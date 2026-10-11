#!/bin/bash
# 使い方: accept.sh <回数> <木> <log> — 受入の形: 同じ木で normal と always-current を交互に撃ち、読み込みの CPU の中央値と差を出す
n=$1; tree=$2; out=$3; : > "$out"
S=/work/agent-workspaces/a-VD2S8F3KBAB5PMHRCDEGH4PAY1/scripts/net.py
cd "$tree" || exit 1
uv run --frozen python $S normal >/dev/null 2>&1; uv run --frozen python $S always-current >/dev/null 2>&1
for i in $(seq 1 "$n"); do
  for m in normal always-current; do
    uv run --frozen python $S $m 2>&1 | tail -1 | tee -a "$out"
  done
done
for m in normal always-current; do
  grep "mode=$m " "$out" | sed 's/.* cpu=\([0-9.]*\).*/\1/' | sort -n | awk -v m=$m '{a[NR]=$1} END {printf "%s median %.3f (n %d)\n", m, (NR%2 ? a[(NR+1)/2] : (a[NR/2]+a[NR/2+1])/2), NR}'
done
paste <(grep "mode=normal " "$out" | sed 's/.* cpu=\([0-9.]*\).*/\1/') <(grep "mode=always-current " "$out" | sed 's/.* cpu=\([0-9.]*\).*/\1/') | awk '{d[NR]=$1-$2} END {n=asort(d); printf "pairwise diff median %.3f (n %d)\n", (n%2 ? d[(n+1)/2] : (d[n/2]+d[n/2+1])/2), n}'

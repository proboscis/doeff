#!/bin/bash
# ab4_run.sh <出力の dir> <窓の秒> <模様(例 ABBAABBA)> <slice の μs> <on の形 on|on-all>
# A = off(既定の slice)・B = on の窓を模様の順に測る(時の流れの偏りを ABBA で打ち消す)。終わり・中断で off。
set -u
out=$1; win=$2; pattern=$3; slice=$4; mode=$5
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
revert() { uv run --script "$here/sched_ab.py" off > /dev/null 2>&1; }
trap revert EXIT
i=0
for c in $(echo "$pattern" | fold -w1); do
  i=$((i + 1))
  if [ "$c" = B ]; then uv run --script "$here/sched_ab.py" "$mode" "$slice" > /dev/null 2>&1; tag=on; else revert; tag=off; fi
  echo "w$i $tag $(date -u +%FT%TZ) load $(cut -d' ' -f1-3 /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$(printf %02d $i)-$tag.json" "$win" > /dev/null 2>&1
done

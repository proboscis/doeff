#!/bin/bash
# ab3_run.sh <出力の dir> <load の閾> <組の数> <窓の秒> <slice の μs>
# load1 が閾を越えたら、off → on(service だけ)→ on-all(Pod の nice 0 の全 thread)の窓を組の数だけ回す。終わり・中断で off。
set -u
out=$1; thresh=$2; rounds=$3; win=$4; slice=$5
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
revert() { uv run --script "$here/sched_ab.py" off 2>&1 | tee -a "$out/log.txt"; }
trap revert EXIT
for r in $(seq 1 "$rounds"); do
  until awk -v l="$(cut -d' ' -f1 /proc/loadavg)" -v t="$thresh" 'BEGIN{exit !(l >= t)}'; do sleep 2; done
  echo "round $r off $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$r-off.json" "$win" > /dev/null 2>&1
  uv run --script "$here/sched_ab.py" on "$slice" > /dev/null 2>&1
  echo "round $r on $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$r-on.json" "$win" > /dev/null 2>&1
  uv run --script "$here/sched_ab.py" on-all "$slice" 2>&1 | cut -c1-300 | tee -a "$out/log.txt"
  echo "round $r onall $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$r-onall.json" "$win" > /dev/null 2>&1
  revert > /dev/null
done

#!/bin/bash
# ab_run.sh <出力の dir> <load の閾> <組の数> <窓の秒> <slice の μs> <先に終わるのを待つ log> [待ちの上限秒 = 7200]
# 先の実験の log に end が出るのを待ち、load1 が閾を越えたら、off の窓 → on の窓(service の thread に custom slice)を組の数だけ交互に測る。
# 終わり・中断の時は必ず off(既定の slice へ戻す)を撃つ。
set -u
out=$1; thresh=$2; pairs=$3; win=$4; slice=$5; before=$6; maxwait=${7:-7200}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
revert() { uv run --script "$here/sched_ab.py" off 2>&1 | tee -a "$out/log.txt"; }
trap revert EXIT
echo "start $(date -u +%FT%TZ)" | tee -a "$out/log.txt"
t0=$(date +%s)
while ! grep -qE '^(end|no load)' "$before" 2>/dev/null; do
  [ $(( $(date +%s) - t0 )) -gt "$maxwait" ] && { echo "先の実験が終わらない" | tee -a "$out/log.txt"; exit 0; }
  sleep 5
done
for r in $(seq 1 "$pairs"); do
  while :; do
    l=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l" -v t="$thresh" 'BEGIN{exit !(l >= t)}' && break
    [ $(( $(date +%s) - t0 )) -gt "$maxwait" ] && { echo "no load >= $thresh within $maxwait s" | tee -a "$out/log.txt"; exit 0; }
    sleep 2
  done
  echo "pair $r off at $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$r-off.json" "$win" > "$out/p$r-off.txt" 2>&1
  uv run --script "$here/sched_ab.py" on "$slice" 2>&1 | tee -a "$out/log.txt"
  echo "pair $r on at $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/measure2.py" "$out/p$r-on.json" "$win" > "$out/p$r-on.txt" 2>&1
  revert
done
echo "end $(date -u +%FT%TZ)" | tee -a "$out/log.txt"

#!/bin/bash
# exp_run.sh <出力の dir> <load の閾> <回の数> <窓の秒> [待ちの上限秒 = 7200]
# load1 が閾を越えるまで待ち、越えたら同じ窓で次を同時に走らせる(読むだけ・自分の起こした probe の scheduling だけを変える):
#   A: SCHED_OTHER・nice 10・既定の slice(long-command の task のまま)
#   B: SCHED_OTHER・nice 10・custom slice 100 μs
#   C: SCHED_IDLE・nice 10(会話の Bash の子と同じ)
#   D: SCHED_OTHER・nice 19
#   M: measure2.py(Pod の中の群ごとの待ち)
set -u
out=$1; thresh=$2; rounds=$3; win=$4; maxwait=${5:-7200}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
echo "start $(date -u +%FT%TZ) $(chrt -p $$ | tr '\n' ' ') nice=$(nice)" | tee -a "$out/log.txt"
t0=$(date +%s)
for r in $(seq 1 "$rounds"); do
  while :; do
    l=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l" -v t="$thresh" 'BEGIN{exit !(l >= t)}' && break
    [ $(( $(date +%s) - t0 )) -gt "$maxwait" ] && { echo "no load >= $thresh within $maxwait s" | tee -a "$out/log.txt"; exit 0; }
    sleep 2
  done
  echo "round $r at $(date -u +%FT%TZ) load $(cat /proc/loadavg)" | tee -a "$out/log.txt"
  uv run --script "$here/slice_probe.py" 0 10 "$win" > "$out/r$r-A.txt" 2>&1 &
  uv run --script "$here/slice_probe.py" 100 10 "$win" > "$out/r$r-B.txt" 2>&1 &
  nice -n 9 uv run --script "$here/slice_probe.py" 0 19 "$win" > "$out/r$r-D.txt" 2>&1 &
  PROBE_POLICY=5 chrt --idle 0 uv run --script "$here/slice_probe.py" 0 10 "$win" > "$out/r$r-C.txt" 2>&1 &
  uv run --script "$here/measure2.py" "$out/r$r-M.json" "$win" > "$out/r$r-M.txt" 2>&1 &
  wait
  for x in A B C D; do echo "r$r-$x $(tail -1 "$out/r$r-$x.txt")" | tee -a "$out/log.txt"; done
  sleep 30
done
echo "end $(date -u +%FT%TZ)" | tee -a "$out/log.txt"

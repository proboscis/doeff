#!/bin/bash
# pin_test.sh <出力の dir> <CPU の番号> <窓の秒> <回の数>
# 1 つの CPU に、probe(SCHED_OTHER・nice 10・2 ms 眠り → 0.5 ms 計算)と、回り続ける相手(なし / SCHED_IDLE / SCHED_OTHER nice 19)を
# 並べ、相手の類で probe の待ちが変わるかを測る(相手の CPU は 1 本・窓の間だけ・card ki-757b489f8a14)。
set -u
out=$1; cpu=$2; win=$3; rounds=$4
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
spin() { timeout "$((win + 4))" taskset -c "$cpu" sh -c 'while :; do :; done'; }
for r in $(seq 1 "$rounds"); do
  for kind in none idle nice19; do
    case $kind in
      idle) chrt --idle 0 bash -c "$(declare -f spin); win=$win cpu=$cpu spin" & ;;
      nice19) nice -n 9 bash -c "$(declare -f spin); win=$win cpu=$cpu spin" & ;;
      none) : ;;
    esac
    sleep 1
    res=$(taskset -c "$cpu" uv run --script "$here/slice_probe.py" 0 10 "$win" 2>&1 | tail -1)
    echo "r$r $kind $(date -u +%T) $res" | tee -a "$out/log.txt"
    wait
  done
done

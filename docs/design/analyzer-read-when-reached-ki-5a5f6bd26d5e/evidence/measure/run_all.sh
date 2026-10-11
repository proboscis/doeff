#!/bin/bash
# 使い方: run_all.sh <analyzer の python dir> <log> [MEASURE_DEEP=1 などの env は外から]
# 検の file ごとに別の process で、測りの script を順に撃つ(答えの置き場は切る)。
analyzer="$1"; log="$2"
cd /home/ubuntu/.worktrees/agora-controllers-wt-ki-5a5f-measure || exit 1
: > "$log"
echo "# $(date -u +%FT%TZ) analyzer=$analyzer load=$(cut -d' ' -f1-3 /proc/loadavg)" >> "$log"
for f in screen unreadable delivery custody turns daily-verify keeper-sets intake toolset; do
  DOEFF_EFFECT_ANALYZER_RESULT_CACHE=off MEASURE_ANALYZER="$analyzer" uv run --no-sync python /work/agent-workspaces/a-QRFPES57Q2P7XNZQ3W8VHE8597/scripts/measure.py "file:$f" >> "$log" 2>&1
done
echo "# end $(date -u +%FT%TZ) load=$(cut -d' ' -f1-3 /proc/loadavg)" >> "$log"
grep -E "^[a-z-]+: import" "$log" | sed -E "s/.*analyze ([0-9.]+)s.*coverage ([0-9.]+)s.*/\1 \2/" | awk '{a+=$1; c+=$2} END {printf "analyze の和 %.1f 秒・coverage の和 %.1f 秒・合計 %.1f 秒(%d 組)\n", a, c, a+c, NR}' | tee -a "$log"

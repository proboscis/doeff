#!/usr/bin/env bash
# 証拠の記録: ev.sh <記録の名> <作業 dir> -- <命令…> — 命令・版・終了状態・出力を evidence/<名>.log.txt に残す(出力は切らない)。
# 設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW の再現と検証の実行記録を作る道具(結果の判定は呼び手が読む)。
set -u
name=$1; dir=$2; shift 3
out=/home/ubuntu/.worktrees/doeff-wt-dc-lt3tf/docs/design-checks/lt-3TF90EJ6BTXWY5VWVS7GRE1CGW/evidence/$name.log.txt
{
  echo "# 記録: $name"
  echo "# 時刻: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# 作業 dir: $dir"
  echo "# 対象の版: $(git -C "$dir" rev-parse HEAD 2>/dev/null || echo 'git の木ではない(一時の写し)')"
  echo "# 命令: $*"
  echo "# ---- 出力 ----"
} > "$out"
( cd "$dir" && "$@" ) >> "$out" 2>&1
status=$?
echo "# ---- 終了状態: $status ----" >> "$out"
echo "$name: 終了状態 $status"
exit $status

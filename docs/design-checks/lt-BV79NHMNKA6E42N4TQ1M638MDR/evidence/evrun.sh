#!/bin/sh
# 設計検証の実行記録を 1 本の log に残すため: 命令・作業木・版・時刻・出力・終了の状態を同じ file に書く。
# 使い方: sh evrun.sh <log> <作業木> <命令…>(命令は作業木で走らせる)
log=$1
dir=$2
shift 2
{
  echo "# 作業木: $dir(HEAD $(git -C "$dir" rev-parse HEAD))"
  echo "# 作業木の未 commit の変更: $(git -C "$dir" status --short | tr '\n' ' ')"
  echo "# 命令: $*"
  echo "# 時刻: $(date -u +%FT%TZ)"
  echo
} > "$log"
(cd "$dir" && "$@") >> "$log" 2>&1
rc=$?
echo "# 終了の状態: $rc" >> "$log"
exit 0

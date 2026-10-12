#!/bin/sh
# macro の値を替え、更新時刻を 2 秒進める: $1 = 根の dir・$2 = 新しい値
set -e
f="$1/pkg/macros.hy"
before=$(stat -c %Y "$f")
printf '(defmacro answer [] %s)\n' "$2" > "$f"
touch -d "@$((before + 2))" "$f"

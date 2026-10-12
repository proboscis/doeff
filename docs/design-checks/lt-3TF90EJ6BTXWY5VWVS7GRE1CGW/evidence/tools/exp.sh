#!/usr/bin/env bash
# 使い方: exp.sh <linter の binary> — 一時の置き場で、事実の上限の片づけと一時 file の片づけが「本物の事実の置き場所」で効くかを見る。
set -u
BIN=$1
W=$(mktemp -d /tmp/dc-lt3tf-run-XXXX)
C=$W/cache
mkdir -p $C
make_repo() { # make_repo <dir> <名の印>
  local dir=$1 tag=$2
  mkdir -p $dir/app/core
  cat > $dir/pyproject.toml <<'TOML'
[tool.doeff-linter]
enable = ["DOEFF106"]

[tool.doeff-linter.layers]
order = ["core", "foundation"]
paths = { core = "app/core", foundation = "app/foundation" }

[tool.doeff-linter.layers.allow_imports]
core = ["core"]
foundation = ["foundation"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
TOML
  for i in $(seq 0 29); do
    printf '(import time)\n(defn now-%s-%s [] (time.time))\n' "$tag" "$i" > $dir/app/core/m$i.hy
  done
}
lint() { # lint <root> [cap]
  local root=$1 cap=${2:-}
  ( cd $root && env -u DOEFF_LINTER_NO_CACHE DOEFF_LINTER_CHECK_SLOT=1 DOEFF_LINTER_CACHE_DIR=$C ${cap:+DOEFF_LINTER_CACHE_MAX_BYTES=$cap} \
      $BIN --output-format editor-json --no-log > /dev/null 2>$W/stderr ; echo "  exit=$?" )
}
facts() { find $C/objects -type f ! -name used ! -name '*.tmp' "$@" | wc -l; }
make_repo $W/a a; make_repo $W/b b
echo "[1] 根 a を既定の上限で走らせる"; lint $W/a
echo "  事実の file の数=$(facts)  objects の byte=$(du -sb $C/objects | cut -f1)"
echo "  事実の file の例: $(find $C/objects -type f ! -name used | head -1 | sed "s|$C/||")"
P=$(find $C/objects -type f ! -name used ! -name '*.tmp' | head -1)
touch -d '2 hours ago' "$(dirname $P)/.$(basename $P).99999.tmp"   # 打ち切られた実行の残り(本物の事実と同じ dir)
find $C/objects -type f ! -name used -exec touch -d '30 days ago' {} +   # 事実と一時 file を 30 日前に使った事にする
touch -d '2 hours ago' "$(dirname $P)/.$(basename $P).99999.tmp"
rm -f $C/.swept-cap   # 上限の片づけの番が来ている
echo "[2] 根 b を上限 1000 byte で走らせる(片づけの番・a の事実は 30 日前に使った物)"; lint $W/b 1000
echo "  30 日前の事実の残り=$(facts -mtime +1)  一時 file の残り=$(find $C/objects -name '*.tmp' | wc -l)  objects の byte=$(du -sb $C/objects | cut -f1)"
echo "  置き場の全体の byte=$(du -sb $C | cut -f1)(上限 1000)"
rm -rf $W

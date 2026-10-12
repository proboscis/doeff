#!/usr/bin/env bash
# 使い方: exp_touch.sh <binary> — 2 時間前に置いた事実を今読んだ直後に、別の実行の上限の片づけがそれを消すか。
set -u
BIN=$1
W=$(mktemp -d /tmp/dc-lt3tf-run-XXXX); C=$W/cache; mkdir -p $C
make_repo() { local dir=$1 tag=$2; mkdir -p $dir/app/core
  printf '[tool.doeff-linter]\nenable = ["DOEFF106"]\n\n[tool.doeff-linter.layers]\norder = ["core", "foundation"]\npaths = { core = "app/core", foundation = "app/foundation" }\n\n[tool.doeff-linter.layers.allow_imports]\ncore = ["core"]\nfoundation = ["foundation"]\n\n[tool.doeff-linter.raw_side_effects]\nallowed_layers = ["foundation"]\n' > $dir/pyproject.toml
  for i in $(seq 0 29); do printf '(import time)\n(defn now-%s-%s [] (time.time))\n' "$tag" "$i" > $dir/app/core/m$i.hy; done; }
lint() { ( cd $1 && env -u DOEFF_LINTER_NO_CACHE DOEFF_LINTER_CHECK_SLOT=1 DOEFF_LINTER_CACHE_DIR=$C ${2:+DOEFF_LINTER_CACHE_MAX_BYTES=$2} $BIN --output-format editor-json --no-log >/dev/null 2>&1 ); }
old() { find $C/objects -type f ! -name used ! -name '*.tmp' -mmin +60 | wc -l; }
make_repo $W/a a; make_repo $W/b b
lint $W/a
find $C/objects -type f ! -name used -exec touch -d '2 hours ago' {} +
echo "[1] a の事実 30 を 2 時間前に置いた事にした: 1 時間より古い事実=$(old)"
lint $W/a
echo "[2] a を走らせ直した(事実を全部読んだ・作り直していない): 1 時間より古い事実=$(old)(読んでも更新時刻は動かない)"
rm -f $C/.swept-cap
lint $W/b 1000
echo "[3] 数秒後、別の根 b の実行が上限 1000 で片づけた: 1 時間より古い事実=$(old)(a が今読んだ事実が消えたか)"
rm -rf $W

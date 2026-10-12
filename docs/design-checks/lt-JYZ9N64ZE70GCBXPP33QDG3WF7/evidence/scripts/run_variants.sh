#!/bin/sh
# 包みの検(packages/doeff-hy/tests/test_bytecode_guard.py)を、Hy 1.3.1 の 1 か所だけを替えた失敗ケースの wheel(build_hy_variants.py が
# 作る)で走らせ、変種ごとの log を書く。uv の find-links だけから入れる(UV_NO_INDEX)ので、検の `--with hy==<uv.lock の版>` は替えた
# wheel を入れる。uv の cache は使わない(同じ名の wheel の中身が変種ごとに違う)。
# 使い方: sh run_variants.sh <作業木> <変種の wheel の親 dir> <log の dir> [変種 …](既定 = base get table reader gensym anon)
# log の名は <LOG_PREFIX>-<変種>.log(環境変数 LOG_PREFIX・既定 after)。
set -u
prefix=${LOG_PREFIX:-after}
tree=$1
wheels=$2
out=$3
shift 3
[ "$#" -gt 0 ] || set -- base get table reader gensym anon
cd "$tree" || exit 2
for variant in "$@"; do
  log="${out}/${prefix}-${variant}.log"
  {
    echo "== 版 $(git rev-parse HEAD)・Hy の変種 ${variant}(UV_FIND_LINKS=${wheels}/${variant}・UV_NO_INDEX=1・UV_NO_CACHE=1)"
    sha256sum "${wheels}/${variant}"/*.whl
    env UV_NO_INDEX=1 UV_FIND_LINKS="${wheels}/${variant}" UV_NO_CACHE=1 \
      uv run --no-sync pytest packages/doeff-hy/tests/test_bytecode_guard.py -q -m 'not e2e' -rf
    echo "rc=$?"
  } > "$log" 2>&1
  tail -1 "$log"
done

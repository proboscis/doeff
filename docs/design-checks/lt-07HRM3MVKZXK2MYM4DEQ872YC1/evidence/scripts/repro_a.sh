#!/bin/sh
# 盲検 A の失敗例の再現: 直す前の包みの代役(probe.py prefix — 局所の状態を開く口を包まない)が作った .pyc と置き場の entry を、
# 今の作業木の包み(fixed)が、macro を変えた後に信じるか。使い方: sh repro_a.sh <作業木> <盲検 A の scripts の dir> <一時の dir>
set -u
tree=$1; s=$2; tmp=$3
run() { env PYTHONDONTWRITEBYTECODE=1 DOEFF_HY_CODE_STORE="$1" uv run --no-sync --project "$tree" python "$s/probe.py" "$2" "$3"; }
echo "== 版 $(git -C "$tree" rev-parse HEAD)・作業木の差分 $(git -C "$tree" diff --stat -- packages | tail -1)"
echo "-- A(.pyc): 代役で 11 → macro を 12 へ → 今の包みで 2 回(要件 12)"
sh "$s/make_pkg.sh" "$tmp/A" 11; run off "$tmp/A" prefix; sh "$s/edit_macro.sh" "$tmp/A" 12; run off "$tmp/A" fixed; run off "$tmp/A" fixed
echo "-- B(対照): 今の包みで 11 → 12 へ → 今の包み(要件 12)"
sh "$s/make_pkg.sh" "$tmp/B" 11; run off "$tmp/B" fixed; sh "$s/edit_macro.sh" "$tmp/B" 12; run off "$tmp/B" fixed
echo "-- C(置き場): 代役が置き場へ entry(11)→ 同じ user.hy の新しい木(macro 12・.pyc 無し)を今の包みで(要件 12)"
sh "$s/make_pkg.sh" "$tmp/C1" 11; run "$tmp/store" "$tmp/C1" prefix; sh "$s/make_pkg.sh" "$tmp/C2" 12; run "$tmp/store" "$tmp/C2" fixed

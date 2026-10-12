#!/bin/sh
# 盲検 A の失敗例を、直す前の実物の包み(old の作業木 — 5454330f5・記録の印 6・局所の表を覚えない)と、測る木(new)で再現する。
# old が .pyc と置き場の entry(macro 11)を作り、macro を 12 へ変えた後に new の包みが信じるか(要件 = 12)。
# 使い方: sh repro_a_real.sh <old の作業木> <new の作業木> <盲検 A の scripts の dir> <この dir の probe_tree.py> <一時の dir>
set -u
old=$1; new=$2; s=$3; probe=$4; tmp=$5
run() { env PYTHONDONTWRITEBYTECODE=1 DOEFF_HY_CODE_STORE="$1" uv run --no-sync --project "$2" python "$probe" "$3" plain; }
for t in "$old" "$new"; do echo "== $t: 版 $(git -C "$t" rev-parse HEAD)・差分 $(git -C "$t" diff --stat -- packages | tail -1)・$(grep '^RECORD_TAG' "$t/packages/doeff-hy/src/doeff_hy_bytecode_guard/records.py")"; done
echo "-- A(.pyc): old で 11 → macro を 12 へ → new で 2 回(要件 12)"
sh "$s/make_pkg.sh" "$tmp/A" 11; run off "$old" "$tmp/A"; sh "$s/edit_macro.sh" "$tmp/A" 12; run off "$new" "$tmp/A"; run off "$new" "$tmp/A"
echo "-- C(置き場): old が置き場へ entry(11)→ 同じ user.hy の新しい木(macro 12・.pyc 無し)を new で(要件 12)"
sh "$s/make_pkg.sh" "$tmp/C1" 11; run "$tmp/store" "$old" "$tmp/C1"; sh "$s/make_pkg.sh" "$tmp/C2" 12; run "$tmp/store" "$new" "$tmp/C2"

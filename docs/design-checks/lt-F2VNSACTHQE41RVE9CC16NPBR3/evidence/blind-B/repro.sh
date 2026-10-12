#!/bin/sh
# 盲検 B の手順 1〜4 を作業木で走らせ、Makefile と検の file を写しから戻す。使い方: repro.sh <作業木>
set -u
wt=$1; here=$(cd "$(dirname "$0")" && pwd -P); tf=tests/test_daily_test_population.py
cp "$wt/Makefile" "$wt/.Makefile.b-backup"; cp "$wt/$tf" "$wt/.test.b-backup"
restore() { cp "$wt/.Makefile.b-backup" "$wt/Makefile"; cp "$wt/.test.b-backup" "$wt/$tf"; }
run() { (cd "$wt" && uv run --no-sync pytest -q -p no:randomly "$tf" -k "$1" -rfE 2>&1 | grep -v '^check-slot'); }
echo "== base $(git -C "$wt" rev-parse HEAD)"
echo "== B step 1: B の差分を当てる"; uv run --no-project python "$here/apply.py" "$wt"
(cd "$wt" && uv run --no-sync ruff check "$tf" 2>&1 | tail -3)
run make_test_packages; restore
echo "== B step 2: B の差分 + 変異(最後の行を [ -z \"\$\$empty\" ] — 赤だけの日に rc 0)"; uv run --no-project python "$here/apply.py" "$wt" --empty-only-exit
git -C "$wt" diff --no-color -- Makefile
run make_test_packages; restore
echo "== B step 3: B の差分 + 変異 + 使い捨ての検 test_probe_red_alone"; uv run --no-project python "$here/apply.py" "$wt" --empty-only-exit --probe
run probe_red_alone; restore
echo "== B step 4(対照): 差分なしで 227 行の exit 1 だけを消す"
sed -i 's|^\tif \[ -n "\$\$failed" \]; then echo ""; echo "test-packages failed:\$\$failed"; exit 1; fi|\tif [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; fi|' "$wt/Makefile"
git -C "$wt" diff --no-color -- Makefile
run make_test_packages; restore
rm -f "$wt/.Makefile.b-backup" "$wt/.test.b-backup"
git -C "$wt" diff --quiet -- Makefile "$tf" && echo "== restored"

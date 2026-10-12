#!/bin/sh
# 変異を 1 つ Makefile に当て、検の file の make の検を走らせ、Makefile を写しから戻す。
# 使い方: mutate.sh <作業木> <検の file(作業木からの相対)> <変異の名 | none>
# 変異の名: skip-one-package / stop-at-first-red / swallow-red / skip-extra-roots / drop-durations-min / and-summary
set -u
wt=$1; file=$2; name=$3
cp "$wt/Makefile" "$wt/.Makefile.mutate-backup"
case "$name" in
  none) ;;
  skip-one-package) sed -i 's|for dir in packages/\*/; do \\|for dir in packages/*/; do case "$$dir" in packages/doeff-hy/) continue;; esac; \\|' "$wt/Makefile" ;;
  stop-at-first-red) sed -i 's/|| failed="\$\$failed \$\$(basename \$\$dir)"/|| exit 1/' "$wt/Makefile" ;;
  swallow-red) sed -i 's/|| failed="\$\$failed \$\$(basename \$\$dir)"/|| true/' "$wt/Makefile" ;;
  skip-extra-roots) sed -i 's|^\tfor root in \$(PACKAGE_EXTRA_TEST_ROOTS); do \\|\tfor root in ; do \\|' "$wt/Makefile" ;;
  drop-durations-min) sed -i 's|^PACKAGE_PYTEST_DURATIONS ?= --durations=0 --durations-min=10|PACKAGE_PYTEST_DURATIONS ?= --durations=0|' "$wt/Makefile" ;;
  and-summary) sed -i 's|^\tif \[ -n "\$\$failed" \]; then echo ""; echo "test-packages failed:\$\$failed"; exit 1; fi|\t[ -n "$$failed" ] \&\& { echo ""; echo "test-packages failed:$$failed"; exit 1; }|' "$wt/Makefile" ;;
  *) echo "unknown mutation $name"; exit 2 ;;
esac
echo "== mutation $name on $(git -C "$wt" rev-parse HEAD) file $file"
git -C "$wt" diff --no-color -- Makefile
(cd "$wt" && uv run --no-sync pytest -q -p no:randomly "$file" -k "make_test_packages" -rfE 2>&1 | grep -v '^check-slot')
echo "== pytest rc (pipe) done"
cp "$wt/.Makefile.mutate-backup" "$wt/Makefile"; rm -f "$wt/.Makefile.mutate-backup"
git -C "$wt" diff --quiet -- Makefile && echo "== Makefile restored"

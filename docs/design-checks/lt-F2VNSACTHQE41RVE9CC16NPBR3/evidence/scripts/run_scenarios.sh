#!/bin/sh
# 変種ごとに scenarios.py を当て、make の検を走らせ、Makefile と検の file を写しから戻す。使い方: run_scenarios.sh <作業木> <変種>…
set -u
wt=$1; shift; here=$(cd "$(dirname "$0")" && pwd -P); tf=tests/test_daily_test_population.py
for v in "$@"; do
  cp "$wt/Makefile" "$wt/.Makefile.s-backup"; cp "$wt/$tf" "$wt/.test.s-backup"
  echo "== variant $v on $(git -C "$wt" rev-parse HEAD) $(date -u +%FT%TZ)"
  uv run --no-project python "$here/scenarios.py" "$wt" "$v"
  git -C "$wt" diff --no-color --stat -- Makefile "$tf"
  (cd "$wt" && uv run --no-sync pytest -q -p no:randomly "$tf" -k make_test_packages -rfE 2>&1 | grep -v '^check-slot')
  cp "$wt/.Makefile.s-backup" "$wt/Makefile"; cp "$wt/.test.s-backup" "$wt/$tf"; rm -f "$wt/.Makefile.s-backup" "$wt/.test.s-backup"
  git -C "$wt" diff --quiet -- Makefile "$tf" && echo "== restored"
done

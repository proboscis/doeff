#!/bin/sh
# 受入条件 1〜8 の手元の走行をまとめて撃つ(全体の走行はしない — 触った file に絞る)。
# 使い方: verify.sh(worktree = ~/.worktrees/doeff-wt-ki-b02c57db9de8・記録 = /tmp/ki-b02c/logs)
wt=$HOME/.worktrees/doeff-wt-ki-b02c57db9de8
ev=$wt/docs/design/effectful-cache-tests-bytecode-ki-b02c57db9de8/evidence
run=/tmp/ki-b02c/run.sh
files="tests/test_effectful.py tests/test_bytecode_settings_pinned.py packages/doeff-hy/tests/test_source_positions.py packages/doeff-hy/tests/test_none_type_contract.py"
matrix() {  # matrix <label> — 4 通りの env(DWB あり / なし × prefix あり / なし)
  for d in 1 unset; do for p in set unset; do
    $run "$1-dwb$d-prefix$p" $d $p $files -q
    grep -E "^(FAILED|ERROR) " "/tmp/ki-b02c/logs/$1-dwb$d-prefix$p.log"
  done; done
}
doeff_clean() {
  test -z "$(git -C "$wt" diff --stat -- doeff/)" && echo "doeff/ clean" || { echo "doeff/ NOT clean"; exit 3; }
}

echo "## 1 (after)"; $run A1-after-env1 1 unset tests/test_effectful.py -q -k "cache or rebuild"
$run A1-after-envunset unset unset tests/test_effectful.py -q -k "cache or rebuild"
echo "## 2"; matrix A2
echo "## 3 (M1)"; git -C "$wt" apply "$ev/M1-write-cache-ignores-flag.patch" && matrix A3-M1
git -C "$wt" apply -R "$ev/M1-write-cache-ignores-flag.patch"; doeff_clean
echo "## 4 (M3)"; git -C "$wt" apply "$ev/M3-cache-follows-pycache-prefix.patch" && matrix A4-M3
git -C "$wt" apply -R "$ev/M3-cache-follows-pycache-prefix.patch"; doeff_clean
echo "## 5 (D1 の行を消す)"
t=$wt/tests/test_effectful.py; cp "$t" /tmp/ki-b02c/test_effectful.py.keep
for pair in "named:test_cache_is_named_by_the_rewrite_version_and_reused" "rebuild:test_a_new_rewrite_version_rebuilds_the_cache"; do
  label=${pair%%:*}; name=${pair#*:}
  # その検査の定義から最初の D1 の行だけを消す
  awk -v name="$name" '
    $0 ~ "^def " name "\\(" { inside = 1 }
    inside && !done && $0 ~ /monkeypatch\.setattr\(sys, "dont_write_bytecode", False\)/ { done = 1; next }
    { print }' /tmp/ki-b02c/test_effectful.py.keep > "$t"
  $run "A5-minusD1-$label-envunset" unset unset tests/test_effectful.py -q -k "cache or rebuild"
  grep -E "^FAILED " "/tmp/ki-b02c/logs/A5-minusD1-$label-envunset.log"
  cp /tmp/ki-b02c/test_effectful.py.keep "$t"
done
test -z "$(git -C "$wt" diff --stat HEAD -- tests/test_effectful.py)" && echo "tests/test_effectful.py restored"
echo "## 6 (D4 を戻す)"
git -C "$wt" apply -R --include=packages/doeff-hy/tests/test_source_positions.py "$ev/REV-tracked.patch"
for d in unset 1; do
  $run "A6-minusD4-dwb$d" $d unset packages/doeff-hy/tests/test_source_positions.py tests/test_bytecode_settings_pinned.py -q
  grep -E "^(FAILED|ERROR) |AssertionError: a test or fixture" "/tmp/ki-b02c/logs/A6-minusD4-dwb$d.log"
done
git -C "$wt" apply --include=packages/doeff-hy/tests/test_source_positions.py "$ev/REV-tracked.patch"
echo "## 7 (lint)"
lint=/tmp/ki-b02c/logs/A7-lint.log
lfiles="conftest.py $files"
(
  cd "$wt" || exit 2
  echo "# HEAD: $(git rev-parse HEAD)  worktree diff: $(git diff --stat HEAD | tail -1)"
  echo "## ruff check"; uv run --no-sync ruff check $lfiles; echo "exit=$?"
  echo "## ruff format --check"; uv run --no-sync ruff format --check $lfiles; echo "exit=$?"
  echo "## semgrep --config .semgrep.yaml --error"
  uv run --no-sync semgrep --config .semgrep.yaml --error --metrics=off --quiet $lfiles; echo "exit=$?"
  echo "## pyright"; timeout 600 uv run --no-sync pyright $lfiles; echo "exit=$?"
) > "$lint" 2>&1
grep -E "^## |^exit=|error:|Would reformat|passed" "$lint"
echo "## 8"; doeff_clean
echo "## final worktree diff"; git -C "$wt" diff --stat HEAD

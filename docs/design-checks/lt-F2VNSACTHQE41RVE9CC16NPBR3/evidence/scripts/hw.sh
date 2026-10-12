#!/bin/sh
# S-hw: 偽の runner を dash と bash の POSIX mode で、symlink の下の cwd から走らせる(Mac の /bin/sh は bash の POSIX mode・
# tmp は /var → /private/var の symlink の下)。比べるのは物理の path なので、symlink 越しの根も赤の dir と同じと数えるか。
# 失敗例: 引数の解きを `pwd -P` から `pwd`(論理)に替えた runner は、symlink 越しの cwd で赤の dir を見落とす。
# 使い方: hw.sh <作業木>
set -u
wt=$1; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/real/packages/a/tests" "$tmp/real/packages/b/tests"; ln -s real "$tmp/link"
uv run --no-project python -c "import ast, sys; tree = ast.parse(open(sys.argv[1], encoding='utf-8').read()); sys.stdout.write(next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == '_FAKE_RUNNER'))" "$wt/tests/test_daily_test_population.py" > "$tmp/runner.sh"
sed 's/&& pwd -P)" = "\$fail"/\&\& pwd)" = "$fail"/' "$tmp/runner.sh" > "$tmp/runner-logical.sh"
echo "== runner の差(失敗例の変種)"; diff "$tmp/runner.sh" "$tmp/runner-logical.sh"
probe() {  # <shell…> <runner> <引数の根> — symlink の下の cwd から呼び、rc と記録の cwd を出す
  runner=$2; root=$3
  rm -f "$tmp/rec"
  (cd "$tmp/link" && DOEFF_FAKE_RUNNER_RECORD="$tmp/rec" DOEFF_FAKE_RUNNER_FAIL="$tmp/link/packages/a/tests" \
     $1 "$runner" pytest "$root" -m "not e2e"; echo "rc=$?")
  echo "record_cwd=$(cut -f2 "$tmp/rec" | sed "s|$tmp|<tmp>|")"
}
for sh in dash "bash --posix"; do
  echo "== $sh: runner(pwd -P)"
  for root in packages/a/tests packages/a/tests/ "$tmp/link/packages/a/tests" packages/b/tests; do
    printf '%s  ' "$(echo "$root" | sed "s|$tmp|<tmp>|")"; probe "$sh" "$tmp/runner.sh" "$root" | tr '\n' ' '; echo
  done
  echo "== $sh: 失敗例 runner(pwd — 論理)"
  for root in packages/a/tests packages/b/tests; do
    printf '%s  ' "$root"; probe "$sh" "$tmp/runner-logical.sh" "$root" | tr '\n' ' '; echo
  done
done

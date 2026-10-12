#!/usr/bin/env bash
# 設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW — 名の走査の検(each_file_uses_only_the_names_of_the_knowledge_it_owns)が、持ち主でない file の越権を拒否するか。
# 使い方: run_owner_scan_mutants.sh <packages を持つ写しの dir> — 変種を 1 つずつ当てて、その検だけを走らせ、元に戻す。コンパイルが通った上で検が赤になる事を「拒否」と数える。
set -u
root=$1
project=$root/packages/doeff-linter/src/project
cd "$root/packages/doeff-linter" || exit 2
for f in facts_cache.rs facts_cache/store.rs facts_cache/sweep.rs; do cp "$project/$f" "$project/$f.orig"; done
run() { # run <名> <file> <追記する文>
  name=$1; file=$2; text=$3
  for f in facts_cache.rs facts_cache/store.rs facts_cache/sweep.rs; do cp "$project/$f.orig" "$project/$f"; done
  # 変種は file の本体(#[cfg(test)] の前)に足す。tests の後ろへ足すと、本体の外で走査の対象にならない。
  uv run --no-project python - "$project/$file" "$text" <<'PY'
import sys
path, text = sys.argv[1], sys.argv[2]
s = open(path).read()
marker = "#[cfg(test)]"
at = s.find(marker)
head, tail = (s[:at], s[at:]) if at >= 0 else (s, "")
open(path, "w").write(head + text + "\n\n" + tail)
PY
  out=$(cargo test --offline --lib each_file_uses_only_the_names_of_the_knowledge_it_owns 2>&1)
  compiled=$(printf '%s\n' "$out" | grep -c "could not compile")
  result=$(printf '%s\n' "$out" | grep -E "^test result" | head -1)
  msg=$(printf '%s\n' "$out" | grep -E "を使っている|を持っている" | head -1)
  echo "[$name] コンパイルの失敗(could not compile)の行=$compiled / $result"
  echo "    検の言い分: $msg"
}
run "M0 変種なし(元の木)" facts_cache.rs ""
run "M1 引く手順が古い事実を並べて消す(store::list と store::remove)" facts_cache.rs '#[allow(dead_code)]
fn reap_old_facts(place: &Place) {
    for object in store::list(&place.objects, 0) {
        if object.used == 0 {
            store::remove(object);
        }
    }
}'
run "M2 片づけが事実を読む(store::read_object)" facts_cache/sweep.rs '#[allow(dead_code)]
fn peek_at_a_fact() {
    let _ = super::store::read_object::<u8>;
}'
run "M3 store が寿命の決まりを持つ(IN_USE_SECONDS を使う)" facts_cache/store.rs '#[allow(dead_code)]
const IN_USE_SECONDS: u64 = 600;'
run "M4 説明の文に名が出るだけ(コメント。拒否されない側の確かめ)" facts_cache.rs '// 片づけは store::remove で消す(説明だけ)。IN_USE_SECONDS はここでは使わない。'
for f in facts_cache.rs facts_cache/store.rs facts_cache/sweep.rs; do mv "$project/$f.orig" "$project/$f"; done
echo "[後始末] 元の file に戻した: $(diff -rq "$project" /home/ubuntu/.worktrees/doeff-wt-facts-store/packages/doeff-linter/src/project > /dev/null && echo 同一 || echo 違う)"

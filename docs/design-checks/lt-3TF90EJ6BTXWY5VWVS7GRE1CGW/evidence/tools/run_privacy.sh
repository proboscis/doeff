#!/usr/bin/env bash
# 設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW — 事実の置き場の「中身の知識」(配置・符号化・一時名)を、store の外(sweep と親)から使えるか。
# 使い方: run_privacy.sh <着地した版の写し(packages/doeff-linter を持つ dir)> — store.rs の非公開の名を 1 つずつ、sweep.rs と facts_cache.rs から参照して cargo check する。
# 期待: どの名も E0603(非公開)で拒否される。名が無い・綴りの誤りなど別の理由の失敗は拒否と数えない(E0603 の行を数える)。
set -u
root=$1
project=$root/packages/doeff-linter/src/project
cd "$root/packages/doeff-linter" || exit 2
for target in facts_cache/sweep.rs facts_cache.rs; do
  cp "$project/$target" "$project/$target.orig"
  for name in encode_value decode_value object_path temporary_path is_temporary; do
    cp "$project/$target.orig" "$project/$target"
    if [ "$target" = facts_cache/sweep.rs ]; then path="super::store::$name"; else path="store::$name"; fi
    printf '\n#[allow(dead_code)]\nfn privacy_probe() { let _ = %s; }\n' "$path" >> "$project/$target"
    out=$(cargo check --offline --lib 2>&1)
    status=$?
    n=$(printf '%s\n' "$out" | grep -c "error\[E0603\]: .*\`$name\` is private")
    echo "[$target から store::$name] cargo check 終了状態=$status E0603(\`$name\` は非公開)の行=$n"
  done
  mv "$project/$target.orig" "$project/$target"
done
echo "[後始末] 参照を足す前の file に戻した: $(diff -q "$project/facts_cache.rs" "/home/ubuntu/.worktrees/doeff-wt-facts-store/packages/doeff-linter/src/project/facts_cache.rs" > /dev/null && echo 親は同一 || echo 親が違う) / $(diff -q "$project/facts_cache/sweep.rs" "/home/ubuntu/.worktrees/doeff-wt-facts-store/packages/doeff-linter/src/project/facts_cache/sweep.rs" > /dev/null && echo sweep は同一 || echo sweep が違う)"

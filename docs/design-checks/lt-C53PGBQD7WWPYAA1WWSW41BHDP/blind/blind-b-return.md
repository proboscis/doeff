## 結論

検査を通るのに責務を侵す変更を 1 つ作り、`/tmp/ki3497-blind-B/` の下で実際に確かめました。中身は、ex04_traverse_macro の終わりに traverse の展開を印字する節を足す変更です。この変更があると、答えを変えない macros だけの変更でも例の検が赤になります。設計者の S1 の主張(macros を変えても examples・期待の出力・例の検は 1 行も変えずに緑のまま。展開を印字する file を戻せば E1・E3 が断る)は、この変更で成り立たなくなります。

## 1. 機能要求と差分

**要求(想定)**: R2 で ex04_debug を外したが、traverse が何に変わるかを読み手に見せる価値はあった。そこで、主例の ex04_traverse_macro に、展開を比べられる形で印字する節を足したい。ex04 は README で「traverse macro: full pipeline with CPS Iterate + fold」と紹介され、頭の注釈にも「CPS-converts Iterate into a Traverse effect」と書かれている例です。

差分は `/tmp/ki3497-blind-B/proposed-ex04.patch` に置きました。

```diff
--- a/packages/doeff-traverse/examples/ex04_traverse_macro.hy
+++ b/packages/doeff-traverse/examples/ex04_traverse_macro.hy
@@ -194,3 +194,19 @@
+
+;; ===========================================================================
+;; What traverse becomes — the CPS conversion described at the top
+;; (stage 3: per-item body becomes the thunk; Iterate becomes a Traverse effect)
+;; ===========================================================================
+
+(print)
+(print "=== what traverse expands to (stage 3) ===")
+(print (hy.repr (hy.macroexpand
+  '(traverse
+     (<- v (Iterate features :label "normalize"))
+     (normalize-value v mean)))))
--- a/packages/doeff-traverse/examples/expected/ex04_traverse_macro.txt
+++ b/packages/doeff-traverse/examples/expected/ex04_traverse_macro.txt
@@ -22,3 +22,6 @@
+
+=== what traverse expands to (stage 3) ===
+'(_doeff_traverse_Traverse (_doeff_do (fn [v] (do (normalize-value v mean)))) features :label "normalize")
```

(patch の注釈は実物の 8 行を短く書き換えています。正確な差分は patch の file の方です。)

## 2. 本来の持ち主と、侵される契約

- **展開の形は macros の持ち物です。** 責務の表で、macros の「隠す物」に入っています。この変更で、展開の形の 2 つ目の定義が `examples/expected/ex04_traverse_macro.txt` に字面どおり写されます。
- **判断が移ります。** traverse の展開をどう組むかは、これまで macros だけで決められました。この変更の後は、答えを変えない展開の変更でも examples の期待の出力を書き直す必要があり、examples の同意が要るようになります。
- **merge の前に止まります。** `macros.hy` を変えた差分は、表の行 `packages/doeff-hy/src/doeff_hy/` から例の検を選びます(実測)。そのため、macros の担当者の merge の前の検査が赤で止まります。examples が macros の内部に事実上の拒否権を持つ形です。
- **崩れる主張**:
  - S1 の「examples・期待の出力・example-check は 1 行も変えず緑」。
  - S1 の「展開の中身を印字する file を examples/ に戻すと E1・E3 が断る」。これは Program も走らせる file には当てはまりません。
- **R3 との関係**: R3 の文面が禁じるのは「中身だけを印字する debug 用の file」です。この file は Program を走らせて比べられるので、文面には触れません。違反は R3 の字面ではなく、責務の表と S1 の主張に対してです。R3 が混ざった file まで禁じる意図かどうかは解釈で、推測です。

## 3. 提示された各検査が拒否しない理由

| 検査 | 拒否しない理由 |
|---|---|
| `test_example_runs_a_program[ex04…]`(E1) | 口の呼び出しが 1 つ以上在るかだけを見ます。ex04 は元々 `(run …)` を 3 回呼んでいます。足した `'(traverse …)` は quote の中なので降りません。印字の中身は見ません。 |
| `test_example_runs[ex04…]`(E2) | 展開は決まった形なので、期待の出力を同時に更新すれば一致します。 |
| `test_tables_name_examples_as_they_are`(E3) | 期待の出力に「…」が無いので、変異の行は要りません。checkout の path もありません。正規表現 `_hy_(?:anon\|gensym)_` は Hy が付ける名だけを見ます。stage 3 の展開には該当する名が無く、`_doeff_traverse_Traverse`・`_doeff_do` は doeff-hy の名です(実測)。 |
| `test_case_failure_shows_in_compared_output` | ex04 は CASE_FAILURE_MUTANTS の表に無いので無関係です。 |
| `test_example_uses_only_doeff_hy_macros` | defmacro も require も足していません。quote の中は traverse・`<-`・Iterate だけです。 |
| `test_broken_example_is_still_broken` | BROKEN の表が空なので skip されます。 |
| inline の 3 つの検 | 例の file を読まないので影響しません。 |
| pyproject の表 | ex04 の差分から `test_examples_run.py` を選びます(実測)。正しく選んだうえで、その検が通ります。 |

E3 が捕まえられるのは gensym を含む展開だけです。stage 1(`<-` を含む形)の展開には `_hy_gensym_bound_2`・`_hy_gensym_outcomes_1` が出て、E3 が断ります(実測)。

## 4. 確かめる手順と結果(すべて実測)

**準備**: `git -C ~/repos/doeff archive cf27cee98 | tar -x -C /tmp/ki3497-blind-B/doeff` で写しを作り、`uv sync --frozen --group dev`(29 秒)で環境を揃えました。写しの `packages/doeff-traverse` と `pyproject.toml` が渡された写しと同じことは diff で確かめました。

検は毎回 `uv run --frozen pytest packages/doeff-traverse/tests/test_examples_run.py` で走らせました。

| 段 | 状態 | 結果 |
|---|---|---|
| 0 | 変更なし | 36 passed, 1 skipped |
| 1 | `proposed-ex04.patch` を当てる | 36 passed, 1 skipped。`tests/test_contract_table_follows_packages.py` も 14 passed |
| 2 | さらに `s1-macro-change.patch` を当てる(`_traverse-form` の実行時の枝で、余分な `(do ~inner-body)` を `~inner-body` にする) | 1 failed(`test_example_runs[ex04_traverse_macro.hy]`)。差は展開の 1 行だけで、Mean・Valid・item history の行は同じ |
| 3 | 対照: ex04 を元に戻し、macro の変更は残す | 36 passed, 1 skipped |

段 3 から、macro の変更は 10 本の例の答えを変えないこと、段 2 の赤は例が展開の形に依存したことだけが原因であることが分かります。表の選び(`select_targets` を直に呼んで実測)は、`macros.hy` の変更で例の検を選び、`uv.lock` の変更では選びません。

## 5. 確かめていない前提・足りない情報

- **macro の変更の影響範囲**: 答えを変えないと確かめたのは例 10 本だけです。doeff-hy 自身の検、型検査用の展開の枝、他の package への影響は確かめていません。
- **走らせていない検**: ex04 の差分に対して表は `test_traverse_answer_types.py` と `tests/test_daily_test_population.py` も選びます。この 2 つは走らせていないので、通るとは言いません。linter の逆依存の選びと日次の段も確かめていません。
- **Hy の版の上げ(推測)**: 期待の出力は `hy.repr` の書式(Hy の中身、版 1.3.1)にも依存します。Hy の版を上げて書式が変わると ex04 が赤になるはずですが、`uv.lock` の変更は例の検を選びません(選ばないことは実測)。この場合は日次で初めて赤になる、と推測します。書式が実際に変わるかは確かめていません。
- **別の穴(関数単位で実測)**: E1 の `_program_calls` は、`(subprocess.run …)`・`(asyncio.run …)`・自分で定義した `(defn run [] …) (run)` も口として数えます。このため、展開を印字するだけの debug 用の file でも E1 を通り得ます。これは S2 の「期待の出力に依らず E1 が赤にする」に反します。ただし、そうした file を丸ごと例の検にかけたわけではなく、E3 が断るかどうかは中身次第です。

**置いた file**:
- 提案の差分: `/tmp/ki3497-blind-B/proposed-ex04.patch`
- macros 側の変更: `/tmp/ki3497-blind-B/s1-macro-change.patch`
- 確かめに使った道具: `/tmp/ki3497-blind-B/scratch/`(`select_probe.py`・`runner_probe.py`・`expand_probe.hy`)
- 写し: `/tmp/ki3497-blind-B/doeff` は今、commit cf27cee98 に提案の差分を当てた状態です(macros は元に戻してあります)。

共有の source、写し、repo には書き込んでいません。commit・push・他の会話への連絡もしていません。

WAIT: work — 利用者の次の入力

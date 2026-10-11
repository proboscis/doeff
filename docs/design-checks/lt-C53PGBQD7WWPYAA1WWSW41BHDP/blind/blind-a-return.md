## 結論

S1 の主張(「展開の中身を印字する file を examples/ に戻すと、E1・E3 が断る」「変えるのは macros だけ」)を崩す例を見つけ、実際に走らせて確かめました。

R3 が認める形の例が 1 本あるだけで、答えを変えない macros の変更が起きると、examples(期待の出力)と example-check(表 CASE_FAILURE_MUTANTS)の書き換えが要ります。その例とは、Program を走らせつつ for/do の展開も印字する読み物です。

## 1. 変更要求と、それが要る理由

- **前の状態(S2 の正当な形):** 読み物の例 `ex10_for_do_desugar.hy` を足してあります。From 1 つの for/do が何に展開されるかを `hy.repr (hy.macroexpand …)` で見せ、同じ for/do を走らせて答え `[2, 4, 6]` を印字します。README の ex04 の説明「CPS Iterate」を読める物にしたい、という要求は自然に出ます。この例は Program を走らせて結果を印字し、出力も比べられるので、R3 の文面(「中身**だけ**を印字する debug 用の file は置けない」)には反しません。
- **変更要求(S1):** traverse・for/do の実行時の展開の形を、答えを変えずに整えます。同じ種類の変更は実際に起きています。`_traverse-form` の docstring に、性能のために展開の形を `(fn [p] ((_doeff_do (fn [] …))))` から `(_doeff_do (fn [p] (do …)))` へ変えた記録があります(agora-redesign #2871)。今回は次の 2 通りで試しました。
  - (a) 余分な `(do …)` を外す(1 行)
  - (b) 型検査用の展開にそろえ、items を gensym の名へ 1 度だけ置く

## 2. 関係する主張・前提と、予想に反して変わる所

- **前提は守っています。** 件の結果は変わりません。既存の 10 本はどちらの変更でも `test_example_runs` が緑で、ex10 の答え `Valid: [2, 4, 6]` も同じです。macros の口(require)も変えていません。
- **変わる所:**
  - (a) の変更: examples の `expected/ex10_for_do_desugar.txt` を書き直す必要があります。
  - (b) の変更: さらに次の 2 段で example-check まで変わります。
    1. 出力に `_hy_gensym_items_7` が出ます。期待の出力にそのまま書くと E3 が断るので、`…` にします。
    2. すると `test_tables_name_examples_as_they_are` が「CASE_FAILURE_MUTANTS に行を足す」と断ります。`tests/test_examples_run.py` に行を足すまで緑になりません。
  - 結果として、macros だけの変更が examples と example-check の 2 つの module に及びます。

## 3. どの知識がどこへ漏れるか(区別)

- **漏れる知識:** macros が隠すはずの「展開の形」が、ex10 の期待の出力にそのまま写ります。(b) では「展開に gensym が在るか」という macros の内部の事実が、example-check の表に行が要るかどうかを決めます。
- **漏れを許す所:** 強制の手段が、主張より狭い代わりの物で確かめているからです。
  - E1 は「件を走らせる口を 1 回以上呼ぶか」という有無の検査で、中身だけを印字していないかは見ません。
  - E3 は `_hy_anon_`・`_hy_gensym_` の 2 つの形しか見ません。From 1 つの展開 `'(_doeff_traverse_Traverse (_doeff_do (fn [x] (do (* x 2)))) items :label "double")` には、どちらも出ません。
- **ほかの分類には当たりません。**
  - 公開契約の拡張ではありません(使い手の code は 1 行も変わりません)。
  - 配線の変更でもありません。
  - 「… を置いたら変異の行を足す」は examples と example-check の意図した共同の不変条件で、S2 の中では正当です。ただ S1 では、その引き金を macros の隠す事実が引くので、漏れと判断しました。

## 4. 最小の再現(実測)

- **場所:** 作業 dir `/tmp/ki3497-blind-A/` に、cf27cee98 の部分の写しを `git archive` で `tree/` に取り出しました。共有の木は変えていません。
- **走らせ方:** `~/repos/doeff/.venv` の Python 3.14.3 と Hy 1.3.1 に、写しの doeff-hy/src と doeff-traverse を `PYTHONPATH` で先に読ませ、この 1 file の pytest だけを走らせました。
- **手順と結果:**
  1. `case-a/…/examples/ex10_for_do_desugar.hy` と期待の出力を置きました。
     - 結果: `test_example_runs_a_program[ex10]`・`test_example_runs[ex10]`・`test_example_uses_only_doeff_hy_macros[ex10]`・`test_tables_name_examples_as_they_are` がすべて PASSED でした。E1・E3 は断りません。
  2. macros の写しに (a) を当てました(`/tmp/ki3497-blind-A/s1-do-cleanup.diff`、1 行)。
     - 結果: 38 passed、1 skipped(BROKEN が空)、1 failed で、失敗は `test_example_runs[ex10]` だけでした。差は `(fn [x] (do (* x 2)))` が `(fn [x] (* x 2))` になった所だけです。
     - 記録: `case-a-s1.log`
  3. 別の写し `case-b` に (b) を当てました(`s1-items-once.diff`)。
     - 期待の出力をそのまま書き直すと、E3 が「Hy の付ける名が在る」で赤になりました。
     - `…` にすると、「CASE_FAILURE_MUTANTS に行を足す」で赤になりました(38 passed / 1 failed)。
     - example-check に行を足すと(`example-check-row.diff`)、対象の 5 本が緑に戻りました。

## 5. 未確認の事、足りない情報

- **実行環境は推測で補っています。**
  - doeff core と doeff_core_effects は venv の版 9d71df154(cf27 の親)で走らせました。cf27 との差はこの 2 つの外(conftest・docs・tests)だけだと読みましたが、同じ版では走らせていません。
  - `uv run` も、マージの前の選び(`run_changed_tests.py`)も走らせていません。
  - 接頭辞 `packages/doeff-hy/src/doeff_hy/` の行で、S1 の差分に例の検が選ばれ、マージの前に赤になる、と表から推測しています。つまり見逃しではなく、責務が波及するという失敗です。
- **R3 の解釈が分かりません。** 設計者が「だけ」を、中身も印字する file まで禁じる意味で書いたのかは不明です。どちらに読んでも「E1・E3 が断る」は実測で成り立ちません。
- **未実行の推測:** 展開を手で写した file(ex04_debug2 の形)に run の呼び出しを足した場合、E1・E3 を通り、S1 の後も赤にならずに、写しが古くなったまま残るはずです。
- **(a)(b) は私が置いた例です。** 実際にこの変更が求められる頻度は確かめていません。

## 付記: 別に見つけた候補(契約の書き漏れ)

- **実態:** traverse・for/do の展開は、`_doeff_traverse_Traverse`・`_doeff_traverse_Skip`・`_doeff_do` を使う側の module の名から引きます。
  - 例 7 本が `Traverse :as _doeff_traverse_Traverse` を、ex09 が `Skip :as _doeff_traverse_Skip` を import しています。
  - ex04_traverse_macro の註には「needed by the traverse macro's expansion」とあります。
- **実測:** ex09 の写しから Skip の import を消すと、rc=0 のまま 2 件が `name '_doeff_traverse_Skip' is not defined` / `failed` と印字されました(`ex09-noskip.out`)。
- **何が食い違うか:** 責務の表では macros の口は require だけですが、macro の docstring は「Requires: (import …)」を求めています。
- **推測(macros 側で名を変える変更は走らせていません):** 展開が引く名を変えたり足したりする S1 の変更では、該当する例すべての import を直す必要があります。これが契約の変更か、隠す物の漏れかは、設計者がこの別名を口に含めるかどうかで決まります。今の S1 の主張は、この場合を除いていません。

WAIT: work — 呼び手(盲検の依頼元)の次の依頼

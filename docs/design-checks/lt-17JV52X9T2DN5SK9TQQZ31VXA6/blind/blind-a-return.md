## 結論

失敗例は見つかりました。doeff-hy が `traverse` と `fnk` の中身を内部の macro へ移し、公開の macro はそれを `hy.R` で呼ぶだけにする作り替えをすると、例の答えは変わりません(最後まで展開した形も同一)。それでも例の検が赤になり、直すには例の検の表と自己検を書き換えるか、macro の展開に印を残すよう持ち主を縛るしかありません。R2 と S1 の予測(変わるのは macros だけ)に反します。

## 1. 現実的な変更要求と、必要になる理由

- **変更:** doeff-hy の作者が `traverse` と `fnk` を「薄い公開 macro + 中身を持つ内部 macro」に分けます。公開 macro の展開は `(hy.R.doeff_hy/macros.traverse-body …)` の 1 行になります。`hy.R` は Hy 1.x の記法で、呼び手に require させずに別 module の macro を呼べます。
- **理由:**
  - `traverse` は docstring に「Alias for for/do」と書かれており、中身を 1 か所にまとめて委ねる整理は自然です。
  - `fnk` の lambda の組み立てを、ほかの macro と共有したくなる場面もあります。
  - 呼び手の書き方は変わりません。require の名、別名の import(`_doeff_traverse_Traverse` など)、答えはそのままです。

## 2. 関係する主張・前提と、予想に反して変わるもの

- **契約の適用範囲の内側です。**
  - §1 は「展開の形を、run の後の答えを変えずに作り替えることがある。そのたびに例や例の検を書き換えさせてはならない」と書いています。
  - R2 と、S1(traverse の展開の形・import の仕方を作り替える)に当たります。
  - 前提の「答えを変えない」も、下の 4. で実測して満たしています。
  - 口(require の名・別名の import・答え)は変えていないので、公開契約の拡張ではありません。
- **予想に反して変わるもの:** 例の検 `packages/doeff-traverse/tests/test_examples_run.py`。
  - `UNMARKED_EXPANSIONS`(113 行)に行を足す必要があります。
  - `test_expansion_check_reads_uses_out_of_context` が `_unmarked(uses) == ["3: val"]`(869 行)と期待値を固定しています。
  - `test_printed_expansion_copy_is_named` は、traverse の 1 段の展開が印を持つことを前提にしています(896 行)。
  - これらを直さないなら、doeff-hy の側が「1 段目に隠す名か `__import__` を残す」よう縛られます。
- **原因の契約は C2 です。** C1 と C3〜C7 は影響を受けません。

## 3. どの知識がどこへ漏れているか

漏れているのは「各 macro の **1 段目の** 展開が、例の検に分かる印を持つ」という事実です。これは macros が隠すはずの「展開の形と段の数」に当たります。漏れは次の 5 か所です。

- **`_EXPANSION_IMPORT = "__import__"`(154 行)。**
  - 隠す名の判定は doeff-hy の `hidden_name` に任せていますが、この印の種類だけは例の検の中で定義されています。注記にも doeff-hy の `_do-reference`・`open-form` の名が書いてあります。
  - 【実測】ex02・ex03 の `fnk` の展開は、印を `__import__` だけで持っていました。
- **`_expand_once` の `hy.macroexpand_1`(548 行)。** 1 段目しか見ないため、内部で段を 1 つ足すだけで印が消えます。
- **`UNMARKED_EXPANSIONS`。** macro ごとの「印が無い」という事実の写しで、2 本の検が両方向から照合しています。
  - 印が消えると `show_their_marks` が赤になります。
  - 印が戻ると `test_unmarked_table_names_unmarked_macros` が「行を外す」と赤になります(【実測】両方向)。
- **自己検の固定値(869 行・896 行)。** 今の doeff-hy の展開の形に固定されています。869 行の検の docstring は「展開を断られるかは doeff-hy の決める事」と緩めていますが、「印が無い」側は緩めていません。
- **断りの文。** 「展開に隠す名を持たせるか…」と、macro の持ち主に中身の変更を求めています。依存の向きが逆です。

**分類:**
- 公開契約の拡張ではなく、配線の変更でもありません。
- 共同の不変条件と見るなら、macros の口に宣言されているべきですが、そうなっていません。doeff-hy の側には定義も検も無く、【実測】doeff-hy 自身の検は緑のままでした。
- したがって、隠すはずの知識の漏洩と判断します。共同の不変条件として扱うつもりなら、R2 と S1 を「1 段目に印を残す変更に限る」と狭める必要があり、今の主張のままでは成り立ちません。
- 補足: traverse を委ねた後の 1 段の展開 `(hy.R.… traverse-body …)` を文字列で写した例は、C1 では見分けられなくなります。C2 が赤を出すこと自体は正しく働いていますが、直すよう求める先が持ち主として間違っています。

## 4. 最小の変更例・再現手順・観測すべき結果

変更(`packages/doeff-hy/src/doeff_hy/macros.hy`):
```diff
@@ fnk
    (fnk [x y] (+ (! (k1 x)) (! (k2 y))))"
+  `(hy.R.doeff_hy/macros.kleisli-lambda ~params ~@body))
+
+(defmacro kleisli-lambda [params #* body]
   ;; Expand bangs in the body …
@@ traverse
      (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])"
+  `(hy.R.doeff_hy/macros.traverse-body ~@forms))
+
+(defmacro traverse-body [#* forms]
   (setv #(bindings body-expr) (_parse-do-body forms "traverse"))
```

手順: 自分用の空の dir を `DOEFF_HY_CODE_STORE` に指して、次を走らせます。
```
DOEFF_HY_CODE_STORE=<空の dir> uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q --tb=line -rf
```

結果(すべて【実測】):
- **展開の同一性:** 例の中の fnk・traverse の使い所 15 個を最後まで展開し、gensym の番号を伏せて比べました。変更の前後で差分はありませんでした。
- **fnk + traverse を委ねた場合:** 10 failed / 68 passed / 1 skipped。
  - 赤は `test_example_expansions_show_their_marks` の ex02〜ex08(8 本)、`test_expansion_check_reads_uses_out_of_context`、`test_printed_expansion_copy_is_named` です。
  - `test_example_runs`、変異の検、件の中で失敗させる検は全部緑で、答えは変わっていません。
- **fnk だけを委ねた場合:** 例の検に、doeff-hy の `test_helper_imports_table.py` と、fnk を使う tests の 4 file を加えて走らせました。3 failed / 323 passed / 1 skipped で、赤は例の検の 3 本だけです。持ち主の側の検は全部緑でした。
- **例の検を直す側の手間:** `UNMARKED_EXPANSIONS` に fnk の行を足し、869 行の期待値も直すと、該当の 12 本は緑になりました。行を残したまま macro を元に戻すと、`test_unmarked_table_names_unmarked_macros` と 869 行の検が赤になりました(2 failed / 10 passed)。
- **別の作り替え方(`hy.I`):** `_do-reference` の `__import__` を `(hy.I "doeff.do")` に替えても、例の検は同じ 3 本が赤になりました。ただしこの形は doeff-hy 自身の検 6 本を赤にします。`hy.eval` で新しい名前空間に評価すると `hy` が束ねられておらず NameError になるので、完全には答えを変えない変更ではありません。そのため主な例は `hy.R` の方にしました。

## 5. 未確認の前提・不足する情報

- 【推測】doeff-hy が実際にこの作り替えをするかどうかは分かりません。現実性の根拠は、Hy の文書と traverse の docstring だけです。
- 【未測】型検査用の展開(`_static-view?`)、doeff-hy-check、誤りの位置の表示(`locate-synthesized`)、bytecode の guard の digest への影響。
- 【推測】for/do を同じように委ねた場合も、ex09 が同じ理由で赤になると見ています(走らせていません)。
- 【未測】直し方のうち「`_shows_expansion` に `hy.R`・`hy.I` の印を足す」案。
- 走らせたのは変更箇所に絞った検だけで、全体の検と code-quality は走らせていません(変更は残っていません)。
- S1 の表の行は traverse・for/do だけを名指しています。fnk の例は §1 と R2 の側で範囲内と判断しました。traverse の測定は S1 の行そのものに当たります。
- 作業木で変えた `macros.hy` と `test_examples_run.py` は、退避した写し(`/tmp/blind-a-work/`)から書き戻しました。sha256 が元と一致することを確かめています。変更を入れて走らせた回は、どれも自分用の bytecode の保存先(`/tmp/blind-a-work/code-store*`)を使いました。

`git status --short` の出力(作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-b7c4`)は空で、変更は残っていません。

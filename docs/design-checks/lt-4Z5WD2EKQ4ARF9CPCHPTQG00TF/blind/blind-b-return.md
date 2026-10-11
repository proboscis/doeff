## 結果

検査を素通りする責務違反を 1 つ作り、実測で確かめました。中身は、ex07 に失敗の起きた所(関数:行)を 1 行で出す機能です。印字に doeff-hy の macro が隠す「Hy の無名関数の番号」が載りますが、提示の検査はどれも拒否しません(実測)。そのうえで、macro の持ち主が答えを変えない内部の変更をすると ex07 の検が赤になります(実測)。共有の木は変えていません(`git status` は空)。

### 1. 機能要求と差分

**要求**: ex07 の item history で、失敗した件が例のどこで起きたかを 1 行で見たい。traceback 全体は lib の frame と checkout の path を含むので、今までどおり `…` で伏せる。例の file 自身の frame だけを要約して比べる。

**差分**(ex07 と期待の出力の 2 file だけ。検の file と表は変えない。`/tmp/lt4z5w-blind-b/feature-ex07.patch`):

```diff
--- a/packages/doeff-traverse/examples/ex07_proboscis_ema_rewrite.hy
+++ b/packages/doeff-traverse/examples/ex07_proboscis_ema_rewrite.hy
@@ -228,6 +228,12 @@
   (when (and item.failed (isinstance item.value BaseException))
+    ;; One line: where in THIS example the failure was raised (function:line, outermost first).
+    ;; Only this file's frames — the lib's frames and the checkout path differ between checkouts.
+    (print (+ "      raised in: "
+              (.join " -> " (gfor fs (tb.extract-tb item.value.__traceback__)
+                                  :if (= fs.filename __file__)
+                                  (+ fs.name ":" (str fs.lineno))))))
     (print "      --- traceback ---")
--- a/packages/doeff-traverse/examples/expected/ex07_proboscis_ema_rewrite.txt
+++ b/packages/doeff-traverse/examples/expected/ex07_proboscis_ema_rewrite.txt
@@ -20,6 +20,7 @@
       zip_failed
+      raised in: _hy_anon_17:129 -> fetch_price:95 -> _hy_anon_28:174
       --- traceback ---
@@ -37,6 +38,7 @@
       zip_failed
+      raised in: _hy_anon_17:129 -> fetch_price:95 -> _hy_anon_28:180
       --- traceback ---
```

書き手は lib の frame と path を避けており、S2 の注意を守っているつもりで書いています。

### 2. 本来の持ち主と、侵す契約

- **macros(doeff-hy)が持つべき知識が漏れる**: design-before-blind.md §2 では、macros の「隠す知識」は「展開の中身(gensym の名・Hy の無名関数の番号)」です。
  - `_hy_anon_17` は traverse の展開が作る、件ごとの関数の番号です。
  - `_hy_anon_28` は defhandler の FetchPrice の節の関数の番号です(174 行がその節の中にある事からの推測)。
  - file と行番号は例のものでも、関数の名は macro の展開と Hy の compile が付けます。例の期待の出力がその番号を固定するので、macro が隠す知識の 2 つ目の置き場が examples にできます。
- **依存の向きが逆になる**: 展開を変えると番号がずれます。その時に直すのは macros ではなく、examples の期待の出力です。macro の持ち主の、答えを変えない内部の変更が、例の検を赤にします。
- **自分の表との食い違い**: ex04_debug2 が OUTPUT_NOT_COMPARED に置かれた理由は「無名関数の番号 — 番号は defk の展開で変わる … 印字は Hy・doeff-hy の中身で、例の結果ではない」です。同じ種類の印字が、比べる例の中に入り込みます。
- **traverse-lib・VM の側(推測・未測定)**: 失敗した例外の `__traceback__` に、例のどの generator の frame が載るかは handler と VM の投げ方で決まります。これは traverse-lib の隠す「handler の中の frame」に近い知識で、その並びも固定されます。

### 3. 各検査が拒否しない理由(すべて実測で PASSED)

| 検 | 拒否しない理由 |
| --- | --- |
| `test_example_runs[ex07]` | 終了コードは 0。期待の出力を印字に合わせて書き直したので `_matches` が通る |
| `test_case_failure_shows_in_compared_output[ex07]` | 変異の元の文 `"MSFT" 300.0` は 1 つのまま。写しは 0 で終わり、`Success: 2` / `Failed: 3` で期待に合わない。写しを手で走らせて確かめた |
| `test_tables_name_examples_as_they_are` | 期待の出力の集合は変わらない。`…` を置いた例の集合は {ex05, ex07} = 変異の表の鍵のまま。新しい行は path を含まない(frame は `__file__` で絞る) |
| `test_example_uses_only_doeff_hy_macros[ex07]` | defmacro も新しい require も無い |
| `test_not_compared_example_runs_no_program` と inline の 2 本 | ex04_debug と ex04_debug2、それに固定の文字列だけを見るので無関係 |
| `test_broken_example_is_still_broken` | BROKEN が空なので skipped |

根本の理由は設計そのものにあります。example-check は `…` の意味と checkout の path の文字列しか知らず、どの印字が誰の隠す知識かを知らない作りです。

### 4. 再現の手順と結果(実測)

```sh
mkdir -p /tmp/lt4z5w-blind-b/tree
git -C /home/ubuntu/.worktrees/doeff-wt-dc-4z5w archive c9e1622b5 | tar -x -C /tmp/lt4z5w-blind-b/tree
cd /tmp/lt4z5w-blind-b/tree && make sync
T=packages/doeff-traverse/tests/test_examples_run.py
uv run pytest $T -q                                   # (a) 31 passed, 1 skipped
patch -p1 < /tmp/lt4z5w-blind-b/feature-ex07.patch
uv run pytest $T -q                                   # (b) 31 passed, 1 skipped  ← 検は通る
patch -p1 < /tmp/lt4z5w-blind-b/macros-pre2871.patch
uv run pytest $T -q                                   # (c) 1 failed: test_example_runs[ex07], 30 passed, 1 skipped
```

`macros-pre2871.patch` は `_traverse-form` の実行時の展開 1 行を、commit 072dce983(#2871)の前の形に戻します。持ち主自身が「答えが 1 文字も違わない」と確かめた展開です。

```diff
-      (let [step `(_doeff_do (fn [~param] (do ~inner-body)))]
+      (let [step `(fn [~param] ((_doeff_do (fn [] (do ~inner-body)))))]
```

- **(c) の赤の中身**: `_hy_anon_17` が `_hy_anon_19` に、`_hy_anon_28` が `_hy_anon_31` に変わるだけです。`Success: 3` / `Failed: 2` と履歴は変わりません。
- **(d) 対照**: (c) の macro の変更を残したまま、ex07 と期待の出力を元の file の写しで戻しました(`patch -R` と同じ結果)。結果は 31 passed, 1 skipped です。元の版では同じ macro の変更が例に波及せず、波及はこの差分が持ち込んだものです。
- 今の木は機能の差分を当て、macro を元に戻した状態です。

### 5. 未確認の前提・不足

- **未測定**: traverse-lib や VM の変更(handler の Try の包み方など)で、例の frame の並びが変わるか。§2 の traverse-lib の件は推測です。
- **推測**: `_hy_anon_28` を作ったのが defhandler の展開である事は、行の位置からの推測で、compile を追ってはいません。
  - 番号が module 全体の数え上げに依る事は、Hy の `get_anon_var` を読んだ事と (c) のずれの実測に基づきます。
  - ほかの macro の commit(7613a2147・c30d79cc5・a53309e37・66d4c9428)も番号をずらすだろうというのは推測で、測ったのは 072dce983 を戻す変更 1 つだけです。
- **実測**: 「raised in」の行が path に依らない事は、相対と絶対の起動、tmp_path の写しの 3 通りで確かめました。
- **走らせた範囲**: 走らせたのは `test_examples_run.py` の 1 file だけです。日次の段の `pytest packages/doeff-traverse/tests` の、ほかの file は走らせていません。環境は venv の Python 3.14t と Hy 1.3.1 の 1 機体です。
- **判断の材料が設計文書だけ**: 例の file の frame の関数名を macros の隠す知識とみなす根拠は、design-before-blind.md §2 の記述と OUTPUT_NOT_COMPARED の理由の文だけです。レビューでこの差分が受け入れられるかは分かりません。

材料の置き場所: `/tmp/lt4z5w-blind-b/feature-ex07.patch`、`/tmp/lt4z5w-blind-b/macros-pre2871.patch`、走らせた記録は `/tmp/lt4z5w-blind-b/run-*.txt`、変異の写しの出力は `/tmp/lt4z5w-blind-b/mut/out.txt` です。

WAIT: work — 依頼元の会話の次の依頼

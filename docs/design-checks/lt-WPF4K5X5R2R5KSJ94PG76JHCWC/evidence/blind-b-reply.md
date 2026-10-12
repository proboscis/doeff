## 結論

検査を通るのに責務を侵す実装を 1 つ作り、実測で確かめました。macro の補助が、Hy の compiler の非公開の method `_syntax_error` に依る変更です。書き方は Hy でふつうの method 呼びの形 `(._syntax-error compiler …)` で、R7 の数え方はこの形を拾いません。提示された 3 本の deftest はどれも緑のまま通り(実測)、依りの一覧には 1 行も足されません。

## 1. 機能要求と最小差分

**要求:** defn の中に `(val …)` を書いた時の ADR-DOE-HY-006 の誤りを、Hy 自身の構文の誤り(HySyntaxError。file・行・列・source の該当行を持つ)と同じ形で出したい。
- 今の出方(実測): 素の SyntaxError を Hy が HyMacroExpansionError に包みます。文の頭は「expanding macro val … SyntaxError: (val …) (line 4): …」と 2 重になり、行は文字で埋めた物で、SyntaxError の下位でもありません。

**差分(`packages/doeff-hy/src/doeff_hy/macros.hy` の `_module-level` の所):**
```hy
+(defn _syntax-error-at [compiler form message]
+  "form の位置を持つ、Hy 自身の構文の誤りと同じ形の誤り(HySyntaxError)を投げる。…"
+  (raise (._syntax-error compiler form message)))
 ...
-    (raise (SyntaxError (+ "\n(" head " …) (line " (str (getattr form "start_line" "?")) "): " … " [ADR-DOE-HY-006]\n"))))
+    (_syntax-error-at compiler form
+                      (+ "(" head " …) は defk・deftest・defhandler の節の本体か、module の直下にだけ書けます"
+                         "(defn・fn・class・let の中には書けません — 関数は defk で書きます)。 [ADR-DOE-HY-006]")))
```
- 実装者は、これに合わせて `test_val_var_lazy.py` の検 1 本を HySyntaxError を待つ形に書き換えます。この検は走らせていません。
- ふつうの実装者が書く形だと言える理由:
  - Hy の compiler 自身が `raise self._syntax_error(...)` と書いています。
  - `(.名 obj …)` は macros.hy の中でいつも使っている method 呼びの形です。
  - compiler を受ける引数の名は、決まりどおり `compiler` です。
- 変更を当てた後の動き(実測): HySyntaxError が file と行 4 を持って出ます。traceback の末尾は `compiler.py:_syntax_error` です。

## 2. 本来の持ち主と、侵す契約

- **新しい依り:** `HyASTCompiler._syntax_error` の名・引数の順 `(expr, message)`・投げるか返すか。実測では、Hy 1.0.0〜1.2.0 は誤りを return し、1.3.0〜1.3.1 は raise します。小版の間で意味が変わった内部の物です。
- **持ち主:** 「doeff がどの Hy の内部に依るか」という知識は、依りを数える責務(reliance-population)が持ちます。この責務は手で書く一覧を持たず、source の形から数えます。だから doeff-hy の回避の側は、依りを数えられる形で書く事を共有の契約として負います(S2 の前提)。
- **何が移るか:** この依りの知識は macros.hy の中にしか在らず、数える・測る・判定の 3 か所のどれにも届きません。「Hy をこの版へ上げてよいか」の判断は、R7 を置く前と同じく、登記の後の日次の検か、利用者の手元の実行時の落ちへ戻ります。
- **侵す契約:**
  - law internal-reliances-exist-in-pypi-hy の「`for_all reliance in compiler_reads(doeff)`」
  - R7 の「依りは source の形から数える」
  - S2 の「変えるのは doeff-hy の source だけで、数える code は変えない」。この依りを照合に入れるには、数える code(`hy-form-reliances`)を変えるしかありません。
  - ADR の :context が「数えない」と宣言しているのは、別名で受けた module への代入と getattr の文字列の名の 2 つだけです。`(.名 compiler …)` は、数える形にも数えない形にも書かれていません。

## 3. 各検がこの code を拒まない理由

- **`internal_reliances_exist_in_pypi_hy`:** `hy-form-reliances` が数えるのは、先頭が記号 `.` で、2 番目の要素が `COMPILER-SYMBOLS` に在る式だけです。reader は `(._syntax-error compiler form msg)` を `((. None _syntax-error) compiler form msg)` と読みます(実測)。内側の式の対象は `None` で、外側の頭は式なので、どちらにも当たりません。依りの一覧は変更の前後とも同じ 9 行で(実測)、9 行はどれも Hy 1.3.1 に在るので、passed でした(実測)。
- **`reliance_probe_names_what_pypi_hy_lacks`:** 手で書いた 7 行を測るだけで、doeff の source を読みません。passed(実測)。
- **`judgments_reject_the_counterexamples`:** 字で書いた入力に純関数を当てるだけで、反例に method 呼びの形が在りません。passed(実測)。
- **選び方(`make test-changed` の契約の組):** `select_targets` を直に呼んで確かめました(実測)。
  - uv.lock を変えると、R4・R6・R7 の 3 本と repo 全体の 2 本が選ばれます(契約どおり)。
  - macros.hy を変えると、選ばれるのは `test_hy_stubs.py`・`test_examples_run.py`・repo 全体の 2 本・変えた検の file で、R7 は選ばれません。
  - Hy を上げる便では R7 が選ばれますが、上のとおりこの依りは見えません。
  - doeff-linter の逆依存の選びは呼んでいません。

## 4. 同じ設定で両方を確かめる最小の手順

条件は版 fe951a108・uv.lock の hy 1.3.1・`uv run --no-sync` で、ここに挙げた結果はすべて実測です。

| 手順 | 結果 |
|---|---|
| 差分を当てて `scan-hy-reliances` を走らせる | 9 行のまま。`_syntax_error` の行は無い |
| 差分を当てて R7 の 3 本を 1 本ずつ走らせる | 3 本とも passed |
| 要件 2 と同じく、在らない名 `._syntax-errorr` を書いて R7 の照合の検を走らせる | **passed**。一方、defn の中に val を書いた file を compile すると `AttributeError: 'HyASTCompiler' object has no attribute '_syntax_errorr'` で落ちる |
| 対照: 同じ名を数える形 `((. compiler _syntax-errorr) form message)` で書く | **failed**。「macros.hy: Hy 1.3.1 の macro が受ける compiler(_hy_compiler)に属性 _syntax_errorr が無い」と名を挙げる |
| 対照: 本当の名を数える形で書く | 一覧に `('macros.hy','compiler-attribute','_hy_compiler','_syntax_error')` が出る |

つまり「名を挙げて赤になる」かどうかは、依りが在るかではなく、書いた形で決まります。

## 5. 確かめていない事・不足する情報

- PyPI の Hy の 1.0.0・1.2.0・1.3.0・1.3.1 には、どれも `_syntax_error(self, expr, message)` が在りました(実測)。だから実在の版では「緑なのに落ちる」を示せず、示したのは在らない名を書いた失敗ケースです。
- 数える形に直しても、種類 compiler-attribute が測るのは `hasattr` だけです。1.2 から 1.3 の return から raise への変化のような意味の違いは測りません。これは probe の code を読んで出した結論で、試してはいません。
- 走らせていない物: 書き換えた `test_val_var_lazy.py`、doeff-hy の他の検、`make test-changed` の全体、逆依存の選び、pyright・semgrep・doeff-hy-check。これらが非公開の属性の読みを拒むかは分かりません。
- 推測:
  - Hy が `_syntax_error` を変えた版へ上げた時、書き換えた検が日次で落ちると見込みます。ただしそれは登記の後です。
  - 同じ穴は `(.compile compiler …)` などどの method 呼びにも、defmacro の本体の `(.名 _hy-compiler …)` にも当たると見込みます。試したのは `_syntax_error` だけです。
- 作業木は手で元へ戻しました(git status に残るのは `BLIND_INPUT.md` だけ)。差分は `/work/agent-workspaces/a-R6ECWFH17Q0S5X4EKT02N5XNQB/scratch/syntax_error_at.patch` に、確かめに使った script は同じ dir に在ります。

WAIT: work — 利用者の次の入力

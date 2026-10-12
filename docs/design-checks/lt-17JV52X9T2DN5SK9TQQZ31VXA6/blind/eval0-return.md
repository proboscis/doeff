## 結論
R1 と R2 は実測で崩れ、R4 は一部が崩れました。R3 は崩れていません。
- R1: macro が作った値の形を印字しても検が全部緑のまま通る例を 3 通り作れました。どれも、答えを変えない macro の変更を入れると、その例だけが赤になりました。
- R2: 答えを変えない macro の変更で、今の例か検が赤になる物を 3 つ見つけました。

試験用の物は全部 `/work/agent-workspaces/a-N8D3D11TB04MYSY4SNR4PQKPWT/scratch/` に置いてあります(以下 `$S`)。改変した macros.hy での試験は、機体で共有する保存先を汚さないように、自分の保存先 `DOEFF_HY_CODE_STORE=$S/store` で走らせました。

## R1 — 崩れた(実測)
`$S/r1examples/` の 3 本(ex10〜ex12)と期待の出力を examples/ に置いて検を走らせると、**94 passed, 1 skipped** で全部緑でした。そのうえで変更を 1 つずつ入れると、次のようになりました。

| 例 | 抜け道 | 答えを変えない変更(patch) | 結果 |
|---|---|---|---|
| `ex10_trace_traverse.hy` | 内側に置いた defhandler が Traverse を受けて形を印字し、`reperform` で先へ渡す | M2(`$S/M2.patch`): 件ごとの関数を #2871 より前の形 `(fn [x] ((_doeff_do (fn [] …))))` に戻す | ex10 だけ赤。`-trace: Traverse step-is-do: True` → `+… False` |
| `ex11_shared_stage.hy` と `examples/support/stages.hy` | traverse の値を lib の側で作り、例はその class 名を印字する | M1(`$S/M1.patch`): traverse の一番外を `do` の Program で包む | ex11 だけ赤。`stage: Traverse` → `stage: Call` |
| `ex12_star_require.hy` | `(require doeff-hy.macros :macros [<- traverse do!])` と書き、`(type (do! …))` を印字する | M3(`$S/M3.patch`): do! を何もしない handler で包む | ex12 だけ赤。`block: Call` → `block: WithHandler` |

赤になった検は、どの場合も `test_example_runs` と `test_example_output_survives_expansion_mutation` の 2 つで、結果は 2 failed, 92 passed でした。

ex10 の要の部分です。
```hy
(defhandler trace-traverse
  (Traverse []
    (print "trace:" (. (type effect) __name__) "step-is-do:" (hasattr effect.f "__wrapped__")
           "items:" (. (type effect.items) __name__))
    (reperform effect)))
```

それぞれ素通りする原因(コードを読んでの判断で、上の実測と合っています)
- **ex10:** 変異が包むのは一番外の値だけで、包む handler は受けた effect をそのまま外へ渡す(`Pass`)。そのため、内側の handler から見える Traverse・`f`・`items` は変異の下でも変わりません。
- **ex11:** 変異は `__file__` が例の file である module の compile にしか当たりません。さらに `EXAMPLES.glob("*.hy")` は下の dir を見ないので、`support/` の lib は検の目にも入りません。
- **ex12:** `_doeff_hy_macro_names` は `module [名…]` の並びしか読みません。`:macros [...]`(Hy 1.3.1 で有効)では使い所が 0 件になり、表の検 `test_example_macros_are_mutated_or_named` が働きません。do! は変異の表に無いので包まれません。

再現の手順: 上の 3 本と `expected/*.txt`・`support/stages.hy` を examples/ に写し、`uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q` で緑を確かめます。次に `git apply $S/M1.patch`(M2・M3 も同じ)を当てて、もう一度走らせます。

## R2 — 崩れた(実測)
- **R2a(`$S/R2a.patch`):** `(defmacro traverse [_hy-compiler #* forms]` と、受けるだけで使わない形にする 1 行の変更です。
  - 例をそのまま走らせた出力は変わらず、`test_example_runs` は全部緑でした。
  - **9 failed** になりました。traverse を使う例 6 本(ex04_simple・ex04_traverse_macro・ex05・ex06・ex07・ex08)の `…survives_expansion_mutation` と、自己検 3 本(`test_expansion_mutation_changes_the_built_value_not_the_answer`・`…names_an_example_that_breaks_on_the_built_value`・`…spares_the_modules_an_example_imports`)です。
  - 原因: 変異の包み `expand(_hy_compiler, *forms)` は Hy から常に compiler を受け取ります(Hy は `co_varnames[:1]` で決める)。ところが元の macro は `macro(*forms)` で呼ぶので、最初の form が `_hy_compiler` の位置に入り、`NameError: name 'x' is not defined` で落ちます。
  - 断りの文は「例が展開の作った値の形(型・属性)に頼っている」と例のせいにしており、名指しが誤っています。doeff-hy には既に `_hy-compiler` を受ける macro(val・defk・defhandler など)が多くあるので、起こりやすい変更です。
- **R2b(`$S/R2b.patch`):** traverse の展開を、検自身が「答えを変えない」と呼ぶ変異と同じ形(何もしない handler の下の Program)で恒久に包みます。
  - 例 9 本は全部緑でした。
  - `test_expansion_mutation_spares_the_modules_an_example_imports` が `assert 'WithHandler' != 'WithHandler'` で赤になります。この検は「今の macro の値は WithHandler ではない」と決め打ちしています。
- **R2c(`$S/R2c.patch`):** `_traverse-form` の実行時の展開で、`_doeff_do` と `_doeff_traverse_Traverse` を `hy.I.doeff.do` と `hy.I.doeff_traverse.Traverse` に替えます。
  - ex04_traverse_macro・ex08 の出力は同じでした。
  - **3 failed** になりました。印の検 2 本(ex04_traverse_macro の `['110: traverse']` と ex08)と、`test_expansion_check_reads_uses_out_of_context`(`['3: val', '8: traverse'] == ['3: val']`)です。
  - 設計上は「UNMARKED_EXPANSIONS に置く」と決めた赤ですが、R2 の主張「全部の検が緑のまま」には反します。
- 参考: M1・M2・M3 では、今の例 9 本も検の自己検も赤になりませんでした。

## R4 — 一部が崩れた(実測)
- **lib の展開は変わらない:** 崩れていません。変異の下でも ex11 は `stage: Traverse` と出し、保存先にある stages.hy の code にも `WithHandlerType`・`Pass` は入っていませんでした。
- **保存先の中身は変わる:** 自分の空の保存先で、traverse を使う lib(stages.hy)の entry を見ました。
  1. 普通に 2 回走らせた後は entry が 1 つ(`aa3b0163`・digest `0cb511aa…`)でした。
  2. 変異の子を 1 回走らせると、新しい entry `4a11437a`(digest `environment-re…`)が**足されました**。
  3. その後に普通に走らせても、2 つとも残りました。

  足された entry の中身は変異していない code で、普通の走りには使われません。ただ「保存先を変えない」という主張には反します。lib の `__pycache__` の .pyc も、変異の走りで書き直され、次の普通の走りでまた書き直されます。
- **今の例 9 本では保存先は変わらない:** 全部を変異の下で走らせても、保存先の file の一覧は 76 → 76 で変わりませんでした。traverse・for/do を使う Hy の lib を import する例が無いためです。
- **例そのものの bytecode:** `hy <例>` は loader を通して例を compile し、`examples/__pycache__/<例>.cpython-314.pyc` を書きます。変異の子はここへ**変異した bytecode**(`WithHandlerType`・`Pass` を含み、記録は無い)を書きました。保存先には入りません。記録が無いので、次の普通の走りは compile し直し、変異の無い code に戻ることを確かめました。
- 推測(未確認): 検を既定の環境で走らせると、`…spares_the_modules…` の自己検が、機体で共有する保存先 `/work/state/doeff-hy-code-store` に、上と同じ使われない entry を毎回足すはずです。共有の保存先は見ていません。

## その他の誤判定(4 の項)
- **正しい例を赤にする:** `(require doeff-hy.macros *)` と書くと、`_required_packages` が `*` を module 名と読みます。その結果、`test_example_uses_only_doeff_hy_macros` が `['require hyx_XasteriskX']`(doeff-hy の外からの require)として赤にしました。全部の検を走らせて確かめています。
- **正しい例を赤にするはず(一部だけ実測):** `[traverse :as trav]` は Hy 1.3.1 で有効で、変異も別名まで届きます(`built: WithHandler`)。ところが検の読み取り関数は `trav` を別の macro の名として拾います。そのため表の検が `trav` の行を求めて赤になるはずです。読み取り関数の結果だけを見て、検全体は走らせていません。
- **断りの文が誤り:** M1〜M3 では、出力が変わった原因は macro の変更なのに、`…survives_expansion_mutation` は「変異の下で変わる」と言い、`test_example_runs` と重ねて赤になります。R2a では例のせいにしています。

## 崩れなかった主張
- R3: 最初と最後の 2 回とも 76 passed, 1 skipped でした。対象の commit 1e06853f3・ec89495d4 は examples/ と expected/ を触っていません。
- R4 の lib の展開: 上のとおり変わりませんでした。
- 未実測: UNMUTATED_MACROS に既に「残る限界」と書かれている defk・fnk・defhandler の型の印字と、`hy.eval` に別の module を渡す抜け道は試していません。他の門(code-quality・module 契約)が ex10〜ex12 を断るかも試していません。

## 作業木の最後の状態
macros.hy を元へ戻し、ex10〜ex12・`support/`・`examples/__pycache__` を `$S` へ退避しました。sha256 が最初と全部一致し、検は 76 passed, 1 skipped です。`git status --short` の出力は空です。

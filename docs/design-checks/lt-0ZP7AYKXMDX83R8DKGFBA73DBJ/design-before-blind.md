# 設計検証 lt-0ZP7AYKXMDX83R8DKGFBA73DBJ — 盲検の前の主張(card ki-fe7a2e60fdfb)

この file は盲検 A・B を起こす前に書いて保存した主張で、盲検の後に書き換えない。盲検の後の結論は design.md に書く。

## 0. 範囲と版

- 基の版: doeff origin/main fe951a108277a99761b2c888958b827d4eebfc82。
- 提案の版: branch wt/ki-fe7a-expansion-string-examples の 6a7f25105e14b7c4dd52a76652edf078661eb46c(2 commit — 例の検の変更と、
  検の module 契約の allow)。
- 完了の範囲: 設計と、提案の実装(branch に commit 済み)。main に入れるのは実装の依頼(class dev)の担い手 — この依頼(class
  investigate)の会話は merge の列に載せない。
- 出自: 設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP(card ki-3497c5fd38df)の design.md §6 の残る限界「展開を引き出さずに、展開の形を
  文字列や quote した form で写して印字する file は見分けない。macros が変わっても赤にならず、写しが古びる」。

## 1. 要件(固定)

- R1(card の受入 cedbad616): 展開の形を文字列で写して印字する例の file を足すと、例の検が名指して赤になる。その失敗ケース(の検)が
  doeff main で緑。
- R2: 答えを変えない doeff-hy の macro の変更で、examples/ の例と例の検(test_examples_run.py)は変わらない(前の設計検証の S1 の
  主張を保つ)。
- R3: 今の例 9 本(ex01〜ex09)と期待の出力は書き換えずに緑のまま。

再現(直す前): evidence/repro/r1-copy-passes-on-base.txt — ex04_simple.hy に今の traverse の展開を写した文字列の print を 1 行足した
file は、例の検の 4 本(走る・件の失敗が出る・Program を走らせる・macro の持ち主)と期待の出力の検を全部通った。

## 2. 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(packages/doeff-hy/src/doeff_hy/macros.hy・outcome_forms.hy・handle.hy ほか)— macro の名と展開の規則 | 展開の中身(隠す別名 `_doeff_…`・Hy の gensym `_hy_gensym_…`・展開が module を引く `(__import__ "…" :fromlist …)`・形) | `(require doeff-hy.macros [名 …])` |
| hidden-names | doeff-hy の `doeff_hy.static_stub.hidden_name(name) -> bool` — 公開の面に出さない名(`_hy_`・`_doeff_`・`__doeff_` で始まる名と MODULE_TAGS)の 1 か所の定義 | 接頭辞の表(`_HIDDEN_PREFIXES`) | 関数 `hidden_name` |
| examples | examples/*.hy と examples/expected/<stem>.txt — 例ごとの data・handler の組・印字 | なし(読み物) | `python -m hy <例>` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | packages/doeff-traverse/tests/test_examples_run.py — 例の母集団(glob)・表(BROKEN・NO_CASES・MARKS・UNPLACEABLE)・出力の比べ方・macro の持ち主の検・展開の形の印の検 | 子の走らせ方・比べ方の中身 | pytest の検の名と、断りの文 |
| failing-child | packages/doeff-traverse/tests/run_example_failing_a_case.py — 件の中で失敗させる子と実行の印(この変更で触らない) | 失敗の起こし方 | 標準エラーの `case-calls: N program-runs: M` |
| module-contract | .agents/code-quality.d/doeff-traverse-tests.json — 検の module の依存の許し(allow) | なし | code-quality の dependency-not-allowed |

## 3. 公開契約(提案)

- C1 example-check → examples: 期待の出力に「展開の形の印」を置かない。印 = (a) `_` で始まる語(Hy の綴りの `-` を含む)w のうち
  `hidden_name(hy.mangle(w))` が真の物(Hy・doeff-hy の隠す名)、(b) 語 `__import__`(展開が名を束ねずに module を引く手立て)。
  UNPLACEABLE の行「展開の形の印(Hy・doeff-hy の隠す名・展開の __import__)」。前の行「Hy が付ける名(_hy_anon_・_hy_gensym_)」は
  この行に含まれるので置き換えた。
- C2 example-check ← macros: 例が使う doeff-hy の macro の使い所(例の require `(require doeff-hy.macros [名 …])` の名を頭に持つ
  式)の 1 段の展開(`hy.macroexpand_1`・`hy.repr`)は、どれも印を持つ。持たない使い所は `test_example_expansions_show_their_marks`
  が `<行>: <macro>` で名指す — 印の無い展開の写しは C1 で見分けられないため。
- C3 example-check ← hidden-names: 隠す名の決まりは `hidden_name` を呼んで判じ、接頭辞を写さない。
- C4 失敗ケース: `test_printed_expansion_copy_is_named` — ex04_simple.hy に、今の traverse の展開(検が展開して得た文字列)を
  print する 1 行を足した file を走らせ、0 で終わり、macro の持ち主の検を通り、標準出力が「ex04_simple の期待の出力 + 写し」で、
  その出力に UNPLACEABLE がちょうど「展開の形の印」を名指す。

## 4. 変更シナリオ(盲検の前の主張)

### S1(axis effects): doeff-hy が答えを変えずに展開を変える

- 変更: (a) 隠す別名を改名する(例 `_doeff_traverse_Traverse` → `_doeff_tv_Traverse`)。(b) fnk の `do` の引きを `__import__` から
  module の頭の隠す別名へ替える。(c) 展開が隠す名も `__import__` も持たない macro を足す/変える。
- 主張: (a)(b) では examples と example-check は変わらない — 印は名の一覧ではなく、持ち主 hidden-names の規則と `__import__` で判じる。
  (c) では example-check の `test_example_expansions_show_their_marks` が、その macro を使う例の使い所を名指して赤になる。直しは
  macros の展開に隠す名を持たせるか、example-check の印の種類を足す事で、どちらも意図した契約(C1・C2)の変更。
- 予想する範囲: (a)(b) macros だけ。(c) macros か、macros と example-check。
- 変える module: macros(+ (c) で example-check)。変えない module: examples・hidden-names・failing-child・module-contract。

### S2(axis simulation): 例を 1 本足す・例が展開を写して印字する

- 変更: (a) 新しい例 ex10(新しい doeff-hy の macro を使ってもよい)を足す。(b) 例に展開の写し — 今の展開・古い展開・quote した
  form の `hy.repr`・model の `str` — を印字する行を足す。
- 主張: (a) は glob で全部の検に入り、表の変更は要らない(件の無い例なら NO_CASES)。(b) は期待の出力の検が file の名と「展開の形の
  印」を名指して赤。写しでない普通の印字(結果の値・`fetch_price` のような `_` を中に持つ名・`__init__`)は赤にならない。
- 予想する範囲: examples だけ((b) は赤で止まる)。
- 変える module: examples。変えない module: example-check・macros・hidden-names・failing-child・module-contract。

### S3(axis effects): doeff-hy が隠す名の決まりを変える

- 変更: hidden-names の接頭辞を足す/変える(例: macro の補助を `_dh_` で始める決まりに替え、`_HIDDEN_PREFIXES` に足す)。
- 主張: example-check は変わらない(`hidden_name` を呼ぶ)。macros が新しい接頭辞の名を展開に使っても、C1・C2 はそのまま効く。
- 予想する範囲: hidden-names と macros。
- 変える module: hidden-names・macros。変えない module: example-check・examples・failing-child・module-contract。

### 適用しない軸

- hardware: 検は機体の資源の種類に依らない。子の process の並びの数(PARALLEL_CHILDREN)は変えない。
- storage: 読む物は repo の file だけ、書く物は pytest の tmp_path だけ。保存の仕方を変える要求は無い。
- distribution: 1 つの pytest の process と、その子の process。cluster へ移す要求は無い。
- concurrency: 新しい検で子を走らせるのは C4 の 1 本だけで、順に走らせる。機体の検の枠は root の conftest が持ち、変えない。

## 5. 強制方法と実装の在りか

| 守る事 | 強制方法 | 実装の在りか | 実行経路 |
|---|---|---|---|
| C1 期待の出力に展開の形の印を置かない | 契約の検(pytest)— UNPLACEABLE の行と test_expected_output_places_nothing_unplaceable | test_examples_run.py の `_shows_expansion`・`UNPLACEABLE`・`_unplaceable_in` | pyproject の `[[tool.doeff.contract-tests]]` の prefix `packages/doeff-traverse/` と `packages/doeff-hy/src/doeff_hy/`・`packages/doeff-core-effects/doeff_core_effects/outcomes.py` がマージの前に選ぶ。日次の全体 |
| C1 の正常例・失敗例 | 表の行ごとの見本の検 | test_unplaceable_table_catches_its_samples(写し 7 形を拾い、普通の名を拾わない) | 同上 |
| C2 例が使う macro の展開はどれも印を持つ | 契約の検(例ごとに parametrize) | test_example_expansions_show_their_marks・`_doeff_hy_expansions`・`_doeff_hy_macro_names` | 同上(doeff-hy の macro を変えると選ばれる) |
| C3 隠す名の定義は 1 か所 | import の依存(公開の関数を呼ぶ)と module 契約の allow | `from doeff_hy.static_stub import hidden_name`・.agents/code-quality.d/doeff-traverse-tests.json の allow `doeff-hy-static-stub` | code-quality(変えた所の範囲)の dependency-not-allowed |
| C4 失敗ケース | 子を 1 本走らせる検 | test_printed_expansion_copy_is_named | 同上 |
| R2・R3 | 今の検(走る・件の失敗・Program・macro の持ち主)のまま | test_examples_run.py | 同上 |

型で表せない所: 「写し」かどうかは文の意味で、型や import の向きでは表せない — 印字の文の語で判じる契約の検に置いた。

## 6. 予想する限界(盲検には渡さない)

- 隠す名も `__import__` も持たない一部だけの写し(例 `(fn [] (do (+ acc x)))`)と、隠す名を公開の名へ言い換えた写し
  (`(Traverse (do (fn [x] …)) items)`)は見分けない。
- 写しを印字せず、source の文字列・註に置くだけの file は見分けない(期待の出力に出ない)。
- C2 は 1 段の展開だけを見る。外の macro の 1 段の展開に印が無く、内の macro の展開にだけ印が在る形は C2 で赤になる(保守的)。
- lib の例外の文が隠す名(`__doeff_resumption__` など)を含み、例がそれを印字すると、期待の出力に置けない(印 MARKS で伏せられない)。
- C2 は例の require を評価する(doeff-hy の macro の module を import する)。

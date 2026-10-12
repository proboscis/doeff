# 設計(盲検の前に保存した版)— 依頼 lt-JGXTY7ES6816GHSNAABAQYJE15

## この文書の目的

定義の macro の展開が import を置く場所について、残りの 3 つの形を直した変更(doeff の main に着地済み: 49a293f19・c4590550)の、責務の分け方・将来の変更への主張・強制の方法を、盲検の失敗例探しの**前に**固定する。盲検の返答を受けて書き換えた結論で、ここの主張を上書きしない(上書きせず `design.md` に差分と理由を残す)。

## 完了の範囲

実装完了まで(main に着地済み)。強制の方法は実装済みで、変更範囲の検査(pytest・品質検査の速い段)から呼ばれる。設計の予定だけで未実施の物は無い。

## 受入条件

| 番号 | 受入条件 |
|---|---|
| R1 | 型検査の投影で、関数・class の本体の中の defrecord(頭の辞書つき)・defwire・defeffect・deff の展開の import が、外の関数・外の class の局所の名にならない(外の関数が先に module の段の `doeff_hy` を読んでも未束縛と読まれない) |
| R2 | class の本体に直に書いた defrecord・defwire・defeffect が、module の段に `doeff_hy` が無くても建ち、使える |
| R3 | conductor.hy の defworkflow を関数の中で使っても、外の関数の局所の名に `_conductor_*` を足さない |
| R4 | 3 つとも、7ad1ca7a2 と同じ `_scoped-definition` の 1 か所に寄せて直す(依頼の方針) |

## 責務

| id | 責務 | 持つ知識 | 隠す知識 |
|---|---|---|---|
| scoped-definition | 定義の macro の展開の文の並びを、書かれた所(module・class の本体・関数の本体)と、実行時の展開か型検査のための展開かに合わせて組む | 書かれた所の判断(`_in-function-body?`・`_function-scope?`・`_in-class-body?`)・包むか・module の globals に置くか | 場所ごとの import の置き方 |
| definition-macros | 定義の中身(契約・型・展開の文)を作る(defk・deff・defeffect・defrecord・defwire・defworkflow ほか) | 契約の鍵・欄・展開の文 | なし(場所を判断しない — 文の並びを `_scoped-definition` に渡すだけ) |
| projection-imports | 型検査の投影の木で、macro が合成した import を関数・class の本体から module の段へ集める | macro が合成する import の module の綴りの表(doeff_hy の module・defsystem の module・defeffect の答えの分け方の module) | 木の歩き方 |
| scope-tests | 上の 3 つの境界を機械で守る | 検体の表・局所の名の数え方 | — |

公開の口:

| 責務 | 口 | 失敗 | effect |
|---|---|---|---|
| scoped-definition | `(_scoped-definition compiler name forms [module-forms] [nested-reads])` — forms は import を含む文の並び | 呼び手が forms に負う事(関数の中で module の状態を変えない)を破ると外の関数を呼ぶたびに積み増す | 実行時の展開の文が、`nested-reads` を真にした class の本体でだけ module の globals へ `setdefault` する |
| projection-imports | `with_module_level_imports(tree) -> tree` | 表に無い module の import は動かさない | なし(受けた木をその場で替える純粋な変換) |

不変条件:

- 関数の中の定義の展開が外の関数に残す局所の名は `name` だけ(実行時の展開)。
- module と class の本体の定義は包まない(`__qualname__` と引数なしの `super()` を保つ)。
- module の globals を変えるのは、`nested-reads` を真にした定義が class の本体に書かれた時だけ。defhandler は module の globals を変えない(別の決め)。
- 型検査の投影で、関数・class の本体の中に、macro 合成の doeff_hy の import(と表の module の import)を残さない。利用者が書いた import は動かさない。

## 前提

- 実行時の展開は包める(定義した値だけを `name` に束ねる)が、型検査のための展開は型の宣言(class・def)を外の関数に残す必要があるので包めない。
- 展開の import の置き場の決めは、実行時は `_scoped-definition`、型検査の投影は後処理の 1 か所に持つ。macro 自身は展開の中から module の段へ文を出せない。
- 未知の将来をすべて予測できるとは主張しない。

## 変更シナリオと主張

| id | 軸 | 変更要求 | 変える module | 変えない module | 主張 |
|---|---|---|---|---|---|
| S1 | effects | 新しい定義の macro を足す、または既存の macro の展開に新しい補助の import を足す | その macro を持つ module と、検体 1 つ(scope-tests の表 1 行) | scoped-definition・projection-imports・他の定義の macro | import の置き場の決めは 2 つの 1 か所が持つので、macro 側は文を渡すだけで足りる。補助が doeff_hy の module か表の module なら、実行時の関数・class の本体と型検査の投影の両方で、局所の名の漏れは起きない。class を建てる macro は `nested-reads` を真にして呼ぶ。別の module の import を足す時だけ、表(static_view.py の定数 1 行)への追加が要る — これは公開契約の拡張で、知識の漏洩ではない |
| S2 | storage | 型検査の投影の後処理(`with_module_level_imports` と、その中の木の変換)の中身を変える | projection-imports | static_cache・macro 側 | 保存した展開は POST_PROCESSING の閉包の digest で古い物を引かない — 後処理の関数と、その中で使う class の method の本体を替えると digest が変わり、保存が無効になる |

不適用の軸:

| 軸 | 理由 |
|---|---|
| hardware | Hy の macro の展開と Python の木の変換で、機体・加速器に依らない |
| distribution | 展開は 1 つの process の中で終わる。単一機体から cluster へ移っても、file ごとに同じ関数が同じ木を変換するだけで、変更の置き場は変わらない |
| concurrency | 型検査の展開は子の process(forkserver)で file ごとに独立に走るが、後処理は引数の木だけを替え、module の状態を持たない。変換器は呼びごとに作る |
| simulation | 展開は純粋(同じ source・同じ macro は同じ木)。gensym の連番は process の数えに依るが、保存の中身は既存の振り直しの規則(`with_canonical_gensyms`・`gensym_renaming`)が正準化する。今回の変更は新しい連番の依存を足していない(runtime の `_publish-imports` の gensym も同じ名の形) — .pyc の保存で振り直しが効く事は今回は測っていない |

## 強制の方法

| 守る責務 | 強制の方法と選んだ理由 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| macro が場所を判断しない・局所の名を漏らさない | macro ごとの検体を関数の中に置き、局所の名を名に依らず数える(`_SCOPE_LEAKING_NAMES` の表に頼らない — 新しい補助も捕まえる)。検体の無い macro は数え上げの検が赤 | tests/test_nested_definition_scope.py の `_DEFINITION_SAMPLES`・`test_every_definition_macro_has_a_nested_sample`・`test_function_with_only_a_definition_binds_only_its_names` | pytest(変更範囲の検・日次の全体) | 検体は macro の名で数えるので、`_scoped-definition` を通さずに `(do …)` を返しても、局所の名が漏れなければ通る。静的な import の方向の検査(import-linter 等)は、macro の展開の文字列(木)に対して使えず導入していない |
| module の状態を変えない | 検体を関数の中に置き、外の関数を繰り返し呼んで module と doeff の module の globals の差を数える | `test_definition_sample_leaves_module_state_unchanged` | 同上 | 検体の形に依る |
| 型を建てる定義が class の本体で動く | module に doeff_hy が無い header で定義を評価し、使う | `test_class_body_type_definition_runs_without_module_doeff_hy` | 同上 | 4 つの macro の検体だけ |
| `nested-reads` の誤用(defhandler が module の globals を変える) | defhandler の既存の検が module の globals の増減を比べる | tests/test_handle_scope.py の `test_class_body_handle_leaves_module_globals_alone` | 同上 | |
| 型検査の投影の import の置き場 | 投影(AST)で、関数・class の本体の import が束ねる名と、module の段の import を比べる | `test_static_projection_of_function_definition_binds_no_module_name`・`…class_body…` | 同上 | pyright は走らせていない(型検査は全体テストの時刻の中だけで走らせる決め) |
| 後処理の変更が保存を無効にする | POST_PROCESSING の閉包の digest(`value_digest`)を、後処理の本体を替える前後で比べる | doeff_hy_bytecode_guard の `value_digest`(既存の機構) | 型検査の展開の保存の照らし | S2 の検で測る |
| 品質検査 | 変更範囲の品質検査(速い段)は型を含まない | `code-quality --scope changed` | 変更範囲の検査 | conductor.hy・macros.hy・static_check.py・static_view.py は継続検査に未登録(今回の変更で変わらない) |

## 予測した変更範囲(S1・S2 の検証前)

- S1: 新しい定義の macro の module と検体 1 行。`_scoped-definition` と `with_module_level_imports` は変えない。例外 = 別の module の plain な名の import を足す時の表の 1 行・class を建てる macro が呼ぶ `nested-reads` の 1 語。
- S2: `static_check.py` の後処理の関数だけ。保存の仕組み(static_cache・macro_use)は変えない。

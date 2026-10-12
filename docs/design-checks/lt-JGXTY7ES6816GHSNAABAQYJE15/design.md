# 設計検証 — 定義の macro の展開が import を置く場所の残り 3 つ(依頼 lt-JGXTY7ES6816GHSNAABAQYJE15)

## この文書の目的

読み手(依頼元・次にこの境界を変える実装者)が、次の 3 点を、文書と証拠だけで判断できるようにする。

1. 定義の macro の展開が置く import の「置き場の決め」は、どこに在り、何が macro 側に残るか。
2. 将来の変更(新しい macro・新しい補助の import・後処理の変更)が、どの範囲に収まると主張でき、盲検の失敗例を試して何が崩れ、何を直したか。
3. 責務違反を、どの検査がどこから呼ばれて防ぐか。測っていない範囲はどこか。

## 全体の構成(import の置き場を決める 2 本の道)

```
 定義の macro(14 種)  defk deff defp defpp deftest defeffect defmcp-tool defhandler
 doeff_hy/macros.hy ほか   defsystem defpipeline defrecord defwire defenum defworkflow
        │  展開の文の並び forms を作る(場所は判断しない)
        ▼
 ┌─ 実行時の展開 ───────────────────────────────┐   ┌─ 型検査の展開(投影) ───────────────────────┐
 │ _scoped-definition(macros.hy)                │   │ 型の宣言(class・def)を外の関数に残す為、包めない │
 │  関数の本体 → すぐ呼ぶ関数で包み name だけ束ねる │   │ macro は module の段へ文を出せない              │
 │  module・class の本体 → 並べる               │   │        │ 展開の後                              │
 │  class の本体 + nested-reads → import の名を   │   │        ▼                                      │
 │    module の globals へ setdefault(定義の前)  │   │ with_module_level_imports(static_check.py)    │
 └──────────────────────────────────────────────┘   │  macro 合成の import を module の直下へ移す      │
                                                     │  判定: doeff_hy・DEFSYSTEM_IMPORTS・            │
        今回(2 段目)の修正は右の判定だけ ────────────▶│        DEFEFFECT_OUTCOMES_MODULE・別名の頭       │
                                                     │        (`_doeff_`・`_conductor_`)              │
                                                     └──────────────────────────────────────────────┘
                                                              │ POST_PROCESSING の digest が変わる → 保存した展開は無効
                                                              ▼
                                                     without_bookkeeping(重ねの畳み・読まれない import の除去)→ pyright
```

## 完了の範囲

実装完了まで。実装は 2 段に分かれる。

| 段 | 内容 | commit | 状態 |
|---|---|---|---|
| 1 | 3 つの形の修正(型検査の投影・class の本体・defworkflow) | 49a293f19・c4590550 | doeff の main に着地済み |
| 2 | 盲検 A・B の失敗例を受けた投影の後処理の修正と、投影の検の作り直し | 9bb308779(branch wt/design-check-scoped-def) | 着地の列に登記する(着地の結果は依頼元への連絡に書く) |

## 事前の主張(盲検の前に保存した版)

`design-pre-blind.md`(sha256 d3ae9c99b9d00a0bab513aeb087cd1bf57d96ad2ba1b226eaba3135a2e1106d1)。盲検へ渡した入力は `blind-input.md`(sha256 c60c35de9f5214a7fe0d27b5431e8d43da9252ec35b6edf81fe4bd7175e3b0bf)。盲検の返答を受けて書き換えた結論で、事前の主張を上書きしない。差は次の節に残す。

## 責務(盲検後の訂正を含む最終版)

| id | 責務 | 持つ知識 | 隠す知識 | 口 | effect |
|---|---|---|---|---|---|
| scoped-definition | 定義の macro の展開の文の並びを、書かれた所と、実行時の展開か型検査のための展開かに合わせて組む | 場所の判断・包むか・module の globals に置くか | 場所ごとの import の置き方 | `_scoped-definition compiler name forms [module-forms] [nested-reads]` | `nested-reads` を真にした class の本体の展開でだけ、module の globals へ `setdefault`(補助の `_install-guard-globals` は別の道で既存) |
| definition-macros | 定義の中身(契約・型・展開の文)を作る | 契約の鍵・欄・展開の文・**型検査の展開で出す文の形**(`_helper-imports` が空・record.hy の static-view の分岐) | 場所の判断(文の並びを `_scoped-definition` に渡すだけ) | 各 macro | なし |
| projection-imports | 型検査の投影の木で、macro 合成の import を関数・class の本体から module の段へ集める。module の頭の補助の import(`STATIC_HELPER_IMPORTS` → `with_static_helpers`)は別の既存の置き場 | macro 合成の import の見分け方: doeff_hy の module・表の module(`DEFSYSTEM_IMPORTS`・`DEFEFFECT_OUTCOMES_MODULE`)・別名の頭(`SYNTHESIZED_ALIAS_PREFIXES` = `_doeff_`・`_conductor_`) | 木の歩き方 | `with_module_level_imports(tree) -> tree` | なし(受けた木をその場で替える変換) |
| scope-tests | 上の 3 つの境界を機械で守る | 検体の表・局所の名の数え方・symtable で読む名 | — | tests/test_nested_definition_scope.py | なし |

不変条件(盲検後の訂正を含む):

- 関数の中の定義(実行時)が外の関数に残す局所の名は `name` だけ。
- module と class の本体の定義は包まない(`__qualname__` と引数なしの `super()` を保つ)。
- `_scoped-definition` が module の globals へ書くのは、`nested-reads` を真にした定義が class の本体に書かれた時だけ。
- 型検査の投影で、関数・class の本体の中に macro 合成の import を残さない。**macro 合成の見分けは綴りで行うので、書き手が同じ綴りで書いた import も移る**(同じ module の名を module の段へ移すだけで、型の意味は変わらない)。

## 盲検 A・B の結果と、事前の主張との差

起動の記録は `blind-meta.md`。返答は未加工で `blind-A-response.md`・`blind-B-response.md`。

| # | 指摘(A / B) | 試した結果 | 成立 | 対応 |
|---|---|---|---|---|
| 1 | class の本体の投影で、defwire・defeffect・頭の辞書つき defrecord の `from typing import ClassVar as _doeff_ClassVar` などが外の class に残り、入れ子の型の本体が読めない(A・B) | 再現した: `evidence/N-A-before-fix.log` — 入れ子の `Span/__annotate__` などが `_doeff_ClassVar`・`_doeff_cast` を global として読み、module は束ねていない | 成立 | 判定に別名の頭を足し、macro 合成の別名の import も module の段へ移す(9bb308779) |
| 2 | 列挙の検は根の名 3 つしか見ないので、B の差分(defrecord の `:tags` を ClassVar で見せる)を当てても通る(B) | 再現した: `evidence/N-B-before-fix.log` — 差分の下で列挙の検 117 件が通り、同じ差分の投影は R1 を破る(`['_doeff_ClassVar'] ['_doeff_ClassVar']`) | 成立 | 投影の検を作り直した(全 macro の検体・scope に import を 1 つも残さない・symtable で読む名が module の段で束ねられている事) |
| 3 | S1 の「表 1 行足せば済む」は小さすぎる。表は 1 つでなく、判定が定数を名指しで並べ、別名の from-import は表で書けない(A・B) | コードで確認: 判定は `ast.Import` の別名なしの形だけ表を読む | 成立(主張の訂正) | 別名の頭の規則で、約束どおりの別名の補助は macro 側だけで済む。別の module の別名なしの import を足す時だけ、定数と判定の並びの 2 か所(どちらも projection-imports の中) |
| 4 | 判定は綴りで見分けるので、書き手が関数の中に書いた `import doeff_core_effects.outcomes` なども移る(A・B) | コードで確認(判定は誰が書いたかを見ない) | 成立(不変条件の文言が過大) | 不変条件を訂正した(上の表)。コードは変えない |
| 5 | definition-macros の「隠す知識: なし」は過大。型検査の展開で import を出し分ける分岐を macro が持つ(A) | `_helper-imports`・record.hy の static-view の分岐で確認 | 成立(文書の誤り) | 責務の表を訂正した |
| 6 | `STATIC_HELPER_IMPORTS` が責務の表のどこにも無い(A) | 確認 | 成立(文書の欠け) | projection-imports の責務に別の既存の置き場として書いた |
| 7 | 「module の globals を変えるのは nested-reads の class の本体だけ」は不正確。関数の中の defk は `_install-guard-globals` が初回の呼びで補助を置く(B) | コードで確認 | 成立(不変条件の文言が過大) | 不変条件を「`_scoped-definition` が書くのは」に絞った |
| 8 | 別名の import が module の段に積まれて宣言が 64 を超えるかもしれない(B・推測) | 測っていない | 未確認 | 重ねの畳みが同じ判定を使うので、同じ文は 1 つに畳まれる(副産物)。上限は測っていない |
| 9 | 判定を広げると、同じ別名を束ねる 2 つの文(`ClassVar` だけと `ClassVar, cast`)が module の段に並び、strict が赤にするかもしれない(A・推測) | 測っていない(pyright は走らせない決め) | 未確認 | module の段の既存の形と同じ。測っていない |
| 10 | `DEFSYSTEM_IMPORTS` に行を足すと defsystem の展開まで変わる(A) | コードで確認(定数が展開の綴りと兼用) | 成立 | 新しい module は別の定数を足し、兼用しない |

## 予測した変更範囲と実際

| シナリオ | 事前の予測 | 実際(盲検の前の版) | 実際(修正後) | 差の理由 |
|---|---|---|---|---|
| S1 effects: 新しい定義の macro、または新しい補助の import | その macro の module と検体 1 行。scoped-definition・projection-imports は変えない | 別名の from-import を足す変更では projection-imports の判定まで変える必要が在った(指摘 1・2・3) | 約束の頭(`_doeff_`・`_conductor_`)の別名なら macro の module と検体 1 行だけ(B の差分で確認: 投影は違反せず、列挙の検 137 件が通る)。別の module の別名なしの import は定数と判定の並び(projection-imports の 2 か所) | 投影の後処理が macro の import の綴りの表を持ち、macro の選んだ綴りが置き場の正しさを決めていた(知識の漏洩)。別名の頭の約束で、綴りの知識を表から約束へ移した |
| S2 storage: 後処理の中身を変える | projection-imports だけ。保存した展開は digest で無効になる | 同じ | 同じ(後処理の変換器の method・判定が呼ぶ関数・判定が読む定数のどれを替えても digest が変わる) | 予測どおり |

## 強制の方法(実装済み・実行経路つき)

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| macro が場所を判断せず、局所の名を漏らさない | 全 macro の検体を関数の中に置き、局所の名を名に依らず数える。検体の無い macro は数え上げの検が赤 | tests/test_nested_definition_scope.py の `_DEFINITION_SAMPLES`・`test_every_definition_macro_has_a_nested_sample`・`test_function_with_only_a_definition_binds_only_its_names` | pytest(変更範囲の検・日次の全体) | `_scoped-definition` を通さず `(do …)` を返しても、局所の名が漏れなければ通る |
| 型検査の投影に import を残さない | 全 macro の検体(14 種)を関数の本体と class の本体に置いた投影で、(1) scope に import を 1 つも残さない(名に依らない)、(2) 入れ子の scope が module の段の名として読む macro の別名・根の module の名は module の段で束ねられている(symtable) | `test_static_projection_of_nested_definition_leaves_no_import_in_its_scope`・`test_static_projection_nested_scopes_read_macro_names_bound_at_module_level` | 同上 | pyright は走らせていない(型検査は全体テストの時刻の中だけの決め)。投影の木と symtable で測った。macro 名を読む検は `_doeff_`・`_conductor_`・根の 3 module の名だけ — 別の頭の別名は (1) が捕まえる |
| class の本体で型が建つ | module に `doeff_hy` が無い header で定義を評価して使う | `test_class_body_type_definition_runs_without_module_doeff_hy` | 同上 | 4 つの macro の検体だけ |
| `nested-reads` の誤用 | defhandler が module の globals を変えない検 | tests/test_handle_scope.py の `test_class_body_handle_leaves_module_globals_alone` | 同上 | |
| 後処理の変更が保存を無効にする | 既存の POST_PROCESSING の閉包の digest | doeff_hy_bytecode_guard の `value_digest` | 型検査の展開の保存の照らし | S2 の実行記録で測った |
| 品質検査(速い段) | 変更範囲の module 契約・依存・規則 | `code-quality --scope changed` | 変更範囲の検査 | 型を含まない。static_check.py・static_view.py・conductor.hy・macros.hy は継続検査に未登録(今回の変更で変えていない) |

## 検証の記録(evidence/)

| 記録 | 内容 |
|---|---|
| N-A-before-fix.log | 盲検 A の失敗例(修正前の版): class の本体の 3 種で別名の import が残り、入れ子が global で読む |
| N-B-before-fix.log | 盲検 B の差分を当てた修正前の版: 列挙の検 117 件が通り、投影は違反する |
| P-S1-after-fix.log | 修正後の版で列挙の検 137 件が通る |
| P-S1-all-macros-no-import-left.log | 全 macro の検体(14 種)の投影で、関数の本体・class の本体に import が残らない |
| P-S1-regression.log | 投影を作る側の既存の検 124 件が通る |
| P-quality-fast.log | 変更範囲の品質検査(速い段)が合格(型を含まない) |
| N-S1-mutations.log | 変異 5 つ(nested-reads の分岐・全 macro の nested-reads・後処理の呼び出し・別名の頭の判定・約束の頭を守らない別名)が、それぞれ意図した検で赤 |
| N-AB-after-fix.log | 盲検 A・B の失敗例が、修正後は成立しない |
| S2-digest.log | 変えずに測った digest は同じ・後処理の 3 種の変更で変わる |
| controls.py ほか | 上の記録を作った script(再実行用) |

## 測っていない範囲

- pyright の実機。赤の有無は、投影の木と symtable(Python の名の引きの規則)で代えた。
- 別名の import が module の段に並ぶ数の上限(64)と、同じ別名を束ねる文の重なりへの strict の反応。
- .pyc の保存での gensym の振り直し(今回、新しい連番の依存は足していない)。
- 盲検の実モデル(要求値と、起動手段が受けた値だけを `blind-meta.md` に記録した)。
- 別の card(ki-c9d9a7994630)へ分けた 2 件のうち、class の本体の defrecord・defeffect・defwire の method が型の名を裸で読む問題(setattr の NameError)は、この修正の範囲外で未着手。もう 1 件の関数の中の defhandler の投影の import は、この修正の別名の判定で module の段へ移る事を全 macro の検体の検で確かめた(card の更新は依頼元の仕事)。
- 型検査の継続検査への登録(static_check.py・static_view.py・conductor.hy・macros.hy)。明示の操作として別に行う。

## 記録の訂正

- 修正の commit 9bb308779 の message は検体を「13 種」と書いたが、`_DEFINITION_SAMPLES` の数えは 14 種(defk・deff・defp・defpp・deftest・defeffect・defmcp-tool・defhandler・defsystem・defpipeline・defrecord・defwire・defenum・defworkflow)。コードと検は 14 種全部を通す。push 済みの message は書き換えず、ここで訂正する。
- `evidence/` の log のうち、変異・再現の記録は実行した時点の出力そのまま。`P-S1-all-macros-no-import-left.log` の頭の注釈だけ 13 → 14 に直した(出力の本体は 14 行 × 2 = 28 行で、最初から 14 種を並べていた)。

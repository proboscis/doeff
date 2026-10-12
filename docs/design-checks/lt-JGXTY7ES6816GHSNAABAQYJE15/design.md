# 設計検証 — 定義の macro の展開が import を置く場所の残り 3 つ(依頼 lt-JGXTY7ES6816GHSNAABAQYJE15)

## この文書の目的

読み手(依頼元・次にこの境界を変える実装者)が、次の 3 点を、文書と証拠だけで判断できるようにする。

1. 定義の macro の展開が置く import の「置き場の決め」は、どこに在り、何が macro 側に残るか。
2. 将来の変更(新しい macro・新しい補助の import・後処理の変更)が、どの範囲に収まると主張でき、盲検の失敗例を試して何が崩れ、何が直ったか。
3. 責務違反を、どの検査がどこから呼ばれて防ぐか。測っていない範囲はどこか。

## 結論(先に)

| 問い | 答え |
|---|---|
| 依頼の 3 つの形は直ったか | 直った(49a293f19・c4590550 が main に着地済み・依頼元が受け入れ済み) |
| 盲検 A・B は何を見つけたか | 着地した版の穴 — class の本体の型検査の投影で、macro が合成する別名の import(`_doeff_ClassVar`・`_doeff_cast`)が外の class に残り、入れ子の型の本体が名を引けない。それを通す検の弱さ(根の名 3 つしか見ない) |
| 穴はどう直ったか | **別の担い手が並行して同じ穴を見つけ、先に main へ入れた**(8a759a421・4e46d0df1)。方式は「展開の名の表 `expansion_names._HOMES` を閉じて綴りの定義元を 1 つにし、後処理の判定をその表に結ぶ」 |
| 私の修正は | **取り下げた**(別名の頭 `_doeff_`・`_conductor_` で見分ける判定。着地の列 L2240・L2242 が、この 2 つの commit との rebase 衝突で失敗)。同じ穴に 2 つの機構を置かず、main の方式を受けた |
| この branch が足す物 | 検の強化だけ(source は変えない)と、設計検証の記録。検は main の方式の上でも通り、盲検 B の「別の綴りの補助」で赤になる |
| 残る危険 | 展開が scope に残す import(defeffect の `_doeff_dataclass`・`_doeff_effect_base`、defworkflow の `_conductor_*`)は、今は入れ子の scope が読まないので無害。将来それを読む macro が書かれると、表の外の綴りでは module の段へ移らない(検は既知の名の表で捕まえる)。pyright は走らせていない |

## 全体の構成(import の置き場を決める 2 本の道)

```
 定義の macro(14 種)  defk deff defp defpp deftest defeffect defmcp-tool defhandler
 doeff_hy/macros.hy ほか   defsystem defpipeline defrecord defwire defenum defworkflow
        │  展開の文の並び forms を作る(場所は判断しない)
        │  展開が読む補助の名(ClassVar・cast・Pass ほか)の綴りと置き場 ← expansion_names._HOMES(閉じた表・綴りの定義元)
        ▼
 ┌─ 実行時の展開 ───────────────────────────────┐   ┌─ 型検査の展開(投影) ───────────────────────────────┐
 │ _scoped-definition(macros.hy)                │   │ 型の宣言(class・def)を外の関数に残す為、包めない     │
 │  関数の本体 → すぐ呼ぶ関数で包み name だけ束ねる │   │ macro は module の段へ文を出せない                   │
 │  module・class の本体 → 並べる               │   │        │ 展開の後                                     │
 │  class の本体 + nested-reads → import の名を   │   │        ▼                                            │
 │    module の globals へ setdefault(定義の前)  │   │ with_module_level_imports(static_check.py)          │
 └──────────────────────────────────────────────┘   │  macro 合成の import を module の直下へ移す            │
                                                     │  判定 _synthesized_import: doeff_hy の module・        │
   [盲検が突いた所] ───────────────────────────────▶│   DEFSYSTEM_IMPORTS・DEFEFFECT_OUTCOMES_MODULE・       │
   別名の import(typing の ClassVar・cast)が        │   expansion_names.is_alias_import(閉じた表の綴り)    │
   class の本体に残っていた                          └──────────────────────────────────────────────────────┘
                                                              │ POST_PROCESSING の digest が変わる → 保存した展開は無効
                                                              ▼
                                                     without_bookkeeping(重ねの畳み・読まれない import の除去)→ pyright
```

## 完了の範囲

実装完了まで。実装は 3 つに分かれる。

| 段 | 内容 | commit | 状態 |
|---|---|---|---|
| 1 | 3 つの形の修正(型検査の投影・class の本体・defworkflow) | 49a293f19・c4590550 | doeff の main に着地済み |
| 2a | 盲検が見つけた穴の修正(typing の別名・defhandler の node の別名) | 8a759a421・4e46d0df1(別の担い手・依頼 lt-MPT2W9D40AMFNSEXSY5JPV780N) | main に着地済み |
| 2b | 検の強化(全定義の macro の検体・名に依らない投影の検)と設計検証の記録 | この branch(wt/design-check-scoped-def-r4) | 着地の列に登記する |

## 事前の主張(盲検の前に保存した版)

`design-pre-blind.md`(sha256 d3ae9c99b9d00a0bab513aeb087cd1bf57d96ad2ba1b226eaba3135a2e1106d1)。盲検へ渡した入力は `blind-input.md`(sha256 c60c35de9f5214a7fe0d27b5431e8d43da9252ec35b6edf81fe4bd7175e3b0bf)。盲検の返答を受けて書き換えた結論で、事前の主張を上書きしない。差は次の節に残す。盲検は 2a より前の版(作業木 b52770e03 = main の 49a293f19・c4590550 と中身が同じ)を読んだ。

## 責務(盲検後の訂正と、main の方式を反映した最終版)

| id | 責務 | 持つ知識 | 隠す知識 | 口 | effect |
|---|---|---|---|---|---|
| scoped-definition | 定義の macro の展開の文の並びを、書かれた所と、実行時の展開か型検査のための展開かに合わせて組む | 場所の判断・包むか・module の globals に置くか | 場所ごとの import の置き方 | `_scoped-definition compiler name forms [module-forms] [nested-reads]` | `nested-reads` を真にした class の本体の展開でだけ、module の globals へ `setdefault`(補助の `_install-guard-globals` は別の道で既存) |
| definition-macros | 定義の中身(契約・型・展開の文)を作る | 契約の鍵・欄・展開の文・型検査の展開で出す文の形(`_helper-imports` が空・record.hy の static-view の分岐)・別名を**読む**所の綴り(`_doeff-ClassVar`・`_doeff-cast` — 表の頭と同じ約束で、手で書く) | 場所の判断(文の並びを `_scoped-definition` に渡すだけ)・別名の import 文の組み立て(typing の別名は表の `import-form`) | 各 macro | なし |
| expansion-names | 展開が読む名(typing の ClassVar・cast、doeff の node ほか)の綴りと置き場の唯一の定義元 | 閉じた表 `_HOMES`・別名の頭 `PREFIX`(`_doeff_`)・`alias`・`import_form`・`import_source` | — | `home`(表に無い名は KeyError)・`is_alias_import(module, name, asname)` | なし |
| projection-imports | 型検査の投影の木で、macro 合成の import を関数・class の本体から module の段へ集める。module の頭の補助の import(`STATIC_HELPER_IMPORTS` → `with_static_helpers`)は別の既存の置き場 | macro 合成の import の見分け方: doeff_hy の module・`DEFSYSTEM_IMPORTS`・`DEFEFFECT_OUTCOMES_MODULE`・`expansion_names.is_alias_import` | 木の歩き方 | `with_module_level_imports(tree) -> tree` | なし(受けた木をその場で替える変換) |
| scope-tests | 上の境界を機械で守る | 検体の表・scope に残してよい import の表・symtable で読む名 | — | tests/test_nested_definition_scope.py | なし |

不変条件(盲検後の訂正を含む):

- 関数の中の定義(実行時)が外の関数に残す局所の名は `name` だけ。
- module と class の本体の定義は包まない(`__qualname__` と引数なしの `super()` を保つ)。
- `_scoped-definition` が module の globals へ書くのは、`nested-reads` を真にした定義が class の本体に書かれた時だけ。
- 型検査の投影で、入れ子の scope が読む macro 合成の名は、module の段で束ねられている。**見分けは綴りで行う**ので、書き手が表と同じ綴りで書いた import も移る(同じ module の名を module の段へ移すだけで、型の意味は変わらない)。
- 展開が scope に残す import は `_IMPORTS_LEFT_IN_SCOPE` の名だけ(新しい名が残れば検が赤)。

## 盲検 A・B の結果と、事前の主張との差

起動の記録は `blind-meta.md`。返答は未加工で `blind-A-response.md`・`blind-B-response.md`。

| # | 指摘(A / B) | 試した結果 | 成立 | 対応 |
|---|---|---|---|---|
| 1 | class の本体の投影で、defwire・defeffect・頭の辞書つき defrecord の `from typing import ClassVar as _doeff_ClassVar` などが外の class に残り、入れ子の型の本体が読めない(A・B) | 再現した: `evidence/N-A-before-fix.log` — 入れ子の `Span/__annotate__` などが `_doeff_ClassVar`・`_doeff_cast` を global として読み、module は束ねていない | 成立 | main の 4e46d0df1 が直した(typing の名を表に足し、3 か所の import を表から組む)。最終の版で再実行: `evidence/N-A-final.log` — 同じ名が module の段で束ねられ、入れ子の読みは解ける |
| 2 | 列挙の検は根の名 3 つしか見ないので、B の差分(defrecord の `:tags` を ClassVar で見せる)を当てても通る(B) | 再現した: `evidence/N-B-before-fix.log` — 差分の下で列挙の検 117 件が通り、同じ差分の投影は R1 を破る | 成立 | この branch で検を作り直した(全 macro の検体・scope に残す import の表・symtable で読む名が module の段で束ねられている事)。変異 M5(表の外の綴りの別名)が赤 |
| 3 | S1 の「表 1 行足せば済む」は小さすぎる。判定が定数を名指しで並べ、別名の from-import は表で書けない(A・B) | コードで確認 | 成立(主張の訂正) | main の方式で、typing の別名は expansion_names の表 1 行と macro 側の `import-form` で済み、後処理の判定は変わらない(表が 1 つになった)。別の module の別名なしの import(`DEFSYSTEM_IMPORTS` の形)は、今も定数と判定の 2 か所 |
| 4 | 判定は綴りで見分けるので、書き手が関数の中に書いた同じ綴りの import も移る(A・B) | コードで確認 | 成立(不変条件の文言が過大) | 不変条件を訂正した。main の判定は閉じた表の綴りだけなので範囲は狭い |
| 5 | definition-macros の「隠す知識: なし」は過大。型検査の展開で import を出し分ける分岐を macro が持つ(A) | `_helper-imports`・record.hy の static-view の分岐で確認 | 成立(文書の誤り) | 責務の表を訂正した |
| 6 | `STATIC_HELPER_IMPORTS` が責務の表のどこにも無い(A) | 確認 | 成立(文書の欠け) | projection-imports の責務に別の既存の置き場として書いた |
| 7 | 「module の globals を変えるのは nested-reads の class の本体だけ」は不正確。関数の中の defk は `_install-guard-globals` が補助を置く(B) | コードで確認 | 成立(不変条件の文言が過大) | 不変条件を「`_scoped-definition` が書くのは」に絞った |
| 8 | 別名の import が module の段に積まれて宣言が 64 を超えるかもしれない(B・推測) | 測っていない | 未確認 | 重ねの畳みは同じ判定を使うので、同じ文は 1 つに畳まれる。上限は測っていない |
| 9 | 同じ別名を束ねる 2 つの文(`ClassVar` だけと `ClassVar, cast`)が module の段に並び、strict が赤にするかもしれない(A・推測) | 測っていない(pyright は走らせない決め) | 未確認 | 測っていない |
| 10 | `DEFSYSTEM_IMPORTS` に行を足すと defsystem の展開まで変わる(A) | コードで確認(定数が展開の綴りと兼用) | 成立 | 新しい module は別の定数を足し、兼用しない(今回は変えていない) |

## 私の修正を取り下げた理由

盲検 A・B を受けて、判定を「macro が付ける別名の頭 `_doeff_`・`_conductor_` で束ねた import」にする修正と、それに合わせた検を作り、直す前の赤と直した後の緑・変異 5 つ・digest を測った(branch `wt/design-check-scoped-def-r2`・`-r3`。着地は L2240・L2242 とも別の担い手の commit との rebase 衝突で失敗)。取り下げた理由は 3 つ。

| 理由 | 内容 |
|---|---|
| 同じ穴に 2 つの機構 | main の 4e46d0df1 が、同じ穴を表(`expansion_names`)の閉じた綴りで直した。頭で見分ける判定を足すと、同じ import を 2 つの規則が名指す |
| 綴りの定義元 | 表は、展開が読む名と置き場を 1 か所に置き、表に無い名は `home` が KeyError で断る。頭の判定は約束(綴りの慣習)だけで、約束を守らない別名は素通りする |
| 書き手の import の誤分類 | 4e46d0df1 の本文は、書き手が `from doeff import do as _doeff_do` と書く形を、頭の判定が展開の合成と誤る例に挙げる。私は、その形を書き手に書かせる記述(for/do の docstring)を macros.hy で見つけられなかった(**未確認**)が、頭だけで見分ける限り起こり得る誤分類で、「書き手の import はこの頭を使わない」という私の前提は検証していなかった |

実測の差: 表の方式では、`defeffect` の `_doeff_dataclass`・`_doeff_effect_base` と `defworkflow` の `_conductor_agent_bang`・`_conductor_defphase`・`_conductor_defworkflow` が scope に残る(頭の判定は、これも module の段へ移していた)。どちらも入れ子の scope は読まないので、pyright の赤にはならない(symtable で確かめた)。この差を、検の表 `_IMPORTS_LEFT_IN_SCOPE` に残した。

## 予測した変更範囲と実際

| シナリオ | 事前の予測 | 実際(盲検が読んだ版) | 実際(最終の版) | 差の理由 |
|---|---|---|---|---|
| S1 effects: 新しい定義の macro、または新しい補助の import | その macro の module と検体 1 行。scoped-definition・projection-imports は変えない | 別名の from-import を足す変更では、projection-imports の判定まで変える必要が在った(指摘 1・2・3) | typing の別名: expansion_names の表 1 行と macro の `import-form`(後処理の判定は変えない — 4e46d0df1 の実績)。表の外の綴りで束ねる補助: 検 M5 が赤にする。別の module の別名なしの import は定数と判定の 2 か所 | 後処理が macro の import の綴りの知識を持っていた(知識の漏洩)。綴りを expansion_names の表へ集めて解いた |
| S2 storage: 後処理の中身を変える | projection-imports だけ。保存した展開は digest で無効になる | 同じ | 後処理の変換器の method・判定の関数・判定が呼ぶ別の module の関数・判定が読む別の module の定数(表)のどれを替えても digest が変わる(`S2-final-digest.log`) | 予測どおり。閉包が expansion_names の表まで届く事を実測で確かめた |

## 強制の方法(実装済み・実行経路つき)

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| macro が場所を判断せず、局所の名を漏らさない | 全 macro の検体を関数の中に置き、局所の名を名に依らず数える。検体の無い macro は数え上げの検が赤 | `_DEFINITION_SAMPLES`・`test_every_definition_macro_has_a_nested_sample`・`test_function_with_only_a_definition_binds_only_its_names` | pytest(変更範囲の検・日次の全体) | `_scoped-definition` を通さず `(do …)` を返しても、局所の名が漏れなければ通る |
| 型検査の投影で、scope に残る import が既知の物だけ | 全 macro の検体(14 種)を関数の本体と class の本体に置いた投影で、scope に残る import は `_IMPORTS_LEFT_IN_SCOPE` の名だけ。別の名が残れば赤 | `test_static_projection_of_nested_definition_leaves_only_known_imports_in_its_scope` | 同上 | 表に足す前に、入れ子の scope が読まない事を人が確かめる |
| 入れ子の scope が読む macro の名は module の段で束ねられている | 同じ検体の投影を symtable で読み、`_doeff_`・`_conductor_` の別名と根の module の名が module の段にある事を検める | `test_static_projection_nested_scopes_read_macro_names_bound_at_module_level` | 同上 | 別の頭の別名は上の検が捕まえる。pyright は走らせていない |
| class の本体の typing の別名・defhandler の node の別名(main 側の検) | 同上(残した) | `test_static_projection_of_class_body_type_reads_module_level_typing_aliases`・`..._nested_defhandler_reads_module_level_nodes` | 同上 | 検体を私の表に付け替えた |
| class の本体で型が建つ | module に `doeff_hy` が無い header で定義を評価して使う | `test_class_body_type_definition_runs_without_module_doeff_hy` | 同上 | 4 つの macro の検体だけ |
| `nested-reads` の誤用 | defhandler が module の globals を変えない検 | tests/test_handle_scope.py の `test_class_body_handle_leaves_module_globals_alone` | 同上 | |
| 後処理の変更が保存を無効にする | 既存の POST_PROCESSING の閉包の digest | doeff_hy_bytecode_guard の `value_digest` | 型検査の展開の保存の照らし | S2 の実行記録で測った |
| 品質検査(速い段) | 変更範囲の module 契約・依存・規則 | `code-quality --scope changed` | 変更範囲の検査 | 型を含まない |

## 検証の記録(evidence/)

| 記録 | 内容 | 対象の版 |
|---|---|---|
| N-A-before-fix.log | 盲検 A の失敗例(修正前): class の本体の 3 種で別名の import が残り、入れ子が global で読む | 盲検が読んだ版 b52770e03 |
| N-B-before-fix.log | 盲検 B の差分を当てた修正前: 列挙の検 117 件が通り、投影は違反する | 同上 |
| N-A-final.log | 盲検 A の失敗例の再実行: module の段で束ねられ、入れ子の読みは解ける。scope に残る `_doeff_dataclass`・`_doeff_effect_base` は入れ子が読まない | 最終の版 |
| P-final-enumeration.log | 列挙の検 147 件が通る(main 側の検を含む) | 最終の版 |
| P-final-scope-imports.log | 全 macro の検体(14 種 × 関数・class = 28 行)のうち、scope に import が残るのは defeffect と defworkflow の 4 行 | 最終の版 |
| N-final-mutations.log | 変異 5 つ(M1 nested-reads の分岐・M2 全 macro の nested-reads・M3 後処理の呼び出し・M4 表の判定・M5 表の外の綴りの別名)が、それぞれ意図した検で赤 | 最終の版 |
| S2-final-digest.log | 変えずに測った digest は同じ・後処理の 4 種の変更で変わる | 最終の版 |
| P-final-quality-fast.log | 変更範囲の品質検査(速い段)が合格(型を含まない) | 最終の版 |
| controls_final.py ほか | 上の記録を作った script(再実行用。`mutations M1`・`s2 2` のように 1 件ずつも走らせられる) | — |

取り下げた修正の測定(別名の頭の判定: 列挙の検 137・144 件・変異 5 つ・digest)は、origin の branch `wt/design-check-scoped-def-r2`・`-r3` の `docs/design-checks/lt-JGXTY7ES6816GHSNAABAQYJE15/evidence/` に残る(着地していない)。

## 測っていない範囲

- pyright の実機。赤の有無は、投影の木と symtable(Python の名の引きの規則)で代えた。
- 別名の import が module の段に並ぶ数の上限(64)と、同じ別名を束ねる文の重なりへの strict の反応。
- .pyc の保存での gensym の振り直し(今回、新しい連番の依存は足していない)。
- 盲検の実モデル(要求値と、起動手段が受けた値だけを `blind-meta.md` に記録した)。
- 書き手が `from doeff import do as _doeff_do` を書く形が実在するか(4e46d0df1 の本文が挙げる例)。私は確かめていない。
- 回帰の検(static_check 系・conductor・defwire・defrecord の check・static_cache)は、この branch が source を変えないので撮り直していない(main 側の着地の検が測る)。
- class の本体の引数の無い defhandler の `reportSelfClsParameterName` の warning(8a759a421 が範囲外として挙げた)は未着手。
- 型検査の継続検査への登録(static_check.py・static_view.py・expansion_names.py ほか)。明示の操作として別に行う。
- 別の card(ki-c9d9a7994630)の 2 件(class の本体の型の method の NameError・入れ子の defhandler の投影の import)は、8a759a421 が直した。card の更新は依頼元の仕事。

## 記録の訂正

- 取り下げた修正の commit の message は、検体を「13 種」と書いたが、`_DEFINITION_SAMPLES` の数えは 14 種(defk・deff・defp・defpp・deftest・defeffect・defmcp-tool・defhandler・defsystem・defpipeline・defrecord・defwire・defenum・defworkflow)。この branch の検は 14 種全部を通す。
- 初版の設計書は「着地の列に登記する(別名の頭の判定)」と書いていたが、その修正は取り下げた(上の節)。事前の主張(`design-pre-blind.md`)は書き換えていない。

# 設計検証 lt-MPT2W9D40AMFNSEXSY5JPV780N — class の本体の型の method の型の読み方と、投影の node の別名の import の置き場

- 完了の範囲: **実装完了**(doeff main 8a759a421ca32d6746d6d3f7fd3b90558b1962ec に着地・L2237)。基準版 787554c3692cfe9bed8e1299442ab24afdd81523。
- この本文は盲検 A・B の前に保存した(主張と事前予測)。盲検の後の結果は「盲検の後」の節に足し、上の主張は書き換えない。

## 要件(固定)

1. class の本体に直に書いた defrecord・defwire・defeffect の型で、凍結の `__setattr__` / `__delattr__` は FrozenInstanceError で断る(NameError で落ちない)。default-factory の欄・init=False で既定値を持つ欄の型は作れる。
2. 型検査の投影(doeff-hy-check が pyright に読ませる Python)で、関数・class の本体の中の defhandler が読む `_doeff_Pass`・`_doeff_WithHandler` の import は module の段に置く(class の本体では pyright が `is not defined` を出していた)。
3. module の段・関数の中の定義の実行時の振る舞いは変えない。

## 責務(module)

| id | 実体 | 責務 | 持つ知識 | 隠す知識 |
|---|---|---|---|---|
| record-class-form | packages/doeff-hy/src/doeff_hy/record.hy の class-form | defrecord・defwire の型の関数(__init__・__repr__・__eq__・__hash__・凍結の __setattr__/__delattr__)を展開の時に書く | 書く関数が定義した型を読む方法(`__class__` の閉包と引数なしの super) | 型がどこ(module・関数・class の本体)に書かれたか |
| defeffect-form | packages/doeff-hy/src/doeff_hy/declarations.hy の defeffect-form | defeffect の effect 型の関数を書く | 同上 | 同上 |
| scoped-definition | packages/doeff-hy/src/doeff_hy/macros.hy の _scoped-definition | 定義の展開の文の並びを、書かれた所に合わせて組む(包む・包まない・import を module の globals へ置く) | 書かれた場所の判定 | 定義が書く関数の中身 |
| expansion-names | packages/doeff-hy/src/doeff_hy/expansion_names.py | handle・defhandler の展開が読む doeff の node の `_doeff_` の別名の綴り・置き場・import の唯一の定義元。is_alias_import で、その綴りの import かを答える | 名 → 置き場・別名 | なし(純粋な表) |
| static-check-postprocess | packages/doeff-hy/src/doeff_hy/static_check.py の _synthesized_import・with_module_level_imports | 投影の後処理で、macro が合成した import を関数・class の本体から module の段へ移す | どの import が macro の合成か(doeff_hy の module・defsystem の module・defeffect の outcomes・expansion-names の別名) | 別名の綴りの具体(expansion-names に問う) |
| handle-expansion | packages/doeff-hy/src/doeff_hy/handle.hy | defhandler・handle の展開(節の関数・本文に被せる関数) | 使う node の名(Pass・WithHandler) | 綴り(expansion-names から引く) |

effects: どれも macro の展開の時の純粋な計算(副作用なし — 展開の時に file・network に触らない)。static_check の投影は木をその場で替えるが、木はその展開だけの物。lifetime: 状態を持たない(解放の所有者は不要)。

## 変更シナリオと事前の主張

| id | 軸 | 適用 | 変更要求 | 主張(事前) | 変わる module(予測) | 変わらない module・契約 |
|---|---|---|---|---|---|---|
| S1 | effects | applicable | handler の展開が新しい VM の node(例 `Delegate`)を別名 `_doeff_Delegate` で読むようになる | 綴りの表 expansion-names に名を足し handle-expansion が使うだけで、投影の後処理は変えずにその import を module の段へ移す(is_alias_import が表から答える) | expansion-names・handle-expansion | static-check-postprocess・scoped-definition |
| S2 | effects | applicable | defrecord / defeffect の展開が新しい関数(例 `__replace__`・`__copy__`)を書き、その関数が定義した型を読む | 書く関数は `__class__` の閉包で型を読む形に揃っているので、新しい関数も同じ形で書けば class の本体の型で動く。場所(class の本体)の扱いは scoped-definition に足さない | record-class-form(と defeffect-form) | scoped-definition・static-check-postprocess |
| S3 | hardware | excluded | — | macro の展開は機体に依らない | — | — |
| S4 | storage | excluded | — | 展開は状態を保存しない(.pyc の無効化は doeff_hy_bytecode_guard の持ち物で、この変更は触らない) | — | — |
| S5 | distribution | excluded | — | 展開は 1 process の中の計算。投影の並列(ProcessPoolExecutor)は file ごとに独立で、後処理は純粋 | — | — |
| S6 | concurrency | excluded | — | 同上(共有の可変状態なし) | — | — |
| S7 | simulation | excluded | — | 決定的な純粋計算で、模擬の時刻・外の世界を読まない | — | — |

前提: 書き手は `_doeff_` の頭の名を自分で import しない(expansion_names.py の頭注の決め)。seal_record / seal_effect は class を作り直さない(作り直すと `__class__` の閉包が古い class を指す)。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 書く関数は定義した型を名で読まない | 契約テスト(class の本体の型で凍結・default-factory・init=False・継いだ型を実際に動かす) | tests/test_nested_definition_scope.py::test_class_body_type_methods_read_their_own_class | 着地の後の日次の全体テスト・変更時の focused 実行 | 新しく足す関数(S2)は、その関数を class の本体で動かす検を足さない限り検出しない。静的な規則(「書く関数の中で ~name を読まない」)は無い |
| 投影は node の別名の import を scope の中に残さない | 契約テスト(関数・class の本体の defhandler の投影の AST を読む) | tests/test_nested_definition_scope.py::test_static_projection_of_nested_defhandler_reads_module_level_nodes | 同上 | 検体は Pass・WithHandler の 2 つ。新しい node(S1)は表から判定されるが、検体は足さないと測らない |
| 別名の綴りの判定は expansion-names だけが持つ | static_check は is_alias_import を呼ぶだけ(綴りを書かない)。module 契約 doeff-hy-static-check → doeff-hy-expansion-names の allow | .agents/code-quality.d/doeff-hy-expansion-names.json | 変更範囲の品質検査 | 綴りを static_check に直に書く実装は依存の検査では拒めない(同じ module の中の文字列) |

## 盲検の後(上の主張は書き換えていない)

盲検の記録: blind-meta.md(起動・model・識別子)・blind-input.md(渡した入力)・blind-a-response.md・blind-b-response.md(加工なし)。

### A の失敗例 — 成立(S2 の主張が破れた)

- 主張 S2「型を建てる定義の展開が型の本体で新しい名を読んでも、static-check-postprocess は変えない」に対し、今ある defeffect・defwire・defrecord(:check)の
  型の本体が読む `_doeff_ClassVar`・`_doeff_cast` が、class の本体の投影で未定義になる(実行時は動く)。
- 再現(設計者・8a759a421 の木): `uv run doeff-hy-check b_cls_effect.hy c_cls_record_check.hy --root … --no-cache` → error 4 件
  (hc-class-body-types-before.log)。
- 原因: 綴りの持ち主が 2 つ — 型を建てる 3 か所(declarations.hy の defeffect-form・record.hy の defrecord の :check・defwire)が
  `(import typing [ClassVar :as _doeff-ClassVar …])` を手で綴り、後処理が見分けに使う表 expansion-names はそれを知らない。隠すはずの知識
  (どの import が展開の合成か)が作り手と表に割れていた — 知識の漏洩であり、意図した契約の変更ではない。
- 修正(doeff 4e46d0df1・L2241): 綴りの持ち主を表 1 つにする。表に typing の ClassVar・cast を足し、3 か所の import を表の import-form から組む。
  後処理(static_check)は表に問うだけで、綴りを足していない。
- 再検証: 失敗する検を先に足し(e0ec39cec)9 failed(red2.log)→ 直した後 152 passed(green2.log)。doeff-hy-check は error 4 件 → 0 件
  (hc-class-body-types-after.log)。触った部品の検 64 passed(around2.log)。
- 実際の波及: expansion-names(表と責務の文)・defeffect-form・record-class-form(と defwire)・module 契約の断片。事前の予測(S2: record-class-form と
  defeffect-form だけ)より広い — 表への配線替え 1 回分で、以後 S2 は「作り手が表から import を組む」限り static-check-postprocess を変えない。

### B の失敗例 — 検査の穴は成立、提案の実装は着地していない

- B の提案(後処理が `_doeff_` の頭の綴りで展開の合成を決める)は、提示した 4 つの検査を通り、書き手の class の本体の import
  (`_doeff_traverse_Traverse`・`_doeff_do`)を module の段へ移して、実行時の NameError を型検査から隠す(B の実測)。
- B が別に見つけた事: 8a759a421 の `home` は表に無い名を doeff と決めたので、`is_alias_import("doeff","do","_doeff_do")` が真で、
  書き手の `(import doeff [do :as _doeff-do])` を移していた。設計者の再現: bneg_check.py を 8a759a421 の木で流すと AssertionError
  (class の本体に残る import が `_doeff_traverse_Traverse` だけ — bneg-old.log)。
- 修正(同じ 4e46d0df1): 表を閉じる(展開が読む名をすべて置き場とともに載せ、表に無い名は home が KeyError・is_alias_import は偽)。
  検 test_is_alias_import_answers_only_the_table に `do`・`Traverse` の別名・置き場違い・別名なしを偽とする例を足した。
- 再検証: bneg_check.py を 4e46d0df1 の木で流すと、書き手の 2 つの import は class の本体に残る(bneg.log)。
- 残る限界: 後処理を綴りの頭で決める形に書き換える実装そのものを拒む静的な規則は無い。拒むのは契約テスト
  (test_is_alias_import_answers_only_the_table は表の判定、test_static_projection_* は移す側)だけで、書き手の import を移さない事を
  投影の側で確かめる検は bneg_check.py(一度の実行)で、検の組には入れていない。

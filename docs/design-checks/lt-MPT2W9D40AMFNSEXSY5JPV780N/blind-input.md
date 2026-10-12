# 盲検の入力(対象版 doeff 8a759a421ca32d6746d6d3f7fd3b90558b1962ec・読む木 /home/ubuntu/.worktrees/doeff-wt-design-check-ltmpt2)

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

## 検査(実体・設定・検出範囲)

| 検査 | 実体 | 検出範囲 |
|---|---|---|
| 契約テスト 1 | tests/test_nested_definition_scope.py::test_class_body_type_methods_read_their_own_class | class の本体の defrecord(凍結・継いだ型・default-factory と init=False)・defwire(凍結)・defeffect(凍結)の 5 検体を実際に動かし、断る error の型と repr を比べる |
| 契約テスト 2 | tests/test_nested_definition_scope.py::test_static_projection_of_nested_defhandler_reads_module_level_nodes | 関数・class の本体の defhandler の投影の AST で、_doeff_Pass・_doeff_WithHandler を scope の中の import が束ねず、module の段の import が束ねる事 |
| 品質検査(fast) | `code-quality --scope changed`(module 契約 .agents/code-quality.d/*.json の allow・layer・public の依存検査と untyped-boundary) | doeff-hy-static-check は doeff-hy-expansion-names に依存してよい(allow) |
| semgrep(commit の hook) | .semgrep.yaml | 構造の規則(この変更に特有の規則は無い) |

## 実コード(読む所)

- packages/doeff-hy/src/doeff_hy/record.hy — defrecord の class-form(行 210〜345 付近)・defwire(行 446〜)
- packages/doeff-hy/src/doeff_hy/declarations.hy — defeffect-form(行 272〜)
- packages/doeff-hy/src/doeff_hy/record_runtime.py — seal_record・seal_effect
- packages/doeff-hy/src/doeff_hy/macros.hy — _scoped-definition・_publish-imports(行 680〜760 付近)
- packages/doeff-hy/src/doeff_hy/expansion_names.py — 別名の表・is_alias_import
- packages/doeff-hy/src/doeff_hy/static_check.py — _synthesized_import・with_module_level_imports・without_bookkeeping(行 375〜560 付近)
- packages/doeff-hy/src/doeff_hy/handle.hy — _vm-imports・defhandler(行 78〜100・880〜940)
- packages/doeff-hy/src/doeff_hy/clause_ops.hy — 節の操作の node
- 変更の差分: `git -C <木> diff 787554c3692cfe9bed8e1299442ab24afdd81523 8a759a421ca32d6746d6d3f7fd3b90558b1962ec`

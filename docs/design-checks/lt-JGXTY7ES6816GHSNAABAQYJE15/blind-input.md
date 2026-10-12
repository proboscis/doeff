# 盲検 A・B に渡した入力(共通)— 依頼 lt-JGXTY7ES6816GHSNAABAQYJE15

対象の版: doeff の作業木 `/home/ubuntu/.worktrees/doeff-wt-scoped-definition`(commit b52770e03。doeff の main の 49a293f19・c4590550 と、変更したファイルの中身は同じ)。この作業木は**読み取りだけ**にする。共有の source を変更しない・全数の検査を走らせない・他の会話へ連絡しない。命令を走らせてよいのは、読み取りだけの物(grep・cat・git log・git show など)に限る。失敗例の再現の手順は、設計者が走らせるので、手順を書いて渡す。

## システムの概要と利用状況

doeff-hy は、Hy(Lisp の方言)の macro を Python へ展開する library。利用者は `defk`・`deff`・`defeffect`・`defrecord`・`defwire`・`defhandler`・`defworkflow` などの「定義の macro」を、module の段・class の本体・関数の本体のどこにでも書く。展開は 2 通り:

- 実行時の展開: Python として import して走らせる。
- 型検査のための展開(投影): doeff-hy-check が、同じ macro を別の形で展開した Python を pyright に読ませる。型の宣言(class・def)は外の関数に残す必要がある。

定義の macro の展開は import 文を含む。import した名は、書かれた所の scope に束縛される。

- 関数の本体: 名は外の関数全体の局所の名になる。外の関数が先に module の段の同じ名を読むと、実行時は UnboundLocalError・pyright は未束縛。
- class の本体: 名は class の属性になる。入れ子の class の本体と method は、名を引く時に外の class の段を飛ばす。module の globals に名が無ければ NameError。

## 受入条件

- R1: 型検査の投影で、関数・class の本体の中の defrecord(頭の辞書つき)・defwire・defeffect・deff の展開の import が、外の関数・外の class の局所の名にならない。
- R2: class の本体に直に書いた defrecord・defwire・defeffect が、module の段に `doeff_hy` が無くても建ち、使える。
- R3: conductor.hy の defworkflow を関数の中で使っても、外の関数の局所の名に `_conductor_*` を足さない。
- R4: 3 つとも、`_scoped-definition` の 1 か所に寄せて直す(依頼の方針)。型検査の投影は macro の展開の中から module の段へ文を出せないので、後処理の 1 か所に置く。

## 責務と公開の口

| 責務 | 実装 | 持つ知識 | 隠す知識 |
|---|---|---|---|
| scoped-definition | packages/doeff-hy/src/doeff_hy/macros.hy の `_scoped-definition`・`_publish-imports`・`_imports-of`・`_publish-names` | 書かれた所の判断・包むか・module の globals に置くか | 場所ごとの import の置き方 |
| definition-macros | macros.hy の defk・deff・defeffect ほか、record.hy の defrecord・defwire、conductor.hy の defworkflow、handle.hy の defhandler | 契約の鍵・欄・展開の文 | なし(場所を判断せず、文の並びを渡すだけ) |
| projection-imports | packages/doeff-hy/src/doeff_hy/static_check.py の `with_module_level_imports`・`_nested_imports`・`_WithoutNestedImports`・`_synthesized_import`、static_view.py の `DEFSYSTEM_IMPORTS`・`DEFEFFECT_OUTCOMES_MODULE` | macro が合成する import の module の綴りの表 | 木の歩き方 |
| scope-tests | tests/test_nested_definition_scope.py、tests/test_handle_scope.py | 検体の表・局所の名の数え方 | — |

公開の口:

- `(_scoped-definition compiler name forms [module-forms] [nested-reads])` — forms は import を含む文の並び。関数の中では包んで `name` だけを束ねる。module と class の本体では並べる。`nested-reads` が真で class の本体なら、forms の import が束ねた名を定義より前に module の globals へ `setdefault` で置く。
- `with_module_level_imports(tree) -> tree` — 型検査の投影の木で、関数・class の本体の中の macro 合成の import(`_synthesized_import` が真の物)を module の段の、囲む文の前へ移す。

不変条件:

- 関数の中の定義の展開(実行時)が外の関数に残す局所の名は `name` だけ。
- module と class の本体の定義は包まない(`__qualname__` と引数なしの `super()` を保つ)。
- module の globals を変えるのは、`nested-reads` を真にした定義が class の本体に書かれた時だけ。defhandler は module の globals を変えない(別の決め)。
- 型検査の投影で、関数・class の本体の中に、macro 合成の doeff_hy の import(と表の module の import)を残さない。利用者が書いた import は動かさない。

前提:

- 実行時の展開は包める。型検査のための展開は型の宣言を外の関数に残す必要があるので包めない。
- 未知の将来をすべて予測できるとは主張しない。

## 将来の変更の主張(事前の予測)

- S1(effects): 新しい定義の macro を足す、または既存の macro の展開に新しい補助の import を足す。変えるのは、その macro を持つ module と、検体 1 つ(scope-tests の表 1 行)。変えないのは scoped-definition・projection-imports・他の定義の macro。補助が doeff_hy の module か表の module なら、関数・class の本体と型検査の投影の両方で局所の名の漏れは起きない。class を建てる macro は `nested-reads` を真にして呼ぶ。別の module の import を足す時だけ、表(static_view.py の定数 1 行)への追加が要る(公開契約の拡張)。
- S2(storage): 型検査の投影の後処理の中身を変える。変えるのは projection-imports(static_check.py の後処理の関数)だけ。保存した展開は、POST_PROCESSING の閉包の digest で古い物を引かない — 後処理の関数と、その中で使う class の method の本体を替えると digest が変わる。

## 設計者が置いた検査と呼び方

作業木のルート `/home/ubuntu/.worktrees/doeff-wt-scoped-definition` で:

| 検査 | 内容 | 呼び方 |
|---|---|---|
| 定義の macro の数え上げ | doeff_hy.macros・doeff_hy.record・doeff_hy.conductor が出す def の付く macro(名を束ねない defphase を除く)の全部に、関数の中の検体が在る | `uv run pytest tests/test_nested_definition_scope.py::test_every_definition_macro_has_a_nested_sample` |
| 局所の名の検 | 検体 1 つだけを書いた関数の局所と閉包の cell が、定義した名と `_hy_` で始まる一意の名だけ(補助の名の表に依らない) | `…::test_function_with_only_a_definition_binds_only_its_names` |
| module の状態の検 | 外の関数を何度呼んでも、module と doeff の module の globals に名・記録・登録が増えない | `…::test_definition_sample_leaves_module_state_unchanged`・`test_nested_definition_leaves_module_globals_unchanged` |
| 入れ子の定義の実行の検 | 外の関数が先に module の段の名を読んだ後に入れ子の定義が来る形が走る | `…::test_nested_definition_runs`・`test_outer_function_binds_no_helper_name` |
| class の本体の型の検 | module に `doeff_hy` が無い header で、class の本体の defrecord・defwire・defeffect が建ち、使える | `…::test_class_body_type_definition_runs_without_module_doeff_hy` |
| 投影の検 | 投影の AST で、関数・class の本体の import が束ねる名に `doeff_hy`・`doeff_core_effects`・`doeff_cluster` が無く、定義が読む `doeff_hy.<部分の module>` の import が module の段に在る | `…::test_static_projection_of_function_definition_binds_no_module_name`・`…class_body…` |
| defhandler が module の globals を変えない検 | class の本体の defhandler の前後で module の globals が同じ | `uv run pytest tests/test_handle_scope.py::test_class_body_handle_leaves_module_globals_alone` |
| 品質検査(速い段) | 変更範囲の module 契約・依存・規則(型は含まない) | `sh /home/ubuntu/dotfiles/agent/venv-run.sh /home/ubuntu/dotfiles/agentcli code-quality --repo <作業木> --scope changed --base <基底 sha>` |
| semgrep の規則 | commit の hook が、`.semgrep.yaml` の規則を staged の file に当てる(architectural rules・Hy architectural rules) | `.pre-commit-config.yaml`・`.semgrep.yaml` を読む |

全検査の実行は設計者がする。あなたは検査の中身を読んで、通るかどうかを推測しない — 通ると断定せず、「通るはず」と書く時は読んだ根拠を示す。

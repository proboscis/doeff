# 盲検 A・B の共通の材料(lt-N19BACF5SXDRMKQ7EZD3AZA8FC)

## 読む物の在り処

- 読ませる版(実装の版): `/home/ubuntu/.worktrees/doeff-blind-d4c8d7ce9`(git の版 d4c8d7ce999c8536cddf7349855d2a3deece43db そのもの・detached・`.venv` を作り済み)。
- 基準の版(直す前): dadb899700fb94f49904af5f26f4d8deae9a847b(`git -C /home/ubuntu/.worktrees/doeff-blind-d4c8d7ce9 show dadb89970:<path>` で読める)。
- 主な file:
  - `packages/doeff-hy/src/doeff_hy/macros.hy`: `_helper-imports`・`_with-scoped-helpers`・`_do-reference`・`_scoped-definition`・`_in-function-body?`・`_install-guard-globals`・`_wrap-statement-guard`・defk・defp・deftest・fnk・do!・defmcp-tool(`(defmacro defmcp-tool` を grep)
  - `packages/doeff-hy/src/doeff_hy/handle.hy`: defhandler / handle の `_do-import`
  - `packages/doeff-hy/src/doeff_hy/static_view.py`: STATIC_HELPER_IMPORTS
  - `packages/doeff-hy/src/doeff_hy/static_check.py`: `without_bookkeeping`・`_synthesized_import`・`with_static_helpers`・`project`・`main`
  - `packages/doeff-hy/src/doeff_hy/static_types.pyi`: 型検査のための展開の `do`・`_doeff_perform`
  - 検: `packages/doeff-hy/tests/test_static_check.py`(`test_defmcp_tool_reads_do_from_the_module_head`・`test_defk_calls_stay_programs_past_sixty_four_mcp_tools`・`test_defk_calls_stay_programs_past_sixty_four_definitions`)・`tests/public_api/test_mcp_tool_macro.py`・`tests/test_nested_definition_scope.py`

## 展開と検の見方(実行の command)

- Python は必ず uv を通す(`.venv/bin/python` の直呼び・機体の python3 は使わない)。作業 dir は読ませる版の根。
- 実行時の展開と型検査のための展開の文字列:
  ```python
  import ast, hy, doeff_hy
  from doeff_hy.static_view import static_view
  src = "(require doeff-hy.macros [defmcp-tool <-])\n(defmcp-tool t \"d\" [{:name \"x\" :type \"string\" :description \"x\"}] x)"
  print(ast.unparse(hy.compiler.hy_compile(hy.read_many(src), "__main__")))
  with static_view():
      print(ast.unparse(hy.compiler.hy_compile(hy.read_many(src), "__main__")))
  ```
  `uv run python -I <script>` で走らせる。
- doeff-hy-check が pyright に渡す投影(module の頭の補助の import を足した後の Python): `from doeff_hy.static_check import project; project(root, [root], path).text`(root は .hy を置いた dir)。
- doeff-hy-check そのもの(pyright を走らせる): `uv run doeff-hy-check --root <dir> --json <file.hy>`。file 1 つ・小さな検体だけに使う(repo 全体に掛けない)。
- 検: `uv run pytest packages/doeff-hy/tests/test_static_check.py -k mcp -q`・`uv run pytest tests/public_api/test_mcp_tool_macro.py tests/test_nested_definition_scope.py -q`。これより広い検(全体の pytest・repo 全体の型検査)は走らせない。

## 守る事

- 読ませる版の作業木を変えない(file の編集・`git add`・`commit`・`stash`・`checkout` をしない)。実験の file は自分の dir(A は `/tmp/ln19-blind-a/`、B は `/tmp/ln19-blind-b/`)に置く。
- code を変えて試す時は、写しの木(例 `git -C /home/ubuntu/.worktrees/doeff-blind-d4c8d7ce9 archive HEAD | tar -x -C <自分の dir>/tree` の後 `cd <自分の dir>/tree && make sync`)を作るか、1 つの Python の process の中で関数を差し替えて試す。
- 他の会話への連絡、GitHub・作業ボードへの書き込みをしない。

## 固めた設計(盲検の前の主張)

以下は設計者が盲検の前に固めた本文(design-before-blind.md)の写し。

この file は盲検 A・B を起こす前に固めた主張です。盲検の後に書き換えません(盲検の後の結論は design.md)。

## 完了の範囲

実装完了まで。直しは doeff の main に入れた(L2145・d4c8d7ce999c8536cddf7349855d2a3deece43db)。

- 基準の版(直す前): dadb899700fb94f49904af5f26f4d8deae9a847b
- 実装の版: d4c8d7ce999c8536cddf7349855d2a3deece43db(packages/doeff-hy/src/doeff_hy/macros.hy と packages/doeff-hy/tests/test_static_check.py)

## 要件(固定)

- R1(card ki-4848d81e0bc5 の受入 c185601c4): 型検査のための展開の defmcp-tool が `_helper-imports` から補助の名を読み、表に無い名を import しない事を確かめる検が doeff の main で緑。
- 依頼の文: 型検査のための展開の defmcp-tool も、`_helper-imports` から補助の名を読む形にする。依頼の所見: 補助を足す・名を変える時に、defmcp-tool の型検査の展開だけが取り残される。

## 基準の版の障害

- defmcp-tool の展開は頭で `'(import doeff.do [do :as _doeff_do])` を直に出していた(実行時の展開でも型検査のための展開でも)。defk・defp・deftest・fnk・do! は補助の名の表 `_helper-imports` を通す。
- 型検査のための展開では、doeff-hy-check が module の頭に `from doeff_hy.static_types import do as _doeff_do` を 1 度だけ置く(static_view.STATIC_HELPER_IMPORTS・static_check.with_static_helpers)。defmcp-tool の import は doeff の module なので、重なった import を畳む処理(static_check.without_bookkeeping の `_synthesized_import` は doeff_hy の module だけ)に掛からず、tool 1 つごとに `_doeff_do` の宣言が 1 つ増えた。
- 実測(基準の版): tool 65 個の module で pyright が `_doeff_do` を Unknown にし、後ろの defk の呼びが `Expand[int, Never]` ではなく `int` に見えた(agora-redesign #1686 と同じ訳)。

## 実装(実装の版)

- defmcp-tool の頭の import を `(_helper-imports)` に替えた。型検査のための展開では import を出さず、module の頭の `_doeff_do` を読む。実行時の展開は defk と同じ補助の import が 2 行増える(`EffectBase as _doeff_effect_base` と doeff_hy.macros の guard の補助)。`(import doeff.mcp [McpToolDef McpParamSchema])` は defmcp-tool に固有の import として残した。
- 検: packages/doeff-hy/tests/test_static_check.py の `test_defmcp_tool_reads_do_from_the_module_head`(module・class の本体・関数の本体の tool の型検査の展開で、`_` で始まる名を import する文は module の直下の頭の補助の import だけで、`_doeff_do` の宣言は 1 つ)と `test_defk_calls_stay_programs_past_sixty_four_mcp_tools[False/True]`(tool 70 個の後ろの defk の呼びが Program のまま・pyright を使う)。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 口 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M1 | 補助の名の表 `_helper-imports`(macros.hy) | 実行時の展開が名で読む補助(`_doeff_do`・`_doeff_effect_base`・`_install-guard-globals`・`_guard-performed`・`_guard-statement-value`・`_doeff-check-program-return`)の出所の module と別名。型検査のための展開では何も出さない決め | 補助の出所の module(doeff.do・doeff・doeff_hy.macros)を定義の macro から隠す | `(_helper-imports)` が返す `(do …)` の form | なし、展開の時の純関数(`_static-view?` を読むだけ) | なし、呼ぶたびに form を返す | 型検査のための展開では空の `(do)`。実行時は 3 つの import の文 |
| M2 | 型検査の頭の補助の import(static_view.STATIC_HELPER_IMPORTS・static_check.with_static_helpers) | 型検査のための展開での補助の名の型の出所(static_types の `do`・`_doeff_perform`・型の名)と置く位置(module の頭に 1 度・読む名だけ) | 型の出所を定義の macro から隠す | STATIC_HELPER_IMPORTS の文字列・`with_static_helpers(tree)` | なし、AST の書き換えだけ | なし、file 1 つの展開ごと | 補助の名の宣言は module の頭の 1 つだけ(#1686) |
| M3 | 定義の macro(defk・defp・deftest・defmcp-tool。式の macro fnk・do! は `_with-scoped-helpers` を通す) | その定義に固有の import(defmcp-tool の doeff.mcp の McpToolDef・McpParamSchema)、本体の組み立て、補助を名で読む所 | 本体の組み立ての中身 | macro の形(`(defmcp-tool name "説明" [params] body…)` → McpToolDef の値と handler `_<name>_mcp_handler`) | なし、展開だけ | なし | 補助の名を自分で import しない — 補助の import は M1 を通す |
| M4 | 置き場 `_scoped-definition`(macros.hy) | 書かれた所(module・class の本体・関数の本体)で、文の並びをすぐ呼ぶ関数に包むか | 置き場の判定(`_in-function-body?`)を定義の macro から隠す | `(_scoped-definition compiler name forms)` | なし、展開だけ | なし | 関数の本体の中の実行時の展開では import を外の関数の局所の名にしない。型検査のための展開は包まない |
| M5 | 検(test_static_check.py の 2 件・test_mcp_tool_macro.py・test_nested_definition_scope.py) | 決まりを機械で確かめる対照 | なし、検の表を公開する | pytest | なし、検の中で展開して走らせるだけ | 検の 1 回 | 型検査のための展開で、補助の名の import は module の直下の頭だけ |
| M6 | handle.hy の defhandler / handle の `_do-import`(この依頼の範囲の外) | 実行時は `doeff.do` の `do` を `_doeff-do` へ import し、型検査のための展開では何も出さない(M1 と同じ決めの 2 つ目の置き場) | handler の組み立て | `(_do-import)` | なし、展開だけ | なし | 型検査のための展開では import を出さない |

## 公開の契約

- 定義の macro(M3)の展開が名で読む補助は、実行時は M1 が import し、型検査のための展開では M2 が module の頭に 1 度だけ置く。
- 型検査のための展開で、補助の名(`_` で始まる別名)を import する文は、module の直下の頭の補助の import だけ。
- defmcp-tool の公開の形(引数・McpToolDef の値・handler の名)は変えない。

## 変更シナリオと主張(盲検の前)

### S1(axis effects・適用)— defmcp-tool の本体に文の guard を足す

- 変更: defmcp-tool の handler の本体に、defk と同じ文の guard(`_wrap-statement-guard` が出す `_guard-statement-value`)を足す。card ki-4848d81e0bc5 の発言が名指した将来の変更。
- 主張: 補助の import に触れずに済む。変わるのは M3 の defmcp-tool の本体の組み立て(guard の wrap と、class の本体の handler からも名が引けるようにする `_install-guard-globals` の呼び)だけ。M1・M2・M4・M6 は変わらない。型検査のための展開でも `_guard_statement_value` を頭の外で import しない。
- 前提: 足す補助が M1 の表に既に在る。
- 予想の範囲: 変わる M3。変わらない M1・M2・M4・M6。

### S2(axis simulation・適用)— 型検査の投影で `_doeff_do` の型の出所を替える

- 変更: 型検査のための展開(走らせない投影)で `_doeff_do` に与える型の出所を替える。例: static_types の `do` を core の `doeff.do.do` と別の型付きの宣言にする(agora-redesign #2321 の前の形へ戻す類いの変更)。
- 主張: 変わるのは M2 だけ。同じ module の defmcp-tool の handler と defk は、どの位置でも M2 の型の `_doeff_do` で包まれる(2 つ目の宣言が無い)。M1・M3・M4・M6 は変わらない。
- 前提: 利用者が自分で `_doeff_do` を import しない module(for/do のために利用者が書く `(import doeff [do :as _doeff-do])` は範囲の外 — static_types.pyi の註)。
- 予想の範囲: 変わる M2。変わらない M1・M3・M4・M6。

### S3(axis effects・適用)— 実行時の `do` の出所を替える(補助の出所の名を変える)

- 変更: 実行時の展開が `_doeff_do` に束ねる `do` の出所を、`doeff.do` から別の module へ替える。
- 主張: 定義の macro(M3)の分は M1 の 1 か所で替わり、defmcp-tool に個別の編集は要らない(基準の版では defmcp-tool の import が取り残された)。M2・M3・M4 は変わらない。
- 前提と範囲の限界: M1 と同じ決めを別に持つ所 — M6(handle.hy の `_do-import`)と、同じ file の fnk の `_do-reference`(実行時に `__import__ "doeff.do"` で引く) — は、今の設計では M1 と一緒に替える必要がある。この依頼は defmcp-tool を M1 に揃える所までで、M6 と `_do-reference` を M1 に寄せる事は範囲の外。
- 予想の範囲: 変わる M1(と、限界として M6・`_do-reference`)。変わらない M2・M3・M4。

### 対象外の軸

- S4 storage: 補助の表は展開の時の純関数で保存を持たない。展開の cache(.pyc・共有の code の置き場)は macro の file の hash で無効になり、表の置き場は macros.hy の中から動かない。
- S5 hardware: 展開は機器に依らない。
- S6 distribution: 展開は module ごとに 1 つの process の中で閉じる。doeff-hy-check の process の pool は file ごとに展開を分けるだけで、表に共有の状態は無い(`_static-view?` は process ごとの ContextVar)。
- S7 concurrency: 表は ContextVar を読む純関数で、並べた展開(doeff-hy-check の process の pool)の間で共有する状態を持たない。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 型検査のための展開で、定義の macro が補助の名を自分で import しない(M3 → M1・M2) | 展開の AST を読む契約の検: `_` で始まる別名を import する文が module の直下の頭の補助の import(doeff_hy.static_types・doeff_hy.macros の from-import)だけか | packages/doeff-hy/tests/test_static_check.py::test_defmcp_tool_reads_do_from_the_module_head | pytest(変更時の focused の検と、全体テストの時刻) | defmcp-tool だけを検める。別の定義の macro の型検査の展開は、#1686 の検(defk)と各 macro の検が持つ。module の直下に置いた doeff_hy.macros からの import は「頭」と区別しない |
| 補助の名の宣言が 64 を超えない(M2 の不変条件) | pyright を使う回帰の検: tool 70 個の後ろの defk の呼びが Program のまま | test_static_check.py::test_defk_calls_stay_programs_past_sixty_four_mcp_tools | pytest(pyright が在る時だけ・`needs_pyright`) | pyright の閾(64)に依る。pyright が無い環境では skip |
| 実行時の展開の動き(M3・M4) | 既存の契約の検 | tests/public_api/test_mcp_tool_macro.py・tests/test_nested_definition_scope.py | pytest | 実行時の展開が M1 を通るかは検めない(展開の文字列の比較は 6 通りを手で行った) |

静的な import の層の検査(semgrep など)は使わない: 表を通すかは macro の展開の結果の形で決まり、source の綴りの規則では「型検査のための展開で何が出るか」を表せないため。

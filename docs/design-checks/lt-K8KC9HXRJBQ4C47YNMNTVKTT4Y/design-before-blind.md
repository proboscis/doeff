# 設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y — 盲検の前の設計と主張(card ki-973b4e44d7bc)

この文書は盲検 A・B を起こす前に固定する。盲検の後に結論を書き換えない(後の結論は design.md に書く)。

## 0. 全体の図(d8db309f3 の形)

```
 直す前: 実行時の do の置き場 "doeff.do" を 4 か所が別々に綴っていた
   macros.hy _helper-imports ─┐
   handle.hy _do-import ──────┼─▶ (import doeff.do [do :as …]) / (__import__ "doeff.do" …)
   macros.hy _do-reference ───┤
   macros.hy _validation-imports ┘

 今(d8db309f3): 置き場は M1 の 1 つ
   M1 macros.hy _runtime-do → RuntimeDo("doeff.do", "do")
        │ _runtime-do-import alias / RuntimeDo の欄
        ├──▶ M2 _helper-imports ─────▶ defk・defp・deftest・defmcp-tool の `_doeff_do`
        ├──▶ M3 handle.hy _do-import ─▶ defhandler・handle の `_doeff_do`
        ├──▶ M4 _do-reference ────────▶ fnk の `__import__(…).do`
        └──▶ M5 _validation-imports ──▶ validate・契約の check の `_doeff_validation_do`

 MCP の引数(別の筋)
   schema [{:name :type :required …}] ─▶ M6 _mcp-handler-param ─▶ handler の引数 (annotate 名 "str" | "int | None" …)
   M8 mcp_server_loop: (tool.handler #* (lfor n param-names (.get arguments n)))  ← 送られない引数は None
   型検査のための展開: M7 static_view.py が module の頭に型付きの do・_doeff_McpToolDef を 1 度だけ置く
```

## 1. 完了の範囲と順序の申告

| 項目 | 内容 |
|---|---|
| 範囲 | 実装完了まで。実装は doeff main `d8db309f31b816f539dd226ba1fdab5b3a37f44d`(マージの列 L2187)に入済み |
| 順序の申告 | 依頼は class=investigate で、本来は設計検証 → 別の実装の依頼の順。card の受入の条が「doeff main で緑」を求めていたので、この会話で実装して main に入れ、その後にこの設計検証を行う。主張は実装した版 d8db309f3 に対して固定する |
| 対象の版 | d8db309f31b816f539dd226ba1fdab5b3a37f44d |

## 2. 固定した受入の条(card ki-973b4e44d7bc)

- c99aedbcf: defmcp-tool の型検査のための展開で handler の引数に schema の型の注記が付き、strict で tool 1 つの赤が 0 件になる検が doeff main で緑
- ca9a5d5a2: 実行時の do の import の決めが補助の表 1 か所になり、表を差し替えると 4 つの macro が追従する検が緑

直す前の状態(依頼の文): (1) defmcp-tool の handler の引数に型の注記が無く、strict では tool 1 つにつき書き手が直せない Unknown の赤が 4〜5 件出る。(2) 実行時の do を import する決めが、補助の表の他に handle.hy の `_do-import`・fnk の `_do-reference`・`_validation-imports` の 3 か所に別に在る。

## 3. 責務(modules)

path は doeff の repo の根から。

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M1 | 実行時の展開が引く `do` の置き場(`packages/doeff-hy/src/doeff_hy/macros.hy` の `RuntimeDo`・`_runtime-do`・`_runtime-do-import`) | `do` の module の名と、その module の中の名(Python の綴りの文字 `"doeff.do"`・`"do"`) | 置き場を M2〜M5 から隠す — M2〜M5 は置き場の文字を持たない | `(_runtime-do)` → `RuntimeDo(module: str, name: str)`(frozen の dataclass)、`(_runtime-do-import alias)` → `(import <module> [<name> :as <alias>])` の form | なし、展開の時の純関数 | なし、呼ぶたびに値を返す | 実行時の展開で `do` を引く所は、すべてこの値から綴りを作る。module は点を含んでよい(Hy は点を含む記号を式に読むので、文字で持つ) |
| M2 | 定義の macro(defk・defp・deftest・defmcp-tool)の補助の import の表(`macros.hy` の `_helper-imports`) | 補助(`_doeff_do`・`_doeff_effect_base`・`_install-guard-globals` ほか)の出所。`do` の行は M1 から | 補助の出所を定義の macro から隠す | `(_helper-imports)` → `(do …)` の form | なし、純関数(`_static-view?` を読むだけ) | なし | 型検査のための展開では空の `(do)`。実行時は `do` の行を `(_runtime-do-import '_doeff_do)` で作る |
| M3 | defhandler・handle の `_doeff_do` の import(`packages/doeff-hy/src/doeff_hy/handle.hy` の `_do-import`) | handler の本体を包む名 `_doeff-do` | `do` の置き場を持たない(M1 から引く) | `(_do-import)` → import の form | なし、純関数 | なし | 型検査のための展開では空。実行時は `(_runtime-do-import '_doeff-do)` |
| M4 | fnk の lambda が呼ばれた時に `do` を引く式(`macros.hy` の `_do-reference`) | 書いた場所の scope に名を束ねない引き方(`__import__`) | `do` の置き場を持たない(M1 から引く) | `(_do-reference)` → 式の form | なし、純関数 | なし | 型検査のための展開では `_doeff_do`。実行時は `(. (__import__ <module> :fromlist #(<name>)) <name>)` |
| M5 | validate と契約の check の `_doeff_validation_do`(`macros.hy` の `_validation-imports`) | 局所の名がぶつからない別名 `_doeff_validation_do` | 実行時の `do` の置き場を持たない(M1 から引く) | `(_validation-imports)` → import の form | なし、純関数 | なし | 実行時は `(_runtime-do-import '_doeff_validation_do)`。型検査のための展開では `doeff-hy.static-types` の do(実行時の決めの外) |
| M6 | MCP の引数の schema から handler の引数を作る(`macros.hy` の `_mcp-handler-param`・`_parse-mcp-params`・defmcp-tool) | JSON Schema の型 → Python の型の写し(string→str・integer→int・number→float・boolean→bool・array→list[object]・object→dict[str, object])。必須でない引数の `| None` | 写しの表を defmcp-tool の書き手から隠す | `(_mcp-handler-param tool p)` → `(annotate <名> "<型>")`。知らない `:type` は SyntaxError | なし、展開の時の純関数(誤りは SyntaxError) | なし | 注記は文字の注記で実行の時に評価しない。`:required` が True の字面でない引数は `T | None`(M8 が送られない引数に None を渡す事を写した共同の不変条件)。型検査のための展開と実行時の展開で同じ注記 |
| M7 | 型検査のための展開の補助(`packages/doeff-hy/src/doeff_hy/static_view.py` の `STATIC_HELPER_IMPORTS`・`MCP_STATIC_IMPORTS`) | 型付きの `do`(`doeff_hy.static_types`)と `_doeff_McpToolDef` / `_doeff_McpParamSchema` を module の頭に 1 度だけ置く | 型検査の側の `do` の出所を macro から隠す | doeff-hy-check が投影の頭に足す文字 | なし | なし | 型検査のための展開の macro は `do` を import しない(M5 の型検査の枝を除く) |
| M8 | MCP の tool の server が handler を呼ぶ(`packages/doeff-agents/src/doeff_agents/handlers/mcp_server_loop.hy` の `_tool-result-with-stack`) | schema の順に引数を位置で渡す。送られなかった引数は `None`(`.get`) | 引数の取り出し方を tool の書き手から隠す | `(tool.handler #* args)` | tool の Program を走らせる(この変更では触らない) | tool の要求 1 つの間 | 引数の値は JSON を解いた値 |
| M9 | 表の検(`packages/doeff-hy/tests/test_helper_imports_table.py`) | M1 を差し替えた時に 8 つの macro が従う事の確かめ方 | — | pytest の検 | tmp の dir に検の module を置く(検の中だけ) | 検 1 つ | 一覧 `_DEFINITIONS` の macro(defk・defp・deftest・defmcp-tool・defhandler・handle・fnk・validate)× module・class・関数の本体で、展開が `do` を引く所が差し替えた置き場だけになる |

## 4. 変更シナリオと事前の主張

| id | 軸 | 適用 | 変える要求 | 主張(検証できる形) | 予想の範囲 | 前提 |
|---|---|---|---|---|---|---|
| S1 | effects | 適用 | 実行時の `do` の置き場を替える(doeff が `do` を別の module へ移す、または doeff.do.do を包む別の do を使う) | M1 の `RuntimeDo` の 2 つの文字だけを変えれば、M2〜M5 の展開(8 macro)が全部新しい置き場を引く | 変わる: M1。変わらない: M2〜M9 | 新しい do は今の do と同じ呼び形(関数を受けて、呼ぶと Program を返す物を返す)で、`from m import n` と `__import__(m, fromlist=(n,)).n` の両方で引ける(m は点を含んでよい) |
| S2 | simulation | 適用 | 決定的な再生・記録のため、検や模擬の間だけ実行時の do を記録つきの do に差し替える | module の global `_runtime_do` を差し替えるだけで、差し替えの後に展開する 8 macro の全部がその do を引き、その do が実際に関数を包む | 変わる: なし(M1 の値を差し替えるだけ)。変わらない: M1〜M9 の code | 差し替えは展開(require・compile)の前。既に compile 済みの code は変わらない |
| S3 | effects | 適用 | MCP の引数の型の写しを足す・変える(新しい JSON Schema の型、`:enum` つきの string を Literal にする等) | M6 の `_mcp-handler-param` の match の表だけが変わる。strict の型検査は、写した型と本体の使い方の食い違いを赤にする | 変わる: M6。変わらない: M1〜M5・M7・M8・M9 | 新しい型は文字の注記で書ける Python の型で、M7 の module の頭の import を要しない(要るなら M7 も変わる — 共同の契約) |
| S4 | effects | 適用 | 実行時の do を引く新しい macro を足す | 新しい macro が M1(`_runtime-do-import`)か M2 を通して do を引けば、M1〜M8 は変わらない。変わるのは新しい macro と M9 の一覧 | 変わる: M9(一覧)と新しい macro。変わらない: M1〜M8 | 新しい macro の書き手が M1 を通す。**強制の限界**: M9 の一覧に足さない macro が `doeff.do` を自分で綴っても、M9 は見ない |
| S5 | storage | 対象外 | — | — | — | 展開は保存を持たない。展開した code の .pyc と共有の code の置き場(DOEFF_HY_CODE_STORE)は macro の提供元を依存として記録するので(`doeff_hy_bytecode_guard`)、M1 を変えると次の import が展開し直す |
| S6 | hardware | 対象外 | — | — | — | 展開の時の純関数で、機体の資源に触れない |
| S7 | distribution | 対象外 | — | — | — | tool の引数は今も agent の process から JSON で届く(M8)。機体を跨いでも値の形は JSON を解いた物で、M6 の写しは変わらない。展開は compile する process の中で閉じる |
| S8 | concurrency | 対象外 | — | — | — | M1 は frozen の値を返す純関数で、共有の可変の状態を持たない。差し替え(S2)は検の中の monkeypatch だけ |

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1 が実行時の do の置き場を 1 つ持つ(M2〜M5 は綴らない) | 置き場を差し替えた展開の構文木を読み、`do` を引く import と `__import__` の module が差し替えた物だけである事を確かめる契約の検。差し替えた do が実際に包む事も走らせて確かめる | `packages/doeff-hy/tests/test_helper_imports_table.py` の `test_runtime_expansion_takes_do_from_the_table`・`test_programs_are_built_by_the_tables_do`・`test_definitions_are_wrapped_by_the_tables_do` | 4 時間に 1 度の全体テスト、変更した file から選ぶ検(`scripts/run_changed_tests.py`) | 一覧 `_DEFINITIONS` の macro だけを見る。validate を class の本体に直に置く組は実行の検から外す(実行時の名が class の属性になる別の形) |
| M1 の値の形(点を含む module の名) | `RuntimeDo` を Python の綴りの `str` 2 つの frozen の dataclass にする。表の検の差し替えた置き場を package の中の module(点を含む名)にする | `macros.hy` の `RuntimeDo`、表の検の `_PROBE = "doeff_hy_table_probe.table"` | 同上 | macros.hy の中の `RuntimeDo` の欄の型は、Hy の module を型検査する経路が無い所では静的に確かめない |
| M6 が schema の型を handler の引数へ写す | pyright strict の型検査の検(doeff-hy-check `--strict`): 6 つの型と必須でない引数の tool で赤 0 件、`str | None` を `str` へ渡すと reportArgumentType。実行時の注記の値の検。知らない `:type` の SyntaxError の検 | `packages/doeff-hy/tests/test_static_check.py` の `test_defmcp_tool_handler_params_carry_the_schema_types`、`tests/public_api/test_mcp_tool_macro.py` の `test_handler_params_are_annotated_with_the_schema_types`・`test_unknown_param_type_raises` | 同上(型検査の検は pyright が在る時だけ走る — `needs_pyright`) | M6 の `| None` が M8 の渡し方を写す事は、M8 の振る舞いと結ぶ検が無い(M6 の註に書いた共同の不変条件) |

## 6. 主張しない事

- 未知の将来の macro が M1 を通る事を、M9 の一覧の外まで機械で保証するとは主張しない(S4 の限界)。
- 型検査のための展開の `do`(M7・M5 の型検査の枝)の置き場を 1 つにしたとは主張しない(依頼の範囲は実行時の決め)。

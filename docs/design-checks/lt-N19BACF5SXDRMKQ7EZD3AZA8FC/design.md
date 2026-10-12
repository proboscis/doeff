# 設計検証 lt-N19BACF5SXDRMKQ7EZD3AZA8FC — 盲検の後の設計(結論)

## この文書の目的

読み手が次の 3 つを判断できる状態にする。

1. defmcp-tool の補助の import が、補助の名の表(`_helper-imports` と、型検査の頭の補助の import)に揃ったか。
2. 盲検 A・B が見つけた失敗例は何で、どう直し、何で確かめたか。
3. 残る限界(この依頼の範囲の外)は何か。

盲検の前の主張は design-before-blind.md(書き換えていない)。この文書は盲検と再現の後の結論。

## 完了の範囲と版

実装完了まで。

| 版 | sha | 中身 |
|---|---|---|
| 基準の版(直す前) | dadb899700fb94f49904af5f26f4d8deae9a847b | defmcp-tool が `doeff.do` を直に import |
| 実装の版 | d4c8d7ce999c8536cddf7349855d2a3deece43db | defmcp-tool の頭を `(_helper-imports)` に替えた(main・L2145) |
| 直した版 | 7458de13a(origin/main 25ddfddff の上へ載せ直した後。載せ直す前は c02f34038) | 盲検 A・B の失敗例を受けた直し(この branch・wt/design-check-lt-N19BACF5) |

証拠の多くは載せ直す前の c02f34038 で取った。載せ直しで重なった file は無く、載せ直した後の版 7808046be で関わる test の file を走らせ直した(evidence/gate/related-tests-after-rebase.txt・404 passed)。

## 要件

- R1(card ki-4848d81e0bc5 の受入 c185601c4): 型検査のための展開の defmcp-tool が `_helper-imports` から補助の名を読み、表に無い名を import しない事を確かめる検が doeff の main で緑。
- 依頼の文: 型検査のための展開の defmcp-tool も、`_helper-imports` から補助の名を読む形にする。

## 結論の要約

| シナリオ | 盲検の前の主張 | 実装の版 d4c8d7ce9 で | 直した版で |
|---|---|---|---|
| S1 effects: defmcp-tool の本体に文の guard を足す | 変わるのは M3 の defmcp-tool だけ | 成り立つ。ただし盲検 B: 表を通らない defmcp-tool を検が止めず、その下では S1 が NameError(evidence/b/B-repro-before.txt) | 成り立つ(差分は defmcp-tool の 3 行・evidence/s1)。表を通らない defmcp-tool は新しい検が断る(evidence/b/B-after-fix.txt) |
| S2 simulation: 型検査の投影の `_doeff_do` の型の出所を替える | 変わるのは M2 だけ | `_doeff_do` は成り立つ。ただし盲検 A: 同じ「頭に 1 度だけ」の不変条件が McpToolDef では破れていた(tool 65 個で Unknown・evidence/a/A-repro-before.txt) | 成り立つ(evidence/s2・evidence/a/A-after-fix.txt) |
| S3 effects: 実行時の `do` の出所を替える | M1 の表 1 か所で defmcp-tool も替わる(限界: M6・`_do-reference`) | 成り立つ(evidence/s3)。ただし盲検 B: それを確かめる検が無かった | 成り立ち、検が守る(test_helper_imports_table.py)。盲検 A が限界に 2 つ足した(`_validation-imports`・static_types の再公開) |

## 責務(盲検の後)

盲検 A で、「補助の名(M1・M2)」と「定義に固有の import(M3)」の分け方が境として成り立たない事が分かった。型検査のための展開では、1 つの名の宣言を 64 以下に保つ不変条件が、macro が出すすべての import に掛かる。だから境を次のように引き直した。

| id | 責務 | 盲検の前からの変更 |
|---|---|---|
| M1 | 実行時の展開の補助の import の表 `_helper-imports` | 変えない |
| M2 | 型検査のための展開で、定義の macro が名で読む物の import を module の頭に 1 度だけ置く(static_view.STATIC_HELPER_IMPORTS・static_check.with_static_helpers) | 持つ名に defmcp-tool の McpToolDef / McpParamSchema(`_doeff_<名>`・MCP_STATIC_IMPORTS)を足した。`set!` の `_doeff_Put`・`_doeff_Some`(SESSION_STATIC_IMPORTS)と同じ作法 |
| M3 | 定義の macro。型検査のための展開では import を 1 つも出さない(defmcp-tool)。実行時は補助を M1 から取り、固有の import(doeff.mcp)だけを自分で出す | 「型検査のための展開で固有の import も出さない」を足した |
| M4 | 置き場 `_scoped-definition` | 変えない |
| M5 | 検 | 下の「強制方法」の 3 つを足し・強めた |
| M6 | handle.hy の `_do-import`(範囲の外) | 変えない |

## 公開の契約(盲検の後)

- 定義の macro の展開が名で読む補助は、実行時は M1 が import する。表を差し替えると、defk・defp・deftest・defmcp-tool の展開は module・class の本体・関数の本体のどこでも差し替えた import を出し、defk の関数と defmcp-tool の handler は差し替えた表の `do` で包まれる。
- 型検査のための展開の defmcp-tool は import を 1 つも出さない。展開の import の文は module の直下の頭(doeff-hy-check が置く)だけで、どの名も 1 度だけ宣言される。
- defmcp-tool の実行時の展開は変えない(6 つの展開の比較・evidence/expansion/six-expansions.txt — 実装の版から直した版への差は型検査の 3 つだけ)。

## 盲検 A — 失敗例・再現・直し

- 失敗例(blind/blind-a-return.md): 型検査のための展開の defmcp-tool は tool ごとに `from doeff.mcp import McpToolDef, McpParamSchema` を出す。doeff の module なので重ねの畳みに掛からず、tool を 65 個持つ module か class で McpToolDef の宣言が 64 を超え、tool の値が Unknown になり、tool を使う側の型の誤りを黙って通す。設計者の検(`_` で始まる名だけを数える)は緑のまま。
- 再現(evidence/a/A-repro-before.txt・実装の版): module・class とも N=2・64 では `(defn #^ int last-tool-name [] (. tool-N name))` の reportReturnType が出て、N=65・70 では赤が 0。投影の `from doeff.mcp import` の文は N 個。成立。
- 直し: 型検査のための展開の defmcp-tool は import を出さず、McpToolDef / McpParamSchema を `_doeff_McpToolDef` / `_doeff_McpParamSchema` で引く。名は M2 が頭に 1 度だけ置く(読む時だけ — `_only_used`)。盲検 A が挙げた案 (b)(後処理で doeff.mcp を畳む)は class の本体で効かない事を盲検 A 自身が測っていたので採らなかった。案 (a) のうち「M1 も変わる」は当たらなかった — 実行時の展開は今までどおり doeff.mcp を import し、M1 の表は変えていない。
- 再検証(evidence/a/A-after-fix.txt): module・class とも N=2・64・65・70 のすべてで reportReturnType が出て、投影の `from doeff.mcp import` の文は 1 つ。
- 予想の範囲との差: S2 の主張(M2 だけが変わる)は `_doeff_do` については成り立っていた。McpToolDef は設計が M3 の固有の知識に分けていたので主張の外だったが、同じ共同の不変条件(頭に 1 度だけ)を破っていた。隠すはずの知識の漏洩ではなく、共同の不変条件に持ち主がいなかった事の欠け。M2 の持ち物を広げて持ち主にした。

## 盲検 B — 失敗例・再現・直し

- 失敗例(blind/blind-b-return.md): 「使わない import を減らす」掃除として、defmcp-tool が `(if (_static-view?) '(do) '(import doeff.do [do :as _doeff_do]))` と表を通らずに綴る。型検査のための展開は同じなので K1・K2 は通り、実行の検 K3・K4 も通る。その下では表を差し替えても defmcp-tool だけが取り残され(S3 が崩れる)、文の guard を足すと NameError(S1 が崩れる)。
- 再現(evidence/b/B-repro-before.txt・実装の版に違反を当てた木): K1 1 passed・K2 2 passed・K3 8 passed・K4 32 passed。表の差し替えで defk は追従、defmcp-tool は 3 か所とも追従しない。成立。
- 直し: packages/doeff-hy/tests/test_helper_imports_table.py。表を差し替える(`do` だけを probe の module から取る)と、4 つの定義の macro × 3 つの置き場の実行時の展開の `_doeff_do` の import がすべて差し替えた物になる事(12 件)と、defk の関数と defmcp-tool の handler が差し替えた表の `do` で包まれる事(6 件)を検める。
- 再検証(evidence/b/B-after-fix.txt・直した版に同じ違反を当てた木): 新しい検が 6 failed(defmcp-tool の 6 件)、提示していた 4 つの検は通ったまま。違反を戻すと 18 passed。
- 盲検 B が未測として挙げた変種(handler の decorator を表の `_doeff_do` ではなく fnk 用の `_do-reference` にし、import は表のまま)も当てた(evidence/b/B-do-reference-variant.txt): 印の検が 3 failed(defmcp-tool の 3 つの置き場)、import の検と提示していた 4 つの検は通ったまま。

## シナリオごとの実測

- S1(evidence/s1/S1-guard-fix-and-base.txt): 直した版に S1 を当てると、差分は defmcp-tool の handler の組み立ての 3 行(guard の wrap と `_install-guard-globals` の呼び)だけで、tool だけの module で文の位置の `(Pure 1)` を guard が止め(RuntimeError)、関わる検は全部通る。基準の版に同じ S1 を当てると NameError(`_install_guard_globals`)で、defmcp-tool の import も別に直す必要があった。
- S2(evidence/s2/S2-typed-do-fix-and-base.txt): 直した版で M2 の頭の `_doeff_do` を型の違う probe の `do` に替えると、module の tool の handler・class の tool の handler・後ろの defk がすべて `StaticProgram[...]`。基準の版では tool の import が後から `_doeff_do` を上書きし、3 つとも core の `Expand[...]` のまま。
- S3(evidence/s3/S3-follow-base-impl-fix.txt): 表を差し替えると、基準の版では defmcp-tool が 3 か所とも取り残され、実装の版と直した版では 3 か所とも追従する。
- 盲検 A が S3 の限界に足した 2 つ(実測ではなく code の読み — 設計者も読んで確かめた):
  - macros.hy の `_validation-imports` も、実行時は `doeff.do`、型検査では static_types という同じ決めを別に持つ。M6・`_do-reference` と同じく、M1 と一緒に替える所。
  - static_types.pyi は `from doeff.do import do as do` で実行時の `do` を再公開する(agora-redesign #2321 — 型検査の `do` と実行時の `do` が同じ型である事の共同の不変条件)。実行時の `do` の module を替えるなら M2 の static_types.pyi も替わる。これは隠す知識の漏洩ではなく、共同の不変条件の配線。

## 強制方法(実装後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 実行時の展開の補助の import は M1 の表から取る(M3 → M1) | 表を差し替えて展開を読む契約の検と、包んだ `do` の印を読む実行の検 | packages/doeff-hy/tests/test_helper_imports_table.py(2 つの検・18 件) | pytest。日次の全体テストは `packages/<名>/tests` を package ごとに集める(tests/test_daily_test_population.py の `_population_roots`)。変更時の検の選び手は scripts/run_changed_tests.py | defk・defp・deftest・defmcp-tool だけ。fnk・do!(`_with-scoped-helpers` と `_do-reference`)・handle.hy の defhandler / handle(`_do-import`)・`_validation-imports` は検めない。印の検は defk と defmcp-tool だけ(deftest の `do` は検を走らせた時に包むので、定義の時の印では読めない) |
| 型検査のための展開の defmcp-tool は import を出さず、どの名も 1 度だけ宣言する(M3 → M2) | 投影の AST を読む契約の検: import の文は module の直下だけ・束ねる名に重なりが無い・`_doeff_do`・`_doeff_McpToolDef`・`_doeff_McpParamSchema` が在る | test_static_check.py::test_defmcp_tool_imports_nothing_of_its_own_in_the_static_view | pytest(同上) | defmcp-tool だけ。defk の同じ決まりは #1686 の検(test_defk_calls_stay_programs_past_sixty_four_definitions)が持つ |
| 1 つの名の宣言が 64 を超えない(M2 の不変条件) | pyright を使う回帰の検: tool 70 個の後ろで、defk の呼びが Program のまま、tool の値の型の誤りが捕まる | test_static_check.py::test_defk_calls_stay_programs_past_sixty_four_mcp_tools | pytest(pyright が在る時だけ) | pyright の閾(64)と版(この環境は 1.1.411)に依る |

静的な import の層の検査(semgrep など)は使わない: 表を通すかは macro の展開の結果で決まり、source の綴りの規則では「型検査のための展開で何が出るか」「表を替えた時に何が出るか」を表せないため。

検の確かめ(evidence/checks/checks-three-versions.txt): 直した版の検を、macros.hy・static_view.py だけ実装の版に戻すと 2 failed(盲検 A の欠陥)、基準の版に戻すと 9 failed、直した版で 21 passed。

## 範囲の外の所見(直していない)

- 型検査のための展開の defmcp-tool の handler の引数に注記が無い(schema の `:type "string"` を写していない)。strict では tool 1 つにつき、書き手に直せない Unknown の赤が出る(盲検 A も実測)。
- 実行時の `do` を import する決めは、M1 の他に handle.hy の `_do-import`・fnk の `_do-reference`・`_validation-imports` が別に持つ(上の S3)。

## 未確認事項

- 盲検 A・B の subagent が実際に使った model と effort(blind/blind-meta.md)。
- 65 個以上の defmcp-tool を 1 つの module か class に持つ利用者が今いるか(agora・agora-controllers・dotfiles の共有の checkout の origin/main には defmcp-tool の使い手が無かった。全数ではない)。
- pyright の本番の版で閾が 64 のままか(この環境は 1.1.411)。

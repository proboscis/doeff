# 盲検 B への依頼

まず `/home/ubuntu/.worktrees/doeff-wt-dc-ln19/docs/design-checks/lt-N19BACF5SXDRMKQ7EZD3AZA8FC/blind/blind-common.md` を読んでください(固定済みの要件、責務、公開契約、将来変更についての主張、実コードの在り処、実行の command、守る事)。

## 提示する検査(実体・設定・検出範囲)

| 検査 | 実体 | 実行の command | 検出範囲 |
|---|---|---|---|
| K1 | `packages/doeff-hy/tests/test_static_check.py::test_defmcp_tool_reads_do_from_the_module_head` | `uv run pytest packages/doeff-hy/tests/test_static_check.py -k test_defmcp_tool_reads_do_from_the_module_head -q` | module・class の本体・関数の本体に 1 つずつ置いた defmcp-tool の型検査のための展開(`static_check.project` の text)で、`_` で始まる別名を import する from-import の文が、module の直下の `doeff_hy.static_types`・`doeff_hy.macros` からの from-import だけか。`_doeff_do` の宣言(別名)が 1 つか |
| K2 | `test_static_check.py::test_defk_calls_stay_programs_past_sixty_four_mcp_tools[False/True]` | `uv run pytest packages/doeff-hy/tests/test_static_check.py -k sixty_four_mcp_tools -q` | defmcp-tool 70 個の後ろの defk の呼びを `Spawn` に渡し `<-` で束ねる検体を doeff-hy-check(pyright・既定の設定)に掛け、赤が無いか(False)・束ねの型の食い違いの reportAssignmentType 1 つだけか(True)。pyright が無ければ skip |
| K3 | `tests/public_api/test_mcp_tool_macro.py` | `uv run pytest tests/public_api/test_mcp_tool_macro.py -q` | defmcp-tool の実行時の展開: McpToolDef の値・params の schema・handler を run で走らせた答え |
| K4 | `tests/test_nested_definition_scope.py` | `uv run pytest tests/test_nested_definition_scope.py -q` | 関数の本体の中の defk・defp・deftest・defmcp-tool の実行時の展開が、外の関数の局所の名を作らない事。module の段の定義を包まない事 |

## 依頼

あなたは静的検査を通過する責務違反の失敗例を生成してください。
合理的な機能追加・修正として実装されそうで、提示した検査をそのまま実行しても通るのに、
知識・判断・状態・effectsの所有者や公開契約を侵す実装を1つ具体化してください。
一般的な設計レビューや承認判定は求めていません。

検査の無効化、baselineの改変、対象除外、as any、ignoreやlint抑制、根拠のないcast、
動的importによる回避は使わないでください。小さい既存例に対する抜け道だけでなく、
通常の実装者が機能を実現しようとして書きそうなコードで示してください。

返答には次を含めてください。
1. 現実的な機能要求と、提案する具体的なコードまたは最小差分。
2. 本来の責務所有者と、違反する契約。何の判断・知識がどこへ移るか。
3. 提示された各検査がそのコードを拒否しない理由。未提示の検査は推測しないこと。
4. 同じ設定で検査通過と責務違反の両方を確かめる最小の入力・手順・期待結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
実行していない検査を通ったと断定しないでください。失敗例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。

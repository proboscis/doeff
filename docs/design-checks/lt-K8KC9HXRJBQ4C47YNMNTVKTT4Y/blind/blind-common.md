# 盲検 A・B の共通の材料(設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y)

## 対象

- repo: doeff(Python と Hy の effect の library)。対象の版 `d8db309f31b816f539dd226ba1fdab5b3a37f44d`。
- 読む木(読むだけ・変えない): `/home/ubuntu/.worktrees/doeff-blind-ki973b-d8db309`(この版そのもの。`.venv` は作り済み)。
- 固定した要件・責務・公開の形・将来の変更についての主張・強制方法:
  `/home/ubuntu/.worktrees/doeff-wt-ki-973b-dc/docs/design-checks/lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y/design-before-blind.md`(読むだけ)。

## 判断に要る実コード(読む木の中の path)

| 何 | path |
|---|---|
| M1・M2・M4・M5・M6 | `packages/doeff-hy/src/doeff_hy/macros.hy`(`RuntimeDo`・`_runtime-do`・`_runtime-do-import`・`_helper-imports`・`_do-reference`・`_validation-imports`・`_mcp-handler-param`・`_parse-mcp-params`・`defmcp-tool`) |
| M3 | `packages/doeff-hy/src/doeff_hy/handle.hy`(`_do-import`) |
| M7 | `packages/doeff-hy/src/doeff_hy/static_view.py`(`STATIC_HELPER_IMPORTS`・`MCP_STATIC_IMPORTS`)、`packages/doeff-hy/src/doeff_hy/static_check.py`(doeff-hy-check) |
| M8 | `packages/doeff-agents/src/doeff_agents/handlers/mcp_server_loop.hy`(`_tool-result-with-stack`)、`doeff/mcp.py`(`McpToolDef`・`McpParamSchema`) |
| M9 と他の検 | `packages/doeff-hy/tests/test_helper_imports_table.py`、`packages/doeff-hy/tests/test_static_check.py`(`test_defmcp_tool_handler_params_carry_the_schema_types` と `_typed_mcp_tool`)、`tests/public_api/test_mcp_tool_macro.py` |
| 文書 | `docs/12-mcp-tools.md` |

## 検の実体と実行のしかた

| 検 | 実行 | 検出する物 |
|---|---|---|
| 表の検(M9) | `uv run pytest packages/doeff-hy/tests/test_helper_imports_table.py -q` | 一覧 `_DEFINITIONS` の 8 macro × module・class・関数の本体で、`_runtime_do` を差し替えた時に展開が `do` を引く import と `__import__` の module が差し替えた物だけか。差し替えた do が実際に包むか |
| 注記の型検査の検 | `uv run pytest "packages/doeff-hy/tests/test_static_check.py::test_defmcp_tool_handler_params_carry_the_schema_types" -q`(pyright が要る — 読む木の `.venv` に在る) | doeff-hy-check `--strict` で、6 つの型と必須でない引数の tool の赤が 0 件か。`str | None` を `str` へ渡すと reportArgumentType 1 件か |
| MCP の macro の検 | `uv run pytest tests/public_api/test_mcp_tool_macro.py -q` | handler の引数の注記の値、知らない `:type` の SyntaxError、McpToolDef の組み立て |
| 型検査そのもの | `uv run doeff-hy-check --root <検体の dir> --strict --json <検体の .hy>` | pyright strict の赤(JSON の `severity` が `error` の行) |

通常の変更の時の経路: 上の pytest は 4 時間に 1 度の全体テストと、変更した file から選ぶ検(`scripts/run_changed_tests.py`)で走る。マージの列の前の自動検査は衝突の印の走査だけ。

## 守る事

- 読む木と、`/home/ubuntu/.worktrees/` の他の木を変えない(`git` の commit・stash・worktree の追加もしない)。code を変えて試すなら、読む木を `/tmp/ki973b-blind-<a か b>/tree` へ `cp -a` で写し、写しの中で変える(写しの中で `uv run` すると `.venv` を作り直す — 数十秒かかる)。実験の file は `/tmp/ki973b-blind-<a か b>/` の下に置く。
- Python は `uv run` を通して走らせる(機体の python3 や `.venv/bin/python` を直に使わない)。
- 全数の検(`pytest` を引数なし・dir ごと)を走らせない。上の検と、自分で作った小さい検体だけ。
- 他の会話への連絡、GitHub・作業ボードへの書き込みをしない。
- 最後の返答に、依頼文の 1〜5 を全部書く(作った実験の file の path も)。

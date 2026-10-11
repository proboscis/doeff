# 盲検の共通の材料 — doeff-traverse の例を走らせる検

## 対象

- repo: doeff。対象の版 = 2cff14d6a353e31b3e894c36fde37d698fb8e344(main)。
- 読める木: `/home/ubuntu/.worktrees/doeff-wt-ex09-design-check`(この版 + 設計の記録)。**この木の file を変えない。**
- 要件・責務・公開の契約・将来の変更についての主張: 上の木の `docs/design-checks/lt-QV9FJWHA394WE5MH3QVJFQBHV5/design.md` の **§1〜§4 だけ**を読む。
  §0・§5 と、`docs/design-checks/` の下の他の file・他の dir は読まない(別の検証の記録で、この依頼の材料ではない)。

## 実コード(読む所)

| 何 | path(上の木の中) |
| --- | --- |
| 例を走らせる検(今回 新規) | `packages/doeff-traverse/tests/test_examples_run.py` |
| 例 | `packages/doeff-traverse/examples/*.hy` |
| traverse の lib | `packages/doeff-traverse/doeff_traverse/`・`packages/doeff-traverse/pyproject.toml` |
| macro | `packages/doeff-hy/src/doeff_hy/macros.hy` |
| compile の置き場と無効化 | `packages/doeff-hy/src/doeff_hy_bytecode_guard/` |
| 日次の package の検の走らせ方 | `Makefile` の `test-packages`・`.agents/land-queue.toml` の `[gate]` |
| pytest の設定 | `pyproject.toml` の `[tool.pytest.ini_options]` |

## 実行してよい事

- 実験は自分だけの木で行う: `git -C /home/ubuntu/.worktrees/doeff-wt-ex09-design-check worktree add --detach /tmp/<自分の名>-ex09/tree 2cff14d6a353e31b3e894c36fde37d698fb8e344` で切り、
  その木で `make sync`(uv の cache の環境変数はそのまま使う・上書きしない)の後、`uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q` のように**この検だけ**を走らせる。
  終わったら `git -C /home/ubuntu/.worktrees/doeff-wt-ex09-design-check worktree remove --force /tmp/<自分の名>-ex09/tree` で外す。
- Python は `uv run` を通す(機体の python3・`.venv/bin/...` の直呼びは使わない)。
- してはいけない事: 上の読める木・`~/repos/doeff` の file を変える、commit・push、repo 全体の検(`make test`・`make test-packages` の全部)、他の会話への連絡。

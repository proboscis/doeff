# 盲検の共通の材料(設計検証 lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD)

## 読む物(読むだけ — 書き換えない)

- 主張: `/home/ubuntu/.worktrees/doeff-wt-ki-f8a5-dc/docs/design-checks/lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD/design-before-blind.md`
  (要件・責務・公開契約・変更シナリオと主張・強制方法・限界)
- 実コード(doeff main 86ecd1344 の木 `/home/ubuntu/.worktrees/doeff-wt-ki-f8a5-dc`):
  - `packages/doeff-traverse/tests/test_examples_run.py`
  - `packages/doeff-traverse/tests/run_example_failing_a_case.py`
  - `packages/doeff-traverse/examples/*.hy`・`packages/doeff-traverse/examples/expected/*.txt`
  - `packages/doeff-traverse/doeff_traverse/`(effects.py・handlers.py)・`doeff/run.py`
  - 検の経路の宣言: `pyproject.toml` の `[[tool.doeff.contract-tests]]`(prefix `packages/doeff-traverse/`)
- `docs/design-checks/` の下の、この依頼(lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD)の外の記録は読まない。git log の本文も読まない。

## 実験の仕方

- 共有の木(`/home/ubuntu/.worktrees/doeff-wt-ki-f8a5-dc`・`/home/ubuntu/repos/doeff`)の file を書き換えない。
- 実験の木は自分の dir に作る: `git -C /home/ubuntu/repos/doeff worktree add --detach /tmp/ki-f8a5-blind-<a か b>/tree 86ecd1344`
  の後、その木で `make sync`(uv の cache が効くので数秒〜数十秒)。木の中なら自由に書き換えてよい。終わっても木は消さなくてよい。
- 検は木の中で `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q`(子 21 本・10〜20 秒)。
  -k で絞ってよい。repo 全体の検は走らせない。
- Python は uv を通す(`uv run python …`)。機体の python3 は使わない。
- 他の会話への連絡・GitHub・agora の命令は使わない。
- 最後の返答に、依頼文が求める 1〜5 を全部書く(実験の file を名指すなら、その path も書く)。

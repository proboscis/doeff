# 盲検の共通の材料(lt-6NTWFZDM0EAGNFGW7TYZ4RM5ES)

## 読む物

- 設計の本文: 材料の作業木 `/home/ubuntu/.worktrees/doeff-wt-examples-premerge-dc` の `docs/design-checks/lt-6NTWFZDM0EAGNFGW7TYZ4RM5ES/design.md` の §1〜§4 だけ
  (固定した事実・責務・事前の主張・強制方法)。材料の作業木は読むだけにしてください(実験の作業木には材料の file はありません)。
- 実コード(対象の版 = doeff の main の df9e627561c91221180ee02e11fc9846e767d962。変更の差分は `git show df9e62756`):

| 何か | path |
| --- | --- |
| 契約の検の表 | root の `pyproject.toml` の `[[tool.doeff.contract-tests]]`(頭の註を含む) |
| 変えた所の検の入口 | `scripts/run_changed_tests.py`(`make test-changed` — `Makefile` の `test-changed`) |
| 表と木のずれの検(今回 1 つ足した) | `tests/test_contract_table_follows_packages.py` |
| 入口の検 | `tests/test_run_changed_tests.py` |
| 例の検 | `packages/doeff-traverse/tests/test_examples_run.py` |
| 例と期待の出力 | `packages/doeff-traverse/examples/*.hy`・`packages/doeff-traverse/examples/expected/*.txt` |
| macro の持ち主 | `packages/doeff-hy/src/doeff_hy/`(`macros.hy` ほか) |
| マージの列の宣言 | `.agents/land-queue.toml`(`mode = "focus"`・門は衝突の印の走査だけ) |

`docs/design-checks/` の下の他の file(他の設計検証の記録・この dir の他の file)は読まないでください。

## 決まり

- 共有の source(`~/repos/doeff` と他の作業木)を変えないでください。実験は自分用の作業木を切って行います:
  `git -C /home/ubuntu/repos/doeff worktree add --detach /home/ubuntu/.worktrees/doeff-wt-blind-<A か B>-6ntw df9e627561c91221180ee02e11fc9846e767d962`
  の後、その木で `make sync`(uv の cache の環境変数は上書きしない)。`/tmp` の下に木を作ると置き場の規律の hook に止められます。
- 検は狙った file だけを走らせます: `uv run --no-sync pytest -q -m 'not e2e' <file または file::検の名>`。全体の検(`pytest` を引数なし・`make test`・
  `make test-packages`)は走らせないでください。
- 入口は `uv run --script scripts/run_changed_tests.py --base HEAD --linter /home/ubuntu/.cache/doeff-linter-snapshots/a9896bbc811f282e6a64457d084990e37fe6e94c/doeff-linter`
  で、作業木の未 commit の差分を変えた file として走らせられます(上限は既定 60 秒・`--budget <秒>` で変えられる)。
  機体は負荷が高く(load average 100 前後)、入口 1 回に 1 分ほどかかります。
- 他の会話への連絡、commit の push、マージの列への登記はしないでください。
- 返答は日本語で。推測と実測を分けて書いてください。

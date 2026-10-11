# 盲検 A・B の共通の材料

## 対象

- repo: doeff。対象の版は commit `c9e1622b589dcde007b42baaa5817ea9332b61e7`(以下 c9e1622b5)。
- 読める木(読むだけ・書かない): `/home/ubuntu/.worktrees/doeff-wt-dc-4z5w`。この木の code は c9e1622b5 と同じ。
- 固めた要件・責務・主張・強制方法: 同じ木の `docs/design-checks/lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF/design-before-blind.md`(§1〜§5 を全部読む)。

## 判断に要る実の code

| 何 | path(木からの相対) |
| --- | --- |
| 例の検(example-check) | `packages/doeff-traverse/tests/test_examples_run.py` |
| 例(examples) | `packages/doeff-traverse/examples/*.hy` |
| 期待の出力 | `packages/doeff-traverse/examples/expected/*.txt`(`…` は U+2026 の 1 文字) |
| traverse-lib の handler | `packages/doeff-traverse/doeff_traverse/handlers.py`・`collection.py`・`effects.py` |
| macros | `packages/doeff-hy/src/doeff_hy/macros.hy`(traverse・for/do・`<-` の展開) |
| 子の締切の係数 | root の `conftest.py` の fixture `deadline_scale_factor` |

`docs/design-checks/` の下の、この依頼の外の記録(他の id の dir)は読まない。

## 検の走らせ方と範囲

- 検は `packages/doeff-traverse/tests/test_examples_run.py` の 1 file だけを走らせる(全数の検査は走らせない)。日次の全体検証の段 packages が `pytest packages/doeff-traverse/tests` として走らせる。マージの列は検を走らせない。
- 実験は自分の dir(A は `/tmp/lt4z5w-blind-a/`、B は `/tmp/lt4z5w-blind-b/`)の下だけで行う。木の写しは次で作れる:

```sh
mkdir -p /tmp/lt4z5w-blind-X/tree
git -C /home/ubuntu/.worktrees/doeff-wt-dc-4z5w archive c9e1622b5 | tar -x -C /tmp/lt4z5w-blind-X/tree
cd /tmp/lt4z5w-blind-X/tree && make sync            # uv の置き場は環境変数のまま使う(上書きしない)
uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q
```

- Python は uv を通して走らせる(`uv run …`)。`.venv/bin/python` を直に呼ばない。
- 例 1 本は `uv run python -m hy packages/doeff-traverse/examples/<名>.hy` で走る。

## してはいけない事

- 読める木・`~/repos/doeff`・他の作業木を書き換える事(写しの木の中だけを変える)。
- git の commit・push・branch の作成。
- 全数の検査(repo 全体の pytest・型検査)。
- 他の会話への連絡(`agora`・`ai` の命令)。

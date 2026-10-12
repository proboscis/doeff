# 盲検 A・B に渡す材料(lt-TH2ZXPJ9QPXBB8EV6SNEWN1N89)

## 対象の版
- doeff: cc8f9e7dfe09dba03b6ecb488222cb84c68e449e(基底 6f439141223231fd40ba86a7640f3548409c7a6d からの 2 commit)。読む木 = `/home/ubuntu/.worktrees/doeff-wt-ki-26eb-dc`(この版を含む main の先端 — 対象の file はこの版から変わっていない)。
- 枠の正本: `/home/ubuntu/dotfiles/agent/tests/machine_check_slots.py` と隣の `machine_check_slots.json`(dotfiles の先端・読むだけ)。pytest の受付 `/home/ubuntu/dotfiles/agent/tests/broad_run_admission.py` の `hold_check_slot`。
- 呼び手(agora-controllers の origin/main・読むだけ): `git -C /home/ubuntu/repos/agora-controllers show origin/main:scripts/hy_type_check.hy`・`…:scripts/hy_type_trend.hy`・`…:scripts/check_slot.hy`。

## 要件(固定)
- 受入: 会話が直に doeff-hy-check を起動しても、機体の検の枠を取ってから走る事を、枠を埋めた失敗ケースの検で確かめて doeff main で緑(直す前は枠を取らずに走る)。
- 前提: 1 枠 = 検の process 木 1 つ。枠を持つ process の子孫は 2 つ目を取らない。値(K・置き場・待ちの上限)は正本の宣言 `machine_check_slots.json` 1 つが持ち、env で替える口は作らない。
- 利用状況: 同じ Pod で会話が数十並ぶ。doeff-hy-check は 1 本で展開の子 4 本と pyright を起こし、RSS 約 10 GiB。撃つ道 = 会話の直(`uv run doeff-hy-check …`・`python -m doeff_hy.static_check …`)/ agora-controllers の `hy_type_check.hy gate` の子(gate が枠を持つ)/ 日次の `hy_type_trend.hy` / pytest の中の in-process の `main([...])`。doeff は使い手の repo に pin で固定され、正本は dotfiles の先端を追う。

## 責務と公開契約・設計者の主張・強制方法
`/home/ubuntu/.worktrees/doeff-wt-ki-26eb-dc/docs/design-checks/lt-TH2ZXPJ9QPXBB8EV6SNEWN1N89/design-pre-blind.md` の「責務」「変更シナリオと主張」「強制方法」の節(この file と同じ dir)。

## 読む実コード(doeff の木からの相対)
- `packages/doeff-hy/src/doeff_hy/check_slot.py`(新)
- `packages/doeff-hy/src/doeff_hy/static_check.py` の `main`(枠を取る所は `take_slot(Path.home(), …)` の行の前後)
- `packages/doeff-hy/src/doeff_hy/env_places.py`
- `packages/doeff-hy/tests/test_static_check_slot.py`
- `.agents/code-quality.d/doeff-hy-check-slot.json`(と同じ dir の `doeff-hy-env-places.json`)
- `git -C /home/ubuntu/.worktrees/doeff-wt-ki-26eb-dc show cc8f9e7df~1 cc8f9e7df --stat` で変更の全体

## 検査の実体・設定・検出範囲
| 検査 | 実体 | 撃ち方(読み・実験用) | 検出範囲 |
|---|---|---|---|
| 枠の検 4 本 | `packages/doeff-hy/tests/test_static_check_slot.py` | 木の root で `uv run --no-sync pytest packages/doeff-hy/tests/test_static_check_slot.py -q` | 正本の写し(K = 1・検体の dir)を検体の HOME に置き、別 process が正本の `run` で枠を持つ。正本の無い宿では skip(4 本目を除く) |
| doeff-linter | `packages/doeff-linter`(DOEFF004 = os.environ・os.getenv の参照)・設定 `pyproject.toml [tool.doeff-linter]` | pre-commit の hook(基点に無い error だけ止める)・日次の lint の段 | Python の source |
| ruff | `pyproject.toml [tool.ruff]` | `uv run --no-sync ruff check <file>` | Python |
| code-quality(fast) | `.agents/code-quality.json` と `.agents/code-quality.d/*.json` の module 契約 | `sh ~/dotfiles/agent/venv-run.sh ~/dotfiles/agentcli code-quality --repo <木> --scope changed --base <分岐点>` | import の依存の向き(module 契約に無い内部の依存 = dependency-uncontracted)ほか。型検査はこの段に無い |
| semgrep | `.semgrep.yaml` | pre-commit の hook | 規則の paths |

## 実験の場所(使うなら)
共有の木・`~/repos/doeff`・機体の本物の枠(`/tmp/agora-check-slots`)は変えない・埋めない。試すなら自分専用の dir に写して撃つ:
`mkdir /tmp/blind-<A|B>-ki26eb && git -C /home/ubuntu/.worktrees/doeff-wt-ki-26eb-dc archive cc8f9e7df | tar -x -C /tmp/blind-<A|B>-ki26eb && (cd /tmp/blind-<A|B>-ki26eb && uv sync --group dev)`。repo 全体の検・全数の検は撃たない。

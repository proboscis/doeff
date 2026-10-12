# 盲検 A・B に渡す材料(設計検証 lt-W8F3HD41FWBR4PS26EDSEJDJMT)

対象版: doeff main c8fd2c26a(作業木 /work/toolset/worktrees/doeff-wt-ki-2320-dc は origin/main = c8fd2c26a)。
変更の差分: `git -C /work/toolset/worktrees/doeff-wt-ki-2320-dc show c8fd2c26a`(3 file・+14/−3)。

## 固定した要件・責務・契約・主張

同じ dir の design-pre-blind.md の全文(要件・原因・責務の表・変更シナリオ S1〜S6 と主張・強制方法の表)。

## 読んでよい実コード(読むだけ)

- packages/doeff-hy/src/doeff_hy/check_slot.py(M-slot)
- packages/doeff-hy/tests/test_static_cache.py(M-place-guard は関数 test_doeff_hy_sources_do_not_read_their_place と表 PLACE_LINES)
- packages/doeff-hy-pytest/src/doeff_hy_pytest/budget.py(M-budget。警告は _record の warnings.warn)
- packages/doeff-hy-pytest/tests/test_budget.py(M-budget-test は test_report_mode_warns_and_lists_but_stays_green)
- packages/doeff-hy/tests/test_static_check_slot.py(M-slot-scene は _scene)
- /work/toolset/dotfiles/agent/tests/machine_check_slots.py(M-canon・別の repo。policy_of が宣言を検める)
- root の conftest.py・pyproject.toml の [tool.pytest.ini_options]

## 検査の実体・設定・検出範囲

- M-place-guard: `uv run pytest packages/doeff-hy/tests/test_static_cache.py::test_doeff_hy_sources_do_not_read_their_place`
  — doeff_hy の package の下の .py・.hy・.pyi の全行のうち、`__file__` か `__cached__` を含み、`#`・`;` で始まらない行を、
  表 PLACE_LINES(file 名・行の中身の組)の外なら拒む。
- M-budget-test: `uv run pytest packages/doeff-hy-pytest/tests/test_budget.py::test_report_mode_warns_and_lists_but_stays_green`
  (外側の走りに `-W ignore::pytest.PytestWarning` を付けた形でも走らせる)。
- M-slot-scene: `uv run pytest packages/doeff-hy/tests/test_static_check_slot.py`(機体に ~/dotfiles の正本が無ければ skip)。
- 変更時の品質検査: `sh ~/dotfiles/agent/venv-run.sh ~/dotfiles/agentcli code-quality --repo <作業木> --scope changed --base <基底>`
  (ruff ほかの速い規則。型検査は日次)。

## 守ること

作業木・共有の clone の file を変えない。git の commit・branch の作成をしない。全数の検査を走らせない。他の会話へ連絡しない。
実験が要るなら手順と期待する結果を書くだけでよい(再現は依頼側が行う)。

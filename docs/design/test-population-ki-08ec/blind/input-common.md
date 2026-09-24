# 盲検 A・B に共通で渡す入力

対象版: doeff origin/main 9df950b5477f9456216ab447b1c37879981425ec(読み取り専用の複製: ~/.worktrees/doeff-wt-ki08ec-population-plan)

## 読む材料(この順)

1. 要件・責務・公開契約・提案の形: `docs/design/test-population-ki-08ec/design.md`
2. 将来の変更についての主張と前提・予想した変更範囲: `docs/design/test-population-ki-08ec/claims-before-blind.md`
3. 実コード(現状 — 提案はまだ入っていない):
   - `.agents/land-queue.toml`(`[gate].full` の現状 = 1 本の文字列・その頭注)
   - `Makefile`(`sync` / `test-packages` / `test-rust`)
   - `pyproject.toml`(`[tool.pytest.ini_options]` の testpaths)
   - `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy`(新しい条を足す先)
   - `docs/adr/enforcement-ledger.json`・`scripts/check_enforcement_ledger.py`(台帳)
   - `packages/doeff-agents/conformance/README.md`(母集団外の検査群)
4. 外部の仕組み(変更しない・dotfiles):
   - `~/dotfiles/agentcli/src/agentcli/land_config.py`(`FullStage`・`full_stages` — 処理ステージの宣言の読み替え)
   - `~/dotfiles/agentcli/src/agentcli/land.py` の 1670〜1760 行(処理ステージの宣言の註・`full_command`)と
     24590〜24740 行(`run_full_stages`・`fold_stage_runs`)
   - `~/dotfiles/agentcli/src/agentcli/remote_check.py`(遠隔実行・同期と削除・錠・手元への倒し込み)

## 検査の実体と検出範囲(提案 — 実装前)

- 宣言の pin: ADR-DOE-ENFORCE-001 に足す deftest が `.agents/land-queue.toml` を tomllib で読み、design.md §3.4 の 1. を検める。
- 挙動のテスト: `tests/test_daily_test_population.py` が偽の runner で `make test-packages` を実際に走らせ、design.md §3.4 の 2.・3. を検める。
- どちらも root の既定の pytest(testpaths に `tests` と `docs/adr`)で収集され、日次の `root` 処理ステージで毎日走る。
- 台帳 R5 / R7(`scripts/check_enforcement_ledger.py`)は ADR の law / deftest の数を突合する。

## 制約

- 共有の source(~/repos 以下・~/dotfiles・上の worktree)を変更しない。試す時は /tmp の下へ写して行う。
- 全数のテスト・日次の全体検証・`make test-packages` の実走(実 pytest)・`make sync` は撃たない。1 回 60 秒以内の焦点の実行だけ。
- 他の会話へ連絡しない(ai tell / herdr / agmsg を使わない)。

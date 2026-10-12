# 盲検の共通の材料(lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97)

対象の repo(読むだけ・変更しない): /home/ubuntu/.worktrees/doeff-wt-ki-6828-mut(doeff・版 27f35483076413668aa7b2ca32949b170a0987b4 に固定した作業木)。変更の前の版は 94287ee5646ae0ffbd790dcc47c9760f0de24a23(`git -C /home/ubuntu/.worktrees/doeff-wt-ki-6828-mut diff 94287ee56 27f354830` で差分を読める)。

要件・責務(module)・変更シナリオと主張・強制方法は、この dir の 1 つ上の `design-before-blind.md` の 1〜4 節(path: /home/ubuntu/.worktrees/doeff-wt-ki-6828-dc/docs/design-checks/lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97/design-before-blind.md)。

読む実コード(path は repo の根から):
- packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py(保存先の入口 — code_key・code_fingerprint・code_entries・current_code・add_code・CODE_ENTRIES_PER_KEY・read_entry・write_entry・stored_code・discard_entry・entry_path)
- packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py(_from_shared_store・_to_shared_store・_store_key・_record_is_current_here・bytecode_is_current)
- packages/doeff-hy/src/doeff_hy_bytecode_guard/records.py(記録の形・RECORD_TAG・record_of)・macro_use.py(digest)
- packages/doeff-hy/src/doeff_hy_bytecode_guard/dataclass_code.py・bound_source.py(.binding)・macro_use.py(.facts)— 同じ保存先の別の種類の entry
- packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy(bake-one・bake・store-module-at)
- packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy(道具の入口・.imports の entry)
- packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy(unused-store-names・unused-store-entries・sweep-leftovers)・worker/core/env_upkeep.hy(CODE-STORE-UNUSED-SECONDS)
- docs/adr/defadr_doeff_build_002_one_bytecode_store.hy(ADR-DOE-BUILD-002 — R1〜R5・R2b・R5b と法 one-bytecode-store-entry)
- 検: packages/doeff-hy/tests/test_code_store_entries.py・packages/doeff-hy/tests/test_bytecode_guard.py(test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round ほか)・packages/doeff-cluster/tests/test_code_prepare_content_store.py(test_e_…)・packages/doeff-cluster/tests/test_env_store.hy(sweep の検 2 本)

検査の実体(これだけ — ほかの検査は無い):
1. pytest — 上の検の file と ADR の冊(`uv run pytest <file>` — doeff-hy の deftest も pytest で集まる)。
2. commit の時の pre-commit(`.pre-commit-config.yaml`)— semgrep(`.semgrep.yaml`・Hy は semgrep-hy)・doeff-linter・packages/doeff-cluster の doeff-linter(scripts/lint-doeff-cluster.sh)・enforcement-ledger(docs/adr と .semgrep.yaml を触った時)。
3. 変更箇所の品質検査 code-quality(fast の段 — 型検査なし)。

背景(主張の外の事実): 本番の保存先は zeus の `/work/state/doeff-hy-code-store`(約 13 万 file・bucket 1 つに約 500 file)。準備の道具は準備する root の venv の python で走り、root の版の doeff-hy(`bytecode_is_current`・`compiled-pyc` など)を import し、worker の版の code_store.py を path で読む。作業木の import は作業木の版の doeff-hy の loader_hooks と code_store を使い、`DOEFF_HY_CODE_STORE` を worker から継ぐ。

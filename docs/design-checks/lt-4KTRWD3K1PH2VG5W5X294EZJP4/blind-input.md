# 盲検 A・B に渡す入力(lt-4KTRWD3K1PH2VG5W5X294EZJP4)

## 固定した要件と利用状況

- doeff-cluster の worker は、job の宣言(repo の commit・uv の lock)ごとに「実行環境の root」(repo の木を兄弟で並べ、uv の venv と
  bytecode を置いた dir)を作り、その中の子 process で job を走らせる。宣言したどれかの repo の commit が動くたびに新しい root になる
  (1 日 30 本ほど)。古い root は worker の掃除が消す。
- macOS の機体(APFS)は 1 時間ごとに local snapshot を作り、消した file の中身を 24 時間持つ。root を作るたびに新しく書いた block は、
  root を消しても 24 時間 disk を使う。
- 要件: 前の root と変わらない file を書き直さない。今の約束(root の鍵・完成の印・掃除の規則・hardlink の写しが写し元の file を書き換えない事)
  は保つ。bytecode の保存先の決まり(ADR-DOE-BUILD-002 — 保存先の並びを綴るのは code_store.py だけ・準備の道具は前の版の木から .pyc を
  引き継がない)は保つ。

## 設計と主張

同じ dir の design-pre-blind.md(責務・不変条件 I1〜I6・変更シナリオ S1〜S6 と主張・強制の方法と検出範囲)を読むこと。

## 実コード(読むだけ — 版 doeff 9fe8c330bdd86f6d2ff5c36ea643344d62b1ca94)

作業木: /home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check(この版の checkout。共有の source は変更しない)。

| 責務 | path |
|---|---|
| tree-placement | packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_translation.hy(tree-delta・tracked-entries・link-unchanged・copied-tree・materialize・archive-into・place-paths) |
| copy-source-choice | packages/doeff-cluster/src/doeff_cluster/worker/core/env_prepare.hy(copy-root・tree-source・stage-trees) |
| code-store | packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py |
| bake-pool | packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy・packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy・packages/doeff-cluster/src/doeff_cluster/worker/core/bake_plan.hy |
| store-sweep | packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_store.hy(unused-store-entries・unused-store-names) |
| import-hook | packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py |
| file の effect | packages/doeff-core-effects/doeff_core_effects/file_effects.hy・os_file.hy(本物)・memory_file.hy(模擬) |
| 模擬の世界 | packages/doeff-cluster/src/doeff_cluster/sim/env_world.hy |
| ADR | docs/adr/defadr_doeff_build_002_one_bytecode_store.hy(R1〜R6・law 3 本) |

## 検の実体と実行の命令(作業木の中で・1 file か -k で絞る — 全数は走らせない)

| 検 | 命令 |
|---|---|
| 木の写し(本物の git・tar・os.link) | `uv run --no-sync pytest -q packages/doeff-cluster/tests/test_env_tree_placement.hy` |
| 準備の模擬 | `uv run --no-sync pytest -q packages/doeff-cluster/tests/test_env_prepare.hy`(約 75 秒) |
| 焼く道具(本物の道具を子 process で) | `uv run --no-sync pytest -q packages/doeff-cluster/tests/test_code_prepare_content_store.py -k <名>`(module の準備に約 50 秒) |
| 保存先 | `uv run --no-sync pytest -q packages/doeff-hy/tests/test_code_store_pyc_links.py packages/doeff-hy/tests/test_code_store_entries.py` |
| ADR の law | `uv run --no-sync pytest -q "docs/adr/defadr_doeff_build_002_one_bytecode_store.hy::test_adr_doe_build_002_judgments_reject_the_counterexamples"`(冊の他の 3 本は repo 全域を読む — 1 本 約 50 秒) |
| 掃除 | `uv run --no-sync pytest -q packages/doeff-cluster/tests/test_env_store.hy` |

一時の file は `mktemp -d` で作った自分専用の dir の下だけに作る。/tmp の共有の名は作らない・上書きしない・消さない。消すのはその dir の中の
自分が作った物だけ。読むだけの調べは file に書かず pipe で読む。機体の python3 は使わず、`uv run --no-sync python …`(作業木の中)で走らせる。

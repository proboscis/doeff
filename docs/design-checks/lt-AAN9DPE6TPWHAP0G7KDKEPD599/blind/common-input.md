# 盲検 A・B に共通の材料

## 対象

- repo: doeff(主 checkout `~/repos/doeff`。共有なので書き換えない)。
- 対象の版: `1021b89465ca81caba11336d1f5cf9734b37081b`(doeff の main に在る)。
- 対象のコード: `packages/doeff-core-effects/doeff_core_effects/scheduler.py` の関数 `_scheduled_python`(Python の協調の scheduler — 1 つの run の状態を closure の中の dict・list に持つ)。
- 設計本文: この版の後に置いた設計の記録の §1〜§4 だけを読むこと — `docs/design-checks/lt-AAN9DPE6TPWHAP0G7KDKEPD599/design.md`(branch `wt/ki-1294-design-check` の上 — 版は起動の依頼文に書く)。同じ dir の他の file と `docs/design-checks/` の下の他の dir は読まないこと。
- 使い方の背景: agora の turn-host(手番ごとに Claude の CLI を起こし、CLI が出す行を手番の記録に足す service)が、この scheduler の上で走っている手番ごとに task を数本(手番の源の task・監視の timer など)待たせる。CLI の行が 1 つ着くごとに、層 2(`packages/doeff-claude-code/src/doeff_claude_code/handler.hy` の呼び鈴の待ち — `WaitWithin(bell.future, left, park=True)`)が async の時計(`packages/doeff-time/src/doeff_time/handlers/async_time.py` — 期限の timer の task を Spawn して Race し、終わりに Cancel)で 1 回の WaitWithin を終える。timer の task は `Await`(`packages/doeff-core-effects/doeff_core_effects/handlers.py` の `await_handler` — CreateExternalPromise と Wait)で寝る。

## 読んでよいコード(版 1021b8946)

- `packages/doeff-core-effects/doeff_core_effects/scheduler.py`(全部)
- `packages/doeff-core-effects/doeff_core_effects/handlers.py` の `await_handler`
- `packages/doeff-time/src/doeff_time/handlers/async_time.py`・`sync_time.py`・`sim_time.py`
- `tests/test_scheduler.py`・`tests/test_scheduler_trace.py`・`tests/test_scheduler_implementation.py`
- `packages/doeff-vm/src/scheduler.rs`(Rust の scheduler — 同じ意味の別実装・本番では使わない)
- 必要なら doeff の他の source も読んでよい(`docs/design-checks/` の下は上の design.md の §1〜§4 だけ)。

## 実験のやり方

- 実験は自分専用の detached な作業木で行う: `git -C ~/repos/doeff worktree add --detach ~/.worktrees/doeff-wt-blind-<a|b>-ki1294 1021b89465ca81caba11336d1f5cf9734b37081b`、その中で `make sync`(数十秒)。commit・push・land・他の会話への連絡はしない。共有の checkout(`~/repos/doeff`)と他の作業木は書き換えない。
- テストは触る file に絞って走らせる(例: `uv run pytest tests/test_scheduler.py -q` — 30 秒ほど)。repo 全体のテスト・全体の lint は走らせない。機体は混んでいる(負荷 50〜150)ので、壁の時計に頼る測りは揺れる。
- Python は `uv run` を通す(機体の python3 を直に使わない)。

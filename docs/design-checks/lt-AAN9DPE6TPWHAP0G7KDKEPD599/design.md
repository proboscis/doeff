# 設計検証 lt-AAN9DPE6TPWHAP0G7KDKEPD599 — Python の scheduler の待ち手の索引と走れる列の数え(card ki-1294af4fa525)

## §0 範囲と経緯

- 完了の範囲: **実装完了まで**。実装は doeff L2127 = main `1021b89465ca81caba11336d1f5cf9734b37081b`(4 commit: 2c3432f23 失敗ケース・b899fe056 直し・0bfee8954 失敗ケース・1021b8946 直し)。agora-controllers L6286 = main `dd7ed541e1b94787ae066da3f1bc965a0ed8217e` が doeff-min-commit をこの版へ上げた。本番の前後(受入の条 3)は配備の後に測る(この検証の範囲外)。
- 経緯: 実装と着地が先で、この設計検証は完了の門の求めで後から行った。§1〜§4 は盲検を起こす前に commit して固定し(この file の版と hash は blind/blind-meta.md)、§5 以降に盲検の結果・再現・直しを足す。§1〜§4 は盲検の後に書き換えない。
- 対象のコード: `packages/doeff-core-effects/doeff_core_effects/scheduler.py` の `_scheduled_python`(Python の scheduler・本番が使う実装 — `DEFAULT_IMPLEMENTATION = "python"`、agora-controllers は `DOEFF_SCHEDULER` を設定しない)。Rust の scheduler(`packages/doeff-vm/src/scheduler.rs`)は変えていない。

## §1 要件(固定)

- R1: 走っている手番の数(待ち手の数・走れる列の長さ)で、1 回の WaitWithin(async の時計の期限の timer を Spawn・Race・Cancel)の Cancel と pick_next の scheduler の仕事が増えない。
- R2: 失敗ケース先 — 待つ task の数を増やすと 1 回の WaitWithin の scheduler の仕事が増える事を、時計でなく数で赤にする条を先に commit する。
- R3: 模擬(feed_bench)で走っている手番 8 本と 36 本の feed-turn の 1 歩の CPU の前後を測り、直した後の 36 本が 8 本の 1.1 倍以内。
- R4: scheduler の意味を変えない — Cancel は始まった task に TaskCancelledError を投げ込む(except・finally が走る)、外の待ちの印(placeholder)は daemon の task を止め(sim の時計の driver)、非 daemon の仕事は印の下でも走る(#505)、pop の順・取り消しの callback の順・deadlock の診断は今まで通り。

## §2 責務(1 つの closure `_scheduled_python` の中の部品)

scheduler の状態は 1 つの run の closure の中の dict・list で、scheduler の thread だけが触る(外の thread は `external_queue.put` だけ — ExternalPromise.complete/fail)。

| id | 責務 | 持つ物 | 隠す物 | 口 | 不変条件 |
|---|---|---|---|---|---|
| waiters-index | 待ち手の表 `waiters`(鍵 → entry の list)と、その逆の索引 `parked_keys_of`(持ち主 → {鍵: entry の数})と鍵の入った順の番号 `waiter_key_stamp` | 誰が何を待つか・鍵の順 | 索引の形 | `park_waiter`(入る唯一の口)・`unpark_key`・`unpark_at`・`keep_waiters`・`keys_parked_by`(その task の鍵を waiters の順で) | I-W1: `parked_keys_of` は `waiters` から数え直した物と等しい。I-W2: `sorted(waiters, key=stamp)` は `list(waiters)` と等しい |
| ready-counts | 走れる列 `ready`(heap)と、印の下(優先度 ≤ PRIORITY_EXTERNAL_WAIT)の生きた runnable entry の数 `live_ready_count`・持ち主ごとの数 `live_ready_of` | 列の中身・生きた仕事の有無 | 数の形 | `ready_push`・`ready_pop`・`ready_remove_at`(列を動かす口)・`has_live_ready_entry` | I-R1: `live_ready_count` は列から数え直した物と等しい(持ち主が cancelled の entry は数えない — `finish_cancelled` が外す)。I-R2: `has_live_ready_entry` は印を pop した直後だけ問われ、その時 列に残るのは優先度 ≤ PRIORITY_EXTERNAL_WAIT だけ |
| placeholder | 外の待ちの印 `("wait_external", owner, k, wk, cell)` と対の待ち手の entry が共有する cell `[claimed, 列の中に居るか]`・列の中の claimed の印の数 `claimed_marks_in_ready` | 印の生き死に | cell の 2 つ目の欄 | `claim`(印を claimed にする唯一の口)・`drop_claimed_marks`(64 以上かつ列の半分を越えたら一度に外す) | I-P1: `claimed_marks_in_ready` は列の中の claimed の印の数と等しい。I-P2: 外しても pop の順は変わらない(列の並びは (優先度, 通し番号) の全順序) |
| cancel | Cancel・Discard の処理(`keys_parked_by` → `detach_parked_continuation` → 投げ込み/捨て → `cancel_abandoned_external_promises`)・`finish_cancelled` | 取り消しの意味 | — | effect `Cancel`・`Discard` | 投げ込みの順・外の約束の on_cancel の callback の順は `waiters` の鍵の順 |
| dispatch | `pick_next`(印の保留 #505・daemon の shield・外の完了を待つ所・deadlock の診断) | 次に走る物の選び | — | 内部 | 今まで通りの選びの順 |
| trace | 測りの口(`set_scheduler_trace` の sink・`trace_enqueued` の ahead・`wake_of_seq`・`ready_wake_count`) | 起こされ方ごとの数 | 数の形 | sink への出来事 | ahead・ahead_by_wake・ready の欄は今まで通りの値。数は sink がある間だけ保ち、`_trace_generation` が替わったら要る時に列から数え直す |
| rust-scheduler | Rust の scheduler(`doeff-vm/src/scheduler.rs`) | 同じ意味の別の実装 | — | `scheduled(..., implementation="rust")` | 変えていない(今も表をなめる) |
| tests | `tests/test_scheduler.py` の `TestTimedWaitWorkDoesNotGrowWithParkedTasks`(行の数・列の長さの 2 つの条)と既存の scheduler の検 | R1・R2 の見張り | — | pytest | 下の §4 |

effects: どの部品も scheduler の thread の中の状態だけを書く(I/O なし)。解放: run の終わりに closure ごと捨てる(止め・再開の持ち主は無い)。

## §3 変更シナリオと事前の主張(盲検の前に固定)

| id | 軸 | 適用 | 変える要求 | 主張(変える部品 / 変えない部品)| 前提 |
|---|---|---|---|---|---|
| S1 | concurrency | 適用 | 走っている手番がさらに増える(待つ task 数百〜数千・列の長さ数千)| 1 回の WaitWithin の scheduler の行の数は増えない。変える部品なし(負荷だけ)| 待つ task はそれぞれ自分の鍵で待つ(1 つの鍵に数千人が並ぶ fan-in は除く — 1 つの鍵の entry の list はなめる)|
| S2 | effects | 適用 | task を待ちに置く新しい scheduler の effect を足す(例: 複数の future のうち k 個を待つ WaitSome)| 変えるのは dispatch(新しい effect の処理)だけで、登録は `park_waiter`、外すのは `unpark_*`・`keep_waiters` を呼ぶ。waiters-index・cancel・ready-counts の中は変えない。Cancel はその task を見つけて投げ込む | 新しい effect が既存の entry の形(type, owner, …)と `claim` を使う |
| S3 | effects | 適用 | 走れる列に新しい種類の entry を足す(例: 優先度を替えて並べ直す reschedule)| 変えるのは dispatch と `_RUNNABLE_ENTRY_KINDS`(runnable かの宣言)だけ。列は `ready_push`・`ready_pop` を通るので数は合ったまま | entry の [1] が持ち主 |
| S4 | simulation | 適用 | sim の時計(sim_time_handler — daemon の時計の driver を印が止める)の中身を替える | scheduler の部品は変えない。印が daemon を止める順・非 daemon が印の下で走る順(#505)は今まで通り | 時計は scheduler の effect だけを使う |
| S5 | effects | 適用 | 測りの口に新しい欄を足す(例: 前に並ぶ entry を持ち主の種類ごとに)| 変えるのは trace だけ(数を足すなら `ready_push`・`ready_pop` の数えの所)。sink が無い間の歩の CPU は変わらない | sink は process に 1 つ |
| S6 | hardware | 対象外 | — | — | 理由: scheduler の状態は scheduler の thread に閉じ、外の thread(asyncio の橋・CLI の読み手)は SimpleQueue に put するだけ。free-threaded の Python でもこの形は替わらない(この変更は thread を跨ぐ状態を足していない) |
| S7 | storage | 対象外 | — | — | 理由: scheduler の状態は 1 run の memory の中だけで、永続化しない |
| S8 | distribution | 対象外 | — | — | 理由: scheduler は 1 process の 1 thread の協調の scheduler。機体を跨ぐ仕事は doeff-cluster が process ごとに別の scheduler で回す(この変更の外)|
| S9 | concurrency | 適用 | Rust の scheduler に同じ索引を入れる(本番を Rust へ切り替える時)| 変えるのは rust-scheduler だけ。Python の部品と検(行の数の条は implementation="python" を名指す)は変えない | 2 つの実装は同じ意味(docs/25-rust-scheduler.md)|

## §4 強制方法(今の実装の所在)

| 守る責務 | 強制方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| R1(1 回の WaitWithin の仕事が待つ task の数で増えない)| 行の数の契約の検: 本番の形の WaitWithin 1 回が走らせる scheduler.py の行の数を sys.settrace で数え、待つ task 256 本で 16 本の 1.1 倍以内(測りの口なし・あり)| `tests/test_scheduler.py::TestTimedWaitWorkDoesNotGrowWithParkedTasks::test_one_timed_wait_does_not_walk_the_parked_tasks` | pytest(tests/ は日次の全体の回と、変えた所の検の選び)| 測るのは WaitWithin の形 1 つ。Gather・Discard・semaphore の形は測らない |
| 死んだ印が列に溜まらない | 列の長さの契約の検: NORMAL の仕事が並び続ける間の WaitWithin 1,000 回の後の `_SchedulerIntrospection()["ready"]` が 250 未満 | 同 `test_cancelled_deadline_timers_do_not_pile_up_in_the_ready_heap` | 同 | Gather・Race の印は測らない |
| R4(意味)| 既存の scheduler の検(Cancel・Discard・印の shield・#505・deadlock の診断・root の close-out・sweep)と、使い手の検(doeff-time・doeff-events・doeff-core-effects)| `tests/test_scheduler.py`・`tests/test_scheduler_trace.py`・`tests/test_scheduler_implementation.py`・各 package の tests | 同 | 索引・数えの不変条件 I-W1・I-W2・I-R1・I-P1 を直に照らす検は無い(実装の時に一時の照合で確かめただけ — commit していない)|
| waiters・ready を入口の外から書き換えない | 入口の関数の docstring と註(「入る唯一の口」)| scheduler.py | 読む人だけ | 静的な検査は無い(closure の中の dict・list への直の代入・append を止める規則は .semgrep.yaml に無い)|
| has_live_ready_entry は印を pop した直後だけ問う | docstring | scheduler.py | 読む人だけ | 静的な検査は無い |
| 型 | なし(scheduler の closure の中は素の dict・tuple — 既存の作り)| — | pyright(make lint-pyright)は scheduler.py を見るが、entry の tuple の形は型で表していない | entry の形の取り違えは型で拒まない |

既存の検査の経路: commit の hook(`.semgrep.yaml` の規則を staged の file へ・台帳)・`make lint`(ruff・pyright・semgrep・doeff-linter)・pytest。

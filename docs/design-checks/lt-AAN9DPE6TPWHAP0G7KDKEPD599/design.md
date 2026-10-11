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

---

以下 §5〜§10 は盲検の後に足した(§1〜§4 は盲検の前の sha256 37dc1566096772ba8df2d065cd17c3de4f896d118badf70dbb2931456d2a68fb のまま — この file の頭から §4 の終わりまでは変えていない)。

## §5 盲検の結果

起動の記録は [blind/blind-meta.md](blind/blind-meta.md)、返答は未加工で [blind/blind-a-return.md](blind/blind-a-return.md)・[blind/blind-b-return.md](blind/blind-b-return.md)。要求した model(codex の gpt-6-astra・low)はこの Pod に codex の資格が無く使えず([blind/codex-attempt.txt](blind/codex-attempt.txt))、この会話の subagent(Opus 5.5・xhigh を要求 — 実 model と effort の効きは観測していない)で代えた。

- **A(変更が複数の責務へ波及する失敗例)**: S2 の WaitSome(k 個を待つ — 複数の鍵の待ち `"some"`)と S3 の Reschedule(列の新しい種類 `"reschedule"`)を、§3 の主張どおり dispatch と `_RUNNABLE_ENTRY_KINDS` だけで足すと、正の場面は通るのに Cancel・Discard が壊れる。
- **B(検査を通りながら責務分離を破る失敗例)**: 走っている task の優先度を変える effect `SetTaskPriority` が、`drop_claimed_marks` の形を写して `ready[:] = [...]` と heapify で列をその場で書き換える。提示の検査(pytest・ruff・semgrep、参考に pyright)を全部通り、外の約束を待つ間に IDLE へ下げた完了者が走らず run が止まる。

## §6 再現(版 1021b8946 — 盲検の前の実装)

| 失敗例 | 命令と結果 | 証拠 |
|---|---|---|
| A | A の差分+A の検を 1021b8946 の作業木で: WaitSome の task の Cancel が `TypeError: argument 'k': 'dict' object is not an instance of 'K'`(state の dict を継続として投げ込んだ)・外の約束の on_cancel は ep1 だけ・Discard の後に待ち手が 1 つ残る・列に並んだ Reschedule の task の Cancel は except/finally を走らせずに落とす(fallback の警告)。既存の検は 117 passed | [evidence/at-1021b8946/a1-s2-s3-new-kinds-cancel.txt](evidence/at-1021b8946/a1-s2-s3-new-kinds-cancel.txt) |
| B | B の差分で pytest 121 passed(基準 117+B の 4 条)・ruff は基準と同じ 6 件・semgrep 0 findings。B の再現の場面 1 は 3 秒で終わらず、`has_live_ready_entry` の時の数え `[0,0]`・列からの数え直し `[1,1]` | [evidence/at-1021b8946/b1-set-task-priority.txt](evidence/at-1021b8946/b1-set-task-priority.txt) |

成立の根拠(どちらも成立した):

- A は**知識の漏洩**。cancel の `detach_from_waiters` が複数の鍵の待ちの種類の名 `("gather", "race")` を、`detach_from_ready` が継続を持つ列の種類の名 `("resume", "raise", "sem_resume")` を持っていた。新しい種類を足す人は cancel の中を知らないと Cancel を正しくできず、§3 の S2・S3 の「cancel は変えない」は前提ごと崩れていた。
- B は**強制の欠け**。`ready`・`waiters` を入口の関数の外から動かさない事を守るのは docstring と註だけで(§4 の「waiters・ready を入口の外から書き換えない」の行の限界の通り)、どの検査も拒まなかった。§3 の S3 の「列は ready_push・ready_pop を通るので数は合ったまま」は、通らない書き方を止める物が無かった。

## §7 直し(doeff L2137 → main e8569d4a36f6・bb9afc60fef9 — branch wt/ki-1294-dc-fix の bb614cbb7・df1ea3efc)

1. **種類の宣言を module の頭の 1 か所に置く**: `_CONTINUATION_ENTRY_KINDS`(継続 = `doeff_vm.K` を `[2]` に持つ列の種類)・`_NEW_TASK_ENTRY_KIND`・`_PLACEHOLDER_ENTRY_KIND`・`_READY_ENTRY_KINDS`(この 3 つから作る)・`_RUNNABLE_ENTRY_KINDS`(導いた集合 — 宣言の場ではない)・`_SINGLE_KEY_WAITER_KINDS`(1 つの鍵の待ちで `[2]` に継続を持つ種類)。
2. **入口が宣言を検める**: `ready_push` は宣言の無い種類を `TypeError` で断り、継続の種類は `[2]` が `K` かを検める。`park_waiter` は 1 つの鍵の種類なら `[2]` が `K` か、それ以外は `withdraw_group_of` に宣言があるかを検める。断りの文言は宣言する場所を名指す。どちらも何も動かす前に断る。
3. **cancel は種類の名を持たない**: 複数の鍵の待ちは `withdraw_group_of = {"gather": remove_gather_waiters, "race": remove_race_waiters}` に外し方の関数と 1 行で宣言する。`detach_from_waiters` はそこを引き、`detach_from_ready` は `_CONTINUATION_ENTRY_KINDS` を読む。
4. **自己照合**: `_INDEX_SELF_CHECK`(既定 off)が on の間、`pick_next` の各周と `handle_scheduler_effect` の頭で `check_indexes` が `waiters`・`ready` から I-W1・I-W2・I-R1・I-P1 と測りの口の起こされ方ごとの数を数え直し、食い違えば `SchedulerIndexDriftError` で食い違った数の名を挙げる。`tests/conftest.py` の fixture `scheduler_index_self_check` を、scheduler の検 4 file が `pytestmark` で使う(行の数の条だけ外す — 照合の行が数えに入るため)。
5. **静的な検** `tests/test_scheduler_index_guards.py`: `_scheduled_python` の中の `waiters`・`ready` の書き換え(添字への代入・del・変更の method・`heapq` の書き換えの関数・`nonlocal`)が入口の関数の中だけにある事を AST で確かめる条、その検が書き換えの形を見落とさず読みを拾わない条、入口の名が今も在る条、宣言の無い・誤って宣言した種類の 4 つを入口が断る条、別名を経た `ready` の書き換えを自己照合が捕まえる条と、外すと黙って通る対照。

途中の版の穴: bb614cbb7 では `_READY_ENTRY_KINDS` を `_RUNNABLE_ENTRY_KINDS` から導いていたので、A の S3 を直した版へ素直に写す形(`_RUNNABLE_ENTRY_KINDS` に `"reschedule"` を足す)を `ready_push` が受け入れた。また、複数の鍵の種類を `_SINGLE_KEY_WAITER_KINDS` に誤って足すと元の TypeError に戻る形だった。どちらも A の差分を写す準備の中で読んで見つけ(bb614cbb7 の上で走らせてはいない)、df1ea3efc で上の 1・2 の形に直した。

### §7.1 強制方法(直した後 — §4 の表の更新)

| 守る責務 | 強制方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| R1(1 回の WaitWithin の仕事が待つ task の数で増えない)| 行の数の契約の検(§4 と同じ) | `tests/test_scheduler.py::TestTimedWaitWorkDoesNotGrowWithParkedTasks` | pytest(着地の列の root の段 `pytest -q -m 'not e2e'`・testpaths に `tests`)| §4 と同じ(WaitWithin の形だけ)|
| 新しい列・待ちの種類は宣言してから使う(cancel が種類の名を持たない)| 入口の実行時の検め(宣言の集合・`withdraw_group_of`・`[2]` が `K`)— 断りの文言が宣言の場所を名指す | `scheduler.py` の module の頭の宣言・`ready_push`・`park_waiter`・`withdraw_group_of`。検は `test_the_entry_functions_refuse_an_undeclared_or_misdeclared_kind` | 本番でも常に効く(新しい種類を使う最初の正の検で落ちる)| 種類ごとの特別な後始末(`sem_resume` の permit の返し・`wait_external` の印の claim)は今も種類の名を持つ。新しい種類が後始末を要るかは宣言では分からない |
| `waiters`・`ready` は入口の関数の中でだけ動かす | AST の検(名 `waiters`・`ready` の書き換えの位置)| `tests/test_scheduler_index_guards.py` | pytest(同上)| 別名を経た書き換え・`_scheduled_python` の外からの書き換えは見えない(下の自己照合が見る)|
| 索引と数えは構造と合う(I-W1・I-W2・I-R1・I-P1・測りの口の数)| 自己照合 `_INDEX_SELF_CHECK` → `SchedulerIndexDriftError` | `scheduler.py` の `check_indexes`・`tests/conftest.py` の fixture・scheduler の検 4 file の `pytestmark` | pytest の中だけ(本番は off — 歩ごとに列と待ち手を全部数え直すため)| 検が走らせた歩だけを見る。package の検(doeff-time など)は fixture を使っていない |
| 型 | なし(§4 と同じ)| — | — | entry の tuple の形は型で表していない(`K` の検めは実行時)。pyright は走らせていない |

## §8 再検証(版 df1ea3efc — main の bb9afc60 と scheduler.py・tests の触った file が同じ中身)

| 何を | 結果 | 証拠 |
|---|---|---|
| A を素直に写した版 a0(宣言なし)| 新しい種類を使う 5 場面が、その種類が入る入口で TypeError(文言が `withdraw_group_of`・`_CONTINUATION_ENTRY_KINDS` を名指す)。Cancel が task を黙って落とす場面は無くなった | [evidence/at-bb9afc60/blind-a-on-fix.txt](evidence/at-bb9afc60/blind-a-on-fix.txt) |
| A に宣言 2 行を足した版 a1 | WaitSome の Cancel・外の約束の Cancel(on_cancel ep1・ep2)・Discard(待ち手 0)・列に並んだ Reschedule の Cancel が、どれも except/finally を走らせ、既存の Race と同じ結果(自己照合つき)| 同上 |
| 誤って宣言した版 a2 | 複数の鍵の種類を 1 つの鍵の種類と宣言すると、`[2]` が dict だと入口が断る | 同上 |
| B の違反の版 b0 | AST の検が `handle_scheduler_effect` の 2 行(`ready[:] =`・heapify)を名指して落ちる。B の正の検 4 条のうち「並んでいる間に下げる」条が自己照合で `SchedulerIndexDriftError`。B の再現の場面 1 は止まらず 0.09 秒で同じ例外、場面 3(測りの口つき)は「trace wake counts」も名指す | [evidence/at-bb9afc60/blind-b-on-fix.txt](evidence/at-bb9afc60/blind-b-on-fix.txt) |
| B の対照 C の版 b1(同じ機能を `ready_remove_at`+`ready_push` で)| AST の検が通り、4 条が自己照合つきで通り、再現の 5 場面がどれも正しい値で終わる | 同上 |
| S1: 行の数の条 | 直す前の版(2c3432f23)で 2 failed(待つ task 16 本で 1376 行・256 本で 11929 行。増えた所の上位 `detach_from_waiters`・`keys_parked_by`・`has_live_ready_entry`)・直した版で 3 passed | [evidence/at-bb9afc60/s1-line-count.txt](evidence/at-bb9afc60/s1-line-count.txt) |
| scheduler の検 4 file(自己照合つき)| 123 passed(python と rust の 2 実装を同じ検で走らせる `test_scheduler_implementation.py` を含む)| [evidence/at-bb9afc60/suite-selfcheck.txt](evidence/at-bb9afc60/suite-selfcheck.txt) |
| S4: doeff-time の sim の daemon の形・close-out の検(自己照合つき)| 8 passed | [evidence/at-bb9afc60/s4-sim-clock.txt](evidence/at-bb9afc60/s4-sim-clock.txt) |
| S9: 同じ WaitWithin の形を Python と Rust で、待つ task 16 本と 1024 本 | 1 回の CPU の比(1024 本 ÷ 16 本)は Python 1.16(5 回の値が重なる)・Rust 2.25(重ならない)。Rust は今も表をなめ、Python の索引は Rust に及んでいない | [evidence/at-bb9afc60/s9-rust-walk.txt](evidence/at-bb9afc60/s9-rust-walk.txt) |
| 普段の歩の CPU(直しの前と後)| 後 ÷ 前は 0.90〜1.17 で片寄り無し(負荷 50〜90 の機体)| [evidence/at-bb9afc60/step-cost.txt](evidence/at-bb9afc60/step-cost.txt) |
| 変更箇所の品質検査(速い段)| passed(型は含まない・tests の 5 file は対象に未登録)| [evidence/at-bb9afc60/code-quality.txt](evidence/at-bb9afc60/code-quality.txt) |

## §9 予測(§3)と実際の差

| id | 予測した範囲(§3)| 実際(1021b8946 の時)| 実際(直した後)| 差の訳 |
|---|---|---|---|---|
| S1 | 変える部品なし | 同じ(行の数の条が守る)| 同じ | 差なし |
| S2 | dispatch だけ | dispatch+cancel の中(`detach_from_waiters` に種類の名を足す)— 足さないと Cancel が壊れる | dispatch+`withdraw_group_of` への宣言 1 行(cancel の処理は変えない)| 1021b8946 の差は**知識の漏洩**(cancel が種類の名を持っていた)で、直した。直した後の宣言 1 行は cancel の公開の契約(宣言の表)への意図した登録 |
| S3 | dispatch と `_RUNNABLE_ENTRY_KINDS` だけ | dispatch+cancel の中(`detach_from_ready` の種類の名)。列をその場で書き換える実装(B)は数えを壊して止まる | dispatch+`_CONTINUATION_ENTRY_KINDS` への宣言 1 行。列を入口の外で動かす実装は AST の検と自己照合が落とす | 1021b8946 の差は漏洩(cancel の種類の名)と強制の欠け(列の書き換え)。宣言の場所は予測(`_RUNNABLE_ENTRY_KINDS`)から `_CONTINUATION_ENTRY_KINDS` に替わった — `_RUNNABLE_ENTRY_KINDS` は導いた集合にした |
| S4 | scheduler の部品は変えない | 予測どおり(盲検は S4 の失敗例を出していない)。ただし B の場面 1 は #505(非 daemon の仕事は印の下でも走る)を破った | sim の検が自己照合つきで通る。外の部品が closure の列を別名で書き換えると自己照合が落とす | 時計の中身の変更そのものは試していない — 試したのは、scheduler の外から列を動かすと見つかる事 |
| S5 | trace だけ(数を足すなら `ready_push`・`ready_pop` の所)| B の場面 3 で、列をその場で書き換えると `ahead`・`ahead_by_wake` が実際と違う値になった | 測りの口つきの歩でも起こされ方ごとの数を数え直して照らす。b0 の場面 3 で「trace wake counts」が名指された | 新しい欄を足す実験はしていない |
| S9 | rust-scheduler だけ | 予測どおり | 予測どおり(Rust は 2.25 倍・Python は 1.16 倍)| 差なし。Rust に索引を入れる時は Rust の側に数の検が要る(行の数の検は Python の行しか数えない)|

S6・S7・S8 は §3 のとおり対象外(盲検もこの軸の失敗例を出していない)。

## §10 限界と未確認

- 自己照合は検の中だけで効く(本番は off)。検が走らせない書き方・形は見ない。package の検(doeff-time・doeff-events など)は fixture を使っていない。
- AST の検は名 `waiters`・`ready` の書き換えだけを見る。別名・`_scheduled_python` の外からの書き換えは自己照合だけが見る。
- 宣言は「Cancel が継続を見つけて外せる」ことだけを保証する。種類ごとの特別な後始末(`sem_resume` の permit の返し・`wait_external` の印の claim)は今も種類の名を持つ所があり、新しい種類が後始末を要るかは宣言では分からない。
- 型: entry の tuple の形は型で表していない。pyright も code-quality の型の段も走らせていない。
- `root_close_out` の `except BaseException` が GeneratorExit も受けるので、handler が例外で抜けた run の generator が gc で閉じられると「generator ignored GeneratorExit」の警告が出る。この変更の前から在る(既存の検 `test_wait_on_swept_id_raises_loud_keyerror` でも出る)。自己照合の例外の時も出るが、検は意図した例外で落ちる。
- code-quality の速い段は、tests/ の 5 file を検査の対象に登録していないと出す(登録は日次か明示の操作)。
- 盲検の model は要求した物(gpt-6-astra・low)でなく、Opus 5.5(xhigh を要求)の subagent。
- 本番の前後(受入の条 3)は §0 のとおりこの検証の範囲外で、card ki-1294af4fa525 に書く。

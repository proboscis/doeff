# 責務分離の失敗例: 待ちに置く新しい effect を足すと、cancel の部品の中を変えないと R4 が破れる

§3 の主張 S2 は、前提を守ったまま崩れました(実測)。同じ原因で S3 も崩れます(実測・副次)。

## 1. 変更要求と、それが要る理由

`WaitSome(k, *futures)` を足します。n 個の future のうち k 個が終わったら、終わった順に `[(index, value), …]` で起きる effect です。失敗や取り消しが 1 つでも出たら、その場で例外を投げます(Gather と同じ)。

- 定足数の待ち(複数の task のうち 2 つ揃えば進む、など)を、Race を毎回登録し直さずに書くための物です。
- design.md §3 の S2 が、例としてこの effect そのものを挙げています。ですから「合理的に求められうる変更」に当たります。

## 2. 関係する主張・前提と、予想に反して変わる所

- **主張 S2:** 「変えるのは dispatch(新しい effect の処理)だけ。登録は `park_waiter`、外すのは `unpark_*`・`keep_waiters`。waiters-index・cancel・ready-counts の中は変えない。Cancel はその task を見つけて投げ込む」
- **S2 の前提:** 新しい effect が既存の entry の形 (type, owner, …) と `claim` を使う。
- **要件 R4:** Cancel は始まった task に TaskCancelledError を投げ込み、except・finally が走る。
- **前提を守っているか:** 守っています。
  - entry は `("some", owner, state, claimed)` で、gather・race の entry と同じ形です。
  - 登録は `register_pending_waiter` を通した `park_waiter` です。外す時は `claim` と `keep_waiters` だけを使います。
  - 1 つの鍵に数千人が並ぶ形ではありません。implementation は python で、測りの口(trace の sink)は無しです。
  - したがって契約の適用範囲の内です。
- **予想に反して変わる部品:** cancel の `detach_from_waiters`(元の版 scheduler.py 1707〜1731 行)。ここを変えないと R4 と #498(取り消しを外の約束の作り手へ伝える)が破れます。

## 3. どの知識がどこへ漏れているか

`detach_from_waiters` が、待ち手の entry の形を種類ごとに文字列で持っています。

- 1719 行 `if entry_type in ("gather", "race")`: 複数の鍵にまたがる登録であること、継続が `state["waiter_k"]` にあること、全部の鍵から外す関数(`remove_gather_waiters` / `remove_race_waiters`)の名前。
- 1727 行: `claim` の cell が `entry[3]` にあるのは "wait_external" だけ、という知識(gather・race では `entry[3]` が cell、"wait" では起こす優先度)。
- 1730 行: 上のどれにも当たらない種類は「鍵 1 つで、継続は `entry[2]`」と黙って決めつけます。

これは新しい effect の処理(dispatch)が持つはずの知識です。それが cancel に書き込まれていて、dispatch の側から渡す口がありません。そのため新しい種類は、断られずに既定の枝へ落ちます。

区別は次の通りです。

| 分類 | 中身 | 欠陥か |
|---|---|---|
| 意図した公開契約の拡張 | `WaitSome` の class を足す。Rust の実装も同じ意味にそろえる(S9・docs/25 の 2 実装同義) | 欠陥に数えない |
| 配線の変更 | `handle_scheduler_effect` と `wake_waiters` に "some" の枝を足す。`register_pending_waiter` の型 `Literal["gather","race"]` を広げる | S2 の想定内 |
| 共同の不変条件 | 外す時に `claim` する(I-P1)。`keep_waiters` で逆の索引をそろえる(I-W1) | 決められた口で守れた |
| 隠すはずの知識の漏洩 | cancel の中の種類ごとの分岐 | **これが欠陥** |

同じ漏洩は `detach_from_ready` にもあります。1740 行の `("resume", "raise", "sem_resume")` で、S3 で宣言の場とされる `_RUNNABLE_ENTRY_KINDS` を読んでいません。

## 4. 最小の再現と観測(実測)

**手順**

1. `git -C ~/repos/doeff worktree add --detach ~/.worktrees/doeff-wt-blind-a-ki1294 1021b89465ca81caba11336d1f5cf9734b37081b` の後、`make sync`。
2. dispatch だけを変える差分(84 行を足し、削除は 0)を当てる。
   - `WaitSome` の class、`remove_some_waiters`・`wake_some_waiter`、`wake_waiters` の "some" の枝、`handle_scheduler_effect` の枝。
   - 差分は `/tmp/blind_a_dispatch_only.diff`(sha256 a173a0b4…a736)。
3. 次の program を動かす。
   - task が `try: yield WaitSome(1, p1.future, p2.future) except TaskCancelledError / finally` で待つ。
   - 根が `Cancel(t)` → `Wait(t)` をする。
   - 対照は同じ program の `Race(p1.future, p2.future)` 版。
4. `uv run pytest tests/test_blind_a_waitsome.py -q -s` の出力:

```
HAPPY ('ok', ([(1, 'b'), (2, 'c')], 0))
CANCEL-INTERNAL kind=race status=ok value=('cancelled', 0) events=['except', 'finally']
CANCEL-INTERNAL kind=some status=raised value=TypeError("argument 'k': 'dict' object is not an instance of 'K'") events=[]
CANCEL-EXTERNAL kind=race ... events=['except', 'finally'] on_cancel_called=['ep1', 'ep2']
CANCEL-EXTERNAL kind=some status=raised value=TypeError(...) events=[] on_cancel_called=['ep1']
DISCARD-INTERNAL kind=race status=ok waiters_left=0 events=['finally']
DISCARD-INTERNAL kind=some status=ok waiters_left=1 events=[]
```

**読み方**

- 普通に使う分には `WaitSome` は動きます(HAPPY)。
- Cancel すると run が TypeError で落ちます。traceback は `pick_next` の "raise" の枝の `return ResumeThrow(cont, error)` を指し、`cont` が state の dict です。except・finally は走りません。
- 外の約束の on_cancel は ep1 にしか届きません(#498 が破れる)。
- Discard の後、捨てた task の entry が waiters に 1 つ残ります。

**直すにはどこを変えるか**

- cancel の 1719 行に "some" と `remove_some_waiters` を足すと、7 行とも Race と同じ結果になりました(実測)。確かめた後、その手編集は戻しました。

**既存の検はこれを捕まえない**

- dispatch だけの版で、`tests/test_scheduler.py` は 88 passed でした。
- `tests/test_scheduler_trace.py` と `tests/test_scheduler_implementation.py` は 29 passed でした。

**副次(S3・実測)**

- 新しい ready の entry `("reschedule", owner, k, priority)` を、dispatch と `_RUNNABLE_ENTRY_KINDS` だけで足しました。
- それで待っている task を Cancel すると、except・finally を走らせずに task を捨てました。
  - 記録は `events=[]` で、警告「Cancel(task 0): the scheduler does not hold its continuation; the task is dropped without running its except/finally blocks」が出ました。
  - 対照の "resume" の entry では `['except', 'finally']` でした。

## 5. 実測と推測の区別、未確認の事

**実測**

- 上の pytest の出力 3 回分と traceback。
- cancel を直した後に Race と一致したこと。
- 既存の scheduler の検が通ること。

**推測(撃っていない)**

- Rust の scheduler は読んだだけで、組み立てていません。
  - 複数の鍵の登録を `WaiterEntry::Group{group}` と `groups` の表で表しているので、`WaitSome` を新しい `GroupKind` として足せば `detach_from_waiters` を変えずに済むはずです。だから今の漏洩は避けられる物で、必ずある共同の不変条件ではない、と考えます。
  - 一方、Rust の `detach_from_ready` には `_ => false` があり、S3 と同じ崩れが起きると推測します。
- 他にも種類の名を持つ所がありますが、測っていません。
  - `sweep_terminal_unobserved_entries` の 1185 行は gather・race だけを守ります。結果を最後に読み直す形の新しい effect なら変更が要ります。私の `WaitSome` は起きた時点で結果を保つので、影響しません。
  - 閉じる時の診断 `abandoned_ready_summary` は、"reschedule" を「捨てた仕事」の警告から落とすと推測します。

**未確認・不足**

- 設計者が「種類ごとの detach」を cancel の公開しない一部と見ているのか、dispatch に属すると見ているのかは、§2 からは分かりません(cancel の「隠す物」は「—」)。どちらにしても、dispatch から渡す口が無いことは変わりません。
- 実装する人が種類の名を "race" と偽って cancel に触れずに済ませる道は、試していません。その場合は Race の state の形(`pending_keys`・`waiter_k`)に縛られます。
- R1(手間が待つ task の数で増えないこと)・trace・sim の時計が絡む形では、`WaitSome` を測っていません。

**作業木の状態**

- `~/.worktrees/doeff-wt-blind-a-ki1294` に未 commit の変更と `tests/test_blind_a_waitsome.py` が残っています。scheduler.py には S2 の dispatch だけの差分と S3 の差分の両方が入っています。
- commit・push・他の会話への連絡はしていません。材料の 2 つの file と共有の checkout には触れていません。

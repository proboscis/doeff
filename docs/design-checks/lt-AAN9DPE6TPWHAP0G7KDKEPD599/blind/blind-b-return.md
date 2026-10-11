# 盲検 B の結果: 提示の検査を全部通り、走れる列の数えの持ち主を侵す実装

失敗例を 1 つ作りました。足したのは、走っている task の優先度を後から変える effect `SetTaskPriority` です。提示の 4 つの検査はどれもこの差分を拒みませんでした(実測)。一方で、外の約束を待つ間に IDLE へ下げた完了者が走らず、run が止まり続けました(実測)。

実験は `~/.worktrees/doeff-wt-blind-b-ki1294`(detached、1021b8946)の中だけで行い、commit も push もしていません。差分は未 commit のまま残してあります。

## 1. 機能要求と差分

**要求(私が立てた想定)**: turn-host で、手番が利用者の入力待ちになったら、その手番の後始末の task(記録の flush など)を `PRIORITY_IDLE` へ下げ、後で戻したい。Spawn の priority は生まれた時に固定なので、走っている task の優先度を変える effect を足す。既に走れる列に並んでいる entry も新しい優先度へ動かし、同じ優先度の中の FIFO は保つ。これは design.md §3 の S3「優先度を替えて並べ直す reschedule」に当たります。

**差分**(`packages/doeff-core-effects/doeff_core_effects/scheduler.py`、sha256 `33c27597…`):
- `Discard` の後に `class SetTaskPriority(EffectBase[None])` を足しました。`task` と `priority` を持ち、docstring は「以後の起こしは新しい優先度(生まれた時からその優先度だったように)・並んでいる entry も動かす・終わった task には何もしない」です。
- `handle_scheduler_effect` に次の分岐を足しました。同じ closure の `drop_claimed_marks` の `ready[:] = [...]` と `heapify` の形をそのまま写した、自然に書かれそうなコードです。

```python
elif isinstance(effect, SetTaskPriority):
    tid = effect.task.task_id
    task = tasks.get(tid)
    if task is not None and task["status"] not in terminal_statuses:
        task["priority"] = effect.priority
        ready[:] = [
            (-effect.priority, seq, entry)
            if entry[0] in _RUNNABLE_ENTRY_KINDS and entry[1] == tid
            else (neg, seq, entry)
            for neg, seq, entry in ready
        ]
        heapq.heapify(ready)
    r = yield Resume(k, None)
    return r
```

- `tests/test_scheduler.py` に、実装者が書きそうな正の検 `TestSetTaskPriority` を 4 条足しました(sha256 `c00a665a…`)。待ちの間に下げる・並んでいる間に下げる・上げる・終わった task には何もしない、の 4 つです。

## 2. 本来の持ち主と、侵す契約

- **持ち主は ready-counts(§2)**: `ready` を動かす口は `ready_push`・`ready_pop`・`ready_remove_at` で、不変条件 I-R1 は「`live_ready_count` は列から数え直した物と等しい」です。「優先度が `PRIORITY_EXTERNAL_WAIT` 以下の entry は生きた仕事として数える」という判断は、`ready_push`・`ready_pop`・`count_ready` の中にあります。
- **差分で移るもの**: 新しい分岐(dispatch)が、並んだ entry の優先度を `ready` の中で直に決め直します。entry が印の境(優先度 5)を数えの持ち主に知らせずに跨ぐので、下げると数え漏れ、上げると数え過ぎになります。heap の項の形 `(-priority, seq, entry)` という隠れた形にも、dispatch が頼るようになります。
- **測りの口(trace)の契約も破れる**: `ready_wake_count` は `-priority` ごとの数で、動いた entry が古い箱に残ります。そのため `ahead`・`ahead_by_wake` が今までと違う値になります。写した元の `drop_claimed_marks` は、自分の数え(`claimed_marks_in_ready`)を合わせ、測りの口の世代も消しています。外すのは生きた仕事として数えない印だけです。今回の写しは形だけを持ち出し、この後始末を持ち出していません。
- **公開の契約の破れ**:
  - R4/#505: 非 daemon の仕事は印の下でも走る。Spawn の docstring では「印の下の非 daemon の entry は、scheduler が止まる前に必ず走る」。
  - 新しい effect 自身の約束「生まれた時からその優先度だったように」(下の対照 A と結果が違う)。
- **§3 の S3 の主張が崩れる**: S3 は「列は ready_push・ready_pop を通るので数は合ったまま」と主張していました。新しい種類の entry を足さずに列をその場で書き換える実装では、この主張が成り立ちません。

## 3. 各検査が拒まない理由

いずれも提示の命令を撃った実測と、なぜ拒まないかの読みです。

| 検査 | 結果(実測) | 拒まない理由 |
|---|---|---|
| pytest(3 file) | `121 passed`。基準は `117 passed` | 足した分岐を通る検は、私が足した 4 条だけです。そこでは印を pop する場面が無いので、数えの食い違いが読まれません。#505 の既存の検は `Spawn(..., priority=IDLE)` を使い、`ready_push` を通ります。行の数の 2 条は WaitWithin の形しか測らず、新しい分岐は走りません。増えるのは elif の isinstance の 1 行で、16 本でも 256 本でも同じだけ増えます |
| ruff | 基準と同じ 6 件(PLW0603×2、PLR1711×3、SIM117)で、行の番号がずれただけ。exit 1 は基準も同じ | 書き方の規則しか見ていません。内包表記と heapify は規則に触れません |
| semgrep | `Ran 34 rules on 1 file: 0 findings.`、exit 0 | 当たる規則は退役した API・`@do` の作法などです。`ready`・`waiters` への直の書き換えを止める規則は無く、これは blind-b-checks.md の申告どおりです |
| pyright(参考) | 基準と同じ 25 errors・2 warnings。行の番号を外して比べ、集合が同じ | `ready` は型の無い list で、entry の形を型で表していません |

## 4. 最小の再現と期待結果

**手順**:
1. 作業木を切って `make sync` します。
2. 上の差分を入れ、提示の 4 つの命令を撃ちます。期待は上の表のとおりです(実測で一致)。
3. 作業木の根で `uv run python /tmp/blindb-scripts/repro_set_task_priority.py` を撃ちます(sha256 `95129781…`)。各場面を別の thread で 3 秒の打ち切り付きで走らせます。また、`has_live_ready_entry` が呼ばれた瞬間の frame から、持っている数と列から数え直した数を写して比べます。

**本体の入力**(`implementation="python"`):

```python
@do
def completer(gate, ep):
    yield Wait(gate.future)
    ep.complete("from-lowered-task")

@do
def body():
    gate = yield CreatePromise()
    ep = yield CreateExternalPromise()
    t = yield Spawn(completer(gate, ep))
    yield CompletePromise(gate, None, yield_to_woken=False)  # t の resume が NORMAL で並ぶ
    yield SetTaskPriority(t, PRIORITY_IDLE)
    value = yield Wait(ep.future)
    yield Wait(t)
    return value
```

**結果**(すべて実測):

| 場面 | 違反の版 | 対照 C(同じ機能を `ready_remove_at`+`ready_push` で書いた版) |
|---|---|---|
| 1 本体 | 3 秒で終わらない。40 秒でも終わらず、30 秒で `scheduler stalled 30s …; parked waiters: ['root (wait_external) on promise 1']` の警告。数え `[0,0]`、列からの数え直し `[1,1]`、列は `[(0,'resume',2)]` | 0.08 秒で `"from-lowered-task"`。数え `[1,1]` = 数え直し `[1,1]` |
| A: 最初から `Spawn(..., priority=IDLE)` | 0.01 秒で値。数え = 数え直し `[1,1]` | 同じ |
| B: 待ちの間に下げる(列に居ない) | 0.00 秒で値。数え = 数え直し | 同じ |
| 2: IDLE で並んだ resume を NORMAL へ上げる | 終わる(`"late"`)が、後の印の pop で数え `[1,1]`・数え直し `[0,0]` の数え過ぎ。意味の破れは観測していない | 数え = 数え直し `[0,0]` |
| 3: 測りの口を開いたまま、外の完了は別の thread が 0.5 秒後 | 順は `ep1 completed` が先で `lowered task ran` が後。外の enqueued は `ahead=1, ahead_by_wake={"internal":1}`。その時の列は `[(10,resume,root),(0,resume,2)]` で、前に並ぶ物は本当は 0 | 順は `lowered task ran` が先。`ahead=0` |

## 5. 未確認の前提と、推測と実測の区別

**推測・未確認のもの**:
- `SetTaskPriority` の要求は私の想定です。この effect を求める実在の依頼は見ていません。
- 本番では外の完了の多くが別の thread から来ます。その場合の結果は止まりではなく遅れ(場面 3 の順)です。別の thread の完了者が、下げた task の出力を待つ形なら止まると読みますが、測っていません。
- Rust の scheduler がこの effect を扱わない事(S9 の「2 つの実装は同じ意味」)は測っていません。
- 提示されていない検査(doeff-linter・commit の hook・他の package の検)は撃っておらず、通るかどうかも推測しません。
- semgrep が当てる 34 個の規則の名は全部は取れていません。自作の近似の script で 30 個を出し、`ready`・`waiters` を見る物が無い事は確かめました。残りの 4 個は特定していません。

**実測に付く注意**:
- 場面 3 の対照の順は 0.5 秒の timer に頼っています。機体が混んで 0.5 秒以上止まると、順が入れ替わる事があり得ます(推測)。場面 1 の止まりは時計に頼った判定ではなく、40 秒の待ちと stall の警告で確かめています。
- frame から closure の変数を読む覗きは、この Python 3.14.3t の上で動く事を実測しました。

**再現に使う file**:
- 作業木: `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki1294`(差分は未 commit)
- 再現の script: `/tmp/blindb-scripts/repro_set_task_priority.py`
- 違反の版・対照 C の版・pyright の比べ: `/tmp/blindb-save/`

WAIT: work — 依頼元の次の指示

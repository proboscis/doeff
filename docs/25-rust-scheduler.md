# 25. Rust の scheduler

`scheduled()` の scheduler(Spawn・Wait・Gather・Race・Cancel・Discard・Semaphore・Promise・
ExternalPromise)は Rust の版と Python の版の 2 つを持つ。意味は同じで、既定は Rust の版(Python の版は選んで使う)。
Rust の版は `packages/doeff-vm/src/scheduler.rs`、Python の版は
`packages/doeff-core-effects/doeff_core_effects/scheduler.py` の `_scheduled_python`。

## 切り替え

```python
from doeff_core_effects.scheduler import scheduled

run(scheduled(main()))                              # 既定(DEFAULT_IMPLEMENTATION = "rust")
run(scheduled(main(), implementation="python"))    # この呼び出しだけ Python の版
```

```sh
DOEFF_SCHEDULER=python python app.py                # process 全体を Python の版にする
```

決まる順は「引数 → 環境変数 `DOEFF_SCHEDULER` → `DEFAULT_IMPLEMENTATION`」。
環境変数は `scheduled()` を呼ぶたびに読むので、テストでは `monkeypatch.setenv` で切り替えられる。
`python` / `rust` 以外の値は `ValueError` になる。
Rust の版を選んだ(既定を含む)のに、読み込んだ Rust の拡張が古くて scheduler を持たない時は、黙って Python の版へ
落とさず `ImportError`(Rust の拡張を build し直す、または `DOEFF_SCHEDULER=python`、と名指す)にする。

## 既定を Rust の版にした理由と、Python の版を選ぶ時

- 決めたこと(2026-10-17・元に戻せる決定): 既定を `"rust"` にする。task 1 個の CPU の時間が約 9 分の 1 になり
  (下の「既定を替えた時の測り」)、多くの task を回す時に asyncio の 1.4〜1.5 倍まで縮む。
- 2026-09-23 の決め(既定を `"python"` に留める)の理由は、Python の source だけを本線に追随して Rust の拡張を
  build し直さない環境で `scheduled()` が `ImportError` になる事だった。PyPI の 0.5.0 以降の doeff-vm の wheel と、
  `make sync`(`uv sync`)で入れた checkout は Rust の scheduler を持つ。古い拡張のままの checkout は
  `ImportError` の文に従って build し直すか、`DOEFF_SCHEDULER=python` を置く。
- 意味が同じことの確認: 2026-09-23 に、scheduler に関係するテスト 1,382 件(scheduler・Cancel・Semaphore・外部 Promise・
  Await・doeff-time の仮想の時計・doeff-agents・doeff-conductor・doeff-traverse ほか)と下流の worker のテスト 100 件が、
  両方の版で 1 件ずつ同じ結果になった(合格・不合格・skip の組が完全に一致)。既定を替えた後は、版を名指さない
  テストが Rust の版で走る(日次の全体の検が両方の版の違いを拾う)。版に依る振る舞いのテストは版を名指す。
- 測りの口 `set_scheduler_trace` の出来事(歩の積算の答え手 `step-tally-handler` の `OpenStepTally`・
  `OpenTaskTally` を含む)は、2026-10-18 から両方の版が出す — 同じ種類・同じ欄の出来事を同じ順で(下の
  「測りの口」)。歩を数えるために Python の版を名指す要は無い。
- Python の版を選ぶ時:
  1. scheduler の中を pdb で 1 行ずつ追う時(Rust の版の判断は Rust の中で、Python の frame が無い)。
  2. Rust の拡張を build し直せない checkout を急いで動かす時。
- 選び方と戻し方(どれも元に戻せる):
  1. 1 つの process だけ: `DOEFF_SCHEDULER=python`。
  2. 1 つの呼び出しだけ: `scheduled(..., implementation="python")`。
  3. 既定そのもの: `scheduler.py` の `DEFAULT_IMPLEMENTATION` を `"python"` に戻す。

## 何が Rust に移り、何が Python に残るか

Rust に移ったもの: effect ごとの判断のすべて — task・promise・semaphore の状態、準備のできた
task の優先度つきの列(CPython の `heapq` と同じ手順の移植なので、列の並びも Python の版と同じ)、
Cancel、外部の完了の受け取り、行き詰まりの検出、#502 の掃除(後始末の済んだ取り消し済みの task を
掃く #3504 も含む。`tests/test_scheduler.py::TestTerminalEntrySweep` が両方の版で確かめている)。

Python に残したもの(利用者から見える形を変えないため):

- effect の class(`Spawn`・`Wait`・`AcquireSemaphore` など)。Rust の版は `isinstance` で
  見分けるので、子 class(下の `CreateNamedSemaphore` など)は今までどおり親として扱われる。
- handle の class(`Task`・`Future`・`Promise`・`ExternalPromise`・`Semaphore`)と例外の class。
- 1 回の実行につき 1 回だけ走る、終わり際の警告(#501 と、誰も受け取らなかった task の例外の警告)と、
  失敗した task ごとに 1 回だけ走る、traceback の補強と、受け取られていない例外の説明の文。
- 測りの口の出来事の dict と時計(`_trace`・`_trace_step_boundary`)。下の「測りの口」。

### 測りの口(`set_scheduler_trace`)

- 両方の版が、sink が据えてある時だけ、同じ種類(start・enqueued・resumed・spawned・task-enter・task-leave・
  external-complete・external-drained・external-ignored)・同じ欄の出来事を同じ順で出す。違うのは時計の値と
  doeff-vm の歩数(`step_vm_steps`・`step_handler_calls` — Rust の版は少ない歩で同じ仕事をする)と run の番号だけ。
  `tests/test_scheduler_trace.py` が場面ごとに両方の版の並びを照らし、歩の積算の test(`test_step_tally.py`・
  `test_task_tally.py`)は両方の版で走り、同じ program の歩の数と task の木が両方の版で一致する事を確かめる。
- 出来事の dict と時計は Python の版の helper を Rust の版も呼んで作る(定義は 1 つ)。Rust の版が自分で数えるのは、
  起こされ方(wake)と前に並ぶ数(ahead・ahead_by_wake)と歩の境目の置き場だけ。
- Python の版の handler は prompt を通る効果を全部受け、scheduler の効果でない物にも task-leave を出して外の完了を
  drain してから `Pass` する。Rust の版は scheduler の効果でない物を `accepts` で断るので、sink がある時だけ、断る時に
  同じ task-leave と drain をする(外の handler が答える効果も歩を区切る)。
- sink が無い間の費用: 出し所ごとに process に 1 つの旗(`TRACE_INSTALLED`、`set_scheduler_trace` が sink と
  一緒に切り替える)を 1 度読むだけで、dict も時計の読みも Python の呼び出しも無い。run の途中で据えても外しても
  次の出来事から従う(Rust の版を組んだ後に据えた sink も出来事を受ける)。
- sink が例外を上げた時: Rust の版の中の出し所は状態の書き換えの途中なので、そこから上げない — Exception は
  `sys.unraisablehook` へ渡して run を続け、KeyboardInterrupt などは次に再開する task の yield へ投げ込む
  (Python の版では出した所から上がる)。

### handler の形の違い(意味は同じ)

Python の版は `@do` の generator の handler、Rust の版は同期の handler(`call_handler` が
すぐ次の命令を返す)。そのため次の 3 点だけ書き方が違うが、VM から見た結果は同じ。

- 別の task へ切り替える時、Python の版は `TailEval(Transfer(k, v))`、Rust の版は
  `Resume(k, v)`。Python の版の `Transfer` は自分の handler の frame を外すための物で、
  Rust の版には外す frame が無い(`Transfer` を使うと呼び手の frame を外してしまう)。
- scheduler が扱わない effect は、Python の版は `yield Pass(effect, k)`、Rust の版は
  `Callable::accepts` で断る。VM はどちらも「scheduler の外側から effect を出し直す」。
- effect の処理中に起きた例外は、どちらの版も effect を出した側の `yield` の位置へ投げ込まれる
  (Python の版は VM の handler 用の復旧で、Rust の版は保持している継続へ直接)。

## 下流が使う口(両方の版で保たれる)

`tests/test_scheduler_implementation.py` が両方の版で確かめている。

1. **effect の子 class**: `class CreateNamedSemaphore(CreateSemaphore)` のような子 class は、
   それを拾う handler が無ければ親の effect として scheduler が解く。
2. **`scheduled` の内側に置く handler**: 名前付きの semaphore の handler(agora の worker の
   `named-semaphore-local` / `cluster-semaphore`)のように、scheduler の effect を先に受けて
   自分で答える、または `Pass` で scheduler へ回すことができる。scheduler は内側の handler が
   回した effect だけを受ける。
3. **捕まえた handler の列にある scheduler**: `GetHandlers` / `GetOuterHandlers` /
   boundary の捕獲で得た scheduler は `__doeff_scheduler_prompt__` が真の object になる
   (Rust の版は `doeff_vm.doeff_vm.SchedulerPrompt`)。`handler(h)(program)` で
   そのまま付け直せる。doeff-agents の `mcp_server_loop` が
   この印で scheduler を見分けている。
4. **scheduler と協調する handler**: 仮想の時計の handler のように、handler の中から
   `CreatePromise`・`Spawn(..., priority=PRIORITY_IDLE, daemon=True)`・`Wait` を出して
   よい。優先度・daemon・外部待ちの盾(#505)の扱いは Python の版と同じ。

## 意味が同じであることの要点

特に Cancel は Python の版と一行ずつ対応させてある(`scheduler.rs` の `on_cancel`)。

- 巻き戻し中の task は生きている扱い(`cancelling`)。
- 待っている側は巻き戻しが終わってから起きる。
- 1 回の Cancel で例外は 1 回だけ投げ込まれる。握りつぶした task は次の Cancel まで走り続ける。
- 後始末で別の例外が起きたら、それを隠さず `failed` として伝える。
- まだ動いていない task は本体を走らせずに取り消す。
- 取り消した task だけが待っていた外部の Promise は `on_cancel` を呼んで取り消す
  (失敗は `ExternalPromiseCancelCallbackError` として Cancel を出した側へ)。

Discard(巻き戻さずに捨てる・SIGKILL と同じ形)も Python の版と一行ずつ対応させてある(`scheduler.rs` の `on_discard`)。
捨てた task の続きはどちらの版でも落とすだけで、finally の中の effect は走らない。

誰も受け取らなかった task の例外(card ki-84667a89b47a)も、両方の版で同じに扱う
(`tests/test_scheduler_unretrieved_task_exception.py` が両方の版で確かめている)。

- Wait・Gather・Race・Cancel・Discard のどれかに渡された task は、終わる前でも後でも「受け取られた」とする。
- 受け取られないまま例外(`TaskCancelledError` を除く)で終わった task は、終わった時に説明の文を作る。
  文を作る関数は Python の `_describe_unretrieved_exception` の 1 つだけで、program の名は Spawn の時に
  控えた関数(`@do` の呼びでなければ program の型)から取る。
- run の終わりに、その文を 1 つの `RuntimeWarning` にまとめて出す。#502 の掃除で行が消えた task の文も
  残しておき、名指すのは `UNRETRIEVED_EXCEPTIONS_NAMED` 個まで(残りは数だけ言う)。

Python の版から引き継いだ振る舞い(変えていない): root の本体が終わった時に置き去りに
なった task(daemon の聞き手など)の継続は、実行の後も解放されず、その `finally` も走らない。
Python の版でも同じ(継続の object が GC から見えない循環を作るため)。

## 測定

道具: `benchmarks/scheduler_bench.py`(`--impl python|rust`)。1 操作あたりの µs、
出荷と同じ release build(LTO あり・VM の自己検査なし)、Apple M 系の Mac、
free-threaded の Python 3.14、交互に 3 回ずつ走らせた最良値(2026-09-23)。

| 流れ | 1 操作 | Python の版 | Rust の版 | 倍 |
|---|---|---:|---:|---:|
| spawn_wait | Spawn + Wait | 33.9 | 4.2 | 8.0 |
| gather | Gather の子 1 つ(Spawn を含む) | 32.0 | 4.1 | 7.8 |
| race | Race 1 回(Spawn 2 回を含む) | 64.3 | 8.1 | 7.9 |
| semaphore_uncontended | Acquire + Release | 13.1 | 1.1 | 12.0 |
| semaphore_contended | Acquire + Spawn + Wait + Release(4 task) | 46.6 | 5.5 | 8.5 |
| promise | CreatePromise + Spawn(Complete) + Wait | 47.1 | 6.3 | 7.5 |
| cancel | Spawn + Cancel + finally + Wait | 106.2 | 16.5 | 6.4 |
| pass_through | scheduler を素通りする effect 1 回 | 4.2 | 1.1 | 3.7 |

### 時間の内訳

native の標本(macOS の `sample`)と Python の標本(`sys._current_frames`)を同じ process から
取り、葉の関数で分けた(計測用に LTO を切った build。道具は
`~/experiments/doeff-core-typing/scripts/profile_scheduler.py`)。割合は spawn_wait の例。

| 区分 | Python の版 | Rust の版 |
|---|---:|---:|
| Rust と Python の境の往復 | 26〜31% | 55〜60% |
| scheduler の Python の処理 | 22〜27% | 6%(handle の class の生成) |
| doeff の Python 側の部品(`@do` の包み・`get_inner_boundaries` など) | 20〜24% | 3〜4% |
| scheduler の Rust の処理 | — | 7〜9% |
| VM 本体 | 17〜20% | 12〜16% |
| 利用者の program | 3〜5% | 2〜4% |

- Python の版では、境の往復のうち大きな部分が、handler の `yield Resume(k, ...)` のたびに
  「末尾の Resume か」を判定するため巨大な handler 関数の現在の行番号を数え直す処理だった
  (競合なしの Semaphore では時間の 3 割)。Rust の版にはこの処理が無い。
- Rust の版で残る時間の大半は、利用者の generator を動かす VM と Python の境
  (`generator.send`・Python の thread 状態の取得・object の変換)で、scheduler の外にある。
  scheduler 自身(Rust)は 1 往復あたり 0.4〜0.8 µs。

### 改善が小さい部分とその理由

- **pass_through(3.7 倍)**: この流れの時間の多くは scheduler ではなく、外側に置いた
  Python の handler と VM の境で使われる。scheduler の分は「扱わない effect を型で断る」
  だけになり、ほぼ消えた。
- **cancel(6.4 倍)**: 取り消しは task へ例外を投げ込み、`finally` を走らせ、失敗した task の
  traceback を Python の補助関数で補強する。この補強と例外の生成は Python の版と同じ意味を
  保つため Python に残した。
- **handle の生成**: `Task(...)`・`Promise(...)` は下流が子 class を作る Python の class なので、
  生成は Python の呼び出しのまま(1 回あたり 0.1〜0.3 µs)。

### 既定を替えた時の測り

道具: `benchmarks/compare_libraries.py --runs 30 --library python --library doeff`。`scheduled(body)` を版を名指さずに
呼ぶ case(`default = <版> scheduler`)を、既定を替える前と後の同じ木で測った。task 1 個あたりの CPU の µs の中央
(括弧は p90)。Linux の 36 論理 CPU(load average 96〜110 の混んだ機体)、free-threaded の Python 3.14.3、2026-10-17。

| 流れ | 替える前(既定 = Python の版) | 替えた後(既定 = Rust の版) | 倍 | asyncio |
|---|---:|---:|---:|---:|
| spawn_gather(何もしない task 1,000 個を出して集める) | 153.5(165.5) | 19.4(27.4) | 7.9 | 11.8〜11.9 |
| fan_out_wait(1,000 個が 1 つの約束を待ち、一度に起きる) | 199.5(223.7) | 32.6(59.4) | 6.1 | 16.7〜17.4 |

- 既定は asyncio の 13 倍と 12 倍から、1.6 倍と 1.9 倍になった。
- 同じ走りで版を名指した case の中央は、Python の版 149〜152 と 193〜196、Rust の版 17.5〜19.2 と 30.2〜31.9。
  既定の case は名指した case と同じ桁で、既定の選びの費用は見えない。
- 混んでいない機体(load average 27〜30)では、Rust の版は spawn_gather 12.5・fan_out_wait 19.9 だった。

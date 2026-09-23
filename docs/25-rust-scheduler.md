# 25. Rust の scheduler

`scheduled()` の scheduler(Spawn・Wait・Gather・Race・Cancel・Semaphore・Promise・
ExternalPromise)は Rust の版と Python の版の 2 つを持つ。意味は同じで、既定は Rust の版。
Rust の版は `packages/doeff-vm/src/scheduler.rs`、Python の版は
`packages/doeff-core-effects/doeff_core_effects/scheduler.py` の `_scheduled_python`。

## 切り替え

```python
from doeff_core_effects.scheduler import scheduled

run(scheduled(main()))                            # 既定(DEFAULT_IMPLEMENTATION = "rust")
run(scheduled(main(), implementation="python"))  # この呼び出しだけ Python の版
```

```sh
DOEFF_SCHEDULER=python python app.py              # process 全体を Python の版へ戻す
```

決まる順は「引数 → 環境変数 `DOEFF_SCHEDULER` → `DEFAULT_IMPLEMENTATION`」。
環境変数は `scheduled()` を呼ぶたびに読むので、テストでは `monkeypatch.setenv` で切り替えられる。
`python` / `rust` 以外の値は `ValueError` になる。

## 既定を Rust の版にした理由と戻し方

- 決めたこと(2026-09-23): 既定を `"rust"` にする。
- 理由: scheduler に関係するテスト 1,382 件(scheduler・Cancel・Semaphore・外部 Promise・
  Await・doeff-time の仮想の時計・doeff-agents・doeff-conductor・doeff-traverse ほか)と、
  下流の agora-controllers の worker のテスト 100 件が、両方の版で 1 件ずつ同じ結果になった
  (合格・不合格・skip の組が完全に一致)。そのうえで下の測定のとおり 3.7〜12 倍速い。
- 戻し方(どれも元に戻せる):
  1. 1 つの process だけ: `DOEFF_SCHEDULER=python`。
  2. 1 つの呼び出しだけ: `scheduled(..., implementation="python")`。
  3. 既定そのもの: `scheduler.py` の `DEFAULT_IMPLEMENTATION` を `"python"` に戻す。

## 何が Rust に移り、何が Python に残るか

Rust に移ったもの: effect ごとの判断のすべて — task・promise・semaphore の状態、準備のできた
task の優先度つきの列(CPython の `heapq` と同じ手順の移植なので、列の並びも Python の版と同じ)、
Cancel、外部の完了の受け取り、行き詰まりの検出、#502 の掃除。

Python に残したもの(利用者から見える形を変えないため):

- effect の class(`Spawn`・`Wait`・`AcquireSemaphore` など)。Rust の版は `isinstance` で
  見分けるので、子 class(下の `CreateNamedSemaphore` など)は今までどおり親として扱われる。
- handle の class(`Task`・`Future`・`Promise`・`ExternalPromise`・`Semaphore`)と例外の class。
- 1 回の実行につき 1 回だけ走る、終わり際の警告(#501)と、失敗した task の traceback の補強。

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
   (Rust の版は `doeff_vm.doeff_vm.SchedulerPrompt`)。`handler(h)(program)` /
   `WithHandler(h, program)` でそのまま付け直せる。doeff-agents の `mcp_server_loop` が
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

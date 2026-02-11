# 第12章: ランタイムとスケジューラ

## この章で学ぶこと

- ランタイムの役割
- 利用可能なランタイムの種類
- スケジューラの仕組み
- カスタムランタイムの作成

---

## 12.1 ランタイムとは

ランタイムはエフェクトを「解釈」して実行する。

```
┌─────────────────────────────────────┐
│  Program（エフェクトの列）           │
│  yield Log("...")                   │
│  yield Get("key")                   │
│  yield Await(coro)                  │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│  ランタイム                          │
│  - エフェクトを解釈                  │
│  - 実際の処理を実行                  │
│  - 結果をジェネレータに返す          │
└─────────────────────────────────────┘
```

---

## 12.2 AsyncRuntime

本番環境で使う非同期対応ランタイム。

```python
from doeff import AsyncRuntime

async def main():
    runtime = AsyncRuntime()
    
    result = await runtime.run(
        my_program(),
        env={"config": "value"},
        store={"initial_state": 0}
    )
    
    if result.is_ok():
        print(f"Success: {result.value}")
    else:
        print(f"Error: {result.error}")
```

### 特徴

- `asyncio` をベースとした非同期 I/O
- `Await`, `Gather`, `Spawn` をフルサポート
- `Delay` は `asyncio.sleep` を使用

---

## 12.3 SimulationRuntime

テストや開発用の同期ランタイム。

```python
from doeff import SimulationRuntime

def test_program():
    runtime = SimulationRuntime()
    
    # async を使わずに実行
    result = runtime.run(my_program())
    
    assert result.is_ok()
    assert result.value == expected_value
```

### 特徴

- 同期的に実行（`async` 不要）
- `Delay` は即座に完了（時間をシミュレート）
- `Gather` は順次実行
- テストに最適

### 時間のシミュレーション

```python
@do
def slow_program():
    yield Delay(3600)  # 1時間
    return "done"

def test_slow():
    runtime = SimulationRuntime()
    # 即座に完了
    result = runtime.run(slow_program())
    assert result.value == "done"
```

---

## 12.4 SyncRuntime（概念的な参照実装）

`SyncRuntime` は同期的にエフェクトを解釈する最もシンプルなランタイムだ。
doeffのエフェクト解釈モデルを理解するための概念的な参照実装として有用。

```python
from doeff import SyncRuntime

# 同期的に実行（async不要）
runtime = SyncRuntime()
result = runtime.run(my_program())
```

実用上は `AsyncRuntime` を使うことが多いが、
ランタイムの内部動作を理解したい場合に `SyncRuntime` のコードを読むと良い。

---

## 12.5 ランタイムの選択

| ランタイム | 用途 | Delay の動作 | Gather の動作 |
|-----------|------|-------------|---------------|
| `AsyncRuntime` | 本番（リファレンス実装） | `asyncio.sleep` | 並列実行 |
| `SyncRuntime` | 概念的な参照実装 | `time.sleep` | 順次実行 |
| `SimulationRuntime` | テスト | 即座に完了 | 順次実行 |

```python
# 本番
async def production():
    runtime = AsyncRuntime()
    return await runtime.run(program())

# テスト
def test():
    runtime = SimulationRuntime()
    return runtime.run(program())
```

### エフェクト対応表

各ランタイムでサポートされるエフェクトの一覧。

#### Reader / State / Writer

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| Ask | `Ask(key)` | ✅ | ✅ | ✅ |
| Local | `Local(env, prog)` | ✅ | ✅ | ✅ |
| Get | `Get(key)` | ✅ | ✅ | ✅ |
| Put | `Put(key, value)` | ✅ | ✅ | ✅ |
| Modify | `Modify(key, func)` | ✅ | ✅ | ✅ |
| Log / Tell | `Log(msg)` / `Tell(msg)` | ✅ | ✅ | ✅ |
| Listen | `Listen(prog)` | ✅ | ✅ | ✅ |

#### 制御フロー / IO

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| Pure | `Pure(value)` | ✅ | ✅ | ✅ |
| Safe | `Safe(prog)` | ✅ | ✅ | ✅ |
| IO | `IO(action)` | ✅ | ✅ | ✅ |

#### キャッシュ

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| CacheGet | `CacheGet(key)` | ✅ | ✅ | ✅ |
| CachePut | `CachePut(key, value)` | ✅ | ✅ | ✅ |
| CacheExists | `CacheExists(key)` | ✅ | ✅ | ✅ |
| CacheDelete | `CacheDelete(key)` | ✅ | ✅ | ✅ |

#### 時間

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| Delay | `Delay(seconds)` | ✅ `time.sleep` | ✅ 即座に完了 | ✅ `asyncio.sleep` |
| GetTime | `GetTime()` | ✅ | ✅ シミュレート時刻 | ✅ |
| WaitUntil | `WaitUntil(dt)` | ✅ 実時間待機 | ✅ 即座に完了 | ✅ 非同期待機 |

#### 並行処理

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| Gather | `Gather(*progs)` | ⚠️ 順次実行 | ⚠️ 順次実行 | ✅ 並列実行 |
| Spawn | `Spawn(prog)` | ❌ | ❌ | ✅ |
| Await | `Await(awaitable)` | ❌ | ❌ | ✅ |

`Spawn` が返す `Task` オブジェクトには以下のメソッドがある（内部的にそれぞれエフェクトとして発行される）:

| メソッド | 内部エフェクト | 説明 |
|---------|--------------|------|
| `task.join()` | `TaskJoinEffect` | タスクの完了を待機し結果を取得 |
| `task.cancel()` | `TaskCancelEffect` | タスクのキャンセルを要求 |
| `task.is_done()` | `TaskIsDoneEffect` | タスクの完了状態を確認 |

いずれも `AsyncRuntime` でのみ利用可能。

#### アトミック / デバッグ / グラフ

| エフェクト | コンストラクタ | Sync | Simulation | Async |
|-----------|--------------|------|------------|-------|
| AtomicGet | `AtomicGet(key)` | ✅ | ✅ | ✅ |
| AtomicUpdate | `AtomicUpdate(key, fn)` | ✅ | ✅ | ✅ |
| ProgramCallFrame | `ProgramCallFrame(depth)` | ✅ | ✅ | ✅ |
| ProgramCallStack | `ProgramCallStack()` | ✅ | ✅ | ✅ |
| Step | `Step(value, meta)` | ✅ | ✅ | ✅ |
| Annotate | `Annotate(meta)` | ✅ | ✅ | ✅ |
| Snapshot | `Snapshot()` | ✅ | ✅ | ✅ |
| CaptureGraph | `CaptureGraph(prog)` | ✅ | ✅ | ✅ |

> **凡例**: ✅ サポート / ⚠️ 制限付き / ❌ 非サポート
>
> 並行処理エフェクト（`Spawn`, `Await` 等）は `AsyncRuntime` でのみ利用可能。
> `Gather` は全ランタイムで動作するが、`SyncRuntime` と `SimulationRuntime` では順次実行となる。

---

## 12.6 RuntimeResult

`runtime.run()` は `RuntimeResult` を返す。

```python
result = await runtime.run(program())

# 成功/失敗のチェック
result.is_ok()     # bool
result.is_err()    # bool

# 値の取得
result.value       # 成功時の値（エラー時は例外）
result.error       # エラー時の例外（成功時は例外）

# 実行コンテキスト
result.state       # 最終状態 (dict)
result.log         # 蓄積されたログ (list)
result.env         # 環境 (dict)

# デバッグ情報
result.k_stack        # 継続スタック
result.effect_stack   # エフェクトコールツリー
result.python_stack   # Python スタックトレース
```

### デバッグ出力

```python
if result.is_err():
    # フォーマットされた出力
    print(result.format(verbose=True))
```

---

## 12.7 スケジューラの仕組み

doeff のランタイムは Rust VM に基づいている。

### Rust VM 実行モデル

| 要素 | 説明 |
|------|------|
| **C** (Control) | 現在実行中のプログラム |
| **E** (Environment) | 環境（設定） |
| **S** (Store) | 状態 |
| **K** (Kontinuation) | 継続（次に何をするか） |

### 実行サイクル

```
1. ジェネレータから次のエフェクトを取得
2. エフェクトの種類を判定
3. 適切なハンドラを呼び出す
4. 結果をジェネレータに send()
5. 1に戻る（StopIteration まで）
```

---

## 12.8 エフェクトハンドラ

各エフェクトはハンドラによって処理される。

### 組み込みハンドラの例

```python
# Log エフェクト
def handle_log(effect, state):
    state.log.append(effect.message)
    return None

# Get エフェクト
def handle_get(effect, state):
    return state.store[effect.key]

# Put エフェクト
def handle_put(effect, state):
    state.store[effect.key] = effect.value
    return None

# Await エフェクト（AsyncRuntime）
async def handle_await(effect, state):
    return await effect.awaitable
```

---

## 12.9 カスタムエフェクトとハンドラ

独自のエフェクトを定義できる（上級者向け）。

### カスタムエフェクトの定義

```python
from dataclasses import dataclass
from doeff import EffectBase

@dataclass(frozen=True)
class SendEmail(EffectBase):
    to: str
    subject: str
    body: str

# 使用
@do
def notify_user(email):
    yield SendEmail(
        to=email,
        subject="Notification",
        body="Hello from doeff!"
    )
```

### カスタムハンドラの登録

```python
# ランタイムにハンドラを追加
runtime = AsyncRuntime()
runtime.register_handler(SendEmail, handle_send_email)

async def handle_send_email(effect, state):
    # 実際のメール送信処理
    await email_service.send(effect.to, effect.subject, effect.body)
    return None
```

---

## 12.10 ベストプラクティス

### ランタイムの選択

- **本番**: `AsyncRuntime`
- **テスト**: `SimulationRuntime`

### エラー処理

```python
result = await runtime.run(program())

if result.is_err():
    # エラーを適切に処理
    logger.error(f"Program failed: {result.error}")
    logger.error(result.format(verbose=True))
```

### 環境と状態の分離

```python
# 環境: 読み取り専用の設定
env = {
    "database_url": "postgres://...",
    "api_key": "..."
}

# 状態: 変更可能なデータ
store = {
    "counter": 0,
    "cache": {}
}

result = await runtime.run(program(), env=env, store=store)
```

---

## まとめ

| ランタイム | 用途 | 非同期 |
|-----------|------|--------|
| `AsyncRuntime` | 本番環境（リファレンス実装） | Yes |
| `SyncRuntime` | 概念的な参照実装 | No |
| `SimulationRuntime` | テスト | No |

- ランタイムがエフェクトを解釈・実行する
- Rust VM モデルに基づいている
- `RuntimeResult` で結果とデバッグ情報を取得

次の章では、Pure Core パターンを見ていく。

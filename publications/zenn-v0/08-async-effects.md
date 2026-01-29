# 第8章: 非同期処理

## この章で学ぶこと

- `Await` エフェクトの使い方
- `Gather` による並列実行
- `Spawn` によるバックグラウンドタスク
- 時間関連のエフェクト

---

## 8.1 Await エフェクト

Python の `async/await` を doeff に統合する。

### 基本的な使い方

```python
import asyncio
from doeff import do, Await, Log
from doeff import AsyncRuntime

async def fetch_data():
    await asyncio.sleep(0.1)
    return {"user_id": 123, "name": "Alice"}

@do
def process_user():
    yield Log("Fetching user data...")
    
    # 非同期関数を呼び出す
    data = yield Await(fetch_data())
    
    yield Log(f"Received: {data}")
    return data["name"]

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(process_user())
    print(result)  # Alice
```

### HTTP リクエスト

```python
import httpx

@do
def fetch_api():
    async with httpx.AsyncClient() as client:
        response = yield Await(
            client.get("https://api.example.com/data")
        )
        
        if response.status_code == 200:
            yield Log("API request successful")
            return response.json()
        else:
            raise Exception(f"HTTP {response.status_code}")
```

### 複数の非同期操作を順番に

```python
@do
def sequential_fetches():
    # 順番に実行
    user = yield Await(fetch_user(123))
    yield Log(f"Fetched user: {user['name']}")
    
    posts = yield Await(fetch_posts(user["id"]))
    yield Log(f"Fetched {len(posts)} posts")
    
    return {"user": user, "posts": posts}
```

---

## 8.2 Gather エフェクト

複数の `Program` を並列実行する。

### 基本的な使い方

```python
from doeff import do, Gather, Log, Delay

@do
def task1():
    yield Delay(0.1)
    return "result1"

@do
def task2():
    yield Delay(0.1)
    return "result2"

@do
def task3():
    yield Delay(0.1)
    return "result3"

@do
def parallel_tasks():
    yield Log("Starting parallel tasks...")
    
    # 全て並列に実行
    results = yield Gather(task1(), task2(), task3())
    
    yield Log(f"All done: {results}")
    return results  # ["result1", "result2", "result3"]
```

### Await との違い

| `Await` | `Gather` |
|---------|----------|
| Python コルーチンを待つ | `Program` を並列実行 |
| エフェクトなし | フルエフェクトサポート |
| 単一の操作 | 複数の操作 |

### 実践例: 複数 API の並列呼び出し

```python
@do
def fetch_from_api(endpoint):
    yield Log(f"Fetching {endpoint}...")
    response = yield Await(httpx_client.get(endpoint))
    yield Log(f"Got response from {endpoint}")
    return response.json()

@do
def fetch_dashboard_data():
    # 3つの API を並列に呼び出す
    results = yield Gather(
        fetch_from_api("/users"),
        fetch_from_api("/posts"),
        fetch_from_api("/stats")
    )
    
    return {
        "users": results[0],
        "posts": results[1],
        "stats": results[2]
    }
```

### パフォーマンス比較

```python
# 順次実行: 300ms
@do
def sequential():
    r1 = yield task1()  # 100ms
    r2 = yield task2()  # 100ms
    r3 = yield task3()  # 100ms
    return [r1, r2, r3]

# 並列実行: 100ms
@do
def parallel():
    return yield Gather(task1(), task2(), task3())
```

---

## 8.3 Spawn エフェクト

バックグラウンドタスクを作成する。

### Fire-and-Forget

```python
@do
def send_notification():
    yield Log("Sending notification...")
    yield Await(email_service.send("Hello!"))
    yield Log("Notification sent")

@do
def main_workflow():
    # 通知をバックグラウンドで送信
    yield Spawn(send_notification())
    
    # 即座に続行
    yield Log("Workflow continues...")
    return "done"
```

### 結果を後で取得

```python
@do
def background_computation():
    yield Delay(1.0)
    return 42

@do
def main_program():
    yield Log("Starting background task")
    
    # タスクをスポーン
    task = yield Spawn(background_computation())
    
    # 他の作業を行う
    yield Log("Doing other work...")
    yield Delay(0.5)
    
    # 結果を待つ
    result = yield task.join()
    yield Log(f"Background result: {result}")
    
    return result
```

### スナップショットセマンティクス

Spawn されたタスクは、スポーン時点の環境と状態の**コピー**を受け取る。

```python
@do
def spawned_task():
    yield Put("counter", 999)
    return yield Get("counter")  # 999

@do
def parent_task():
    yield Put("counter", 0)
    
    task = yield Spawn(spawned_task())
    
    # 親の状態は変わらない
    parent_counter = yield Get("counter")  # 0
    
    # スポーンされたタスクは独自の状態を持つ
    spawned_result = yield task.join()  # 999
    
    return {"parent": parent_counter, "spawned": spawned_result}
```

### タスクのキャンセル

```python
@do
def cancellable_program():
    task = yield Spawn(long_running_work())
    
    # 少し待つ
    yield Delay(0.5)
    
    # キャンセル
    cancelled = yield task.cancel()
    yield Log(f"Cancelled: {cancelled}")
```

---

## 8.4 時間関連のエフェクト

### Delay - 遅延

```python
@do
def with_delay():
    yield Log("Starting...")
    yield Delay(1.0)  # 1秒待つ
    yield Log("After 1 second")
    yield Delay(0.5)
    yield Log("After another 0.5 seconds")
```

### GetTime - 現在時刻を取得

```python
from doeff import GetTime

@do
def measure_duration():
    start = yield GetTime()
    yield Log(f"Start: {start}")
    
    yield some_operation()
    
    end = yield GetTime()
    duration = (end - start).total_seconds()
    yield Log(f"Duration: {duration}s")
    
    return duration
```

### SimulationRuntime での時間

`SimulationRuntime` を使うと、時間が即座に進む。

```python
from doeff import SimulationRuntime

@do
def slow_program():
    yield Delay(3600)  # 1時間
    return "done"

def test_slow_program():
    runtime = SimulationRuntime()
    result = runtime.run(slow_program())
    # 即座に完了する
    assert result.is_ok()
```

---

## 8.5 タイムアウト

### asyncio.wait_for を使う

```python
@do
def with_timeout():
    try:
        result = yield Await(
            asyncio.wait_for(slow_operation(), timeout=5.0)
        )
        return result
    except asyncio.TimeoutError:
        yield Log("Operation timed out")
        raise
```

### 手動タイムアウト

```python
@do
def manual_timeout():
    task = yield Spawn(slow_operation())
    
    # 5秒待つ
    yield Delay(5.0)
    
    # まだ完了していなければキャンセル
    is_done = yield task.is_done()
    if not is_done:
        yield task.cancel()
        raise TimeoutError("Operation timed out")
    
    return yield task.join()
```

---

## 8.6 レート制限

### シンプルなレート制限

```python
@do
def rate_limited_requests(urls):
    results = []
    
    for i, url in enumerate(urls):
        if i > 0:
            yield Delay(0.5)  # 0.5秒間隔
        
        yield Log(f"Fetching {url}...")
        response = yield Await(fetch_url(url))
        results.append(response)
    
    return results
```

### バッチ処理

```python
@do
def batch_requests(urls, batch_size=5, delay=1.0):
    results = []
    
    for i in range(0, len(urls), batch_size):
        batch = urls[i:i+batch_size]
        
        # バッチを並列実行
        tasks = [fetch_url(url) for url in batch]
        batch_results = yield Gather(*tasks)
        results.extend(batch_results)
        
        yield Log(f"Processed batch {i//batch_size + 1}")
        
        # 次のバッチの前に待つ
        if i + batch_size < len(urls):
            yield Delay(delay)
    
    return results
```

---

## 8.7 エラーハンドリングと非同期

### Safe と Await の組み合わせ

```python
@do
def safe_fetch():
    result = yield Safe(Await(fetch_data()))
    
    if result.is_ok():
        return result.ok()
    else:
        yield Log(f"Fetch failed: {result.err()}")
        return None
```

### Gather でのエラー処理

```python
@do
def parallel_with_errors():
    # 各タスクを Safe でラップ
    results = yield Gather(
        Safe(task1()),
        Safe(task2()),
        Safe(task3())
    )
    
    successes = [r.ok() for r in results if r.is_ok()]
    errors = [r.err() for r in results if r.is_err()]
    
    yield Log(f"Successes: {len(successes)}, Errors: {len(errors)}")
    
    return {"successes": successes, "errors": errors}
```

---

## 8.8 ベストプラクティス

### Await を使う場面

- I/O バウンドな操作（ネットワーク、ディスク、DB）
- 外部ライブラリの非同期関数

### Gather を使う場面

- 独立した複数の操作
- 全ての結果が必要な場合
- 各操作でエフェクトを使いたい場合

### Spawn を使う場面

- Fire-and-forget
- 結果を後で取得したい
- キャンセルが必要
- 状態の分離が必要

---

## まとめ

| エフェクト | 用途 |
|-----------|------|
| `Await(coro)` | 非同期関数を待つ |
| `Gather(*progs)` | 並列実行して結果を収集 |
| `Spawn(prog)` | バックグラウンドタスク |
| `Delay(seconds)` | 遅延 |
| `GetTime()` | 現在時刻 |

次の章では、キャッシュシステムについて見ていく。

---

## 練習問題

1. **並列 API 呼び出し**: 複数の API を並列に呼び出し、最初に成功したものを返す機構を実装せよ

2. **レート制限付きバッチ処理**: 1秒あたり最大10リクエストの制限を守りながらバッチ処理を行う機構を実装せよ

3. **タイムアウト付きリトライ**: 各試行にタイムアウトを設定したリトライ機構を実装せよ

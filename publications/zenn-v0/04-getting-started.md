# 第4章: インストールと最初のプログラム

## この章で学ぶこと

- doeffのインストール方法
- 最初のプログラムの書き方
- 基本的な実行パターン

---

## 4.1 インストール

### pip を使う場合

```bash
pip install doeff
```

### uv を使う場合（推奨）

```bash
uv add doeff
```

### 要件

- Python 3.10以上
- asyncioサポート（Python標準）

---

## 4.2 最初のプログラム

まずは「Hello, doeff!」から始めよう。

```python
import asyncio
from doeff import do, Log
from doeff import AsyncRuntime

@do
def hello():
    yield Log("Hello, doeff!")
    return "done"

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(hello())
    print(f"Result: {result}")

asyncio.run(main())
```

実行結果:
```
Result: done
```

### 解説

1. `@do` デコレータで関数を `Program` に変換
2. `yield Log(...)` でログを出力（エフェクトを宣言）
3. `return` で最終結果を返す
4. `AsyncRuntime` で実行

---

## 4.3 状態を使う

次に、状態管理を追加してみよう。

```python
import asyncio
from doeff import do, Get, Put, Log
from doeff import AsyncRuntime

@do
def counter_program():
    # 初期化
    yield Put("count", 0)
    yield Log("Counter initialized to 0")
    
    # インクリメント
    count = yield Get("count")
    yield Put("count", count + 1)
    yield Log(f"Counter incremented to {count + 1}")
    
    # 最終値を返す
    final = yield Get("count")
    return final

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(counter_program())
    print(f"Final count: {result}")

asyncio.run(main())
```

実行結果:
```
Final count: 1
```

### エフェクトの説明

| エフェクト | 説明 |
|-----------|------|
| `Put(key, value)` | 状態に値を保存 |
| `Get(key)` | 状態から値を取得 |
| `Log(message)` | ログを出力 |

---

## 4.4 環境を使う

設定値を環境から読み取る例。

```python
import asyncio
from doeff import do, Ask, Log
from doeff import AsyncRuntime

@do
def greet():
    name = yield Ask("user_name")
    greeting = yield Ask("greeting", default="Hello")
    
    message = f"{greeting}, {name}!"
    yield Log(message)
    return message

async def main():
    runtime = AsyncRuntime()
    
    # 環境を渡して実行
    result = await runtime.run(
        greet(),
        env={"user_name": "Alice", "greeting": "Hi"}
    )
    print(result)

asyncio.run(main())
```

実行結果:
```
Hi, Alice!
```

### ポイント

- `Ask(key)` で環境から値を取得
- `Ask(key, default=...)` でデフォルト値を指定可能
- 環境は `runtime.run()` の `env` パラメータで渡す

---

## 4.5 プログラムを合成する

複数のプログラムを組み合わせてみよう。

```python
import asyncio
from doeff import do, Get, Put, Log
from doeff import AsyncRuntime

@do
def setup():
    yield Put("initialized", True)
    yield Put("count", 0)
    yield Log("Setup complete")

@do
def increment():
    count = yield Get("count")
    yield Put("count", count + 1)
    yield Log(f"Incremented to {count + 1}")
    return count + 1

@do
def main_program():
    # セットアップを実行
    yield setup()
    
    # 3回インクリメント
    for _ in range(3):
        result = yield increment()
    
    yield Log(f"Final result: {result}")
    return result

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(main_program())
    print(f"Result: {result}")

asyncio.run(main())
```

実行結果:
```
Result: 3
```

### 合成のポイント

- `yield other_program()` で他のプログラムを呼び出せる
- 普通の関数呼び出しのように見えるが、エフェクトが適切に処理される
- ループや条件分岐も通常のPythonと同じ

---

## 4.6 エラーハンドリング

エラーを安全に処理する方法。

```python
import asyncio
from doeff import do, Safe, Log
from doeff import AsyncRuntime

@do
def risky_operation():
    yield Log("About to fail...")
    raise ValueError("Something went wrong!")

@do
def safe_program():
    yield Log("Starting safe program")
    
    # Safe でエラーをキャッチ
    result = yield Safe(risky_operation())
    
    if result.is_ok():
        yield Log(f"Success: {result.ok()}")
        return result.ok()
    else:
        yield Log(f"Error caught: {result.err()}")
        return "fallback value"

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(safe_program())
    print(f"Result: {result}")

asyncio.run(main())
```

実行結果:
```
Result: fallback value
```

### Safe の使い方

- `Safe(program)` でプログラムをラップ
- 結果は `Result` 型（`Ok` または `Err`）
- `is_ok()` / `is_err()` で判定
- `ok()` / `err()` で値を取得

---

## 4.7 非同期処理

外部APIを呼び出す例。

```python
import asyncio
from doeff import do, Await, Log
from doeff import AsyncRuntime

async def fetch_data():
    """実際のAPIコールをシミュレート"""
    await asyncio.sleep(0.1)
    return {"user_id": 123, "name": "Bob"}

@do
def async_program():
    yield Log("Fetching data...")
    
    # 非同期関数を呼び出す
    data = yield Await(fetch_data())
    
    yield Log(f"Got data: {data}")
    return data["name"]

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(async_program())
    print(f"User name: {result}")

asyncio.run(main())
```

実行結果:
```
User name: Bob
```

### Await の使い方

- `Await(coroutine)` で非同期関数の結果を待つ
- 通常の `async/await` と同様に動作
- 他のエフェクト（Log, Get, Put）と組み合わせ可能

---

## 4.8 テストを書く

doeffの大きな利点：テストが簡単。

```python
import pytest
from doeff import do, Get, Put, Log
from doeff import AsyncRuntime

@do
def process_order(order_id):
    order = yield Get("orders", order_id)
    if order is None:
        return None
    
    total = sum(order["items"])
    yield Put("totals", order_id, total)
    yield Log(f"Processed order {order_id}: {total}")
    return total

@pytest.mark.asyncio
async def test_process_order():
    runtime = AsyncRuntime()
    
    # テストデータを渡すだけ
    result = await runtime.run(
        process_order("ORD-001"),
        store={
            "orders": {"ORD-001": {"items": [100, 200, 300]}},
            "totals": {}
        }
    )
    
    assert result == 600

@pytest.mark.asyncio
async def test_process_order_not_found():
    runtime = AsyncRuntime()
    
    result = await runtime.run(
        process_order("ORD-999"),
        store={
            "orders": {},
            "totals": {}
        }
    )
    
    assert result is None
```

### テストのポイント

- モックは不要
- `store` にテストデータを渡すだけ
- ビジネスロジックのみをテスト

---

## 4.9 よくある間違い

### 間違い1: @do を忘れる

```python
# 間違い
def my_program():
    yield Log("test")

# 正しい
@do
def my_program():
    yield Log("test")
```

### 間違い2: yield を忘れる

```python
# 間違い
@do
def my_program():
    Log("test")  # yield がない！
    return 42

# 正しい
@do
def my_program():
    yield Log("test")
    return 42
```

### 間違い3: await を直接使う

```python
# 間違い
@do
def my_program():
    data = await fetch_data()  # これは動かない
    return data

# 正しい
@do
def my_program():
    data = yield Await(fetch_data())
    return data
```

---

## まとめ

- `pip install doeff` または `uv add doeff` でインストール
- `@do` デコレータでプログラムを定義
- `yield` でエフェクトを宣言
- `AsyncRuntime` で実行
- テストは `store` にデータを渡すだけ

次の章では、`Program` と `Effect` の概念をより詳しく見ていく。

---

## クイックリファレンス

```python
from doeff import (
    do,                # デコレータ
    Program,           # プログラム型
    
    # 状態
    Get, Put, Modify,
    
    # 環境
    Ask, Local,
    
    # ログ
    Log, Tell, Listen,
    
    # 非同期
    Await, Gather, Spawn, Delay,
    
    # エラー
    Safe,
    
    # IO
    IO,
)

from doeff import AsyncRuntime
```

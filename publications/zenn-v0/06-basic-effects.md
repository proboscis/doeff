# 第6章: 基本エフェクト (Reader, State, Writer)

## この章で学ぶこと

- Reader エフェクト（環境の読み取り）
- State エフェクト（状態の管理）
- Writer エフェクト（ログの蓄積）
- これらを組み合わせたパターン

---

## 6.1 Reader エフェクト

Reader エフェクトは、読み取り専用の環境（設定）にアクセスするために使う。

### Ask - 環境から値を取得

```python
from doeff import do, Ask, Log
from doeff import AsyncRuntime

@do
def connect_to_database():
    # 環境からデータベースURLを取得
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    
    yield Log(f"Connecting to {db_url} with timeout {timeout}s")
    return f"Connected to {db_url}"

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(
        connect_to_database(),
        env={
            "database_url": "postgresql://localhost/mydb",
            "timeout": 30
        }
    )
    print(result)

# Output: Connected to postgresql://localhost/mydb
```

### デフォルト値

キーが存在しない場合のデフォルト値を指定できる。

```python
@do
def with_defaults():
    # "debug" がなければ False を返す
    debug = yield Ask("debug", default=False)
    # "max_retries" がなければ 3 を返す
    retries = yield Ask("max_retries", default=3)
    
    yield Log(f"Debug: {debug}, Retries: {retries}")
    return {"debug": debug, "retries": retries}
```

### Local - 環境を一時的に変更

サブプログラムの実行時だけ環境を変更する。

```python
@do
def fetch_data():
    url = yield Ask("api_url")
    yield Log(f"Fetching from {url}")
    return f"data from {url}"

@do
def main_program():
    # 通常の環境
    url1 = yield Ask("api_url")
    yield Log(f"Default URL: {url1}")
    
    # サブプログラムだけ異なる環境で実行
    result = yield Local(
        {"api_url": "https://staging.example.com"},
        fetch_data()
    )
    
    # 元の環境に戻る
    url2 = yield Ask("api_url")
    yield Log(f"Back to: {url2}")
    
    return result
```

### Reader の使いどころ

| 用途 | 例 |
|------|-----|
| 設定値 | データベースURL、APIキー |
| 機能フラグ | デバッグモード、実験的機能 |
| 依存関係 | ロガー設定、タイムゾーン |

---

## 6.2 State エフェクト

State エフェクトは、変更可能な状態を管理するために使う。

### Get - 状態を取得

```python
@do
def read_counter():
    count = yield Get("counter")
    yield Log(f"Current count: {count}")
    return count
```

### Put - 状態を設定

```python
@do
def initialize_state():
    yield Put("counter", 0)
    yield Put("status", "ready")
    yield Put("items", [])
    yield Log("State initialized")
```

### Modify - 状態を変換

`Get` + 変換 + `Put` を一度に行う。

```python
@do
def increment():
    # Get, 変換, Put を一度に
    new_value = yield Modify("counter", lambda x: x + 1)
    yield Log(f"Counter is now {new_value}")
    return new_value

# 以下と同等:
@do
def increment_manual():
    current = yield Get("counter")
    new_value = current + 1
    yield Put("counter", new_value)
    yield Log(f"Counter is now {new_value}")
    return new_value
```

### 実践例: ショッピングカート

```python
@do
def add_to_cart(item):
    yield Modify("cart", lambda items: items + [item])
    yield Log(f"Added {item} to cart")

@do
def remove_from_cart(item):
    yield Modify("cart", lambda items: [i for i in items if i != item])
    yield Log(f"Removed {item} from cart")

@do
def get_cart_total():
    cart = yield Get("cart")
    total = sum(item["price"] for item in cart)
    yield Log(f"Cart total: {total}")
    return total

@do
def shopping_session():
    yield Put("cart", [])
    
    yield add_to_cart({"name": "Apple", "price": 100})
    yield add_to_cart({"name": "Banana", "price": 80})
    yield add_to_cart({"name": "Orange", "price": 120})
    
    total = yield get_cart_total()
    return total

# 実行
async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(shopping_session())
    print(f"Total: {result}")  # Total: 300
```

### State の使いどころ

| 用途 | 例 |
|------|-----|
| カウンター | リクエスト数、処理済み件数 |
| 一時データ | キャッシュ、中間結果 |
| 状態機械 | ワークフローの状態 |

---

## 6.3 Writer エフェクト

Writer エフェクトは、ログやイベントを蓄積するために使う。

### Log - ログを出力

```python
@do
def with_logging():
    yield Log("Processing started")
    yield Log("Step 1 complete")
    yield Log("Step 2 complete")
    yield Log("Processing finished")
    return "done"
```

### Tell - 値を蓄積

`Log` と同様だが、より汎用的な値を蓄積できる。

```python
@do
def collect_events():
    yield Tell([{"event": "start", "time": "10:00"}])
    yield Tell([{"event": "process", "time": "10:01"}])
    yield Tell([{"event": "end", "time": "10:02"}])
    return "done"
```

### Listen - サブプログラムのログを取得

サブプログラムが出力したログを取得する。

```python
@do
def inner_operation():
    yield Log("Inner step 1")
    yield Log("Inner step 2")
    return 42

@do
def outer_operation():
    yield Log("Before inner")
    
    # Listen でサブプログラムのログを取得
    listen_result = yield Listen(inner_operation())
    
    yield Log("After inner")
    yield Log(f"Inner returned: {listen_result.value}")
    yield Log(f"Inner logs: {listen_result.log}")
    
    return listen_result.value

# listen_result.value = 42
# listen_result.log = ["Inner step 1", "Inner step 2"]
```

### 実践例: 監査ログ

```python
@do
def process_transaction(transaction_id, amount):
    yield Log(f"[AUDIT] Transaction {transaction_id} started")
    yield Log(f"[AUDIT] Amount: {amount}")
    
    # 処理
    balance = yield Get("balance")
    if balance < amount:
        yield Log(f"[AUDIT] Transaction {transaction_id} REJECTED: insufficient funds")
        return {"success": False, "reason": "insufficient funds"}
    
    yield Modify("balance", lambda b: b - amount)
    yield Log(f"[AUDIT] Transaction {transaction_id} COMPLETED")
    
    return {"success": True, "new_balance": balance - amount}

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(
        process_transaction("TXN-001", 500),
        store={"balance": 1000}
    )
    print(result)
```

---

## 6.4 エフェクトを組み合わせる

3つのエフェクトを組み合わせた実践的な例。

### 設定 + 状態 + ログ

```python
@do
def application_workflow():
    # Reader: 設定を読む
    max_retries = yield Ask("max_retries")
    yield Log(f"Config: max_retries = {max_retries}")
    
    # State: 状態を初期化
    yield Put("attempt", 0)
    yield Put("status", "pending")
    
    # 処理ループ
    for i in range(max_retries):
        yield Modify("attempt", lambda x: x + 1)
        attempt = yield Get("attempt")
        yield Log(f"Attempt {attempt}/{max_retries}")
        
        success = yield try_operation()
        
        if success:
            yield Put("status", "success")
            yield Log("Operation succeeded!")
            return "success"
        else:
            yield Log(f"Attempt {attempt} failed")
    
    yield Put("status", "failed")
    yield Log("All attempts failed")
    return "failed"

@do
def try_operation():
    # 50%の確率で成功
    import random
    return random.random() > 0.5
```

### テスト環境での実行

```python
async def test_application_workflow():
    runtime = AsyncRuntime()
    
    # 環境と状態を渡してテスト
    result = await runtime.run(
        application_workflow(),
        env={"max_retries": 3}
    )
    
    # 結果は "success" または "failed"
    assert result in ["success", "failed"]
```

---

## 6.5 ベストプラクティス

### Reader

**DO:**
- 実行中に変わらない設定に使う
- キー名を定数として定義する
- デフォルト値を適切に設定する

```python
# キー名を定数化
CONFIG_KEYS = {
    "DB_URL": "database_url",
    "TIMEOUT": "timeout",
}

@do
def good_reader():
    db_url = yield Ask(CONFIG_KEYS["DB_URL"])
    timeout = yield Ask(CONFIG_KEYS["TIMEOUT"], default=30)
    return {"db_url": db_url, "timeout": timeout}
```

**DON'T:**
- 頻繁に変わる値に使わない（State を使う）
- 変更可能なオブジェクトを環境に入れない

### State

**DO:**
- 明示的に初期化する
- キー名は説明的に
- `Modify` でアトミックな更新を行う

```python
@do
def good_state():
    # 明示的な初期化
    yield Put("processed_count", 0)
    yield Put("errors", [])
    
    # Modify でアトミック更新
    yield Modify("processed_count", lambda x: x + 1)
```

**DON'T:**
- 初期化せずに Get しない
- 設定に State を使わない（Reader を使う）

### Writer

**DO:**
- 一貫したログフォーマット
- 重要なイベントをログ
- 構造化ログを検討

```python
@do
def good_writer():
    yield Log("[INFO] Operation started")
    yield Log("[DEBUG] Processing item 1")
    yield Log("[INFO] Operation completed")
```

**DON'T:**
- タイトなループで過剰にログを出さない
- 機密情報をログに含めない

---

## まとめ

| エフェクト | 用途 | 主なAPI |
|-----------|------|---------|
| Reader | 環境（設定）の読み取り | `Ask`, `Local` |
| State | 状態の管理 | `Get`, `Put`, `Modify` |
| Writer | ログの蓄積 | `Log`, `Tell`, `Listen` |

これら3つを組み合わせることで、多くのアプリケーションパターンを表現できる。

次の章では、エラーハンドリングについて詳しく見ていく。

---

## 練習問題

1. **カウンターアプリ**: `increment`, `decrement`, `reset` 機能を持つカウンターを実装せよ

2. **設定マネージャー**: 複数の設定ソース（デフォルト、ファイル、環境変数）をマージする機能を実装せよ

3. **イベントコレクター**: サブプログラムが発行したイベントを集約し、最後にまとめて処理する機能を実装せよ

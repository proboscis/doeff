# 第10章: 実用パターン集

## この章で学ぶこと

- アーキテクチャパターン
- エラーハンドリングパターン
- パフォーマンスパターン
- アンチパターン

---

## 10.1 レイヤードアーキテクチャ

関心を分離する。

```python
# ドメイン層: 純粋なビジネスロジック
@do
def calculate_order_total(items):
    subtotal = sum(item["price"] * item["quantity"] for item in items)
    tax = subtotal * 0.1
    return subtotal + tax

# サービス層: オーケストレーション
@do
def order_service(order_id):
    db_url = yield Ask("database_url")
    
    order = yield fetch_order(order_id)
    total = yield calculate_order_total(order["items"])
    
    order["total"] = total
    yield save_order(order)
    
    return order

# API層: 外部インターフェース
@do
def get_order_handler(order_id):
    yield Log(f"Request: GET /orders/{order_id}")
    
    result = yield Safe(order_service(order_id))
    
    if result.is_ok():
        return {"status": "ok", "data": result.ok()}
    else:
        return {"status": "error", "message": str(result.err())}
```

---

## 10.2 リポジトリパターン

データアクセスを抽象化する。

```python
@do
def user_repository():
    """ユーザーリポジトリを作成"""
    
    @do
    def find_by_id(user_id):
        yield Log(f"Finding user {user_id}")
        data = yield Get("users")
        return data.get(user_id)
    
    @do
    def find_all():
        data = yield Get("users")
        return list(data.values())
    
    @do
    def save(user):
        yield Modify("users", lambda users: {**users, user["id"]: user})
        yield Log(f"Saved user {user['id']}")
        return user
    
    @do
    def delete(user_id):
        yield Modify("users", lambda users: {k: v for k, v in users.items() if k != user_id})
        yield Log(f"Deleted user {user_id}")
    
    return {
        "find_by_id": find_by_id,
        "find_all": find_all,
        "save": save,
        "delete": delete
    }

# 使用例
@do
def user_workflow():
    repo = yield user_repository()
    
    user = yield repo["find_by_id"](123)
    user["name"] = "Updated Name"
    yield repo["save"](user)
```

---

## 10.3 Unit of Work パターン

トランザクション境界を管理する。

```python
@do
def unit_of_work(operations):
    """操作をトランザクションとして実行"""
    
    # 状態のスナップショットを取る
    original_state = yield Get("_all_state")
    
    try:
        result = yield operations()
        yield Log("Transaction committed")
        return result
    except Exception as e:
        # エラー時はロールバック
        yield Put("_all_state", original_state)
        yield Log(f"Transaction rolled back: {e}")
        raise

# 使用例
@do
def transfer_money(from_account, to_account, amount):
    @do
    def operations():
        yield debit_account(from_account, amount)
        yield credit_account(to_account, amount)
        return {"status": "transferred", "amount": amount}
    
    return yield unit_of_work(operations)
```

---

## 10.4 サーキットブレーカーパターン

障害の連鎖を防ぐ。

```python
@do
def circuit_breaker(operation, service_name, threshold=5, timeout=60):
    """サーキットブレーカーで操作をラップ"""
    
    failures = yield Get(f"{service_name}_failures")
    last_failure = yield Get(f"{service_name}_last_failure")
    
    now = yield IO(lambda: time.time())
    
    # サーキットが開いているか確認
    if failures >= threshold:
        if last_failure and (now - last_failure) < timeout:
            yield Log(f"Circuit OPEN for {service_name}")
            raise Exception(f"Circuit breaker open for {service_name}")
        else:
            # タイムアウト後はリセット
            yield Put(f"{service_name}_failures", 0)
    
    # 操作を試行
    result = yield Safe(operation())
    
    if result.is_ok():
        yield Put(f"{service_name}_failures", 0)
        return result.ok()
    else:
        yield Modify(f"{service_name}_failures", lambda x: x + 1)
        yield Put(f"{service_name}_last_failure", now)
        raise result.err()
```

---

## 10.5 リトライパターン（指数バックオフ）

```python
@do
def retry_with_backoff(operation, max_attempts=5, base_delay=0.1):
    """指数バックオフでリトライ"""
    
    for attempt in range(1, max_attempts + 1):
        result = yield Safe(operation())
        
        if result.is_ok():
            yield Log(f"Success on attempt {attempt}")
            return result.ok()
        
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            # ジッターを追加
            jitter = delay * 0.1 * (yield IO(lambda: random.random()))
            actual_delay = delay + jitter
            
            yield Log(f"Attempt {attempt} failed, retrying in {actual_delay:.2f}s")
            yield Delay(actual_delay)
    
    raise Exception(f"Failed after {max_attempts} attempts")
```

---

## 10.6 バッチ処理パターン

```python
@do
def batch_processor(items, batch_size=100, delay_between=1.0):
    """バッチ単位で処理"""
    
    results = []
    total_batches = (len(items) + batch_size - 1) // batch_size
    
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        yield Log(f"Processing batch {batch_num}/{total_batches}")
        
        # バッチを並列処理
        batch_results = yield Gather(*[process_item(item) for item in batch])
        results.extend(batch_results)
        
        # 次のバッチの前に待つ（レート制限）
        if i + batch_size < len(items):
            yield Delay(delay_between)
    
    yield Log(f"Processed {len(results)} items in {total_batches} batches")
    return results
```

---

## 10.7 並列データフェッチパターン

```python
@do
def fetch_dashboard(user_id):
    """ダッシュボードデータを並列取得"""
    
    # 並列に取得
    results = yield Gather(
        fetch_user(user_id),
        fetch_posts(user_id),
        fetch_notifications(user_id),
        fetch_stats(user_id)
    )
    
    return {
        "user": results[0],
        "posts": results[1],
        "notifications": results[2],
        "stats": results[3]
    }
```

---

## 10.8 状態マシンパターン

```python
@do
def order_state_machine(order_id, action):
    """注文の状態遷移を管理"""
    
    state = yield Get(f"order_{order_id}_state")
    
    # 状態遷移テーブル
    transitions = {
        ("pending", "pay"): "paid",
        ("paid", "ship"): "shipped",
        ("shipped", "deliver"): "delivered",
        ("pending", "cancel"): "cancelled",
        ("paid", "cancel"): "cancelled",
    }
    
    key = (state, action)
    if key not in transitions:
        raise ValueError(f"Invalid transition: {state} + {action}")
    
    new_state = transitions[key]
    yield Put(f"order_{order_id}_state", new_state)
    yield Log(f"Order {order_id}: {state} -> {new_state}")
    
    return new_state
```

---

## 10.9 アンチパターン

### NG: ブロッキング操作

```python
# BAD
@do
def bad_blocking():
    import time
    time.sleep(5)  # ランタイム全体をブロック
    return "done"

# GOOD
@do
def good_async():
    yield Delay(5)  # 非同期で待機
    return "done"
```

### NG: IO ラップなしの副作用

```python
# BAD
@do
def bad_side_effect():
    with open("file.txt", "w") as f:
        f.write("data")  # 追跡されない副作用

# GOOD
@do
def good_side_effect():
    def write_file():
        with open("file.txt", "w") as f:
            f.write("data")
    yield IO(write_file)
```

### NG: エラーの無視

```python
# BAD
@do
def bad_error_handling():
    result = yield Safe(risky_operation())
    return result.ok()  # エラー時は None、静かに失敗

# GOOD
@do
def good_error_handling():
    result = yield Safe(risky_operation())
    if result.is_err():
        yield Log(f"Error: {result.err()}")
        raise result.err()  # または適切なフォールバック
    return result.ok()
```

### NG: 過度な状態の使用

```python
# BAD
@do
def bad_state_usage(a, b, c):
    yield Put("arg_a", a)
    yield Put("arg_b", b)
    yield Put("arg_c", c)
    return yield compute_from_state()

# GOOD
@do
def good_parameter_passing(a, b, c):
    return yield compute(a, b, c)  # 直接渡す
```

---

## まとめ

| パターン | 用途 |
|---------|------|
| レイヤードアーキテクチャ | 関心の分離 |
| リポジトリ | データアクセスの抽象化 |
| Unit of Work | トランザクション管理 |
| サーキットブレーカー | 障害の連鎖防止 |
| リトライ + バックオフ | 一時的障害への対応 |
| バッチ処理 | 大量データの効率的処理 |
| 並列フェッチ | パフォーマンス向上 |

次の章では、Kleisli Arrow と関数合成を見ていく。

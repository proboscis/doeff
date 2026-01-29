# 第7章: エラーハンドリング

## この章で学ぶこと

- doeff でのエラーハンドリングの考え方
- `Safe` エフェクトの使い方
- `RuntimeResult` の活用
- 一般的なエラーパターン

---

## 7.1 doeff のエラーハンドリング哲学

doeff では、エラーを2つの方法で扱える。

1. **Python ネイティブ**: `raise` でエラーを投げる
2. **エフェクトベース**: `Safe` でエラーを `Result` 型にラップ

### いつどちらを使うか

| 状況 | 方法 |
|------|------|
| 回復不能なエラー | `raise` |
| バリデーションエラー | `raise` |
| 回復可能なエラー | `Safe` |
| 複数の操作を試す | `Safe` |

---

## 7.2 raise によるエラー

最もシンプルな方法。

```python
@do
def validate_input(value):
    if value < 0:
        raise ValueError("Value must be non-negative")
    
    if value > 100:
        raise ValueError("Value must be <= 100")
    
    yield Log(f"Valid value: {value}")
    return value
```

### カスタム例外

```python
class ValidationError(Exception):
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")

@do
def validate_user(data):
    if "email" not in data:
        raise ValidationError("email", "Email is required")
    
    if "@" not in data["email"]:
        raise ValidationError("email", "Invalid email format")
    
    yield Log(f"User validated: {data['email']}")
    return data
```

---

## 7.3 Safe エフェクト

`Safe` はエラーを `Result` 型でキャッチする。

### 基本的な使い方

```python
from doeff import do, Safe, Log

@do
def risky_operation():
    raise ValueError("Something went wrong!")

@do
def safe_program():
    # Safe でエラーをキャッチ
    result = yield Safe(risky_operation())
    
    if result.is_ok():
        yield Log(f"Success: {result.ok()}")
        return result.ok()
    else:
        yield Log(f"Error: {result.err()}")
        return "fallback"
```

### Result 型

`Safe` は `Result` 型を返す。

```python
from doeff._vendor import Ok, Err

# 成功の場合
result = Ok(42)
result.is_ok()   # True
result.ok()      # 42

# 失敗の場合
result = Err(ValueError("error"))
result.is_err()  # True
result.err()     # ValueError("error")
```

### パターンマッチング

Python 3.10+ ではパターンマッチングが使える。

```python
@do
def with_pattern_matching():
    result = yield Safe(risky_operation())
    
    match result:
        case Ok(value):
            yield Log(f"Success: {value}")
            return value
        case Err(error):
            yield Log(f"Error: {error}")
            return "fallback"
```

---

## 7.4 フォールバックパターン

### 単純なフォールバック

```python
@do
def fetch_with_fallback():
    result = yield Safe(fetch_from_primary())
    
    if result.is_ok():
        return result.ok()
    
    yield Log(f"Primary failed: {result.err()}")
    
    # バックアップを試す
    backup_result = yield Safe(fetch_from_backup())
    
    if backup_result.is_ok():
        return backup_result.ok()
    
    # 両方失敗したらデフォルト
    yield Log("All sources failed, using default")
    return get_default_data()
```

### 複数ソースを順番に試す

```python
@do
def first_success(sources):
    """最初に成功したソースの結果を返す"""
    errors = []
    
    for source in sources:
        result = yield Safe(source())
        
        if result.is_ok():
            return result.ok()
        
        errors.append(result.err())
    
    # 全て失敗
    raise Exception(f"All sources failed: {errors}")

@do
def fetch_data():
    return yield first_success([
        fetch_from_cache,
        fetch_from_db,
        fetch_from_api,
    ])
```

---

## 7.5 リトライパターン

### シンプルなリトライ

```python
@do
def retry_operation(max_attempts=3):
    for attempt in range(max_attempts):
        result = yield Safe(unstable_operation())
        
        if result.is_ok():
            return result.ok()
        
        yield Log(f"Attempt {attempt + 1} failed: {result.err()}")
    
    raise Exception(f"Failed after {max_attempts} attempts")
```

### 指数バックオフ付きリトライ

```python
@do
def retry_with_backoff(max_attempts=3, base_delay=0.1):
    last_error = None
    
    for attempt in range(max_attempts):
        result = yield Safe(operation())
        
        if result.is_ok():
            return result.ok()
        
        last_error = result.err()
        
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt)
            yield Log(f"Attempt {attempt + 1} failed, retrying in {delay}s...")
            yield Delay(delay)
    
    raise Exception(f"Failed after {max_attempts} attempts: {last_error}")
```

---

## 7.6 エラー集約パターン

### バッチ処理でのエラー収集

```python
@do
def process_batch(items):
    successes = []
    errors = []
    
    for item in items:
        result = yield Safe(process_item(item))
        
        if result.is_ok():
            successes.append(result.ok())
        else:
            errors.append({
                "item": item,
                "error": str(result.err())
            })
    
    yield Log(f"Processed {len(successes)}/{len(items)} items")
    
    if errors:
        yield Log(f"Errors: {errors}")
    
    return {
        "successes": successes,
        "errors": errors
    }
```

### 並列処理でのエラー収集

```python
@do
def parallel_with_errors(items):
    # 全てを Safe でラップ
    safe_tasks = [Safe(process_item(item)) for item in items]
    
    # 並列実行
    results = yield Gather(*safe_tasks)
    
    # 成功と失敗を分類
    successes = [r.ok() for r in results if r.is_ok()]
    errors = [r.err() for r in results if r.is_err()]
    
    yield Log(f"Successes: {len(successes)}, Errors: {len(errors)}")
    
    return {"successes": successes, "errors": errors}
```

---

## 7.7 RuntimeResult

`runtime.run()` は `RuntimeResult` を返す。

### 基本的な使い方

```python
async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(my_program())
    
    # 成功/失敗のチェック
    if result.is_ok():
        print(f"Value: {result.value}")
    else:
        print(f"Error: {result.error}")
```

### RuntimeResult の属性

```python
result.result      # Result[T]: Ok(value) or Err(error)
result.value       # T: 値を取得（エラー時は例外）
result.error       # Exception: エラーを取得（成功時は例外）

result.is_ok()     # bool: 成功かどうか
result.is_err()    # bool: エラーかどうか

result.state       # dict: 最終状態
result.log         # list: 蓄積されたログ
result.env         # dict: 環境
```

### デバッグ情報

```python
if result.is_err():
    # フォーマットされた出力
    print(result.format(verbose=True))
    
    # 個別のスタック
    print(result.k_stack.format())
    print(result.effect_stack.format())
    print(result.python_stack.format())
```

---

## 7.8 重要: Safe は状態をロールバックしない

`Safe` でエラーをキャッチしても、それまでの状態変更は残る。

```python
@do
def demo_no_rollback():
    yield Put("counter", 0)
    
    result = yield Safe(failing_with_side_effects())
    
    # エラーが起きても counter は 10 のまま
    counter = yield Get("counter")
    yield Log(f"Counter: {counter}")  # 10
    
    return result

@do
def failing_with_side_effects():
    yield Modify("counter", lambda x: x + 10)  # これは残る
    raise ValueError("Oops!")
```

トランザクション的な動作が必要な場合は、手動でロールバックするか、別のパターンを使う必要がある。

---

## 7.9 ベストプラクティス

### raise を使う場面

```python
# バリデーションエラー
if not data:
    raise ValueError("Data cannot be empty")

# 前提条件の違反
if user is None:
    raise RuntimeError("User must be logged in")

# 回復不能なエラー
if critical_failure:
    raise SystemError("Critical failure occurred")
```

### Safe を使う場面

```python
# 外部サービスの呼び出し
result = yield Safe(call_external_api())

# 複数の選択肢がある場合
result = yield Safe(try_primary())
if result.is_err():
    result = yield Safe(try_secondary())

# バッチ処理で一部の失敗を許容
for item in items:
    result = yield Safe(process(item))
```

### エラーコンテキストを含める

```python
@do
def with_context():
    user_id = yield Get("user_id")
    
    result = yield Safe(fetch_user(user_id))
    
    if result.is_err():
        # エラーにコンテキストを追加
        yield Log(f"Failed to fetch user {user_id}: {result.err()}")
        raise RuntimeError(f"User fetch failed for {user_id}") from result.err()
    
    return result.ok()
```

---

## まとめ

| 方法 | 用途 |
|------|------|
| `raise` | 回復不能なエラー、バリデーション |
| `Safe(prog)` | 回復可能なエラー、フォールバック |
| `RuntimeResult` | 実行結果の取得とデバッグ |

**重要なポイント:**
- `Safe` は状態をロールバックしない
- `is_ok()` と `is_err()` は**メソッド**（括弧が必要）
- エラーにはコンテキストを含める

次の章では、非同期処理について詳しく見ていく。

---

## 練習問題

1. **リトライ機構**: ジッターを追加した指数バックオフを実装せよ

2. **サーキットブレーカー**: N回連続失敗したらしばらく呼び出しをスキップする機構を実装せよ

3. **エラー集約**: 複数のバリデーションエラーを集約して返す機構を実装せよ

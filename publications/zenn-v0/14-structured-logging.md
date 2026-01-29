# 第14章: 構造化ログと実行トレース

## この章で学ぶこと

- 構造化ログの活用
- 実行トレースの取得
- デバッグとモニタリング

---

## 14.1 なぜ構造化ログか

従来のログ:
```
[2024-01-15 10:30:45] INFO: Processing order ORD-001
[2024-01-15 10:30:46] INFO: Order total: 1500
[2024-01-15 10:30:46] ERROR: Payment failed
```

構造化ログ:
```json
{"timestamp": "2024-01-15T10:30:45Z", "level": "info", "event": "order_processing_started", "order_id": "ORD-001"}
{"timestamp": "2024-01-15T10:30:46Z", "level": "info", "event": "order_total_calculated", "order_id": "ORD-001", "total": 1500}
{"timestamp": "2024-01-15T10:30:46Z", "level": "error", "event": "payment_failed", "order_id": "ORD-001", "reason": "insufficient_funds"}
```

構造化ログは:
- 機械で解析しやすい
- フィルタリング可能
- 集計・可視化が容易

---

## 14.2 StructuredLog エフェクト

```python
from doeff import do, StructuredLog

@do
def process_order(order_id):
    yield StructuredLog(
        level="info",
        event="order_processing_started",
        order_id=order_id
    )
    
    order = yield Get("orders", order_id)
    total = calculate_total(order)
    
    yield StructuredLog(
        level="info",
        event="order_total_calculated",
        order_id=order_id,
        total=total
    )
    
    return total
```

---

## 14.3 Listen でログを取得

```python
@do
def outer_operation():
    yield Log("Outer start")
    
    # Listen でサブプログラムのログを取得
    listen_result = yield Listen(inner_operation())
    
    yield Log("Outer end")
    
    # listen_result.value: サブプログラムの戻り値
    # listen_result.log: サブプログラムのログ
    
    yield StructuredLog(
        event="inner_completed",
        inner_logs=listen_result.log,
        inner_result=listen_result.value
    )
    
    return listen_result.value

@do
def inner_operation():
    yield Log("Inner step 1")
    yield Log("Inner step 2")
    return 42
```

---

## 14.4 RuntimeResult からのログ取得

```python
async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(process_order("ORD-001"))
    
    # 蓄積されたログを取得
    for log_entry in result.log:
        print(log_entry)
```

---

## 14.5 実行トレース

### スタックトレース

```python
if result.is_err():
    # Python スタック
    print(result.python_stack.format())
    
    # エフェクトコールツリー
    print(result.effect_stack.format())
    
    # 継続スタック
    print(result.k_stack.format())
```

### エフェクトパス

```python
if result.is_err():
    path = result.effect_stack.get_effect_path()
    # "main() -> process_order() -> Get('orders')"
```

---

## 14.6 モニタリングパターン

### 処理時間の計測

```python
@do
def timed_operation(name, operation):
    start = yield GetTime()
    
    result = yield operation
    
    end = yield GetTime()
    duration = (end - start).total_seconds()
    
    yield StructuredLog(
        event="operation_completed",
        operation=name,
        duration_seconds=duration
    )
    
    return result
```

### メトリクス収集

```python
@do
def with_metrics(operation):
    yield StructuredLog(event="operation_started")
    
    result = yield Safe(operation)
    
    if result.is_ok():
        yield StructuredLog(
            event="operation_succeeded",
            result_type=type(result.ok()).__name__
        )
    else:
        yield StructuredLog(
            event="operation_failed",
            error_type=type(result.err()).__name__,
            error_message=str(result.err())
        )
    
    return result
```

---

## 14.7 可視化への活用

構造化ログは可視化ツールと連携できる。

```python
# ログをJSON Lines形式で出力
@do
def export_logs(result):
    import json
    
    for entry in result.log:
        if isinstance(entry, dict):
            print(json.dumps(entry))
```

これを Elasticsearch, Grafana Loki, CloudWatch Logs などに送信して可視化できる。

---

## まとめ

| 機能 | 用途 |
|------|------|
| `Log(message)` | シンプルなログ |
| `StructuredLog(**kwargs)` | 構造化ログ |
| `Listen(prog)` | サブプログラムのログ取得 |
| `result.log` | 蓄積されたログの取得 |
| `result.format()` | デバッグ出力 |

次の章では、card_game_2026 での実例を見ていく。

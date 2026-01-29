# 第19章: 構造化ログとトレース（産業利用編）

## この章で学ぶこと

- 産業利用での構造化ログ
- `slog` による構造化ログ
- `listen` によるログキャプチャ
- 後処理でのログ活用

---

## 19.1 産業利用での要件

本番環境では以下が求められる:

- 機械可読なログ形式
- コンテキスト情報の付加
- ログの集約と分析
- デバッグ時のトレース再現

---

## 19.2 slog: 構造化ログ

`slog` は構造化されたログエントリを出力する。

```python
from doeff import slog

@do
def process_order(order_id: str):
    yield slog(
        msg="Order processing started",
        order_id=order_id,
        level="info"
    )
    
    order = yield Get("orders", order_id)
    
    yield slog(
        msg="Order loaded",
        order_id=order_id,
        item_count=len(order["items"]),
        level="debug"
    )
    
    total = calculate_total(order)
    
    yield slog(
        msg="Order processed",
        order_id=order_id,
        total=total,
        level="info"
    )
    
    return total
```

---

## 19.3 listen: ログキャプチャ

`listen` でサブプログラムのログを取得する。

```python
from doeff import listen

@do
def process_with_logging():
    # サブプログラムのログをキャプチャ
    listen_result = yield listen(process_order("ORD-001"))
    
    # 結果とログを取得
    value = listen_result.value
    logs = listen_result.log
    
    yield slog(
        msg="Process completed",
        result=value,
        log_count=len(logs)
    )
    
    return {"value": value, "logs": logs}
```

---

## 19.4 ログのエクスポート

```python
import json

@do
def process_and_export_logs(order_id: str, log_path: Path):
    listen_result = yield listen(process_order(order_id))
    
    # ログを JSON Lines で出力
    with open(log_path, "w") as f:
        for entry in listen_result.log:
            if isinstance(entry, dict):
                f.write(json.dumps(entry) + "\n")
    
    yield slog(
        msg="Logs exported",
        log_path=str(log_path),
        entry_count=len(listen_result.log)
    )
    
    return listen_result.value
```

---

## 19.5 Transform マーカー

`# doeff: transform` マーカーで後処理関数を定義する。

```python
@do
def add_logging(  # doeff: transform
    program: Program[T],
) -> EffectGenerator[T]:
    """全ての実行にログを追加"""
    yield slog(msg="Program execution started")
    result = yield program
    yield slog(msg=f"Program completed", result_type=type(result).__name__)
    return result

@do
def with_log_capture(  # doeff: transform
    program: Program[T],
    output_dir: Path = Path("logs"),
) -> EffectGenerator[T]:
    """ログをキャプチャしてエクスポート"""
    listen_result = yield listen(program)
    
    # ログを保存
    log_file = output_dir / f"run_{time.time()}.jsonl"
    yield export_logs(listen_result.log, log_file)
    
    return listen_result.value
```

---

## 19.6 デバッグへの活用

```python
@do
def debug_failing_operation():
    listen_result = yield listen(potentially_failing_operation())
    
    if listen_result.is_err():
        # エラー時にログを詳細出力
        yield slog(
            msg="Operation failed",
            level="error",
            logs=listen_result.log
        )
        
        # ログを分析
        for entry in listen_result.log:
            if entry.get("level") == "warning":
                yield slog(msg="Warning found", entry=entry)
```

---

## 19.7 メトリクス収集

```python
@do
def with_metrics(operation_name: str, operation: Program[T]):
    start_time = yield GetTime()
    
    result = yield Safe(operation)
    
    end_time = yield GetTime()
    duration = (end_time - start_time).total_seconds()
    
    yield slog(
        msg="Metrics",
        operation=operation_name,
        duration_seconds=duration,
        success=result.is_ok(),
        error_type=type(result.err()).__name__ if result.is_err() else None
    )
    
    if result.is_err():
        raise result.err()
    
    return result.ok()
```

---

## まとめ

| 機能 | 用途 |
|------|------|
| `slog(**kwargs)` | 構造化ログエントリ |
| `listen(program)` | サブプログラムのログキャプチャ |
| `# doeff: transform` | 後処理関数のマーキング |
| JSON Lines | ログのエクスポート形式 |

次の章では、Kleisli Tools と transform を見ていく。

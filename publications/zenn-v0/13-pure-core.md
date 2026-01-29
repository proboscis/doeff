# 第13章: Pure Core パターン

## この章で学ぶこと

- Pure Core パターンとは何か
- ロジックと IO の完全分離
- テスト容易性の向上
- アーキテクチャへの適用

---

## 13.1 Pure Core パターンとは

アプリケーションを2つの層に分ける設計パターン。

```
┌─────────────────────────────────────┐
│  Pure Core（純粋なロジック）        │
│  - 副作用なし                       │
│  - 決定論的                         │
│  - テストが簡単                     │
└───────────────┬─────────────────────┘
                │ エフェクトで接続
                ▼
┌─────────────────────────────────────┐
│  Impure Shell（IOの実行）           │
│  - データベースアクセス              │
│  - ネットワーク通信                  │
│  - ファイル操作                      │
└─────────────────────────────────────┘
```

doeff では、この分離がエフェクトによって自然に実現される。

---

## 13.2 従来のアプローチの問題

```python
# 従来: ロジックと IO が混在
class OrderProcessor:
    def __init__(self, db, cache, logger):
        self.db = db
        self.cache = cache
        self.logger = logger
    
    def process_order(self, order_id):
        self.logger.info(f"Processing {order_id}")  # IO
        
        order = self.db.get(order_id)  # IO
        
        # ビジネスロジック
        if order["amount"] > 1000:
            discount = 0.1
        else:
            discount = 0
        
        total = order["amount"] * (1 - discount)  # 純粋
        
        self.cache.set(order_id, total)  # IO
        
        return total
```

問題:
- テストには全ての依存のモックが必要
- ビジネスロジックがどこにあるか不明確
- 再利用が困難

---

## 13.3 Pure Core アプローチ

### ステップ1: 純粋なロジックを分離

```python
# Pure Core: 純粋な計算のみ
def calculate_discount(amount: float) -> float:
    """純粋関数: 入力から出力を決定"""
    if amount > 1000:
        return 0.1
    return 0

def calculate_total(amount: float, discount: float) -> float:
    """純粋関数: 計算のみ"""
    return amount * (1 - discount)
```

### ステップ2: doeff でオーケストレーション

```python
@do
def process_order(order_id):
    # IO: データ取得
    yield Log(f"Processing {order_id}")
    order = yield Get("orders", order_id)
    
    # Pure Core: 純粋な計算
    discount = calculate_discount(order["amount"])
    total = calculate_total(order["amount"], discount)
    
    # IO: 結果の保存
    yield Put("totals", order_id, total)
    yield Log(f"Order {order_id}: total = {total}")
    
    return total
```

---

## 13.4 利点

### テストの簡素化

```python
# Pure Core のテスト: モック不要
def test_calculate_discount():
    assert calculate_discount(500) == 0
    assert calculate_discount(1500) == 0.1

def test_calculate_total():
    assert calculate_total(1000, 0.1) == 900
    assert calculate_total(500, 0) == 500

# オーケストレーションのテスト
async def test_process_order():
    runtime = AsyncRuntime()
    result = await runtime.run(
        process_order("ORD-001"),
        store={
            "orders": {"ORD-001": {"amount": 1500}},
            "totals": {}
        }
    )
    assert result.value == 1350  # 1500 * 0.9
```

### 関心の分離

| 層 | 責務 | テスト方法 |
|----|------|-----------|
| Pure Core | ビジネスロジック | 単体テスト |
| doeff Program | オーケストレーション | 統合テスト |
| ランタイム | IO の実行 | E2E テスト |

---

## 13.5 実践例: 注文処理システム

### Pure Core

```python
from dataclasses import dataclass
from typing import List

@dataclass
class OrderItem:
    product_id: str
    quantity: int
    unit_price: float

@dataclass
class Order:
    id: str
    customer_id: str
    items: List[OrderItem]

# 純粋な計算
def calculate_subtotal(items: List[OrderItem]) -> float:
    return sum(item.quantity * item.unit_price for item in items)

def calculate_tax(subtotal: float, tax_rate: float = 0.1) -> float:
    return subtotal * tax_rate

def calculate_shipping(subtotal: float) -> float:
    if subtotal >= 5000:
        return 0  # 5000円以上で送料無料
    return 500

def calculate_order_total(items: List[OrderItem]) -> dict:
    subtotal = calculate_subtotal(items)
    tax = calculate_tax(subtotal)
    shipping = calculate_shipping(subtotal)
    total = subtotal + tax + shipping
    
    return {
        "subtotal": subtotal,
        "tax": tax,
        "shipping": shipping,
        "total": total
    }
```

### doeff オーケストレーション

```python
@do
def process_order(order_id):
    yield Log(f"[START] Processing order {order_id}")
    
    # データ取得
    order_data = yield Get("orders", order_id)
    customer = yield Get("customers", order_data["customer_id"])
    
    # Pure Core で計算
    items = [OrderItem(**item) for item in order_data["items"]]
    totals = calculate_order_total(items)
    
    yield Log(f"Calculated totals: {totals}")
    
    # 結果を保存
    result = {
        "order_id": order_id,
        "customer_name": customer["name"],
        **totals,
        "status": "processed"
    }
    
    yield Put("processed_orders", order_id, result)
    yield Log(f"[END] Order {order_id} processed")
    
    return result
```

---

## 13.6 複雑なドメインロジック

### バリデーション

```python
# Pure Core: バリデーションルール
def validate_order(order: dict) -> list[str]:
    errors = []
    
    if not order.get("items"):
        errors.append("Order must have at least one item")
    
    for item in order.get("items", []):
        if item["quantity"] <= 0:
            errors.append(f"Invalid quantity for {item['product_id']}")
        if item["unit_price"] < 0:
            errors.append(f"Invalid price for {item['product_id']}")
    
    return errors

# doeff でバリデーション実行
@do
def validate_and_process(order_id):
    order = yield Get("orders", order_id)
    
    # Pure Core でバリデーション
    errors = validate_order(order)
    
    if errors:
        yield Log(f"Validation failed: {errors}")
        return {"success": False, "errors": errors}
    
    # バリデーション成功後に処理
    result = yield process_order(order_id)
    return {"success": True, "result": result}
```

### 状態遷移

```python
# Pure Core: 状態遷移ルール
VALID_TRANSITIONS = {
    ("pending", "pay"): "paid",
    ("paid", "ship"): "shipped",
    ("shipped", "deliver"): "delivered",
    ("pending", "cancel"): "cancelled",
    ("paid", "refund"): "refunded",
}

def get_next_state(current: str, action: str) -> str | None:
    return VALID_TRANSITIONS.get((current, action))

def is_valid_transition(current: str, action: str) -> bool:
    return (current, action) in VALID_TRANSITIONS

# doeff で状態遷移
@do
def transition_order(order_id, action):
    current_state = yield Get("order_states", order_id)
    
    # Pure Core で遷移チェック
    if not is_valid_transition(current_state, action):
        raise ValueError(f"Invalid transition: {current_state} + {action}")
    
    next_state = get_next_state(current_state, action)
    
    yield Put("order_states", order_id, next_state)
    yield Log(f"Order {order_id}: {current_state} -> {next_state}")
    
    return next_state
```

---

## 13.7 ガイドライン

### Pure Core に入れるもの

- 計算ロジック
- バリデーションルール
- データ変換
- ビジネスルール
- 状態遷移ロジック

### doeff に残すもの

- データの取得/保存
- ログ出力
- 外部サービス呼び出し
- エラーハンドリングの制御フロー

### 判断基準

「この関数は、同じ入力に対して常に同じ出力を返すか？」

- Yes → Pure Core
- No → doeff (エフェクトとして)

---

## まとめ

| 層 | 内容 | 特徴 |
|----|------|------|
| Pure Core | ビジネスロジック | 純粋、決定論的、テスト容易 |
| doeff Program | オーケストレーション | エフェクトで IO を宣言 |
| ランタイム | 実行 | エフェクトを解釈・実行 |

Pure Core パターンにより:
- テストが簡単になる
- コードの意図が明確になる
- 再利用性が向上する

次の章では、構造化ログと実行トレースを見ていく。

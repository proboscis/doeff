# 第1章: 従来のPythonの問題点

## この章で学ぶこと

- なぜ「普通のPython」では複雑なアプリケーションが辛くなるのか
- 状態管理、非同期処理、テストの何が難しいのか
- これらの問題に共通する根本原因

---

## 1.1 あなたも経験したことがあるはず

Pythonでアプリケーションを書いていて、こんな経験はないだろうか？

### コールバック地獄

```python
def fetch_user(user_id, callback):
    def on_user_fetched(user):
        def on_orders_fetched(orders):
            def on_items_fetched(items):
                callback({"user": user, "orders": orders, "items": items})
            fetch_items(orders, on_items_fetched)
        fetch_orders(user, on_orders_fetched)
    db.get_user(user_id, on_user_fetched)
```

async/awaitで改善できる？確かに。でも別の問題が出てくる。

### 状態とロジックの混在

```python
class OrderProcessor:
    def __init__(self, db, cache, logger, metrics):
        self.db = db
        self.cache = cache
        self.logger = logger
        self.metrics = metrics
    
    def process_order(self, order_id):
        self.logger.info(f"Processing {order_id}")  # 副作用
        order = self.db.get(order_id)                # 副作用
        if order in self.cache:                      # 副作用
            return self.cache[order]
        result = self._calculate(order)              # 純粋？
        self.cache[order_id] = result                # 副作用
        self.metrics.increment("orders_processed")  # 副作用
        return result
```

「ビジネスロジック」はどこ？ `_calculate` だけ？
残りは全部「どうやるか」の詳細。

### テストの難しさ

```python
def test_process_order():
    # モックの嵐
    mock_db = Mock()
    mock_cache = Mock()
    mock_logger = Mock()
    mock_metrics = Mock()
    
    mock_db.get.return_value = {"id": 1, "amount": 100}
    mock_cache.__contains__.return_value = False
    
    processor = OrderProcessor(mock_db, mock_cache, mock_logger, mock_metrics)
    result = processor.process_order(1)
    
    # 何をテストしてる？ロジック？それとも配線？
    mock_db.get.assert_called_once_with(1)
    mock_cache.__setitem__.assert_called_once()
    mock_metrics.increment.assert_called_once_with("orders_processed")
```

テストコードの半分以上がモックの設定。
本当にテストしたいのは「注文処理のロジック」なのに。

---

## 1.2 問題の根本原因

これらの問題に共通する根本原因は何か？

**「何をしたいか」と「どうやるか」が混在している**

```
従来のコード:
┌─────────────────────────────────────┐
│  ビジネスロジック                    │
│    + データベースアクセス方法        │
│    + キャッシュの実装詳細            │
│    + ログの出力方法                  │
│    + メトリクスの送信方法            │
│                                     │
│  全部が一つの関数に混在             │
└─────────────────────────────────────┘
```

これが引き起こす問題:

| 問題 | なぜ起きるか |
|------|-------------|
| テストが難しい | IOを分離できないからモックが必要 |
| 再利用が難しい | 具体的な実装に依存しているから |
| 理解が難しい | 本質と詳細が混在しているから |
| 変更が難しい | 一箇所の変更が全体に波及するから |

---

## 1.3 理想の世界

もし、こう書けたらどうだろう？

```python
@do
def process_order(order_id):
    yield Log(f"Processing {order_id}")      # 「ログを出したい」
    order = yield Get("orders", order_id)    # 「注文を取得したい」
    
    cached = yield CacheGet(order_id)        # 「キャッシュを確認したい」
    if cached:
        return cached
    
    result = calculate(order)                # 純粋なロジック
    
    yield CachePut(order_id, result)         # 「キャッシュに保存したい」
    yield Emit("order_processed", order_id)  # 「イベントを発行したい」
    
    return result
```

このコードは:
- **何をしたいか**だけを記述している
- **どうやるか**は書いていない
- データベース？キャッシュ？ログ？ → 別の場所で決める

```
理想のコード:
┌─────────────────────────────────────┐
│  Pure Core（純粋なロジック）        │
│  「何をしたいか」だけを記述          │
│  • Log("...")  → ログを出したい     │
│  • Get(...)    → データが欲しい     │
│  • CachePut    → 保存したい         │
└───────────────┬─────────────────────┘
                │ エフェクトとして発行
                ▼
┌─────────────────────────────────────┐
│  Handler（実行環境）                │
│  「どうやるか」を決める              │
│  • Log → print? ファイル? 無視?    │
│  • Get → DB? API? モック?          │
└─────────────────────────────────────┘
```

---

## 1.4 これが代数的エフェクト

この「分離」を実現する仕組みが**代数的エフェクト**。

- 1990年代: 研究が始まる（Plotkin, Power）
- 2010年代: 実用的な言語が登場（Koka, Eff）
- 2019年: Dan Abramovの記事でJSコミュニティに広まる
- 2022年: OCaml 5.0で産業用言語に初搭載
- **今**: Pythonでも使える → **doeff**

次の章では、代数的エフェクトの考え方をもう少し詳しく見ていく。

---

## まとめ

- 従来のPythonでは「何をしたいか」と「どうやるか」が混在しがち
- これがテスト困難、再利用困難、理解困難の原因
- 代数的エフェクトは、この2つを分離する仕組み
- doeffは、Pythonで代数的エフェクトを使うためのライブラリ

---

## 議論ポイント

> **Q1**: あなたのプロジェクトで、モックの嵐に悩んだ経験はありますか？
> 
> **Q2**: 「ビジネスロジック」と「インフラの詳細」を分離できていますか？
>
> **Q3**: この分離が実現できたら、何が変わりそうですか？

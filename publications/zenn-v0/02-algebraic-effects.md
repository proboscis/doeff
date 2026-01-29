# 第2章: 代数的エフェクトという解決策

## この章で学ぶこと

- 代数的エフェクトの基本的なアイデア
- なぜ「yield」で副作用を表現できるのか
- doeffがどのようにこのアイデアを実現しているか

---

## 2.1 発想の転換

前章で見た問題の根本原因は「何をしたいか」と「どうやるか」の混在だった。

では、どうすれば分離できるのか？

**発想の転換**: 副作用を「実行する」のではなく「宣言する」

```python
# 従来: 副作用を実行する
def process():
    print("Starting...")          # 今すぐ出力される
    data = db.query("SELECT *")   # 今すぐDBにアクセス
    cache.set("key", data)        # 今すぐキャッシュに書き込む
    return data

# 新しい発想: 副作用を宣言する
def process():
    yield Log("Starting...")      # 「ログを出したい」と宣言
    data = yield Query("SELECT *") # 「クエリしたい」と宣言
    yield CacheSet("key", data)    # 「キャッシュしたい」と宣言
    return data
```

この違いは決定的だ。

宣言された副作用は、**まだ実行されていない**。
誰かが後で「どう実行するか」を決める。

---

## 2.2 yield の魔法

Pythonの `yield` は、関数の実行を「一時停止」して値を返す。

```python
def counter():
    yield 1
    yield 2
    yield 3

gen = counter()
print(next(gen))  # 1 (1を返して停止)
print(next(gen))  # 2 (再開して2を返して停止)
print(next(gen))  # 3 (再開して3を返して停止)
```

ここで重要なのは、`yield` は**双方向**だということ。

```python
def conversation():
    question = "What is your name?"
    answer = yield question  # 質問を出して、答えを待つ
    return f"Hello, {answer}!"

gen = conversation()
q = next(gen)           # "What is your name?"
try:
    gen.send("Alice")   # 答えを送る
except StopIteration as e:
    print(e.value)      # "Hello, Alice!"
```

この仕組みを使えば:

1. 関数が「やりたいこと」を `yield` で宣言
2. 外部の誰かがそれを受け取り、実際に実行
3. 結果を `send()` で関数に返す
4. 関数は何事もなかったかのように続行

これが代数的エフェクトの本質だ。

---

## 2.3 エフェクトとハンドラ

代数的エフェクトは2つの概念から成る:

### エフェクト (Effect)

「やりたいこと」の宣言。データとして表現される。

```python
@dataclass(frozen=True)
class Log:
    message: str

@dataclass(frozen=True)
class Query:
    sql: str

@dataclass(frozen=True)
class CacheSet:
    key: str
    value: Any
```

### ハンドラ (Handler)

エフェクトを受け取り、「どうやるか」を決める。

```python
# 本番用ハンドラ
def production_handler(effect):
    if isinstance(effect, Log):
        logger.info(effect.message)
        return None
    elif isinstance(effect, Query):
        return database.execute(effect.sql)
    elif isinstance(effect, CacheSet):
        redis.set(effect.key, effect.value)
        return None

# テスト用ハンドラ
def test_handler(effect):
    if isinstance(effect, Log):
        test_logs.append(effect.message)  # 記録だけ
        return None
    elif isinstance(effect, Query):
        return mock_data[effect.sql]      # モックデータ
    elif isinstance(effect, CacheSet):
        test_cache[effect.key] = effect.value
        return None
```

**同じプログラム**を**異なるハンドラ**で実行できる:

```
┌─────────────────────────────────────┐
│  プログラム（不変）                  │
│  yield Log("Starting...")           │
│  data = yield Query("SELECT *")     │
│  yield CacheSet("key", data)        │
└───────────────┬─────────────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
┌───────────────┐ ┌───────────────┐
│ 本番ハンドラ   │ │ テストハンドラ │
│ - 実DB接続    │ │ - モックデータ │
│ - 実Redis     │ │ - メモリ内    │
│ - 実ログ      │ │ - ログ収集    │
└───────────────┘ └───────────────┘
```

---

## 2.4 doeffでの実現

doeffは `@do` デコレータとランタイムでこの仕組みを実現する。

### プログラムを書く

```python
from doeff import do, Log, Get, Put

@do
def process_order(order_id):
    yield Log(f"Processing order {order_id}")
    
    order = yield Get("orders", order_id)
    if order is None:
        yield Log(f"Order {order_id} not found")
        return None
    
    total = calculate_total(order)  # 純粋な計算
    
    yield Put("processed", order_id, total)
    yield Log(f"Order {order_id} processed: {total}")
    
    return total
```

### 実行する

```python
from doeff import AsyncRuntime
import asyncio

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(
        process_order("ORD-001"),
        store={
            "orders": {"ORD-001": {"items": [100, 200, 300]}},
            "processed": {}
        }
    )
    print(result.value)

asyncio.run(main())
```

### テストする

```python
async def test_process_order():
    runtime = AsyncRuntime()
    result = await runtime.run(
        process_order("ORD-001"),
        store={
            "orders": {"ORD-001": {"items": [10, 20]}},  # テストデータ
            "processed": {}
        }
    )
    assert result.value == 30  # 10 + 20
```

モックは不要。テストデータを渡すだけ。

---

## 2.5 従来手法との比較

### 依存性注入 (DI)

DIも「何をしたいか」と「どうやるか」を分離する。だが、限界がある。

```python
# DIパターン
class OrderProcessor:
    def __init__(self, db: Database, logger: Logger, cache: Cache):
        self.db = db
        self.logger = logger
        self.cache = cache
    
    def process(self, order_id):
        self.logger.info(f"Processing {order_id}")
        order = self.db.get(order_id)
        self.cache.set(order_id, order)
        return order
```

**DIの問題点**:

| 問題 | 説明 |
|------|-----|
| コンストラクタ肥大化 | 依存が増えるとコンストラクタが巨大になる |
| テスト準備が大変 | 全ての依存のモックを用意する必要 |
| 型が複雑化 | インターフェース定義が増える |
| 副作用の位置が曖昧 | どのメソッド呼び出しが副作用か不明瞭 |

**doeffの解決**:

```python
@do
def process_order(order_id):
    yield Log(f"Processing {order_id}")   # 副作用だと明示
    order = yield Get("orders", order_id)  # 副作用だと明示
    yield CachePut(order_id, order)        # 副作用だと明示
    return order
```

- コンストラクタ不要
- 依存関係はエフェクトとして明示
- テストは単にデータを渡すだけ
- `yield` の有無で副作用が一目瞭然

### モナド

Haskellやその他の関数型言語では、モナドで副作用を扱う。

```haskell
-- Haskell
process :: OrderId -> ReaderT Config (StateT Store (WriterT [Log] IO)) Order
process orderId = do
    tell ["Processing " ++ show orderId]
    order <- getOrder orderId
    modify (addToProcessed orderId order)
    return order
```

**モナドの問題点**:

| 問題 | 説明 |
|------|-----|
| モナドの積み重ね | `ReaderT (StateT (WriterT IO))` のような複雑な型 |
| 順序の固定 | モナドトランスフォーマーの順序を変えると大変 |
| lift地獄 | 適切なモナド層に到達するために `lift` を連発 |
| 学習曲線 | モナド則、各種トランスフォーマーの理解が必要 |

**doeffの解決**:

```python
@do
def process(order_id):
    yield Log(f"Processing {order_id}")   # Writer
    order = yield Get("orders", order_id)  # State (Reader)
    yield Put("processed", order_id, order) # State
    return order
```

- エフェクトは平坦に並ぶ（積み重ね不要）
- 順序を気にしなくていい
- `lift` 不要
- 直感的な `yield` だけ覚えればOK

---

## 2.6 代数的エフェクトの理論的背景

「代数的」とは何か？

### 代数 = 操作 + 等式

```
整数の代数:
- 操作: +, -, *, /
- 等式: a + b = b + a, a * 1 = a, ...
```

エフェクトも同じように考える:

```
ログの代数:
- 操作: Log(message)
- 等式: Log(a); Log(b) ≠ Log(b); Log(a)  (順序に意味がある)

状態の代数:
- 操作: Get(key), Put(key, value)
- 等式: Get(k) after Put(k, v) = v  (書いた値が読める)
```

この「操作と等式」で定義された構造がハンドラによって解釈される。

### エフェクトシステムとしての構造

doeffの内部は、Pythonのジェネレータを活用したエフェクトシステムだ。
Free Monadに着想を得ているが、実装はジェネレータベースで、
より実用的なアプローチを取っている。

```
┌─────────────────────────────────────┐
│  エフェクト定義                      │
│  Log, Get, Put, Query, ...          │
└───────────────┬─────────────────────┘
                │ ジェネレータによる構成
                ▼
┌─────────────────────────────────────┐
│  Program[T]                         │
│  モナドとして合成可能な計算           │
└───────────────┬─────────────────────┘
                │ ハンドラによる解釈
                ▼
┌─────────────────────────────────────┐
│  実行結果                            │
│  実際のIO、状態変更、ログ出力         │
└─────────────────────────────────────┘
```

ただし、これを理解しなくてもdoeffは使える。
`yield` と `@do` だけ覚えれば十分だ。

---

## 2.7 制約と注意点

doeffは万能ではない。いくつかの制約がある。

### 1. Python Generatorはシリアライズできない

```python
# これはできない
import pickle

@do
def my_program():
    yield Log("Hello")
    return 42

prog = my_program()
pickle.dumps(prog)  # エラー！
```

**影響**: 計算の途中状態を保存・復元することはできない。

**対処**: 構造化ログ（StructuredLog）で実行トレースを記録し、
必要なら最初から再実行する。

### 2. エラーハンドリングの選択肢

doeffでは、エラーを扱う方法が2つある。

```python
# 方法1: try/except（Pythonネイティブ）
@do
def with_try_except():
    try:
        value = yield risky_operation()
    except Exception:
        return "fallback"  # これは動く！
    return value

# 方法2: Safe エフェクト（明示的なResult型）
@do
def with_safe():
    result = yield Safe(risky_operation())
    if result.is_ok():
        return result.ok()
    return "fallback"
```

doeffのランタイムはエラーを `.throw()` でジェネレータに送り返すため、
`try/except` は期待通りに動作する。
`Safe` はエラーを `Result` 型として扱いたい場合に便利だ。

### 3. 非同期との統合

doeffは `async/await` と併用できるが、ランタイムが非同期対応である必要がある。

```python
from doeff import AsyncRuntime

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(my_program())  # awaitが必要
```

---

## まとめ

- 代数的エフェクトは「副作用の宣言」と「副作用の実行」を分離する
- Pythonの `yield` は双方向通信を可能にし、これを実現できる
- doeffは `@do` デコレータとランタイムでこの仕組みを提供
- DIやモナドと比べて、より直感的で柔軟
- ただし、シリアライズ不可などの制約がある

次の章では、doeffの設計思想をより深く見ていく。

---

## 議論ポイント

> **Q1**: あなたのプロジェクトで、DIのコンストラクタが肥大化した経験はありますか？
> 
> **Q2**: モナドを学ぼうとして挫折した経験はありますか？doeffならどうでしょう？
>
> **Q3**: 「副作用を宣言する」という発想は、あなたのコードにどう適用できそうですか？

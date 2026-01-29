# 第3章: doeffの設計思想

## この章で学ぶこと

- doeffが目指したもの
- ジェネレータベースのエフェクトシステムとは何か
- Pythonイディオムとの調和

---

## 3.1 doeffが解決したい問題

前章で代数的エフェクトの概念を学んだ。では、なぜ**Python**で実装するのか？

### 既存の選択肢

| 言語 | ライブラリ/機能 | 問題点 |
|------|----------------|--------|
| Haskell | mtl, polysemy | 学習曲線が急、実務で使いにくい |
| OCaml 5 | 組み込みエフェクト | OCamlを書ける人が少ない |
| Koka | 言語全体がエフェクト | まだ実験的、エコシステムが小さい |
| TypeScript | Effect-TS | 型が複雑、ボイラープレートが多い |

**Pythonのメリット**:
- 多くの開発者が既に使っている
- 豊富なエコシステム（ML、Web、データ処理）
- シンプルな文法

**doeffの目標**: Pythonで「使える」代数的エフェクトを提供する

---

## 3.2 設計原則

doeffは以下の原則に基づいて設計されている。

### 原則1: Pythonイディオムを尊重する

```python
# Haskell風（doeffは採用しない）
result = program.flat_map(lambda x: 
    another_program(x).flat_map(lambda y:
        Program.pure(x + y)))

# Python風（doeffが採用）
@do
def combined():
    x = yield program
    y = yield another_program(x)
    return x + y
```

`yield` を使った do-notation は、Pythonの generator 構文に自然にマッチする。

### 原則2: 型安全性を維持する

```python
from doeff import do, Get, Put, Log

@do
def typed_program() -> EffectGenerator[int]:
    count: int = yield Get("counter")  # 型ヒント可能
    yield Put("counter", count + 1)
    yield Log(f"Count: {count}")
    return count
```

doeffは `typing` と連携し、IDE補完やmypyチェックをサポートする。

### 原則3: 漸進的に導入できる

```python
# 既存コードはそのまま
def legacy_function():
    return requests.get("https://api.example.com").json()

# doeffを部分的に導入
@do
def new_feature():
    yield Log("Starting new feature")
    data = yield IO(legacy_function)  # 既存関数をラップ
    return process(data)
```

全てを書き換える必要はない。必要な部分から導入できる。

### 原則4: テストを第一級市民に

```python
# プロダクションコード
@do
def process_order(order_id):
    yield Log(f"Processing {order_id}")
    order = yield Get("orders", order_id)
    total = calculate_total(order)
    yield Put("processed", order_id, total)
    return total

# テスト - モック不要
async def test_process_order():
    runtime = AsyncRuntime()
    result = await runtime.run(
        process_order("ORD-001"),
        store={
            "orders": {"ORD-001": {"items": [100, 200]}},
            "processed": {}
        }
    )
    assert result.value == 300
```

---

## 3.3 ジェネレータベースのエフェクトシステム

doeffの内部は、Pythonのジェネレータを活用したエフェクトシステムだ。
Free Monadに着想を得ているが、実装はより実用的なアプローチを取っている。

### 直感的な説明

doeffは「インタプリタパターン」に近い構造を持つ。

```
┌─────────────────────────────────────┐
│  プログラム（エフェクトの列）        │
│  - yieldで副作用を宣言               │
│  - まだ実行されていない              │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│  インタプリタ（ランタイム）          │
│  - エフェクトを解釈して実行          │
│  - 実行方法を自由に変更可能          │
└─────────────────────────────────────┘
```

doeffでは:
- **プログラム** = `Program[T]`（エフェクトの列）
- **インタプリタ** = `AsyncRuntime` など

### なぜエフェクトシステムなのか

任意のエフェクト定義から、合成可能な計算を作れる。

```python
# エフェクトを定義するだけで
@dataclass(frozen=True)
class MyEffect(EffectBase):
    param: str

# 自動的にモナドとして合成可能になる
@do
def use_my_effect():
    result = yield MyEffect("hello")
    return result
```

複雑なモナドトランスフォーマーの積み重ねは不要。

---

## 3.4 Program[T] の構造

doeffの中心は `Program[T]` 型だ。

### 定義

```python
@dataclass(frozen=True)
class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
```

- `generator_func`: 呼び出すとジェネレータを返す関数
- `T`: プログラムが最終的に返す値の型

### 遅延評価

`Program` は作っただけでは実行されない。

```python
@do
def expensive():
    yield Log("This is expensive!")
    return 42

# ここでは何も起きない
prog = expensive()

# ここで初めて実行される
runtime = AsyncRuntime()
result = await runtime.run(prog)
```

### 再利用可能

通常のジェネレータと違い、`Program` は何度でも実行できる。

```python
prog = expensive()

# 1回目
result1 = await runtime.run(prog)

# 2回目（同じProgram）
result2 = await runtime.run(prog)
```

これは `generator_func` が毎回新しいジェネレータを生成するため。

---

## 3.5 ランタイムの役割

ランタイムはエフェクトを「解釈」する。

### AsyncRuntime

非同期IOをサポートする本番用ランタイム。

```python
from doeff import AsyncRuntime

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(
        my_program(),
        env={"config": "value"},
        store={"state": 0}
    )
```

### SimulationRuntime

時間をシミュレートするテスト用ランタイム。

```python
from doeff import SimulationRuntime

def test_with_delays():
    runtime = SimulationRuntime()
    # Delay(3600) も即座に完了
    result = runtime.run(slow_program())
    assert result.is_ok()
```

### カスタムランタイム

独自のランタイムを作ることも可能（上級者向け）。

---

## 3.6 エフェクトの分類

doeffのエフェクトは用途別に分類される。

### Reader系（環境の読み取り）

```python
Ask(key)           # 環境から値を取得
Ask(key, default)  # デフォルト付き
Local(env, prog)   # 一時的に環境を変更
```

### State系（状態の管理）

```python
Get(key)           # 状態を取得
Put(key, value)    # 状態を設定
Modify(key, func)  # 状態を変換
```

### Writer系（ログの蓄積）

```python
Log(message)       # ログを出力
Tell(values)       # 値を蓄積
Listen(prog)       # サブプログラムのログを取得
```

### Async系（非同期処理）

```python
Await(coro)        # コルーチンを待機
Gather(*progs)     # 並列実行
Spawn(prog)        # バックグラウンド実行
Delay(seconds)     # 遅延
```

### Control系（制御フロー）

```python
Safe(prog)         # エラーをResultでキャッチ
IO(func)           # 副作用のある関数を実行
```

---

## 3.7 他のアプローチとの比較

### vs. async/await のみ

```python
# async/awaitだけ
async def fetch_and_process():
    data = await fetch_data()      # 非同期OK
    logger.info(f"Got {data}")     # ログはグローバル
    cache[key] = data              # 状態はグローバル
    return data

# doeff
@do
def fetch_and_process():
    data = yield Await(fetch_data())  # 非同期OK
    yield Log(f"Got {data}")          # ログは管理される
    yield Put(key, data)              # 状態は管理される
    return data
```

**doeffの利点**: ログと状態が明示的に管理され、テスト可能。

### vs. FastAPI の Depends

```python
# FastAPI
async def get_db():
    db = Database()
    try:
        yield db
    finally:
        await db.close()

@app.get("/users")
async def get_users(db: Database = Depends(get_db)):
    return await db.fetch_all("SELECT * FROM users")

# doeff
@do
def get_users():
    db_url = yield Ask("database_url")
    result = yield Await(fetch_users(db_url))
    yield Log(f"Fetched {len(result)} users")
    return result
```

**FastAPI**: HTTPリクエスト・レスポンスに特化
**doeff**: 汎用的なエフェクト管理

---

## 3.8 制約の再確認

doeffには制約がある。これらを理解して使うことが重要。

### シリアライズ不可

Pythonのジェネレータは `pickle` できない。
計算の途中状態を保存・復元することはできない。

**対処法**: 構造化ログで実行トレースを記録し、必要なら再実行。

### try/except の制限

`try/except` で `yield` を囲むことでエフェクトのエラーをキャッチできる。
より明示的にエラーを扱いたい場合は `Safe` エフェクトも使える。

### パフォーマンス

ジェネレータベースのため、純粋なPythonより若干遅い。
ただし、IOバウンドな処理では問題にならない。

---

## まとめ

- doeffはPythonイディオムを尊重しつつ、代数的エフェクトを提供
- ジェネレータベースのエフェクトシステムにより、エフェクト定義から合成可能な計算を生成
- `Program[T]` は遅延評価・再利用可能な計算の単位
- ランタイムがエフェクトを解釈し、実際の処理を行う
- 制約を理解し、適切な場面で使うことが重要

次の章から、実際にdoeffを使ったプログラミングを始める。

---

## 議論ポイント

> **Q1**: あなたのプロジェクトで、テストのためにモックを大量に作った経験はありますか？
> 
> **Q2**: async/await だけでは解決できなかった問題はありますか？
>
> **Q3**: 「漸進的な導入」は、あなたの環境で現実的ですか？

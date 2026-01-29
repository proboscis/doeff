# 第11章: Kleisli Arrow と合成

## この章で学ぶこと

- KleisliProgram とは何か
- 自動的な Program アンラップ
- 関数合成のパターン
- パイプライン構築

---

## 11.1 KleisliProgram とは

`@do` デコレータが作るのは `KleisliProgram` だ。

```python
@do
def my_func(x: int, y: str) -> EffectGenerator[bool]:
    yield Log(f"x={x}, y={y}")
    return x > 0 and y != ""

# 型: KleisliProgram[(int, str), bool]
# つまり: Callable[[int, str], Program[bool]]
```

通常の関数と違い、以下の機能を持つ:

1. 引数として渡された `Program` を自動でアンラップ
2. `>>` 演算子で合成可能
3. `fmap` で結果を変換可能
4. `partial` で部分適用可能

---

## 11.2 自動 Program アンラップ

KleisliProgram は `Program` 型の引数を自動でアンラップする。

```python
@do
def add(x: int, y: int):
    yield Log(f"Adding {x} + {y}")
    return x + y

@do
def multiply(x: int, y: int):
    return x * y

@do
def calculation():
    # add(5, 3) は Program[int] を返す
    a = add(5, 3)  # Program[int] (まだ実行されていない)
    
    # multiply(2, 4) も Program[int]
    b = multiply(2, 4)  # Program[int]
    
    # add に Program を渡すと、自動でアンラップされる
    result = yield add(a, b)  # add(8, 8) として実行
    
    return result  # 16
```

### アンラップの仕組み

```python
@do
def process(x: int, y: int):
    return x + y

# Program を作成
prog_x = Program.pure(5)
prog_y = Program.pure(10)

# Program を引数として渡す
result = process(prog_x, prog_y)
# 内部処理:
# 1. prog_x をアンラップして 5 を取得
# 2. prog_y をアンラップして 10 を取得
# 3. process(5, 10) を実行
# 4. Program[int] を返す
```

### アンラップを無効にする

型アノテーションで `Program[T]` と指定すると、アンラップされない。

```python
@do
def manual_control(x: int, y: Program[int]):
    # x は自動アンラップされる
    # y はアンラップされない（Program のまま）
    
    yield Log(f"x = {x}")
    actual_y = yield y  # 手動でアンラップ
    yield Log(f"y = {actual_y}")
    
    return x + actual_y
```

---

## 11.3 関数合成: `>>` 演算子

`>>` (または `and_then_k`) で関数を連鎖できる。

```python
@do
def fetch_user(user_id: int):
    yield Log(f"Fetching user {user_id}")
    return {"id": user_id, "name": f"User{user_id}"}

@do
def fetch_posts(user: dict):
    yield Log(f"Fetching posts for {user['name']}")
    return [{"id": 1, "title": "Post 1"}, {"id": 2, "title": "Post 2"}]

# >> で合成
fetch_user_posts = fetch_user >> (lambda user: fetch_posts(user))

# 実行
result = await runtime.run(fetch_user_posts(123))
# [{"id": 1, "title": "Post 1"}, {"id": 2, "title": "Post 2"}]
```

### パイプラインパターン

```python
@do
def load_data(filename: str):
    yield Log(f"Loading {filename}")
    return {"data": [1, 2, 3, 4, 5]}

@do
def validate_data(data: dict):
    yield Log("Validating")
    if not data["data"]:
        raise ValueError("Empty data")
    return data

@do
def process_data(data: dict):
    yield Log("Processing")
    return {"result": sum(data["data"])}

# パイプラインを構築
pipeline = (
    load_data
    >> (lambda d: validate_data(d))
    >> (lambda d: process_data(d))
)

# 実行
result = await runtime.run(pipeline("data.json"))
# {"result": 15}
```

---

## 11.4 fmap: 結果の変換

純粋関数で結果を変換する。

```python
@do
def get_user():
    return {"id": 1, "name": "Alice", "age": 30}

# name だけを取り出す
get_name = get_user.fmap(lambda user: user["name"])

result = await runtime.run(get_name())
# "Alice"
```

### fmap と >> の組み合わせ

```python
@do
def fetch_number():
    return 42

pipeline = (
    fetch_number
    .fmap(lambda x: x * 2)  # 84
    .fmap(lambda x: x + 10)  # 94
    .and_then_k(lambda x: Program.pure(f"Result: {x}"))  # "Result: 94"
)

result = await runtime.run(pipeline())
# "Result: 94"
```

---

## 11.5 partial: 部分適用

一部の引数を固定して新しい関数を作る。

```python
@do
def greet(greeting: str, name: str):
    message = f"{greeting}, {name}!"
    yield Log(message)
    return message

# greeting を固定
say_hello = greet.partial("Hello")
say_hi = greet.partial("Hi")

result1 = await runtime.run(say_hello("Alice"))  # "Hello, Alice!"
result2 = await runtime.run(say_hi("Bob"))        # "Hi, Bob!"
```

### カリー化パターン

```python
@do
def multiply(x: int, y: int):
    return x * y

# 特殊化した関数を作成
double = multiply.partial(2)
triple = multiply.partial(3)

@do
def use_multipliers():
    a = yield double(5)  # 10
    b = yield triple(5)  # 15
    return a + b  # 25
```

---

## 11.6 高階関数パターン

### 関数を引数に取る

```python
@do
def apply_twice(f, x):
    """関数を2回適用"""
    result1 = yield f(x)
    result2 = yield f(result1)
    return result2

@do
def increment(x: int):
    return x + 1

@do
def example():
    result = yield apply_twice(increment, 5)
    return result  # 7 (5 -> 6 -> 7)
```

### ファクトリパターン

```python
def create_processor(config: dict):
    """設定に基づいてプロセッサを作成"""
    
    @do
    def process(data: list):
        yield Log(f"Processing with config: {config}")
        
        if config.get("filter"):
            data = [x for x in data if x > 0]
        
        if config.get("double"):
            data = [x * 2 for x in data]
        
        return data
    
    return process

# 特殊化したプロセッサを作成
positive_doubler = create_processor({"filter": True, "double": True})
simple_filter = create_processor({"filter": True})

result = await runtime.run(positive_doubler([1, -2, 3, -4, 5]))
# [2, 6, 10]
```

---

## 11.7 実践例: データ変換パイプライン

```python
@do
def read_csv(filename: str):
    yield Log(f"Reading {filename}")
    # CSVを読み込む処理
    return [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25},
        {"name": "Charlie", "age": 35}
    ]

@do
def filter_by_age(min_age: int, data: list):
    yield Log(f"Filtering age >= {min_age}")
    return [row for row in data if row["age"] >= min_age]

@do
def add_status(data: list):
    yield Log("Adding status")
    return [{**row, "status": "active"} for row in data]

@do
def write_json(filename: str, data: list):
    yield Log(f"Writing {filename}")
    # JSONを書き出す処理
    return {"written": len(data), "filename": filename}

# パイプラインを構築
def create_pipeline(min_age: int, output_file: str):
    return (
        read_csv
        >> (lambda data: filter_by_age(min_age, data))
        >> add_status
        >> (lambda data: write_json(output_file, data))
    )

# 使用
pipeline = create_pipeline(min_age=28, output_file="output.json")
result = await runtime.run(pipeline("input.csv"))
```

---

## 11.8 ベストプラクティス

### 型アノテーションを使う

```python
# Good: 型が明確
@do
def process(x: int, y: str) -> EffectGenerator[bool]:
    ...

# Less clear: 型がない
@do
def process(x, y):
    ...
```

### 小さな関数に分割して合成

```python
# Good: 小さく分割して合成
@do
def validate(data):
    ...

@do
def transform(data):
    ...

@do
def save(data):
    ...

pipeline = validate >> transform >> save

# Less good: 1つの大きな関数
@do
def validate_transform_and_save(data):
    # 全部ここに...
```

---

## まとめ

| 機能 | 説明 |
|------|------|
| 自動アンラップ | Program 引数を自動的にアンラップ |
| `>>` | KleisliProgram を連鎖 |
| `fmap` | 純粋関数で結果を変換 |
| `partial` | 部分適用 |
| `Program[T]` アノテーション | アンラップを無効化 |

次の章では、ランタイムとスケジューラを見ていく。

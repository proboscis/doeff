# 第5章: ProgramとEffectの概念

## この章で学ぶこと

- `Program[T]` の内部構造
- `Effect` の仕組み
- 実行モデルの詳細

---

## 5.1 Program[T] とは

`Program[T]` は doeff の中心的な型だ。

### 定義

```python
@dataclass(frozen=True)
class Program(Generic[T]):
    generator_func: Callable[[], Generator[Effect | Program, Any, T]]
```

### 直感的な理解

`Program[T]` は「`T` 型の値を返す計算の**レシピ**」と考えられる。

```
┌─────────────────────────────────────┐
│  Program[int]                       │
│                                     │
│  "整数を返す計算のレシピ"            │
│  - まだ実行されていない              │
│  - 何度でも実行できる                │
│  - 実行方法は外部が決める            │
└─────────────────────────────────────┘
```

### 遅延評価

`Program` を作っただけでは何も起きない。

```python
@do
def expensive():
    yield Log("Computing...")
    import time
    time.sleep(1)
    return 42

# ここでは何も起きない（ログも出ない、時間もかからない）
prog = expensive()
print(type(prog))  # <class 'Program'>

# run() で初めて実行される
result = await runtime.run(prog)  # ここで1秒かかる
```

### 再利用可能

通常の Python ジェネレータは一度しか使えない。
`Program` は何度でも実行できる。

```python
@do
def random_number():
    import random
    return random.randint(1, 100)

prog = random_number()

# 毎回新しいジェネレータが作られる
result1 = await runtime.run(prog)  # 例: 42
result2 = await runtime.run(prog)  # 例: 73
result3 = await runtime.run(prog)  # 例: 15
```

---

## 5.2 Effect とは

`Effect` は「やりたいこと」を表すデータ。

### 基本構造

```python
@dataclass(frozen=True)
class EffectBase(Effect):
    """全てのエフェクトの基底クラス"""
    pass

# 具体的なエフェクト
@dataclass(frozen=True)
class Log(EffectBase):
    message: str

@dataclass(frozen=True)
class Get(EffectBase):
    key: str

@dataclass(frozen=True)
class Put(EffectBase):
    key: str
    value: Any
```

### エフェクトは「データ」

エフェクトは何も実行しない。単なるデータ構造だ。

```python
# これは単なるデータ
log_effect = Log("Hello")
print(log_effect.message)  # "Hello"

# 何も出力されない（まだ実行していない）
```

### エフェクトのライフサイクル

```
1. 作成
   effect = Log("message")

2. yield で発行
   yield effect

3. ランタイムが受け取る
   runtime receives effect

4. ランタイムが解釈・実行
   runtime executes effect

5. 結果をジェネレータに返す
   generator receives result
```

---

## 5.3 @do デコレータの役割

`@do` は関数を `Program` に変換する。

### 変換前後

```python
# 変換前: 普通のジェネレータ関数
def my_func():
    value = yield Get("key")
    return value

# 変換後: KleisliProgram（引数を取ってProgramを返す関数）
@do
def my_func():
    value = yield Get("key")
    return value

# 呼び出すとProgramが返る
prog = my_func()
print(type(prog))  # Program
```

### 型の変換

```python
# 入力の型
Callable[[], Generator[Effect, Any, T]]

# 出力の型（@do適用後）
Callable[[], Program[T]]
```

### 引数を持つ場合

```python
@do
def greet(name: str, greeting: str = "Hello"):
    message = f"{greeting}, {name}!"
    yield Log(message)
    return message

# 呼び出し方は普通の関数と同じ
prog = greet("Alice")
prog = greet("Bob", greeting="Hi")
```

---

## 5.4 実行モデル

プログラムがどう実行されるかを見ていこう。

### 単純な例

```python
@do
def simple():
    yield Log("Step 1")
    yield Log("Step 2")
    return "done"
```

実行の流れ:

```
1. runtime.run(simple()) が呼ばれる
2. generator = simple().generator_func()
3. effect1 = next(generator)  → Log("Step 1")
4. ランタイムが Log を処理（ログ出力）
5. generator.send(None)  → Log("Step 2")
6. ランタイムが Log を処理
7. generator.send(None)  → StopIteration(value="done")
8. 結果 "done" を返す
```

### 値を返すエフェクト

```python
@do
def with_value():
    count = yield Get("counter")  # 値を受け取る
    yield Log(f"Count is {count}")
    return count
```

実行の流れ:

```
1. effect = next(generator)  → Get("counter")
2. ランタイムが状態から値を取得: 42
3. generator.send(42)  → Log("Count is 42")
4. ランタイムがログ出力
5. generator.send(None)  → StopIteration(value=42)
```

### ネストしたプログラム

```python
@do
def inner():
    yield Log("Inner")
    return 10

@do
def outer():
    yield Log("Outer start")
    value = yield inner()  # Programをyield
    yield Log(f"Got {value}")
    return value * 2
```

実行の流れ:

```
1. Log("Outer start") を処理
2. inner() が yield される（これはProgram）
3. ランタイムが inner() を再帰的に実行
   - Log("Inner") を処理
   - return 10
4. outer のジェネレータに 10 を send
5. Log("Got 10") を処理
6. return 20
```

---

## 5.5 モナディック操作

`Program` はモナドとしての操作をサポートする。

### pure / of

純粋な値を `Program` にラップ。

```python
# 42 を Program[int] にラップ
prog = Program.pure(42)

# 実行すると 42 が返る
result = await runtime.run(prog)  # 42
```

### map

結果を変換。

```python
@do
def get_count():
    return yield Get("count")

# 結果を2倍
doubled = get_count().map(lambda x: x * 2)
```

### flat_map

`Program` を返す関数で変換（モナディックバインド）。

```python
def double_program(x):
    return Program.pure(x * 2)

prog = Program.pure(21).flat_map(double_program)
result = await runtime.run(prog)  # 42
```

### then

2つのプログラムを順番に実行（最初の結果は捨てる）。

```python
setup = Put("ready", True)
work = Get("data")

program = setup.then(work)
```

### sequence

複数のプログラムを順番に実行し、結果をリストで返す。

```python
progs = [Program.pure(1), Program.pure(2), Program.pure(3)]
result = Program.sequence(progs)
# result: Program[list[int]] → [1, 2, 3]
```

---

## 5.6 エフェクトの種類

doeff には多くの組み込みエフェクトがある。

### Reader 系

環境（設定）を読み取る。

| エフェクト | 説明 | 戻り値 |
|-----------|------|--------|
| `Ask(key)` | 環境から値を取得 | `Any` |
| `Ask(key, default)` | デフォルト付き取得 | `Any` |
| `Local(env, prog)` | 環境を一時的に変更 | プログラムの戻り値 |

### State 系

状態を管理する。

| エフェクト | 説明 | 戻り値 |
|-----------|------|--------|
| `Get(key)` | 状態を取得 | `Any` |
| `Put(key, value)` | 状態を設定 | `None` |
| `Modify(key, func)` | 状態を変換 | 変換後の値 |

### Writer 系

ログを蓄積する。

| エフェクト | 説明 | 戻り値 |
|-----------|------|--------|
| `Log(message)` | ログを追加 | `None` |
| `Tell(values)` | 値を蓄積 | `None` |
| `Listen(prog)` | サブプログラムのログを取得 | `ListenResult` |

### Async 系

非同期処理を行う。

| エフェクト | 説明 | 戻り値 |
|-----------|------|--------|
| `Await(coro)` | コルーチンを待機 | コルーチンの戻り値 |
| `Gather(*progs)` | 並列実行 | 結果のリスト |
| `Spawn(prog)` | バックグラウンド実行 | `Task` |
| `Delay(seconds)` | 遅延 | `None` |

### Control 系

制御フローを管理する。

| エフェクト | 説明 | 戻り値 |
|-----------|------|--------|
| `Safe(prog)` | エラーをキャッチ | `Result` |
| `IO(func)` | 副作用関数を実行 | 関数の戻り値 |

---

## 5.7 カスタムエフェクトを作る

独自のエフェクトを定義することも可能。

```python
from dataclasses import dataclass
from doeff import EffectBase, Program

@dataclass(frozen=True)
class SendEmail(EffectBase):
    to: str
    subject: str
    body: str

@do
def notify_user(user_email: str):
    yield Log(f"Sending email to {user_email}")
    yield SendEmail(
        to=user_email,
        subject="Notification",
        body="Hello from doeff!"
    )
    yield Log("Email sent")
```

カスタムエフェクトを処理するには、カスタムランタイムまたはハンドラを実装する必要がある（上級者向け）。

---

## まとめ

- `Program[T]` は遅延評価・再利用可能な計算のレシピ
- `Effect` は「やりたいこと」を表すデータ
- `@do` デコレータが関数を `Program` に変換
- 実行時、ランタイムがエフェクトを解釈・実行
- `Program` はモナドとして `map`, `flat_map`, `sequence` などをサポート

次の章では、基本エフェクト（Reader, State, Writer）を詳しく見ていく。

---

## 議論ポイント

> **Q1**: 遅延評価のメリットは何だと思いますか？
> 
> **Q2**: エフェクトが「データ」であることの利点は？
>
> **Q3**: どんなカスタムエフェクトを作りたいですか？

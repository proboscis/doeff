# 第18章: recover パターン

## この章で学ぶこと

- エラーは呼び出し側で処理する原則
- `recover` エフェクトの使い方
- `first_success` パターン
- None を返さない設計

---

## 18.1 原則: エラーは呼び出し側で処理

### 悪い例: None を返す

```python
# 悪い: エラー時に None を返す
@do
def find_user(user_id: str) -> EffectGenerator[User | None]:
    try:
        return db.get_user(user_id)
    except NotFoundError:
        return None  # 呼び出し側が None チェック必要
```

問題:
- 呼び出し側が必ず None チェックが必要
- 型が `User | None` に汚染される
- エラーが静かに無視される

### 良い例: 例外を投げる

```python
# 良い: エラー時は例外
@do
def find_user(user_id: str) -> EffectGenerator[User]:
    user = yield Get("users", user_id)
    if user is None:
        raise ValueError(f"User not found: {user_id}")
    return user
```

利点:
- 型がクリーン（`User` のみ）
- エラーが明示的
- 呼び出し側が `recover` で処理

---

## 18.2 recover エフェクト

`recover` は失敗時のフォールバックを提供する。

```python
from doeff.effects.result import recover

@do
def process_with_fallback():
    # フォールバック値
    user = yield recover(
        find_user("unknown_id"),
        fallback=default_user
    )
    return user
```

### フォールバックの種類

```python
# 1. 値でフォールバック
result = yield recover(operation(), fallback=default_value)

# 2. Program でフォールバック
result = yield recover(
    primary_operation(),
    fallback=fallback_operation()
)

# 3. エラーハンドラでフォールバック
@do
def handle_error(error: Exception):
    yield Log(f"Error occurred: {error}")
    return yield fallback_operation()

result = yield recover(
    primary_operation(),
    fallback=handle_error
)
```

---

## 18.3 実践例: 翻訳ファイルの読み込み

```python
@do
def load_translation(run_id: str):
    """翻訳ファイルを読み込む。なければ例外。"""
    path = f"translations/{run_id}/translation.json"
    data = yield load_json(path)
    return data

@do
def load_translation_with_fallback(run_id: str):
    """プライマリを試し、失敗したらフォールバック。"""
    
    @do
    def _fallback(error: Exception):
        yield Log(f"Primary failed: {error}. Trying fallback...")
        
        # localize.json から抽出
        localize = yield load_json(f"translations/{run_id}/localize.json")
        translation = localize.get("translation")
        
        if not translation:
            raise ValueError("No translation in localize.json")
        
        return translation
    
    return yield recover(
        load_translation(run_id),
        fallback=_fallback
    )
```

---

## 18.4 first_success パターン

複数の選択肢を順番に試す。

```python
from doeff import Program

# 複数のソースを順番に試す
p_data: Program[Data] = Program.first_success(
    load_from_cache(),      # まずキャッシュ
    load_from_local_db(),   # 次にローカルDB
    load_from_remote_api(), # 最後にリモートAPI
)
```

### 仕組み

1. 最初の Program を実行
2. 成功すればその結果を返す
3. 失敗したら次の Program を試す
4. 全て失敗したら最後のエラーを投げる

---

## 18.5 None が許容されるケース

`None` が**エラーではなく正常な結果**の場合のみ許容。

```python
# OK: "設定がない" は正常なケース
@do
def find_optional_config(key: str) -> EffectGenerator[Config | None]:
    """オプショナルな設定を取得。なければ None。"""
    config = yield Get("configs", key)
    return config  # None は "設定されていない" を意味

# NG: "ユーザーが見つからない" はエラー
@do
def find_user(user_id: str) -> EffectGenerator[User]:
    """ユーザーを取得。いなければ例外。"""
    user = yield Get("users", user_id)
    if user is None:
        raise ValueError(f"User not found: {user_id}")
    return user
```

---

## 18.6 パターンの使い分け

| 状況 | 使用するパターン |
|------|----------------|
| 失敗時に固定値を使いたい | `recover(op, fallback=value)` |
| 失敗時に別の処理を試したい | `recover(op, fallback=another_op)` |
| 失敗をログに残したい | `recover(op, fallback=error_handler)` |
| 複数のソースを順番に試す | `Program.first_success(...)` |

---

## 18.7 テストでの活用

```python
async def test_with_recover():
    @do
    def test_program():
        # 必ず失敗する操作
        return yield recover(
            failing_operation(),
            fallback="test_fallback"
        )
    
    result = await runtime.run(test_program())
    assert result.value == "test_fallback"
```

---

## まとめ

| 原則 | 説明 |
|------|------|
| 例外を投げる | 関数はエラー時に例外を投げる |
| 呼び出し側で処理 | `recover` で呼び出し側がフォールバック |
| None は正常なケースのみ | エラーを None で表さない |
| first_success | 複数のソースを順番に試す |

次の章では、構造化ログとトレースの産業利用を見ていく。

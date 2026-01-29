# 第9章: キャッシュシステム

## この章で学ぶこと

- doeff のキャッシュエフェクト
- キャッシュデコレータの使い方
- キャッシュポリシーの設定

---

## 9.1 なぜキャッシュが必要か

同じ計算を何度も行うのは無駄だ。

```python
# キャッシュなし: 毎回計算
@do
def fetch_user(user_id):
    yield Log(f"Fetching user {user_id}...")
    data = yield Await(slow_api_call(user_id))
    return data

# 同じユーザーを3回取得 → 3回APIコール
user1 = yield fetch_user(123)
user2 = yield fetch_user(123)  # 同じ計算を繰り返す
user3 = yield fetch_user(123)  # 同じ計算を繰り返す
```

キャッシュを使えば、2回目以降は即座に結果を返せる。

---

## 9.2 キャッシュエフェクト

### CacheGet / CachePut

```python
from doeff import do, CacheGet, CachePut, Log, Safe

@do
def fetch_with_manual_cache(user_id):
    cache_key = f"user_{user_id}"
    
    # キャッシュを確認
    result = yield Safe(CacheGet(cache_key))
    
    if result.is_ok():
        yield Log(f"Cache hit for {cache_key}")
        return result.ok()
    
    # キャッシュミス: 計算してキャッシュに保存
    yield Log(f"Cache miss for {cache_key}")
    data = yield Await(slow_api_call(user_id))
    
    yield CachePut(cache_key, data, ttl=300)  # 5分間キャッシュ
    
    return data
```

---

## 9.3 キャッシュデコレータ

より簡潔に書ける `@cache` デコレータ。

```python
from doeff import cache, do, Log

@cache(ttl=60)
@do
def expensive_computation(x: int):
    yield Log("Computing (this should only happen once)...")
    # 重い計算
    return x * 2

@do
def main():
    # 1回目: 計算が実行される
    result1 = yield expensive_computation(5)
    yield Log(f"First call: {result1}")
    
    # 2回目: キャッシュから取得
    result2 = yield expensive_computation(5)
    yield Log(f"Second call: {result2}")
    
    # 引数が違えば再計算
    result3 = yield expensive_computation(10)
    yield Log(f"Different arg: {result3}")
```

### デコレータのオプション

```python
@cache(
    ttl=300,                        # 5分間有効
    lifecycle=CacheLifecycle.SESSION,  # セッション期間
    storage=CacheStorage.MEMORY     # メモリに保存
)
@do
def cached_function():
    ...
```

---

## 9.4 キャッシュポリシー

### TTL (Time To Live)

キャッシュの有効期間を秒単位で指定。

```python
@cache(ttl=60)  # 60秒間有効
@do
def short_lived():
    ...

@cache(ttl=3600)  # 1時間有効
@do
def long_lived():
    ...
```

### ライフサイクル

| ライフサイクル | 説明 |
|--------------|------|
| `SESSION` | セッション終了まで有効 |
| `PERSISTENT` | 永続化（再起動後も有効） |
| `TEMPORARY` | 短期間のみ |

```python
from doeff import CacheLifecycle

@cache(lifecycle=CacheLifecycle.PERSISTENT)
@do
def persistent_cache():
    ...
```

### ストレージ

| ストレージ | 説明 |
|----------|------|
| `MEMORY` | メモリ内（デフォルト） |
| `DISK` | ディスクに永続化 |
| `DISTRIBUTED` | 分散キャッシュ |

```python
from doeff import CacheStorage

@cache(storage=CacheStorage.DISK)
@do
def disk_cached():
    ...
```

---

## 9.5 条件付きキャッシュ

### 強制的に再計算

```python
@do
def smart_fetch(user_id, force_refresh=False):
    cache_key = f"user_{user_id}"
    
    if not force_refresh:
        result = yield Safe(CacheGet(cache_key))
        if result.is_ok():
            return result.ok()
    
    # 再計算
    data = yield fetch_from_api(user_id)
    yield CachePut(cache_key, data, ttl=300)
    return data
```

### 条件付きキャッシュ保存

```python
@do
def conditional_cache(user_id):
    data = yield fetch_user(user_id)
    
    # 成功した場合のみキャッシュ
    if data and data.get("status") == "active":
        yield CachePut(f"user_{user_id}", data, ttl=300)
    
    return data
```

---

## 9.6 キャッシュの無効化

### 手動での無効化

```python
from doeff import CacheInvalidate

@do
def update_user(user_id, new_data):
    # 更新処理
    yield save_to_db(user_id, new_data)
    
    # キャッシュを無効化
    yield CacheInvalidate(f"user_{user_id}")
    
    yield Log(f"User {user_id} updated and cache invalidated")
```

### パターンによる一括無効化

```python
@do
def clear_user_caches():
    # user_ で始まる全てのキャッシュを無効化
    yield CacheInvalidatePattern("user_*")
```

---

## 9.7 実践例

### API レスポンスのキャッシュ

```python
@cache(ttl=60)
@do
def fetch_api_data(endpoint: str):
    yield Log(f"Fetching {endpoint}...")
    response = yield Await(httpx.get(endpoint))
    return response.json()

@do
def dashboard():
    # 全て並列で取得（キャッシュがあれば即座に返る）
    results = yield Gather(
        fetch_api_data("/users"),
        fetch_api_data("/posts"),
        fetch_api_data("/stats")
    )
    return {
        "users": results[0],
        "posts": results[1],
        "stats": results[2]
    }
```

### 計算結果のキャッシュ

```python
@cache(ttl=3600, storage=CacheStorage.DISK)
@do
def heavy_computation(params: dict):
    yield Log(f"Starting heavy computation with {params}")
    
    # 重い計算（数分かかる可能性）
    result = yield compute_expensive_result(params)
    
    yield Log("Computation complete")
    return result
```

### フォールバック付きキャッシュ

```python
@do
def fetch_with_fallback(key):
    # 1. キャッシュを確認
    result = yield Safe(CacheGet(key))
    if result.is_ok():
        return result.ok()
    
    # 2. プライマリソースを試す
    primary = yield Safe(fetch_from_primary(key))
    if primary.is_ok():
        yield CachePut(key, primary.ok(), ttl=300)
        return primary.ok()
    
    # 3. セカンダリソースを試す
    secondary = yield Safe(fetch_from_secondary(key))
    if secondary.is_ok():
        yield CachePut(key, secondary.ok(), ttl=60)  # 短いTTL
        return secondary.ok()
    
    raise Exception("All sources failed")
```

---

## 9.8 ベストプラクティス

### DO

- 適切な TTL を設定する
- キャッシュキーは一意で予測可能に
- 更新時にキャッシュを無効化する
- 重要でないデータのみキャッシュする

### DON'T

- センシティブなデータをキャッシュしない
- TTL を長すぎに設定しない
- キャッシュに依存しすぎない（いつでも再計算できるように）

---

## まとめ

| エフェクト | 用途 |
|-----------|------|
| `CacheGet(key)` | キャッシュから取得 |
| `CachePut(key, value, **policy)` | キャッシュに保存 |
| `CacheInvalidate(key)` | キャッシュを無効化 |
| `@cache` デコレータ | 関数結果の自動キャッシュ |

次の章では、実用的なパターン集を見ていく。

# 第17章: Protocol-Based Injection

## この章で学ぶこと

- 型安全な依存性注入
- Protocol を使った設計
- `@impl` パターン
- 発見可能な実装

---

## 17.1 従来の依存性注入の問題

文字列キーによる注入は型安全ではない。

```python
# 問題: 文字列キーでは型が不明
@do
def process_data(data: Data):
    processor = yield Ask("processor")  # 型がわからない
    return processor(data)
```

問題点:
- IDE補完が効かない
- 実装を見つけにくい
- 型エラーが実行時まで発覚しない

---

## 17.2 Protocol による型定義

Python の `Protocol` を使って注入ポイントを定義する。

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class DataProcessorFn(Protocol):
    """データ処理関数の型定義"""
    def __call__(self, data: Data) -> ProcessedData: ...
```

### Protocol を Ask のキーに使う

```python
@do
def process_data(data: Data):
    # Protocol をキーとして使う
    processor: DataProcessorFn = yield Ask(DataProcessorFn)
    return processor(data)
```

利点:
- 型が明示される
- IDE補完が効く
- 静的型チェックが可能

---

## 17.3 @impl パターン

実装を Protocol にマーキングする。

```python
from doeff import impl

@impl(DataProcessorFn)
def fast_processor(data: Data) -> ProcessedData:
    """高速処理の実装"""
    ...

@impl(DataProcessorFn)
def accurate_processor(data: Data) -> ProcessedData:
    """高精度処理の実装"""
    ...
```

### 発見可能性

```python
# 実装の発見
from doeff import get_impl_protocols, implements

# この関数が実装する Protocol を取得
protocols = get_impl_protocols(fast_processor)
# [DataProcessorFn]

# 特定の Protocol を実装しているか確認
implements(fast_processor, DataProcessorFn)  # True
```

---

## 17.4 環境への注入

```python
# デフォルト環境を定義
default_env = {
    DataProcessorFn: fast_processor,  # Protocol をキーに
}

# 実行時に注入
result = await runtime.run(
    process_data(my_data),
    env=default_env
)

# 別の実装に差し替え
test_env = {
    DataProcessorFn: mock_processor,
}
result = await runtime.run(
    process_data(my_data),
    env=test_env
)
```

---

## 17.5 複数の Protocol

```python
@runtime_checkable
class ValidatorFn(Protocol):
    def __call__(self, data: Data) -> bool: ...

@runtime_checkable
class TransformerFn(Protocol):
    def __call__(self, data: Data) -> Data: ...

@runtime_checkable
class ExporterFn(Protocol):
    def __call__(self, data: Data, path: Path) -> None: ...

# 複数の実装を持つ関数
@impl(ValidatorFn, TransformerFn)
def validate_and_transform(data: Data) -> Data:
    ...
```

---

## 17.6 @do と @impl の組み合わせ

`@impl` は `@do` の外側に置く。

```python
@impl(DataProcessorFn)
@do
def process_with_effects(data: Data) -> EffectGenerator[ProcessedData]:
    yield Log(f"Processing data: {data}")
    result = do_processing(data)
    yield Log(f"Result: {result}")
    return result
```

順序: `@impl` → `@do` → 関数本体

---

## 17.7 戦略の切り替え

### 悪い例: 文字列で分岐

```python
# 悪い: 文字列で分岐
@do
def process(data: Data, strategy: str):
    if strategy == "fast":
        return fast_process(data)
    elif strategy == "accurate":
        return accurate_process(data)
```

### 良い例: Protocol で注入

```python
# 良い: Protocol で注入
@do
def process(data: Data):
    strategy: ProcessStrategyFn = yield Ask(ProcessStrategyFn)
    return strategy(data)

# 環境で切り替え
fast_env = {ProcessStrategyFn: fast_strategy}
accurate_env = {ProcessStrategyFn: accurate_strategy}
```

---

## 17.8 命名規約

| 種類 | 命名パターン | 例 |
|------|-------------|-----|
| Protocol | `*Fn` サフィックス | `ProcessorFn`, `ValidatorFn` |
| 実装 | 説明的な名前 | `fast_processor`, `strict_validator` |
| 環境変数 | `*_env` | `default_env`, `test_env` |

---

## 17.9 実践例

```python
from typing import Protocol, runtime_checkable
from doeff import do, Ask, impl

# Protocol 定義
@runtime_checkable
class ImageLoaderFn(Protocol):
    def __call__(self, path: Path) -> Image: ...

@runtime_checkable
class ImageProcessorFn(Protocol):
    def __call__(self, image: Image) -> Image: ...

@runtime_checkable
class ImageSaverFn(Protocol):
    def __call__(self, image: Image, path: Path) -> None: ...

# 実装
@impl(ImageLoaderFn)
def load_with_pillow(path: Path) -> Image:
    return Image.open(path)

@impl(ImageProcessorFn)
def resize_to_thumbnail(image: Image) -> Image:
    return image.resize((100, 100))

@impl(ImageSaverFn)
def save_as_png(image: Image, path: Path) -> None:
    image.save(path, "PNG")

# パイプライン
@do
def image_pipeline(input_path: Path, output_path: Path):
    loader: ImageLoaderFn = yield Ask(ImageLoaderFn)
    processor: ImageProcessorFn = yield Ask(ImageProcessorFn)
    saver: ImageSaverFn = yield Ask(ImageSaverFn)
    
    image = loader(input_path)
    processed = processor(image)
    saver(processed, output_path)
    
    yield Log(f"Processed {input_path} -> {output_path}")

# 環境
production_env = {
    ImageLoaderFn: load_with_pillow,
    ImageProcessorFn: resize_to_thumbnail,
    ImageSaverFn: save_as_png,
}
```

---

## まとめ

| 概念 | 説明 |
|------|------|
| Protocol | 注入ポイントの型定義 |
| @impl | 実装のマーキング |
| Ask(Protocol) | 型安全な依存性取得 |
| 環境 | 実装のマッピング |

次の章では、recover パターンを見ていく。

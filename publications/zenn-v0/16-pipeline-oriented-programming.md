# 第16章: Pipeline Oriented Programming

## この章で学ぶこと

- パイプライン指向プログラミングとは
- データフロー中心の設計
- Path を避ける理由
- 実践的なパイプライン構築

---

## 16.1 パイプライン指向プログラミングとは

データを変換のパイプラインとして捉える設計手法。

```
入力データ → 処理1 → 処理2 → 処理3 → 出力データ
```

### 従来のアプローチ

```python
# 悪い例: モノリシックで再利用困難
def do_everything(input_path: Path, output_path: Path, config: dict):
    # データ読み込み
    data = load_from_file(input_path)
    
    # 処理1
    if config["mode"] == "A":
        data = process_a(data)
    else:
        data = process_b(data)
    
    # 処理2
    data = transform(data)
    
    # 書き出し
    save_to_file(data, output_path)
```

問題:
- テストが困難（ファイルI/Oが必要）
- 再利用できない
- 設定で挙動が変わる
- 中間結果を検査できない

### パイプライン指向

```python
# 良い例: 分解された純粋な処理
@do
def load_data(path: Path):
    """データ読み込み（ここだけ Path を受け取る）"""
    ...

@do
def process_a(data: Data):
    """処理A（純粋なデータ変換）"""
    ...

@do
def process_b(data: Data):
    """処理B（純粋なデータ変換）"""
    ...

@do
def transform(data: Data):
    """変換（純粋なデータ変換）"""
    ...

# パイプラインを構築
p_data: Program[Data] = load_data(Path("input.json"))
p_processed: Program[Data] = process_a(p_data)
p_transformed: Program[Data] = transform(p_processed)
```

---

## 16.2 Path を避ける

### なぜ Path を避けるか

| Path を使う | データを渡す |
|-------------|-------------|
| テストにファイルが必要 | メモリ内でテスト可能 |
| 再利用困難 | 任意のソースから使える |
| 中間結果が見えない | 各段階を検査可能 |
| 暗黙の依存関係 | 明示的なデータフロー |

### ルール

1. **Path を受け取るのはデータ読み込み関数だけ**
2. **処理関数はデータを受け取りデータを返す**
3. **Path を書き出すのは最終段階だけ**

```python
# データ読み込み: Path OK
@do
def load_images(directory: Path) -> EffectGenerator[list[Image]]:
    ...

# データ処理: Path NG, データのみ
@do
def resize_images(images: list[Image], size: tuple[int, int]) -> EffectGenerator[list[Image]]:
    ...

# データ書き出し: Path OK
@do
def save_images(images: list[Image], output_dir: Path) -> EffectGenerator[None]:
    ...
```

---

## 16.3 Program を定数として定義

パイプラインの各段階を `Program` 変数として定義する。

```python
# 中間変数としてパイプラインを構築
p_raw_data: Program[list[Data]] = load_data(Path("data/input.json"))
p_validated: Program[list[Data]] = validate(p_raw_data)
p_processed: Program[list[Data]] = process(p_validated)
p_enriched: Program[list[Data]] = enrich(p_processed)

# 各段階を個別に実行・検査可能
result = await runtime.run(p_validated)  # バリデーション結果を確認
```

### 複数のバリエーション

```python
# 同じ処理関数で異なるデータセット
p_small_test: Program[list[Data]] = process(load_data(Path("test/small.json")))
p_large_test: Program[list[Data]] = process(load_data(Path("test/large.json")))
p_production: Program[list[Data]] = process(load_data(Path("data/prod.json")))
```

---

## 16.4 遅延読み込み

データ読み込みを `Program` でラップすることで遅延評価を実現。

```python
@do
def load_image(path: Path) -> EffectGenerator[Image]:
    """画像の遅延読み込み"""
    yield Log(f"Loading image: {path}")
    return Image.open(path)

# この時点では読み込まれない
p_image: Program[Image] = load_image(Path("large_image.png"))

# 処理関数は Image を受け取る（どう読み込まれたかは知らない）
@do
def resize(image: Image, size: tuple[int, int]) -> EffectGenerator[Image]:
    return image.resize(size)

# パイプラインを構築（まだ実行されない）
p_resized: Program[Image] = resize(p_image, (100, 100))

# ここで初めて読み込みと処理が実行される
result = await runtime.run(p_resized)
```

---

## 16.5 設定の扱い

### 悪い例: 設定で挙動を変える

```python
# 悪い: 設定で分岐
@do
def process_data(data: Data, mode: str):
    if mode == "fast":
        return fast_process(data)
    elif mode == "accurate":
        return accurate_process(data)
```

### 良い例: 別の関数として定義

```python
# 良い: 別の関数として定義
@do
def fast_process(data: Data):
    ...

@do
def accurate_process(data: Data):
    ...

# 使う側で選択
p_result_fast = fast_process(p_data)
p_result_accurate = accurate_process(p_data)
```

### 定数の扱い

設定値はパイプライン構築時に固定する。

```python
@do
def filter_by_threshold(data: list[Data], threshold: float):
    return [d for d in data if d.score >= threshold]

# 閾値をパイプライン定義時に固定
p_filtered_strict: Program = filter_by_threshold(p_data, threshold=0.9)
p_filtered_loose: Program = filter_by_threshold(p_data, threshold=0.5)
```

---

## 16.6 実践例: 画像処理パイプライン

```python
from pathlib import Path
from doeff import do, Program

# データ読み込み
@do
def load_images(directory: Path):
    yield Log(f"Loading images from {directory}")
    paths = list(directory.glob("*.png"))
    images = [Image.open(p) for p in paths]
    return images

# 処理関数（純粋）
@do
def resize_images(images: list[Image], size: tuple[int, int]):
    yield Log(f"Resizing {len(images)} images to {size}")
    return [img.resize(size) for img in images]

@do
def apply_filter(images: list[Image], filter_type: str):
    yield Log(f"Applying {filter_type} filter")
    return [apply_filter_to_image(img, filter_type) for img in images]

@do
def compress_images(images: list[Image], quality: int):
    yield Log(f"Compressing with quality {quality}")
    return [compress(img, quality) for img in images]

# パイプライン定義
p_raw: Program[list[Image]] = load_images(Path("input/"))
p_resized: Program[list[Image]] = resize_images(p_raw, (800, 600))
p_filtered: Program[list[Image]] = apply_filter(p_resized, "sharpen")
p_compressed: Program[list[Image]] = compress_images(p_filtered, quality=85)

# 出力
@do
def save_images(images: list[Image], output_dir: Path):
    yield Log(f"Saving {len(images)} images to {output_dir}")
    output_dir.mkdir(exist_ok=True)
    for i, img in enumerate(images):
        img.save(output_dir / f"output_{i}.png")

# 完全なパイプライン
p_complete: Program[None] = save_images(p_compressed, Path("output/"))
```

---

## 16.7 ベストプラクティス

### DO

- データ読み込みと処理を分離する
- 処理関数は純粋に（データ in → データ out）
- `Program` 変数として中間結果を定義する
- 設定値はパイプライン定義時に固定する

### DON'T

- 処理関数内で Path を直接扱わない
- 設定引数で挙動を分岐させない
- モノリシックな「全部入り」関数を作らない

---

## まとめ

| 原則 | 説明 |
|------|------|
| データフロー中心 | Path ではなくデータを渡す |
| 純粋な処理関数 | 入力データから出力データへの変換 |
| 分離された I/O | 読み込みと書き出しは端にだけ |
| Program で遅延評価 | 必要になるまで実行しない |
| 定数としてのパイプライン | 各段階を `Program` 変数として定義 |

次の章では、Protocol-Based Injection を見ていく。

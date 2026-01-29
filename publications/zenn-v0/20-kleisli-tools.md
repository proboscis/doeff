# 第20章: Kleisli Tools と transform

## この章で学ぶこと

- Kleisli Tool システム
- IDE連携
- transform による後処理チェーン
- 実践的なツール作成

---

## 20.1 Kleisli Tool とは

KleisliProgram を後処理「ツール」として公開する仕組み。

```python
@do
def visualize_dataframe(  # doeff: kleisli
    df: pd.DataFrame,
    max_rows: int = 100
):
    """DataFrameを可視化する後処理ツール"""
    yield Log(f"Visualizing {len(df)} rows")
    # 可視化処理...
```

`# doeff: kleisli` マーカーにより、IDE プラグインがツールとして認識する。

---

## 20.2 ツールの使い方

1. `Program[pd.DataFrame]` を実行
2. IDE のメニューから Kleisli Tool を選択
3. ツールが自動的に結果を処理

```python
# エントリポイント
p_analysis_result: Program[pd.DataFrame] = run_analysis()

# IDE から visualize_dataframe を選択すると
# visualize_dataframe(p_analysis_result) が実行される
```

---

## 20.3 ツールの設計

### 基本パターン

```python
@do
def tool_name(  # doeff: kleisli
    target: T,  # 第一引数: 処理対象
    *,  # キーワード引数のみ
    option1: str = "default",
    option2: int = 10
):
    """ツールの説明"""
    # 処理...
    return None
```

### 実例: エラービューア

```python
@do
def launch_error_viewer(  # doeff: kleisli
    errors_df: pd.DataFrame,
    *,
    port: int = 7860
):
    """エラー一覧をGradioで可視化"""
    yield Log(f"Launching error viewer on port {port}")
    
    # DataFrameを加工
    enriched = yield enrich_error_data(errors_df)
    
    # Gradioアプリを起動
    yield launch_gradio_app(enriched, port=port)
```

---

## 20.4 Transform マーカー

`# doeff: transform` は `Program -> Program` 変換を定義する。

```python
@do
def add_timing(  # doeff: transform
    program: Program[T],
) -> EffectGenerator[T]:
    """実行時間を計測"""
    start = yield GetTime()
    result = yield program
    end = yield GetTime()
    
    yield slog(
        msg="Execution time",
        duration_seconds=(end - start).total_seconds()
    )
    
    return result
```

---

## 20.5 Transform の連鎖

複数の transform を連鎖できる。

```python
# コマンドラインで連鎖
# doeff run --program some.module.p_data \
#           --transform module.add_timing \
#           --transform module.add_logging
```

連鎖の順序: 左から右に適用

```
original_program
  → add_timing(original_program)
  → add_logging(add_timing(original_program))
```

---

## 20.6 実践例: 分析パイプライン

```python
# エントリポイント
p_raw_data: Program[pd.DataFrame] = load_data()
p_analyzed: Program[pd.DataFrame] = analyze(p_raw_data)
p_summarized: Program[pd.DataFrame] = summarize(p_analyzed)

# Transform
@do
def export_to_excel(  # doeff: transform
    program: Program[pd.DataFrame],
    output_path: Path = Path("output.xlsx")
) -> EffectGenerator[pd.DataFrame]:
    """結果をExcelに出力"""
    df = yield program
    df.to_excel(output_path)
    yield slog(msg="Exported to Excel", path=str(output_path))
    return df

# Kleisli Tool
@do
def interactive_explorer(  # doeff: kleisli
    df: pd.DataFrame,
    *,
    sample_size: int = 1000
):
    """インタラクティブなデータ探索"""
    sample = df.sample(min(sample_size, len(df)))
    yield launch_explorer(sample)
```

---

## 20.7 ベストプラクティス

### Kleisli Tool

- 第一引数は処理対象
- 追加オプションはキーワード引数のみ
- デフォルト値を設定
- 戻り値は `None`（副作用のみ）

### Transform

- 入力は `Program[T]`
- 出力は `Program[T]` または `Program[U]`
- 元のプログラムの結果を変更しないことが多い
- ログ追加、計測、キャプチャなどの横断的関心事に使用

---

## まとめ

| マーカー | 用途 |
|---------|------|
| `# doeff: kleisli` | 後処理ツール |
| `# doeff: transform` | Program変換 |

次の章では、他の言語との比較を見ていく。

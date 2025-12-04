---
id: TASK-LINTER-011
title: DOEFF026 no-monolithic-function ルール実装
module: linter
status: todo
priority: low
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff026]
---

# TASK-LINTER-011 — DOEFF026 no-monolithic-function ルール実装

## Description

データ読込と処理を1つの関数に詰め込むモノリシックなパターンを検出するルール。読込・処理・書込は分離すべき。

## Acceptance Criteria

- [ ] Path 引数を持ち、かつファイル書込みを行う関数を検出
- [ ] 関数内に `open(..., "w")` と `Path` 引数がある場合に警告
- [ ] 純粋な処理関数は許可
- [ ] ユニットテスト 3 件以上

## Implementation Notes

### Detection Logic

```rust
// FunctionDef で:
// 1. 引数に Path がある (入力)
// 2. body 内に open(..., "w") または Path.write_* がある (出力)
// 3. body が長い (処理を含む)
fn is_monolithic(func: &StmtFunctionDef) -> bool {
    let has_path_input = func.args.args.iter()
        .any(|a| is_path_annotation(&a.annotation));
    let has_file_write = contains_file_write(&func.body);
    let is_long = count_statements(&func.body) > 10;
    
    has_path_input && has_file_write && is_long
}
```

### Error Message

```
DOEFF026: Function '{func}' appears to be a monolithic function that
combines data loading, processing, and writing.

Problem: Monolithic functions that take input paths and write to output
paths are hard to test, reuse, and compose.

Fix: Split into separate functions:
  # Before
  @do
  def do_everything(input_path: Path, output_path: Path):
      data = load(input_path)
      processed = transform(data)
      write(processed, output_path)

  # After
  @do
  def load_data(path: Path) -> Data: ...
  
  @do
  def process(data: Data) -> ProcessedData: ...
  
  @do
  def export(data: ProcessedData, path: Path): ...
  
  # Compose as pipeline
  p_pipeline = export(
      data=process(data=load_data(path=Path("in.json"))),
      path=Path("out.json")
  )
```

## Subtasks

- [ ] `doeff026_no_monolithic_function.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF026.md ドキュメント作成
- [ ] cargo test 実行

## Notes

- ヒューリスティックな検出のため、精度に限界あり
- Severity は Info レベルを推奨

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- 関連: DOEFF025 (no-path-intermediate)

## Progress Log

### 2025-12-04
- タスク作成



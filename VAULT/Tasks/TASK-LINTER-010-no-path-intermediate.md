---
id: TASK-LINTER-010
title: DOEFF025 no-path-intermediate ルール実装
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
tags: [task, linter, doeff025]
---

# TASK-LINTER-010 — DOEFF025 no-path-intermediate ルール実装

## Description

関数の引数に `Path` 型を使用することを検出し、データを直接渡すパイプライン指向を推奨するルール。データ読込関数と最終エクスポート関数以外では Path を避けるべき。

## Acceptance Criteria

- [ ] `def process(path: Path)` を検出して警告
- [ ] `def load_data(path: Path)` は許可（読込関数）
- [ ] `def export(data, dst_path: Path)` は許可（書込関数）
- [ ] `src_path`, `input_path` などの読込パス引数は警告
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// FunctionDef の引数で Path 型を検出
// 関数名や引数名で読込/書込みかを判定
const LOAD_PATTERNS: &[&str] = &["load_", "read_", "fetch_", "download_"];
const EXPORT_PATTERNS: &[&str] = &["export_", "write_", "save_", "upload_"];
const DST_PARAM_PATTERNS: &[&str] = &["dst_", "dest_", "output_", "out_"];

fn should_allow_path(func_name: &str, param_name: &str) -> bool {
    // Allow in load functions
    if LOAD_PATTERNS.iter().any(|p| func_name.starts_with(p)) {
        return true;
    }
    // Allow dst_path in export functions
    if EXPORT_PATTERNS.iter().any(|p| func_name.starts_with(p)) {
        if DST_PARAM_PATTERNS.iter().any(|p| param_name.starts_with(p)) {
            return true;
        }
    }
    false
}
```

### Error Message

```
DOEFF025: Function '{func}' has Path parameter '{param}'.

Problem: Passing paths between functions creates tight coupling to the
filesystem. Pipeline-oriented code should pass data directly, with I/O
isolated at the boundaries (load at start, export at end).

Fix: Accept the data directly instead of a path:
  # Before
  def process(input_path: Path) -> Result:
      data = load(input_path)
      ...

  # After
  def process(data: Data) -> Result:
      ...
  
  # Compose at entrypoint level
  p_result = process(data=load_data(path=Path("input.json")))
```

## Subtasks

- [ ] `doeff025_no_path_intermediate.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF025.md ドキュメント作成
- [ ] cargo test 実行

## Notes

- False positive が多い可能性あり
- Severity を Info にして、明らかな違反のみ Warning にすることを検討

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



---
id: TASK-LINTER-012
title: DOEFF027 no-side-effect-in-processing ルール実装
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
tags: [task, linter, doeff027]
---

# TASK-LINTER-012 — DOEFF027 no-side-effect-in-processing ルール実装

## Description

純粋であるべきデータ処理関数が副作用（ファイルI/O、ネットワーク）を持つことを検出するルール。

## Acceptance Criteria

- [ ] `process_*`, `transform_*`, `compute_*` 関数内の I/O を検出
- [ ] `open()` 呼び出しを検出
- [ ] `requests.get()` などのネットワーク呼び出しを検出
- [ ] `load_*`, `save_*`, `export_*` 関数は除外
- [ ] ユニットテスト 3 件以上

## Implementation Notes

### Detection Logic

```rust
const PURE_FUNCTION_PATTERNS: &[&str] = &[
    "process_", "transform_", "compute_", "calculate_",
    "parse_", "convert_", "format_", "build_", "create_",
];

const IO_FUNCTIONS: &[&str] = &[
    "open", "read", "write", "requests.get", "requests.post",
    "urllib", "httpx", "aiohttp",
];

fn is_pure_function_name(name: &str) -> bool {
    PURE_FUNCTION_PATTERNS.iter().any(|p| name.starts_with(p))
}

fn contains_io_call(stmts: &[Stmt]) -> bool {
    // Check for IO function calls
}
```

### Error Message

```
DOEFF027: Pure function '{func}' contains I/O operations.

Problem: Functions named 'process_*', 'transform_*', etc. are expected to
be pure data transformations. I/O operations make them hard to test and
reason about.

Fix: Separate I/O from processing:
  # Before
  def process_data(input_path: Path) -> Result:
      with open(input_path) as f:
          data = json.load(f)
      return transform(data)

  # After
  def process_data(data: Data) -> Result:
      return transform(data)
  
  # I/O at boundaries
  data = load_json(path)
  result = process_data(data)
```

## Subtasks

- [ ] `doeff027_no_side_effect_in_processing.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF027.md ドキュメント作成
- [ ] cargo test 実行

## Notes

- 文脈依存のため誤検出の可能性あり
- Severity は Info レベルを推奨

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



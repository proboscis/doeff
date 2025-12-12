---
id: TASK-LINTER-005
title: DOEFF020 program-naming-convention ルール実装
module: linter
status: done
priority: medium
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff020]
---

# TASK-LINTER-005 — DOEFF020 program-naming-convention ルール実装

## Description

`Program` 型の変数は `p_` プレフィックスを使用すべき。`*_program` サフィックスは非推奨。

## Acceptance Criteria

- [x] `data_program: Program = ...` を検出して警告
- [x] `some_program: Program[T] = ...` を検出して警告
- [x] `p_data: Program = ...` は許可
- [x] `p_result: Program[T] = ...` は許可
- [x] ユニットテスト 5 件以上 (10件実装済み)

## Implementation Notes

### Detection Logic

```rust
// AnnAssign で Program 型の変数名を検査
fn check_program_naming(ann_assign: &StmtAnnAssign) -> Option<Violation> {
    if !is_program_type(&ann_assign.annotation) {
        return None;
    }
    
    let name = get_name(&ann_assign.target);
    
    // p_ プレフィックスで始まればOK
    if name.starts_with("p_") {
        return None;
    }
    
    // _program サフィックスは非推奨
    if name.ends_with("_program") {
        return Some(violation_with_suggestion(name));
    }
    
    // その他の命名も警告（ただしInfoレベル）
    Some(violation_info(name))
}
```

### Error Message

```
DOEFF020: Program variable '{name}' should use 'p_' prefix.

Naming convention: Program type variables should be named with 'p_' prefix
instead of '_program' suffix for consistency and brevity.

Fix: Rename the variable:
  # Before
  data_program: Program[Data] = load_data(path=Path("data.json"))
  
  # After
  p_data: Program[Data] = load_data(path=Path("data.json"))
```

## Subtasks

- [x] `doeff020_program_naming_convention.rs` 作成
- [x] mod.rs に登録
- [x] DOEFF020.md ドキュメント作成
- [x] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成
- DOEFF020 ルール実装完了
  - `doeff020_program_naming_convention.rs` を作成
  - mod.rs に登録
  - DOEFF020.md ドキュメント作成
  - テスト 10 件全てパス



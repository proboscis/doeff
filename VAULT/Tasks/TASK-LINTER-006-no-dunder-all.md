---
id: TASK-LINTER-006
title: DOEFF021 no-dunder-all ルール実装
module: linter
status: todo
priority: medium
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff021]
---

# TASK-LINTER-006 — DOEFF021 no-dunder-all ルール実装

## Description

`__all__` の使用を禁止するルール。デフォルトで全てエクスポートする方針。

## Acceptance Criteria

- [ ] `__all__ = [...]` を検出してエラー
- [ ] `__all__: list = [...]` を検出してエラー
- [ ] `__all__ += [...]` を検出してエラー
- [ ] ユニットテスト 3 件以上

## Implementation Notes

### Detection Logic

```rust
// Assign または AnnAssign で target が __all__ を検出
fn check_dunder_all(stmt: &Stmt) -> Option<Violation> {
    match stmt {
        Stmt::Assign(assign) => {
            for target in &assign.targets {
                if is_name_eq(target, "__all__") {
                    return Some(violation());
                }
            }
        }
        Stmt::AnnAssign(ann) => {
            if is_name_eq(&ann.target, "__all__") {
                return Some(violation());
            }
        }
        Stmt::AugAssign(aug) => {
            if is_name_eq(&aug.target, "__all__") {
                return Some(violation());
            }
        }
        _ => {}
    }
    None
}
```

### Error Message

```
DOEFF021: '__all__' should not be used.

Policy: This project defaults to exporting everything from modules.
Using __all__ restricts exports and goes against the project convention.

Fix: Remove the __all__ declaration. If you need to limit exports for a
specific reason, add a comment explaining why and use # noqa: DOEFF021.
```

## Subtasks

- [ ] `doeff021_no_dunder_all.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF021.md ドキュメント作成
- [ ] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



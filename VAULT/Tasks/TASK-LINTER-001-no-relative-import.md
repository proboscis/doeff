---
id: TASK-LINTER-001
title: DOEFF016 no-relative-import ルール実装
module: linter
status: done
priority: high
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/doeff016_no_relative_import.rs
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff016]
---

# TASK-LINTER-001 — DOEFF016 no-relative-import ルール実装

## Description

相対インポート (`from .module import ...`) を禁止するリンタールールを実装する。

## Acceptance Criteria

- [x] `from .module import x` を検出してエラー
- [x] `from ..parent import x` を検出してエラー
- [x] `from package.module import x` は許可
- [x] 違反メッセージに絶対インポートへの修正例を含む
- [x] ユニットテスト 5 件以上（10件実装）

## Implementation Notes

### Detection Logic

```rust
// Stmt::ImportFrom で level > 0 を検出
if let Stmt::ImportFrom(import) = stmt {
    let level: u32 = import.level.as_ref().map(|l| l.to_u32()).unwrap_or(0);
    if level > 0 {
        // violation
    }
}
```

### Error Message

```
DOEFF016: Relative import detected: 'from .{module} import ...'

Problem: Relative imports make code harder to move and refactor.

Fix: Use absolute import instead:
  from <package>.<module> import ...
```

## Subtasks

- [x] `doeff016_no_relative_import.rs` 作成
- [x] mod.rs に登録
- [x] DOEFF016.md ドキュメント作成
- [x] cargo test 実行（全216テストパス）

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成
- ルール実装完了（10件のユニットテスト）
- ドキュメント作成
- mod.rs に登録
- 全テストパス確認



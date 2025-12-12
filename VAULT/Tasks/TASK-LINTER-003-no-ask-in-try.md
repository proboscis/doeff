---
id: TASK-LINTER-003
title: DOEFF018 no-ask-in-try ルール実装
module: linter
status: done
priority: high
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff018]
---

# TASK-LINTER-003 — DOEFF018 no-ask-in-try ルール実装

## Description

`ask` effect を try/except で囲むことを禁止するルール。DI の失敗はプログラミングエラーであり、実行時にキャッチして処理すべきではない。

## Acceptance Criteria

- [x] try ブロック内の `yield ask(...)` を検出してエラー
- [x] try ブロック外の `yield ask(...)` は許可
- [x] ネストした try も検出
- [x] ユニットテスト 5 件以上 (10件実装)

## Implementation Notes

### Detection Logic

```rust
// Stmt::Try を走査
// body 内の Expr::Yield で func が ask かチェック
fn check_try_for_ask(try_stmt: &StmtTry) -> Vec<Violation> {
    let mut violations = vec![];
    for stmt in &try_stmt.body {
        if contains_ask_yield(stmt) {
            violations.push(...);
        }
    }
    violations
}
```

### Error Message

```
DOEFF018: 'ask' effect is used inside a try/except block.

Problem: ask effect failures indicate a programming error (missing dependency injection).
These should never be caught at runtime - fix the DI configuration instead.

Fix: Remove the try/except and ensure the dependency is properly injected:
  # Before
  try:
      value = yield ask("config_key")
  except:
      value = "default"
  
  # After
  value = yield ask("config_key")  # Ensure DI provides this value
```

## Subtasks

- [x] `doeff018_no_ask_in_try.rs` 作成
- [x] mod.rs に登録
- [x] DOEFF018.md ドキュメント作成
- [x] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- 関連: DOEFF014 (no-try-except)

## Progress Log

### 2025-12-04
- タスク作成
- DOEFF018 ルール実装完了
  - `packages/doeff-linter/src/rules/doeff018_no_ask_in_try.rs` 作成
  - `mod.rs` に登録 (17個目のルール)
  - `packages/doeff-linter/docs/rules/DOEFF018.md` ドキュメント作成
  - 10件のユニットテストすべてパス



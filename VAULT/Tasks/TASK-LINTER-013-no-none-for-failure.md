---
id: TASK-LINTER-013
title: DOEFF028 no-none-for-failure ルール実装
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
tags: [task, linter, doeff028]
---

# TASK-LINTER-013 — DOEFF028 no-none-for-failure ルール実装

## Description

失敗を `None` で表現することを検出し、例外を raise することを推奨するルール。`None` は「結果がない」という正当なケースにのみ使用すべき。

## Acceptance Criteria

- [ ] `return None` を try/except 内で使用している場合に警告
- [ ] `find_*`, `get_*` 関数で `return None` がある場合に警告
- [ ] `Optional[T]` 戻り値型と組み合わせて検出
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// パターン1: try/except 内で return None
// パターン2: find_*/get_* 関数で except 後に return None
// パターン3: if not found: return None パターン

fn is_failure_none_return(func: &StmtFunctionDef) -> Vec<Violation> {
    let mut violations = vec![];
    
    // Check for try/except with return None in except
    for stmt in &func.body {
        if let Stmt::Try(try_stmt) = stmt {
            for handler in &try_stmt.handlers {
                if contains_return_none(&handler.body) {
                    violations.push(...);
                }
            }
        }
    }
    
    violations
}
```

### Error Message

```
DOEFF028: Function '{func}' returns None to indicate failure.

Problem: Returning None for failures pollutes the return type and forces
callers to check for None. Use exceptions for errors and the 'recover'
effect to handle them.

Fix: Raise an exception and use 'recover' effect at call site:
  # Before
  @do
  def find_user(user_id: str) -> User | None:
      try:
          return db.get_user(user_id)
      except NotFoundError:
          return None  # Bad: caller must check for None

  # After
  @do
  def find_user(user_id: str) -> User:
      user = db.get_user(user_id)
      if user is None:
          raise ValueError(f"User not found: {user_id}")
      return user

  # Caller uses recover effect
  user = yield recover(find_user(user_id), fallback=default_user)
```

## Subtasks

- [ ] `doeff028_no_none_for_failure.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF028.md ドキュメント作成
- [ ] cargo test 実行

## Notes

- `Optional[T]` が正当なケース（オプショナル検索など）との区別が難しい
- 関数名パターンと try/except パターンの組み合わせで精度向上

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- 関連: DOEFF014 (no-try-except)

## Progress Log

### 2025-12-04
- タスク作成



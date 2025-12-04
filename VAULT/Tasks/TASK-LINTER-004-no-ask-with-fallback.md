---
id: TASK-LINTER-004
title: DOEFF019 no-ask-with-fallback ルール実装
module: linter
status: todo
priority: high
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff019]
---

# TASK-LINTER-004 — DOEFF019 no-ask-with-fallback ルール実装

## Description

`ask` effect とデフォルト引数の併用パターン (`arg or (yield ask(...))`) を禁止するルール。`ask` を使う場合は唯一の取得方法とすべき。

## Acceptance Criteria

- [ ] `arg = arg or (yield ask(...))` パターンを検出
- [ ] `arg if arg else (yield ask(...))` パターンを検出
- [ ] `arg = yield ask(...)` 単独は許可
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// BoolOp (Or) で right が Yield(ask(...)) を検出
// IfExp で orelse が Yield(ask(...)) を検出
fn is_ask_fallback_pattern(expr: &Expr) -> bool {
    match expr {
        Expr::BoolOp(boolop) if boolop.op == BoolOp::Or => {
            boolop.values.iter().any(|v| is_yield_ask(v))
        }
        Expr::IfExp(ifexp) => {
            is_yield_ask(&ifexp.orelse)
        }
        _ => false
    }
}
```

### Error Message

```
DOEFF019: 'ask' effect is used with a fallback pattern.

Problem: Using 'arg or (yield ask(...))' or default argument + ask creates
ambiguity about where the value comes from. ask should be the ONLY way to
obtain the value to reduce complexity.

Fix: Remove the fallback and use ask as the sole source:
  # Before
  @do
  def do_something(arg=None):
      arg = arg or (yield ask("arg_key"))
  
  # After
  @do
  def do_something():
      arg = yield ask("arg_key")  # Single source of truth
```

## Subtasks

- [ ] `doeff019_no_ask_with_fallback.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF019.md ドキュメント作成
- [ ] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



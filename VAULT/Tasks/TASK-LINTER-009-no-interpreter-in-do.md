---
id: TASK-LINTER-009
title: DOEFF024 no-interpreter-in-do ルール実装
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
tags: [task, linter, doeff024]
---

# TASK-LINTER-009 — DOEFF024 no-interpreter-in-do ルール実装

## Description

`@do` 関数内で interpreter を使用して Program を実行することを禁止するルール。テストコード以外では Program は yield で合成すべき。

## Acceptance Criteria

- [ ] `interpreter.run(program)` を検出してエラー
- [ ] `interpreter.run_sync(program)` を検出してエラー
- [ ] `ProgramInterpreter().run(...)` を検出してエラー
- [ ] `tests/` ディレクトリ内のファイルは除外
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// Call で func が Attribute(attr="run" or "run_sync") を検出
// value が "interpreter" または "*Interpreter" パターンか確認
fn is_interpreter_run_call(call: &ExprCall) -> bool {
    if let Expr::Attribute(attr) = &*call.func {
        if matches!(attr.attr.as_str(), "run" | "run_sync") {
            // Check if it's an interpreter
            return is_interpreter_expr(&attr.value);
        }
    }
    false
}
```

### Error Message

```
DOEFF024: Interpreter is used inside a function to run a Program.

Problem: Using interpreter.run() inside functions breaks composability.
If a Program needs to be run inside another function, that function should
be a @do function that yields the Program.

Fix: Convert to a @do function and yield the Program:
  # Before
  def process_data(interpreter):
      result = interpreter.run(load_program)
      ...

  # After
  @do
  def process_data():
      result = yield load_program
      ...
```

## Subtasks

- [ ] `doeff024_no_interpreter_in_do.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF024.md ドキュメント作成
- [ ] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



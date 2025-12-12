---
id: TASK-LINTER-002
title: DOEFF017 no-program-type-param ルール実装
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
tags: [task, linter, doeff017]
---

# TASK-LINTER-002 — DOEFF017 no-program-type-param ルール実装

## Description

`@do` 関数の引数に `Program[T]` 型を使用することを禁止するルール。doeff は自動的に `Program[T]` を解決するため、引数は素の `T` 型で受けるべき。

## Acceptance Criteria

- [x] `def f(data: Program[T])` を検出してエラー
- [x] `def f(data: Program)` を検出してエラー
- [x] `def f(data: T)` は許可
- [x] `@do` デコレータ付き関数のみを対象
- [x] ユニットテスト 5 件以上 (12件実装)

## Implementation Notes

### Detection Logic

```rust
// FunctionDef で @do デコレータがある場合
// 引数の型アノテーションに Program が含まれるか検査
fn is_program_annotation(expr: &Expr) -> bool {
    match expr {
        Expr::Name(name) => name.id == "Program",
        Expr::Subscript(sub) => {
            if let Expr::Name(name) = &*sub.value {
                name.id == "Program"
            } else { false }
        }
        _ => false
    }
}
```

### Error Message

```
DOEFF017: Function parameter '{param}' has type 'Program[{T}]'.

Problem: @do functions should accept the underlying type T, not Program[T].
doeff automatically resolves Program[T] arguments before executing the function body.

Fix: Change the parameter type from 'Program[{T}]' to '{T}':
  # Before
  def process(data: Program[DataFrame]) -> EffectGenerator[Result]: ...
  
  # After
  def process(data: DataFrame) -> EffectGenerator[Result]: ...
```

## Subtasks

- [x] `doeff017_no_program_type_param.rs` 作成
- [x] mod.rs に登録
- [x] DOEFF017.md ドキュメント作成
- [x] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成
- `doeff017_no_program_type_param.rs` 実装完了
  - `@do` デコレータの検出
  - 関数引数の `Program[T]` 型検出
  - Union型 (`Program[T] | None`) の対応
  - async関数対応
  - keyword-only引数対応
- mod.rs にルール登録
- DOEFF017.md ドキュメント作成
- cargo test 全12テストパス
- タスク完了



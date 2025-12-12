---
id: TASK-LINTER-015
title: DOEFF022 prefer-do-function ルール実装
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
tags: [task, linter, doeff022, do-decorator]
---

# TASK-LINTER-015 — DOEFF022 prefer-do-function ルール実装

## Description

`@do` デコレータを使用していない関数を検出し、可能な限り `@do` 関数を使用することを推奨するルール。
また、`yield slog` を使用してログを残すことを推奨する。

## Motivation

doeffプロジェクトでは、Pipeline Oriented Programmingパラダイムに従い、関数は可能な限り `@do` デコレータを使用して定義すべき。これにより：

1. **Effect Tracking**: 副作用が明示的に追跡可能
2. **Structured Logging**: `yield slog` でログを残せる
3. **Composability**: Program同士の合成が容易
4. **Testability**: モック/スタブが容易

## Acceptance Criteria

- [ ] `@do` デコレータなしの関数定義を検出
- [ ] 特殊メソッド (`__init__`, `__str__` など) は除外
- [ ] `@property`, `@staticmethod`, `@classmethod` デコレータ付きは除外
- [ ] test_ で始まる関数は除外
- [ ] 修正方法として `@do` と `yield slog` の使用を提案
- [ ] `# noqa: DOEFF022` での抑制を案内
- [ ] Severity は Info (強制ではなく推奨)
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
fn should_skip(func_name: &str, decorators: &[Expr]) -> bool {
    // Dunder methods
    if func_name.starts_with("__") && func_name.ends_with("__") {
        return true;
    }
    
    // Test functions
    if func_name.starts_with("test_") {
        return true;
    }
    
    // Property/staticmethod/classmethod
    for dec in decorators {
        if matches decorator name "property" | "staticmethod" | "classmethod" {
            return true;
        }
    }
    
    false
}

fn has_do_decorator(decorators: &[Expr]) -> bool {
    // Check for @do or @do()
}
```

### Error Message

```
DOEFF022: Function '{}' is not decorated with @do.

Recommendation: Consider using @do decorator to enable structured effects:
  - Effect tracking for IO, async, and side effects
  - Structured logging with `yield slog("message", key=value)`
  - Composition with other Program functions

Fix: Add @do decorator:
  # Before
  def process_data(data: Data) -> Result:
      ...
  
  # After
  @do
  def process_data(data: Data) -> EffectGenerator[Result]:
      yield slog("Processing data", count=len(data))
      ...

If this function intentionally doesn't use doeff effects, suppress with: # noqa: DOEFF022
```

## Subtasks

- [x] `doeff022_prefer_do_function.rs` 作成
- [x] mod.rs に登録
- [x] main.rs に RuleInfo 追加
- [x] DOEFF022.md ドキュメント作成
- [x] cargo test 実行 (18 tests passed)

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- DOEFF017: @do関数でのProgram[T]パラメータ検出（関連）
- DOEFF009: 戻り値型注釈の欠落（関連）

## Progress Log

### 2025-12-04
- タスク作成
- 実装開始
- DOEFF022 ルール実装完了
- 18件のユニットテスト全て通過



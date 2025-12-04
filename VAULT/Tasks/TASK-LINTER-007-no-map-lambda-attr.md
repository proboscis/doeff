---
id: TASK-LINTER-007
title: DOEFF022 no-map-lambda-attr ルール実装
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
tags: [task, linter, doeff022]
---

# TASK-LINTER-007 — DOEFF022 no-map-lambda-attr ルール実装

## Description

`Program.map(lambda x: x.attr)` パターンを検出し、`Program.attr` プロキシの使用を推奨するルール。

## Acceptance Criteria

- [ ] `p.map(lambda x: x.attr)` を検出して警告
- [ ] `p.map(lambda x: x[0])` を検出して警告
- [ ] `p.map(lambda x: x.method())` を検出して警告
- [ ] `p.map(lambda x: complex_expr(x))` は許可（プロキシ不可）
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// Call で func が Attribute(value=*, attr="map") を検出
// 引数が Lambda で body が単純な属性アクセス/添字/呼び出しか判定
fn is_simple_lambda_attr(lambda: &ExprLambda) -> Option<String> {
    match &*lambda.body {
        // lambda x: x.attr
        Expr::Attribute(attr) => {
            if is_same_name(&attr.value, &lambda.args[0]) {
                return Some(format!(".{}", attr.attr));
            }
        }
        // lambda x: x[0]
        Expr::Subscript(sub) => {
            if is_same_name(&sub.value, &lambda.args[0]) {
                return Some(format!("[...]"));
            }
        }
        // lambda x: x.method()
        Expr::Call(call) => {
            if let Expr::Attribute(attr) = &*call.func {
                if is_same_name(&attr.value, &lambda.args[0]) && call.args.is_empty() {
                    return Some(format!(".{}()", attr.attr));
                }
            }
        }
        _ => {}
    }
    None
}
```

### Error Message

```
DOEFF022: Use Program proxy instead of '.map(lambda x: x.{access})'.

Program[T] acts as a proxy, allowing direct attribute access, __getitem__,
and __call__ without explicit .map().

Fix: Replace the map with direct proxy access:
  # Before
  p_run_id: Program[str] = p_asset.map(lambda asset: asset.run_id)
  p_first_item: Program[Item] = p_items.map(lambda items: items[0])
  p_result: Program[Result] = p_func.map(lambda func: func(arg))
  
  # After (using proxy)
  p_run_id: Program[str] = p_asset.run_id
  p_first_item: Program[Item] = p_items[0]
  p_result: Program[Result] = p_func(arg)
```

## Subtasks

- [ ] `doeff022_no_map_lambda_attr.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF022.md ドキュメント作成
- [ ] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成



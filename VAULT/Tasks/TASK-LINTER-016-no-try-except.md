---
id: TASK-LINTER-016
title: DOEFF014 no-try-except ルール実装
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
tags: [task, linter, doeff014]
---

# TASK-LINTER-016 — DOEFF014 no-try-except ルール実装

## Description

try-except ブロックの使用を検出し、doeff のエラーハンドリングエフェクト（`Safe`, `recover`, `first_success`, `Catch` など）の使用を推奨するルール。

try-except は：
- エラーハンドリングフローを隠蔽する
- エフェクトシステムをバイパスする
- 型安全性を損なう
- 合成可能性が低い

## Acceptance Criteria

- [x] try ブロックを検出して警告
- [x] ネストした try ブロックも検出
- [x] 非同期関数内の try も検出
- [x] クラスメソッド内の try も検出
- [x] 警告メッセージに `Safe`, `recover`, `first_success`, `Catch` を含める
- [x] ユニットテスト 10 件以上 (14件実装)

## Implementation Notes

### Detection Logic

```rust
fn check_stmt(stmt: &Stmt, violations: &mut Vec<Violation>, file_path: &str) {
    match stmt {
        Stmt::Try(try_stmt) => {
            violations.push(Violation::new(
                "DOEFF014".to_string(),
                "Avoid using try-except blocks...",
                try_stmt.range.start().to_usize(),
                file_path.to_string(),
                Severity::Warning,
            ));
            // ネストした try も再帰的に検出
            for s in &try_stmt.body { Self::check_stmt(s, violations, file_path); }
            // handlers, orelse, finalbody も走査
        }
        // 他の statement タイプも再帰的に走査
        ...
    }
}
```

### Error Message

```
DOEFF014: Avoid using try-except blocks.

Problem: Using try-except blocks hides error handling flow. 

Fix: Use doeff's error handling effects instead:
  - `Safe(program)` to get a Result object
  - `program.recover(fallback)` for fallback values
  - `program.first_success(alt1, alt2)` to try alternatives
  - `Catch(program, handler)` to transform errors

Example: `result = yield Safe(risky_op())` then match on Ok/Err.
```

## Subtasks

- [x] `doeff014_no_try_except.rs` 作成
- [x] mod.rs に登録
- [x] main.rs にルール情報追加
- [x] DOEFF014.md ドキュメント作成
- [x] cargo test 実行
- [x] cargo install 動作確認

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- 関連: DOEFF018 (no-ask-in-try) - try 内での ask 使用禁止
- ドキュメント: [[05-error-handling]] - Safe, Catch, Recover の使い方

## Progress Log

### 2025-12-04
- タスク作成
- DOEFF014 ルール実装完了
  - `packages/doeff-linter/src/rules/doeff014_no_try_except.rs` 作成
  - `mod.rs` に登録
  - `packages/doeff-linter/docs/rules/DOEFF014.md` ドキュメント作成
  - 14件のユニットテストすべてパス
  - 警告メッセージに `Safe`, `recover`, `first_success`, `Catch` を含める形で更新
  - `cargo install --path .` で動作確認完了



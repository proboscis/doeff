---
id: PROJECT-CORE-001
title: Native try-except Support in @do Functions
status: done
owner:
start-date: 2025-12-07
target-date: 2025-12-07
tags: [project, core, error-handling]
---

# PROJECT-CORE-001 — Native try-except Support in @do Functions

## 1. Summary

Python の native `try-except` を `@do` 関数内で使用できるようにする。Generator wrapper と interpreter の両方を修正し、エラーが適切に generator に throw されるようにする。

### Goals

1. `try-except` が yielded sub-programs のエラーをキャッチできる
2. Effect-based error handling (`Safe`, `Catch`, `Recover`) との共存
3. 既存コードの後方互換性維持

## 2. Scope（範囲）

### In Scope

- Generator wrapper の修正 (4箇所)
- Interpreter の `_execute_program_loop` 修正
- Documentation 更新
- テスト追加

### Out of Scope

- Effect-based handlers の変更（互換性のため維持）
- ~~Linter rule DOEFF014 の削除（別途検討）~~ → Updated to Info severity (TASK-CORE-006)

## 3. Related Tasks

### Core Implementation

- [[TASK-CORE-001]] — Generator wrapper: DoYieldFunction
- [[TASK-CORE-002]] — Generator wrapper: KleisliProgramCall
- [[TASK-CORE-003]] — Generator wrapper: force_eval & _intercept_generator
- [[TASK-CORE-004]] — Interpreter: _execute_program_loop

### Documentation & Testing

- [[TASK-CORE-005]] — Documentation update & Tests

### Linter Updates

- [[TASK-CORE-006]] — Update DOEFF014 linter rule

## 4. Related Issues

- [GitHub Issue #2](https://github.com/CyberAgentAILab/doeff/issues/2) — Generator wrapper doesn't forward errors

## 5. Metrics / Progress

| Status | Count |
|--------|-------|
| 計画済み | 0 |
| 進行中 | 0 |
| 完了 | 6 |

### Task Progress

| Task | Description | Status |
|------|-------------|--------|
| TASK-CORE-001 | DoYieldFunction wrapper | ✅ done |
| TASK-CORE-002 | KleisliProgramCall wrapper | ✅ done |
| TASK-CORE-003 | force_eval & _intercept_generator | ✅ done |
| TASK-CORE-004 | Interpreter loop | ✅ done |
| TASK-CORE-005 | Docs & Tests | ✅ done |
| TASK-CORE-006 | DOEFF014 linter rule update | ✅ done |

## 6. Risks / Blockers

- **Risk 1**: finally ブロック内の yield 処理が複雑になる可能性
  - Mitigation: Python の generator protocol に従う
- **Risk 2**: 既存テストの破壊
  - Mitigation: 全テストを事前に実行して baseline 確認

## 7. Changelog

### 2025-12-07
- 初回セットアップ
- Spec, Project, Tasks 作成
- All 5 tasks implemented and tested
- 415 tests passing
- GitHub Issue #2 resolved

---
id: PROJECT-LINTER-001
title: AGENTS.md Linter Migration
status: in-progress
owner: 
start-date: 2025-12-04
target-date: 
tags: [project, linter, doeff-linter]
---

# PROJECT-LINTER-001 — AGENTS.md Linter Migration

## 1. Summary

AGENTS.md で定義されているコーディングガイドラインを doeff-linter の静的解析ルールとして実装し、開発時に自動的にアンチパターンを検出できるようにする。

### Goals

1. ガイドライン違反を CI/IDE で自動検出
2. 開発者がガイドラインを意識せずとも正しいパターンに導く
3. コードレビューの負担軽減

## 2. Scope（範囲）

### In Scope

- Program/Doeff 関連のアンチパターン検出
- ask effect の誤用検出
- インポート/モジュール規則の検出
- 命名規則の検出

### Out of Scope

- 実行時のみ検出可能なパターン
- 型推論が必要な高度な検出（pyright に委譲）

## 3. Related Tasks

### High Priority (P0)
- [[TASK-LINTER-001]] — DOEFF016 no-relative-import
- [[TASK-LINTER-002]] — DOEFF017 no-program-type-param
- [[TASK-LINTER-003]] — DOEFF018 no-ask-in-try
- [[TASK-LINTER-004]] — DOEFF019 no-ask-with-fallback

### Medium Priority (P1)
- [[TASK-LINTER-005]] — DOEFF020 program-naming-convention
- [[TASK-LINTER-006]] — DOEFF021 no-dunder-all
- [[TASK-LINTER-007]] — DOEFF022 no-map-lambda-attr
- [[TASK-LINTER-008]] — DOEFF023 no-ask-cli-param
- [[TASK-LINTER-009]] — DOEFF024 no-interpreter-in-do
- [[TASK-LINTER-013]] — DOEFF028 no-none-for-failure
- [[TASK-LINTER-014]] — DOEFF029 doeff-kleisli-comment-placement

### Low Priority (P2)
- [[TASK-LINTER-010]] — DOEFF025 no-path-intermediate
- [[TASK-LINTER-011]] — DOEFF026 no-monolithic-function
- [[TASK-LINTER-012]] — DOEFF027 no-side-effect-in-processing

## 4. Related Issues

- 

## 5. Metrics / Progress

| Status | Count |
|--------|-------|
| 実装済み (既存) | 15 (DOEFF001-015) |
| 未実装 (High Priority) | 4 |
| 未実装 (Medium Priority) | 7 |
| 未実装 (Low Priority) | 3 |
| **合計タスク** | **14** |

### 既存ルール (15)

| Rule ID | Name | Status |
|---------|------|--------|
| DOEFF001 | builtin-shadowing | ✅ |
| DOEFF002 | mutable-attribute-naming | ✅ |
| DOEFF003 | max-mutable-attributes | ✅ |
| DOEFF004 | no-os-environ | ✅ |
| DOEFF005 | no-setter-methods | ✅ |
| DOEFF006 | no-tuple-returns | ✅ |
| DOEFF007 | no-mutable-argument-mutations | ✅ |
| DOEFF008 | no-dataclass-attribute-mutation | ✅ |
| DOEFF009 | missing-return-type-annotation | ✅ |
| DOEFF010 | test-file-placement | ✅ |
| DOEFF011 | no-flag-arguments | ✅ |
| DOEFF012 | no-append-loop | ✅ |
| DOEFF013 | prefer-maybe-monad | ✅ |
| DOEFF014 | no-try-except | ✅ |
| DOEFF015 | no-zero-arg-program | ✅ |

## 6. Risks / Blockers

- False positive が多いとルールが無効化される可能性
- 一部のパターンは型情報なしでは検出困難

## 7. Changelog

### 2025-12-04
- 初回セットアップ
- DOEFF015 (no-zero-arg-program) 実装完了
- アンチパターン一覧抽出完了



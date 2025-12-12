---
id: ISSUE-CORE-401
title: Intercept Semantics — Optional Hook for Yielded Program Rewriting
module: core
status: open
severity: low
related-project:
related-spec:
related-task:
related-feature:
created: 2025-12-13
updated: 2025-12-13
tags: [issue, core, intercept, semantics]
---

# ISSUE-CORE-401 — Intercept Semantics — Optional Hook for Yielded Program Rewriting

## Summary

`intercept` は現在「yield された `EffectBase` を transform する」ための仕組みとして運用されているが、将来的には
「yield された `Program`（nested program 呼び出し）そのものを差し替える/ラップする」フックが欲しくなる可能性がある。

現状は必須ではないが、intercept のセマンティクスを明確にしたうえで、必要なら別APIとして拡張できるように設計余地を残す。

## Context / Motivation

- 一部の interpreter 実装で、`Program` yield を effect-level intercept で処理しようとして破綻した事例があった（nested Program への transform 伝播が切れる等）。
- セマンティクスを安定させるには、intercept を **Effect-only** として定義し、`Program` yield は「同一 intercept 文脈で実行（transforms の伝播）」に寄せるのが自然。
- 一方で、将来的に以下の要望が出る可能性はある:
  - nested Program の差し替え（A/B, fallback, compatibility shim）
  - nested Program 単位の instrumentation（profiling/tracing/caching）を強制適用
  - call-site 情報（call stack / frame）に基づくルーティング

## Desired Outcome

- intercept の仕様を「Effect-only」前提で明文化する。
- `Program` yield を変換したいユースケースに対して、intercept と混ぜずに提供できる拡張点（別API）を検討する。

## Candidate Designs

### Option A: Separate API (recommended)

`Program` yield を扱う専用のフックを追加する（例: `Program.intercept_programs(...)` / `with_program_yield_hook(...)`）。

- `Callable[[ProgramBase], ProgramBase]` のような変換関数を適用
- 既存の `intercept`（Effect-only）とは独立させ、再帰/二重適用のルールを明確にできる

### Option B: Extend intercept transform signature (risky)

現行の `Callable[[Effect], Effect | Program[Effect]]` を拡張して `Program` を入力に含める。

- `Effect` と `Program` の両方を扱えるため強力だが、セマンティクスが複雑化しやすい
- transform が返す “transform program” に対する再帰防止との相性が難しい

## Acceptance Criteria

- [ ] `intercept` の仕様（Effect-only / nested Program は transforms を伝播して実行）を docs に明記
- [ ] Program-yield rewriting を行う拡張点を 1 つ提案（Option A/B のどちらか）
- [ ] 最小の回帰テスト方針（どのレイヤで保証するか）を決める

## Related

- Code: `doeff/interpreter.py` (`_intercept_generator` / `_InterceptedProgram`)

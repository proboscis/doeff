---
id: SPEC-LINTER-001
title: AGENTS.md Anti-Pattern Detection Rules
module: linter
status: approved
code_path: packages/doeff-linter/
version: 0.1.0
related-feature: 
created: 2025-12-04
updated: 2025-12-04
tags: [spec, linter]
---

# SPEC-LINTER-001 — AGENTS.md Anti-Pattern Detection Rules

## 1. Overview

AGENTS.md で定義されているコーディングガイドラインから抽出したアンチパターンを静的解析で検出するためのルール仕様。

## 2. Background / Motivation

- AGENTS.md には多数のコーディング規則が記載されている
- 人間がレビューで全てをチェックするのは非現実的
- 静的解析で自動検出することで品質を担保

## 3. Requirements

### 3.1 Functional Requirements

各ルールは以下を満たす:
- AST ベースで検出可能
- 明確な違反メッセージを出力
- 修正方法を提示
- noqa コメントで抑制可能

### 3.2 Non-Functional Requirements

- 大規模コードベースでも高速に動作
- False positive を最小限に

## 4. Detailed Specification

### 4.1 Anti-Pattern Catalog

#### Category A: Program/Doeff 関連

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| A1 | `ask` で CLI 引数取得 | `ask("param_*")` パターン | Medium |
| A2 | Program 引数なしファクトリ | `p_x: Program = f()` | High ✅ |
| A3 | interpreter 直接使用 | `interpreter.run()` 呼び出し | Medium |
| A4 | `Program[T]` を引数型に | FunctionDef 引数検査 | High |
| A5 | `.map(lambda)` 過剰使用 | Call + Lambda パターン | Medium |

#### Category B: ask effect 関連

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| B1 | `ask` を try/except で囲む | Try 内の `ask` yield | High |
| B2 | `ask` とデフォルト引数併用 | `arg or (yield ask())` | High |

#### Category C: エラーハンドリング

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| C1 | None で失敗表現 | Return None + 関数名 | Medium |
| C2 | try/except 濫用 | Try ステートメント | High ✅ |

#### Category D: インポート/モジュール

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| D1 | 相対インポート | `from .module` | High |
| D2 | `__all__` 使用 | `__all__ = [...]` | High |

#### Category E: 命名規則

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| E1 | Program 変数名規則違反 | `*_program` vs `p_*` | High |

#### Category F: コードスタイル

| ID | Pattern | Detection | Feasibility |
|----|---------|-----------|-------------|
| F1 | 型アノテーション欠如 | AnnAssign 欠如 | High ✅ |
| F2 | 環境変数アクセス | `os.environ` | High ✅ |
| F3 | フラグ引数 | `bool` + `is_*` 等 | High ✅ |

### 4.2 Priority Matrix

| Priority | Rules | Rationale |
|----------|-------|-----------|
| P0 (Immediate) | D1, A4, B1 | 低複雑度・高インパクト |
| P1 (High) | B2, E1, D2 | 低複雑度・中インパクト |
| P2 (Medium) | A5, A1, C1 | 中複雑度 |
| P3 (Low) | A3 | 高複雑度・低インパクト |

### 4.3 Rule Mapping to DOEFF IDs

| New Rule | Proposed ID | Priority | Task |
|----------|-------------|----------|------|
| no-relative-import | DOEFF016 | High | TASK-LINTER-001 |
| no-program-type-param | DOEFF017 | High | TASK-LINTER-002 |
| no-ask-in-try | DOEFF018 | High | TASK-LINTER-003 |
| no-ask-with-fallback | DOEFF019 | High | TASK-LINTER-004 |
| program-naming-convention | DOEFF020 | Medium | TASK-LINTER-005 |
| no-dunder-all | DOEFF021 | Medium | TASK-LINTER-006 |
| no-map-lambda-attr | DOEFF022 | Medium | TASK-LINTER-007 |
| no-ask-cli-param | DOEFF023 | Medium | TASK-LINTER-008 |
| no-interpreter-in-do | DOEFF024 | Medium | TASK-LINTER-009 |
| no-path-intermediate | DOEFF025 | Low | TASK-LINTER-010 |
| no-monolithic-function | DOEFF026 | Low | TASK-LINTER-011 |
| no-side-effect-in-processing | DOEFF027 | Low | TASK-LINTER-012 |
| no-none-for-failure | DOEFF028 | Medium | TASK-LINTER-013 |
| doeff-kleisli-comment-placement | DOEFF029 | Medium | TASK-LINTER-014 |

## 5. Examples

### D1: no-relative-import

```python
# ❌ Bad
from .module import something
from ..utils import helper

# ✅ Good
from placement.analysis.module import something
from doeff.utils import helper
```

### A4: no-program-type-param

```python
# ❌ Bad
@do
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    ...

# ✅ Good
@do
def process(data: DataFrame) -> EffectGenerator[Result]:
    ...
# doeff が自動的に Program[DataFrame] を解決
```

### B1: no-ask-in-try

```python
# ❌ Bad
@do
def get_config():
    try:
        value = yield ask("config_key")
    except:
        value = "default"
    return value

# ✅ Good - ask は必ず成功すべき。失敗は設計エラー
@do
def get_config():
    value = yield ask("config_key")
    return value
```

### E1: program-naming-convention

```python
# ❌ Bad
data_program: Program[Data] = load_data(path=Path("data.json"))
some_dataframe_program: Program[DataFrame] = process()

# ✅ Good
p_data: Program[Data] = load_data(path=Path("data.json"))
p_df: Program[DataFrame] = process(config=cfg)
```

## 6. Open Questions

- A1 (ask で CLI 引数): どの引数名パターンを「CLI 引数」とみなすか？
- C1 (None で失敗表現): Optional[T] を返す正当なケースとの区別は？

## 7. References

- [[PROJECT-LINTER-001]]
- AGENTS.md (repository guidelines)
- packages/doeff-linter/docs/rules/



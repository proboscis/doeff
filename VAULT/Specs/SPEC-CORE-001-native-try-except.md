---
id: SPEC-CORE-001
title: Native try-except Support in @do Functions
module: core
status: approved
code_path: doeff/
version: 0.1.0
related-feature:
created: 2025-12-07
updated: 2025-12-07
tags: [spec, core, error-handling]
---

# SPEC-CORE-001 — Native try-except Support in @do Functions

## 1. Overview

Python の native `try-except` 構文を `@do` 関数内で使用できるようにする。現状では generator wrapper がエラーを内部 generator に転送しないため、`yield` した sub-program のエラーを `try-except` でキャッチできない。

## 2. Background / Motivation

### Current Problem (GitHub Issue #2)

```python
@do
def catches():
    try:
        x = yield fails()  # ValueError raised in sub-program
        return f"got: {x}"
    except ValueError as e:
        return f"caught: {e}"  # ← This NEVER runs!

# Expected: "caught: test error"
# Actual: ValueError propagates up uncaught
```

### Root Cause

1. **Generator wrappers don't forward errors** - `DoYieldFunction.generator_wrapper` と `KleisliProgramCall.to_generator()` は `gen.throw()` を使用していない
2. **Interpreter returns Err instead of throwing** - エラー発生時に `Err()` を返して早期終了し、generator に throw しない

### Why This Matters

- Python 開発者の直感に反する動作
- 単純なエラーハンドリングでも effect-based API (`Safe`, `Catch`, `Recover`) が必要
- 学習コストの増加

## 3. Requirements

### 3.1 Functional Requirements

- FR1: `try-except` が yielded effects/programs からのエラーをキャッチできる
- FR2: Effect-based エラーハンドリング (`Safe`, `Catch`, `Recover`) が引き続き動作する
- FR3: `finally` ブロックが正しく実行される
- FR4: Exception chaining (`raise ... from e`) が保持される
- FR5: `GeneratorExit` が適切に処理される（cleanup）

### 3.2 Non-Functional Requirements

- NFR1: 既存の正常パスのパフォーマンスに影響を与えない
- NFR2: 後方互換性を維持
- NFR3: テストカバレッジ 100%

## 4. Detailed Specification

### 4.1 Generator Wrapper Pattern

全ての generator wrapper に以下のパターンを適用:

```python
while True:
    try:
        sent_value = yield current
    except GeneratorExit:
        gen.close()
        raise
    except BaseException as e:
        try:
            current = gen.throw(e)
        except StopIteration as stop_exc:
            return stop_exc.value
        continue
    try:
        current = gen.send(sent_value)
    except StopIteration as stop_exc:
        return stop_exc.value
```

### 4.2 Affected Locations

| File | Location | Lines |
|------|----------|-------|
| `doeff/do.py` | `DoYieldFunction.generator_wrapper` | 41-46 |
| `doeff/program.py` | `KleisliProgramCall.to_generator()` | 540-545 |
| `doeff/interpreter.py` | `force_eval()` | 106-116 |
| `doeff/interpreter.py` | `_intercept_generator()` | 652-685 |

### 4.3 Interpreter Changes

`_execute_program_loop()` で `gen.throw()` を使用:

**Effect Error (lines 296-312):**
```python
except Exception as exc:
    # NEW: Throw into generator instead of returning Err
    try:
        current = gen.throw(exc)
        continue
    except StopIteration as e:
        return RunResult(ctx, Ok(e.value))
    except Exception as uncaught:
        if uncaught is exc:
            return RunResult(ctx, Err(effect_failure))
        # Handle new exception from catch block
        new_failure = EffectFailure(effect=current, cause=uncaught, ...)
        return RunResult(ctx, Err(new_failure))
```

**Sub-program Error (lines 314-324):**
```python
if isinstance(sub_result.result, Err):
    error = sub_result.result.error
    exc = error.cause if isinstance(error, EffectFailure) else error
    try:
        current = gen.throw(exc)
        ctx = sub_result.context
        continue
    except StopIteration as e:
        return RunResult(ctx, Ok(e.value))
    except Exception as uncaught:
        if uncaught is exc:
            return sub_result
        return RunResult(ctx, Err(uncaught))
```

### 4.4 Constraints

1. **GeneratorExit 特別処理** - `GeneratorExit` は inner generator に forward しない。`gen.close()` を呼んで re-raise
2. **StopIteration 処理** - `throw()` から `StopIteration` が返る場合は正常終了
3. **Exception identity** - uncaught exception が元の exception と同一かチェック

## 5. Examples

### Basic try-except

```python
@do
def safe_divide():
    try:
        result = yield divide(10, 0)
        return result
    except ZeroDivisionError:
        return float('inf')
```

### Nested try-except

```python
@do
def nested():
    try:
        try:
            value = yield risky_operation()
        except ValueError:
            value = yield fallback_operation()
        return value
    except Exception as e:
        yield log(f"All attempts failed: {e}")
        raise
```

### try-finally

```python
@do
def with_cleanup():
    resource = yield acquire_resource()
    try:
        result = yield use_resource(resource)
        return result
    finally:
        yield release_resource(resource)  # Always executes
```

### Mixing with Effect-based handling

```python
@do
def mixed():
    # Effect-based for complex recovery
    config = yield Safe(load_config())

    # Native try-except for simple cases
    try:
        data = yield fetch_data(config.value)
    except NetworkError:
        data = cached_data

    return data
```

## 6. Open Questions

- ~~Q1: `GeneratorExit` の処理方法~~ → Resolved: `gen.close()` + re-raise
- ~~Q2: 既存の effect-based handlers との互換性~~ → Resolved: `run_async` boundary で動作するため影響なし

## 7. References

- [GitHub Issue #2](https://github.com/CyberAgentAILab/doeff/issues/2)
- [[PROJECT-CORE-001]]
- Python Generator Protocol: PEP 342
- `doeff/do.py` lines 91-126 (current warning documentation)

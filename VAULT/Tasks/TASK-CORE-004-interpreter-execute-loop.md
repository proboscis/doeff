---
id: TASK-CORE-004
title: Interpreter - _execute_program_loop
module: core
status: todo
priority: high
due-date:
related-project: PROJECT-CORE-001
related-spec: SPEC-CORE-001
related-feature:
code_path: doeff/interpreter.py
created: 2025-12-07
updated: 2025-12-07
tags: [task, core, error-handling]
---

# TASK-CORE-004 — Interpreter - _execute_program_loop

## Description

`ProgramInterpreter._execute_program_loop()` を修正し、エラー発生時に `Err()` を返すのではなく `gen.throw()` で generator にエラーを注入する。これにより native `try-except` がエラーをキャッチできるようになる。

## Acceptance Criteria

- [ ] Effect エラーが generator に throw される
- [ ] Sub-program エラーが generator に throw される
- [ ] Uncaught exception は `Err()` として返される
- [ ] Catch block で発生した新しい例外が正しく処理される
- [ ] `Safe`, `Catch`, `Recover` effects が引き続き動作する
- [ ] 既存テストが全てパスする

## Implementation Notes

### Effect Error Handling (lines 296-312)

**Current:**
```python
try:
    value = await self._handle_effect(current, ctx)
except Exception as exc:
    runtime_tb = capture_traceback(exc)
    effect_failure = EffectFailure(...)
    return RunResult(ctx, Err(effect_failure))
```

**New:**
```python
try:
    value = await self._handle_effect(current, ctx)
except Exception as exc:
    runtime_tb = capture_traceback(exc)
    effect_failure = EffectFailure(
        effect=current,
        cause=exc,
        runtime_traceback=runtime_tb,
        creation_context=current.created_at,
    )
    # Throw into generator
    try:
        current = gen.throw(exc)
        continue
    except StopIteration as e:
        return RunResult(ctx, Ok(e.value))
    except Exception as uncaught:
        if uncaught is exc:
            return RunResult(ctx, Err(effect_failure))
        # New exception from catch block
        new_tb = capture_traceback(uncaught)
        new_failure = EffectFailure(
            effect=current,
            cause=uncaught,
            runtime_traceback=new_tb,
            creation_context=current.created_at,
        )
        return RunResult(ctx, Err(new_failure))
```

### Sub-program Error Handling (lines 314-324)

**Current:**
```python
sub_result = await self.run_async(current, ctx)
if isinstance(sub_result.result, Err):
    return sub_result
```

**New:**
```python
sub_result = await self.run_async(current, ctx)
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

## Subtasks

- [ ] Modify effect error handling to use gen.throw()
- [ ] Modify sub-program error handling to use gen.throw()
- [ ] Handle exception identity check (uncaught is exc)
- [ ] Handle new exceptions from catch blocks
- [ ] Verify Safe/Catch/Recover compatibility

## Related

- Spec: [[SPEC-CORE-001]]
- Project: [[PROJECT-CORE-001]]
- PR:

## Progress Log

### 2025-12-07
- Task created

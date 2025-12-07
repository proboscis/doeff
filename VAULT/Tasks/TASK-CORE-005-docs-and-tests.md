---
id: TASK-CORE-005
title: Documentation update & Tests
module: core
status: todo
priority: medium
due-date:
related-project: PROJECT-CORE-001
related-spec: SPEC-CORE-001
related-feature:
code_path: doeff/
created: 2025-12-07
updated: 2025-12-07
tags: [task, core, documentation, testing]
---

# TASK-CORE-005 — Documentation update & Tests

## Description

native try-except サポートに関するドキュメント更新とテスト追加。

## Acceptance Criteria

- [ ] `do.py` の警告ドキュメントが更新される
- [ ] 新機能のテストが追加される
- [ ] 既存テスト `test_why_try_except_doesnt_work` が更新される
- [ ] Edge case テストが追加される

## Implementation Notes

### Documentation Updates

**File: `doeff/do.py` (lines 91-126)**

現在の "CRITICAL ERROR HANDLING WARNING" を更新:
- try-except が動作することを説明
- Effect-based handling との使い分けガイダンス

### New Tests

**File: `tests/test_error_handling_effects.py`**

追加するテスト:
1. `test_native_try_except_catches_effect_error`
2. `test_native_try_except_catches_subprogram_error`
3. `test_nested_try_except`
4. `test_try_finally_executes`
5. `test_exception_chaining_preserved`
6. `test_uncaught_exception_becomes_err`
7. `test_safe_catch_recover_still_work`
8. `test_generator_exit_cleanup`

### Test Examples

```python
@pytest.mark.asyncio
async def test_native_try_except_catches_effect_error():
    """Native try-except should catch errors from yielded effects."""

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Fail(ValueError("test error"))
            return "unreachable"
        except ValueError as e:
            return f"caught: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught: test error"


@pytest.mark.asyncio
async def test_native_try_except_catches_subprogram_error():
    """Native try-except should catch errors from sub-programs."""

    @do
    def failing_subprogram() -> EffectGenerator[int]:
        yield Program.pure(1)
        raise ValueError("subprogram error")

    @do
    def program() -> EffectGenerator[str]:
        try:
            x = yield failing_subprogram()
            return f"got: {x}"
        except ValueError as e:
            return f"caught: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught: subprogram error"
```

## Subtasks

- [ ] Update do.py documentation (lines 91-126)
- [ ] Add basic try-except tests
- [ ] Add nested try-except test
- [ ] Add try-finally test
- [ ] Add exception chaining test
- [ ] Add uncaught exception test
- [ ] Add Safe/Catch/Recover compatibility tests
- [ ] Add GeneratorExit cleanup test
- [ ] Update existing test_why_try_except_doesnt_work

## Related

- Spec: [[SPEC-CORE-001]]
- Project: [[PROJECT-CORE-001]]
- PR:

## Progress Log

### 2025-12-07
- Task created

---
id: TASK-CORE-003
title: Generator wrapper - force_eval & _intercept_generator
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

# TASK-CORE-003 — Generator wrapper - force_eval & _intercept_generator

## Description

`interpreter.py` 内の2つの generator wrapper に例外転送機能を追加:
1. `force_eval()` の `forced_generator()`
2. `_intercept_generator()`

## Acceptance Criteria

- [ ] `force_eval` 経由の例外が正しく転送される
- [ ] `_intercept_generator` の全 yield point で例外が転送される
- [ ] `GeneratorExit` が適切に処理される
- [ ] 既存テストが全てパスする

## Implementation Notes

### force_eval (lines 106-116)

**Current:**
```python
try:
    current = next(gen)
    while True:
        from doeff.types import Program as ProgramType
        if isinstance(current, ProgramType):
            current = force_eval(current)
        value = yield current
        current = gen.send(value)
except StopIteration as e:
    return e.value
```

**New:**
```python
try:
    current = next(gen)
except StopIteration as e:
    return e.value

while True:
    from doeff.types import Program as ProgramType
    if isinstance(current, ProgramType):
        current = force_eval(current)
    try:
        value = yield current
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
        current = gen.send(value)
    except StopIteration as e:
        return e.value
```

### _intercept_generator (lines 652-685)

複数の yield point がある:
- Line 655: `final_effect = yield effect_program`
- Line 666: `result = yield nested_effect`
- Line 676: `yield wrapped` (in send)
- Line 681: `value = yield current`

各 yield point に例外転送パターンを適用。

## Subtasks

- [ ] Restructure force_eval to separate next() from loop
- [ ] Add exception forwarding to force_eval
- [ ] Add exception forwarding to _intercept_generator (4 yield points)
- [ ] Test intercept functionality with exceptions

## Related

- Spec: [[SPEC-CORE-001]]
- Project: [[PROJECT-CORE-001]]
- PR:

## Progress Log

### 2025-12-07
- Task created

---
id: TASK-CORE-002
title: Generator wrapper - KleisliProgramCall
module: core
status: todo
priority: high
due-date:
related-project: PROJECT-CORE-001
related-spec: SPEC-CORE-001
related-feature:
code_path: doeff/program.py
created: 2025-12-07
updated: 2025-12-07
tags: [task, core, error-handling]
---

# TASK-CORE-002 — Generator wrapper - KleisliProgramCall

## Description

`KleisliProgramCall.to_generator()` の inner generator に例外転送機能を追加する。TASK-CORE-001 と同じパターンを適用。

## Acceptance Criteria

- [ ] `try-except` が KleisliProgramCall 経由の例外をキャッチできる
- [ ] `GeneratorExit` が適切に処理される
- [ ] 既存テストが全てパスする

## Implementation Notes

### Current Code (lines 540-545)

```python
while True:
    sent_value = yield current
    try:
        current = generator_obj.send(sent_value)
    except StopIteration as stop_exc:
        return stop_exc.value
```

### New Code

```python
while True:
    try:
        sent_value = yield current
    except GeneratorExit:
        generator_obj.close()
        raise
    except BaseException as e:
        try:
            current = generator_obj.throw(e)
        except StopIteration as stop_exc:
            return stop_exc.value
        continue
    try:
        current = generator_obj.send(sent_value)
    except StopIteration as stop_exc:
        return stop_exc.value
```

## Subtasks

- [ ] Modify to_generator inner loop
- [ ] Add GeneratorExit handling
- [ ] Add BaseException forwarding via throw()

## Related

- Spec: [[SPEC-CORE-001]]
- Project: [[PROJECT-CORE-001]]
- PR:

## Progress Log

### 2025-12-07
- Task created

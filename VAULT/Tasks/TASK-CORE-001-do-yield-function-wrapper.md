---
id: TASK-CORE-001
title: Generator wrapper - DoYieldFunction
module: core
status: todo
priority: high
due-date:
related-project: PROJECT-CORE-001
related-spec: SPEC-CORE-001
related-feature:
code_path: doeff/do.py
created: 2025-12-07
updated: 2025-12-07
tags: [task, core, error-handling]
---

# TASK-CORE-001 — Generator wrapper - DoYieldFunction

## Description

`DoYieldFunction.generator_wrapper` に例外転送機能を追加する。現在のコードは `gen.send()` のみを使用しているが、`gen.throw()` を使用して例外を inner generator に転送する必要がある。

## Acceptance Criteria

- [ ] `try-except` が inner generator の例外をキャッチできる
- [ ] `GeneratorExit` が適切に処理される（forward せず close）
- [ ] `StopIteration` from throw が正常終了として処理される
- [ ] 既存テストが全てパスする

## Implementation Notes

### Current Code (lines 41-46)

```python
while True:
    sent_value = yield current
    try:
        current = gen.send(sent_value)
    except StopIteration as stop_exc:
        return stop_exc.value
```

### New Code

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

## Subtasks

- [ ] Modify generator_wrapper while loop
- [ ] Add GeneratorExit handling
- [ ] Add BaseException forwarding via throw()
- [ ] Test with simple try-except case

## Related

- Spec: [[SPEC-CORE-001]]
- Project: [[PROJECT-CORE-001]]
- PR:

## Progress Log

### 2025-12-07
- Task created

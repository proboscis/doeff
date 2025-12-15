---
id: ISSUE-CORE-402
title: Reader env should accept hashable keys (ask/local)
module: core
status: open
severity: medium
related-project:
related-spec:
related-task: TASK-CORE-007
related-feature:
created: 2025-12-15
updated: 2025-12-15
tags: [issue, core, reader, ask, local, typing, di]
---

# ISSUE-CORE-402 — Reader env should accept hashable keys (ask/local)

## Summary

`ask` / `local` currently enforce `str`-only environment keys (both at the type level and via runtime
validators). This blocks DI patterns that use Python objects (e.g., a `Protocol` class) as the key.

## Context / Motivation

Example desired usage:

```python
from typing import Protocol
from doeff import ask, Local

class SomeFunc(Protocol):
    def __call__(self) -> None: ...

@do
def program():
    func: SomeFunc = yield ask(SomeFunc)
    return func()

impl: SomeFunc = lambda: None
run_program = Local({SomeFunc: impl}, program())
```

Today this fails because:

- `AskEffect.key: str` + `ensure_str(self.key)` rejects non-strings.
- `LocalEffect.env_update: Mapping[str, object]` + `ensure_env_mapping(...)` rejects non-string keys.

## Desired Outcome

- Allow any hashable key type for the Reader environment:
  - `yield ask(SomeFunc)` works when the env contains `{SomeFunc: impl}`.
  - `Local({SomeFunc: impl}, ...)` accepts env updates with non-string keys.
- Preserve existing string-key behavior and internal reserved keys (e.g., `"__interpreter__"`,
  `"__resolver__"`, `"__repr_limit__"`).

## Notes / Direction

- Introduce an explicit `EnvKey = Hashable` alias (or similar) and use it consistently in:
  - `AskEffect.key`, `ask(...)`, `Ask(...)`
  - `LocalEffect.env_update`, `local(...)`, `Local(...)`
  - `ExecutionContext.env`
- Update runtime validation to enforce "hashable" instead of "str".
- Update type stubs (`*.pyi`) and any downstream call sites that assume `dict[str, Any]`.
- Update error messages (e.g., missing key / cyclic dependency) to use `key!r` for non-string keys.

## Acceptance Criteria

- [ ] `ask(key)` accepts any hashable key (rejects unhashable keys with a clear `TypeError`).
- [ ] `local(env_update, ...)` accepts mappings with hashable keys.
- [ ] `ExecutionContext.env` typing supports non-string keys end-to-end (including `.copy()` paths).
- [ ] Existing reserved string keys keep working as before.
- [ ] Add regression tests covering class/Protocol keys.

## Related

- Task: [[TASK-CORE-007]]
- Code: `doeff/effects/reader.py`, `doeff/effects/_validators.py`, `doeff/_types_internal.py`,
  `doeff/handlers/__init__.py`

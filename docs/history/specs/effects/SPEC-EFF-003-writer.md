# SPEC-EFF-003: Writer Effect Semantics

## Status: Confirmed

## Summary

This spec defines the semantics for Writer effects: `Tell` and `Listen`. `Tell` appends a message to the writer log. `Listen` runs a sub-program and returns the effects of chosen types that the sub-program performed, as values.

`slog` (structured observability logs) is a separate wire type, `SlogEffect`, and is not a Writer effect. `Listen` collects it only when asked (see [slog](#slog)).

## Effect Definitions

### Tell

`Tell` appends a message to the writer log.

```python
def Tell(message: object) -> WriterTellEffect:
    """Append message to the writer log. Returns None."""
```

`Tell` accepts any Python object and stores it unchanged. The effect keeps the object as `.msg`.

### Listen

`Listen` executes a sub-program and returns its value together with the effects of the requested types that the sub-program performed.

```python
Listen(program: Program[T], types: tuple[type, ...] | None = None) -> Effect
# yield Listen(program)  ->  (value: T, collected: list[Effect])
```

- `types` defaults to `(WriterTellEffect,)`: the `Tell` entries.
- `collected` holds the effect objects themselves (`Tell('x')`; read the message with `.msg`), in the order they were performed. It does not hold plain messages.

## Implementation Details

### Writer Log

The `writer` handler appends each told message (`effect.msg`) to a list kept in the `state` handler's store, so `state` must be installed outside `writer`. `writer_log()` returns a copy of that list: the plain messages, not the effects.

### Listen Mechanism

`listen_handler` handles `Listen`. When `Listen(program, types)` is performed:

1. Read the handlers installed between the Listen site and `listen_handler` (the inner handlers).
2. Build the program that runs the sub-program: the inner handlers are installed again around it, so it sees the handler chain it had at the Listen site; an observer handler is installed outside them; `listen_handler` itself is installed outside the observer.
3. The observer records each effect whose class is in `types`, then passes it on unchanged.
4. When the sub-program completes, return `(value, collected)` to the Listen site.

Consequences:

- Entries are passed on, not removed: the handlers outside `listen_handler` still handle them (`writer` still logs every `Tell`).
- `listen_handler` goes inside `writer` and `state`. A handler installed inside `listen_handler` consumes entries before the observer sees them: `listen_handler(writer(prog))` returns an empty `collected`.
- Because `listen_handler` installs itself again around the sub-program, a `Listen` inside a sub-program is handled by the same handler (see [Listen + Listen](#listen--listen-nested)).

## Composition Rules

### Listen + Tell

Entries told within a Listen scope are captured in `collected`, and they also stay in the writer log.

```python
@do
def program():
    value, collected = yield Listen(inner())  # inner() does Tell("x"), Tell("y")
    # [e.msg for e in collected] == ["x", "y"]
    # value is inner's return value
```

**Status: CONFIRMED**

### Listen + Local

Entries told within a Local scope (inside Listen) are captured normally.

```python
@do
def program():
    value, collected = yield Listen(
        Local({"key": "value"}, inner())  # inner() does Tell("x")
    )
    # [e.msg for e in collected] == ["x"]
```

**Status: CONFIRMED**

### Listen + Try

Entries told before a caught error are NOT lost when `Listen` wraps `Try`.

```python
@do
def inner():
    yield Tell("before_error")
    raise ValueError("error")

@do
def program():
    result, collected = yield Listen(Try(inner()))
    # result is Err(ValueError("error"))
    # [e.msg for e in collected] == ["before_error"]
```

When `Try` wraps `Listen`, an error in the sub-program leaves `Listen` before it returns: `Try(Listen(inner()))` is `Err(ValueError("error"))` and no `(value, collected)` pair is produced.

**Status: CONFIRMED**

### Listen + Spawn / Gather

- A task spawned inside the Listen scope performs its effects inside the scope, so its entries are captured, in the order the scheduler runs the tasks.
- A task spawned before the Listen and only waited on inside it (`Listen(Gather(t1, t2))`) performs its effects outside the scope, so its entries are not captured.

```python
@do
def spawn_then_listen():
    first = yield Spawn(task("A"))
    second = yield Spawn(task("B"))
    # Spawned before Listen: A's and B's Tells are not in collected.
    values, collected = yield Listen(Gather(first, second))

@do
def scope():
    first = yield Spawn(task("A"))
    second = yield Spawn(task("B"))
    return (yield Gather(first, second))

@do
def listen_then_spawn():
    # Spawned inside the scope: collected holds A's and B's Tells.
    values, collected = yield Listen(scope())
```

The order of entries from parallel tasks is the scheduler's running order. If a deterministic order is required, run the tasks one after another or sort the entries yourself.

**Status: CONFIRMED**

### Listen + Listen (Nested)

One `listen_handler` serves every nesting level. A `Listen` inside another `Listen`'s sub-program:

- The inner Listen captures only the entries of its own scope.
- The outer Listen captures its whole scope, including the entries of the inner scope. The inner Listen passes its entries on, so they reach the outer Listen's observer.
- Entries stay in the writer log (nothing is removed).

```python
@do
def inner():
    yield Tell("inner1")
    yield Tell("inner2")
    return "inner_result"

@do
def middle():
    yield Tell("middle1")
    inner_listen = yield Listen(inner())
    # [e.msg for e in inner_listen[1]] == ["inner1", "inner2"]
    yield Tell("middle2")
    return inner_listen

@do
def outer():
    outer_listen = yield Listen(middle())
    # [e.msg for e in outer_listen[1]] == ["middle1", "inner1", "inner2", "middle2"]
    # outer_listen[0] is the inner (value, collected) pair
```

Run as `run(state()(writer(listen_handler(outer()))))`: one `listen_handler`, however deep the nesting. Each level can use its own `types`; an outer Listen collects an inner scope's entries only if their class is in the outer `types`.

**Semantics:**

- Each Listen collects the entries performed between its start and its end, within its scope.
- A nested Listen collects its own scope and does not hide it from the outer Listen.

**History:** doeff 0.5.0 did not install `listen_handler` again around the sub-program, so the inner Listen found no handler and failed with `UnhandledEffect`; the only way out was `listen_handler(listen_handler(prog))`, one extra handler per nesting level. `listen_handler` now installs itself again, as `try_handler` and `local_handler` do.

**Status: CONFIRMED**

## Open Questions (Resolved)

### 1. Listen + Gather log ordering

**Resolution:** The order of entries from parallel tasks is the order the scheduler runs them. This is intentional: parallel execution means parallel logging.

### 2. Listen + Try (entries kept on error)

**Resolution:** `Listen(Try(prog))` keeps the entries told before the caught error. `Try` only turns the error into `Err`; it does not touch the entries.

### 3. Nested Listen

**Resolution:** The outer Listen sees the entries of the inner scope too. The inner Listen captures only its own scope. Entries are never removed.

### 4. Log removed in favor of Tell

**Resolution:** `Log` has been removed. Use `Tell` for all writer effect needs: it appends any object to the log.

## slog

`slog(msg, **kwargs)` creates a `SlogEffect`, a wire type disjoint from `WriterTellEffect` (ADR-DOE-CORE-EFFECTS-001 R1). The default `Listen` does not collect it, and `writer` does not log it. `slog_handler` displays it on stderr; `slog_discard_handler` consumes it silently.

To read `slog` entries as values, pass the type: `Listen(program, types=(SlogEffect,))`, or `types=(WriterTellEffect, SlogEffect)` for both.

## Test Matrix

| Composition | Tested | File |
|-------------|--------|------|
| Listen + Tell | Yes | `docs/03-basic-effects.md` example (run by `tests/test_docs_entry_path_examples.py`); `tests/test_slog_semantics.py` (default `types`) |
| Listen + Local | Yes | `tests/effects/test_effect_combinations.py::TestListenCaptureLaw::test_listen_captures_logs_from_local` |
| Listen + Try (Try inside Listen) | Yes | `tests/effects/test_effect_combinations.py::TestListenCaptureLaw::test_listen_of_try_keeps_entries_before_the_caught_error` |
| Listen + Try (Listen inside Try) | Yes | `tests/effects/test_effect_combinations.py::TestListenCaptureLaw::test_listen_does_not_capture_on_error` |
| Listen + Spawn (spawned inside the scope) | Yes | `tests/effects/test_effect_combinations.py::TestListenCaptureLaw::test_listen_captures_tasks_spawned_inside_its_scope` |
| Listen + Gather (spawned before the scope) | Yes | `tests/effects/test_effect_combinations.py::TestListenCaptureLaw::test_listen_does_not_capture_tasks_spawned_before_its_scope` |
| Listen + Listen (nested) | Yes | `tests/effects/test_effect_combinations.py::TestListenNested` |
| Listen + slog | Yes | `tests/test_slog_semantics.py` |

## References

- Effect definitions: `packages/doeff-core-effects/doeff_core_effects/effects.py`
- Handlers (`writer`, `writer_log`, `listen_handler`): `packages/doeff-core-effects/doeff_core_effects/handlers.py`
- User guide: `docs/03-basic-effects.md` (Listen, Listen Composition Rules)
- Related issue: [#176](https://github.com/proboscis/doeff/issues/176)

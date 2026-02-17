# SPEC-EFF-001: Reader Effects (Ask, Local)

**Status:** Confirmed | **Ref:** gh#174 | **Tests:** `tests/effects/test_reader_effects.py`

## Related Specs

- [SPEC-EFF-014: Cooperative Semaphore](SPEC-EFF-014-semaphore.md)
- [SPEC-SCHED-001: Cooperative Scheduling for the Rust VM](../vm/SPEC-SCHED-001-cooperative-scheduling.md)

## Effects

| Effect | Signature | Description |
|--------|-----------|-------------|
| `Ask(key)` | `Ask(key: Hashable) -> T` | Read from env. Raises `MissingEnvKeyError` if missing |
| `Local(update, prog)` | `Local(Mapping, Program) -> T` | Run prog with modified env, restore after |

## Ask Semantics

### Missing Key
```python
yield Ask("missing")  # Raises MissingEnvKeyError (subclass of KeyError)
```

### Lazy Program Evaluation
If env value is a `Program`, Ask evaluates it and caches the result:

```python
@do
def expensive():
    yield Delay(1.0)
    return 42

env = {"service": expensive()}

val1 = yield Ask("service")  # Evaluates expensive(), caches result
val2 = yield Ask("service")  # Returns cached 42 (no re-evaluation)
```

| Aspect | Behavior |
|--------|----------|
| Scope | Per `runtime.run()` invocation |
| Key | Same as Ask key (any hashable) |
| Invalidation | Local override with different Program object |
| Concurrency | Protected by per-key semaphore (`Semaphore(1)`); simultaneous Ask waits, doesn't re-execute |
| Errors | Program failure = entire `run()` fails |

### Concurrency Contract (Semaphore-based)

For lazy env values, concurrent `Ask(key)` calls across spawned tasks MUST be
coordinated by a per-key semaphore (`Semaphore(1)`) as defined in
[SPEC-EFF-014](SPEC-EFF-014-semaphore.md).

Required behavior:

1. At most one task evaluates a lazy env program for a given key at a time.
2. Other tasks asking the same key wait cooperatively and receive the cached result.
3. Waiting tasks MUST NOT be treated as circular dependencies.
4. Lazy program evaluation remains one-shot per key per run unless invalidated by `Local`.

**Implementation:** #190 (AsyncRuntime), #191 (SyncRuntime), #192 (SimulationRuntime)

## Composition Rules

| Composition | Behavior |
|-------------|----------|
| Local + Ask | Ask sees override inside, original restored after |
| Local + Local | Inner wins, both restore independently (LIFO) |
| Local + Try | Env restored even on error |
| Local + Gather | Children inherit parent env; child's Local isolated |
| Local + State | State (Get/Put) persists outside Local (intentional) |

## References

- Handlers: `doeff/handlers.py`
- Frames: `packages/doeff-vm/src/frame.rs`

# SPEC-EFF-001: Reader Effects (Ask, Local)

**Status:** Confirmed | **Ref:** gh#174 | **Tests:** `tests/effects/test_reader_effects.py`

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

## Composition Rules

| Composition | Behavior |
|-------------|----------|
| Local + Ask | Ask sees override inside, original restored after |
| Local + Local | Inner wins, both restore independently (LIFO) |
| Local + Safe | Env restored even on error |
| Local + Gather | Children inherit parent env; child's Local isolated |
| Local + State | State (Get/Put) persists outside Local (intentional) |

## References

- Handlers: `doeff/cesk/handlers/core.py`
- Frames: `doeff/cesk/frames.py`

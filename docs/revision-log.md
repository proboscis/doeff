# Revision Log

Historical and migration notes are collected here so the main documentation chapters stay focused on
the current architecture and APIs.

## DA3-007 (2026-02-17)

Removed component matrix for the KPC transition (historical reference):

| Removed component | Previous role | Current replacement |
| --- | --- | --- |
| `KPC` effect value type | Represented call intent as an effect payload | `Call` DoCtrl emitted directly by `KleisliProgram.__call__` |
| KPC runtime handler | Interpreted `KPC(...)` through handler dispatch | Standard VM evaluation of `Call(...)` DoExpr |
| KPC-specific dispatch path | Special-case effect-dispatch branch | Call-time macro expansion + regular DoExpr execution |

## DA2-007 (2026-02-17)

Updates for KPC/Intercept documentation gaps:

- Added Intercept transform contract coverage (`None` pass-through, `Effect` replacement,
  `Program` replacement, first non-`None` wins).
- Added explicit KPC non-effect invariant callout in Kleisli docs.
- Added `@do` handler authoring contract details for handler signatures.
- Added varargs auto-unwrap boundary notes for `*args`/`**kwargs`.

KPC migration reminder (historical context):

- The KPC handler is removed. Current architecture uses call-time macro expansion:
  `KleisliProgram.__call__()` returns `Call` DoCtrl directly, and the VM evaluates
  that DoExpr without any KPC-specific handler.

## DA-001 (2026-02-17)

`docs/02-core-concepts.md` was rewritten to present only current architecture.
The following historical topics were moved out of the core chapter:

- legacy `Program` dataclass wrapper representation
- legacy inheritance discussions around `ProgramBase` / `EffectBase(ProgramBase)`
- legacy writer-effect references that used `Log` examples
- legacy KPC-as-effect discussion (superseded by call-time macro model)
- legacy runtime naming references (`ProgramInterpreter`, `ExecutionContext`, `CESKRuntime`)

Current docs should describe the active model directly:

- `Program[T]` as `DoExpr[T]`
- explicit `Perform(effect)` dispatch boundary
- binary `classify_yielded` architecture and current `run` / `async_run` semantics

## DA-002: Writer and Protocol Documentation Cleanup

- Main docs now use the canonical writer API: `Tell`, `StructuredLog`, `slog`, and `Listen`.
- Inline references to deprecated protocol names were removed from core chapters.

## DA2-002 (2026-02-17)

`docs/13-api-reference.md` was realigned to current APIs:

- removed stale `ExecutionContext` API section
- replaced protocol-style effect documentation with the `EffectBase`/`EffectValue` model
- updated stale signatures (`RunResult`, `run`/`async_run`, graph utilities, and effect constructors)

Historical/deprecated protocol-era references are tracked in this revision log instead of inline
API chapters.

## DA-011 (2026-02-17)

`docs/program-architecture-overview.md` was fully rewritten to match:

- SPEC-TYPES-001 Rev 12
- SPEC-008 Rev 14
- SPEC-CORE-001

Archived historical content from the old chapter includes:

- old runtime naming and execution flow references
- old inheritance-centric type modeling notes
- old KPC dispatch framing that predated call-time macro expansion
- old interpreter-loop wording that predated `run` / `async_run` + explicit `Perform` boundary

## Error Handling Migration Notes (Archived)

The following migration guide was moved from `docs/05-error-handling.md`.

### Fail to raise

**Before (dropped):**
```python
yield Fail(ValueError("error"))
```

**After:**
```python
raise ValueError("error")
```

### Catch to Safe

**Before (dropped):**
```python
result = yield Catch(
    risky_operation(),
    lambda e: "fallback"
)
```

**After:**
```python
safe_result = yield Safe(risky_operation())
result = safe_result.ok() if safe_result.is_ok() else "fallback"
```

### Recover to Safe

**Before (dropped):**
```python
data = yield Recover(
    fetch_data(),
    fallback=[]
)
```

**After:**
```python
safe_result = yield Safe(fetch_data())
data = safe_result.ok() if safe_result.is_ok() else []
```

### Retry to manual loop

**Before (dropped):**
```python
result = yield Retry(
    unstable_operation(),
    max_attempts=5,
    delay_ms=100
)
```

**After:**
```python
for attempt in range(5):
    result = yield Safe(unstable_operation())
    if result.is_ok():
        break
    if attempt < 4:
        yield Delay(0.1)
else:
    raise Exception("Max retries exceeded")

final_result = result.ok()
```

### Finally to try/finally

**Before (dropped):**
```python
result = yield Finally(
    use_resource(),
    cleanup_resource()
)
```

**After:**
```python
try:
    result = yield use_resource()
finally:
    yield cleanup_resource()
```

### FirstSuccess to sequential Safe

**Before (dropped):**
```python
result = yield FirstSuccess(
    fetch_from_cache(),
    fetch_from_db(),
    fetch_from_api()
)
```

**After:**
```python
for fetch_fn in [fetch_from_cache, fetch_from_db, fetch_from_api]:
    result = yield Safe(fetch_fn())
    if result.is_ok():
        break
else:
    raise Exception("All sources failed")

final_result = result.ok()
```

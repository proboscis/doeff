# Revision Log

This document keeps historical and migration-only notes. User-facing architecture docs describe
only the current system.

## DA-005 (2026-02-17)

### Archived from `docs/05-error-handling.md`

This section preserves the removed "Migration from Dropped Effects" guidance.

If you're migrating from older doeff versions that used `Fail`, `Catch`, `Recover`, `Retry`,
`Finally`, or `FirstSuccess`, here are the historical migration patterns.

#### Fail to raise

**Before (dropped):**
```python
yield Fail(ValueError("error"))
```

**After:**
```python
raise ValueError("error")
```

#### Catch to Safe

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

#### Recover to Safe

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

#### Retry to Manual Loop

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

#### Finally to try/finally

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

#### FirstSuccess to Sequential Safe

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

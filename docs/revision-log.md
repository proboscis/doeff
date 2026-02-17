# Revision Log

Historical and migration notes that are intentionally excluded from the main chapter docs.

## DA-009 (2026-02-17)

### Error Handling: Migration from Dropped Effects

The following migration mapping was moved from `docs/05-error-handling.md`.

#### Fail to raise

```python
# Before (dropped)
yield Fail(ValueError("error"))

# After
raise ValueError("error")
```

#### Catch to Safe

```python
# Before (dropped)
result = yield Catch(
    risky_operation(),
    lambda e: "fallback"
)

# After
safe_result = yield Safe(risky_operation())
result = safe_result.value if safe_result.is_ok() else "fallback"
```

#### Recover to Safe

```python
# Before (dropped)
data = yield Recover(
    fetch_data(),
    fallback=[]
)

# After
safe_result = yield Safe(fetch_data())
data = safe_result.value if safe_result.is_ok() else []
```

#### Retry to manual loop

```python
# Before (dropped)
result = yield Retry(
    unstable_operation(),
    max_attempts=5,
    delay_ms=100
)

# After
for attempt in range(5):
    result = yield Safe(unstable_operation())
    if result.is_ok():
        break
    if attempt < 4:
        yield Delay(0.1)
else:
    raise Exception("Max retries exceeded")

final_result = result.value
```

#### Finally to try/finally

```python
# Before (dropped)
result = yield Finally(
    use_resource(),
    cleanup_resource()
)

# After
try:
    result = yield use_resource()
finally:
    yield cleanup_resource()
```

#### FirstSuccess to sequential Safe

```python
# Before (dropped)
result = yield FirstSuccess(
    fetch_from_cache(),
    fetch_from_db(),
    fetch_from_api()
)

# After
for fetch_fn in [fetch_from_cache, fetch_from_db, fetch_from_api]:
    result = yield Safe(fetch_fn())
    if result.is_ok():
        break
else:
    raise Exception("All sources failed")

final_result = result.value
```

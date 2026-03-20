# DOEFF032: Prefer Transfer for Tail Resume

## Summary

When a handler is finished and only wants to hand a value to the continuation, prefer:

```python
yield Transfer(k, value)
```

instead of:

```python
return (yield Resume(k, value))
```

`Resume` keeps the handler alive because it may need to receive the continuation result back for
post-processing. In tail position that extra liveness is unnecessary and can retain large locals in
memory longer than needed.

## Violation

```python
@do
def handler(effect: Effect, k: object):
    if isinstance(effect, LoadBigPayload):
        payload = build_payload(effect)
        return (yield Resume(k, payload))
    yield Pass()
```

## Preferred

```python
@do
def handler(effect: Effect, k: object):
    if isinstance(effect, LoadBigPayload):
        payload = build_payload(effect)
        yield Transfer(k, payload)
    yield Pass()
```

## When Not To Use Transfer

Keep `Resume` when the handler truly needs the continuation result:

```python
@do
def handler(effect: Effect, k: object):
    if isinstance(effect, Ping):
        resumed = yield Resume(k, effect.value)
        return resumed * 3
    yield Pass()
```

## Suppression

If the tail `Resume` is intentional, suppress it on that line:

```python
return (yield Resume(k, payload))  # noqa: DOEFF032
```

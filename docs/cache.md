# Cache Effect

This guide explains how to use the cache effect helpers (`CacheGet`, `CachePut`, and the
`@cache` decorator), which persist values with the built-in sqlite handler.

When you decorate a function with `@cache`, the default key is a tuple of the function's fully
qualified module path, positional arguments, and keyword arguments wrapped in `FrozenDict`. Using the
module path (for example `"my_app.services.fetch_user"`) ensures cache hits remain valid across
separate Python processes.

## Cache Policy Fields

`CachePut` accepts either individual policy parameters or a pre-built `CachePolicy`.
All keyword parameters are optional:

- `ttl`: Expiry in seconds. `None` means the entry never expires. Values \<= 0 are treated as
  "no expiry" by the handler.
- `lifecycle`: Hint about how long data should live. Accepts the `CacheLifecycle` enum or the
  strings `"transient"`, `"session"`, and `"persistent"`. Defaults to `TRANSIENT`.
- `storage`: Preferred storage backend hint. Accepts `CacheStorage` enum values or the strings
  `"memory"` and `"disk"`. Defaults to `None`, in which case the lifecycle hint is used to derive
  a suggestion via `CachePolicy.resolved_storage()`.
- `metadata`: Arbitrary mapping that is carried alongside the policy for custom handlers.
- `policy`: Either a `CachePolicy` instance or a mapping. When provided, the individual policy
  parameters above must be omitted.

## Interpreter Support Today

The default `CacheEffectHandler` persists every entry in a sqlite database compressed with LZMA.
The database location can be overridden with the `DOEFF_CACHE_PATH` environment variable; when
absent it falls back to `${TMPDIR}/doeff_cache.sqlite3`.

Currently the handler observes the policy fields as follows:

- `ttl` is enforced. Entries expire once the handler observes a `ttl` > 0 and the deadline passes;
  expired rows are deleted eagerly when accessed.
- `lifecycle` and `storage` are stored on the `CachePolicy` but only serve as hints today.
  Regardless of their values, the bundled handler always uses the sqlite backend (disk storage).
  Custom handlers may inspect `effect.policy` if you need different behaviour.
- `metadata` is preserved on the policy object and made available to custom handlers, but the
  default handler ignores it.

Because the built-in handler always uses sqlite, choosing `CacheLifecycle.PERSISTENT` or
`CacheStorage.DISK` does not change runtime behaviour at the moment; they are intended for
future extensions and for custom handler implementations.

## Usage Example

```python
from doeff import CachePut, CacheGet, CacheLifecycle, CacheStorage, ProgramInterpreter, do

@do
def store_value():
    yield CachePut(
        key=("query", {"user": 1}),
        value={"result": "ok"},
        ttl=300.0,
        lifecycle=CacheLifecycle.SESSION,
        storage=CacheStorage.MEMORY,
        metadata={"source": "demo"},
    )

@do
def load_value():
    return (yield CacheGet(("query", {"user": 1})))

engine = ProgramInterpreter()
await engine.run(store_value())
await engine.run(load_value())
```

The example sets every optional field to demonstrate the accepted values, even though only `ttl`
affects the bundled interpreter today.

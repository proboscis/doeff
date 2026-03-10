# Removed `IO(...)` Effect

`IO(...)` is not part of doeff's current public API.

This page is kept only as a migration note because older docs and external links may still point
here.

## Current Guidance

- Use the effect-specific APIs that exist today: `Ask`, `Local`, `Get`, `Put`, `Tell`, `Await`,
  `Spawn`, `Wait`, `Gather`, `Race`, `Try`, cache effects, graph effects, and semaphore effects.
- For custom handler composition, prefer `WithHandler(handler=..., expr=...)`.
- For builtin runtime behavior, install the sync or async preset with `default_handlers()` or
  `default_async_handlers()`.
- If you need to model a new side effect, define a domain-specific effect type and handle it with
  `WithHandler(...)` instead of reaching for a generic `IO(...)` wrapper.

## Migration from Legacy `IO(...)`

If you still have older examples that use `IO(...)`, rewrite them toward one of these patterns:

- Replace generic wrappers with an existing public effect when one already matches the operation.
- Move concrete side effects behind a custom effect plus handler pair.
- Inject plain Python callables or clients through `Ask(...)` / `Local(...)` when the program should
  remain effectfully structured but the operation itself does not need a dedicated public effect.

## See Also

- [Getting Started](01-getting-started.md)
- [Basic Effects](03-basic-effects.md)
- [Async Effects](04-async-effects.md)
- [API Reference](13-api-reference.md)

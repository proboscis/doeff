# DOEFF031: No Redundant @do Wrapper Entrypoints

## Summary

When a module-level `Program` entrypoint is created by calling a `@do` function that only:

1. yields exactly one call, and
2. forwards parameters directly (e.g. `x=x`), and
3. returns the yielded result,

the wrapper is usually redundant. Prefer creating the entrypoint by calling the underlying program
directly.

## Example

### Violation

```python
@do
def _test_polygon_optimizer_simple(
    text: str,
    max_lines: int,
) -> EffectGenerator[TextLayout]:
    result: TextLayout = yield optimize_text_for_polygon(text=text, max_lines=max_lines)
    return result

p_test_polygon_optimizer_simple: Program[TextLayout] = _test_polygon_optimizer_simple(
    text="Hello",
    max_lines=10,
)
```

### Preferred

```python
p_test_polygon_optimizer_simple: Program[TextLayout] = optimize_text_for_polygon(
    text="Hello",
    max_lines=10,
)
```

## What This Rule Detects

This rule reports when:

- an annotated assignment creates a `Program` / `Program[T]`, and
- the RHS is a keyword-only call to a local `@do` function, and
- that `@do` function is a trivial wrapper around a single `yield`ed call with direct parameter
  forwarding.

## Suppression

If the wrapper exists intentionally (naming/tracing/doc/teaching), suppress on the entrypoint line:

```python
p_test_polygon_optimizer_simple: Program[TextLayout] = _test_polygon_optimizer_simple(
    text="Hello",
    max_lines=10,
)  # noqa: DOEFF031
```


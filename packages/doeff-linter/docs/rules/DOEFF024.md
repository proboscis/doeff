# DOEFF024: No recover with ask Effect

## Overview

Forbid using `recover` (or `Recover`) with `ask` effect. The `ask` effect should fail fast to help users identify missing dependencies.

## Why is this bad?

The `ask` effect is designed to fail fast when a required dependency is missing from the environment. This fail-fast behavior is intentional because:

1. **Clear error messages**: When `ask` fails, it immediately tells you exactly what dependency is missing
2. **Early detection**: Configuration errors are caught at program start, not deep in execution
3. **Explicit dependencies**: Forces code to be explicit about what it needs

When you wrap `ask` in `recover`, you're silently providing a fallback, which:

- **Hides configuration errors**: Missing dependencies go unnoticed
- **Makes debugging harder**: The program continues with unexpected default values
- **Defeats the purpose of `ask`**: If you have a default, why use `ask` at all?

## Examples

### ❌ Bad

```python
@do
def get_bubble_wrap_impl():
    # Bad: recover hides missing dependency
    bubble_wrap_impl = yield recover(
        ask(BUBBLE_WRAP_IMPL_ASK_KEY),
        fallback=lambda _: wrap_text_for_polygon_pure,
    )
    return bubble_wrap_impl
```

```python
@do
def get_config():
    # Bad: using Recover (capitalized) is also forbidden
    config = yield Recover(ask("config_key"), fallback=default_config)
    return config
```

```python
@do
def nested_ask():
    # Bad: even nested ask in recover is detected
    impl = yield recover(some_wrapper(ask("key")), fallback=default)
    return impl
```

### ✅ Good

```python
@do
def get_bubble_wrap_impl():
    # Good: ask fails fast if dependency is missing
    bubble_wrap_impl = yield ask(BUBBLE_WRAP_IMPL_ASK_KEY)
    return bubble_wrap_impl
```

If you need a default value, provide it in the environment when running the program:

```python
# Good: provide defaults at the call site, not in the function
run_program(
    get_bubble_wrap_impl(),
    env={BUBBLE_WRAP_IMPL_ASK_KEY: wrap_text_for_polygon_pure}
)
```

Using `recover` with other operations (not `ask`) is perfectly fine:

```python
@do
def do_something():
    # Good: recover with dangerous operations is fine
    result = yield recover(
        dangerous_network_call(),
        fallback=cached_value,
    )
    return result
```

## Configuration

This rule has no configuration options.

## Related Rules

- [DOEFF018](./DOEFF018.md): No ask in try-except blocks
- [DOEFF019](./DOEFF019.md): No ask with fallback patterns

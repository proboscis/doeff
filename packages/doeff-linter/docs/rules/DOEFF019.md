# DOEFF019: No ask with Fallback Pattern

## Overview

Forbid using `ask` effect with fallback patterns like `arg or (yield ask(...))` or `arg if arg else (yield ask(...))`.

Also forbid calling `ask` with extra arguments like `ask("key", default_value)`. The `ask` effect only accepts one argument (the key) and does not support default values.

## Why is this bad?

### Fallback patterns

When you combine `ask` with a fallback pattern, you create ambiguity about where the value comes from. This makes the code harder to reason about and increases complexity.

The `ask` effect should be the **ONLY** way to obtain the value. If a value can come from multiple sources (function argument OR `ask`), it becomes unclear:
- Where does this value actually come from?
- What's the expected behavior when the argument is provided vs not provided?
- Who is responsible for providing this configuration?

### Extra arguments (default values)

The `ask` effect is designed to only accept a key and does not support default values. If you try to call `ask("key", 0)`, you are misusing the API. doeff intentionally does not support defaults for `ask` because:

- If the key is not found in the environment, it should be treated as a **coding mistake** that needs to be fixed
- Defaults hide missing dependencies and make bugs harder to find
- Using `ask` should mean "this value MUST be provided"

## Examples

### ❌ Bad

```python
@do
def do_something(arg=None):
    # Bad: arg can come from function argument OR ask
    arg = arg or (yield ask("arg_key"))
    return process(arg)
```

```python
@do
def get_config(override=None):
    # Bad: ternary expression with ask fallback
    config = override if override else (yield ask("config"))
    return config
```

```python
@do
def process_data(data=None, config=None):
    # Bad: multiple fallback patterns
    data = data or (yield ask("data"))
    config = config or (yield ask("config"))
    return transform(data, config)
```

```python
@do
def get_value():
    # Bad: ask does not support default values
    value = yield ask("key", 0)
    return value
```

```python
@do
def get_config():
    # Bad: keyword argument for default is also not supported
    config = yield ask("config", default=None)
    return config
```

### ✅ Good

```python
@do
def do_something():
    # Good: ask is the single source of truth
    arg = yield ask("arg_key")
    return process(arg)
```

```python
@do
def get_config():
    # Good: clear single source, only key argument
    config = yield ask("config")
    return config
```

```python
@do
def process_data():
    # Good: all dependencies come from ask
    data = yield ask("data")
    config = yield ask("config")
    return transform(data, config)
```

If you need conditional behavior, use an explicit `if` statement instead:

```python
@do
def conditional_ask(should_use_default: bool):
    # Good: explicit branching, clear intent
    if should_use_default:
        value = "default"
    else:
        value = yield ask("key")
    return value
```

If you truly need an optional value, ensure it's properly provided in the environment rather than using defaults:

```python
@do
def use_optional_config():
    # Good: provide the value in the environment, not as a default
    # The caller is responsible for providing "optional_key" if needed
    config = yield ask("optional_key")
    return config
```

## Configuration

This rule has no configuration options.

## Related Rules

- [DOEFF018](./DOEFF018.md): No ask in try-except blocks


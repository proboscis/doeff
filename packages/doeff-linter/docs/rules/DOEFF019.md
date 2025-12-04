# DOEFF019: No ask with Fallback Pattern

## Overview

Forbid using `ask` effect with fallback patterns like `arg or (yield ask(...))` or `arg if arg else (yield ask(...))`.

## Why is this bad?

When you combine `ask` with a fallback pattern, you create ambiguity about where the value comes from. This makes the code harder to reason about and increases complexity.

The `ask` effect should be the **ONLY** way to obtain the value. If a value can come from multiple sources (function argument OR `ask`), it becomes unclear:
- Where does this value actually come from?
- What's the expected behavior when the argument is provided vs not provided?
- Who is responsible for providing this configuration?

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
    # Good: clear single source
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

## Configuration

This rule has no configuration options.

## Related Rules

- [DOEFF018](./DOEFF018.md): No ask in try-except blocks


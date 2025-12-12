# DOEFF020: Program Naming Convention

## Summary

`Program` type variables should use the `p_` prefix. The `_program` suffix is deprecated.

## Why This Matters

Consistent naming conventions improve code readability and make it easier to identify `Program` types at a glance. The `p_` prefix convention:

1. **Brevity**: `p_data` is shorter than `data_program`
2. **Consistency**: All Program variables start with `p_`, making them easy to spot
3. **Grep-ability**: Easy to search for all Program entrypoints with `p_*` pattern
4. **IDE Support**: Autocomplete shows all Programs when typing `p_`

### Why Not `_program` Suffix?

The `_program` suffix was the original convention but has several drawbacks:

- **Verbose**: `user_authentication_program` vs `p_user_authentication`
- **Inconsistent sorting**: Programs get mixed with other variables in autocomplete
- **Harder to scan**: The important part (that it's a Program) comes at the end

## What This Rule Detects

### `_program` Suffix (Warning Level)

```python
# ❌ Bad: Uses deprecated _program suffix
data_program: Program = load_data(path=Path("data.json"))
some_program: Program[int] = compute()
fetch_program: Program[Result] = fetch_data(url="https://api.example.com")
```

### Other Non-Prefixed Names (Info Level)

```python
# ⚠️ Info: Should use p_ prefix
my_task: Program = run_task()
pipeline: Program[int] = build_pipeline()
```

## Recommended Fixes

### Use `p_` Prefix

```python
# ✅ Good: Uses p_ prefix
p_data: Program = load_data(path=Path("data.json"))
p_result: Program[int] = compute()
p_fetch: Program[Result] = fetch_data(url="https://api.example.com")
p_task: Program = run_task()
p_pipeline: Program[int] = build_pipeline()
```

### Migration Examples

```python
# Before (deprecated)
data_program: Program = load_data()
user_program: Program[User] = fetch_user(id=42)
complex_pipeline_program: Program[DataFrame] = build_pipeline()

# After (recommended)
p_data: Program = load_data()
p_user: Program[User] = fetch_user(id=42)
p_complex_pipeline: Program[DataFrame] = build_pipeline()
```

## Allowed Patterns

The rule does NOT flag these patterns:

```python
# ✅ p_ prefix is correct
p_data: Program = load_data(path=Path("data.json"))
p_result: Program[int] = compute()
p_user: Program[User] = fetch_user(id=42)

# ✅ Non-Program types are ignored
data_program: int = 42  # Not a Program type
my_pipeline: list = []  # Not a Program type

# ✅ Assignments without type annotations are ignored
data_program = load_data()  # No annotation, not checked
```

## Severity

- **Warning** - for `_program` suffix (deprecated naming pattern)
- **Info** - for other non-prefixed names (style suggestion)

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF020"]
```

## Rationale

The `p_` prefix convention is similar to other established naming patterns:

- `i_` for interfaces (in some languages)
- `s_` for static variables
- `m_` for member variables

The prefix makes the type immediately visible without having to look at the type annotation, which is especially useful when reading code quickly or when type annotations are not displayed.

## See Also

- DOEFF015: No Zero-Argument Program Entrypoints (related: Program entrypoint design)
- [doeff Program Architecture](../../program-architecture-overview.md)


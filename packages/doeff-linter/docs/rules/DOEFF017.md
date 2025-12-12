# DOEFF017: No Program Type in Function Parameters

## Summary

`@do` functions typically accept the underlying type T, not `Program[T]`. By default, doeff automatically resolves `Program[T]` arguments before executing the function body.

**Important**: If you explicitly annotate a parameter as `Program[T]`, the `@do` wrapper will **NOT** auto-unwrap the passed Program object. This behavior is intentional and useful for writing Program transforms.

## Why This Matters

In doeff's Pipeline Oriented Programming paradigm, `@do` decorated functions (KleisliPrograms) are building blocks that transform data. When you call a `@do` function with a `Program[T]` argument:

### Default Behavior (parameter annotated as `T`)

1. **Resolves the Program**: Executes the `Program[T]` to get the underlying `T` value
2. **Passes the resolved value**: The function receives `T`, not `Program[T]`
3. **Chains the computation**: The function's result is wrapped in a new `Program`

### Explicit Program Behavior (parameter annotated as `Program[T]`)

When you annotate a parameter as `Program[T]`, the `@do` wrapper **skips auto-unwrapping**:

1. **Passes the Program directly**: The function receives the `Program[T]` object itself
2. **Enables Program transforms**: Useful for `Program -> Program` functions
3. **Allows composition without resolution**: You can manipulate Programs without executing them

## What This Rule Detects

### Program Type in @do Function Parameters

```python
# ⚠️ Warning: Parameter type is Program[DataFrame]
@do
def process(data: Program[DataFrame]) -> EffectGenerator[Result]:
    # data is a Program[DataFrame], NOT a DataFrame!
    # The @do wrapper does NOT auto-unwrap because of the annotation
    ...

# ⚠️ Warning: Program without type parameter
@do
def process(data: Program) -> EffectGenerator[Result]:
    ...
```

## When This Pattern Is Intentional

### Writing Program Transforms (Program -> Program)

If you're explicitly writing a function that transforms Programs without resolving them, the `Program[T]` annotation is correct:

```python
# ✅ Intentional: Program transform function
@do
def add_retry(p: Program[T]) -> EffectGenerator[Program[T]]:  # noqa: DOEFF017
    """Wrap a program with retry logic without executing it."""
    return p.recover(lambda e: p)  # Returns modified Program

# ✅ Intentional: Composing Programs
@do
def combine_programs(
    p1: Program[DataFrame],  # noqa: DOEFF017
    p2: Program[Config],     # noqa: DOEFF017
) -> EffectGenerator[Program[Result]]:
    """Combine programs without resolving them first."""
    return p1.flat_map(lambda df: p2.map(lambda cfg: process(df, cfg)))
```

In these cases, suppress the warning with `# noqa: DOEFF017`.

## Recommended Fixes (For Typical Use Cases)

### Use the Underlying Type Directly

```python
# ✅ Good: Parameter type is the underlying DataFrame
@do
def process(data: DataFrame) -> EffectGenerator[Result]:
    # data is a DataFrame, annotation matches actual type
    result = data.transform()
    return result

# ✅ Good: Multiple parameters with underlying types
@do
def transform(source: DataFrame, config: Config) -> EffectGenerator[Result]:
    ...
```

### How doeff Handles Program Resolution

```python
# When you have these programs:
p_data: Program[DataFrame] = load_data(path=Path("data.csv"))
p_config: Program[Config] = load_config(env="production")

# And this @do function (with underlying types):
@do
def process(data: DataFrame, config: Config) -> EffectGenerator[Result]:
    ...

# You can call it with Program arguments:
p_result: Program[Result] = process(data=p_data, config=p_config)

# doeff automatically:
# 1. Resolves p_data to get DataFrame
# 2. Resolves p_config to get Config
# 3. Calls process(data=<DataFrame>, config=<Config>)
# 4. Wraps the result in Program[Result]
```

## Allowed Patterns

The rule does NOT flag these patterns:

```python
# ✅ Non-@do functions can use Program types freely
def helper(data: Program[DataFrame]) -> Program[Result]:
    return data.map(transform)

# ✅ Regular types in @do functions are fine
@do
def process(data: DataFrame, threshold: float) -> EffectGenerator[Result]:
    ...

# ✅ No type annotation is fine (though not recommended)
@do
def process(data) -> EffectGenerator[Result]:
    ...
```

## Async Functions

The rule also applies to async @do functions:

```python
# ⚠️ Warning
@do
async def fetch_data(url: Program[str]) -> EffectGenerator[bytes]:
    ...

# ✅ Good (typical case)
@do
async def fetch_data(url: str) -> EffectGenerator[bytes]:
    ...
```

## Severity

**Warning** - This pattern may indicate misunderstanding of doeff's automatic Program resolution, or may be intentional for Program transforms. Review and suppress if intentional.

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF017"]
```

Or use inline suppression for intentional Program transforms:

```python
@do
def transform_program(p: Program[DataFrame]) -> EffectGenerator[Program[Result]]:  # noqa: DOEFF017
    """Intentionally working with Program objects."""
    ...
```

## Background: @do Function Semantics

In doeff, `@do` decorated functions have special argument handling:

| Parameter Annotation | Behavior |
|---------------------|----------|
| `param: T` | Auto-unwraps `Program[T]` arguments to `T` |
| `param: Program[T]` | Passes `Program[T]` directly (no unwrap) |

This annotation-based behavior allows you to:
- **Default case**: Write functions that work with resolved values
- **Advanced case**: Write Program transforms that manipulate Programs without resolving them

## Related Rules

- [DOEFF015: No Zero-Argument Program Entrypoints](DOEFF015.md) - Related: Program creation patterns
- [DOEFF020: Program Naming Convention](DOEFF020.md) - Related: Proper naming for Program variables

## See Also

- [doeff Program Architecture](../../docs/program-architecture-overview.md)
- [Kleisli Arrows Documentation](../../docs/11-kleisli-arrows.md)

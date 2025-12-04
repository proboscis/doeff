# DOEFF015: No Zero-Argument Program Entrypoints

## Summary

Program entrypoints should not be created by calling zero-argument factory functions. Pass explicit arguments to make the entrypoint's configuration visible and reviewable.

## Why This Matters

In doeff, `Program` type global variables serve as static entrypoints that should have predictable, transparent behavior. The Pipeline Oriented Programming paradigm requires:

1. **Transparency**: An entrypoint's behavior should be obvious from looking at its definition
2. **Version Control**: Configuration changes should be trackable in commit history
3. **Reproducibility**: The same entrypoint should always produce the same behavior
4. **Testability**: Creating multiple variants with different parameters should be easy

### The Problem with Zero-Argument Factory Functions

```python
# ❌ Bad: Zero-argument factory hides configuration
p_pipeline: Program = _create_complex_pipeline()
```

This pattern causes several issues:

- **Hidden Configuration**: What parameters does this pipeline use? You must read the factory function to find out
- **Difficult Change Tracking**: Changes inside `_create_complex_pipeline()` silently affect `p_pipeline`
- **No Reusability**: Can't create variants with different settings without modifying or duplicating the factory
- **Code Review Difficulty**: Reviewers must trace through function calls to understand behavior

## What This Rule Detects

### Zero-Argument Function Calls Assigned to Program Type

```python
# ❌ Bad: All of these are flagged
p_data: Program = create_pipeline()
p_result: Program[int] = build_program()
p_complex: Program[DataFrame] = _internal_factory()
p_test_optimizer_lettering: Program = _create_sample_lettering_pipeline()
```

## Recommended Fixes

### Use @do Functions with Explicit Arguments

```python
# ✅ Good: Configuration is visible at the entrypoint definition
@do
def process_data(input_data: Data, threshold: float) -> EffectGenerator[Result]:
    # Processing logic here
    ...

# Each entrypoint's configuration is immediately visible
p_small_test: Program[Result] = process_data(input_data=test_data, threshold=0.1)
p_production: Program[Result] = process_data(input_data=prod_data, threshold=0.5)
p_strict_mode: Program[Result] = process_data(input_data=prod_data, threshold=0.9)
```

### Use Program.pure() for Constant Values

```python
# ✅ Good: Direct value construction
p_config: Program[Config] = Program.pure(Config(api_key="xxx", timeout=30))
p_constant: Program[int] = Program.pure(42)
```

### Use Program.pure(func) Pattern for Pure Functions

```python
# ✅ Good: Pure function with explicit arguments
p_result: Program[int] = Program.pure(compute_value)(x=10, y=20)
```

### Refactor Factory Functions to Accept Parameters

```python
# Before (bad):
def _create_pipeline() -> Program[Result]:
    config = Config(threshold=0.5, mode="fast")
    return process_data(config=config)

p_pipeline: Program = _create_pipeline()

# After (good):
@do
def process_data(threshold: float, mode: str) -> EffectGenerator[Result]:
    ...

# Configuration is now visible at the entrypoint
p_pipeline: Program[Result] = process_data(threshold=0.5, mode="fast")
```

## Allowed Patterns

The rule does NOT flag these patterns:

```python
# ✅ Program static methods are allowed
p_pure: Program[int] = Program.pure(42)
p_fail: Program[int] = Program.fail(ValueError("error"))
p_first: Program[int] = Program.first_success(p1, p2, p3)

# ✅ Function calls with any arguments are allowed
p_with_args: Program = create_pipeline(config=Config())
p_with_positional: Program[int] = do_task(42)
p_with_kwarg: Program = factory(name="test")

# ✅ Non-Program types are ignored
regular_var: int = some_func()
data: list = load_data()

# ✅ Assignments without type annotations are ignored
p_data = create_pipeline()  # No annotation, not checked
```

## Severity

**Warning** - This is a design guideline violation that affects code maintainability and reviewability.

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF015"]
```

## Background: Pipeline Oriented Programming

doeff follows a Pipeline Oriented Programming paradigm where:

1. **@do functions** (KleisliPrograms) are reusable building blocks that accept parameters
2. **Program entrypoints** are static instances created by calling @do functions with specific arguments
3. **Configuration is explicit** at the point where entrypoints are defined, not hidden in factory functions

This design enables:

- Running any entrypoint via `doeff run --program module.entrypoint_name`
- IDE integration that can click-to-run any `Program` global variable
- Clear audit trail of what each entrypoint does without tracing through factories
- Easy creation of test/production/variant entrypoints with different parameters

## Related Patterns

- [doeff Program Architecture](../../program-architecture-overview.md)
- [Pipeline Oriented Programming](../../docs/12-patterns.md)

## See Also

- DOEFF004: No os.environ Access (related: configuration should be explicit, not from environment)
- DOEFF011: No Flag Arguments (related: use explicit strategies instead of mode flags)


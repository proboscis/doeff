# DOEFF023: Pipeline Marker Required for Entrypoint @do Functions

## Summary

When a `@do` decorated function is used to create a module-level `Program` variable (entrypoint), it must have the `# doeff: pipeline` marker. This ensures developers are consciously following pipeline-oriented programming.

## Why This Matters

In doeff, pipeline-oriented programming is the recommended paradigm:

1. **Intermediate Programs as Global Variables**: Each processing step should be exposed as a global `Program` variable
2. **Composition over Wrapping**: Build complex pipelines by composing simple Programs, not by wrapping them in new functions
3. **Explicit Acknowledgment**: The marker forces developers to consciously adopt the pipeline pattern
4. **Code Review**: Reviewers can easily identify pipeline-aware code by the presence of markers

### The Anti-Pattern

```python
# Bad: Single-use wrapper function without pipeline awareness
@do
def _run_test_optimizer(*, test_input: OptimizationInput) -> EffectGenerator[OptimizationOutput]:
    yield slog(msg="Running...")
    result = yield optimize_page_layout(test_input)
    yield slog(msg="Complete!")
    return result

p_test_optimizer: Program[OptimizationOutput] = _run_test_optimizer(test_input=_TEST_INPUT)
```

This pattern hides intermediate steps inside a wrapper function instead of exposing them as composable Program variables.

### The Correct Pattern

```python
# Good: Pipeline-oriented with explicit marker
@do  # doeff: pipeline
def process_optimization(input_data: OptimizationInput) -> EffectGenerator[OptimizationOutput]:
    return (yield optimize_page_layout(input_data))

# Intermediate Programs exposed as global variables
p_input: Program[OptimizationInput] = Program.pure(_TEST_INPUT)
p_result: Program[OptimizationOutput] = process_optimization(p_input)
p_logged: Program[OptimizationOutput] = log_result(p_result)
```

## What This Rule Detects

The rule flags `@do` functions that:
1. Are used to create a module-level `Program[T]` type variable
2. Do NOT have the `# doeff: pipeline` marker

```python
# Violation: Missing pipeline marker
@do
def process_x(data) -> EffectGenerator[Y]:
    return Y(data)

p_result: Program[Y] = process_x(p_data)  # <- This triggers the violation
```

## Marker Placement Options

The marker can be placed in any of these three locations:

### Option 1: After @do Decorator

```python
@do  # doeff: pipeline
def process_x(data) -> EffectGenerator[Y]:
    return Y(data)
```

### Option 2: After def Line

```python
@do
def process_x(data) -> EffectGenerator[Y]:  # doeff: pipeline
    return Y(data)
```

### Option 3: In Docstring

```python
@do
def process_x(data) -> EffectGenerator[Y]:
    """doeff: pipeline"""
    return Y(data)
```

Or in a multi-line docstring:

```python
@do
def process_x(data) -> EffectGenerator[Y]:
    """
    Process data through the pipeline.
    
    doeff: pipeline
    """
    return Y(data)
```

## Allowed Patterns (Skipped by This Rule)

The rule does NOT flag these patterns:

```python
# Functions not used to create Program variables
@do
def helper_func(data):
    return process(data)

result = helper_func(data)  # Not a Program type assignment

# Non-@do functions
def regular_func(data):
    return data

p_result: Program = regular_func(data)  # Not a @do function

# Test files
# test_*.py and *_test.py files are skipped entirely
```

## Severity

**Warning** - This rule helps enforce pipeline-oriented programming conventions but can be suppressed for legitimate cases.

## Configuration

This rule is enabled by default but can be disabled:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF023"]
```

## Suppressing the Rule

If a function intentionally doesn't follow the pipeline pattern, add a noqa comment:

```python
p_legacy: Program = old_style_func(data)  # noqa: DOEFF023
```

## Best Practices

1. **Add the marker consciously**: Don't just add it to silence the linter; ensure you understand pipeline-oriented programming
2. **Expose intermediates**: When you add the marker, consider if you should expose intermediate Programs as global variables
3. **Document the pipeline**: Use the docstring marker option to also document what the function does in the pipeline

## Related Rules

- **DOEFF015**: No Zero-Argument Program Entrypoints (related: Program construction patterns)
- **DOEFF020**: Program Naming Convention (related: `p_` prefix for Program variables)
- **DOEFF022**: Prefer @do Decorated Functions (related: @do usage)

## See Also

- [Pipeline Oriented Programming Guidelines](../../docs/12-patterns.md)
- [Program Architecture Overview](../../docs/program-architecture-overview.md)
- [Core Concepts](../../docs/02-core-concepts.md)



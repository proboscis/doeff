# DOEFF030: Ask Result Must Be Type Annotated

## Overview

When you bind the result of `yield ask(...)` to a variable, the assignment must use an inline type
annotation:

```python
some_flag: bool = yield ask("project.a.use_b")
```

Additionally, when `ask` is used to request a callable/KleisliProgram, you must:

1. Define a `Protocol` describing the callable signature.
2. Use that `Protocol` as both the annotation and the `ask` key.
3. Provide an implementation decorated with `@impl(Protocol)`.

## Why is this bad?

### Missing annotation

`ask` yields values from dependency injection. Their types are not inferable at the call site, so
binding them without an annotation reduces type safety and makes the dependency unclear during code
review.

### Callable injection without Protocol/@impl

Using a `Protocol` for injected callables makes the expected signature explicit and stable. The
`@impl(Protocol)` decorator marks provider functions clearly, allowing readers and tooling to trace
injected implementations. The `ask` key must be that `Protocol` so the DI contract is unambiguous.

## Examples

### ❌ Bad

```python
@do
def feature_gate():
    # Bad: missing inline type annotation
    enabled = yield ask("project.a.use_b")
    return enabled
```

```python
from typing import Callable

@do
def upload_flow():
    # Bad: asking for a callable without Protocol contract
    uploader: Callable = yield ask("uploader")
    return yield uploader(b"...")
```

```python
class UploadFunc:
    def __call__(self, bin): ...

@do
def upload_flow():
    # Bad: key looks like a type but it's not a Protocol and has no @impl
    uploader: UploadFunc = yield ask(UploadFunc)
    return yield uploader(b"...")
```

### ✅ Good

```python
@do
def feature_gate():
    enabled: bool = yield ask("project.a.use_b")
    return enabled
```

```python
from typing import Protocol

class UploadFunc(Protocol):
    def __call__(self, bin): ...

@do
def upload_flow():
    uploader: UploadFunc = yield ask(UploadFunc)
    return yield uploader(b"...")

@impl(UploadFunc)
@do
def upload_gcs(bin):
    ...
```

## Configuration

This rule has no configuration options.

## Related Rules

- [DOEFF018](./DOEFF018.md): No ask in try-except blocks
- [DOEFF019](./DOEFF019.md): No ask with fallback patterns
- [DOEFF024](./DOEFF024.md): No recover with ask


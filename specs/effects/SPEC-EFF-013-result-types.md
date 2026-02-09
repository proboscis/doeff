# SPEC-EFF-013: Rust Result Types (Ok/Err)

## Status: Draft

## Summary

Defines the canonical `Ok` and `Err` types implemented as Rust `#[pyclass]`
structs and exposed to Python. These types represent success/failure outcomes
throughout the doeff system: `RunResult.result`, `TaskCompleted` results,
`Safe` wrapper outputs, and user-level pattern matching.

## Motivation

The doeff system has two separate Ok/Err hierarchies:

1. **Python dataclasses** (`doeff._vendor.Ok`, `doeff._vendor.Err`) — used by
   `Safe`, user code pattern matching, and the public API.
2. **Rust `#[pyclass]` structs** (`PyResultOk`, `PyResultErr`) — used by
   `RunResult.result` in the VM.

This creates problems:
- `isinstance(result, Ok)` behaves differently depending on which `Ok` was
  imported.
- The scheduler's `TaskCompleted` effect uses `value: Option<PyObject>` /
  `error: Option<PyObject>` as a workaround because Rust can't distinguish
  the Python `Ok`/`Err` dataclasses without GIL isinstance checks.
- `Safe` constructs Python `Ok`/`Err`, but `RunResult` constructs Rust
  `Ok`/`Err` — a Gather over Safe-wrapped tasks could return a mix of both
  types.

This spec unifies them into a single Rust-backed implementation.

## Design

### Rust Structs

```rust
#[pyclass(frozen, name = "Ok")]
pub struct PyResultOk {
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pyclass(frozen, name = "Err")]
pub struct PyResultErr {
    #[pyo3(get)]
    pub error: Py<PyAny>,
    #[pyo3(get)]
    pub captured_traceback: Py<PyAny>,  // Maybe[EffectTraceback] — None if not captured
}
```

### Python API

Both types are registered in the `doeff_vm` module and re-exported as
`doeff.Ok` and `doeff.Err`. The Python dataclass versions in `_vendor.py`
are replaced.

```python
from doeff import Ok, Err

# Construction
ok = Ok(42)
err = Err(ValueError("boom"))
err_with_tb = Err(ValueError("boom"), captured_traceback=tb)

# Attribute access
ok.value        # 42
err.error       # ValueError("boom")
err.captured_traceback  # None or EffectTraceback

# Pattern matching (Python 3.10+)
match result:
    case Ok(value=v):   print(f"success: {v}")
    case Err(error=e):  print(f"failure: {e}")

# Boolean coercion
bool(Ok(42))     # True
bool(Err(...))   # False

# isinstance
isinstance(Ok(42), Ok)    # True
isinstance(Err(...), Err) # True
```

### Constructor Signatures

```rust
#[pymethods]
impl PyResultOk {
    #[new]
    fn new(value: Py<PyAny>) -> Self {
        PyResultOk { value }
    }
}

#[pymethods]
impl PyResultErr {
    #[new]
    #[pyo3(signature = (error, captured_traceback=None))]
    fn new(py: Python<'_>, error: Py<PyAny>, captured_traceback: Option<Py<PyAny>>) -> Self {
        PyResultErr {
            error,
            captured_traceback: captured_traceback.unwrap_or_else(|| py.None()),
        }
    }
}
```

### Rust-Side Discrimination

From Rust, the VM and scheduler distinguish Ok/Err with a single extract:

```rust
fn is_ok(obj: &Bound<'_, PyAny>) -> bool {
    obj.extract::<PyRef<'_, PyResultOk>>().is_ok()
}

fn extract_result(obj: &Bound<'_, PyAny>) -> Result<Py<PyAny>, Py<PyAny>> {
    if let Ok(ok) = obj.extract::<PyRef<'_, PyResultOk>>() {
        Ok(ok.value.clone_ref(obj.py()))
    } else if let Ok(err) = obj.extract::<PyRef<'_, PyResultErr>>() {
        Err(err.error.clone_ref(obj.py()))
    } else {
        panic!("expected Ok or Err, got {}", obj.get_type().name().unwrap())
    }
}
```

This is a single `extract` call (one isinstance check), not an
`is_none()`-based two-field probe.

## Integration Points

### TaskCompleted (SPEC-SCHED-001)

With canonical Rust Ok/Err, `TaskCompleted` returns to a **single result
field**:

```rust
#[pyclass(frozen, name = "_SchedulerTaskCompleted", extends=PyEffectBase)]
pub struct PyTaskCompleted {
    #[pyo3(get)]
    pub task_id: u64,
    #[pyo3(get)]
    pub result: Py<PyAny>,  // Always Ok(...) or Err(...)
}
```

The envelope constructs Ok/Err directly:

```python
def _scheduler_envelope(gen, task_id):
    result = None
    try:
        while True:
            do_expr = gen.send(result)
            result = yield do_expr
            _ = yield Perform(SchedulerYield(task_id))
    except StopIteration as e:
        yield Perform(TaskCompleted(task_id, result=Ok(e.value)))
    except Exception as e:
        yield Perform(TaskCompleted(task_id, result=Err(e)))
```

The scheduler handler extracts the result from Rust:

```rust
SchedulerEffect::TaskCompleted { task, result } => {
    let obj = result.bind(py);
    let completion = if let Ok(ok) = obj.extract::<PyRef<'_, PyResultOk>>() {
        Ok(Value::from_pyobject(ok.value.bind(py)))
    } else if let Ok(err) = obj.extract::<PyRef<'_, PyResultErr>>() {
        Err(pyobject_to_exception(py, err.error.bind(py)))
    } else {
        return RustProgramStep::Throw(PyException::type_error(
            "TaskCompleted.result must be Ok or Err".into()
        ));
    };
    // ... mark_task_done(task, completion)
}
```

### RunResult (SPEC-008)

No change. `RunResult.result` already returns `PyResultOk`/`PyResultErr`.
After unification, these are the same types that `Safe` and `TaskCompleted`
use.

### Safe (SPEC-EFF-012)

`Safe` wraps sub-program results in `Ok`/`Err`. After unification, it
imports from `doeff_vm` (Rust) instead of constructing Python dataclasses:

```python
from doeff import Ok, Err  # re-exports from doeff_vm

def _wrap_kernel_as_result(execution_kernel):
    def wrapped_kernel(*args, **kwargs):
        try:
            gen_or_value = execution_kernel(*args, **kwargs)
        except Exception as exc:
            return Err(exc)
        if not inspect.isgenerator(gen_or_value):
            return Ok(gen_or_value)
        # ... generator forwarding with Ok/Err wrapping
    return wrapped_kernel
```

## Migration

### Phase 1: Add `captured_traceback` to Rust `PyResultErr`

The existing Rust `PyResultErr` has only `error`. Add the optional
`captured_traceback` field to match the Python `Err` dataclass API.

### Phase 2: Re-export from `doeff`

Update `doeff/__init__.py` and `doeff/types.py` to re-export `Ok`/`Err`
from `doeff_vm` instead of from `doeff._vendor`.

### Phase 3: Remove Python dataclass versions

Remove `class Ok` and `class Err` from `doeff/_vendor.py`. Update `Safe`
and any other code that constructs these types directly.

### Phase 4: Simplify TaskCompleted

Replace the two-field (`value`/`error`) `TaskCompleted` with a single
`result: Py<PyAny>` field that is always `Ok(...)` or `Err(...)`.

## `Result` Base Class

The Python `Result` base class (`doeff._vendor.Result`) is used for type
annotations (`Result[T]`). Since Rust `#[pyclass]` cannot extend Python
generics, the base class stays as a Python abstract type:

```python
class Result(Generic[T_co]):
    __slots__ = ()
    def is_ok(self) -> bool:
        return isinstance(self, Ok)
```

`Ok` and `Err` do NOT inherit from `Result` in Rust. Instead, `is_ok()`
and `is_err()` methods are provided directly on the Rust types. Type
checkers see the relationship via `TYPE_CHECKING` stubs:

```python
if TYPE_CHECKING:
    class Ok(Result[T], Generic[T]): ...
    class Err(Result[NoReturn]): ...
```

At runtime, `Ok` and `Err` are the Rust `#[pyclass]` types with no Python
base class.

## Properties

- **Single type**: One `Ok`, one `Err` — no dual-hierarchy confusion.
- **Rust-constructible**: The VM and scheduler can create Ok/Err without
  calling Python constructors.
- **Rust-discriminable**: `extract::<PyRef<PyResultOk>>()` — one isinstance
  check, no GIL contention on frozen structs.
- **Pattern-matchable**: Python `match` works via `__match_args__`.
- **Bool-coercible**: `Ok` is truthy, `Err` is falsy.
- **Backward-compatible**: Same attribute names (`value`, `error`,
  `captured_traceback`) as the Python dataclass versions.

## `__match_args__` and `__eq__`

```rust
#[pymethods]
impl PyResultOk {
    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("value",)
    }

    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> PyResult<bool> {
        if let Ok(other_ok) = other.extract::<PyRef<'_, PyResultOk>>() {
            self.value.bind(py).eq(other_ok.value.bind(py))
        } else {
            Ok(false)
        }
    }

    fn __hash__(&self, py: Python<'_>) -> PyResult<isize> {
        self.value.bind(py).hash()
    }
}

#[pymethods]
impl PyResultErr {
    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("error",)
    }

    // Err is not hashable (exceptions are not hashable)
}
```

## Related Specs

| Spec | Relationship |
|------|-------------|
| SPEC-008 | `RunResult.result` returns Ok/Err |
| SPEC-SCHED-001 | `TaskCompleted.result` is Ok/Err |
| SPEC-EFF-012 | `Safe` wraps sub-program results in Ok/Err |

## Location

`packages/doeff-vm/src/pyvm.rs` — `PyResultOk`, `PyResultErr`
`doeff/types.py` — re-exports

//! Runtime half of doeff's static typing (the static half is `doeff_vm/__init__.pyi`).
//!
//! - `__iter__` on `EffectBase` and every DoExpr node returns
//!   `doeff_vm._bind.bind(self)`, a one-step generator that yields the node to
//!   the VM and returns the value the VM sends back. `x = yield from eff`
//!   therefore evaluates exactly like `x = yield eff`, while a type checker
//!   reads `x`'s type from `EffectBase[T]` / `Expand[T, E]`.
//! - `__class_getitem__` makes `EffectBase[int]`, `Expand[int, E]`, ... valid at
//!   runtime (a `types.GenericAlias`), so effect classes can be declared as
//!   `class ReadClock(EffectBase[int])` and annotations can be evaluated.
//!
//! The generator lives in Python on purpose: CPython's `yield from` over a
//! Python generator returns the sent value without raising StopIteration.

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyType;

static BIND: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static GENERIC_ALIAS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

pub fn bind_iter<'py>(node: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = node.py();
    let bind = BIND.get_or_try_init(py, || {
        py.import("doeff_vm._bind")
            .and_then(|m| m.getattr("bind"))
            .map(Bound::unbind)
    })?;
    bind.bind(py).call1((node,))
}

pub fn class_getitem<'py>(
    cls: &Bound<'py, PyType>,
    item: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = cls.py();
    let generic_alias = GENERIC_ALIAS.get_or_try_init(py, || {
        py.import("types")
            .and_then(|m| m.getattr("GenericAlias"))
            .map(Bound::unbind)
    })?;
    generic_alias.bind(py).call1((cls, item))
}

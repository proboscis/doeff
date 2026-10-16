//! The `hint:` line an UnhandledEffect message ends with.
//!
//! The VM knows no handler, so the line comes from Python: a package that ships
//! handlers registers a function from an effect to the line naming the handler and
//! how to install it (`doeff_vm._unhandled_hint`); the core effects' table is
//! `doeff_core_effects.unhandled_hints`.

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;

use doeff_vm_core::value::Value;

static HINT_FOR: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The hint line for an effect no handler took, or None when none is registered for
/// it. A failing lookup becomes the line itself, so it neither hides the original
/// error nor goes unseen.
pub fn unhandled_hint(py: Python<'_>, effect: &Value) -> Option<String> {
    let Value::Opaque(shared) = effect else {
        return None;
    };
    let looked_up = HINT_FOR
        .get_or_try_init(py, || {
            py.import("doeff_vm._unhandled_hint")
                .and_then(|m| m.getattr("hint_for"))
                .map(Bound::unbind)
        })
        .and_then(|hint_for| hint_for.bind(py).call1((shared.inner().bind(py),)))
        .and_then(|hint| hint.extract::<Option<String>>());
    match looked_up {
        Ok(hint) => hint,
        Err(e) => Some(format!("hint lookup failed: {e}")),
    }
}

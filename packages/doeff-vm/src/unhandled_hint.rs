//! The lines an UnhandledEffect message ends with: the handlers an annotation
//! skipped, and the `hint:` line.
//!
//! The VM knows no handler, so the lines come from Python: a package that ships
//! handlers registers a function from an effect to the line naming the handler and
//! how to install it (`doeff_vm._unhandled_hint`); the table for its effects is
//! `doeff_core_effects.unhandled_hints`. The VM hands over the handlers in scope that
//! filter by their annotation, and Python writes the line naming the ones that left
//! the effect out.

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyTuple;

use doeff_vm_core::value::Value;

static HINT_FOR: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static SKIPPED_BY_ANNOTATION: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// The line naming the handlers whose effect annotation left the effect out, or None
/// when none did. `typed_handlers`: the handlers in scope that filter by their
/// annotation, innermost first, as (the name the chain line shows, the types the
/// annotation admits). A failing lookup becomes the line itself, as for the hint.
pub fn skipped_by_annotation(
    py: Python<'_>,
    effect: &Value,
    typed_handlers: Vec<(String, Py<PyTuple>)>,
) -> Option<String> {
    let Value::Opaque(shared) = effect else {
        return None;
    };
    if typed_handlers.is_empty() {
        return None;
    }
    let looked_up = SKIPPED_BY_ANNOTATION
        .get_or_try_init(py, || {
            py.import("doeff_vm._unhandled_hint")
                .and_then(|m| m.getattr("skipped_by_annotation"))
                .map(Bound::unbind)
        })
        .and_then(|skipped| {
            skipped
                .bind(py)
                .call1((shared.inner().bind(py), typed_handlers))
        })
        .and_then(|line| line.extract::<Option<String>>());
    match looked_up {
        Ok(line) => line,
        Err(e) => Some(format!("skipped-handler lookup failed: {e}")),
    }
}

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

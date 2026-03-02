use std::sync::Arc;

use pyo3::prelude::*;

/// GIL-free clonable Python object reference (`Arc<Py<PyAny>>`).
/// `.clone()` is atomic increment — no GIL assertion on free-threaded 3.14t.
#[derive(Debug, Clone)]
pub struct PyShared(Arc<Py<PyAny>>);

impl PyShared {
    pub fn new(obj: Py<PyAny>) -> Self {
        PyShared(Arc::new(obj))
    }

    pub fn inner(&self) -> &Py<PyAny> {
        &self.0
    }

    pub fn bind<'py>(&self, py: Python<'py>) -> &Bound<'py, PyAny> {
        self.0.bind(py)
    }

    pub fn clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
        self.0.clone_ref(py)
    }

    pub fn into_inner(self) -> Py<PyAny> {
        match Arc::try_unwrap(self.0) {
            Ok(py) => py,
            Err(arc) => {
                // SAFETY: into_inner is only used while executing VM/Python-initiated paths where
                // the thread is already attached to the Python runtime.
                let py = unsafe { Python::assume_attached() };
                arc.clone_ref(py)
            }
        }
    }
}

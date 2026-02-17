//! Hash-preserving Python environment key wrapper.

use std::hash::{Hash, Hasher};

use pyo3::prelude::*;
#[cfg(test)]
use pyo3::types::PyString;

use crate::py_shared::PyShared;

/// HashMap key wrapper that preserves Python hash/eq semantics.
///
/// - `hash` is computed once from Python `hash(key)` at construction.
/// - `Eq` uses Python `__eq__` for collision resolution.
#[derive(Debug, Clone)]
pub struct PyEnvKey {
    object: PyShared,
    hash: isize,
    repr: String,
}

impl PyEnvKey {
    pub fn from_bound(key: &Bound<'_, PyAny>) -> PyResult<Self> {
        let hash = key.hash()?;
        let repr = key.repr()?.to_string_lossy().into_owned();
        Ok(PyEnvKey {
            object: PyShared::new(key.clone().unbind()),
            hash,
            repr,
        })
    }

    pub fn from_object(key: Py<PyAny>) -> PyResult<Self> {
        Python::attach(|py| Self::from_bound(key.bind(py)))
    }

    pub fn clone_object(&self, py: Python<'_>) -> Py<PyAny> {
        self.object.clone_ref(py)
    }

    pub fn repr(&self) -> &str {
        &self.repr
    }

    #[cfg(test)]
    pub fn from_str(key: &str) -> Self {
        Python::attach(|py| {
            let py_key = PyString::new(py, key).into_any();
            Self::from_bound(&py_key).expect("string keys are hashable")
        })
    }
}

impl Hash for PyEnvKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.hash.hash(state);
    }
}

impl PartialEq for PyEnvKey {
    fn eq(&self, other: &Self) -> bool {
        if self.hash != other.hash {
            return false;
        }

        Python::attach(|py| {
            let lhs = self.object.bind(py);
            let rhs = other.object.bind(py);
            lhs.eq(rhs).unwrap_or(false)
        })
    }
}

impl Eq for PyEnvKey {}

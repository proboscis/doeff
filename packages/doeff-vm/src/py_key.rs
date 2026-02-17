//! Hashable Python key wrapper for Rust maps.

use std::fmt;
use std::hash::{Hash, Hasher};

use pyo3::prelude::*;
#[cfg(test)]
use pyo3::types::PyString;

#[derive(Clone)]
enum HashedPyKeyInner {
    Python(Py<PyAny>),
    #[cfg(test)]
    TestString(String),
}

/// Wrapper that preserves Python hash/equality behavior for dictionary keys.
#[derive(Clone)]
pub struct HashedPyKey {
    hash: isize,
    inner: HashedPyKeyInner,
}

impl HashedPyKey {
    /// Build from a Python object by capturing its `hash(obj)` once.
    pub fn from_bound(obj: &Bound<'_, PyAny>) -> PyResult<Self> {
        let hash = obj.hash()?;
        Ok(Self {
            hash,
            inner: HashedPyKeyInner::Python(obj.clone().unbind()),
        })
    }

    pub fn to_pyobject(&self, py: Python<'_>) -> Py<PyAny> {
        match &self.inner {
            HashedPyKeyInner::Python(obj) => obj.clone_ref(py),
            #[cfg(test)]
            HashedPyKeyInner::TestString(value) => PyString::new(py, value).into_any().unbind(),
        }
    }

    pub fn display_for_error(&self) -> String {
        Python::attach(|py| {
            self.to_pyobject(py)
                .bind(py)
                .repr()
                .and_then(|repr| repr.to_str().map(|s| s.to_owned()))
                .unwrap_or_else(|_| "<unprintable env key>".to_string())
        })
    }

    #[cfg(test)]
    pub fn from_test_string(key: impl Into<String>) -> Self {
        let value = key.into();
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        value.hash(&mut hasher);
        let hash = (hasher.finish() & (isize::MAX as u64)) as isize;
        Self {
            hash,
            inner: HashedPyKeyInner::TestString(value),
        }
    }
}

impl fmt::Debug for HashedPyKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HashedPyKey")
            .field("hash", &self.hash)
            .field("repr", &self.display_for_error())
            .finish()
    }
}

impl PartialEq for HashedPyKey {
    fn eq(&self, other: &Self) -> bool {
        match (&self.inner, &other.inner) {
            (HashedPyKeyInner::Python(lhs), HashedPyKeyInner::Python(rhs)) => {
                if self.hash != other.hash {
                    return false;
                }
                Python::attach(|py| lhs.bind(py).eq(rhs.bind(py)).unwrap_or(false))
            }
            #[cfg(test)]
            (HashedPyKeyInner::TestString(lhs), HashedPyKeyInner::TestString(rhs)) => lhs == rhs,
            #[cfg(test)]
            (HashedPyKeyInner::Python(_), HashedPyKeyInner::TestString(_))
            | (HashedPyKeyInner::TestString(_), HashedPyKeyInner::Python(_)) => false,
        }
    }
}

impl Eq for HashedPyKey {}

impl Hash for HashedPyKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        match &self.inner {
            HashedPyKeyInner::Python(_) => self.hash.hash(state),
            #[cfg(test)]
            HashedPyKeyInner::TestString(value) => value.hash(state),
        }
    }
}

//! Bridge helpers for OpaqueRef ↔ PyO3 interop.
//!
//! `PyShared` remains as a convenience for bridge code that knows the inner
//! value is a `Py<PyAny>`. Core data structures store `OpaqueRef` instead.

use pyo3::prelude::*;

use crate::opaque_ref::OpaqueRef;

/// Extension trait: PyO3-specific methods on `OpaqueRef`.
///
/// Import this trait in bridge code to get `.bind(py)`, `.as_py()`, etc.
/// Core VM code should NOT import this trait.
pub trait OpaqueRefPyExt {
    /// Borrow the inner `Py<PyAny>`.
    ///
    /// # Panics
    /// Panics if this `OpaqueRef` does not contain a `Py<PyAny>`.
    fn as_py(&self) -> &Py<PyAny>;

    /// Bind the inner Python object to the GIL lifetime.
    ///
    /// # Panics
    /// Panics if this `OpaqueRef` does not contain a `Py<PyAny>`.
    fn bind<'py>(&self, py: Python<'py>) -> &Bound<'py, PyAny>;

    /// Clone the inner Python reference (increments refcount).
    ///
    /// # Panics
    /// Panics if this `OpaqueRef` does not contain a `Py<PyAny>`.
    fn clone_ref(&self, py: Python<'_>) -> Py<PyAny>;

    /// Try to borrow the inner `Py<PyAny>`, returning `None` if the
    /// `OpaqueRef` holds a different type.
    fn try_as_py(&self) -> Option<&Py<PyAny>>;
}

impl OpaqueRefPyExt for OpaqueRef {
    fn as_py(&self) -> &Py<PyAny> {
        self.downcast_ref::<Py<PyAny>>()
            .expect("OpaqueRef does not contain Py<PyAny>")
    }

    fn bind<'py>(&self, py: Python<'py>) -> &Bound<'py, PyAny> {
        self.as_py().bind(py)
    }

    fn clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
        self.as_py().clone_ref(py)
    }

    fn try_as_py(&self) -> Option<&Py<PyAny>> {
        self.downcast_ref::<Py<PyAny>>()
    }
}

/// Wrap a `Py<PyAny>` into an `OpaqueRef`.
pub fn py_to_opaque(obj: Py<PyAny>) -> OpaqueRef {
    OpaqueRef::new(obj)
}

/// Consume an `OpaqueRef` and extract the `Py<PyAny>`.
///
/// Returns `None` if the `OpaqueRef` does not hold a `Py<PyAny>`.
///
/// Because `OpaqueRef` uses `Arc` internally, this clones the `Py<PyAny>`
/// when there are multiple references. In the single-owner case it moves.
pub fn opaque_into_py(opaque: OpaqueRef) -> Option<Py<PyAny>> {
    // OpaqueRef wraps Arc<dyn Any + Send + Sync>.
    // We can't unwrap the Arc directly, so we clone the Py<PyAny>.
    opaque.downcast_ref::<Py<PyAny>>().cloned()
}

// ---- Legacy PyShared compat layer ----

/// GIL-free clonable Python object reference.
///
/// This is a thin wrapper around `OpaqueRef` for bridge code that knows
/// the contained value is `Py<PyAny>`.
#[derive(Debug, Clone)]
pub struct PyShared(pub(crate) OpaqueRef);

impl PyShared {
    pub fn new(obj: Py<PyAny>) -> Self {
        PyShared(OpaqueRef::new(obj))
    }

    pub fn inner(&self) -> &Py<PyAny> {
        self.0.as_py()
    }

    pub fn bind<'py>(&self, py: Python<'py>) -> &Bound<'py, PyAny> {
        self.0.bind(py)
    }

    pub fn clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
        self.0.clone_ref(py)
    }

    pub fn into_inner(self) -> Py<PyAny> {
        // Clone the inner Py<PyAny> before attempting unwrap, so we have a
        // fallback if the Arc is shared.
        let fallback = self.inner().clone();
        // Try to extract without GIL; fall back to the pre-cloned copy if Arc is shared.
        opaque_into_py(self.0).unwrap_or(fallback)
    }

    /// Convert to the runtime-agnostic `OpaqueRef`.
    pub fn into_opaque(self) -> OpaqueRef {
        self.0
    }

    /// Borrow the underlying `OpaqueRef`.
    pub fn as_opaque(&self) -> &OpaqueRef {
        &self.0
    }

    /// Try to wrap an `OpaqueRef` back into a `PyShared`.
    ///
    /// Returns `None` if the `OpaqueRef` does not contain a `Py<PyAny>`.
    pub fn from_opaque(opaque: OpaqueRef) -> Option<Self> {
        if opaque.is::<Py<PyAny>>() {
            Some(PyShared(opaque))
        } else {
            None
        }
    }

    /// Wrap an `OpaqueRef` into a `PyShared`, panicking if type mismatch.
    pub fn from_opaque_unwrap(opaque: OpaqueRef) -> Self {
        assert!(
            opaque.is::<Py<PyAny>>(),
            "OpaqueRef does not contain Py<PyAny>"
        );
        PyShared(opaque)
    }
}

impl From<PyShared> for OpaqueRef {
    fn from(shared: PyShared) -> Self {
        shared.0
    }
}

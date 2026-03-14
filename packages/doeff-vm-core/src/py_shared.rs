use crate::handle::Handle;

pub enum PyObjectTag {}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PyShared(Handle<PyObjectTag>);

impl PyShared {
    pub fn from_handle(handle: Handle<PyObjectTag>) -> Self {
        Self(handle)
    }

    pub fn stable_id(&self) -> u64 {
        self.0.stable_id()
    }

    pub fn downcast_ref<T: 'static>(&self) -> Option<&T> {
        self.0.downcast_ref::<T>()
    }

    pub fn retag<T>(&self) -> Handle<T> {
        self.0.retag()
    }

    pub fn into_handle(self) -> Handle<PyObjectTag> {
        self.0
    }
}

#[cfg(feature = "python_bridge")]
mod python_bridge {
    use std::any::Any;
    use std::sync::atomic::{AtomicU64, Ordering};

    use crate::handle::HandleToken;
    use crate::py_shared::{PyObjectTag, PyShared};
    use pyo3::prelude::*;

    static NEXT_PY_SHARED_ID: AtomicU64 = AtomicU64::new(1);

    #[derive(Debug)]
    struct PyObjectToken {
        stable_id: u64,
        obj: Py<PyAny>,
    }

    impl HandleToken for PyObjectToken {
        fn stable_id(&self) -> u64 {
            self.stable_id
        }

        fn as_any(&self) -> &dyn Any {
            self
        }

        fn into_any(self: Box<Self>) -> Box<dyn Any> {
            self
        }
    }

    impl PyShared {
        pub fn new(obj: Py<PyAny>) -> Self {
            // Process-local monotonic handle ids avoid pointer-reuse collisions after Python GC.
            let stable_id = NEXT_PY_SHARED_ID.fetch_add(1, Ordering::Relaxed);
            Self::from_handle(crate::handle::Handle::<PyObjectTag>::from_token(
                PyObjectToken { stable_id, obj },
            ))
        }

        pub fn inner(&self) -> &Py<PyAny> {
            &self
                .downcast_ref::<PyObjectToken>()
                .expect("PyShared must carry a PyObjectToken")
                .obj
        }

        pub fn bind<'py>(&self, py: Python<'py>) -> &Bound<'py, PyAny> {
            self.inner().bind(py)
        }

        pub fn clone_ref(&self, py: Python<'_>) -> Py<PyAny> {
            self.inner().clone_ref(py)
        }

        pub fn into_inner(self) -> Py<PyAny> {
            match self.into_handle().try_unwrap_token() {
                Ok(token) => {
                    token
                        .into_any()
                        .downcast::<PyObjectToken>()
                        .expect("PyShared must unwrap back into PyObjectToken")
                        .obj
                }
                Err(handle) => {
                    let shared = PyShared::from_handle(handle);
                    Python::try_attach(|py| shared.clone_ref(py)).unwrap_or_else(|| {
                        panic!(
                            "PyShared::into_inner requires an attached Python runtime when the handle has shared refs"
                        )
                    })
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use std::panic::{catch_unwind, AssertUnwindSafe};

        use super::*;
        use pyo3::types::PyDict;

        #[test]
        fn stable_id_is_unique_per_live_handle() {
            Python::attach(|py| {
                let first = PyShared::new(PyDict::new(py).into_any().unbind());
                let second = PyShared::new(PyDict::new(py).into_any().unbind());
                assert_ne!(first.stable_id(), second.stable_id());
            });
        }

        #[test]
        fn into_inner_can_move_unique_ref_without_gil() {
            Python::attach(|py| {
                let shared = PyShared::new(PyDict::new(py).into_any().unbind());
                let obj = py.detach(|| shared.into_inner());
                assert!(obj.bind(py).cast::<PyDict>().is_ok());
            });
        }

        #[test]
        fn into_inner_panics_without_gil_when_handle_is_shared() {
            Python::attach(|py| {
                let shared = PyShared::new(PyDict::new(py).into_any().unbind());
                let keep_alive = shared.clone();
                let result = py.detach(|| catch_unwind(AssertUnwindSafe(|| shared.into_inner())));
                assert!(result.is_err());
                drop(keep_alive);
            });
        }
    }
}

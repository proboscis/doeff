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

    use pyo3::prelude::*;

    use crate::handle::HandleToken;
    use crate::py_shared::{PyObjectTag, PyShared};

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
    }

    impl PyShared {
        pub fn new(obj: Py<PyAny>) -> Self {
            let stable_id = Python::attach(|py| obj.bind(py).as_ptr() as usize as u64);
            Self::from_handle(crate::handle::Handle::<PyObjectTag>::from_token(PyObjectToken {
                stable_id,
                obj,
            }))
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
            let py = unsafe { Python::assume_attached() };
            self.clone_ref(py)
        }
    }
}

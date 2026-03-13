//! Opaque handle type for Python (or other runtime) objects.
//!
//! `OpaqueRef` wraps an `Arc<dyn Any + Send + Sync>` so that core VM data
//! structures can carry runtime-specific objects without depending on PyO3
//! or any other specific runtime crate.
//!
//! Bridge code (e.g. `py_shared`) provides extension traits to extract
//! concrete types such as `Py<PyAny>`.

use std::any::Any;
use std::fmt;
use std::sync::Arc;

/// An opaque, cheaply-clonable reference to a runtime object.
///
/// The VM core treats this as a black box. Only bridge code
/// knows how to resolve the inner value.
#[derive(Clone)]
pub struct OpaqueRef(Arc<dyn Any + Send + Sync>);

impl OpaqueRef {
    /// Wrap any `Send + Sync + 'static` value.
    pub fn new<T: Any + Send + Sync + 'static>(val: T) -> Self {
        OpaqueRef(Arc::new(val))
    }

    /// Try to borrow the inner value as a concrete type.
    pub fn downcast_ref<T: Any>(&self) -> Option<&T> {
        self.0.as_ref().downcast_ref::<T>()
    }

    /// Check whether the inner value is a given type.
    pub fn is<T: Any>(&self) -> bool {
        self.0.as_ref().is::<T>()
    }

    /// Get a raw pointer for identity comparison (not dereferenceable).
    pub fn as_ptr(&self) -> *const () {
        Arc::as_ptr(&self.0) as *const ()
    }
}

impl fmt::Debug for OpaqueRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "OpaqueRef({:p})", Arc::as_ptr(&self.0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_opaque_ref_roundtrip() {
        let val = 42u64;
        let opaque = OpaqueRef::new(val);
        assert_eq!(opaque.downcast_ref::<u64>(), Some(&42u64));
        assert!(opaque.is::<u64>());
        assert!(!opaque.is::<String>());
    }

    #[test]
    fn test_opaque_ref_clone_is_cheap() {
        let opaque = OpaqueRef::new("hello".to_string());
        let cloned = opaque.clone();
        assert_eq!(opaque.as_ptr(), cloned.as_ptr());
    }

    #[test]
    fn test_opaque_ref_debug() {
        let opaque = OpaqueRef::new(123i32);
        let debug = format!("{:?}", opaque);
        assert!(debug.starts_with("OpaqueRef("));
    }
}

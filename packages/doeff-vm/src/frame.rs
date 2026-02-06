//! Frame types for the continuation stack.

use pyo3::prelude::*;

use crate::handler::RustProgramRef;
use crate::ids::CallbackId;

/// A frame in the continuation stack.
///
/// Frames must be Clone to allow continuation capture (Arc snapshots).
/// Rust callbacks are stored in a separate table and referenced by CallbackId.
#[derive(Debug, Clone)]
pub enum Frame {
    RustReturn { callback_id: CallbackId },
    RustProgram { program: RustProgramRef },
    PythonGenerator { generator: Py<PyAny>, started: bool },
}

impl Frame {
    pub fn rust_return(callback_id: CallbackId) -> Self {
        Frame::RustReturn { callback_id }
    }

    pub fn python_generator(generator: Py<PyAny>) -> Self {
        Frame::PythonGenerator {
            generator,
            started: false,
        }
    }

    pub fn rust_program(program: RustProgramRef) -> Self {
        Frame::RustProgram { program }
    }

    pub fn is_rust(&self) -> bool {
        matches!(self, Frame::RustReturn { .. } | Frame::RustProgram { .. })
    }

    pub fn is_rust_program(&self) -> bool {
        matches!(self, Frame::RustProgram { .. })
    }

    pub fn is_python(&self) -> bool {
        matches!(self, Frame::PythonGenerator { .. })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_rust_return() {
        let cb_id = CallbackId::fresh();
        let frame = Frame::rust_return(cb_id);
        assert!(frame.is_rust());
        assert!(!frame.is_python());
    }

    #[test]
    fn test_frame_is_clone() {
        let cb_id = CallbackId::fresh();
        let frame = Frame::rust_return(cb_id);
        let _cloned = frame.clone();
    }
}

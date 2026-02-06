//! Frame types for the continuation stack.

use pyo3::prelude::*;

use crate::handler::RustProgramRef;
use crate::ids::CallbackId;

/// Metadata about a program call for call stack reconstruction. [SPEC-008 R9-D]
///
/// Extracted by the driver (with GIL) during classify_yielded or by
/// RustHandlerPrograms that emit Call primitives. Stored on PythonGenerator frames.
#[derive(Debug, Clone)]
pub struct CallMetadata {
    /// Human-readable function name (e.g., "fetch_user")
    pub function_name: String,
    /// Source file where the @do function is defined
    pub source_file: String,
    /// Line number in source file
    pub source_line: u32,
    /// Optional: reference to the full KleisliProgramCall Python object
    pub program_call: Option<Py<PyAny>>,
}

/// A frame in the continuation stack.
///
/// Frames must be Clone to allow continuation capture (Arc snapshots).
/// Rust callbacks are stored in a separate table and referenced by CallbackId.
#[derive(Debug, Clone)]
pub enum Frame {
    RustReturn {
        cb: CallbackId,
    },
    RustProgram {
        program: RustProgramRef,
    },
    PythonGenerator {
        generator: Py<PyAny>,
        started: bool,
        metadata: Option<CallMetadata>,
    },
}

impl Frame {
    pub fn rust_return(cb: CallbackId) -> Self {
        Frame::RustReturn { cb }
    }

    pub fn python_generator(generator: Py<PyAny>) -> Self {
        Frame::PythonGenerator {
            generator,
            started: false,
            metadata: None,
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

    /// G13: Frame::RustReturn uses `cb` field name per spec.
    #[test]
    fn test_frame_rust_return_field_is_cb() {
        let cb_id = CallbackId::fresh();
        let frame = Frame::RustReturn { cb: cb_id };
        match frame {
            Frame::RustReturn { cb } => assert_eq!(cb, cb_id),
            _ => panic!("Expected RustReturn"),
        }
    }
}

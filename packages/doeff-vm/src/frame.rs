//! Frame types for the continuation stack.

use std::sync::atomic::{AtomicU64, Ordering};

use crate::ast_stream::ASTStreamRef;
use crate::ids::CallbackId;
use crate::py_shared::PyShared;

static NEXT_FRAME_ID: AtomicU64 = AtomicU64::new(1);

pub fn fresh_frame_id() -> u64 {
    NEXT_FRAME_ID.fetch_add(1, Ordering::Relaxed)
}

/// Metadata about a program call for call stack reconstruction. [SPEC-008 R9-D]
///
/// Extracted by the driver (with GIL) during classify_yielded or by
/// RustHandlerPrograms that emit Call primitives. Stored on Program frames.
#[derive(Debug, Clone)]
pub struct CallMetadata {
    pub frame_id: u64,
    pub function_name: String,
    pub source_file: String,
    pub source_line: u32,
    pub args_repr: Option<String>,
    pub program_call: Option<PyShared>,
}

impl CallMetadata {
    pub fn new(
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
        program_call: Option<PyShared>,
    ) -> Self {
        CallMetadata {
            frame_id: fresh_frame_id(),
            function_name,
            source_file,
            source_line,
            args_repr,
            program_call,
        }
    }

    pub fn anonymous() -> Self {
        // Restriction (VM-PROTO-005 / C7):
        // This helper is only for tests and VM-internal synthetic calls where
        // metadata is carried through another typed channel. User-facing runtime
        // paths must provide explicit callback metadata.
        Self::new(
            "<anonymous>".to_string(),
            "<unknown>".to_string(),
            0,
            None,
            None,
        )
    }
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
    Program {
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    },
}

impl Frame {
    pub fn rust_return(cb: CallbackId) -> Self {
        Frame::RustReturn { cb }
    }

    pub fn program(stream: ASTStreamRef, metadata: Option<CallMetadata>) -> Self {
        Frame::Program { stream, metadata }
    }

    pub fn is_rust(&self) -> bool {
        matches!(self, Frame::RustReturn { .. })
    }

    pub fn is_program(&self) -> bool {
        matches!(self, Frame::Program { .. })
    }

    pub fn has_metadata(&self) -> bool {
        matches!(
            self,
            Frame::Program {
                metadata: Some(_),
                ..
            }
        )
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
        assert!(!frame.is_program());
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

    #[test]
    fn test_vm_proto_program_frame_uses_ast_stream_ref() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/frame.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("Program {")
                && runtime_src.contains("stream: ASTStreamRef")
                && !runtime_src.contains("PythonGenerator"),
            "VM-PROTO-001: Frame::Program must carry ASTStreamRef and replace PythonGenerator"
        );
    }
}

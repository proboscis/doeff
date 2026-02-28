//! Frame types for the continuation stack.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::ir_stream::IRStreamRef;
use crate::do_ctrl::DoCtrl;
use crate::ids::{DispatchId, Marker};
use crate::py_shared::PyShared;
use crate::step::PyException;
use crate::value::Value;

static NEXT_FRAME_ID: AtomicU64 = AtomicU64::new(1);

pub fn fresh_frame_id() -> u64 {
    NEXT_FRAME_ID.fetch_add(1, Ordering::Relaxed)
}

/// Metadata about a program call for call stack reconstruction. [SPEC-008 R9-D]
///
/// Extracted by the driver (with GIL) during classify_yielded or by
/// Rust handler streams that emit call primitives. Stored on Program frames.
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
#[derive(Debug, Clone)]
pub struct InterceptorContinuation {
    pub marker: Marker,
    pub original_yielded: DoCtrl,
    pub original_obj: PyShared,
    pub emitter_stream: IRStreamRef,
    pub emitter_metadata: Option<CallMetadata>,
    pub chain: Arc<Vec<Marker>>,
    pub next_idx: usize,
    pub interceptor_metadata: Option<CallMetadata>,
}

#[derive(Debug, Clone)]
pub enum EvalReturnContinuation {
    ApplyResolveFunction {
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
        evaluate_result: bool,
    },
    ApplyResolveArg {
        f: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        arg_idx: usize,
        metadata: CallMetadata,
        evaluate_result: bool,
    },
    ApplyResolveKwarg {
        f: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        kwarg_idx: usize,
        metadata: CallMetadata,
        evaluate_result: bool,
    },
    ExpandResolveFactory {
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    },
    ExpandResolveArg {
        factory: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        arg_idx: usize,
        metadata: CallMetadata,
    },
    ExpandResolveKwarg {
        factory: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        kwarg_idx: usize,
        metadata: CallMetadata,
    },
    FinallyCleanup {
        original: FinallyOutcome,
    },
}

#[derive(Debug, Clone)]
pub enum FinallyOutcome {
    Deliver(Value),
    Throw(PyException),
}

#[derive(Debug, Clone)]
pub enum Frame {
    Program {
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
    },
    InterceptorApply(Box<InterceptorContinuation>),
    InterceptorEval(Box<InterceptorContinuation>),
    HandlerDispatch {
        dispatch_id: DispatchId,
    },
    EvalReturn(Box<EvalReturnContinuation>),
    MapReturn {
        mapper: PyShared,
        mapper_meta: CallMetadata,
    },
    FlatMapBindResult,
    FlatMapBindSource {
        binder: PyShared,
        binder_meta: CallMetadata,
    },
    InterceptBodyReturn {
        marker: Marker,
    },
}

impl Frame {
    pub fn program(stream: IRStreamRef, metadata: Option<CallMetadata>) -> Self {
        Frame::Program { stream, metadata }
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
    use crate::ir_stream::{IRStream, IRStreamStep};
    use crate::rust_store::RustStore;
    use crate::segment::ScopeStore;
    use crate::value::Value;

    #[derive(Debug)]
    struct DummyStream;

    impl IRStream for DummyStream {
        fn resume(
            &mut self,
            _value: Value,
            _store: &mut RustStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Return(Value::Unit)
        }

        fn throw(
            &mut self,
            exc: crate::driver::PyException,
            _store: &mut RustStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Throw(exc)
        }
    }

    #[test]
    fn test_frame_is_clone() {
        let frame = Frame::FlatMapBindResult;
        let _cloned = frame.clone();
    }

    #[test]
    fn test_program_frame_is_program() {
        let stream = std::sync::Arc::new(std::sync::Mutex::new(
            Box::new(DummyStream) as Box<dyn IRStream>
        ));
        let frame = Frame::program(stream, None);
        assert!(frame.is_program());
    }

    #[test]
    fn test_vm_proto_program_frame_uses_ast_stream_ref() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/frame.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("Program {")
                && runtime_src.contains("stream: IRStreamRef")
                && !runtime_src.contains("PythonGenerator"),
            "VM-PROTO-001: Frame::Program must carry IRStreamRef and replace PythonGenerator"
        );
    }
}

//! Frame types for the continuation stack.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::do_ctrl::DoCtrl;
use crate::ids::{FiberId, Marker, SegmentId, VarId};
use crate::ir_stream::IRStreamRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::segment::InterceptMode;
use crate::value::{CallableRef, Value};

static NEXT_FRAME_ID: AtomicU64 = AtomicU64::new(1);

pub fn fresh_frame_id() -> u64 {
    NEXT_FRAME_ID.fetch_add(1, Ordering::Relaxed)
}

/// Metadata about a program call for call stack reconstruction.
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
        Self::new(
            "<anonymous>".to_string(),
            "<unknown>".to_string(),
            0,
            None,
            None,
        )
    }
}

/// Link in the interceptor chain (extension, not OCaml 5 core).
#[derive(Debug, Clone)]
pub struct InterceptorChainLink {
    pub marker: Marker,
    pub interceptor: CallableRef,
    pub types: Option<Vec<PyShared>>,
    pub mode: InterceptMode,
    pub metadata: Option<CallMetadata>,
}

impl InterceptorChainLink {
    pub fn from_handler(handler: &crate::segment::Handler) -> Option<Self> {
        let intercept = handler.intercept_boundary()?;
        Some(Self {
            marker: handler.marker(),
            interceptor: intercept.interceptor.clone(),
            types: intercept.types.clone(),
            mode: intercept.mode,
            metadata: intercept.metadata.clone(),
        })
    }
}

/// Continuation state for interceptor frames.
#[derive(Debug)]
pub struct InterceptorContinuation {
    pub marker: Marker,
    pub original_yielded: DoCtrl,
    pub original_obj: PyShared,
    pub emitter_stream: IRStreamRef,
    pub emitter_metadata: Option<CallMetadata>,
    pub interceptor_metadata: Option<CallMetadata>,
    pub guard_eval_depth: bool,
}

#[derive(Debug)]
pub enum EvalReturnContinuation {
    ApplyResolveFunction {
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    },
    ApplyResolveArg {
        f: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        arg_idx: usize,
        metadata: CallMetadata,
    },
    ApplyResolveKwarg {
        f: DoCtrl,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        kwarg_idx: usize,
        metadata: CallMetadata,
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
    ResumeToContinuation {
        head_fiber: FiberId,
    },
    TailResumeReturn,
    ReturnToContinuation {
        head_fiber: FiberId,
    },
    EvalInScopeReturn {
        head_fiber: FiberId,
    },
    InterceptApplyResult {
        continuation: InterceptorContinuation,
    },
    InterceptEvalResult {
        continuation: InterceptorContinuation,
    },
    /// Expand: inner expr evaluated, result must be Value::Stream → push as frame.
    ///
    /// `handler_k_handle` is Some only when this Expand came from a handler
    /// dispatch (eval_perform / eval_perform_with_k on the generator-handler
    /// path, e.g. the @do wrapper's `Expand(Apply(Pure(thunk), []))`). The
    /// handle owns the perform-site continuation chain for the duration of
    /// the deferred handler construction; it is consumed on EVERY exit from
    /// this frame — moved into `Frame::Program.handler_k_handle` on the value
    /// path, or used to discontinue the perform-site chain on the raise path
    /// (see step_raise). Storing it here (not in a VM-global slot) ties its
    /// lifetime to the dispatch that created it.
    ExpandReturn {
        handler_k_handle: Option<pyo3::Py<crate::continuation::PyK>>,
    },
}

impl EvalReturnContinuation {
    pub(crate) fn metadata(&self) -> Option<&CallMetadata> {
        match self {
            EvalReturnContinuation::ApplyResolveFunction { metadata, .. }
            | EvalReturnContinuation::ApplyResolveArg { metadata, .. }
            | EvalReturnContinuation::ApplyResolveKwarg { metadata, .. }
            | EvalReturnContinuation::ExpandResolveFactory { metadata, .. }
            | EvalReturnContinuation::ExpandResolveArg { metadata, .. }
            | EvalReturnContinuation::ExpandResolveKwarg { metadata, .. } => Some(metadata),
            EvalReturnContinuation::InterceptApplyResult { continuation }
            | EvalReturnContinuation::InterceptEvalResult { continuation } => {
                continuation.emitter_metadata.as_ref()
            }
            EvalReturnContinuation::ResumeToContinuation { .. }
            | EvalReturnContinuation::TailResumeReturn
            | EvalReturnContinuation::ReturnToContinuation { .. }
            | EvalReturnContinuation::EvalInScopeReturn { .. }
            | EvalReturnContinuation::ExpandReturn { .. } => None,
        }
    }
}

/// Frame on the fiber's stack.
///
/// NO handler_kind or dispatch fields — those are OCaml 5 violations.
/// Handler info is on the Fiber.handler, not on frames.
#[derive(Debug)]
pub enum Frame {
    Program {
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        /// Reference to the PyK Python object that owns the perform-site
        /// continuation chain, used only for handler-body program frames.
        /// If this stream raises an uncaught exception, the VM borrows this
        /// handle, takes the chain from the PyK (if the handler didn't
        /// consume k), and reattaches it so the inner handler's `<-` site
        /// (or the user program's perform site) can `try/except` the error.
        /// None for ordinary program frames (user body, etc.).
        ///
        /// This is a Python handle (Py<PyK>), NOT a continuation. The chain
        /// lives inside the PyK — move-only by construction (SPEC-VM-021).
        handler_k_handle: Option<pyo3::Py<crate::continuation::PyK>>,
    },
    LexicalScope {
        bindings: HashMap<HashedPyKey, Value>,
        var_overrides: HashMap<VarId, Value>,
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
}

impl Frame {
    pub fn program(stream: IRStreamRef, metadata: Option<CallMetadata>) -> Self {
        Frame::Program {
            stream,
            metadata,
            handler_k_handle: None,
        }
    }

    pub fn program_with_k_handle(
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_k_handle: Option<pyo3::Py<crate::continuation::PyK>>,
    ) -> Self {
        Frame::Program {
            stream,
            metadata,
            handler_k_handle,
        }
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
    use crate::ir_stream::IRStream;
    use crate::value::Value;

    #[derive(Debug)]
    struct DummyStream;

    impl IRStream for DummyStream {
        fn resume(&mut self, _value: Value) -> crate::ir_stream::StreamStep {
            crate::ir_stream::StreamStep::Done(Value::Unit)
        }

        fn throw(&mut self, error: Value) -> crate::ir_stream::StreamStep {
            crate::ir_stream::StreamStep::Error(error)
        }
    }

    #[test]
    fn test_program_frame_is_program() {
        let stream = IRStreamRef::new(Box::new(DummyStream) as Box<dyn IRStream>);
        let frame = Frame::program(stream, None);
        assert!(frame.is_program());
    }
}

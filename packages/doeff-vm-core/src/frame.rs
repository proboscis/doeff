//! Frame types for the continuation stack.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::do_ctrl::{DoCtrl, InterceptMode};
use crate::driver::PyException;
use crate::effect::DispatchEffect;
use crate::ids::{FiberId, Marker, SegmentId, VarId};
use crate::ir_stream::IRStreamRef;
use crate::kleisli::KleisliRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
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
    pub auto_unwrap_programlike: bool,
}

impl CallMetadata {
    pub fn new(
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
        program_call: Option<PyShared>,
        auto_unwrap_programlike: bool,
    ) -> Self {
        CallMetadata {
            frame_id: fresh_frame_id(),
            function_name,
            source_file,
            source_line,
            args_repr,
            program_call,
            auto_unwrap_programlike,
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
            false,
        )
    }
}

#[derive(Debug, Clone)]
pub struct InterceptorChainLink {
    pub marker: Marker,
    pub interceptor: KleisliRef,
    pub types: Option<Vec<PyShared>>,
    pub mode: InterceptMode,
    pub metadata: Option<CallMetadata>,
}

impl InterceptorChainLink {
    pub fn from_boundary(boundary: &SegmentKind) -> Option<Self> {
        let boundary = boundary.boundary()?;
        let intercept = boundary.intercept_boundary()?;
        Some(Self {
            marker: boundary.marker(),
            interceptor: intercept.interceptor.clone(),
            types: intercept.types.clone(),
            mode: intercept.mode,
            metadata: intercept.metadata.clone(),
        })
    }

    pub fn into_boundary(self) -> SegmentKind {
        SegmentKind::Boundary(FiberBoundary::intercept(
            self.marker,
            self.interceptor,
            self.types,
            self.mode,
            self.metadata,
        ))
    }
}

#[derive(Debug)]
pub struct DispatchEffectSite {
    pub frame_id: FrameId,
    pub function_name: String,
    pub source_file: String,
    pub source_line: u32,
}

impl Clone for DispatchEffectSite {
    fn clone(&self) -> Self {
        Self {
            frame_id: self.frame_id,
            function_name: self.function_name.clone(),
            source_file: self.source_file.clone(),
            source_line: self.source_line,
        }
    }
}

#[derive(Debug)]
pub struct DispatchDisplay {
    pub effect_site: Option<DispatchEffectSite>,
    pub handler_stack: Vec<HandlerDispatchEntry>,
    pub transfer_target_repr: Option<String>,
    pub result: EffectResult,
    pub resumed_once: bool,
    pub is_execution_context_effect: bool,
}

impl Clone for DispatchDisplay {
    fn clone(&self) -> Self {
        Self {
            effect_site: self.effect_site.clone(),
            handler_stack: self.handler_stack.clone(),
            transfer_target_repr: self.transfer_target_repr.clone(),
            result: self.result.clone(),
            resumed_once: self.resumed_once,
            is_execution_context_effect: self.is_execution_context_effect,
        }
    }
}

// ProgramDispatch removed — OCaml 5 has no dispatch state.
// The handler closure receives (effect, k) as arguments.
// No persistent dispatch tracking.

#[derive(Debug, Clone)]
pub struct ProgramFrameSnapshot {
    pub stream: IRStreamRef,
    pub metadata: Option<CallMetadata>,
    pub handler_kind: Option<HandlerKind>,
    pub dispatch: Option<ProgramDispatch>,
}

#[derive(Debug)]
pub struct InterceptorContinuation {
    pub marker: Marker,
    pub original_yielded: DoCtrl,
    pub original_obj: PyShared,
    pub emitter_stream: IRStreamRef,
    pub emitter_metadata: Option<CallMetadata>,
    pub emitter_handler_kind: Option<HandlerKind>,
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
            | EvalReturnContinuation::EvalInScopeReturn { .. } => None,
        }
    }
}

#[derive(Debug)]
pub enum Frame {
    Program {
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
        dispatch: Option<ProgramDispatch>,
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
            handler_kind: None,
            dispatch: None,
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
    use crate::ir_stream::{IRStream, IRStreamStep};
    use crate::segment::ScopeStore;
    use crate::value::Value;
    use crate::var_store::VarStore;

    #[derive(Debug)]
    struct DummyStream;

    impl IRStream for DummyStream {
        fn resume(
            &mut self,
            _value: Value,
            _store: &mut VarStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Return(Value::Unit)
        }

        fn throw(
            &mut self,
            exc: crate::driver::PyException,
            _store: &mut VarStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Throw(exc)
        }
    }

    #[test]
    fn test_program_frame_is_program() {
        let stream = IRStreamRef::new(Box::new(DummyStream) as Box<dyn IRStream>);
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

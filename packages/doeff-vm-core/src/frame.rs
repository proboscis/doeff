//! Frame types for the continuation stack.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::capture::HandlerKind;
use crate::do_ctrl::{DoCtrl, InterceptMode};
use crate::ids::Marker;
use crate::ir_stream::IRStreamRef;
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::segment::SegmentKind;

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
    pub fn from_boundary(marker: Marker, boundary: &SegmentKind) -> Option<Self> {
        match boundary {
            SegmentKind::InterceptorBoundary {
                interceptor,
                types,
                mode,
                metadata,
            } => Some(Self {
                marker,
                interceptor: interceptor.clone(),
                types: types.clone(),
                mode: *mode,
                metadata: metadata.clone(),
            }),
            SegmentKind::Normal
            | SegmentKind::PromptBoundary { .. }
            | SegmentKind::MaskBoundary { .. } => None,
        }
    }

    pub fn into_boundary(self) -> SegmentKind {
        SegmentKind::InterceptorBoundary {
            interceptor: self.interceptor,
            types: self.types,
            mode: self.mode,
            metadata: self.metadata,
        }
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
    pub emitter_handler_kind: Option<HandlerKind>,
    /// Snapshot of the remaining interceptor chain for the current yielded effect.
    ///
    /// Effectful interceptor execution can suspend and later resume through this frame.
    /// Resuming must continue with the exact tail that was visible when the yield was first
    /// classified, not a freshly rebuilt live chain that may have diverged while the interceptor
    /// itself was running. Marker remaps are applied to this snapshot when continuations move.
    pub chain: Arc<Vec<InterceptorChainLink>>,
    pub next_idx: usize,
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
        continuation: crate::continuation::Continuation,
    },
    TailResumeReturn,
    ReturnToContinuation {
        continuation: crate::continuation::Continuation,
    },
    EvalInScopeReturn {
        continuation: crate::continuation::Continuation,
    },
}

impl EvalReturnContinuation {
    fn contains_started_continuation(&self) -> bool {
        match self {
            EvalReturnContinuation::ResumeToContinuation { continuation }
            | EvalReturnContinuation::ReturnToContinuation { continuation }
            | EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                continuation.is_started()
            }
            EvalReturnContinuation::ApplyResolveFunction { .. }
            | EvalReturnContinuation::ApplyResolveArg { .. }
            | EvalReturnContinuation::ApplyResolveKwarg { .. }
            | EvalReturnContinuation::ExpandResolveFactory { .. }
            | EvalReturnContinuation::ExpandResolveArg { .. }
            | EvalReturnContinuation::ExpandResolveKwarg { .. }
            | EvalReturnContinuation::TailResumeReturn => false,
        }
    }
}

impl Clone for EvalReturnContinuation {
    #[track_caller]
    fn clone(&self) -> Self {
        if self.contains_started_continuation()
            && std::env::var_os("DOEFF_PANIC_ON_STARTED_CONT_CLONE").is_some()
        {
            panic!(
                "started continuation clone detected via EvalReturnContinuation at {}",
                std::panic::Location::caller()
            );
        }

        match self {
            EvalReturnContinuation::ApplyResolveFunction {
                args,
                kwargs,
                metadata,
            } => EvalReturnContinuation::ApplyResolveFunction {
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ApplyResolveArg {
                f,
                args,
                kwargs,
                arg_idx,
                metadata,
            } => EvalReturnContinuation::ApplyResolveArg {
                f: f.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                arg_idx: *arg_idx,
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ApplyResolveKwarg {
                f,
                args,
                kwargs,
                kwarg_idx,
                metadata,
            } => EvalReturnContinuation::ApplyResolveKwarg {
                f: f.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                kwarg_idx: *kwarg_idx,
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ExpandResolveFactory {
                args,
                kwargs,
                metadata,
            } => EvalReturnContinuation::ExpandResolveFactory {
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ExpandResolveArg {
                factory,
                args,
                kwargs,
                arg_idx,
                metadata,
            } => EvalReturnContinuation::ExpandResolveArg {
                factory: factory.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                arg_idx: *arg_idx,
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ExpandResolveKwarg {
                factory,
                args,
                kwargs,
                kwarg_idx,
                metadata,
            } => EvalReturnContinuation::ExpandResolveKwarg {
                factory: factory.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                kwarg_idx: *kwarg_idx,
                metadata: metadata.clone(),
            },
            EvalReturnContinuation::ResumeToContinuation { continuation } => {
                EvalReturnContinuation::ResumeToContinuation {
                    continuation: continuation.clone(),
                }
            }
            EvalReturnContinuation::TailResumeReturn => EvalReturnContinuation::TailResumeReturn,
            EvalReturnContinuation::ReturnToContinuation { continuation } => {
                EvalReturnContinuation::ReturnToContinuation {
                    continuation: continuation.clone(),
                }
            }
            EvalReturnContinuation::EvalInScopeReturn { continuation } => {
                EvalReturnContinuation::EvalInScopeReturn {
                    continuation: continuation.clone(),
                }
            }
        }
    }
}

#[derive(Debug)]
pub enum Frame {
    Program {
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
        handler_kind: Option<HandlerKind>,
    },
    InterceptorApply(Box<InterceptorContinuation>),
    InterceptorEval(Box<InterceptorContinuation>),
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
    fn contains_started_continuation(&self) -> bool {
        match self {
            Frame::EvalReturn(continuation) => continuation.contains_started_continuation(),
            Frame::Program { .. }
            | Frame::InterceptorApply(_)
            | Frame::InterceptorEval(_)
            | Frame::MapReturn { .. }
            | Frame::FlatMapBindResult
            | Frame::FlatMapBindSource { .. }
            | Frame::InterceptBodyReturn { .. } => false,
        }
    }
}

impl Clone for Frame {
    #[track_caller]
    fn clone(&self) -> Self {
        if self.contains_started_continuation()
            && std::env::var_os("DOEFF_PANIC_ON_STARTED_CONT_CLONE").is_some()
        {
            panic!(
                "started continuation clone detected via Frame at {}",
                std::panic::Location::caller()
            );
        }

        match self {
            Frame::Program {
                stream,
                metadata,
                handler_kind,
            } => Frame::Program {
                stream: stream.clone(),
                metadata: metadata.clone(),
                handler_kind: *handler_kind,
            },
            Frame::InterceptorApply(continuation) => {
                Frame::InterceptorApply(Box::new((**continuation).clone()))
            }
            Frame::InterceptorEval(continuation) => {
                Frame::InterceptorEval(Box::new((**continuation).clone()))
            }
            Frame::EvalReturn(continuation) => Frame::EvalReturn(Box::new((**continuation).clone())),
            Frame::MapReturn {
                mapper,
                mapper_meta,
            } => Frame::MapReturn {
                mapper: mapper.clone(),
                mapper_meta: mapper_meta.clone(),
            },
            Frame::FlatMapBindResult => Frame::FlatMapBindResult,
            Frame::FlatMapBindSource {
                binder,
                binder_meta,
            } => Frame::FlatMapBindSource {
                binder: binder.clone(),
                binder_meta: binder_meta.clone(),
            },
            Frame::InterceptBodyReturn { marker } => Frame::InterceptBodyReturn { marker: *marker },
        }
    }
}

impl Frame {
    pub fn program(stream: IRStreamRef, metadata: Option<CallMetadata>) -> Self {
        Frame::Program {
            stream,
            metadata,
            handler_kind: None,
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

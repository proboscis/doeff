//! doeff-vm-core: generic VM machinery and handler protocol.

pub mod arena;
pub mod capture;
pub mod continuation;
mod debug_state;
pub mod dispatch;
pub mod do_ctrl;
pub mod doeff_generator;
pub mod driver;
pub mod effect;
pub mod error;
pub mod frame;
pub mod handler;
pub mod ids;
mod interceptor_state;
pub mod ir_stream;
pub mod kleisli;
pub mod py_key;
pub mod py_shared;
pub mod pyvm;
pub mod python_call;
pub mod rust_store;
pub mod segment;
mod step;
mod trace_state;
pub mod value;
mod vm;
mod vm_logging;

pub use arena::SegmentArena;
pub use capture::{
    ActiveChainEntry, CaptureEvent, DelegationEntry, DispatchAction, EffectResult, FrameId,
    HandlerAction, HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry, HandlerStatus,
    SpawnSite, TraceEntry, TraceFrame, TraceHop,
};
pub use continuation::Continuation;
pub use do_ctrl::DoCtrl;
pub use doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
pub use driver::{Mode, StepEvent};
pub use effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, dispatch_to_pyobject,
    make_execution_context_object, make_get_execution_context_effect, DispatchEffect, Effect,
    PyEffectBase, PyExecutionContext, PyGetExecutionContext,
};
pub use error::VMError;
pub use frame::Frame;
pub use handler::{
    IRStreamFactory, IRStreamFactoryRef, IRStreamProgram, IRStreamProgramRef,
};
pub use ids::{ContId, DispatchId, Marker, PromiseId, RunnableId, SegmentId, TaskId};
pub use ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream, StreamLocation};
pub use kleisli::{Kleisli, KleisliDebugInfo, KleisliRef, PyKleisli, RustKleisli};
pub use py_key::HashedPyKey;
pub use pyvm::{
    classify_yielded_for_vm, doctrl_tag, doctrl_to_pyexpr_for_vm, install_vm_hooks,
    is_doexpr_like, is_effect_base_like, DoExprTag, PyDoCtrlBase, PyDoExprBase, PyK, PyResultErr,
    PyResultOk, PyTraceFrame, PyTraceHop, VmHooks,
};
pub use python_call::{PendingPython, PyCallOutcome, PythonCall};
pub use rust_store::RustStore;
pub use segment::{Segment, SegmentKind};
pub use step::PyException;
pub use value::Value;
pub use vm::{DebugConfig, DebugLevel, TraceEvent, VM};

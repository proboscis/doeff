//! doeff-vm-core: generic VM machinery and handler protocol.

pub mod handle;
pub mod py_shared;

#[cfg(feature = "python_bridge")]
pub mod arena;
#[cfg(feature = "python_bridge")]
pub mod bridge;
#[cfg(feature = "python_bridge")]
pub mod capture;
#[cfg(feature = "python_bridge")]
pub mod continuation;
#[cfg(feature = "python_bridge")]
mod debug_state;
#[cfg(feature = "python_bridge")]
pub mod dispatch;
#[cfg(feature = "python_bridge")]
pub mod do_ctrl;
#[cfg(feature = "python_bridge")]
pub mod doeff_generator;
#[cfg(feature = "python_bridge")]
pub mod driver;
#[cfg(feature = "python_bridge")]
pub mod effect;
#[cfg(feature = "python_bridge")]
pub mod error;
#[cfg(feature = "python_bridge")]
pub mod frame;
#[cfg(feature = "python_bridge")]
pub mod handler;
#[cfg(feature = "python_bridge")]
pub mod ids;
#[cfg(feature = "python_bridge")]
mod interceptor_state;
#[cfg(feature = "python_bridge")]
pub mod ir_stream;
#[cfg(feature = "python_bridge")]
pub mod kleisli;
#[cfg(feature = "python_bridge")]
pub mod py_key;
#[cfg(feature = "python_bridge")]
pub mod python_call;
#[cfg(feature = "python_bridge")]
pub mod result;
#[cfg(feature = "python_bridge")]
pub mod rust_store;
#[cfg(feature = "python_bridge")]
pub mod segment;
#[cfg(feature = "python_bridge")]
mod step;
#[cfg(feature = "python_bridge")]
mod trace_state;
#[cfg(feature = "python_bridge")]
pub mod value;
#[cfg(feature = "python_bridge")]
mod vm;
#[cfg(feature = "python_bridge")]
mod vm_logging;

#[cfg(feature = "python_bridge")]
pub use arena::SegmentArena;
#[cfg(feature = "python_bridge")]
pub use bridge::{
    classify_yielded_for_vm, doctrl_tag, doctrl_to_pyexpr_for_vm, install_vm_hooks, is_doexpr_like,
    is_effect_base_like, VmHooks,
};
#[cfg(feature = "python_bridge")]
pub use capture::{
    ActiveChainEntry, DelegationEntry, DispatchAction, EffectResult, FrameId, HandlerAction,
    HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry, HandlerStatus, SpawnSite, TraceEntry,
    TraceFrame, TraceHop,
};
#[cfg(feature = "python_bridge")]
pub use continuation::{Continuation, PyK};
#[cfg(feature = "python_bridge")]
pub use do_ctrl::{DoCtrl, DoExprTag, PyDoCtrlBase, PyDoExprBase};
#[cfg(feature = "python_bridge")]
pub use doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
#[cfg(feature = "python_bridge")]
pub use driver::{Mode, StepEvent};
#[cfg(feature = "python_bridge")]
pub use effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, dispatch_to_pyobject,
    make_execution_context_object, make_get_execution_context_effect, DispatchEffect, Effect,
    PyEffectBase, PyExecutionContext, PyGetExecutionContext,
};
#[cfg(feature = "python_bridge")]
pub use error::VMError;
#[cfg(feature = "python_bridge")]
pub use frame::Frame;
#[cfg(feature = "python_bridge")]
pub use handler::{IRStreamFactory, IRStreamFactoryRef, IRStreamProgram, IRStreamProgramRef};
#[cfg(feature = "python_bridge")]
pub use ids::{ContId, DispatchId, Marker, PromiseId, RunnableId, SegmentId, TaskId};
#[cfg(feature = "python_bridge")]
pub use ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream, StreamLocation};
#[cfg(feature = "python_bridge")]
pub use kleisli::{Kleisli, KleisliDebugInfo, KleisliRef, PyKleisli, RustKleisli};
#[cfg(feature = "python_bridge")]
pub use py_key::HashedPyKey;
#[cfg(feature = "python_bridge")]
pub use python_call::{PendingPython, PyCallOutcome, PythonCall};
#[cfg(feature = "python_bridge")]
pub use result::{PyResultErr, PyResultOk};
#[cfg(feature = "python_bridge")]
pub use rust_store::RustStore;
#[cfg(feature = "python_bridge")]
pub use segment::{Segment, SegmentKind};
#[cfg(feature = "python_bridge")]
pub use step::PyException;
#[cfg(feature = "python_bridge")]
pub use value::{PyTraceFrame, PyTraceHop, Value};
#[cfg(feature = "python_bridge")]
pub use vm::{DebugConfig, DebugLevel, TraceEvent, VM};

//! doeff-vm: cdylib glue for VM core + core effects.

pub mod arena {
    pub use doeff_vm_core::arena::*;
}
pub mod capture {
    pub use doeff_vm_core::capture::*;
}
pub mod continuation {
    pub use doeff_vm_core::continuation::*;
}
pub mod dispatch {
    pub use doeff_vm_core::dispatch::*;
}
pub mod do_ctrl {
    pub use doeff_vm_core::do_ctrl::*;
}
pub mod doeff_generator {
    pub use doeff_vm_core::doeff_generator::*;
}
pub mod driver {
    pub use doeff_vm_core::driver::*;
}
pub mod effect {
    pub use doeff_core_effects::effects::*;
    pub use doeff_vm_core::effect::{
        make_execution_context_object, make_get_execution_context_effect, PyExecutionContext,
        PyGetExecutionContext,
    };
}
pub mod error {
    pub use doeff_vm_core::error::*;
}
pub mod frame {
    pub use doeff_vm_core::frame::*;
}
mod handler {
    pub use doeff_core_effects::handlers::*;
    pub use doeff_vm_core::handler::*;
}
pub mod ids {
    pub use doeff_vm_core::ids::*;
}
pub mod ir_stream {
    pub use doeff_vm_core::ir_stream::*;
}
pub mod kleisli {
    pub use doeff_vm_core::kleisli::*;
}
pub mod py_key {
    pub use doeff_vm_core::py_key::*;
}
pub mod py_shared {
    pub use doeff_vm_core::py_shared::*;
}
pub mod pyvm;
pub mod python_call {
    pub use doeff_vm_core::python_call::*;
}
pub mod rust_store {
    pub use doeff_vm_core::rust_store::*;
}
pub mod scheduler {
    pub use doeff_core_effects::scheduler::*;
}
pub mod segment {
    pub use doeff_vm_core::segment::*;
}
mod step {
    pub use doeff_vm_core::do_ctrl::DoCtrl;
    pub use doeff_vm_core::driver::{Mode, PyException, StepEvent};
    pub use doeff_vm_core::python_call::{PendingPython, PyCallOutcome, PythonCall};
}
pub mod value {
    pub use doeff_vm_core::value::*;
}
mod vm {
    pub use doeff_vm_core::{DebugConfig, DebugLevel, RustStore, TraceEvent, VM};
}

// Re-exports for convenience
pub use arena::SegmentArena;
pub use capture::{
    ActiveChainEntry, CaptureEvent, DelegationEntry, DispatchAction, EffectResult, FrameId,
    HandlerAction, HandlerDispatchEntry, HandlerKind, HandlerSnapshotEntry, HandlerStatus,
    SpawnSite, TraceEntry, TraceFrame, TraceHop,
};
pub use continuation::Continuation;
pub use dispatch::{Dispatch, HandlerActivation};
pub use do_ctrl::DoCtrl;
pub use doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
pub use driver::{Mode, StepEvent};
pub use effect::*;
pub use error::VMError;
pub use frame::Frame;
pub use handler::*;
pub use ids::{ContId, DispatchId, Marker, PromiseId, RunnableId, SegmentId, TaskId};
pub use ir_stream::{IRStream, IRStreamRef, IRStreamStep, PythonGeneratorStream, StreamLocation};
pub use kleisli::{Kleisli, KleisliDebugInfo, KleisliRef, PyKleisli, RustKleisli};
pub use py_key::HashedPyKey;
pub use doeff_vm_core::DoExprTag;
pub use pyvm::PyVM;
pub use python_call::{PendingPython, PyCallOutcome, PythonCall};
pub use rust_store::RustStore;
pub use segment::{Segment, SegmentKind};
pub use step::PyException;
pub use value::Value;
pub use vm::VM;

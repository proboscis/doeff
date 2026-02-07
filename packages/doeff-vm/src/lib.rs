//! doeff-vm: Rust VM for algebraic effects with PyO3 Python bindings.
//!
//! This crate implements the VM specified in SPEC-008.
//!
//! # Architecture
//!
//! - **3-layer state model**: Internals / RustStore / PyStore
//! - **Mode-based step machine**: Deliver, Throw, HandleYield, Return
//! - **Segment-based continuations**: Arc snapshots for one-shot semantics
//! - **All effects dispatch**: No bypass for stdlib effects

pub mod arena;
pub mod continuation;
pub mod effect;
pub mod error;
pub mod frame;
pub mod handler;
pub mod ids;
pub mod py_shared;
pub mod pyvm;
pub mod scheduler;
pub mod segment;
pub mod step;
pub mod value;
pub mod vm;

// Re-exports for convenience
pub use arena::SegmentArena;
pub use continuation::Continuation;
pub use effect::Effect;
pub use error::VMError;
pub use frame::Frame;
pub use handler::{
    Handler, HandlerEntry, ReaderHandlerFactory, StateHandlerFactory, WriterHandlerFactory,
};
pub use ids::{CallbackId, ContId, DispatchId, Marker, RunnableId, SegmentId};
pub use pyvm::{PyStdlib, PyVM};
pub use segment::{Segment, SegmentKind};
pub use step::{
    DoCtrl, Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent,
    Yielded,
};
pub use value::Value;
pub use vm::{Callback, DispatchContext, RustStore, VM};

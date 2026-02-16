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
pub mod capture;
pub mod continuation;
pub mod dispatch;
pub mod do_ctrl;
pub mod driver;
mod effect;
pub mod error;
pub mod frame;
mod handler;
pub mod ids;
pub mod py_shared;
pub mod python_call;
pub mod pyvm;
pub mod rust_store;
pub mod scheduler;
pub mod segment;
mod step;
pub mod value;
mod vm;
pub mod yielded;

// Re-exports for convenience
pub use arena::SegmentArena;
pub use capture::{
    CaptureEvent, DelegationEntry, DispatchAction, FrameId, HandlerAction, HandlerKind, TraceEntry,
};
pub use continuation::Continuation;
pub use dispatch::DispatchContext;
pub use do_ctrl::DoCtrl;
pub use driver::{Mode, StepEvent};
pub use effect::{Effect, PyAsk, PyGet, PyModify, PyPut, PyTell};
pub use error::VMError;
pub use frame::Frame;
pub use handler::{
    Handler, HandlerEntry, ReaderHandlerFactory, StateHandlerFactory, WriterHandlerFactory,
};
pub use ids::{CallbackId, ContId, DispatchId, Marker, RunnableId, SegmentId};
pub use python_call::{PendingPython, PyCallOutcome, PythonCall};
pub use pyvm::{DoExprTag, PyStdlib, PyVM};
pub use rust_store::RustStore;
pub use segment::{Segment, SegmentKind};
pub use step::PyException;
pub use value::Value;
pub use vm::{Callback, VM};
pub use yielded::Yielded;

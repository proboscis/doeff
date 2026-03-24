//! doeff-vm-core: language-agnostic effect handler VM.

pub mod handle;
pub mod py_shared;

#[cfg(feature = "python_bridge")]
pub mod arena;
#[cfg(feature = "python_bridge")]
pub mod bridge;
#[cfg(feature = "python_bridge")]
pub mod continuation;
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
pub mod ir_stream;
#[cfg(feature = "python_bridge")]
pub mod kleisli;
#[cfg(feature = "python_bridge")]
pub mod memory_stats;
#[cfg(feature = "python_bridge")]
pub mod py_key;
#[cfg(feature = "python_bridge")]
pub mod python_call;
#[cfg(feature = "python_bridge")]
pub mod result;
#[cfg(feature = "python_bridge")]
pub mod scope_store;
#[cfg(feature = "python_bridge")]
pub mod segment;
#[cfg(feature = "python_bridge")]
pub mod value;
#[cfg(feature = "python_bridge")]
pub mod var_store;
#[cfg(feature = "python_bridge")]
mod vm;

// --- Re-exports ---

#[cfg(feature = "python_bridge")]
pub use arena::FiberArena;
#[cfg(feature = "python_bridge")]
pub use continuation::{Continuation, OwnedControlContinuation, PendingContinuation, PyK};
#[cfg(feature = "python_bridge")]
pub use do_ctrl::DoCtrl;
#[cfg(feature = "python_bridge")]
pub use driver::{Mode, StepResult};
#[cfg(feature = "python_bridge")]
pub use effect::DispatchEffect;
#[cfg(feature = "python_bridge")]
pub use error::VMError;
#[cfg(feature = "python_bridge")]
pub use frame::Frame;
#[cfg(feature = "python_bridge")]
pub use ids::{FiberId, Marker, SegmentId, VarId};
#[cfg(feature = "python_bridge")]
pub use ir_stream::{IRStream, IRStreamRef, StreamStep};
#[cfg(feature = "python_bridge")]
pub use segment::Fiber;
#[cfg(feature = "python_bridge")]
pub use value::{Callable, CallableRef, Value};
#[cfg(feature = "python_bridge")]
pub use var_store::VarStore;
#[cfg(feature = "python_bridge")]
pub use vm::VM;

//! IRStream — the program/generator abstraction.
//!
//! An IRStream produces DoCtrl instructions when stepped.
//! It's the "program counter" — the thing that generates the next instruction.
//!
//! Python generators implement IRStream (via PythonGeneratorStream).
//! Rust handlers implement IRStream directly.
//! The VM doesn't know which — it just calls resume/throw.

use std::fmt;
use std::sync::{Arc, Mutex};

use crate::do_ctrl::DoCtrl;
use crate::driver::ExternalCall;
use crate::memory_stats;
use crate::value::Value;

/// A stream of DoCtrl instructions.
///
/// Language-agnostic interface. The VM steps through this.
pub trait IRStream: fmt::Debug + Send {
    /// Send a value to the stream, get back the next step.
    fn resume(&mut self, value: Value) -> StreamStep;

    /// Signal an error to the stream, get back the next step.
    /// The error is a Value (opaque to the VM).
    fn throw(&mut self, error: Value) -> StreamStep;
}

/// What a stream produces when stepped.
#[derive(Debug)]
pub enum StreamStep {
    /// Next instruction to evaluate.
    Instruction(DoCtrl),
    /// Stream completed with a value.
    Done(Value),
    /// Stream encountered an error it couldn't handle.
    Error(Value),
    /// Stream needs an external computation (Python call, etc.)
    External(ExternalCall),
}

// ---------------------------------------------------------------------------
// IRStreamRef — reference-counted stream handle
// ---------------------------------------------------------------------------

#[derive(Debug)]
struct TrackedIRStream {
    stream: Mutex<Box<dyn IRStream>>,
}

impl Drop for TrackedIRStream {
    fn drop(&mut self) {
        memory_stats::unregister_ir_stream();
    }
}

#[derive(Clone)]
pub struct IRStreamRef {
    inner: Arc<TrackedIRStream>,
}

impl fmt::Debug for IRStreamRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "IRStreamRef(...)")
    }
}

impl IRStreamRef {
    pub fn new(stream: Box<dyn IRStream>) -> Self {
        memory_stats::register_ir_stream();
        IRStreamRef {
            inner: Arc::new(TrackedIRStream {
                stream: Mutex::new(stream),
            }),
        }
    }

    pub fn resume(&self, value: Value) -> StreamStep {
        self.inner
            .stream
            .lock()
            .expect("IRStream lock poisoned")
            .resume(value)
    }

    pub fn throw(&self, error: Value) -> StreamStep {
        self.inner
            .stream
            .lock()
            .expect("IRStream lock poisoned")
            .throw(error)
    }
}

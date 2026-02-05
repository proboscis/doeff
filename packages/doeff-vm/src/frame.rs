//! Frame types for the continuation stack.
//!
//! Frames represent pending computations in a segment.

use pyo3::prelude::*;

use crate::value::Value;
use crate::vm::VM;

/// Control flow result from frame execution.
#[derive(Debug)]
pub enum ControlFlow {
    /// Continue with this value
    Continue(Value),
    /// An effect was yielded
    Effect(crate::effect::Effect),
    /// A control primitive was yielded
    Primitive(crate::primitives::ControlPrimitive),
    /// Computation completed
    Done(Value),
    /// Error occurred
    Error(crate::error::VMError),
    /// Need to call into Python
    PythonCall(PythonCall),
}

/// A pending call into Python code.
#[derive(Debug)]
pub struct PythonCall {
    /// The Python callable to invoke
    pub callable: Py<PyAny>,
    /// Arguments to pass
    pub args: Vec<Value>,
    /// What to do with the result
    pub continuation: PythonCallContinuation,
}

/// How to handle the result of a Python call.
#[derive(Debug, Clone, Copy)]
pub enum PythonCallContinuation {
    /// Send result to current frame
    SendToFrame,
    /// Start as new generator in current segment
    StartGenerator,
    /// Handle yielded value from generator
    HandleYield,
}

/// A frame in the continuation stack.
///
/// Rust manages the frame structure; Python generators are leaves.
pub enum Frame {
    /// Rust-native return frame (for built-in handlers).
    ///
    /// The callback receives the value and VM, returning control flow.
    RustReturn {
        /// Callback to receive the value
        callback: Box<dyn FnOnce(Value, &mut VM) -> ControlFlow + Send>,
    },

    /// Python generator frame (user code or Python handlers).
    PythonGenerator {
        /// The Python generator object (GIL-independent storage)
        generator: Py<PyAny>,
        /// Whether this generator has been started (first __next__ called)
        started: bool,
    },
}

impl std::fmt::Debug for Frame {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Frame::RustReturn { .. } => f.debug_struct("RustReturn").finish_non_exhaustive(),
            Frame::PythonGenerator { started, .. } => f
                .debug_struct("PythonGenerator")
                .field("started", started)
                .finish(),
        }
    }
}

impl Frame {
    /// Create a new Rust return frame.
    pub fn rust_return<F>(callback: F) -> Self
    where
        F: FnOnce(Value, &mut VM) -> ControlFlow + Send + 'static,
    {
        Frame::RustReturn {
            callback: Box::new(callback),
        }
    }

    /// Create a new Python generator frame.
    pub fn python_generator(generator: Py<PyAny>) -> Self {
        Frame::PythonGenerator {
            generator,
            started: false,
        }
    }

    /// Check if this is a Rust frame.
    pub fn is_rust(&self) -> bool {
        matches!(self, Frame::RustReturn { .. })
    }

    /// Check if this is a Python generator frame.
    pub fn is_python(&self) -> bool {
        matches!(self, Frame::PythonGenerator { .. })
    }
}

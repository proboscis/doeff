//! Core VM struct and module declarations.
//!
//! The VM has 5 registers (OCaml 5 alignment):
//!   arena, current_fiber, heap, mode, pending_python

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::arena::FiberArena;
use crate::continuation::Continuation;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::frame::Frame;
use crate::ids::{FiberId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::python_call::PendingPython;
use crate::segment::Fiber;
use crate::value::Value;

pub use crate::var_store::VarStore;

// Sub-modules implement operations on VM
#[path = "vm/dispatch.rs"]
mod dispatch_impl;

#[path = "vm/handler.rs"]
mod handler_impl;

#[path = "vm/step.rs"]
mod step_impl;

#[path = "vm/var_store.rs"]
mod var_store_impl;

// ---------------------------------------------------------------------------
// VM struct — 5 registers
// ---------------------------------------------------------------------------

pub struct VM {
    pub segments: FiberArena,
    pub var_store: VarStore,
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub current_segment: Option<SegmentId>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            var_store: VarStore::new(),
            mode: Mode::Deliver(Value::Unit),
            pending_python: None,
            current_segment: None,
        }
    }

    pub fn begin_run_session(&mut self) {
        self.segments.clear();
        self.var_store.clear_run_local();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
    }

    pub fn end_active_run_session(&mut self) {
        self.segments.clear();
        self.segments.shrink_to_fit();
        self.var_store.clear_run_local();
        self.var_store.shrink_run_local_to_fit();
        self.mode = Mode::Deliver(Value::Unit);
        self.pending_python = None;
        self.current_segment = None;
    }

    /// Allocate a fiber in the arena.
    pub fn alloc_segment(&mut self, fiber: Fiber) -> FiberId {
        self.segments.alloc_segment(fiber)
    }

    /// Get the current segment's parent.
    pub fn parent_segment(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.segments.get(seg_id).and_then(|s| s.parent)
    }
}

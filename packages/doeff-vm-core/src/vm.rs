//! Core VM struct — 5 registers.

use crate::arena::FiberArena;
use crate::driver::Mode;
use crate::ids::{FiberId, SegmentId};
use crate::segment::Fiber;
use crate::value::Value;

pub use crate::var_store::VarStore;

// Sub-modules
#[path = "vm/dispatch.rs"]
mod dispatch_impl;

#[path = "vm/handler.rs"]
mod handler_impl;

#[path = "vm/step.rs"]
mod step_impl;

#[path = "vm/var_store.rs"]
mod var_store_impl;

/// VM — 5 registers (OCaml 5 alignment).
pub struct VM {
    pub segments: FiberArena,
    pub var_store: VarStore,
    pub mode: Mode,
    pub pending_external: Option<crate::driver::ExternalCall>,
    pub current_segment: Option<SegmentId>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            var_store: VarStore::new(),
            mode: Mode::Send(Value::Unit),
            pending_external: None,
            current_segment: None,
        }
    }

    pub fn begin_run_session(&mut self) {
        self.segments.clear();
        self.var_store.clear_run_local();
        self.mode = Mode::Send(Value::Unit);
        self.pending_external = None;
        self.current_segment = None;
    }

    pub fn end_active_run_session(&mut self) {
        self.segments.clear();
        self.segments.shrink_to_fit();
        self.var_store.clear_run_local();
        self.var_store.shrink_run_local_to_fit();
        self.mode = Mode::Send(Value::Unit);
        self.pending_external = None;
        self.current_segment = None;
    }

    pub fn alloc_segment(&mut self, fiber: Fiber) -> FiberId {
        self.segments.alloc(fiber)
    }

    pub fn parent_segment(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.segments.get(seg_id).and_then(|s| s.parent)
    }
}

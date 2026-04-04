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
    /// Execution context captured at the first error point (before unwinding).
    /// GetExecutionContext returns this if set, giving the error-site context
    /// rather than the post-unwind context.
    pub last_error_context: Option<Vec<Value>>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            var_store: VarStore::new(),
            mode: Mode::Send(Value::Unit),
            pending_external: None,
            current_segment: None,
            last_error_context: None,
        }
    }

    pub fn begin_run_session(&mut self) {
        self.segments.clear();
        self.var_store.clear_run_local();
        self.mode = Mode::Send(Value::Unit);
        self.pending_external = None;
        self.current_segment = None;
        self.last_error_context = None;
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

    /// Free fibers from continuations that were dropped without being consumed.
    ///
    /// When a handler drops a continuation (e.g., scheduler ignoring
    /// TaskCompleted's k), the fiber chain stays orphaned in the arena.
    /// This walks each orphaned chain from head→last following parent
    /// pointers, freeing every fiber.
    pub fn reclaim_orphaned_fibers(&mut self) {
        let orphans = crate::continuation::drain_orphan_fibers();
        for head in orphans {
            let mut cursor = Some(head);
            while let Some(fid) = cursor {
                cursor = self.segments.get(fid).and_then(|s| s.parent);
                self.segments.free(fid);
            }
        }
    }

    pub fn alloc_segment(&mut self, fiber: Fiber) -> FiberId {
        self.segments.alloc(fiber)
    }

    pub fn parent_segment(&self, seg_id: SegmentId) -> Option<SegmentId> {
        self.segments.get(seg_id).and_then(|s| s.parent)
    }

    /// Collect a traceback from a fiber, walking the parent chain.
    ///
    /// For each fiber, queries each Program frame's stream for live source location.
    /// Returns frames from innermost (current fiber, topmost frame) to outermost (root).
    pub fn collect_traceback(&self, start: SegmentId) -> Vec<crate::ir_stream::StreamSourceLocation> {
        let mut frames = Vec::new();
        let mut current = Some(start);

        while let Some(seg_id) = current {
            let Some(seg) = self.segments.get(seg_id) else { break };

            // Walk frames top-to-bottom (innermost first)
            for frame in seg.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        frames.push(loc);
                    }
                }
            }

            current = seg.parent;
        }

        frames
    }

    /// Collect traceback from the current segment.
    pub fn collect_current_traceback(&self) -> Vec<crate::ir_stream::StreamSourceLocation> {
        match self.current_segment {
            Some(seg_id) => self.collect_traceback(seg_id),
            None => Vec::new(),
        }
    }

    /// Collect rich execution context — program frames + handler chain.
    ///
    /// Walks fiber chain from current_segment upward.
    /// Returns outermost-first (root first, current frame last).
    /// Ends with a single handler entry listing all handlers in scope.
    pub fn collect_rich_execution_context(&self) -> Vec<Value> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.collect_rich_context_from(seg_id)
    }

    /// Collect rich execution context starting from an arbitrary segment.
    pub fn collect_rich_context_from(&self, start: SegmentId) -> Vec<Value> {
        let mut frames = Vec::new();
        let mut first_boundary: Option<crate::ids::SegmentId> = None;
        let mut cursor = Some(start);

        while let Some(fid) = cursor {
            let Some(seg) = self.segments.get(fid) else { break };

            if first_boundary.is_none() {
                if seg.handler.as_ref().and_then(|h| h.prompt_boundary()).is_some() {
                    first_boundary = Some(fid);
                }
            }

            for frame in seg.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        frames.push(Value::List(vec![
                            Value::String("frame".to_string()),
                            Value::String(loc.func_name),
                            Value::String(loc.source_file),
                            Value::Int(loc.source_line as i64),
                        ]));
                    }
                }
            }

            cursor = seg.parent;
        }

        frames.reverse();

        if let Some(boundary_id) = first_boundary {
            let handler_chain = self.handlers_in_caller_chain(boundary_id);
            let handler_names: Vec<Value> = handler_chain
                .into_iter()
                .map(|entry| Value::String(
                    entry.handler.name().unwrap_or_else(|| "<handler>".to_string())
                ))
                .collect();
            if !handler_names.is_empty() {
                frames.push(Value::List(vec![
                    Value::String("handler".to_string()),
                    Value::String("chain".to_string()),
                    Value::List(handler_names),
                ]));
            }
        }

        frames
    }
}

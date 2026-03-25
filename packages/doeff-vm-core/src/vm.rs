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

    /// Collect rich execution context — program frames + handler boundaries.
    ///
    /// Walks fiber chain from current_segment upward. For each fiber:
    /// - Program frames: [kind="frame", func_name, source_file, source_line]
    /// - Handler boundaries: [kind="handler", handler_name, handler_names_in_scope...]
    ///
    /// Returns innermost-first (current fiber first, root last).
    pub fn collect_rich_execution_context(&self) -> Vec<Value> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };

        let mut entries = Vec::new();
        let mut cursor = Some(seg_id);

        while let Some(fid) = cursor {
            let Some(seg) = self.segments.get(fid) else { break };

            // If this fiber is a handler boundary, emit handler entry
            if let Some(handler) = &seg.handler {
                if let Some(prompt) = handler.prompt_boundary() {
                    let handler_name = prompt.handler.name()
                        .unwrap_or_else(|| "<handler>".to_string());

                    // Collect all handler names in scope from this point
                    let handler_chain = self.handlers_in_caller_chain(fid);
                    let mut handler_names = Vec::new();
                    for entry in &handler_chain {
                        handler_names.push(Value::String(
                            entry.handler.name().unwrap_or_else(|| "<handler>".to_string())
                        ));
                    }

                    entries.push(Value::List(vec![
                        Value::String("handler".to_string()),
                        Value::String(handler_name),
                        Value::List(handler_names),
                    ]));
                }
            }

            // Walk frames top-to-bottom (innermost first)
            for frame in seg.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        entries.push(Value::List(vec![
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

        entries
    }
}

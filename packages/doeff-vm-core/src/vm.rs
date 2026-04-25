//! Core VM struct.

use crate::arena::FiberArena;
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

/// VM runtime state.
pub struct VM {
    pub segments: FiberArena,
    pub var_store: VarStore,
    pub current_segment: Option<SegmentId>,
    /// Transient slot used by `eval_perform`/`eval_perform_with_k` to thread
    /// the chain backup through to the next `push_stream_value` call. Set
    /// before calling a handler callable; consumed when the resulting Stream
    /// frame is pushed (the backup ends up in `Frame::Program.chain_backup`).
    /// Always None outside that narrow window.
    pub pending_handler_chain_backup: Option<crate::continuation::Continuation>,
}

impl VM {
    pub fn new() -> Self {
        VM {
            segments: FiberArena::new(),
            var_store: VarStore::new(),
            current_segment: None,
            pending_handler_chain_backup: None,
        }
    }

    pub fn begin_run_session(&mut self) {
        self.segments.clear();
        self.var_store.clear_run_local();
        self.current_segment = None;
        self.pending_handler_chain_backup = None;
    }

    pub fn end_active_run_session(&mut self) {
        self.segments.clear();
        self.segments.shrink_to_fit();
        self.var_store.clear_run_local();
        self.var_store.shrink_run_local_to_fit();
        self.current_segment = None;
        self.pending_handler_chain_backup = None;
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
    pub fn collect_traceback(
        &self,
        start: SegmentId,
    ) -> Vec<crate::ir_stream::StreamSourceLocation> {
        let mut frames = Vec::new();
        let mut current = Some(start);

        while let Some(seg_id) = current {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };

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
    ///
    /// Consecutive identical frames (same func, file, line) are compressed
    /// into a single entry with an appended repeat count — this prevents
    /// handler Resume loops from flooding the traceback (see #386).
    pub fn collect_rich_context_from(&self, start: SegmentId) -> Vec<Value> {
        // Phase 1: collect raw (func_name, source_file, source_line) tuples.
        let mut raw: Vec<(String, String, u32)> = Vec::new();
        let mut first_boundary: Option<crate::ids::SegmentId> = None;
        let mut cursor = Some(start);

        while let Some(fid) = cursor {
            let Some(seg) = self.segments.get(fid) else {
                break;
            };

            if first_boundary.is_none() {
                if seg
                    .handler
                    .as_ref()
                    .and_then(|h| h.prompt_boundary())
                    .is_some()
                {
                    first_boundary = Some(fid);
                }
            }

            for frame in seg.frames.iter().rev() {
                if let crate::frame::Frame::Program { stream, .. } = frame {
                    if let Some(loc) = stream.source_location() {
                        raw.push((loc.func_name, loc.source_file, loc.source_line));
                    }
                }
            }

            cursor = seg.parent;
        }

        raw.reverse();

        // Phase 2: deduplicate consecutive identical frames.
        let mut frames = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let (ref func, ref file, line) = raw[i];
            let mut count: i64 = 1;
            while i + (count as usize) < raw.len() {
                let (ref nf, ref nfile, nline) = raw[i + count as usize];
                if nf == func && nfile == file && nline == line {
                    count += 1;
                } else {
                    break;
                }
            }
            let mut entry = vec![
                Value::String("frame".to_string()),
                Value::String(func.clone()),
                Value::String(file.clone()),
                Value::Int(line as i64),
            ];
            if count > 1 {
                entry.push(Value::Int(count));
            }
            frames.push(Value::List(entry));
            i += count as usize;
        }

        if let Some(boundary_id) = first_boundary {
            let handler_chain = self.handlers_in_caller_chain(boundary_id);
            let handler_names: Vec<Value> = handler_chain
                .into_iter()
                .map(|entry| {
                    Value::String(
                        entry
                            .handler
                            .name()
                            .unwrap_or_else(|| "<handler>".to_string()),
                    )
                })
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

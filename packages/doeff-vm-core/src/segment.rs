//! Fiber: a stack chunk in the fiber chain.
//!
//! Matches OCaml 5's stack_info:
//!   sp              → frames (the stack)
//!   exception_ptr   → (handled by Python, not stored on fiber)
//!   handler         → handler (Option<Handler>)
//!   parent          → parent pointer (on the handler in OCaml 5, on fiber in doeff)
//!
//! Extensions (intercept, mask) are on the Handler, not the Fiber.
//! Pending state (effect, error context) is on VM registers, not the Fiber.

use std::sync::Arc;

use crate::do_ctrl::InterceptMode;
use crate::frame::CallMetadata;
use crate::frame::Frame;
use crate::ids::{FiberId, Marker};
use crate::kleisli::KleisliRef;
use crate::memory_stats;
use crate::py_shared::PyShared;
pub use crate::scope_store::ScopeStore;

// ---------------------------------------------------------------------------
// Handler — the handler delimiter at the boundary fiber
// ---------------------------------------------------------------------------

/// Handler installed on a boundary fiber.
/// Matches OCaml 5's stack_handler (handle_value, handle_exn, handle_effect + parent).
/// Extensions (intercept, mask) are layered on the handler, not the fiber.
#[derive(Debug, Clone)]
pub struct Handler {
    pub marker: Marker,
    pub prompt: Option<PromptBoundary>,
    pub intercept: Option<InterceptSpec>,
    pub mask: Option<MaskSpec>,
}

#[derive(Debug, Clone)]
pub struct PromptBoundary {
    pub handled_marker: Marker,
    pub handler: KleisliRef,
    pub types: Option<Arc<Vec<PyShared>>>,
}

#[derive(Debug, Clone)]
pub struct InterceptSpec {
    pub interceptor: KleisliRef,
    pub types: Option<Vec<PyShared>>,
    pub mode: InterceptMode,
    pub metadata: Option<CallMetadata>,
}

#[derive(Debug, Clone)]
pub struct MaskSpec {
    pub masked_effects: Vec<PyShared>,
    pub behind: bool,
}

impl Handler {
    pub fn prompt(
        marker: Marker,
        handled_marker: Marker,
        handler: KleisliRef,
        types: Option<Arc<Vec<PyShared>>>,
    ) -> Self {
        Self {
            marker,
            prompt: Some(PromptBoundary {
                handled_marker,
                handler,
                types,
            }),
            intercept: None,
            mask: None,
        }
    }

    pub fn intercept(
        marker: Marker,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    ) -> Self {
        Self {
            marker,
            prompt: None,
            intercept: Some(InterceptSpec {
                interceptor,
                types,
                mode,
                metadata,
            }),
            mask: None,
        }
    }

    pub fn mask(marker: Marker, masked_effects: Vec<PyShared>, behind: bool) -> Self {
        Self {
            marker,
            prompt: None,
            intercept: None,
            mask: Some(MaskSpec {
                masked_effects,
                behind,
            }),
        }
    }

    pub fn marker(&self) -> Marker {
        self.marker
    }

    pub fn prompt_boundary(&self) -> Option<&PromptBoundary> {
        self.prompt.as_ref()
    }

    pub fn intercept_boundary(&self) -> Option<&InterceptSpec> {
        self.intercept.as_ref()
    }

    pub fn mask_boundary(&self) -> Option<&MaskSpec> {
        self.mask.as_ref()
    }

    pub fn handled_marker(&self) -> Option<Marker> {
        self.prompt.as_ref().map(|p| p.handled_marker)
    }
}

// ---------------------------------------------------------------------------
// Fiber — 3 fields (OCaml 5 alignment)
// ---------------------------------------------------------------------------

/// A fiber (stack chunk) in the arena.
///
/// 3 fields only:
///   frames  — the stack (Vec<Frame>)
///   parent  — parent fiber in the chain
///   handler — handler delimiter (None = normal fiber, Some = boundary)
///
/// NO pending_effect, pending_continuation, pending_error_context,
/// interceptor_eval_depth, interceptor_skip_stack, pending_program_dispatch.
/// Those belong on VM registers or the Handler.
#[derive(Debug)]
pub struct Fiber {
    pub frames: Vec<Frame>,
    pub parent: Option<FiberId>,
    pub handler: Option<Handler>,
}

impl Fiber {
    pub fn new(parent: Option<FiberId>) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            handler: None,
        }
    }

    pub fn new_boundary(parent: Option<FiberId>, handler: Handler) -> Self {
        memory_stats::register_segment();
        Fiber {
            frames: Vec::new(),
            parent,
            handler: Some(handler),
        }
    }

    pub fn push_frame(&mut self, frame: Frame) {
        self.frames.push(frame);
    }

    pub fn pop_frame(&mut self) -> Option<Frame> {
        self.frames.pop()
    }

    /// Is this a handler boundary fiber?
    pub fn is_boundary(&self) -> bool {
        self.handler.is_some()
    }

    /// Is this a prompt (handler) boundary?
    pub fn is_prompt_boundary(&self) -> bool {
        self.handler.as_ref().and_then(|h| h.prompt_boundary()).is_some()
    }

    /// Get the handled marker if this is a prompt boundary.
    pub fn handled_marker(&self) -> Option<Marker> {
        self.handler.as_ref().and_then(|h| h.handled_marker())
    }

    /// Get the prompt boundary handler.
    pub fn prompt_handler(&self) -> Option<&KleisliRef> {
        self.handler.as_ref()
            .and_then(|h| h.prompt_boundary())
            .map(|p| &p.handler)
    }
}

impl Drop for Fiber {
    fn drop(&mut self) {
        memory_stats::unregister_segment();
    }
}

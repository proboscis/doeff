//! Dispatch-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use crate::arena::SegmentArena;
use crate::capture::{CaptureEvent, HandlerAction};
use crate::continuation::Continuation;
use crate::dispatch::DispatchContext;
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::handler::{Handler, HandlerEntry};
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::step::PyException;

#[derive(Debug, Clone, Default)]
pub(crate) struct DispatchState {
    dispatch_stack: Vec<DispatchContext>,
}

pub(crate) struct WithHandlerPlan {
    pub(crate) handler_marker: Marker,
    pub(crate) outside_seg_id: SegmentId,
    pub(crate) outside_scope: Vec<Marker>,
    pub(crate) handler: Handler,
    pub(crate) py_identity: Option<PyShared>,
}

impl DispatchState {
    pub(crate) fn depth(&self) -> usize {
        self.dispatch_stack.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.dispatch_stack.is_empty()
    }

    pub(crate) fn contexts(&self) -> &[DispatchContext] {
        &self.dispatch_stack
    }

    pub(crate) fn top(&self) -> Option<&DispatchContext> {
        self.dispatch_stack.last()
    }

    pub(crate) fn top_mut(&mut self) -> Option<&mut DispatchContext> {
        self.dispatch_stack.last_mut()
    }

    pub(crate) fn top_cloned(&self) -> Option<DispatchContext> {
        self.dispatch_stack.last().cloned()
    }

    pub(crate) fn get(&self, idx: usize) -> Option<&DispatchContext> {
        self.dispatch_stack.get(idx)
    }

    pub(crate) fn get_mut(&mut self, idx: usize) -> Option<&mut DispatchContext> {
        self.dispatch_stack.get_mut(idx)
    }

    pub(crate) fn push_dispatch(&mut self, ctx: DispatchContext) {
        self.dispatch_stack.push(ctx);
    }

    pub(crate) fn active_top_dispatch_id(&self) -> Option<DispatchId> {
        self.top().filter(|ctx| !ctx.completed).map(|ctx| ctx.dispatch_id)
    }

    pub(crate) fn top_effect_cloned(&self) -> Option<DispatchEffect> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| !ctx.completed)
            .map(|ctx| ctx.effect.clone())
    }

    pub(crate) fn top_original_exception(&self) -> Option<(DispatchId, PyException)> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| !ctx.completed)
            .and_then(|ctx| ctx.original_exception.clone().map(|exc| (ctx.dispatch_id, exc)))
    }

    pub(crate) fn find_by_dispatch_id(&self, dispatch_id: DispatchId) -> Option<&DispatchContext> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
    }

    pub(crate) fn dispatch_is_execution_context_effect(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(|ctx| ctx.is_execution_context_effect)
    }

    pub(crate) fn mark_completed_at(
        &mut self,
        idx: usize,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        if let Some(ctx) = self.dispatch_stack.get_mut(idx) {
            ctx.completed = true;
            consumed_cont_ids.insert(ctx.k_user.cont_id);
        }
    }

    pub(crate) fn iter_mut_from(
        &mut self,
        start: usize,
    ) -> impl Iterator<Item = &mut DispatchContext> {
        self.dispatch_stack.iter_mut().skip(start)
    }

    pub(crate) fn current_scope_chain(
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Vec<Marker> {
        current_segment
            .and_then(|id| segments.get(id))
            .map(|seg| seg.scope_chain.clone())
            .unwrap_or_default()
    }

    pub(crate) fn lazy_pop_completed(&mut self) {
        while let Some(top) = self.dispatch_stack.last() {
            if top.completed {
                self.dispatch_stack.pop();
            } else {
                break;
            }
        }
    }

    pub(crate) fn visible_handlers(
        &self,
        scope_chain: &[Marker],
        consumed_cont_ids: &HashSet<ContId>,
    ) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };

        if top.completed || consumed_cont_ids.contains(&top.k_user.cont_id) {
            return scope_chain.to_vec();
        }

        let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
            .iter()
            .copied()
            .collect();

        scope_chain
            .iter()
            .copied()
            .filter(|marker| !busy.contains(marker))
            .collect()
    }

    pub(crate) fn find_matching_handler(
        handlers: &HashMap<Marker, HandlerEntry>,
        handler_chain: &[Marker],
        effect: &DispatchEffect,
    ) -> Result<(usize, Marker, HandlerEntry), VMError> {
        for (idx, &marker) in handler_chain.iter().enumerate() {
            let Some(entry) = handlers.get(&marker) else {
                return Err(VMError::internal(format!(
                    "find_matching_handler: missing handler marker {} at index {}",
                    marker.raw(),
                    idx
                )));
            };
            if entry.handler.can_handle(effect)? {
                return Ok((idx, marker, entry.clone()));
            }
        }
        Err(VMError::no_matching_handler(effect.clone()))
    }

    pub(crate) fn check_dispatch_completion(&mut self, k: &Continuation) {
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id
                    && top.k_user.cont_id == k.cont_id
                    && top.k_user.parent.is_none()
                {
                    top.completed = true;
                }
            }
        }
    }

    pub(crate) fn dispatch_supports_error_context_conversion(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .is_some_and(|ctx| ctx.supports_error_context_conversion)
    }

    pub(crate) fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| !ctx.completed && ctx.original_exception.is_some())
            .and_then(|ctx| ctx.original_exception.clone())
    }

    pub(crate) fn original_exception_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<PyException> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .and_then(|ctx| ctx.original_exception.clone())
    }

    pub(crate) fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch_id = k.dispatch_id?;
        let ctx = self
            .dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)?;
        let original = ctx.original_exception.clone()?;
        let mut cursor = Some(ctx.k_user.clone());
        while let Some(current) = cursor {
            if current.cont_id == k.cont_id {
                return Some((dispatch_id, original, current.parent.is_none()));
            }
            cursor = current.parent.as_ref().map(|parent| (**parent).clone());
        }
        None
    }

    pub(crate) fn mark_dispatch_threw(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        self.mark_dispatch_completed(dispatch_id, consumed_cont_ids);
    }

    pub(crate) fn mark_dispatch_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        if let Some(ctx) = self
            .dispatch_stack
            .iter_mut()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
        {
            ctx.completed = true;
            consumed_cont_ids.insert(ctx.k_user.cont_id);
        }
    }

    pub(crate) fn dispatch_has_terminal_handler_action(
        &self,
        dispatch_id: DispatchId,
        capture_log: &[CaptureEvent],
    ) -> bool {
        capture_log.iter().rev().any(|event| match event {
            CaptureEvent::HandlerCompleted {
                dispatch_id: event_dispatch_id,
                action:
                    HandlerAction::Resumed { .. }
                    | HandlerAction::Transferred { .. }
                    | HandlerAction::Returned { .. }
                    | HandlerAction::Threw { .. },
                ..
            } => *event_dispatch_id == dispatch_id,
            _ => false,
        })
    }

    pub(crate) fn prepare_with_handler(
        handler: Handler,
        explicit_py_identity: Option<PyShared>,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Result<WithHandlerPlan, VMError> {
        let handler_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithHandler"));
        };
        let outside_scope = segments
            .get(outside_seg_id)
            .map(|s| s.scope_chain.clone())
            .unwrap_or_default();

        let py_identity = explicit_py_identity.or_else(|| handler.py_identity());
        Ok(WithHandlerPlan {
            handler_marker,
            outside_seg_id,
            outside_scope,
            handler,
            py_identity,
        })
    }
}

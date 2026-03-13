//! Dispatch-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use crate::continuation::Continuation;
use crate::dispatch::Dispatch;
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::ids::{ContId, DispatchId, Marker, SegmentId};
use crate::kleisli::KleisliRef;
use crate::step::PyException;
use crate::trace_state::ActiveChainAssemblyState;

#[derive(Debug, Clone, Default)]
pub(crate) struct DispatchState {
    dispatches: HashMap<DispatchId, Dispatch>,
}

pub(crate) struct WithHandlerPlan {
    pub(crate) handler_marker: Marker,
    pub(crate) outside_seg_id: SegmentId,
    pub(crate) handler: KleisliRef,
}

impl DispatchState {
    pub(crate) fn depth(&self) -> usize {
        self.dispatches.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.dispatches.is_empty()
    }

    pub(crate) fn dispatches_ref(&self) -> &HashMap<DispatchId, Dispatch> {
        &self.dispatches
    }

    pub(crate) fn dispatches_mut(&mut self) -> &mut HashMap<DispatchId, Dispatch> {
        &mut self.dispatches
    }

    pub(crate) fn insert(&mut self, dispatch: Dispatch) {
        self.dispatches.insert(dispatch.dispatch_id, dispatch);
    }

    pub(crate) fn find_by_dispatch_id(&self, dispatch_id: DispatchId) -> Option<&Dispatch> {
        self.dispatches.get(&dispatch_id)
    }

    pub(crate) fn find_mut_by_dispatch_id(
        &mut self,
        dispatch_id: DispatchId,
    ) -> Option<&mut Dispatch> {
        self.dispatches.get_mut(&dispatch_id)
    }

    pub(crate) fn effect_for_dispatch(&self, dispatch_id: DispatchId) -> Option<DispatchEffect> {
        self.find_by_dispatch_id(dispatch_id)
            .map(|d| d.effect.clone())
    }

    pub(crate) fn dispatch_is_execution_context_effect(&self, dispatch_id: DispatchId) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(|d| d.is_execution_context_effect)
    }

    pub(crate) fn mark_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        if let Some(d) = self.dispatches.get_mut(&dispatch_id) {
            d.completed = true;
            consumed_cont_ids.insert(d.k_origin.cont_id);
        }
    }

    pub(crate) fn lazy_pop_completed(&mut self) {
        self.dispatches.retain(|_, d| !d.completed);
    }

    pub(crate) fn check_dispatch_completion(
        &mut self,
        k: &Continuation,
        consumed_cont_ids: &HashSet<ContId>,
    ) {
        let Some(dispatch_id) = k.dispatch_id else {
            return;
        };
        let Some(d) = self.dispatches.get_mut(&dispatch_id) else {
            return;
        };
        // Dispatch is complete when the first activation's k_passed (the original
        // user continuation) has been consumed via Resume or Transfer.
        if let Some(first) = d.activations.first() {
            if consumed_cont_ids.contains(&first.k_passed.cont_id) {
                d.completed = true;
            }
        }
    }

    pub(crate) fn dispatch_supports_error_context_conversion(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.dispatches
            .get(&dispatch_id)
            .is_some_and(|d| d.current_supports_error_context_conversion())
    }

    pub(crate) fn active_error_dispatch_original_exception(&self) -> Option<PyException> {
        self.dispatches
            .values()
            .filter(|d| !d.completed && d.original_exception.is_some())
            .max_by_key(|d| d.dispatch_id)
            .and_then(|d| d.original_exception.clone())
    }

    pub(crate) fn original_exception_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<PyException> {
        self.dispatches
            .get(&dispatch_id)
            .and_then(|d| d.original_exception.clone())
    }

    pub(crate) fn error_dispatch_for_continuation(
        &self,
        k: &Continuation,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch_id = k.dispatch_id?;
        let d = self.dispatches.get(&dispatch_id)?;
        let original = d.original_exception.clone()?;
        let activation = d.current_activation()?;
        // Walk from k_current through parent chain to determine terminality.
        let mut cursor = Some(activation.k_current.clone());
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
        self.mark_completed(dispatch_id, consumed_cont_ids);
    }

    pub(crate) fn mark_dispatch_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        self.mark_completed(dispatch_id, consumed_cont_ids);
    }

    pub(crate) fn dispatch_has_terminal_handler_action(
        &self,
        dispatch_id: DispatchId,
        active_chain_state: &ActiveChainAssemblyState,
    ) -> bool {
        active_chain_state.dispatch_has_terminal_result(dispatch_id)
    }

    pub(crate) fn prepare_with_handler(
        handler: KleisliRef,
        current_segment: Option<SegmentId>,
    ) -> Result<WithHandlerPlan, VMError> {
        let handler_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithHandler"));
        };

        Ok(WithHandlerPlan {
            handler_marker,
            outside_seg_id,
            handler,
        })
    }
}

//! Dispatch-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

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

    pub(crate) fn iter(&self) -> impl Iterator<Item = &Dispatch> {
        self.dispatches.values()
    }

    pub(crate) fn iter_mut(&mut self) -> impl Iterator<Item = &mut Dispatch> {
        self.dispatches.values_mut()
    }

    pub(crate) fn dispatch_ids(&self) -> Vec<DispatchId> {
        self.dispatches.keys().copied().collect()
    }

    pub(crate) fn push_dispatch(&mut self, dispatch: Dispatch) {
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
            .map(|dispatch| dispatch.effect.clone())
    }

    pub(crate) fn dispatch_is_execution_context_effect(&self, dispatch_id: DispatchId) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(|dispatch| dispatch.is_execution_context_effect)
    }

    pub(crate) fn mark_completed(
        &mut self,
        dispatch_id: DispatchId,
        consumed_cont_ids: &mut HashSet<ContId>,
    ) {
        let Some(dispatch) = self.find_mut_by_dispatch_id(dispatch_id) else {
            return;
        };
        dispatch.completed = true;
        if let Some(continuation) = dispatch.current_continuation() {
            consumed_cont_ids.insert(continuation.cont_id);
        }
    }

    pub(crate) fn lazy_pop_completed(&mut self) {
        self.dispatches.retain(|_, dispatch| !dispatch.completed);
    }

    pub(crate) fn dispatch_supports_error_context_conversion(
        &self,
        dispatch_id: DispatchId,
    ) -> bool {
        self.find_by_dispatch_id(dispatch_id)
            .is_some_and(Dispatch::supports_error_context_conversion)
    }

    pub(crate) fn original_exception_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Option<PyException> {
        self.find_by_dispatch_id(dispatch_id)
            .and_then(|dispatch| dispatch.original_exception.clone())
    }

    pub(crate) fn error_dispatch_for_continuation(
        &self,
        k: ContId,
        dispatch_id: DispatchId,
    ) -> Option<(DispatchId, PyException, bool)> {
        let dispatch = self.find_by_dispatch_id(dispatch_id)?;
        let original = dispatch.original_exception.clone()?;
        let activation_idx = dispatch.activation_index_for_cont_id(k)?;
        Some((dispatch_id, original, activation_idx == 0))
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

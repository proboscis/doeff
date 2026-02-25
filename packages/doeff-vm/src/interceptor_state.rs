//! Interceptor-domain state and helper logic for VM composition.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::dispatch::DispatchContext;
use crate::do_ctrl::{CallArg, InterceptMode};
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::HandlerEntry;
use crate::ids::{CallbackId, DispatchId, Marker, SegmentId};
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyPure};
use crate::segment::Segment;
use crate::step::PyException;
use crate::vm::InterceptorEntry;

#[derive(Clone, Default)]
pub(crate) struct InterceptorState {
    interceptors: HashMap<Marker, InterceptorEntry>,
    interceptor_callbacks: HashMap<CallbackId, Marker>,
    interceptor_call_metadata: HashMap<CallbackId, CallMetadata>,
    interceptor_eval_callbacks: HashSet<CallbackId>,
    interceptor_eval_depth: usize,
    interceptor_skip_stack: Vec<Marker>,
}

impl InterceptorState {
    pub(crate) fn clear_for_run(&mut self) {
        self.interceptors.clear();
        self.interceptor_callbacks.clear();
        self.interceptor_call_metadata.clear();
        self.interceptor_eval_callbacks.clear();
        self.interceptor_eval_depth = 0;
        self.interceptor_skip_stack.clear();
    }

    pub(crate) fn current_chain(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        scope_chain
            .iter()
            .copied()
            .filter(|marker| self.interceptors.contains_key(marker))
            .collect()
    }

    pub(crate) fn visible_to_active_handler(
        &self,
        interceptor_marker: Marker,
        dispatch_stack: &[DispatchContext],
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
        handlers: &HashMap<Marker, HandlerEntry>,
    ) -> bool {
        let Some(top) = dispatch_stack.last() else {
            return true;
        };
        if top.completed {
            return true;
        }

        let Some(seg_id) = current_segment else {
            return true;
        };
        let Some(seg) = segments.get(seg_id) else {
            return true;
        };
        let Some(handler_marker) = top.handler_chain.get(top.handler_idx).copied() else {
            debug_assert!(false, "handler_idx out of bounds");
            return false;
        };
        if seg.marker != handler_marker {
            return true;
        }

        let Some(entry) = handlers.get(&handler_marker) else {
            debug_assert!(false, "handler marker not in registry");
            return false;
        };
        let Some(prompt_seg) = segments.get(entry.prompt_seg_id) else {
            debug_assert!(false, "prompt segment missing");
            return false;
        };

        prompt_seg.scope_chain.contains(&interceptor_marker)
    }

    pub(crate) fn is_skipped(&self, marker: Marker) -> bool {
        self.interceptor_skip_stack.contains(&marker)
    }

    pub(crate) fn pop_skip(&mut self, marker: Marker) {
        if let Some(pos) = self
            .interceptor_skip_stack
            .iter()
            .rposition(|active| *active == marker)
        {
            self.interceptor_skip_stack.remove(pos);
        }
    }

    pub(crate) fn push_skip(&mut self, marker: Marker) {
        self.interceptor_skip_stack.push(marker);
    }

    pub(crate) fn should_apply(
        entry: &InterceptorEntry,
        yielded_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let is_match = Python::attach(|py| {
            yielded_obj
                .bind(py)
                .is_instance(entry.types.bind(py))
                .map_err(PyException::from)
        })?;
        Ok(match entry.mode {
            InterceptMode::Include => is_match,
            InterceptMode::Exclude => !is_match,
        })
    }

    pub(crate) fn classify_result_shape(result_obj: &Py<PyAny>) -> (bool, bool) {
        Python::attach(|py| {
            let bound = result_obj.bind(py);
            let doctrl_tag = bound
                .extract::<PyRef<'_, PyDoCtrlBase>>()
                .ok()
                .and_then(|base| crate::pyvm::DoExprTag::try_from(base.tag).ok());
            let is_effect_base = bound.is_instance_of::<PyEffectBase>();
            let is_doexpr =
                bound.is_instance_of::<PyDoExprBase>() || bound.is_instance_of::<DoeffGenerator>();
            let is_direct_expr = is_effect_base
                || doctrl_tag.is_some_and(|tag| tag != crate::pyvm::DoExprTag::Expand);
            (is_direct_expr, is_doexpr)
        })
    }

    pub(crate) fn interceptor_call_arg(
        interceptor_callable: &Py<PyAny>,
        yielded_obj: &Py<PyAny>,
    ) -> Result<CallArg, PyException> {
        Python::attach(|py| {
            let callable = interceptor_callable.bind(py);
            let is_do_callable = callable.is_instance_of::<DoeffGeneratorFn>()
                || callable
                    .getattr("_doeff_generator_factory")
                    .is_ok_and(|factory| factory.is_instance_of::<DoeffGeneratorFn>());

            if is_do_callable {
                let quoted = Bound::new(
                    py,
                    PyClassInitializer::from(crate::pyvm::PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: crate::pyvm::DoExprTag::Pure as u8,
                        })
                        .add_subclass(PyPure {
                            value: yielded_obj.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind();
                return Ok(CallArg::Expr(PyShared::new(quoted)));
            }

            Ok(CallArg::Value(crate::value::Value::Python(
                yielded_obj.clone_ref(py),
            )))
        })
    }

    pub(crate) fn current_active_handler_dispatch_id(
        &self,
        dispatch_stack: &[DispatchContext],
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Option<DispatchId> {
        let top = dispatch_stack.last()?;
        if top.completed {
            return None;
        }
        let marker = *top.handler_chain.get(top.handler_idx)?;
        let seg_id = current_segment?;
        let seg = segments.get(seg_id)?;
        if seg.marker == marker {
            Some(top.dispatch_id)
        } else {
            None
        }
    }

    pub(crate) fn insert(
        &mut self,
        marker: Marker,
        interceptor: PyShared,
        types: PyShared,
        mode: InterceptMode,
        metadata: CallMetadata,
    ) {
        self.interceptors.insert(
            marker,
            InterceptorEntry {
                interceptor,
                types,
                mode,
                metadata,
            },
        );
    }

    pub(crate) fn get_entry(&self, marker: Marker) -> Option<InterceptorEntry> {
        self.interceptors.get(&marker).cloned()
    }

    pub(crate) fn register_callback(&mut self, cb: CallbackId, marker: Marker) {
        self.interceptor_callbacks.insert(cb, marker);
    }

    pub(crate) fn unregister_callback(&mut self, cb: CallbackId) -> Option<Marker> {
        self.interceptor_callbacks.remove(&cb)
    }

    pub(crate) fn set_call_metadata(&mut self, cb: CallbackId, metadata: CallMetadata) {
        self.interceptor_call_metadata.insert(cb, metadata);
    }

    pub(crate) fn take_call_metadata(&mut self, cb: CallbackId) -> Option<CallMetadata> {
        self.interceptor_call_metadata.remove(&cb)
    }

    pub(crate) fn increment_eval_depth(&mut self) {
        self.interceptor_eval_depth = self.interceptor_eval_depth.saturating_add(1);
    }

    pub(crate) fn decrement_eval_depth(&mut self) {
        self.interceptor_eval_depth = self.interceptor_eval_depth.saturating_sub(1);
    }

    pub(crate) fn is_eval_idle(&self) -> bool {
        self.interceptor_eval_depth == 0
    }

    pub(crate) fn register_eval_callback(&mut self, cb: CallbackId) {
        self.interceptor_eval_callbacks.insert(cb);
        self.increment_eval_depth();
    }

    pub(crate) fn unregister_eval_callback(&mut self, cb: CallbackId) -> bool {
        let removed = self.interceptor_eval_callbacks.remove(&cb);
        if removed {
            self.decrement_eval_depth();
        }
        removed
    }

    pub(crate) fn prepare_with_intercept(
        &mut self,
        interceptor: PyShared,
        types: PyShared,
        mode: InterceptMode,
        metadata: CallMetadata,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
    ) -> Result<Segment, VMError> {
        let interceptor_marker = Marker::fresh();
        let Some(outside_seg_id) = current_segment else {
            return Err(VMError::internal("no current segment for WithIntercept"));
        };
        let outside_scope = segments
            .get(outside_seg_id)
            .map(|s| s.scope_chain.clone())
            .unwrap_or_default();

        self.insert(interceptor_marker, interceptor, types, mode, metadata);

        let mut body_scope = vec![interceptor_marker];
        body_scope.extend(outside_scope);
        Ok(Segment::new(
            interceptor_marker,
            Some(outside_seg_id),
            body_scope,
        ))
    }
}

use super::*;
use std::sync::Arc;

impl VM {
    pub(super) fn first_handler_hint_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> Option<crate::continuation::DispatchHandlerHint> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let SegmentKind::PromptBoundary { handled_marker, .. } = &seg.kind {
                return Some(crate::continuation::DispatchHandlerHint {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                });
            }
            cursor = seg.parent;
        }
        None
    }

    pub(super) fn visible_scope_store(
        &self,
        start_seg_id: SegmentId,
    ) -> crate::segment::ScopeStore {
        let mut layers = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            if let Some(bindings) = self.scope_bindings(seg_id) {
                if !bindings.is_empty() {
                    layers.push(Arc::new(bindings.clone()));
                }
            }
            cursor = self.scope_parent(seg_id);
        }
        if !self.env_store.is_empty() {
            layers.push(Arc::new(self.env_store.clone()));
        }
        layers.reverse();
        crate::segment::ScopeStore {
            scope_bindings: layers,
        }
    }

    pub(super) fn track_run_handler(&mut self, handler: &KleisliRef) {
        if !self
            .run_handlers
            .iter()
            .any(|existing| Arc::ptr_eq(existing, handler))
        {
            self.run_handlers.push(handler.clone());
        }
    }

    pub(super) fn find_prompt_boundary_by_marker(
        &self,
        marker: Marker,
    ) -> Option<(SegmentId, KleisliRef, Option<Vec<PyShared>>)> {
        self.segments
            .iter()
            .find_map(|(seg_id, seg)| match &seg.kind {
                SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } if *handled_marker == marker => Some((seg_id, handler.clone(), types.clone())),
                SegmentKind::PromptBoundary { .. }
                | SegmentKind::Normal
                | SegmentKind::InterceptorBoundary { .. }
                | SegmentKind::MaskBoundary { .. } => None,
            })
    }

    pub(super) fn handlers_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> Vec<HandlerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                types,
                ..
            } = &seg.kind
            {
                chain.push(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                    handler: handler.clone(),
                    types: types.clone(),
                });
            }
            cursor = seg.parent;
        }
        chain
    }

    pub(super) fn chain_entries_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> Vec<CallerChainEntry> {
        let mut chain = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            match &seg.kind {
                SegmentKind::PromptBoundary {
                    handled_marker,
                    handler,
                    types,
                    ..
                } => chain.push(CallerChainEntry::Handler(HandlerChainEntry {
                    marker: *handled_marker,
                    prompt_seg_id: seg_id,
                    handler: handler.clone(),
                    types: types.clone(),
                })),
                SegmentKind::InterceptorBoundary { .. } => {
                    let link = InterceptorChainLink::from_boundary(seg.marker, &seg.kind)
                        .expect("InterceptorBoundary should always produce a chain link");
                    chain.push(CallerChainEntry::Interceptor(link));
                }
                SegmentKind::Normal | SegmentKind::MaskBoundary { .. } => {}
            }
            cursor = seg.parent;
        }
        chain
    }

    pub(super) fn find_prompt_boundary_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<SegmentId> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let SegmentKind::PromptBoundary { handled_marker, .. } = &seg.kind {
                if *handled_marker == marker {
                    return Some(seg_id);
                }
            }
            cursor = seg.parent;
        }
        None
    }

    pub(super) fn same_effect_python_type(a: &DispatchEffect, b: &DispatchEffect) -> bool {
        let Some(a_obj) = dispatch_ref_as_python(a) else {
            return false;
        };
        let Some(b_obj) = dispatch_ref_as_python(b) else {
            return false;
        };
        Python::attach(|py| {
            let a_ty = a_obj.bind(py).get_type();
            let b_ty = b_obj.bind(py).get_type();
            a_ty.as_ptr() == b_ty.as_ptr()
        })
    }

    pub(super) fn current_handler_chain(&self) -> Vec<HandlerChainEntry> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.handlers_in_caller_chain(seg_id)
    }

    pub(super) fn prepare_with_handler(
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

    pub(super) fn should_invoke_handler(
        &self,
        entry: &HandlerChainEntry,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        self.should_invoke_handler_types(entry.types.as_ref(), effect_obj)
    }

    pub(super) fn should_invoke_handler_types(
        &self,
        types: Option<&Vec<PyShared>>,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = types else {
            return Ok(true);
        };
        if types.is_empty() {
            return Ok(false);
        }

        Ok(Python::attach(|py| -> PyResult<bool> {
            let effect = effect_obj.bind(py);
            let type_tuple = PyTuple::new(py, types.iter().map(|ty| ty.clone_ref(py)))?;
            effect.is_instance(&type_tuple)
        })?)
    }

    pub(super) fn handler_index_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<usize> {
        let mut cursor = Some(start_seg_id);
        let mut handler_index = 0usize;
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let SegmentKind::PromptBoundary { handled_marker, .. } = &seg.kind {
                if *handled_marker == marker {
                    return Some(handler_index);
                }
                handler_index += 1;
            }
            cursor = seg.parent;
        }
        None
    }

    pub(super) fn handler_trace_info_for_marker_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
        marker: Marker,
    ) -> Option<(String, HandlerKind, Option<String>, Option<u32>)> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let SegmentKind::PromptBoundary {
                handled_marker,
                handler,
                ..
            } = &seg.kind
            {
                if *handled_marker == marker {
                    return Some(Self::handler_trace_info(handler));
                }
            }
            cursor = seg.parent;
        }
        None
    }
}

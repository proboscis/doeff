use super::*;

impl VM {
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
            cursor = seg.caller;
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
                SegmentKind::InterceptorBoundary {
                    interceptor,
                    types,
                    mode,
                    metadata,
                } => chain.push(CallerChainEntry::Interceptor(InterceptorChainEntry {
                    marker: seg.marker,
                    interceptor: interceptor.clone(),
                    types: types.clone(),
                    mode: *mode,
                    metadata: metadata.clone(),
                })),
                SegmentKind::Normal | SegmentKind::MaskBoundary { .. } => {
                    assert!(
                        self.interceptor_state.get_entry(seg.marker).is_none(),
                        "normal segment marker {} unexpectedly has interceptor state entry",
                        seg.marker.raw()
                    );
                }
            }
            cursor = seg.caller;
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
            cursor = seg.caller;
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
        let Some(types) = entry.types.as_ref() else {
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
}

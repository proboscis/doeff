use super::*;

#[derive(Clone)]
pub(super) struct InstalledHandler {
    pub(super) marker: Marker,
    pub(super) handler: KleisliRef,
}

#[derive(Clone)]
pub(super) struct HandlerChainEntry {
    pub(super) marker: Marker,
    pub(super) prompt_seg_id: SegmentId,
    pub(super) handler: KleisliRef,
    pub(super) types: Option<Vec<PyShared>>,
}

#[derive(Clone)]
pub(super) struct WithHandlerPlan {
    pub(super) handler_marker: Marker,
    pub(super) outside_seg_id: SegmentId,
    pub(super) handler: KleisliRef,
}

#[derive(Clone)]
pub(super) struct SelectedHandler {
    pub(super) index: usize,
    pub(super) entry: HandlerChainEntry,
    pub(super) bootstrap_with_pass: bool,
}

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

    pub(super) fn register_handler_continuation_if_needed(
        &mut self,
        handler: &KleisliRef,
        continuation: &Continuation,
    ) {
        if handler.py_identity().is_some() {
            self.register_continuation(continuation.clone());
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

    pub(super) fn current_handler_chain(&self) -> Vec<HandlerChainEntry> {
        let Some(seg_id) = self.current_segment else {
            return Vec::new();
        };
        self.handlers_in_caller_chain(seg_id)
    }

    pub(super) fn prepare_with_handler(
        &self,
        handler: KleisliRef,
    ) -> Result<WithHandlerPlan, VMError> {
        let handler_marker = Marker::fresh();
        let Some(outside_seg_id) = self.current_segment else {
            return Err(VMError::internal("no current segment for WithHandler"));
        };

        Ok(WithHandlerPlan {
            handler_marker,
            outside_seg_id,
            handler,
        })
    }

    pub(super) fn install_handler_scope(
        &mut self,
        marker: Marker,
        outside_seg_id: Option<SegmentId>,
        handler: KleisliRef,
        types: Option<Vec<PyShared>>,
    ) -> (SegmentId, SegmentId) {
        let mut prompt_seg =
            Segment::new_prompt_with_types(marker, outside_seg_id, marker, handler.clone(), types);
        self.copy_interceptor_guard_state(outside_seg_id, &mut prompt_seg);
        self.copy_scope_store_from(outside_seg_id, &mut prompt_seg);
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        self.track_run_handler(&handler);

        let mut body_seg = Segment::new(marker, Some(prompt_seg_id));
        self.copy_interceptor_guard_state(outside_seg_id, &mut body_seg);
        self.copy_scope_store_from(outside_seg_id, &mut body_seg);
        let body_seg_id = self.alloc_segment(body_seg);

        (prompt_seg_id, body_seg_id)
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

    pub(super) fn select_handler(
        &self,
        handler_chain: &[HandlerChainEntry],
        effect: &DispatchEffect,
        bootstrap_with_pass: bool,
    ) -> Result<Option<SelectedHandler>, VMError> {
        let effect_obj =
            Python::attach(|py| dispatch_to_pyobject(py, effect).map(|obj| obj.unbind())).map_err(
                |err| {
                    VMError::python_error(format!(
                        "failed to convert dispatch effect to Python object: {err}"
                    ))
                },
            )?;

        let mut selected: Option<(usize, HandlerChainEntry)> = None;
        let mut first_type_filtered_skip: Option<(usize, HandlerChainEntry)> = None;
        for (idx, entry) in handler_chain.iter().enumerate() {
            let can_handle = entry.handler.can_handle(effect)?;
            if !can_handle {
                continue;
            }

            let should_invoke = self
                .should_invoke_handler(entry, &effect_obj)
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to evaluate WithHandler type filter: {err:?}"
                    ))
                })?;
            if should_invoke {
                selected = Some((idx, entry.clone()));
                break;
            }

            if first_type_filtered_skip.is_none() {
                first_type_filtered_skip = Some((idx, entry.clone()));
            }
        }

        let Some(found) = selected else {
            return Ok(None);
        };

        let selected = if bootstrap_with_pass {
            if let Some(skipped) = &first_type_filtered_skip {
                if skipped.0 < found.0 {
                    SelectedHandler {
                        index: skipped.0,
                        entry: skipped.1.clone(),
                        bootstrap_with_pass: true,
                    }
                } else {
                    SelectedHandler {
                        index: found.0,
                        entry: found.1,
                        bootstrap_with_pass: false,
                    }
                }
            } else {
                SelectedHandler {
                    index: found.0,
                    entry: found.1,
                    bootstrap_with_pass: false,
                }
            }
        } else {
            SelectedHandler {
                index: found.0,
                entry: found.1,
                bootstrap_with_pass: false,
            }
        };

        Ok(Some(selected))
    }

    pub fn instantiate_installed_handlers(&mut self) -> Option<SegmentId> {
        let installed = self.installed_handlers.clone();
        let mut outside_seg_id: Option<SegmentId> = None;
        for entry in installed.into_iter().rev() {
            let (_, body_seg_id) =
                self.install_handler_scope(entry.marker, outside_seg_id, entry.handler, None);
            outside_seg_id = Some(body_seg_id);
        }
        outside_seg_id
    }

    pub fn register_continuation(&mut self, k: Continuation) {
        self.continuation_registry.insert(k.cont_id, k);
    }

    pub fn lookup_continuation(&self, cont_id: ContId) -> Option<&Continuation> {
        self.continuation_registry.get(&cont_id)
    }

    pub fn capture_continuation(&self, dispatch_id: Option<DispatchId>) -> Option<Continuation> {
        let seg_id = self.current_segment?;
        let segment = self.segments.get(seg_id)?;
        Some(Continuation::capture(segment, seg_id, dispatch_id))
    }

    pub fn install_handler(
        &mut self,
        marker: Marker,
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) {
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        self.installed_handlers
            .push(InstalledHandler { marker, handler });
    }

    pub fn remove_handler(&mut self, marker: Marker) -> bool {
        let before = self.installed_handlers.len();
        self.installed_handlers
            .retain(|entry| entry.marker != marker);
        before != self.installed_handlers.len()
    }

    pub fn installed_handler_markers(&self) -> Vec<Marker> {
        self.installed_handlers
            .iter()
            .map(|entry| entry.marker)
            .collect()
    }

    pub fn install_handler_on_segment(
        &mut self,
        marker: Marker,
        prompt_seg_id: SegmentId,
        handler: KleisliRef,
        _py_identity: Option<PyShared>,
    ) -> bool {
        let Some(seg) = self.segments.get_mut(prompt_seg_id) else {
            let prompt_seg = Segment::new_prompt(marker, None, marker, handler.clone());
            self.alloc_segment(prompt_seg);
            self.track_run_handler(&handler);
            return true;
        };
        seg.kind = SegmentKind::PromptBoundary {
            handled_marker: marker,
            handler: handler.clone(),
            types: None,
        };
        self.track_run_handler(&handler);
        true
    }
}

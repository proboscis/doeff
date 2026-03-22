use std::collections::HashSet;

use super::*;

impl VM {
    pub(super) fn first_handler_hint_in_caller_chain(
        &self,
        start_seg_id: SegmentId,
    ) -> Option<crate::continuation::DispatchHandlerHint> {
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let seg = self.segments.get(seg_id)?;
            if let Some(boundary) = seg.kind.prompt_boundary() {
                return Some(crate::continuation::DispatchHandlerHint {
                    marker: boundary.handled_marker,
                    prompt_seg_id: seg_id,
                });
            }
            cursor = seg.parent;
        }
        None
    }

    pub(super) fn handler_marker_in_caller_chain(&self, start_seg_id: SegmentId) -> Option<Marker> {
        self.first_handler_hint_in_caller_chain(start_seg_id)
            .map(|hint| hint.marker)
    }

    pub(super) fn visible_scope_store(
        &self,
        start_seg_id: SegmentId,
    ) -> crate::segment::ScopeStore {
        let mut layers = Vec::new();
        let mut seen_segments = HashSet::new();
        for seg_id in self.visible_lexical_segments(start_seg_id) {
            if !seen_segments.insert(seg_id) {
                continue;
            }
            if let Some(bindings) = self.segment_scope_bindings(seg_id) {
                if !bindings.is_empty() {
                    layers.push(Arc::new(bindings.clone()));
                }
            }
        }
        if !self.var_store.root_scope_bindings().is_empty() {
            layers.push(Arc::new(self.var_store.root_scope_bindings().clone()));
        }
        layers.reverse();
        crate::segment::ScopeStore {
            scope_bindings: layers,
        }
    }

    pub(super) fn find_prompt_boundary_by_marker(
        &self,
        marker: Marker,
    ) -> Option<(SegmentId, KleisliRef, Option<Arc<Vec<PyShared>>>)> {
        self.segments
            .iter()
            .find_map(|(seg_id, seg)| {
                let boundary = seg.kind.prompt_boundary()?;
                (boundary.handled_marker == marker).then(|| {
                    (seg_id, boundary.handler.clone(), boundary.types.clone())
                })
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
            if let Some(boundary) = seg.kind.prompt_boundary() {
                chain.push(HandlerChainEntry {
                    marker: boundary.handled_marker,
                    prompt_seg_id: seg_id,
                    handler: boundary.handler.clone(),
                    types: boundary.types.clone(),
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
            if let Some(boundary) = seg.kind.prompt_boundary() {
                chain.push(CallerChainEntry::Handler(HandlerChainEntry {
                    marker: boundary.handled_marker,
                    prompt_seg_id: seg_id,
                    handler: boundary.handler.clone(),
                    types: boundary.types.clone(),
                }));
            } else if let Some(link) = InterceptorChainLink::from_boundary(&seg.kind) {
                chain.push(CallerChainEntry::Interceptor(link));
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
            if seg
                .kind
                .prompt_boundary()
                .is_some_and(|boundary| boundary.handled_marker == marker)
            {
                return Some(seg_id);
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

    pub(super) fn effect_type_cache_key(effect_obj: &Py<PyAny>) -> PyResult<usize> {
        Python::attach(|py| Ok(effect_obj.bind(py).get_type().as_ptr() as usize))
    }

    pub(super) fn collect_dispatch_handler_entries(
        &self,
        start_seg_id: SegmentId,
        exclude_prompt: Option<SegmentId>,
        restricted_excluded_prompts: &HashSet<SegmentId>,
    ) -> (Vec<HandlerChainEntry>, Vec<HandlerChainEntry>) {
        let mut full_entries = Vec::new();
        let mut current_entries = Vec::new();
        let mut cursor = Some(start_seg_id);
        while let Some(seg_id) = cursor {
            let Some(seg) = self.segments.get(seg_id) else {
                break;
            };
            cursor = seg.parent;
            let Some(boundary) = seg.kind.prompt_boundary() else {
                continue;
            };

            let entry = HandlerChainEntry {
                marker: boundary.handled_marker,
                prompt_seg_id: seg_id,
                handler: boundary.handler.clone(),
                types: boundary.types.clone(),
            };
            full_entries.push(entry.clone());

            let is_shared_spawn_writer = boundary.handler.handler_name() == "WriterHandler"
                && self.shared_builtin_handler_prompt(seg_id) != seg_id;
            let restricted_excluded = restricted_excluded_prompts.contains(&seg_id);
            if Some(seg_id) != exclude_prompt && !restricted_excluded && !is_shared_spawn_writer {
                current_entries.push(entry);
            }
        }
        (full_entries, current_entries)
    }

    pub(super) fn first_matching_handler_in_entries(
        &mut self,
        entries: &[HandlerChainEntry],
        effect: &DispatchEffect,
        effect_obj: &Py<PyAny>,
    ) -> Result<Option<(usize, Marker, SegmentId, KleisliRef)>, VMError> {
        for (index, entry) in entries.iter().enumerate() {
            if !entry.handler.can_handle(effect)? {
                continue;
            }
            let should_invoke = self
                .should_invoke_handler(entry, effect_obj)
                .map_err(|err| {
                    VMError::python_error(format!(
                        "failed to evaluate WithHandler type filter: {err:?}"
                    ))
                })?;
            if should_invoke {
                return Ok(Some((
                    index,
                    entry.marker,
                    entry.prompt_seg_id,
                    entry.handler.clone(),
                )));
            }
        }
        Ok(None)
    }

    pub(super) fn cached_current_chain_handler_resolution(
        &mut self,
        seg_id: SegmentId,
        effect_type_id: usize,
        effect: &DispatchEffect,
        effect_obj: &Py<PyAny>,
        current_entries: &[HandlerChainEntry],
    ) -> Result<Option<(usize, Marker, SegmentId, KleisliRef)>, VMError> {
        let cache_key = (seg_id, effect_type_id);
        let Some(cached) = self
            .segment_handler_resolution_cache
            .get(&cache_key)
            .copied()
        else {
            return Ok(None);
        };

        if cached.segment_epoch != self.segment_topology_epoch(seg_id) {
            self.segment_handler_resolution_cache.remove(&cache_key);
            return Ok(None);
        }

        let Some((index, entry)) = current_entries
            .iter()
            .enumerate()
            .find(|(_, entry)| entry.prompt_seg_id == cached.prompt_seg_id)
        else {
            self.segment_handler_resolution_cache.remove(&cache_key);
            return Ok(None);
        };

        if !entry.handler.can_handle(effect)? {
            self.segment_handler_resolution_cache.remove(&cache_key);
            return Ok(None);
        }

        let should_invoke = self
            .should_invoke_handler(entry, effect_obj)
            .map_err(|err| {
                VMError::python_error(format!(
                    "failed to evaluate WithHandler type filter: {err:?}"
                ))
            })?;
        if !should_invoke {
            self.segment_handler_resolution_cache.remove(&cache_key);
            return Ok(None);
        }

        Ok(Some((
            index,
            entry.marker,
            entry.prompt_seg_id,
            entry.handler.clone(),
        )))
    }

    pub(super) fn cache_current_chain_handler_resolution(
        &mut self,
        seg_id: SegmentId,
        effect_type_id: usize,
        prompt_seg_id: SegmentId,
    ) {
        self.segment_handler_resolution_cache.insert(
            (seg_id, effect_type_id),
            CachedHandlerResolution {
                prompt_seg_id,
                segment_epoch: self.segment_topology_epoch(seg_id),
            },
        );
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
        &mut self,
        entry: &HandlerChainEntry,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        self.should_invoke_handler_types(
            Self::handler_type_cache_key(&entry.handler),
            entry.types.as_ref(),
            effect_obj,
        )
    }

    pub(super) fn should_invoke_handler_types(
        &mut self,
        handler_cache_key: usize,
        types: Option<&Arc<Vec<PyShared>>>,
        effect_obj: &Py<PyAny>,
    ) -> Result<bool, PyException> {
        let Some(types) = types else {
            return Ok(true);
        };
        if types.is_empty() {
            return Ok(false);
        }

        let (effect_type_ptr, cached_match) = Python::attach(|py| -> PyResult<(usize, bool)> {
            let effect = effect_obj.bind(py);
            let effect_type = effect.get_type();
            let effect_type_ptr = effect_type.as_ptr() as usize;
            if let Some(cached_match) = self
                .handler_type_match_cache
                .get(&(handler_cache_key, effect_type_ptr))
                .copied()
            {
                return Ok((effect_type_ptr, cached_match));
            }

            let matches_type_ptr =
                |candidate_ptr| types.iter().any(|ty| candidate_ptr == ty.bind(py).as_ptr());

            if matches_type_ptr(effect_type.as_ptr()) {
                return Ok((effect_type_ptr, true));
            }

            for mro_type in effect_type.mro().iter().skip(1) {
                let mro_type = mro_type.cast::<pyo3::types::PyType>()?;
                if matches_type_ptr(mro_type.as_ptr()) {
                    return Ok((effect_type_ptr, true));
                }
            }

            Ok((effect_type_ptr, false))
        })?;

        self.handler_type_match_cache
            .insert((handler_cache_key, effect_type_ptr), cached_match);
        Ok(cached_match)
    }

    pub(super) fn handler_type_cache_key(handler: &KleisliRef) -> usize {
        Arc::as_ptr(handler) as *const () as usize
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
            if let Some(boundary) = seg.kind.prompt_boundary() {
                if boundary.handled_marker == marker {
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
            if let Some(boundary) = seg.kind.prompt_boundary() {
                if boundary.handled_marker == marker {
                    return Some(Self::handler_trace_info(&boundary.handler));
                }
            }
            cursor = seg.parent;
        }
        None
    }
}

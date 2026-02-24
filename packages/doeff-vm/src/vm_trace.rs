//! VM trace assembly and active-chain reconstruction.

use super::*;

impl VM {
    pub(super) fn maybe_emit_frame_entered(&mut self, metadata: &CallMetadata) {
        self.capture_log.push(CaptureEvent::FrameEntered {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: metadata.source_line,
            args_repr: metadata.args_repr.clone(),
            program_call_repr: Self::program_call_repr(metadata),
        });
    }

    pub(super) fn maybe_emit_frame_exited(&mut self, metadata: &CallMetadata) {
        self.capture_log.push(CaptureEvent::FrameExited {
            function_name: metadata.function_name.clone(),
        });
    }

    pub(super) fn maybe_emit_handler_threw_for_dispatch(
        &mut self,
        dispatch_id: DispatchId,
        exc: &PyException,
    ) {
        let Some((handler_index, handler_name)) =
            self.current_handler_identity_for_dispatch(dispatch_id)
        else {
            return;
        };
        self.capture_log.push(CaptureEvent::HandlerCompleted {
            dispatch_id,
            handler_name,
            handler_index,
            action: HandlerAction::Threw {
                exception_repr: Self::exception_repr(exc),
            },
        });
    }

    pub(super) fn maybe_emit_resume_event(
        &mut self,
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        continuation: &Continuation,
        transferred: bool,
    ) {
        if let Some((resumed_function_name, source_file, source_line)) =
            Self::continuation_resume_location(continuation)
        {
            if transferred {
                self.capture_log.push(CaptureEvent::Transferred {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                });
            } else {
                self.capture_log.push(CaptureEvent::Resumed {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                });
            }
        }
    }

    pub fn assemble_trace(&self) -> Vec<TraceEntry> {
        let mut trace: Vec<TraceEntry> = Vec::new();
        let mut dispatch_positions: HashMap<DispatchId, usize> = HashMap::new();

        for event in &self.capture_log {
            match event {
                CaptureEvent::FrameEntered {
                    frame_id,
                    function_name,
                    source_file,
                    source_line,
                    args_repr,
                    program_call_repr: _,
                } => {
                    trace.push(TraceEntry::Frame {
                        frame_id: *frame_id,
                        function_name: function_name.clone(),
                        source_file: source_file.clone(),
                        source_line: *source_line,
                        args_repr: args_repr.clone(),
                    });
                }
                CaptureEvent::FrameExited { .. } => {}
                CaptureEvent::DispatchStarted {
                    dispatch_id,
                    effect_repr,
                    is_execution_context_effect: _,
                    creation_site: _,
                    handler_name,
                    handler_kind,
                    handler_source_file,
                    handler_source_line,
                    handler_chain_snapshot: _,
                    effect_frame_id: _,
                    effect_function_name: _,
                    effect_source_file: _,
                    effect_source_line: _,
                } => {
                    let pos = trace.len();
                    dispatch_positions.insert(*dispatch_id, pos);
                    trace.push(TraceEntry::Dispatch {
                        dispatch_id: *dispatch_id,
                        effect_repr: effect_repr.clone(),
                        handler_name: handler_name.clone(),
                        handler_kind: handler_kind.clone(),
                        handler_source_file: handler_source_file.clone(),
                        handler_source_line: *handler_source_line,
                        delegation_chain: vec![DelegationEntry {
                            handler_name: handler_name.clone(),
                            handler_kind: handler_kind.clone(),
                            handler_source_file: handler_source_file.clone(),
                            handler_source_line: *handler_source_line,
                        }],
                        action: DispatchAction::Active,
                        value_repr: None,
                        exception_repr: None,
                    });
                }
                CaptureEvent::Delegated {
                    dispatch_id,
                    from_handler_name: _,
                    from_handler_index: _,
                    to_handler_name,
                    to_handler_index: _,
                    to_handler_kind,
                    to_handler_source_file,
                    to_handler_source_line,
                }
                | CaptureEvent::Passed {
                    dispatch_id,
                    from_handler_name: _,
                    from_handler_index: _,
                    to_handler_name,
                    to_handler_index: _,
                    to_handler_kind,
                    to_handler_source_file,
                    to_handler_source_line,
                } => {
                    if let Some(&pos) = dispatch_positions.get(dispatch_id) {
                        if let TraceEntry::Dispatch {
                            handler_name,
                            handler_kind,
                            handler_source_file,
                            handler_source_line,
                            delegation_chain,
                            ..
                        } = &mut trace[pos]
                        {
                            *handler_name = to_handler_name.clone();
                            *handler_kind = to_handler_kind.clone();
                            *handler_source_file = to_handler_source_file.clone();
                            *handler_source_line = *to_handler_source_line;
                            delegation_chain.push(DelegationEntry {
                                handler_name: to_handler_name.clone(),
                                handler_kind: to_handler_kind.clone(),
                                handler_source_file: to_handler_source_file.clone(),
                                handler_source_line: *to_handler_source_line,
                            });
                        }
                    }
                }
                CaptureEvent::HandlerCompleted {
                    dispatch_id,
                    handler_name: _,
                    handler_index: _,
                    action,
                } => {
                    if let Some(&pos) = dispatch_positions.get(dispatch_id) {
                        if let TraceEntry::Dispatch {
                            action: dispatch_action,
                            value_repr,
                            exception_repr,
                            ..
                        } = &mut trace[pos]
                        {
                            match action {
                                HandlerAction::Resumed { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Resumed;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Transferred { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Transferred;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Returned { value_repr: repr } => {
                                    *dispatch_action = DispatchAction::Returned;
                                    *value_repr = repr.clone();
                                }
                                HandlerAction::Threw {
                                    exception_repr: repr,
                                } => {
                                    *dispatch_action = DispatchAction::Threw;
                                    *exception_repr = repr.clone();
                                }
                            }
                        }
                    }
                }
                CaptureEvent::Resumed {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                }
                | CaptureEvent::Transferred {
                    dispatch_id,
                    handler_name,
                    value_repr,
                    resumed_function_name,
                    source_file,
                    source_line,
                } => {
                    trace.push(TraceEntry::ResumePoint {
                        dispatch_id: *dispatch_id,
                        handler_name: handler_name.clone(),
                        resumed_function_name: resumed_function_name.clone(),
                        source_file: source_file.clone(),
                        source_line: *source_line,
                        value_repr: value_repr.clone(),
                    });
                }
            }
        }

        self.supplement_with_live_state(&mut trace);
        trace
    }

    pub(super) fn supplement_with_live_state(&self, trace: &mut Vec<TraceEntry>) {
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            let Some(seg) = self.segments.get(id) else {
                break;
            };
            for frame in &seg.frames {
                let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                } = frame
                else {
                    continue;
                };

                let current_line = Self::stream_debug_location(stream)
                    .map(|location| location.source_line)
                    .unwrap_or(metadata.source_line);
                let last_line = trace.iter().rev().find_map(|entry| match entry {
                    TraceEntry::Frame {
                        frame_id,
                        source_line,
                        ..
                    } if *frame_id == metadata.frame_id => Some(*source_line),
                    _ => None,
                });
                if last_line != Some(current_line) {
                    trace.push(TraceEntry::Frame {
                        frame_id: metadata.frame_id,
                        function_name: metadata.function_name.clone(),
                        source_file: metadata.source_file.clone(),
                        source_line: current_line,
                        args_repr: metadata.args_repr.clone(),
                    });
                }
            }
            seg_id = seg.caller;
        }

        for ctx in &self.dispatch_stack {
            if ctx.completed {
                continue;
            }
            let already_in_trace = trace.iter().any(|entry| {
                matches!(
                    entry,
                    TraceEntry::Dispatch { dispatch_id, .. } if *dispatch_id == ctx.dispatch_id
                )
            });
            if already_in_trace {
                continue;
            }

            let Some((handler_name, handler_kind, handler_source_file, handler_source_line)) = ctx
                .handler_chain
                .get(ctx.handler_idx)
                .and_then(|marker| self.marker_handler_trace_info(*marker))
            else {
                continue;
            };

            trace.push(TraceEntry::Dispatch {
                dispatch_id: ctx.dispatch_id,
                effect_repr: Self::effect_repr(&ctx.effect),
                handler_name: handler_name.clone(),
                handler_kind: handler_kind.clone(),
                handler_source_file: handler_source_file.clone(),
                handler_source_line,
                delegation_chain: vec![DelegationEntry {
                    handler_name,
                    handler_kind,
                    handler_source_file,
                    handler_source_line,
                }],
                action: DispatchAction::Active,
                value_repr: None,
                exception_repr: None,
            });
        }
    }

    pub(super) fn exception_site(exception: &PyException) -> ActiveChainEntry {
        match exception {
            PyException::Materialized {
                exc_type: _exc_type,
                exc_value,
                exc_tb,
            } => Python::attach(|py| {
                let exc_value_bound = exc_value.bind(py);

                let exception_type = exc_value_bound
                    .get_type()
                    .name()
                    .ok()
                    .map(|name| name.to_string())
                    .unwrap_or_else(|| MISSING_EXCEPTION_TYPE.to_string());

                let message = exc_value_bound
                    .str()
                    .map(|v| v.to_string())
                    .unwrap_or_default();

                let mut function_name = MISSING_UNKNOWN.to_string();
                let mut source_file = MISSING_UNKNOWN.to_string();
                let mut source_line = 0u32;

                let mut tb = exc_tb
                    .as_ref()
                    .map(|tb| tb.bind(py).clone().into_any())
                    .or_else(|| exc_value_bound.getattr("__traceback__").ok());

                while let Some(tb_obj) = tb {
                    let next = tb_obj.getattr("tb_next").ok();
                    let has_next = next.as_ref().is_some_and(|n| !n.is_none());
                    if has_next {
                        tb = next;
                        continue;
                    }

                    source_line = tb_obj
                        .getattr("tb_lineno")
                        .ok()
                        .and_then(|v| v.extract::<u32>().ok())
                        .unwrap_or(0);

                    if let Ok(frame) = tb_obj.getattr("tb_frame") {
                        if let Ok(code) = frame.getattr("f_code") {
                            function_name = code
                                .getattr("co_name")
                                .ok()
                                .and_then(|v| v.extract::<String>().ok())
                                .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
                            source_file = code
                                .getattr("co_filename")
                                .ok()
                                .and_then(|v| v.extract::<String>().ok())
                                .unwrap_or_else(|| MISSING_UNKNOWN.to_string());
                        }
                    }
                    break;
                }

                ActiveChainEntry::ExceptionSite {
                    function_name,
                    source_file,
                    source_line,
                    exception_type,
                    message,
                }
            }),
            PyException::RuntimeError { message } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "RuntimeError".to_string(),
                message: message.clone(),
            },
            PyException::TypeError { message } => ActiveChainEntry::ExceptionSite {
                function_name: "<runtime>".to_string(),
                source_file: "<runtime>".to_string(),
                source_line: 0,
                exception_type: "TypeError".to_string(),
                message: message.clone(),
            },
        }
    }

    pub(super) fn context_entries_from_exception(exception: &PyException) -> Vec<Py<PyAny>> {
        let PyException::Materialized { exc_value, .. } = exception else {
            return Vec::new();
        };

        Python::attach(|py| {
            let exc = exc_value.bind(py);
            let context = exc
                .getattr(EXECUTION_CONTEXT_ATTR)
                .ok()
                .filter(|ctx| !ctx.is_none());
            let Some(context) = context else {
                return Vec::new();
            };
            let entries = context
                .getattr("entries")
                .ok()
                .filter(|entries| !entries.is_none());
            let Some(entries) = entries else {
                return Vec::new();
            };
            match entries.try_iter() {
                Ok(iter) => iter
                    .filter_map(Result::ok)
                    .map(|entry| entry.unbind())
                    .collect(),
                Err(_) => Vec::new(),
            }
        })
    }

    pub(super) fn context_entries_from_context_obj(context: &Bound<'_, PyAny>) -> Vec<Py<PyAny>> {
        if !context.is_instance_of::<PyExecutionContext>() {
            return Vec::new();
        }
        let entries = context
            .getattr("entries")
            .ok()
            .filter(|entries| !entries.is_none());
        let Some(entries) = entries else {
            return Vec::new();
        };
        match entries.try_iter() {
            Ok(iter) => iter
                .filter_map(Result::ok)
                .map(|entry| entry.unbind())
                .collect(),
            Err(_) => Vec::new(),
        }
    }

    pub(super) fn build_execution_context_from_entries(
        py: Python<'_>,
        entries: &[Py<PyAny>],
    ) -> PyResult<Py<PyAny>> {
        let context = make_execution_context_object(py)?;
        let add = context.bind(py).getattr("add")?;
        for entry in entries {
            add.call1((entry.clone_ref(py),))?;
        }
        Ok(context)
    }

    pub(super) fn attach_execution_context(exception: &PyException, context: &Py<PyAny>) {
        let PyException::Materialized { exc_value, .. } = exception else {
            return;
        };
        Python::attach(|py| {
            let _ = exc_value
                .bind(py)
                .setattr(EXECUTION_CONTEXT_ATTR, context.clone_ref(py));
        });
    }

    pub(super) fn enrich_original_exception_with_context(
        original: PyException,
        context_value: Value,
    ) -> Result<PyException, PyException> {
        let Value::Python(new_context) = context_value else {
            let err = PyException::type_error(
                "GetExecutionContext handlers must Resume with ExecutionContext".to_string(),
            );
            Self::set_exception_cause(&err, &original);
            return Err(err);
        };

        Python::attach(|py| {
            let context_bound = new_context.bind(py);
            if !context_bound.is_instance_of::<PyExecutionContext>() {
                let err = PyException::type_error(
                    "GetExecutionContext handlers must Resume with ExecutionContext".to_string(),
                );
                Self::set_exception_cause(&err, &original);
                return Err(err);
            }

            let mut merged_entries = Self::context_entries_from_context_obj(context_bound);
            let existing_entries = Self::context_entries_from_exception(&original);
            merged_entries.extend(existing_entries);

            let merged_context =
                match Self::build_execution_context_from_entries(py, &merged_entries) {
                    Ok(context) => context,
                    Err(err) => {
                        let err = PyException::runtime_error(format!(
                            "failed to merge ExecutionContext entries: {err}"
                        ));
                        Self::set_exception_cause(&err, &original);
                        return Err(err);
                    }
                };

            Self::attach_execution_context(&original, &merged_context);
            Ok(original)
        })
    }

    pub fn assemble_active_chain(&self, exception: &PyException) -> Vec<ActiveChainEntry> {
        let raw_events = self.collect_raw_events();
        let entries = self.events_to_entries(&raw_events);
        let entries = self.dedup_adjacent(entries);
        self.inject_context(entries, exception)
    }

    pub(super) fn collect_raw_events(&self) -> Vec<CaptureEvent> {
        self.capture_log.clone()
    }

    pub(super) fn events_to_entries(&self, raw_events: &[CaptureEvent]) -> Vec<ActiveChainEntry> {
        let mut state = ActiveChainAssemblyState::new();
        for event in raw_events {
            self.apply_active_chain_event(&mut state, event);
        }
        self.merge_live_frame_state(&mut state);
        self.entries_from_active_chain_state(&state, raw_events)
    }

    pub(super) fn apply_active_chain_event(
        &self,
        state: &mut ActiveChainAssemblyState,
        event: &CaptureEvent,
    ) {
        match event {
            CaptureEvent::FrameEntered {
                frame_id,
                function_name,
                source_file,
                source_line,
                args_repr: _,
                program_call_repr,
            } => {
                state.frame_stack.push(ActiveChainFrameState {
                    frame_id: *frame_id,
                    function_name: function_name.clone(),
                    source_file: source_file.clone(),
                    source_line: *source_line,
                    sub_program_repr: program_call_repr
                        .clone()
                        .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                });
            }
            CaptureEvent::FrameExited { .. } => {
                let _ = state.frame_stack.pop();
            }
            CaptureEvent::DispatchStarted {
                dispatch_id,
                effect_repr,
                is_execution_context_effect,
                creation_site: _,
                handler_name: _,
                handler_kind: _,
                handler_source_file: _,
                handler_source_line: _,
                handler_chain_snapshot,
                effect_frame_id,
                effect_function_name,
                effect_source_file,
                effect_source_line,
            } => {
                let visible_effect = !*is_execution_context_effect;
                if let Some(frame_id) = effect_frame_id {
                    if visible_effect {
                        state.frame_dispatch.insert(*frame_id, *dispatch_id);
                        if let Some(frame) = state
                            .frame_stack
                            .iter_mut()
                            .find(|f| f.frame_id == *frame_id)
                        {
                            if let Some(line) = effect_source_line {
                                frame.source_line = *line;
                            }
                        }
                    }
                }

                state.dispatches.insert(
                    *dispatch_id,
                    ActiveChainDispatchState {
                        function_name: effect_function_name.clone(),
                        source_file: effect_source_file.clone(),
                        source_line: *effect_source_line,
                        effect_repr: effect_repr.clone(),
                        is_execution_context_effect: *is_execution_context_effect,
                        handler_stack: Self::handler_stack_from_snapshot(handler_chain_snapshot),
                        result: EffectResult::Active,
                    },
                );
            }
            CaptureEvent::Delegated {
                dispatch_id,
                from_handler_name: _,
                from_handler_index,
                to_handler_name: _,
                to_handler_index,
                to_handler_kind: _,
                to_handler_source_file: _,
                to_handler_source_line: _,
            } => {
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
                    if let Some(from_entry) = dispatch.handler_stack.get_mut(*from_handler_index) {
                        if from_entry.status == HandlerStatus::Active {
                            from_entry.status = HandlerStatus::Delegated;
                        }
                    }
                    if let Some(to_entry) = dispatch.handler_stack.get_mut(*to_handler_index) {
                        to_entry.status = HandlerStatus::Active;
                    }
                }
            }
            CaptureEvent::Passed {
                dispatch_id,
                from_handler_name: _,
                from_handler_index,
                to_handler_name: _,
                to_handler_index,
                to_handler_kind: _,
                to_handler_source_file: _,
                to_handler_source_line: _,
            } => {
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
                    if let Some(from_entry) = dispatch.handler_stack.get_mut(*from_handler_index) {
                        if from_entry.status == HandlerStatus::Active {
                            from_entry.status = HandlerStatus::Passed;
                        }
                    }
                    if let Some(to_entry) = dispatch.handler_stack.get_mut(*to_handler_index) {
                        to_entry.status = HandlerStatus::Active;
                    }
                }
            }
            CaptureEvent::HandlerCompleted {
                dispatch_id,
                handler_name,
                handler_index,
                action,
            } => {
                if let Some(dispatch) = state.dispatches.get_mut(dispatch_id) {
                    let status = match action {
                        HandlerAction::Resumed { .. } => HandlerStatus::Resumed,
                        HandlerAction::Transferred { .. } => HandlerStatus::Transferred,
                        HandlerAction::Returned { .. } => HandlerStatus::Returned,
                        HandlerAction::Threw { .. } => HandlerStatus::Threw,
                    };
                    if let Some(target) = dispatch.handler_stack.get_mut(*handler_index) {
                        target.status = status;
                    }

                    dispatch.result = match action {
                        HandlerAction::Resumed { value_repr }
                        | HandlerAction::Returned { value_repr } => EffectResult::Resumed {
                            value_repr: value_repr
                                .clone()
                                .unwrap_or_else(|| MISSING_NONE_REPR.to_string()),
                        },
                        HandlerAction::Transferred { value_repr } => EffectResult::Transferred {
                            handler_name: handler_name.clone(),
                            target_repr: state
                                .transfer_targets
                                .get(dispatch_id)
                                .cloned()
                                .unwrap_or_else(|| {
                                    value_repr
                                        .clone()
                                        .unwrap_or_else(|| MISSING_TARGET.to_string())
                                }),
                        },
                        HandlerAction::Threw { exception_repr } => EffectResult::Threw {
                            handler_name: handler_name.clone(),
                            exception_repr: exception_repr
                                .clone()
                                .unwrap_or_else(|| MISSING_EXCEPTION.to_string()),
                        },
                    };
                }
            }
            CaptureEvent::Resumed { .. } => {}
            CaptureEvent::Transferred {
                dispatch_id,
                resumed_function_name,
                source_file,
                source_line,
                ..
            } => {
                state.transfer_targets.insert(
                    *dispatch_id,
                    format!("{resumed_function_name}() {source_file}:{source_line}"),
                );
            }
        }
    }

    pub(super) fn handler_stack_from_snapshot(
        handler_chain_snapshot: &[HandlerSnapshotEntry],
    ) -> Vec<HandlerDispatchEntry> {
        handler_chain_snapshot
            .iter()
            .enumerate()
            .map(|(index, snapshot)| HandlerDispatchEntry {
                handler_name: snapshot.handler_name.clone(),
                handler_kind: snapshot.handler_kind.clone(),
                source_file: snapshot.source_file.clone(),
                source_line: snapshot.source_line,
                status: if index == 0 {
                    HandlerStatus::Active
                } else {
                    HandlerStatus::Pending
                },
            })
            .collect()
    }

    pub(super) fn merge_live_frame_state(&self, state: &mut ActiveChainAssemblyState) {
        self.merge_frame_lines_from_segments(&mut state.frame_stack);
        let (frame_stack, dispatches) = (&mut state.frame_stack, &state.dispatches);
        self.merge_frame_lines_from_visible_dispatch_snapshot(frame_stack, dispatches);
    }

    pub(super) fn merge_frame_lines_from_segments(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
    ) {
        let mut seg_chain = Vec::new();
        let mut seg_id = self.current_segment;
        while let Some(id) = seg_id {
            seg_chain.push(id);
            seg_id = self.segments.get(id).and_then(|seg| seg.caller);
        }
        seg_chain.reverse();

        for id in seg_chain {
            let Some(seg) = self.segments.get(id) else {
                continue;
            };
            for frame in &seg.frames {
                let Frame::Program {
                    stream,
                    metadata: Some(metadata),
                } = frame
                else {
                    continue;
                };
                Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata);
            }
        }
    }

    pub(super) fn merge_frame_lines_from_visible_dispatch_snapshot(
        &self,
        frame_stack: &mut Vec<ActiveChainFrameState>,
        dispatches: &HashMap<DispatchId, ActiveChainDispatchState>,
    ) {
        let Some(dispatch_ctx) = self.dispatch_stack.iter().rev().find(|ctx| {
            dispatches
                .get(&ctx.dispatch_id)
                .is_some_and(|dispatch| Self::is_visible_dispatch(dispatch))
        }) else {
            return;
        };

        for frame in dispatch_ctx.k_user.frames_snapshot.iter() {
            let Frame::Program {
                stream,
                metadata: Some(metadata),
            } = frame
            else {
                continue;
            };
            Self::upsert_frame_state_from_metadata(frame_stack, stream, metadata);
        }
    }

    pub(super) fn upsert_frame_state_from_metadata(
        frame_stack: &mut Vec<ActiveChainFrameState>,
        stream: &ASTStreamRef,
        metadata: &CallMetadata,
    ) {
        let line = Self::stream_debug_location(stream)
            .map(|location| location.source_line)
            .unwrap_or(metadata.source_line);
        if let Some(existing) = frame_stack
            .iter_mut()
            .find(|entry| entry.frame_id == metadata.frame_id)
        {
            existing.source_line = line;
            if existing.sub_program_repr == MISSING_SUB_PROGRAM {
                if let Some(repr) = Self::program_call_repr(metadata) {
                    existing.sub_program_repr = repr;
                }
            }
            return;
        }

        frame_stack.push(ActiveChainFrameState {
            frame_id: metadata.frame_id as FrameId,
            function_name: metadata.function_name.clone(),
            source_file: metadata.source_file.clone(),
            source_line: line,
            sub_program_repr: Self::program_call_repr(metadata)
                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
        });
    }

    pub(super) fn entries_from_active_chain_state(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = self.entries_from_frame_stack(state);
        if active_chain.is_empty() {
            self.fallback_entries_when_chain_empty(state, raw_events, &mut active_chain);
        }
        active_chain
    }

    pub(super) fn entries_from_frame_stack(
        &self,
        state: &ActiveChainAssemblyState,
    ) -> Vec<ActiveChainEntry> {
        let mut active_chain = Vec::new();
        for (index, frame) in state.frame_stack.iter().enumerate() {
            let dispatch_id = state.frame_dispatch.get(&frame.frame_id).copied();
            let dispatch = dispatch_id.and_then(|id| state.dispatches.get(&id));
            if let Some(dispatch) = dispatch.filter(|dispatch| Self::is_visible_dispatch(dispatch))
            {
                Self::push_effect_yield_entry(&mut active_chain, dispatch, Some(frame));
                continue;
            }

            active_chain.push(Self::program_yield_entry(
                frame,
                state.frame_stack.get(index + 1),
            ));
        }
        active_chain
    }

    pub(super) fn fallback_entries_when_chain_empty(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
        active_chain: &mut Vec<ActiveChainEntry>,
    ) {
        let Some(dispatch_id) = self.fallback_dispatch_id(state, raw_events) else {
            return;
        };
        let Some(dispatch) = state
            .dispatches
            .get(&dispatch_id)
            .filter(|dispatch| Self::is_visible_dispatch(dispatch))
        else {
            return;
        };

        let snapshot_frames = self.snapshot_frames_for_dispatch(dispatch_id);
        if snapshot_frames.is_empty() {
            Self::push_effect_yield_entry(active_chain, dispatch, None);
            return;
        }

        let last_index = snapshot_frames.len() - 1;
        for (index, frame) in snapshot_frames.iter().enumerate() {
            if index == last_index {
                Self::push_effect_yield_entry(active_chain, dispatch, Some(frame));
                continue;
            }
            active_chain.push(Self::program_yield_entry(
                frame,
                snapshot_frames.get(index + 1),
            ));
        }
    }

    pub(super) fn fallback_dispatch_id(
        &self,
        state: &ActiveChainAssemblyState,
        raw_events: &[CaptureEvent],
    ) -> Option<DispatchId> {
        self.dispatch_stack
            .iter()
            .rev()
            .find_map(|ctx| {
                let dispatch = state.dispatches.get(&ctx.dispatch_id)?;
                if Self::is_visible_dispatch(dispatch) {
                    Some(ctx.dispatch_id)
                } else {
                    None
                }
            })
            .or_else(|| {
                raw_events.iter().rev().find_map(|event| {
                    let dispatch_id = Self::dispatch_id_for_event(event)?;
                    let dispatch = state.dispatches.get(&dispatch_id)?;
                    if Self::is_visible_dispatch(dispatch) {
                        Some(dispatch_id)
                    } else {
                        None
                    }
                })
            })
    }

    pub(super) fn dispatch_id_for_event(event: &CaptureEvent) -> Option<DispatchId> {
        match event {
            CaptureEvent::DispatchStarted { dispatch_id, .. }
            | CaptureEvent::Delegated { dispatch_id, .. }
            | CaptureEvent::Passed { dispatch_id, .. }
            | CaptureEvent::HandlerCompleted { dispatch_id, .. }
            | CaptureEvent::Resumed { dispatch_id, .. }
            | CaptureEvent::Transferred { dispatch_id, .. } => Some(*dispatch_id),
            _ => None,
        }
    }

    pub(super) fn snapshot_frames_for_dispatch(
        &self,
        dispatch_id: DispatchId,
    ) -> Vec<ActiveChainFrameState> {
        self.dispatch_stack
            .iter()
            .rev()
            .find(|ctx| ctx.dispatch_id == dispatch_id)
            .map(|dispatch_ctx| {
                dispatch_ctx
                    .k_user
                    .frames_snapshot
                    .iter()
                    .filter_map(|frame| {
                        let Frame::Program {
                            stream,
                            metadata: Some(metadata),
                        } = frame
                        else {
                            return None;
                        };

                        let line = Self::stream_debug_location(stream)
                            .map(|location| location.source_line)
                            .unwrap_or(metadata.source_line);
                        Some(ActiveChainFrameState {
                            frame_id: metadata.frame_id as FrameId,
                            function_name: metadata.function_name.clone(),
                            source_file: metadata.source_file.clone(),
                            source_line: line,
                            sub_program_repr: Self::program_call_repr(metadata)
                                .unwrap_or_else(|| MISSING_SUB_PROGRAM.to_string()),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    pub(super) fn push_effect_yield_entry(
        chain: &mut Vec<ActiveChainEntry>,
        dispatch: &ActiveChainDispatchState,
        frame: Option<&ActiveChainFrameState>,
    ) {
        let function_name = dispatch.function_name.clone().unwrap_or_else(|| {
            frame
                .map(|snapshot| snapshot.function_name.clone())
                .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
        });
        let source_file = dispatch.source_file.clone().unwrap_or_else(|| {
            frame
                .map(|snapshot| snapshot.source_file.clone())
                .unwrap_or_else(|| MISSING_UNKNOWN.to_string())
        });
        let source_line = dispatch
            .source_line
            .unwrap_or_else(|| frame.map_or(0, |snapshot| snapshot.source_line));
        chain.push(ActiveChainEntry::EffectYield {
            function_name,
            source_file,
            source_line,
            effect_repr: dispatch.effect_repr.clone(),
            handler_stack: dispatch.handler_stack.clone(),
            result: dispatch.result.clone(),
        });
    }

    pub(super) fn program_yield_entry(
        frame: &ActiveChainFrameState,
        next_frame: Option<&ActiveChainFrameState>,
    ) -> ActiveChainEntry {
        let inferred_sub_program = next_frame.map(|next| format!("{}()", next.function_name));
        let sub_program_repr = if frame.sub_program_repr == MISSING_SUB_PROGRAM {
            inferred_sub_program.unwrap_or_else(|| frame.sub_program_repr.clone())
        } else {
            frame.sub_program_repr.clone()
        };
        ActiveChainEntry::ProgramYield {
            function_name: frame.function_name.clone(),
            source_file: frame.source_file.clone(),
            source_line: frame.source_line,
            sub_program_repr,
        }
    }

    pub(super) fn dedup_adjacent(&self, entries: Vec<ActiveChainEntry>) -> Vec<ActiveChainEntry> {
        let mut deduped = Vec::with_capacity(entries.len());
        for entry in entries {
            let is_duplicate = deduped
                .last()
                .is_some_and(|prev| Self::is_adjacent_duplicate(prev, &entry));
            if !is_duplicate {
                deduped.push(entry);
            }
        }
        deduped
    }

    pub(super) fn is_adjacent_duplicate(lhs: &ActiveChainEntry, rhs: &ActiveChainEntry) -> bool {
        match (lhs, rhs) {
            (
                ActiveChainEntry::ProgramYield {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    sub_program_repr: lhs_sub_program_repr,
                },
                ActiveChainEntry::ProgramYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    sub_program_repr: rhs_sub_program_repr,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_sub_program_repr == rhs_sub_program_repr
            }
            (
                ActiveChainEntry::EffectYield {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    effect_repr: lhs_effect_repr,
                    handler_stack: lhs_handler_stack,
                    result: lhs_result,
                },
                ActiveChainEntry::EffectYield {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    effect_repr: rhs_effect_repr,
                    handler_stack: rhs_handler_stack,
                    result: rhs_result,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_effect_repr == rhs_effect_repr
                    && lhs_handler_stack == rhs_handler_stack
                    && lhs_result == rhs_result
            }
            (
                ActiveChainEntry::ExceptionSite {
                    function_name: lhs_function_name,
                    source_file: lhs_source_file,
                    source_line: lhs_source_line,
                    exception_type: lhs_exception_type,
                    message: lhs_message,
                },
                ActiveChainEntry::ExceptionSite {
                    function_name: rhs_function_name,
                    source_file: rhs_source_file,
                    source_line: rhs_source_line,
                    exception_type: rhs_exception_type,
                    message: rhs_message,
                },
            ) => {
                lhs_function_name == rhs_function_name
                    && lhs_source_file == rhs_source_file
                    && lhs_source_line == rhs_source_line
                    && lhs_exception_type == rhs_exception_type
                    && lhs_message == rhs_message
            }
            _ => false,
        }
    }

    pub(super) fn inject_context(
        &self,
        mut active_chain: Vec<ActiveChainEntry>,
        exception: &PyException,
    ) -> Vec<ActiveChainEntry> {
        let context_entries = Self::context_entries_from_exception(exception);
        let has_context_entries = !context_entries.is_empty();
        for data in context_entries {
            active_chain.push(ActiveChainEntry::ContextEntry { data });
        }

        let exception_site = Self::exception_site(exception);
        let exception_function_name = match &exception_site {
            ActiveChainEntry::ExceptionSite { function_name, .. } => function_name.as_str(),
            _ => "",
        };
        let exception_function_is_visible = active_chain.iter().any(|entry| match entry {
            ActiveChainEntry::ProgramYield { function_name, .. }
            | ActiveChainEntry::EffectYield { function_name, .. }
            | ActiveChainEntry::ExceptionSite { function_name, .. } => {
                function_name == exception_function_name
            }
            ActiveChainEntry::ContextEntry { .. } => false,
        });

        let suppress_exception_site = !has_context_entries
            && active_chain
                .iter()
                .rev()
                .find(|entry| !matches!(entry, ActiveChainEntry::ContextEntry { .. }))
                .is_some_and(|entry| {
                    matches!(
                        entry,
                        ActiveChainEntry::EffectYield {
                            result: EffectResult::Threw { .. },
                            ..
                        }
                    ) && !exception_function_is_visible
                });
        if !suppress_exception_site {
            active_chain.push(exception_site);
        }
        active_chain
    }

    pub(super) fn is_visible_dispatch(dispatch: &ActiveChainDispatchState) -> bool {
        !dispatch.is_execution_context_effect
    }
}

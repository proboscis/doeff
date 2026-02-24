//! VM debug and trace-formatting helpers.

use super::*;

impl VM {
    pub(super) fn truncate_repr(mut text: String) -> String {
        const MAX_REPR_LEN: usize = 200;
        if text.len() > MAX_REPR_LEN {
            text.truncate(MAX_REPR_LEN);
            text.push_str("...");
        }
        text
    }

    pub(super) fn value_repr(value: &Value) -> Option<String> {
        let repr = match value {
            Value::None | Value::Unit => "None".to_string(),
            Value::Python(obj) => Python::attach(|py| {
                obj.bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<python-value>".to_string())
            }),
            other => format!("{other:?}"),
        };
        Some(Self::truncate_repr(repr))
    }

    pub(super) fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
        let repr = metadata.program_call.as_ref().map(|program_call| {
            Python::attach(|py| {
                program_call
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| MISSING_SUB_PROGRAM.to_string())
            })
        })?;
        Some(Self::truncate_repr(repr))
    }

    pub(super) fn exception_repr(exception: &PyException) -> Option<String> {
        let repr = match exception {
            PyException::Materialized { exc_value, .. } => Python::attach(|py| {
                exc_value
                    .bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| MISSING_EXCEPTION.to_string())
            }),
            PyException::RuntimeError { message } => format!("RuntimeError({message:?})"),
            PyException::TypeError { message } => format!("TypeError({message:?})"),
        };
        Some(Self::truncate_repr(repr))
    }

    pub(super) fn effect_repr(effect: &DispatchEffect) -> String {
        let repr = if let Some(obj) = dispatch_ref_as_python(effect) {
            Python::attach(|py| {
                obj.bind(py)
                    .repr()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|_| "<effect>".to_string())
            })
        } else {
            format!("{effect:?}")
        };
        Self::truncate_repr(repr)
    }

    pub(super) fn format_do_ctrl(yielded: &DoCtrl, verbosity: ModeFormatVerbosity) -> &'static str {
        let formatted = match yielded {
            DoCtrl::Pure { .. } => "HandleYield(Pure)",
            DoCtrl::Map { .. } => "HandleYield(Map)",
            DoCtrl::FlatMap { .. } => "HandleYield(FlatMap)",
            DoCtrl::Perform { .. } => "HandleYield(Perform)",
            DoCtrl::Resume { .. } => "HandleYield(Resume)",
            DoCtrl::Transfer { .. } => "HandleYield(Transfer)",
            DoCtrl::TransferThrow { .. } => "HandleYield(TransferThrow)",
            DoCtrl::ResumeThrow { .. } => "HandleYield(ResumeThrow)",
            DoCtrl::WithHandler { .. } => "HandleYield(WithHandler)",
            DoCtrl::WithIntercept { .. } => "HandleYield(WithIntercept)",
            DoCtrl::Delegate { .. } => "HandleYield(Delegate)",
            DoCtrl::Pass { .. } => "HandleYield(Pass)",
            DoCtrl::GetContinuation => "HandleYield(GetContinuation)",
            DoCtrl::GetHandlers => "HandleYield(GetHandlers)",
            DoCtrl::GetTraceback { .. } => "HandleYield(GetTraceback)",
            DoCtrl::CreateContinuation { .. } => "HandleYield(CreateContinuation)",
            DoCtrl::ResumeContinuation { .. } => "HandleYield(ResumeContinuation)",
            DoCtrl::PythonAsyncSyntaxEscape { .. } => "HandleYield(AsyncEscape)",
            DoCtrl::Apply { .. } => "HandleYield(Apply)",
            DoCtrl::Expand { .. } => "HandleYield(Expand)",
            DoCtrl::Eval { .. } => "HandleYield(Eval)",
            DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
            DoCtrl::GetTrace => "HandleYield(GetTrace)",
        };
        match verbosity {
            ModeFormatVerbosity::Compact | ModeFormatVerbosity::Verbose => formatted,
        }
    }

    pub(super) fn format_mode(&self, verbosity: ModeFormatVerbosity) -> &'static str {
        match &self.mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(yielded) => Self::format_do_ctrl(yielded, verbosity),
            Mode::Return(_) => "Return",
        }
    }

    pub(super) fn mode_kind(&self) -> &'static str {
        self.format_mode(ModeFormatVerbosity::Compact)
    }

    pub(super) fn pending_kind(&self) -> &'static str {
        self.pending_python
            .as_ref()
            .map(|p| match p {
                PendingPython::EvalExpr { .. } => "EvalExpr",
                PendingPython::CallFuncReturn { .. } => "CallFuncReturn",
                PendingPython::ExpandReturn { .. } => "ExpandReturn",
                PendingPython::StepUserGenerator { .. } => "StepUserGenerator",
                PendingPython::RustProgramContinuation { .. } => "RustProgramContinuation",
                PendingPython::AsyncEscape => "AsyncEscape",
            })
            .unwrap_or("None")
    }

    pub(super) fn result_kind(result: &StepEvent) -> String {
        match result {
            StepEvent::Continue => "Continue".to_string(),
            StepEvent::Done(_) => "Done".to_string(),
            StepEvent::Error(e) => format!("Error({e})"),
            StepEvent::NeedsPython(call) => {
                let call_kind = match call {
                    PythonCall::EvalExpr { .. } => "EvalExpr",
                    PythonCall::CallFunc { .. } => "CallFunc",
                    PythonCall::GenNext => "GenNext",
                    PythonCall::GenSend { .. } => "GenSend",
                    PythonCall::GenThrow { .. } => "GenThrow",
                    PythonCall::CallAsync { .. } => "CallAsync",
                };
                format!("NeedsPython({call_kind})")
            }
        }
    }

    pub(super) fn record_trace_entry(&mut self) {
        let mode = self.mode_kind().to_string();
        let pending = self.pending_kind().to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "enter".to_string(),
            mode,
            pending,
            dispatch_depth: self.dispatch_stack.len(),
            result: None,
        });
    }

    pub(super) fn record_trace_exit(&mut self, result: &StepEvent) {
        let mode = self.mode_kind().to_string();
        let pending = self.pending_kind().to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "exit".to_string(),
            mode,
            pending,
            dispatch_depth: self.dispatch_stack.len(),
            result: Some(Self::result_kind(result)),
        });
    }

    pub(super) fn debug_step_entry(&self) {
        let mode_kind = self.format_mode(ModeFormatVerbosity::Verbose);

        let seg_info = self
            .current_segment
            .and_then(|id| self.segments.get(id))
            .map(|s| format!("seg={:?} frames={}", self.current_segment, s.frames.len()))
            .unwrap_or_else(|| "seg=None".to_string());

        let pending = self.pending_kind();

        eprintln!(
            "[step {}] mode={} {} dispatch_depth={} pending={}",
            self.step_counter,
            mode_kind,
            seg_info,
            self.dispatch_stack.len(),
            pending
        );

        if self.debug.level == DebugLevel::Trace && self.debug.show_frames {
            if let Some(seg) = self.current_segment.and_then(|id| self.segments.get(id)) {
                for (i, frame) in seg.frames.iter().enumerate() {
                    let frame_kind = match frame {
                        Frame::RustReturn { .. } => "RustReturn",
                        Frame::Program { metadata, .. } if metadata.is_some() => "Program(meta)",
                        Frame::Program { .. } => "Program",
                    };
                    eprintln!("  frame[{}]: {}", i, frame_kind);
                }
            }
        }
    }

    pub(super) fn debug_step_exit(&self, result: &StepEvent) {
        let result_kind = match result {
            StepEvent::Continue => "Continue",
            StepEvent::Done(_) => "Done",
            StepEvent::Error(e) => {
                eprintln!("[step {}] -> Error: {}", self.step_counter, e);
                return;
            }
            StepEvent::NeedsPython(call) => {
                let call_kind = match call {
                    PythonCall::EvalExpr { .. } => "EvalExpr",
                    PythonCall::CallFunc { .. } => "CallFunc",
                    PythonCall::GenNext => "GenNext",
                    PythonCall::GenSend { .. } => "GenSend",
                    PythonCall::GenThrow { .. } => "GenThrow",
                    PythonCall::CallAsync { .. } => "CallAsync",
                };
                eprintln!("[step {}] -> NeedsPython({})", self.step_counter, call_kind);
                return;
            }
        };
        if self.debug.level == DebugLevel::Trace {
            eprintln!("[step {}] -> {}", self.step_counter, result_kind);
        }
    }
}

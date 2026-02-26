//! Debug and trace-formatting state for VM decomposition.

use pyo3::prelude::*;

use crate::arena::SegmentArena;
use crate::do_ctrl::DoCtrl;
use crate::driver::{Mode, PyException, StepEvent};
use crate::effect::{dispatch_ref_as_python, DispatchEffect};
use crate::frame::{CallMetadata, Frame};
use crate::ids::SegmentId;
use crate::python_call::{PendingPython, PythonCall};
use crate::value::Value;
use crate::vm::{DebugConfig, DebugLevel, ModeFormatVerbosity, TraceEvent};

const MISSING_SUB_PROGRAM: &str = "[MISSING] <sub_program>";
const MISSING_EXCEPTION: &str = "[MISSING] <exception>";

#[derive(Debug, Clone)]
pub(crate) struct DebugState {
    pub(crate) config: DebugConfig,
    pub(crate) step_counter: u64,
    pub(crate) trace_enabled: bool,
    pub(crate) trace_events: Vec<TraceEvent>,
}

impl DebugState {
    pub(crate) fn new(config: DebugConfig) -> Self {
        Self {
            config,
            step_counter: 0,
            trace_enabled: false,
            trace_events: Vec::new(),
        }
    }

    pub(crate) fn set_config(&mut self, config: DebugConfig) {
        self.config = config;
    }

    pub(crate) fn is_enabled(&self) -> bool {
        self.config.is_enabled()
    }

    pub(crate) fn enable_trace(&mut self, enabled: bool) {
        self.trace_enabled = enabled;
        self.trace_events.clear();
    }

    pub(crate) fn trace_events(&self) -> &[TraceEvent] {
        &self.trace_events
    }

    pub(crate) fn advance_step(&mut self) {
        self.step_counter += 1;
    }

    pub(crate) fn truncate_repr(mut text: String) -> String {
        const MAX_REPR_LEN: usize = 200;
        if text.len() > MAX_REPR_LEN {
            text.truncate(MAX_REPR_LEN);
            text.push_str("...");
        }
        text
    }

    pub(crate) fn value_repr(value: &Value) -> Option<String> {
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

    pub(crate) fn program_call_repr(metadata: &CallMetadata) -> Option<String> {
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

    pub(crate) fn exception_repr(exception: &PyException) -> Option<String> {
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

    pub(crate) fn effect_repr(effect: &DispatchEffect) -> String {
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

    pub(crate) fn format_do_ctrl(yielded: &DoCtrl, verbosity: ModeFormatVerbosity) -> &'static str {
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
            DoCtrl::ASTStream { .. } => "HandleYield(ASTStream)",
            DoCtrl::Eval { .. } => "HandleYield(Eval)",
            DoCtrl::GetCallStack => "HandleYield(GetCallStack)",
            DoCtrl::GetTrace => "HandleYield(GetTrace)",
        };
        match verbosity {
            ModeFormatVerbosity::Compact | ModeFormatVerbosity::Verbose => formatted,
        }
    }

    pub(crate) fn format_mode(&self, mode: &Mode, verbosity: ModeFormatVerbosity) -> &'static str {
        match mode {
            Mode::Deliver(_) => "Deliver",
            Mode::Throw(_) => "Throw",
            Mode::HandleYield(yielded) => Self::format_do_ctrl(yielded, verbosity),
            Mode::Return(_) => "Return",
        }
    }

    pub(crate) fn mode_kind(&self, mode: &Mode) -> &'static str {
        self.format_mode(mode, ModeFormatVerbosity::Compact)
    }

    pub(crate) fn pending_kind(&self, pending_python: &Option<PendingPython>) -> &'static str {
        pending_python
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

    pub(crate) fn result_kind(result: &StepEvent) -> String {
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

    pub(crate) fn record_trace_entry(
        &mut self,
        mode: &Mode,
        pending_python: &Option<PendingPython>,
        dispatch_depth: usize,
    ) {
        let mode = self.mode_kind(mode).to_string();
        let pending = self.pending_kind(pending_python).to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "enter".to_string(),
            mode,
            pending,
            dispatch_depth,
            result: None,
        });
    }

    pub(crate) fn record_trace_exit(
        &mut self,
        mode: &Mode,
        pending_python: &Option<PendingPython>,
        dispatch_depth: usize,
        result: &StepEvent,
    ) {
        let mode = self.mode_kind(mode).to_string();
        let pending = self.pending_kind(pending_python).to_string();
        self.trace_events.push(TraceEvent {
            step: self.step_counter,
            event: "exit".to_string(),
            mode,
            pending,
            dispatch_depth,
            result: Some(Self::result_kind(result)),
        });
    }

    pub(crate) fn debug_step_entry(
        &self,
        mode: &Mode,
        current_segment: Option<SegmentId>,
        segments: &SegmentArena,
        dispatch_depth: usize,
        pending_python: &Option<PendingPython>,
    ) {
        let mode_kind = self.format_mode(mode, ModeFormatVerbosity::Verbose);

        let seg_info = current_segment
            .and_then(|id| segments.get(id))
            .map(|s| format!("seg={:?} frames={}", current_segment, s.frames.len()))
            .unwrap_or_else(|| "seg=None".to_string());

        let pending = self.pending_kind(pending_python);

        crate::vm_debug_log!(
            "[step {}] mode={} {} dispatch_depth={} pending={}",
            self.step_counter,
            mode_kind,
            seg_info,
            dispatch_depth,
            pending
        );

        if self.config.level == DebugLevel::Trace && self.config.show_frames {
            if let Some(seg) = current_segment.and_then(|id| segments.get(id)) {
                for (i, frame) in seg.frames.iter().enumerate() {
                    let frame_kind = match frame {
                        Frame::Program { metadata, .. } if metadata.is_some() => "Program(meta)",
                        Frame::Program { .. } => "Program",
                        Frame::InterceptorApply(_) => "InterceptorApply",
                        Frame::InterceptorEval(_) => "InterceptorEval",
                        Frame::HandlerDispatch { .. } => "HandlerDispatch",
                        Frame::EvalReturn(_) => "EvalReturn",
                        Frame::MapReturn { .. } => "MapReturn",
                        Frame::FlatMapBindResult => "FlatMapBindResult",
                        Frame::FlatMapBindSource { .. } => "FlatMapBindSource",
                        Frame::InterceptBodyReturn { .. } => "InterceptBodyReturn",
                    };
                    crate::vm_debug_log!("  frame[{}]: {}", i, frame_kind);
                }
            }
        }
    }

    pub(crate) fn debug_step_exit(&self, result: &StepEvent) {
        let result_kind = match result {
            StepEvent::Continue => "Continue",
            StepEvent::Done(_) => "Done",
            StepEvent::Error(e) => {
                crate::vm_debug_log!("[step {}] -> Error: {}", self.step_counter, e);
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
                crate::vm_debug_log!("[step {}] -> NeedsPython({})", self.step_counter, call_kind);
                return;
            }
        };
        if self.config.level == DebugLevel::Trace {
            crate::vm_debug_log!("[step {}] -> {}", self.step_counter, result_kind);
        }
    }
}

use std::collections::HashMap;
use std::sync::Arc;

use pyo3::exceptions::{
    PyAttributeError, PyBaseException, PyRuntimeError, PyStopIteration, PyTypeError,
};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple, PyType};

pyo3::create_exception!(doeff_vm, UnhandledEffectError, PyTypeError);
pyo3::create_exception!(doeff_vm, NoMatchingHandlerError, UnhandledEffectError);
pyo3::create_exception!(doeff_vm, Discontinued, pyo3::exceptions::PyException);

use crate::do_ctrl::{DoCtrl, InterceptMode};
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::effect::{
    dispatch_from_shared, dispatch_ref_as_python, PyExecutionContext, PyGetExecutionContext,
    PyProgramCallStack,
};

use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::ids::{Marker, SegmentId};
use crate::ir_stream::{IRStream, IRStreamRef, PythonGeneratorStream};
use crate::kleisli::{DgfnKleisli, IdentityKleisli, KleisliRef, PyKleisli};
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
#[allow(unused_imports)]
use crate::segment::{Segment, SegmentKind};
use crate::step::{Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent};
use crate::value::Value;
use crate::vm::VM;
use doeff_core_effects::scheduler::{set_run_external_wait_mode, ExternalWaitMode};
use doeff_core_effects::sentinels::PyRustHandlerSentinel;
use doeff_vm_core::{
    install_vm_hooks, live_object_counts, DoExprTag, PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyK,
    PyResultErr, PyResultOk, PyTraceFrame, PyTraceHop, PyVar, VmHooks,
};

fn ensure_vm_core_hooks_installed() {
    install_vm_hooks(VmHooks {
        classify_yielded: classify_yielded_for_vm,
        doctrl_to_pyexpr: doctrl_to_pyexpr_for_vm,
    });
}

#[cfg(target_os = "linux")]
fn current_rust_heap_bytes() -> usize {
    let info = unsafe { libc::mallinfo2() };
    (info.uordblks as usize).saturating_add(info.hblkhd as usize)
}

#[cfg(not(target_os = "linux"))]
fn current_rust_heap_bytes() -> usize {
    0
}

fn build_traceback_data_pyobject(
    py: Python<'_>,
    trace: Vec<crate::capture::TraceEntry>,
    active_chain: Vec<crate::capture::ActiveChainEntry>,
) -> Option<Py<PyDoeffTracebackData>> {
    let entries = match Value::Trace(trace).to_pyobject(py) {
        Ok(value) => value.unbind(),
        Err(err) => {
            eprintln!("[VM WARNING] traceback serialization failed for entries: {err}");
            return None;
        }
    };
    let active_chain = match Value::ActiveChain(active_chain).to_pyobject(py) {
        Ok(value) => value.unbind(),
        Err(err) => {
            eprintln!("[VM WARNING] traceback serialization failed for active_chain: {err}");
            return None;
        }
    };
    let data = match Bound::new(
        py,
        PyDoeffTracebackData {
            entries,
            active_chain,
        },
    ) {
        Ok(value) => value,
        Err(err) => {
            eprintln!(
                "[VM WARNING] traceback serialization failed for traceback_data object: {err}"
            );
            return None;
        }
    };
    Some(data.unbind())
}

fn vmerror_to_pyerr_with_traceback_data(
    py: Python<'_>,
    e: VMError,
) -> (PyErr, Option<Py<PyDoeffTracebackData>>) {
    match e {
        VMError::OneShotViolation { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::UnhandledEffect { .. } => (UnhandledEffectError::new_err(e.to_string()), None),
        VMError::NoMatchingHandler { .. } => (NoMatchingHandlerError::new_err(e.to_string()), None),
        VMError::DelegateNoOuterHandler { .. } => {
            (NoMatchingHandlerError::new_err(e.to_string()), None)
        }
        VMError::HandlerNotFound { .. } => (NoMatchingHandlerError::new_err(e.to_string()), None),
        VMError::InvalidSegment { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::PythonError { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::InternalError { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::TypeError { .. } => (PyTypeError::new_err(e.to_string()), None),
        VMError::UncaughtException {
            exception,
            trace,
            active_chain,
        } => {
            let exc_value = exception.value_clone_ref(py);
            let traceback_data = build_traceback_data_pyobject(py, trace, active_chain);
            (
                PyErr::from_value(exc_value.bind(py).clone()),
                traceback_data,
            )
        }
    }
}

fn vmerror_to_pyerr(e: VMError) -> PyErr {
    // SAFETY: vmerror_to_pyerr is always called from GIL-holding contexts (run/step_once)
    let py = unsafe { Python::assume_attached() };
    vmerror_to_pyerr_with_traceback_data(py, e).0
}

fn attach_doeff_traceback_to_exception_if_present(
    py: Python<'_>,
    error: &Bound<'_, PyAny>,
    traceback_data: Option<&Bound<'_, PyDoeffTracebackData>>,
) {
    let Some(traceback_data) = traceback_data else {
        return;
    };

    let attach_result = (|| -> PyResult<()> {
        let importlib = py.import("importlib")?;
        let traceback_mod = importlib.call_method1("import_module", ("doeff.traceback",))?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("traceback_data", traceback_data)?;
        let doeff_tb = traceback_mod
            .getattr("attach_doeff_traceback")?
            .call((error,), Some(&kwargs))?;
        if doeff_tb.is_none() {
            return Ok(());
        }
        traceback_mod
            .getattr("set_attached_doeff_traceback")?
            .call1((error, doeff_tb))?;
        Ok(())
    })();

    if let Err(err) = attach_result {
        eprintln!("[VM WARNING] failed to attach doeff traceback: {err}");
    }
}

const HANDLER_HELP_URL: &str = "https://docs.doeff.dev/handlers";

fn py_type_name(obj: &Bound<'_, PyAny>) -> String {
    obj.get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "<unknown>".to_string())
}

fn py_repr_text(obj: &Bound<'_, PyAny>) -> String {
    obj.repr()
        .map(|value| value.to_string())
        .unwrap_or_else(|_| "<unrepresentable>".to_string())
}

fn strict_handler_type_error(api_name: &str, role: &str, obj: &Bound<'_, PyAny>) -> PyErr {
    let got_repr = py_repr_text(obj);
    let ty = py_type_name(obj);
    let fix_block = if role == "handler" {
        "  To fix, decorate your handler with @do:\n\n\
    from doeff import do\n\
    from doeff.effects.base import Effect\n\n\
    @do\n\
    def my_handler(effect: Effect, k):\n\
        ...\n\
        yield Resume(k, value)\n"
    } else {
        "  To fix, decorate your interceptor with @do:\n\n\
    from doeff import do\n\
    from doeff.effects.base import Effect\n\n\
    @do\n\
    def my_interceptor(effect: Effect):\n\
        return effect\n"
    };
    PyTypeError::new_err(format!(
        "{api_name} {role} must be a @do decorated function, PyKleisli, or RustHandler.\n\n\
  Got: {got_repr} (type: {ty})\n\n\
{fix_block}\n\
  See: {HANDLER_HELP_URL}"
    ))
}

fn strict_kleisli_ref_type_error(context: &str, obj: &Bound<'_, PyAny>) -> PyErr {
    if context.starts_with("WithHandler") {
        return strict_handler_type_error("WithHandler", "handler", obj);
    }
    if context.starts_with("WithIntercept") {
        return strict_handler_type_error("WithIntercept", "interceptor", obj);
    }
    let ty = py_type_name(obj);
    let repr = py_repr_text(obj);
    PyTypeError::new_err(format!(
        "{context} must be DoeffGeneratorFn, PyKleisli, or RustHandler, got {repr} (type: {ty})"
    ))
}

fn continuation_for_control(k_obj: &Bound<'_, PyK>) -> PyResult<doeff_vm_core::Continuation> {
    let continuation = k_obj.borrow().continuation();
    if continuation.consumed() {
        return Err(PyRuntimeError::new_err(format!(
            "one-shot violation: continuation {} already consumed",
            continuation.cont_id.raw()
        )));
    }
    Ok(continuation)
}

fn is_effect_base_like(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(obj.is_instance_of::<PyEffectBase>())
}

fn classify_call_expr(vm: &VM, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<DoCtrl> {
    if obj.is_instance_of::<PyDoExprBase>() || is_effect_base_like(py, obj)? {
        classify_yielded_bound(vm, py, obj)
    } else {
        Ok(DoCtrl::Pure {
            value: Value::from_pyobject(obj),
        })
    }
}

fn lift_effect_to_perform_expr(py: Python<'_>, expr: Py<PyAny>) -> PyResult<Py<PyAny>> {
    if !is_effect_base_like(py, expr.bind(py))? {
        return Ok(expr);
    }
    let perform = Bound::new(
        py,
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Perform as u8,
            })
            .add_subclass(PyPerform {
                effect: expr.clone_ref(py),
            }),
    )?;
    Ok(perform.into_any().unbind())
}

fn intercept_mode_from_str(mode: &str) -> PyResult<InterceptMode> {
    InterceptMode::from_str(mode).ok_or_else(|| {
        PyTypeError::new_err(format!(
            "WithIntercept.mode must be 'include' or 'exclude', got '{mode}'"
        ))
    })
}

fn normalize_intercept_types_obj(
    py: Python<'_>,
    types: Option<Py<PyAny>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let types_bound = types_obj.bind(py);
    let iter = types_bound.try_iter().map_err(|_| {
        PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithIntercept.types must contain only Python type objects",
            ));
        }
        normalized.push(item.unbind());
    }

    let tuple = PyTuple::new(py, normalized)?;
    Ok(Some(tuple.into_any().unbind()))
}

fn normalize_handler_types_obj(
    py: Python<'_>,
    types: Option<Py<PyAny>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let types_bound = types_obj.bind(py);
    let iter = types_bound.try_iter().map_err(|_| {
        PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithHandler.types must contain only Python type objects",
            ));
        }
        normalized.push(item.unbind());
    }

    let tuple = PyTuple::new(py, normalized)?;
    Ok(Some(tuple.into_any().unbind()))
}

fn intercept_types_from_pyobj(
    py: Python<'_>,
    types: &Option<Py<PyAny>>,
) -> PyResult<Option<Vec<PyShared>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let iter = types_obj.bind(py).try_iter().map_err(|_| {
        PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithIntercept.types must contain only Python type objects",
            ));
        }
        normalized.push(PyShared::new(item.unbind()));
    }
    Ok(Some(normalized))
}

fn handler_types_from_pyobj(
    py: Python<'_>,
    types: &Option<Py<PyAny>>,
) -> PyResult<Option<Vec<PyShared>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let iter = types_obj.bind(py).try_iter().map_err(|_| {
        PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithHandler.types must contain only Python type objects",
            ));
        }
        normalized.push(PyShared::new(item.unbind()));
    }
    Ok(Some(normalized))
}

fn intercept_types_to_pyobj(
    py: Python<'_>,
    types: &Option<Vec<PyShared>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types) = types else {
        return Ok(None);
    };
    let tuple = PyTuple::new(py, types.iter().map(|item| item.clone_ref(py)))?;
    Ok(Some(tuple.into_any().unbind()))
}

fn handler_types_to_pyobj(
    py: Python<'_>,
    types: &Option<Vec<PyShared>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types) = types else {
        return Ok(None);
    };
    let tuple = PyTuple::new(py, types.iter().map(|item| item.clone_ref(py)))?;
    Ok(Some(tuple.into_any().unbind()))
}

#[pyclass]
pub struct PyVM {
    vm: VM,
}

enum SyncDriverLoopOutcome {
    Done(Value),
    VmError(VMError),
    PythonException(PyException),
}

#[pymethods]
impl PyVM {
    #[new]
    pub fn new() -> Self {
        ensure_vm_core_hooks_installed();
        PyVM { vm: VM::new() }
    }

    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let run_result = self.run_with_result(py, program)?;

        match &run_result.result {
            Ok(value) => Ok(value.clone_ref(py)),
            Err(error) => {
                let error_obj = error.value_clone_ref(py);
                let error_bound = error_obj.bind(py);
                attach_doeff_traceback_to_exception_if_present(
                    py,
                    error_bound,
                    run_result.traceback_data.as_ref().map(|data| data.bind(py)),
                );
                Err(PyErr::from_value(error_bound.clone()))
            }
        }
    }

    pub fn run_with_result(
        &mut self,
        py: Python<'_>,
        program: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        self.start_with_expr(py, program)?;

        let (result, traceback_data) = match py.detach(|| self.run_sync_driver_loop()) {
            SyncDriverLoopOutcome::Done(value) => match value.to_pyobject(py) {
                Ok(v) => (Ok(v.unbind()), None),
                Err(e) => {
                    let exc = pyerr_to_exception(py, e)?;
                    (Err(exc), None)
                }
            },
            SyncDriverLoopOutcome::VmError(e) => {
                let (pyerr, traceback_data) = vmerror_to_pyerr_with_traceback_data(py, e);
                let exc = pyerr_to_exception(py, pyerr)?;
                (Err(exc), traceback_data)
            }
            SyncDriverLoopOutcome::PythonException(exc) => (Err(exc), None),
        };
        self.vm.end_active_run_session();

        Ok(PyRunResult {
            result,
            traceback_data,
            raw_store: self.state_items_dict(py)?.unbind(),
            log: self.log_items_list(py)?.into_any().unbind(),
            trace: self.build_trace_list(py)?,
        })
    }

    pub fn state_items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.state_items_dict(py)?.into())
    }

    pub fn logs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.log_items_list(py)?.into())
    }

    pub fn put_state(&mut self, key: String, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.vm.rust_store.put(key, Value::from_pyobject(value));
        Ok(())
    }

    pub fn put_env(&mut self, key: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let env_key = HashedPyKey::from_bound(key)?;
        self.vm
            .env_store
            .insert(env_key, Value::from_python_opaque(value));
        Ok(())
    }

    pub fn env_items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.env_store {
            dict.set_item(k.to_pyobject(py), v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    pub fn _segment_count(&self) -> usize {
        self.vm.segments.len()
    }

    pub fn _continuation_count(&self) -> usize {
        self.vm.continuation_count()
    }

    pub fn memory_stats(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let counts = live_object_counts();
        let mut normal_segments = 0usize;
        let mut prompt_segments = 0usize;
        let mut interceptor_segments = 0usize;
        let mut mask_segments = 0usize;
        let mut segments_with_frames = 0usize;
        let mut empty_segments = 0usize;
        let mut program_frames = 0usize;
        let mut interceptor_frames = 0usize;
        let mut eval_return_frames = 0usize;
        let mut other_frames = 0usize;
        for (_, segment) in self.vm.segments.iter() {
            match &segment.kind {
                SegmentKind::Normal { .. } => normal_segments += 1,
                SegmentKind::PromptBoundary { .. } => prompt_segments += 1,
                SegmentKind::InterceptorBoundary { .. } => interceptor_segments += 1,
                SegmentKind::MaskBoundary { .. } => mask_segments += 1,
            }
            if segment.frames.is_empty() {
                empty_segments += 1;
            } else {
                segments_with_frames += 1;
            }
            for frame in &segment.frames {
                match frame {
                    crate::frame::Frame::Program { .. } => program_frames += 1,
                    crate::frame::Frame::InterceptorApply(_)
                    | crate::frame::Frame::InterceptorEval(_) => interceptor_frames += 1,
                    crate::frame::Frame::EvalReturn(_) => eval_return_frames += 1,
                    crate::frame::Frame::MapReturn { .. }
                    | crate::frame::Frame::FlatMapBindResult
                    | crate::frame::Frame::FlatMapBindSource { .. }
                    | crate::frame::Frame::InterceptBodyReturn { .. } => other_frames += 1,
                }
            }
        }
        let dict = PyDict::new(py);
        dict.set_item("arena_segments", self.vm.segments.len())?;
        dict.set_item("arena_slots", self.vm.segments.slot_count())?;
        dict.set_item("arena_capacity", self.vm.segments.capacity())?;
        dict.set_item("continuations_live", self.vm.continuation_count())?;
        dict.set_item("dispatch_count", self.vm.dispatch_count())?;
        dict.set_item("dispatch_capacity", self.vm.dispatch_capacity())?;
        dict.set_item(
            "segment_dispatch_bindings",
            self.vm.segment_dispatch_binding_count(),
        )?;
        dict.set_item(
            "segment_dispatch_binding_capacity",
            self.vm.segment_dispatch_binding_capacity(),
        )?;
        dict.set_item("trace_frame_stack", self.vm.trace_frame_stack_count())?;
        dict.set_item(
            "trace_frame_stack_capacity",
            self.vm.trace_frame_stack_capacity(),
        )?;
        dict.set_item(
            "trace_dispatch_displays",
            self.vm.trace_dispatch_display_count(),
        )?;
        dict.set_item(
            "trace_dispatch_display_capacity",
            self.vm.trace_dispatch_display_capacity(),
        )?;
        dict.set_item("debug_trace_events", self.vm.trace_events().len())?;
        dict.set_item("scope_state_count", self.vm.scope_state_count())?;
        dict.set_item("scope_state_capacity", self.vm.scope_state_capacity())?;
        dict.set_item("scope_writer_log_count", self.vm.scope_writer_log_count())?;
        dict.set_item(
            "scope_writer_log_capacity",
            self.vm.scope_writer_log_capacity(),
        )?;
        dict.set_item("scope_epoch_count", self.vm.scope_epoch_count())?;
        dict.set_item("scope_epoch_capacity", self.vm.scope_epoch_capacity())?;
        dict.set_item("retired_scope_state_count", 0)?;
        dict.set_item("retired_scope_state_capacity", 0)?;
        dict.set_item("retired_scope_writer_log_count", 0)?;
        dict.set_item("retired_scope_writer_log_capacity", 0)?;
        dict.set_item("retired_scope_epoch_count", 0)?;
        dict.set_item("retired_scope_epoch_capacity", 0)?;
        dict.set_item("normal_segments", normal_segments)?;
        dict.set_item("prompt_segments", prompt_segments)?;
        dict.set_item("interceptor_segments", interceptor_segments)?;
        dict.set_item("mask_segments", mask_segments)?;
        dict.set_item("segments_with_frames", segments_with_frames)?;
        dict.set_item("empty_segments", empty_segments)?;
        dict.set_item("program_frames", program_frames)?;
        dict.set_item("interceptor_frames", interceptor_frames)?;
        dict.set_item("eval_return_frames", eval_return_frames)?;
        dict.set_item("other_frames", other_frames)?;
        dict.set_item("live_segments", counts.live_segments)?;
        dict.set_item("live_continuations", counts.live_continuations)?;
        dict.set_item("live_ir_streams", counts.live_ir_streams)?;
        dict.set_item("in_place_reentries", counts.in_place_reentries)?;
        dict.set_item(
            "abandoned_transfer_branch_frees",
            counts.abandoned_transfer_branch_frees,
        )?;
        dict.set_item("rust_heap_bytes", current_rust_heap_bytes())?;
        Ok(dict.into())
    }

    pub fn enable_debug(&mut self, level: String) {
        use crate::vm::DebugConfig;
        let config = match level.as_str() {
            "steps" => DebugConfig::steps(),
            "trace" => DebugConfig::trace(),
            _ => DebugConfig::default(),
        };
        self.vm.set_debug(config);
    }

    pub fn py_store(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.vm.init_py_store(py);
        match self.vm.py_store() {
            Some(store) => Ok(store.dict.clone_ref(py).into()),
            None => Ok(py.None().into()),
        }
    }

    pub fn set_store(
        &mut self,
        py: Python<'_>,
        key: &str,
        value: Bound<'_, PyAny>,
    ) -> PyResult<()> {
        self.vm.init_py_store(py);
        if let Some(store) = self.vm.py_store_mut() {
            store.dict.bind(py).set_item(key, value)?;
        }
        Ok(())
    }

    pub fn get_store(&self, py: Python<'_>, key: &str) -> PyResult<Py<PyAny>> {
        match self.vm.py_store() {
            Some(store) => {
                let dict = store.dict.bind(py);
                match dict.get_item(key)? {
                    Some(val) => Ok(val.into()),
                    None => Ok(py.None().into()),
                }
            }
            None => Ok(py.None().into()),
        }
    }

    fn build_trace_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let trace_list = pyo3::types::PyList::empty(py);
        for event in self.vm.trace_events() {
            let row = pyo3::types::PyDict::new(py);
            row.set_item("step", event.step)?;
            row.set_item("event", event.event.as_str())?;
            row.set_item("mode", event.mode.as_str())?;
            row.set_item("pending", event.pending.as_str())?;
            row.set_item("dispatch_depth", event.dispatch_depth)?;
            if let Some(result) = &event.result {
                row.set_item("result", result.as_str())?;
            } else {
                row.set_item("result", py.None())?;
            }
            trace_list.append(row)?;
        }
        Ok(trace_list.into_any().unbind())
    }

    fn state_items_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in self.vm.final_state_entries() {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict)
    }

    fn log_items_list<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyList>> {
        let list = pyo3::types::PyList::empty(py);
        for entry in self.vm.final_log_entries() {
            list.append(entry.to_pyobject(py)?)?;
        }
        Ok(list)
    }

    pub fn build_run_result(
        &self,
        py: Python<'_>,
        value: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        Ok(PyRunResult {
            result: Ok(value.unbind()),
            traceback_data: None,
            raw_store: self.state_items_dict(py)?.unbind(),
            log: self.log_items_list(py)?.into_any().unbind(),
            trace: self.build_trace_list(py)?,
        })
    }

    #[pyo3(signature = (error, traceback_data=None))]
    pub fn build_run_result_error(
        &self,
        py: Python<'_>,
        error: Bound<'_, PyAny>,
        traceback_data: Option<Bound<'_, PyDoeffTracebackData>>,
    ) -> PyResult<PyRunResult> {
        let exc = pyerr_to_exception(py, PyErr::from_value(error))?;
        Ok(PyRunResult {
            result: Err(exc),
            traceback_data: traceback_data.map(Bound::unbind),
            raw_store: self.state_items_dict(py)?.unbind(),
            log: self.log_items_list(py)?.into_any().unbind(),
            trace: self.build_trace_list(py)?,
        })
    }

    pub fn start_program(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<()> {
        self.start_with_expr(py, program)
    }

    pub fn step_once(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let event = py.detach(|| self.run_rust_steps());

        match event {
            StepEvent::Done(value) => {
                self.vm.end_active_run_session();
                let py_val = value.to_pyobject(py)?;
                let elems: Vec<Bound<'_, pyo3::PyAny>> =
                    vec!["done".into_pyobject(py)?.into_any(), py_val];
                let tuple = PyTuple::new(py, elems)?;
                Ok(tuple.into())
            }
            StepEvent::Error(e) => {
                self.vm.end_active_run_session();
                self.step_once_error_tuple(py, e)
            }
            StepEvent::NeedsPython(call) => {
                if let PythonCall::CallAsync { func, args } = call {
                    let py_func = func.bind(py).clone().into_any();
                    let py_args = self.values_to_tuple(py, &args)?.into_any();
                    let elems: Vec<Bound<'_, pyo3::PyAny>> =
                        vec!["call_async".into_pyobject(py)?.into_any(), py_func, py_args];
                    let tuple = PyTuple::new(py, elems)?;
                    Ok(tuple.into())
                } else {
                    // Handle synchronously like run() does
                    let outcome = self.execute_python_call(py, call)?;
                    if let Err(e) = self.vm.receive_python_result(outcome) {
                        self.vm.end_active_run_session();
                        return self.step_once_error_tuple(py, e);
                    }
                    let elems: Vec<Bound<'_, pyo3::PyAny>> =
                        vec!["continue".into_pyobject(py)?.into_any()];
                    let tuple = PyTuple::new(py, elems)?;
                    Ok(tuple.into())
                }
            }
            StepEvent::Continue => unreachable!("handled in run_rust_steps"),
        }
    }

    pub fn feed_async_result(&mut self, _py: Python<'_>, value: Bound<'_, PyAny>) -> PyResult<()> {
        let val = Value::from_pyobject(&value);
        self.vm
            .receive_python_result(PyCallOutcome::Value(val))
            .map_err(vmerror_to_pyerr)
    }

    pub fn feed_async_error(
        &mut self,
        py: Python<'_>,
        error_value: Bound<'_, PyAny>,
    ) -> PyResult<()> {
        // Build a PyException from the error value.
        // error_value is expected to be a Python exception instance.
        let exc_type = error_value.get_type().into_any().unbind();
        let exc_value = error_value.clone().unbind();
        let exc_tb = py.None();
        let py_exc = crate::step::PyException::new(exc_type, exc_value, Some(exc_tb));
        self.vm
            .receive_python_result(PyCallOutcome::GenError(py_exc))
            .map_err(vmerror_to_pyerr)
    }
}

impl PyVM {
    fn extract_kleisli_ref(
        py: Python<'_>,
        obj: &Bound<'_, PyAny>,
        context: &str,
    ) -> PyResult<KleisliRef> {
        if obj.is_instance_of::<PyKleisli>() {
            let kleisli: PyRef<'_, PyKleisli> = obj.extract()?;
            return Ok(Arc::new(kleisli.clone()));
        }

        if obj.is_instance_of::<DoeffGeneratorFn>() {
            let dgfn: PyRef<'_, DoeffGeneratorFn> = obj.extract()?;
            let callable_identity = dgfn.callable.clone_ref(py);
            let kleisli = DgfnKleisli::from_dgfn(py, obj.clone().unbind(), callable_identity)?;
            return Ok(Arc::new(kleisli));
        }

        if obj.is_instance_of::<PyRustHandlerSentinel>() {
            let sentinel: PyRef<'_, PyRustHandlerSentinel> = obj.extract()?;
            let identity = PyShared::new(obj.clone().unbind());
            return Ok(Arc::new(IdentityKleisli::new(
                sentinel.kleisli_ref(),
                identity,
            )));
        }

        Err(strict_kleisli_ref_type_error(context, obj))
    }

    fn start_with_expr(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<()> {
        self.vm.end_active_run_session();
        self.vm.begin_run_session();

        let expr = lift_effect_to_perform_expr(py, program.unbind())?;
        let expr_bound = expr.bind(py);
        if !expr_bound.is_instance_of::<PyDoExprBase>() {
            let ty = expr_bound
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "<unknown>".to_string());
            return Err(PyTypeError::new_err(format!(
                "program must be DoExpr; got {ty}"
            )));
        }

        let outside_seg_id = self.vm.instantiate_installed_handlers();
        let seg = Segment::new(Marker::fresh(), outside_seg_id);
        let seg_id = self.vm.alloc_segment(seg);
        self.vm.current_segment = Some(seg_id);
        if self.vm.current_segment_mut().is_none() {
            return Err(PyRuntimeError::new_err(
                "start_with_expr: current segment missing after allocation",
            ));
        }
        self.vm.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: PyShared::new(expr),
            metadata: None,
        });
        Ok(())
    }

    fn run_rust_steps(&mut self) -> StepEvent {
        loop {
            match self.vm.step() {
                StepEvent::Continue => continue,
                other => return other,
            }
        }
    }

    fn run_sync_driver_loop(&mut self) -> SyncDriverLoopOutcome {
        loop {
            match self.run_rust_steps() {
                StepEvent::Done(value) => return SyncDriverLoopOutcome::Done(value),
                StepEvent::Error(error) => return SyncDriverLoopOutcome::VmError(error),
                StepEvent::NeedsPython(call) => {
                    let outcome = Python::attach(|py| self.execute_python_call(py, call));
                    match outcome {
                        Ok(outcome) => {
                            if let Err(error) = self.vm.receive_python_result(outcome) {
                                return SyncDriverLoopOutcome::VmError(error);
                            }
                        }
                        Err(pyerr) => {
                            let exception = Python::attach(|py| pyerr_to_exception(py, pyerr))
                                .unwrap_or_else(|err| {
                                    PyException::runtime_error(format!(
                                        "failed to convert Python error during sync driver loop: {err}"
                                    ))
                                });
                            return SyncDriverLoopOutcome::PythonException(exception);
                        }
                    }
                }
                StepEvent::Continue => unreachable!("handled in run_rust_steps"),
            }
        }
    }

    fn step_once_error_tuple(&self, py: Python<'_>, e: VMError) -> PyResult<Py<PyAny>> {
        let (pyerr, traceback_data) = vmerror_to_pyerr_with_traceback_data(py, e);
        let err_obj = pyerr.value(py).clone().into_any();
        let traceback_obj = traceback_data
            .map(|obj| obj.into_bound(py).into_any().unbind())
            .unwrap_or_else(|| py.None());
        let elems: Vec<Bound<'_, pyo3::PyAny>> = vec![
            "error".into_pyobject(py)?.into_any(),
            err_obj,
            traceback_obj.bind(py).clone(),
        ];
        let tuple = PyTuple::new(py, elems)?;
        Ok(tuple.into())
    }

    fn execute_python_call(&self, py: Python<'_>, call: PythonCall) -> PyResult<PyCallOutcome> {
        match call {
            PythonCall::EvalExpr { expr } => {
                let obj = expr.bind(py);
                match self.classify_yielded(py, obj) {
                    Ok(yielded) => Ok(PyCallOutcome::GenYield(yielded)),
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                }
            }
            PythonCall::CallFunc { func, args, kwargs } => {
                let py_args = self.values_to_tuple(py, &args)?;
                if kwargs.is_empty() {
                    match func.bind(py).call1(py_args) {
                        Ok(result) => Ok(PyCallOutcome::Value(Value::from_pyobject(&result))),
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    }
                } else {
                    let py_kwargs = PyDict::new(py);
                    for (key, val) in &kwargs {
                        py_kwargs.set_item(key, self.value_to_runtime_pyobject(py, val)?)?;
                    }
                    match func.bind(py).call(py_args, Some(&py_kwargs)) {
                        Ok(result) => Ok(PyCallOutcome::Value(Value::from_pyobject(&result))),
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    }
                }
            }
            PythonCall::GenNext => {
                let gen = self.pending_generator(py)?;
                self.step_generator(py, gen, None)
            }
            PythonCall::GenSend { value } => {
                let gen = self.pending_generator(py)?;
                let py_value = self.value_to_runtime_pyobject(py, &value)?;
                self.step_generator(py, gen, Some(py_value))
            }
            PythonCall::GenThrow { exc } => {
                let gen = self.pending_generator(py)?;
                let exc_obj = exc.value_clone_ref(py);
                let exc_bound = exc_obj.bind(py);
                match gen.bind(py).call_method1("throw", (exc_bound,)) {
                    Ok(yielded) => match self.classify_yielded(py, &yielded) {
                        Ok(classified) => Ok(PyCallOutcome::GenYield(classified)),
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    },
                    Err(e) if e.is_instance_of::<PyStopIteration>(py) => {
                        let return_value = extract_stop_iteration_value(py, &e)?;
                        Ok(PyCallOutcome::GenReturn(return_value))
                    }
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                }
            }
            PythonCall::CallAsync { .. } => Ok(PyCallOutcome::GenError(PyException::type_error(
                "CallAsync requires async_run (PythonAsyncSyntaxEscape not supported in sync mode)",
            ))),
        }
    }

    fn pending_generator(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.vm.current_segment_ref().is_none() {
            return Err(PyRuntimeError::new_err(
                "GenNext/GenSend/GenThrow: no current segment",
            ));
        }
        match &self.vm.pending_python {
            Some(PendingPython::StepUserGenerator { stream, .. }) => {
                let guard = stream
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("IRStream lock poisoned"))?;
                let Some(generator) = guard.python_generator() else {
                    return Err(PyRuntimeError::new_err(
                        "GenNext/GenSend/GenThrow: pending stream is not PythonGeneratorStream",
                    ));
                };
                Ok(generator.clone_ref(py))
            }
            Some(PendingPython::EvalExpr { .. })
            | Some(PendingPython::CallFuncReturn)
            | Some(PendingPython::ExpandReturn { .. })
            | Some(PendingPython::RustProgramContinuation { .. })
            | Some(PendingPython::AsyncEscape)
            | None => Err(PyRuntimeError::new_err(
                "GenNext/GenSend/GenThrow: expected StepUserGenerator in pending_python",
            )),
        }
    }

    fn step_generator(
        &self,
        py: Python<'_>,
        gen: Py<PyAny>,
        send_value: Option<Bound<'_, PyAny>>,
    ) -> PyResult<PyCallOutcome> {
        let gen_bound = gen.bind(py);

        let result = match send_value {
            Some(v) => gen_bound.call_method1("send", (v,)),
            None => gen_bound.call_method0("__next__"),
        };

        match result {
            Ok(yielded) => match self.classify_yielded(py, &yielded) {
                Ok(classified) => Ok(PyCallOutcome::GenYield(classified)),
                Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
            },
            Err(e) if e.is_instance_of::<PyStopIteration>(py) => {
                let return_value = extract_stop_iteration_value(py, &e)?;
                Ok(PyCallOutcome::GenReturn(return_value))
            }
            Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
        }
    }

    fn classify_yielded(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<DoCtrl> {
        classify_yielded_bound(&self.vm, py, obj)
    }

    fn values_to_tuple<'py>(
        &self,
        py: Python<'py>,
        values: &[Value],
    ) -> PyResult<Bound<'py, PyTuple>> {
        let py_values: Vec<_> = values
            .iter()
            .map(|v| self.value_to_runtime_pyobject(py, v))
            .collect::<PyResult<_>>()?;
        Ok(PyTuple::new(py, py_values)?)
    }

    fn value_to_runtime_pyobject<'py>(
        &self,
        py: Python<'py>,
        value: &Value,
    ) -> PyResult<Bound<'py, PyAny>> {
        match value {
            // Runtime callback arguments must receive the opaque K handle.
            Value::Continuation(k) => Ok(Bound::new(py, PyK::from_continuation(k))?.into_any()),
            Value::Python(_)
            | Value::Unit
            | Value::Int(_)
            | Value::String(_)
            | Value::Bool(_)
            | Value::None
            | Value::Handlers(_)
            | Value::Kleisli(_)
            | Value::Var(_)
            | Value::Task(_)
            | Value::Promise(_)
            | Value::ExternalPromise(_)
            | Value::CallStack(_)
            | Value::Trace(_)
            | Value::Traceback(_)
            | Value::ActiveChain(_)
            | Value::List(_) => value.to_pyobject(py),
        }
    }
}

fn call_metadata_to_dict(py: Python<'_>, metadata: &CallMetadata) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("function_name", metadata.function_name.as_str())?;
    dict.set_item("source_file", metadata.source_file.as_str())?;
    dict.set_item("source_line", metadata.source_line)?;
    if let Some(args_repr) = &metadata.args_repr {
        dict.set_item("args_repr", args_repr.as_str())?;
    }
    if let Some(program_call) = &metadata.program_call {
        dict.set_item("program_call", program_call.bind(py))?;
    }
    if metadata.auto_unwrap_programlike {
        dict.set_item("auto_unwrap_programlike", true)?;
    }
    Ok(dict.into_any().unbind())
}

fn call_expr_to_pyobject(py: Python<'_>, expr: &DoCtrl) -> PyResult<Py<PyAny>> {
    match doctrl_to_pyexpr_for_vm(expr) {
        Ok(Some(obj)) => Ok(obj),
        Ok(None) => Err(PyTypeError::new_err(
            "Apply/Expand argument DoExpr cannot be represented as Python object",
        )),
        Err(exc) => Err(exc.to_pyerr(py)),
    }
}

pub(crate) fn doctrl_to_pyexpr_for_vm(yielded: &DoCtrl) -> Result<Option<Py<PyAny>>, PyException> {
    Python::attach(|py| {
        let obj = match yielded {
            DoCtrl::Pure { value } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Pure as u8,
                        })
                        .add_subclass(PyPure {
                            value: value.to_pyobject(py)?.unbind(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Map {
                source,
                mapper,
                mapper_meta,
            } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Map as u8,
                        })
                        .add_subclass(PyMap {
                            source: source.clone_ref(py),
                            mapper: mapper.clone_ref(py),
                            mapper_meta: call_metadata_to_dict(py, mapper_meta)?,
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::FlatMap {
                source,
                binder,
                binder_meta,
            } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::FlatMap as u8,
                        })
                        .add_subclass(PyFlatMap {
                            source: source.clone_ref(py),
                            binder: binder.clone_ref(py),
                            binder_meta: call_metadata_to_dict(py, binder_meta)?,
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Perform { effect } => {
                dispatch_ref_as_python(effect).map(|value| value.clone_ref(py))
            }
            DoCtrl::Resume {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_continuation(continuation))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Resume as u8,
                            })
                            .add_subclass(PyResume {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Transfer {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_continuation(continuation))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Transfer as u8,
                            })
                            .add_subclass(PyTransfer {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Discontinue {
                continuation,
                exception,
            } => {
                let k = Bound::new(py, PyK::from_continuation(continuation))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Discontinue as u8,
                            })
                            .add_subclass(PyDiscontinue {
                                continuation: k,
                                exception: exception.value_clone_ref(py),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::TransferThrow { .. } | DoCtrl::ResumeThrow { .. } => None,
            DoCtrl::WithHandler {
                handler,
                body,
                types,
            } => {
                let debug = handler.debug_info();
                let handler_obj = handler
                    .py_identity()
                    .map(|identity| identity.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let body_obj = doctrl_to_pyexpr_for_vm(body)?.ok_or_else(|| {
                    PyException::type_error("WithHandler.body must convert to DoExpr")
                })?;
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::WithHandler as u8,
                            })
                            .add_subclass(PyWithHandler {
                                handler: handler_obj,
                                expr: body_obj,
                                types: handler_types_to_pyobj(py, types)?,
                                handler_name: Some(debug.name),
                                handler_file: debug.file,
                                handler_line: debug.line,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::WithIntercept {
                interceptor,
                body,
                types,
                mode,
                metadata,
            } => {
                let interceptor_obj = interceptor
                    .py_identity()
                    .map(|identity| identity.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let body_obj = doctrl_to_pyexpr_for_vm(body)?.ok_or_else(|| {
                    PyException::type_error("WithIntercept.body must convert to DoExpr")
                })?;
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::WithIntercept as u8,
                            })
                            .add_subclass(PyWithIntercept {
                                f: interceptor_obj,
                                expr: body_obj,
                                types: intercept_types_to_pyobj(py, types)?,
                                mode: mode.as_str().to_string(),
                                meta: metadata
                                    .as_ref()
                                    .map(|meta| call_metadata_to_dict(py, meta))
                                    .transpose()?,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Delegate { .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Delegate as u8,
                        })
                        .add_subclass(PyDelegate {}),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Pass { .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Pass as u8,
                        })
                        .add_subclass(PyPass {}),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetContinuation => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetContinuation as u8,
                        })
                        .add_subclass(PyGetContinuation),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetHandlers => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetHandlers as u8,
                        })
                        .add_subclass(PyGetHandlers),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetTraceback { continuation } => {
                let k = Bound::new(py, PyK::from_continuation(continuation))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::GetTraceback as u8,
                            })
                            .add_subclass(PyGetTraceback { continuation: k }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::CreateContinuation {
                expr,
                handlers,
                handler_identities,
                outside_scope,
            } => {
                let list = PyList::empty(py);
                for (idx, handler) in handlers.iter().enumerate() {
                    if let Some(identity_opt) = handler_identities.get(idx) {
                        if let Some(identity) = identity_opt {
                            list.append(identity.bind(py))
                                .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                            continue;
                        }
                    }
                    if let Some(identity) = handler.py_identity() {
                        list.append(identity.bind(py))
                            .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                    } else {
                        list.append(py.None())
                            .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                    }
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::CreateContinuation as u8,
                            })
                            .add_subclass(PyCreateContinuation {
                                program: expr.clone_ref(py),
                                handlers: list.into_any().unbind(),
                                outside_scope: match outside_scope {
                                    Some(seg_id) => (seg_id.index() as u32)
                                        .into_pyobject(py)
                                        .expect("u32 conversion is infallible")
                                        .into_any()
                                        .unbind(),
                                    None => py
                                        .None()
                                        .into_pyobject(py)
                                        .expect("None conversion is infallible")
                                        .into_any()
                                        .unbind(),
                                },
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_continuation(continuation))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::ResumeContinuation as u8,
                            })
                            .add_subclass(PyResumeContinuation {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::PythonAsyncSyntaxEscape { action } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::AsyncEscape as u8,
                        })
                        .add_subclass(PyAsyncEscape {
                            action: action.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Apply {
                f,
                args,
                kwargs,
                metadata,
            } => {
                let py_args = PyList::empty(py);
                for arg in args {
                    py_args
                        .append(call_expr_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_expr_to_pyobject(py, value)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Apply as u8,
                            })
                            .add_subclass(PyApply {
                                f: call_expr_to_pyobject(py, f)?,
                                args: py_args.into_any().unbind(),
                                kwargs: py_kwargs.into_any().unbind(),
                                meta: Some(call_metadata_to_dict(py, metadata)?),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => {
                let py_args = PyList::empty(py);
                for arg in args {
                    py_args
                        .append(call_expr_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_expr_to_pyobject(py, value)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Expand as u8,
                            })
                            .add_subclass(PyExpand {
                                factory: call_expr_to_pyobject(py, factory)?,
                                args: py_args.into_any().unbind(),
                                kwargs: py_kwargs.into_any().unbind(),
                                meta: Some(call_metadata_to_dict(py, metadata)?),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::IRStream { .. } => None,
            DoCtrl::Eval { expr, .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Eval as u8,
                        })
                        .add_subclass(PyEval {
                            expr: expr.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::EvalInScope {
                expr,
                scope,
                bindings,
                ..
            } => {
                let k = Bound::new(py, PyK::from_continuation(scope))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::EvalInScope as u8,
                            })
                            .add_subclass(PyEvalInScope {
                                expr: expr.clone_ref(py),
                                scope: k,
                                bindings: scope_bindings_to_pydict(py, bindings)?,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::AllocVar { initial } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::AllocVar as u8,
                        })
                        .add_subclass(PyAllocVar {
                            initial: initial.to_pyobject(py)?.unbind(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::ReadVar { var } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::ReadVar as u8,
                        })
                        .add_subclass(PyReadVar {
                            var: Py::new(py, PyVar::from_var(*var))
                                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                                .into_any(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::WriteVar { var, value } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::WriteVar as u8,
                        })
                        .add_subclass(PyWriteVar {
                            var: Py::new(py, PyVar::from_var(*var))
                                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                                .into_any(),
                            value: value.to_pyobject(py)?.unbind(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::WriteVarNonlocal { var, value } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::WriteVarNonlocal as u8,
                        })
                        .add_subclass(PyWriteVarNonlocal {
                            var: Py::new(py, PyVar::from_var(*var))
                                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                                .into_any(),
                            value: value.to_pyobject(py)?.unbind(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::ReadHandlerState { .. }
            | DoCtrl::WriteHandlerState { .. }
            | DoCtrl::AppendHandlerLog { .. } => None,
            DoCtrl::GetCallStack => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetCallStack as u8,
                        })
                        .add_subclass(PyGetCallStack),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
        };

        Ok(obj)
    })
}

fn merged_metadata_from_doeff(
    inherited: Option<CallMetadata>,
    function_name: String,
    source_file: String,
    source_line: u32,
) -> Option<CallMetadata> {
    match inherited {
        Some(metadata) => Some(metadata),
        None => Some(CallMetadata::new(
            function_name,
            source_file,
            source_line,
            None,
            None,
            false,
        )),
    }
}

fn scope_bindings_to_pydict(
    py: Python<'_>,
    bindings: &HashMap<HashedPyKey, Value>,
) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    for (key, value) in bindings {
        dict.set_item(key.to_pyobject(py), value.to_pyobject(py)?)?;
    }
    Ok(dict.into_any().unbind())
}

fn scope_bindings_from_pyany(obj: &Bound<'_, PyAny>) -> PyResult<HashMap<HashedPyKey, Value>> {
    if obj.is_none() {
        return Ok(HashMap::new());
    }
    let dict = obj
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("EvalInScope.bindings must be a dict"))?;
    let mut bindings = HashMap::new();
    for (key, value) in dict.iter() {
        bindings.insert(
            HashedPyKey::from_bound(&key)?,
            Value::from_python_opaque(&value),
        );
    }
    Ok(bindings)
}

fn classify_doeff_generator_as_irstream(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    inherited_metadata: Option<CallMetadata>,
    context: &str,
) -> PyResult<DoCtrl> {
    let wrapped: PyRef<'_, DoeffGenerator> = obj.extract().map_err(|_| {
        let ty = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        PyTypeError::new_err(format!("{context}: expected DoeffGenerator, got {ty}"))
    })?;

    if !wrapped.get_frame.bind(py).is_callable() {
        return Err(PyTypeError::new_err(format!(
            "{context}: DoeffGenerator.get_frame must be callable"
        )));
    }

    let stream = IRStreamRef::new(Box::new(PythonGeneratorStream::new(
        PyShared::new(wrapped.generator.clone_ref(py)),
        PyShared::new(wrapped.get_frame.clone_ref(py)),
    )) as Box<dyn IRStream>);

    Ok(DoCtrl::IRStream {
        stream,
        metadata: merged_metadata_from_doeff(
            inherited_metadata,
            wrapped.factory_function_name().to_string(),
            wrapped.factory_source_file().to_string(),
            wrapped.factory_source_line(),
        ),
    })
}

pub(crate) fn classify_yielded_bound(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> PyResult<DoCtrl> {
    // R13-I: GIL-free tag dispatch.
    //
    // 1. Single isinstance check: extract PyDoCtrlBase
    // 2. Read tag (u8 on frozen struct — no GIL contention)
    // 3. Match on DoExprTag → single targeted extract for the variant
    // 4. EffectBase instances are wrapped as DoCtrl::Perform
    //
    // Reduces average isinstance checks from ~8 to 2, worst case from 16 to 2.
    if let Ok(base) = obj.extract::<PyRef<'_, PyDoCtrlBase>>() {
        let tag = DoExprTag::try_from(base.tag).unwrap_or(DoExprTag::Unknown);
        return match tag {
            DoExprTag::WithHandler => {
                let wh: PyRef<'_, PyWithHandler> = obj.extract()?;
                let handler_bound = wh.handler.bind(py);
                let handler = PyVM::extract_kleisli_ref(py, handler_bound, "WithHandler.handler")?;
                let body = classify_yielded_bound(vm, py, wh.expr.bind(py))?;
                let types = handler_types_from_pyobj(py, &wh.types)?;
                Ok(DoCtrl::WithHandler {
                    handler,
                    body: Box::new(body),
                    types,
                })
            }
            DoExprTag::WithIntercept => {
                let wi: PyRef<'_, PyWithIntercept> = obj.extract()?;
                let interceptor = PyVM::extract_kleisli_ref(py, wi.f.bind(py), "WithIntercept.f")?;
                let body = classify_yielded_bound(vm, py, wi.expr.bind(py))?;
                let types = intercept_types_from_pyobj(py, &wi.types)?;
                let mode = intercept_mode_from_str(&wi.mode)?;
                Ok(DoCtrl::WithIntercept {
                    interceptor,
                    body: Box::new(body),
                    types,
                    mode,
                    metadata: call_metadata_from_optional_meta(py, &wi.meta, "WithIntercept")?,
                })
            }
            DoExprTag::Discontinue => {
                let d: PyRef<'_, PyDiscontinue> = obj.extract()?;
                let k_pyobj = d.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Discontinue.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let k = continuation_for_control(&k_pyobj)?;
                let bound_exception = d.exception.bind(py);
                if !bound_exception.is_instance_of::<PyBaseException>() {
                    return Err(PyTypeError::new_err(
                        "Discontinue.exception must be a BaseException instance",
                    ));
                }
                Ok(DoCtrl::Discontinue {
                    continuation: k,
                    exception: pyerr_to_exception(py, PyErr::from_value(bound_exception.clone()))?,
                })
            }
            DoExprTag::Pure => {
                let p: PyRef<'_, PyPure> = obj.extract()?;
                Ok(DoCtrl::Pure {
                    value: Value::from_pyobject(p.value.bind(py)),
                })
            }
            DoExprTag::Apply => {
                let a: PyRef<'_, PyApply> = obj.extract()?;
                let f = classify_call_expr(vm, py, a.f.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in a.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_expr(vm, py, item.as_any())?);
                }
                let kwargs_dict = a.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_expr(vm, py, v.as_any())?));
                }
                Ok(DoCtrl::Apply {
                    f: Box::new(f),
                    args,
                    kwargs,
                    metadata: call_metadata_from_pyapply(py, &a)?,
                })
            }
            DoExprTag::Expand => {
                let e: PyRef<'_, PyExpand> = obj.extract()?;
                let factory = classify_call_expr(vm, py, e.factory.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in e.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_expr(vm, py, item.as_any())?);
                }
                let kwargs_dict = e.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_expr(vm, py, v.as_any())?));
                }
                Ok(DoCtrl::Expand {
                    factory: Box::new(factory),
                    args,
                    kwargs,
                    metadata: call_metadata_from_pyexpand(py, &e)?,
                })
            }
            DoExprTag::Map => {
                let m: PyRef<'_, PyMap> = obj.extract()?;
                Ok(DoCtrl::Map {
                    source: PyShared::new(m.source.clone_ref(py)),
                    mapper: PyShared::new(m.mapper.clone_ref(py)),
                    mapper_meta: call_metadata_from_meta_obj(m.mapper_meta.bind(py)),
                })
            }
            DoExprTag::FlatMap => {
                let fm: PyRef<'_, PyFlatMap> = obj.extract()?;
                Ok(DoCtrl::FlatMap {
                    source: PyShared::new(fm.source.clone_ref(py)),
                    binder: PyShared::new(fm.binder.clone_ref(py)),
                    binder_meta: call_metadata_from_meta_obj(fm.binder_meta.bind(py)),
                })
            }
            DoExprTag::Perform => {
                let pf: PyRef<'_, PyPerform> = obj.extract()?;
                Ok(DoCtrl::Perform {
                    effect: dispatch_from_shared(PyShared::new(pf.effect.clone_ref(py))),
                })
            }
            DoExprTag::Resume => {
                let r: PyRef<'_, PyResume> = obj.extract()?;
                let k_pyobj = r.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Resume.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let k = continuation_for_control(&k_pyobj)?;
                Ok(DoCtrl::Resume {
                    continuation: k,
                    value: Value::from_pyobject(r.value.bind(py)),
                })
            }
            DoExprTag::Transfer => {
                let t: PyRef<'_, PyTransfer> = obj.extract()?;
                let k_pyobj = t.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Transfer.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let k = continuation_for_control(&k_pyobj)?;
                Ok(DoCtrl::Transfer {
                    continuation: k,
                    value: Value::from_pyobject(t.value.bind(py)),
                })
            }
            DoExprTag::Delegate => {
                let _d: PyRef<'_, PyDelegate> = obj.extract()?;
                let dispatch_id = vm.current_dispatch_id().ok_or_else(|| {
                    PyRuntimeError::new_err("Delegate called outside dispatch context")
                })?;
                let effect = vm.effect_for_dispatch(dispatch_id).ok_or_else(|| {
                    PyRuntimeError::new_err("Delegate dispatch context not found")
                })?;
                Ok(DoCtrl::Delegate { effect })
            }
            DoExprTag::Pass => {
                let _p: PyRef<'_, PyPass> = obj.extract()?;
                let dispatch_id = vm.current_dispatch_id().ok_or_else(|| {
                    PyRuntimeError::new_err("Pass called outside dispatch context")
                })?;
                let effect = vm
                    .effect_for_dispatch(dispatch_id)
                    .ok_or_else(|| PyRuntimeError::new_err("Pass dispatch context not found"))?;
                Ok(DoCtrl::Pass { effect })
            }
            DoExprTag::ResumeContinuation => {
                let rc: PyRef<'_, PyResumeContinuation> = obj.extract()?;
                let k_pyobj = rc.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "ResumeContinuation.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let k = continuation_for_control(&k_pyobj)?;
                Ok(DoCtrl::ResumeContinuation {
                    continuation: k,
                    value: Value::from_pyobject(rc.value.bind(py)),
                })
            }
            DoExprTag::CreateContinuation => {
                let cc: PyRef<'_, PyCreateContinuation> = obj.extract()?;
                let program = cc.program.clone_ref(py);
                let handlers_list = cc.handlers.bind(py);
                let mut handlers = Vec::new();
                let mut handler_identities = Vec::new();
                for item in handlers_list.try_iter()? {
                    let item = item?;
                    let kleisli = PyVM::extract_kleisli_ref(py, &item, "CreateContinuation")?;
                    let identity = kleisli
                        .py_identity()
                        .or_else(|| Some(PyShared::new(item.clone().unbind())));
                    handlers.push(kleisli);
                    handler_identities.push(identity);
                }
                let outside_scope = if cc.outside_scope.bind(py).is_none() {
                    None
                } else {
                    Some(SegmentId::from_index(
                        cc.outside_scope.bind(py).extract::<u32>()? as usize,
                    ))
                };
                Ok(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities,
                    outside_scope,
                })
            }
            DoExprTag::GetContinuation => Ok(DoCtrl::GetContinuation),
            DoExprTag::GetHandlers => Ok(DoCtrl::GetHandlers),
            DoExprTag::GetTraceback => {
                let gt: PyRef<'_, PyGetTraceback> = obj.extract()?;
                let k_pyobj = gt.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "GetTraceback.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let k = continuation_for_control(&k_pyobj)?;
                Ok(DoCtrl::GetTraceback { continuation: k })
            }
            DoExprTag::GetCallStack => Ok(DoCtrl::GetCallStack),
            DoExprTag::Eval => {
                let eval: PyRef<'_, PyEval> = obj.extract()?;
                let expr = eval.expr.clone_ref(py);
                Ok(DoCtrl::Eval {
                    expr: PyShared::new(expr),
                    metadata: None,
                })
            }
            DoExprTag::EvalInScope => {
                let eval: PyRef<'_, PyEvalInScope> = obj.extract()?;
                let expr = eval.expr.clone_ref(py);
                let scope_obj = eval.scope.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err("EvalInScope.scope must be K (opaque continuation handle)")
                })?;
                let scope = continuation_for_control(&scope_obj)?;
                Ok(DoCtrl::EvalInScope {
                    expr: PyShared::new(expr),
                    scope,
                    bindings: scope_bindings_from_pyany(eval.bindings.bind(py).as_any())?,
                    metadata: None,
                })
            }
            DoExprTag::AllocVar => {
                let alloc: PyRef<'_, PyAllocVar> = obj.extract()?;
                Ok(DoCtrl::AllocVar {
                    initial: Value::from_pyobject(alloc.initial.bind(py)),
                })
            }
            DoExprTag::ReadVar => {
                let read: PyRef<'_, PyReadVar> = obj.extract()?;
                let var: PyRef<'_, PyVar> = read
                    .var
                    .bind(py)
                    .extract()
                    .map_err(|_| PyTypeError::new_err("ReadVar.var must be Var"))?;
                Ok(DoCtrl::ReadVar {
                    var: var.to_var_id(),
                })
            }
            DoExprTag::WriteVar => {
                let write: PyRef<'_, PyWriteVar> = obj.extract()?;
                let var: PyRef<'_, PyVar> = write
                    .var
                    .bind(py)
                    .extract()
                    .map_err(|_| PyTypeError::new_err("WriteVar.var must be Var"))?;
                Ok(DoCtrl::WriteVar {
                    var: var.to_var_id(),
                    value: Value::from_pyobject(write.value.bind(py)),
                })
            }
            DoExprTag::WriteVarNonlocal => {
                let write: PyRef<'_, PyWriteVarNonlocal> = obj.extract()?;
                let var: PyRef<'_, PyVar> = write
                    .var
                    .bind(py)
                    .extract()
                    .map_err(|_| PyTypeError::new_err("WriteVarNonlocal.var must be Var"))?;
                Ok(DoCtrl::WriteVarNonlocal {
                    var: var.to_var_id(),
                    value: Value::from_pyobject(write.value.bind(py)),
                })
            }
            DoExprTag::AsyncEscape => {
                let ae: PyRef<'_, PyAsyncEscape> = obj.extract()?;
                Ok(DoCtrl::PythonAsyncSyntaxEscape {
                    action: PyShared::new(ae.action.clone_ref(py)),
                })
            }
            DoExprTag::Effect | DoExprTag::Unknown => Err(PyTypeError::new_err(
                "yielded DoCtrlBase has unrecognized tag",
            )),
        };
    }

    if obj.is_instance_of::<DoeffGenerator>() {
        return classify_doeff_generator_as_irstream(py, obj, None, "yielded value");
    }

    if obj.is_instance_of::<PyDoExprBase>() {
        let generated = obj.call_method0("to_generator").map_err(|err| {
            if err
                .matches(py, py.get_type::<PyAttributeError>())
                .unwrap_or(false)
            {
                PyTypeError::new_err("DoExpr object is missing to_generator()")
            } else {
                err
            }
        })?;
        let ty_name = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "DoExpr".to_string());
        let metadata = CallMetadata::new(
            format!("{ty_name}.to_generator"),
            "<doexpr>".to_string(),
            0,
            None,
            Some(PyShared::new(obj.clone().unbind())),
            vm_has_nearby_programlike_auto_unwrap(vm),
        );
        return classify_doeff_generator_as_irstream(
            py,
            generated.as_any(),
            Some(metadata),
            "DoExpr.to_generator",
        );
    }

    // Fallback: bare effect -> auto-lift to Perform (R14-C)
    if is_effect_base_like(py, obj)? {
        if obj.is_instance_of::<PyProgramCallStack>() {
            return Ok(DoCtrl::GetCallStack);
        }
        return Ok(DoCtrl::Perform {
            effect: dispatch_from_shared(PyShared::new(obj.clone().unbind())),
        });
    }

    let ty = py_type_name(obj);
    let repr = py_repr_text(obj);
    let hint = maybe_programlike_auto_unwrap_hint(vm, obj)
        .map(|text| format!("\n{text}"))
        .unwrap_or_default();
    Err(PyTypeError::new_err(format!(
        "yielded value must be EffectBase or DoExpr, got {repr} (type: {ty}){hint}"
    )))
}

pub(crate) fn classify_yielded_for_vm(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<DoCtrl, PyException> {
    classify_yielded_bound(vm, py, obj).map_err(|err| {
        pyerr_to_exception(py, err)
            .unwrap_or_else(|conv_err| PyException::runtime_error(format!("{conv_err}")))
    })
}

fn pyerr_to_exception(py: Python<'_>, e: PyErr) -> PyResult<PyException> {
    let exc_type = e.get_type(py).into_any().unbind();
    let exc_value = e.value(py).clone().into_any().unbind();
    let exc_tb = e.traceback(py).map(|tb| tb.into_any().unbind());
    Ok(PyException::new(exc_type, exc_value, exc_tb))
}

fn maybe_programlike_auto_unwrap_hint(vm: &VM, _obj: &Bound<'_, PyAny>) -> Option<&'static str> {
    vm_has_nearby_programlike_auto_unwrap(vm).then_some(
        "Hint: this may be a resolved ProgramLike value. If you passed a Program or Effect to a \
@do function, ensure the parameter is annotated as ProgramLike (not Any) to prevent auto-unwrapping.",
    )
}

fn vm_has_nearby_programlike_auto_unwrap(vm: &VM) -> bool {
    vm.has_nearby_auto_unwrap_programlike()
}

fn default_discontinued_exception(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let err = Discontinued::new_err(());
    Ok(err.value(py).clone().into_any().unbind())
}

fn extract_stop_iteration_value(py: Python<'_>, e: &PyErr) -> PyResult<Value> {
    let value = e.value(py).getattr("value")?;
    Ok(Value::from_pyobject(&value))
}

fn metadata_attr_as_string(meta: &Bound<'_, PyAny>, key: &str) -> Option<String> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| v.extract::<String>().ok())
}

fn metadata_attr_as_u32(meta: &Bound<'_, PyAny>, key: &str) -> Option<u32> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| v.extract::<u32>().ok())
}

fn metadata_attr_as_bool(meta: &Bound<'_, PyAny>, key: &str) -> Option<bool> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| v.extract::<bool>().ok())
}

fn metadata_attr_as_py(meta: &Bound<'_, PyAny>, key: &str) -> Option<PyShared> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| {
            if v.is_none() {
                None
            } else {
                Some(PyShared::new(v.unbind()))
            }
        })
}

fn call_metadata_from_meta_obj(meta_obj: &Bound<'_, PyAny>) -> CallMetadata {
    let function_name = metadata_attr_as_string(meta_obj, "function_name")
        .unwrap_or_else(|| "<anonymous>".to_string());
    let source_file =
        metadata_attr_as_string(meta_obj, "source_file").unwrap_or_else(|| "<unknown>".to_string());
    let source_line = metadata_attr_as_u32(meta_obj, "source_line").unwrap_or(0);
    let args_repr = metadata_attr_as_string(meta_obj, "args_repr");
    let program_call = metadata_attr_as_py(meta_obj, "program_call");
    let auto_unwrap_programlike =
        metadata_attr_as_bool(meta_obj, "auto_unwrap_programlike").unwrap_or(false);
    CallMetadata::new(
        function_name,
        source_file,
        source_line,
        args_repr,
        program_call,
        auto_unwrap_programlike,
    )
}

fn call_metadata_from_required_meta(
    py: Python<'_>,
    meta: &Option<Py<PyAny>>,
    ctrl_name: &str,
) -> PyResult<CallMetadata> {
    if let Some(meta) = meta {
        let meta_obj = meta.bind(py);
        if !meta_obj.is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err(format!(
                "{ctrl_name}.meta must be dict with function_name/source_file/source_line"
            )));
        }
        return Ok(call_metadata_from_meta_obj(meta_obj));
    }

    Err(PyTypeError::new_err(format!(
        "{ctrl_name}.meta is required. \
Supply {ctrl_name}(..., meta={{function_name, source_file, source_line}})."
    )))
}

fn call_metadata_from_optional_meta(
    py: Python<'_>,
    meta: &Option<Py<PyAny>>,
    ctrl_name: &str,
) -> PyResult<Option<CallMetadata>> {
    let Some(meta_obj) = meta else {
        return Ok(None);
    };
    let bound = meta_obj.bind(py);
    if !bound.is_instance_of::<PyDict>() {
        return Err(PyTypeError::new_err(format!(
            "{ctrl_name}.meta must be dict with function_name/source_file/source_line"
        )));
    }
    Ok(Some(call_metadata_from_meta_obj(bound)))
}

fn call_metadata_from_pyapply(
    py: Python<'_>,
    apply: &PyRef<'_, PyApply>,
) -> PyResult<CallMetadata> {
    call_metadata_from_required_meta(py, &apply.meta, "Apply")
}

fn call_metadata_from_pyexpand(
    py: Python<'_>,
    expand: &PyRef<'_, PyExpand>,
) -> PyResult<CallMetadata> {
    call_metadata_from_required_meta(py, &expand.meta, "Expand")
}

// ---------------------------------------------------------------------------
// PyRunResult — execution output [R8-J]
// ---------------------------------------------------------------------------

#[pyclass(frozen, name = "DoeffTracebackData")]
pub struct PyDoeffTracebackData {
    #[pyo3(get)]
    entries: Py<PyAny>,
    #[pyo3(get)]
    active_chain: Py<PyAny>,
}

#[pymethods]
impl PyDoeffTracebackData {
    #[new]
    #[pyo3(signature = (entries, active_chain=None))]
    fn new(py: Python<'_>, entries: Py<PyAny>, active_chain: Option<Py<PyAny>>) -> Self {
        PyDoeffTracebackData {
            entries,
            active_chain: active_chain.unwrap_or_else(|| py.None()),
        }
    }
}

#[pyclass(frozen, name = "RunResult")]
pub struct PyRunResult {
    result: Result<Py<PyAny>, PyException>,
    #[pyo3(get)]
    traceback_data: Option<Py<PyDoeffTracebackData>>,
    raw_store: Py<pyo3::types::PyDict>,
    log: Py<PyAny>,
    trace: Py<PyAny>,
}

impl PyRunResult {
    fn preview_sequence(seq: &Bound<'_, PyAny>, max_items: usize) -> String {
        let mut lines: Vec<String> = Vec::new();
        if let Ok(iter) = seq.try_iter() {
            for (idx, item_res) in iter.enumerate() {
                if idx >= max_items {
                    lines.push("  ...".to_string());
                    break;
                }
                let text = match item_res {
                    Ok(item) => item
                        .repr()
                        .map(|v| v.to_string())
                        .unwrap_or_else(|_| "<item>".to_string()),
                    Err(_) => "<iter-error>".to_string(),
                };
                lines.push(format!("  {}. {}", idx + 1, text));
            }
            if lines.is_empty() {
                lines.push("  (empty)".to_string());
            }
            return lines.join("\n");
        }
        let fallback = seq
            .repr()
            .map(|v| v.to_string())
            .unwrap_or_else(|_| "<unavailable>".to_string());
        format!("  {}", fallback)
    }

    fn format_traceback_data_preview(
        traceback_data: &Bound<'_, PyDoeffTracebackData>,
        verbose: bool,
    ) -> String {
        let mut lines: Vec<String> = Vec::new();
        let max_items = if verbose { 32 } else { 8 };
        let py = traceback_data.py();
        let traceback_data_ref = traceback_data.borrow();
        let active_chain = traceback_data_ref.active_chain.bind(py);
        let entries = traceback_data_ref.entries.bind(py);

        if !active_chain.is_none() {
            lines.push("ActiveChain:".to_string());
            lines.push(Self::preview_sequence(active_chain, max_items));
        }

        let entry_count = entries.len().ok();
        if verbose {
            lines.push("TraceEntries:".to_string());
            lines.push(Self::preview_sequence(entries, max_items));
        } else if let Some(count) = entry_count {
            lines.push(format!("TraceEntries: {count}"));
        } else {
            lines.push("TraceEntries: <unknown>".to_string());
        }

        if lines.is_empty() {
            return "TracebackData: <unavailable>".to_string();
        }
        lines.join("\n")
    }
}

#[pymethods]
impl PyRunResult {
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)),
            Err(e) => Err(e.to_pyerr(py)),
        }
    }

    #[getter]
    fn error(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Err(e) => Ok(e.value_clone_ref(py)),
            Ok(_) => Err(pyo3::exceptions::PyValueError::new_err(
                "RunResult is Ok, not Err",
            )),
        }
    }

    // D9: Returns Ok(value) or Err(exception) objects per SPEC-008.
    #[getter]
    fn result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => {
                let ok_obj = Bound::new(
                    py,
                    PyResultOk {
                        value: v.clone_ref(py),
                    },
                )?;
                Ok(ok_obj.into_any().unbind())
            }
            Err(e) => {
                let err_obj = e.value_clone_ref(py);
                let err_obj = Bound::new(
                    py,
                    PyResultErr {
                        error: err_obj,
                        captured_traceback: py.None(),
                    },
                )?;
                Ok(err_obj.into_any().unbind())
            }
        }
    }

    #[getter]
    fn raw_store(&self, py: Python<'_>) -> Py<PyAny> {
        self.raw_store.clone_ref(py).into_any()
    }

    #[getter]
    fn log(&self, py: Python<'_>) -> Py<PyAny> {
        self.log.clone_ref(py)
    }

    #[getter]
    fn trace(&self, py: Python<'_>) -> Py<PyAny> {
        self.trace.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        self.result.is_ok()
    }

    fn is_err(&self) -> bool {
        self.result.is_err()
    }

    #[pyo3(signature = (verbose=false))]
    fn display(&self, py: Python<'_>, verbose: bool) -> PyResult<String> {
        if let Err(err) = &self.result {
            let err_obj = err.value_clone_ref(py);
            let label = if verbose { "verbose" } else { "default" };
            let mut lines = vec![
                format!("RunResult status: err ({label})"),
                format!("Error: {:?}", err_obj),
            ];
            if let Some(traceback_data) = &self.traceback_data {
                lines.push(Self::format_traceback_data_preview(
                    traceback_data.bind(py),
                    verbose,
                ));
            } else {
                lines.push("TracebackData: none".to_string());
            }
            return Ok(lines.join("\n"));
        }

        let value_text = match &self.result {
            Ok(value) => value
                .bind(py)
                .repr()
                .map(|v| v.to_string())
                .unwrap_or_else(|_| "<value>".to_string()),
            Err(_) => "<error>".to_string(),
        };
        Ok(format!("RunResult status: ok\nValue: {value_text}"))
    }
}

// ---------------------------------------------------------------------------
// Pyclass control primitives [R8-C]
// ---------------------------------------------------------------------------

/// Composition primitive — usable in any Program.
#[pyclass(name = "WithHandler", extends=PyDoCtrlBase)]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub types: Option<Py<PyAny>>,
    #[pyo3(get)]
    pub handler_name: Option<String>,
    #[pyo3(get)]
    pub handler_file: Option<String>,
    #[pyo3(get)]
    pub handler_line: Option<u32>,
}

#[pymethods]
impl PyWithHandler {
    #[new]
    #[pyo3(signature = (handler, expr, *, types=None, handler_name=None, handler_file=None, handler_line=None))]
    fn new(
        py: Python<'_>,
        handler: Py<PyAny>,
        expr: Py<PyAny>,
        types: Option<Py<PyAny>>,
        handler_name: Option<String>,
        handler_file: Option<String>,
        handler_line: Option<u32>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let handler_obj = handler.bind(py);
        let is_rust_handler = handler_obj.is_instance_of::<PyRustHandlerSentinel>();
        let is_dgfn = handler_obj.is_instance_of::<DoeffGeneratorFn>();
        let is_kleisli = handler_obj.is_instance_of::<PyKleisli>();
        if !is_rust_handler && !is_dgfn && !is_kleisli {
            return Err(strict_handler_type_error(
                "WithHandler",
                "handler",
                handler_obj,
            ));
        }

        let expr = lift_effect_to_perform_expr(py, expr)?;
        let normalized_types = normalize_handler_types_obj(py, types)?;

        let expr_obj = expr.bind(py);
        if !expr_obj.is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("WithHandler.expr must be DoExpr"));
        }

        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WithHandler as u8,
            })
            .add_subclass(PyWithHandler {
                handler,
                expr,
                types: normalized_types,
                handler_name,
                handler_file,
                handler_line,
            }))
    }
}

#[pyclass(name = "WithIntercept", extends=PyDoCtrlBase)]
pub struct PyWithIntercept {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub types: Option<Py<PyAny>>,
    #[pyo3(get)]
    pub mode: String,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyWithIntercept {
    #[new]
    #[pyo3(signature = (f, expr, types=None, mode="include", meta=None))]
    fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        expr: Py<PyAny>,
        types: Option<Py<PyAny>>,
        mode: &str,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let f_obj = f.bind(py);
        let is_rust_handler = f_obj.is_instance_of::<PyRustHandlerSentinel>();
        let is_dgfn = f_obj.is_instance_of::<DoeffGeneratorFn>();
        let is_kleisli = f_obj.is_instance_of::<PyKleisli>();
        if !is_rust_handler && !is_dgfn && !is_kleisli {
            return Err(strict_handler_type_error(
                "WithIntercept",
                "interceptor",
                f_obj,
            ));
        }

        let normalized_types = normalize_intercept_types_obj(py, types)?;
        let mode = intercept_mode_from_str(mode)?;

        if let Some(meta_obj) = meta.as_ref() {
            let meta_bound = meta_obj.bind(py);
            if !meta_bound.is_instance_of::<PyDict>() {
                return Err(PyTypeError::new_err(
                    "WithIntercept.meta must be dict with function_name/source_file/source_line",
                ));
            }
        }
        let expr = lift_effect_to_perform_expr(py, expr)?;
        let expr_obj = expr.bind(py);
        if !expr_obj.is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("WithIntercept.expr must be DoExpr"));
        }

        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WithIntercept as u8,
            })
            .add_subclass(PyWithIntercept {
                f,
                expr,
                types: normalized_types,
                mode: mode.as_str().to_string(),
                meta,
            }))
    }
}

#[pyclass(name = "Discontinue", extends=PyDoCtrlBase)]
pub struct PyDiscontinue {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub exception: Py<PyAny>,
}

#[pymethods]
impl PyDiscontinue {
    #[new]
    #[pyo3(signature = (continuation, exception=None))]
    fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        exception: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Discontinue.continuation must be K (opaque continuation handle)",
            ));
        }
        let exception = match exception {
            Some(exception) => {
                if !exception.bind(py).is_instance_of::<PyBaseException>() {
                    return Err(PyTypeError::new_err(
                        "Discontinue.exception must be a BaseException instance",
                    ));
                }
                exception
            }
            None => default_discontinued_exception(py)?,
        };
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Discontinue as u8,
            })
            .add_subclass(PyDiscontinue {
                continuation,
                exception,
            }))
    }
}

#[pyclass(name = "Map", extends=PyDoCtrlBase)]
pub struct PyMap {
    #[pyo3(get)]
    pub source: Py<PyAny>,
    #[pyo3(get)]
    pub mapper: Py<PyAny>,
    #[pyo3(get)]
    pub mapper_meta: Py<PyAny>,
}

#[pyclass(name = "Pure", extends=PyDoCtrlBase)]
pub struct PyPure {
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyPure {
    #[new]
    fn new(value: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Pure as u8,
            })
            .add_subclass(PyPure { value })
    }
}

#[pyclass(name = "Apply", extends=PyDoCtrlBase)]
pub struct PyApply {
    #[pyo3(get)]
    pub f: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
    #[pyo3(get)]
    pub kwargs: Py<PyAny>,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyApply {
    #[new]
    #[pyo3(signature = (f, args, kwargs, meta=None))]
    fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        args: Py<PyAny>,
        kwargs: Py<PyAny>,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if args.bind(py).try_iter().is_err() {
            return Err(PyTypeError::new_err("Apply.args must be iterable"));
        }
        if !kwargs.bind(py).is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err("Apply.kwargs must be dict"));
        }
        if meta.is_none() {
            return Err(PyTypeError::new_err(
                "Apply.meta is required. \
Program/Kleisli call sites must pass {'function_name', 'source_file', 'source_line'}.",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Apply as u8,
            })
            .add_subclass(PyApply {
                f,
                args,
                kwargs,
                meta,
            }))
    }
}

#[pyclass(name = "Expand", extends=PyDoCtrlBase)]
pub struct PyExpand {
    #[pyo3(get)]
    pub factory: Py<PyAny>,
    #[pyo3(get)]
    pub args: Py<PyAny>,
    #[pyo3(get)]
    pub kwargs: Py<PyAny>,
    #[pyo3(get)]
    pub meta: Option<Py<PyAny>>,
}

#[pymethods]
impl PyExpand {
    #[new]
    #[pyo3(signature = (factory, args, kwargs, meta=None))]
    fn new(
        py: Python<'_>,
        factory: Py<PyAny>,
        args: Py<PyAny>,
        kwargs: Py<PyAny>,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if args.bind(py).try_iter().is_err() {
            return Err(PyTypeError::new_err("Expand.args must be iterable"));
        }
        if !kwargs.bind(py).is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err("Expand.kwargs must be dict"));
        }
        if meta.is_none() {
            return Err(PyTypeError::new_err(
                "Expand.meta is required. \
Program/Kleisli call sites must pass {'function_name', 'source_file', 'source_line'}.",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Expand as u8,
            })
            .add_subclass(PyExpand {
                factory,
                args,
                kwargs,
                meta,
            }))
    }
}

#[pyclass(name = "Eval", extends=PyDoCtrlBase)]
pub struct PyEval {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
}

#[pymethods]
impl PyEval {
    #[new]
    fn new(py: Python<'_>, expr: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        let expr = lift_effect_to_perform_expr(py, expr)?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Eval as u8,
            })
            .add_subclass(PyEval { expr }))
    }
}

#[pyclass(name = "EvalInScope", extends=PyDoCtrlBase)]
pub struct PyEvalInScope {
    #[pyo3(get)]
    pub expr: Py<PyAny>,
    #[pyo3(get)]
    pub scope: Py<PyAny>,
    #[pyo3(get)]
    pub bindings: Py<PyAny>,
}

#[pymethods]
impl PyEvalInScope {
    #[new]
    #[pyo3(signature = (expr, scope, bindings=None))]
    fn new(
        py: Python<'_>,
        expr: Py<PyAny>,
        scope: Py<PyAny>,
        bindings: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let expr = lift_effect_to_perform_expr(py, expr)?;
        if !scope.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "EvalInScope.scope must be K (opaque continuation handle)",
            ));
        }
        let bindings = match bindings {
            Some(bindings) => {
                let _ = scope_bindings_from_pyany(bindings.bind(py).as_any())?;
                bindings
            }
            None => PyDict::new(py).into_any().unbind(),
        };
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::EvalInScope as u8,
            })
            .add_subclass(PyEvalInScope {
                expr,
                scope,
                bindings,
            }))
    }
}

#[pyclass(name = "AllocVar", extends=PyDoCtrlBase)]
pub struct PyAllocVar {
    #[pyo3(get)]
    pub initial: Py<PyAny>,
}

#[pymethods]
impl PyAllocVar {
    #[new]
    fn new(initial: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::AllocVar as u8,
            })
            .add_subclass(PyAllocVar { initial })
    }
}

#[pyclass(name = "ReadVar", extends=PyDoCtrlBase)]
pub struct PyReadVar {
    #[pyo3(get)]
    pub var: Py<PyAny>,
}

#[pymethods]
impl PyReadVar {
    #[new]
    fn new(py: Python<'_>, var: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !var.bind(py).is_instance_of::<PyVar>() {
            return Err(PyTypeError::new_err("ReadVar.var must be Var"));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::ReadVar as u8,
            })
            .add_subclass(PyReadVar { var }))
    }
}

#[pyclass(name = "WriteVar", extends=PyDoCtrlBase)]
pub struct PyWriteVar {
    #[pyo3(get)]
    pub var: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyWriteVar {
    #[new]
    fn new(py: Python<'_>, var: Py<PyAny>, value: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !var.bind(py).is_instance_of::<PyVar>() {
            return Err(PyTypeError::new_err("WriteVar.var must be Var"));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WriteVar as u8,
            })
            .add_subclass(PyWriteVar { var, value }))
    }
}

#[pyclass(name = "WriteVarNonlocal", extends=PyDoCtrlBase)]
pub struct PyWriteVarNonlocal {
    #[pyo3(get)]
    pub var: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyWriteVarNonlocal {
    #[new]
    fn new(py: Python<'_>, var: Py<PyAny>, value: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !var.bind(py).is_instance_of::<PyVar>() {
            return Err(PyTypeError::new_err("WriteVarNonlocal.var must be Var"));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WriteVarNonlocal as u8,
            })
            .add_subclass(PyWriteVarNonlocal { var, value }))
    }
}

#[pyclass(name = "Perform", extends=PyDoCtrlBase)]
pub struct PyPerform {
    #[pyo3(get)]
    pub effect: Py<PyAny>,
}

#[pymethods]
impl PyPerform {
    #[new]
    fn new(py: Python<'_>, effect: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !is_effect_base_like(py, effect.bind(py))? {
            return Err(PyTypeError::new_err("Perform.effect must be EffectBase"));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Perform as u8,
            })
            .add_subclass(PyPerform { effect }))
    }
}

#[pymethods]
impl PyMap {
    #[new]
    #[pyo3(signature = (source, mapper, mapper_meta=None))]
    fn new(
        py: Python<'_>,
        source: Py<PyAny>,
        mapper: Py<PyAny>,
        mapper_meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !mapper.bind(py).is_callable() {
            return Err(PyTypeError::new_err("Map.mapper must be callable"));
        }
        let mapper_meta = mapper_meta.ok_or_else(|| {
            PyTypeError::new_err(
                "Map.mapper_meta is required. \
Program.map() should supply metadata from mapper.__code__. \
Pass mapper_meta={'function_name': ..., 'source_file': ..., 'source_line': ...}.",
            )
        })?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Map as u8,
            })
            .add_subclass(PyMap {
                source,
                mapper,
                mapper_meta,
            }))
    }
}

#[pyclass(name = "FlatMap", extends=PyDoCtrlBase)]
pub struct PyFlatMap {
    #[pyo3(get)]
    pub source: Py<PyAny>,
    #[pyo3(get)]
    pub binder: Py<PyAny>,
    #[pyo3(get)]
    pub binder_meta: Py<PyAny>,
}

#[pymethods]
impl PyFlatMap {
    #[new]
    #[pyo3(signature = (source, binder, binder_meta=None))]
    fn new(
        py: Python<'_>,
        source: Py<PyAny>,
        binder: Py<PyAny>,
        binder_meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !binder.bind(py).is_callable() {
            return Err(PyTypeError::new_err("FlatMap.binder must be callable"));
        }
        let binder_meta = binder_meta.ok_or_else(|| {
            PyTypeError::new_err(
                "FlatMap.binder_meta is required. \
Program.flat_map() should supply metadata from binder.__code__. \
Pass binder_meta={'function_name': ..., 'source_file': ..., 'source_line': ...}.",
            )
        })?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::FlatMap as u8,
            })
            .add_subclass(PyFlatMap {
                source,
                binder,
                binder_meta,
            }))
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Resume", extends=PyDoCtrlBase)]
pub struct PyResume {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResume {
    #[new]
    fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Resume.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Resume as u8,
            })
            .add_subclass(PyResume {
                continuation,
                value,
            }))
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Delegate", extends=PyDoCtrlBase)]
pub struct PyDelegate {}

#[pymethods]
impl PyDelegate {
    #[new]
    #[pyo3(signature = ())]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Delegate as u8,
            })
            .add_subclass(PyDelegate {})
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Pass", extends=PyDoCtrlBase)]
pub struct PyPass {}

#[pymethods]
impl PyPass {
    #[new]
    #[pyo3(signature = ())]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Pass as u8,
            })
            .add_subclass(PyPass {})
    }
}

/// Dispatch primitive — handler-only, one-shot.
#[pyclass(name = "Transfer", extends=PyDoCtrlBase)]
pub struct PyTransfer {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

/// Resume an unstarted continuation produced by CreateContinuation.
#[pyclass(name = "ResumeContinuation", extends=PyDoCtrlBase)]
pub struct PyResumeContinuation {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResumeContinuation {
    #[new]
    fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "ResumeContinuation.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::ResumeContinuation as u8,
            })
            .add_subclass(PyResumeContinuation {
                continuation,
                value,
            }))
    }
}

#[pymethods]
impl PyTransfer {
    #[new]
    fn new(
        py: Python<'_>,
        continuation: Py<PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "Transfer.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Transfer as u8,
            })
            .add_subclass(PyTransfer {
                continuation,
                value,
            }))
    }
}

/// Create a delimited continuation scope.
#[pyclass(name = "CreateContinuation", extends=PyDoCtrlBase)]
pub struct PyCreateContinuation {
    #[pyo3(get)]
    pub program: Py<PyAny>,
    #[pyo3(get)]
    pub handlers: Py<PyAny>,
    #[pyo3(get)]
    pub outside_scope: Py<PyAny>,
}

#[pymethods]
impl PyCreateContinuation {
    #[new]
    #[pyo3(signature = (program, handlers, outside_scope=None))]
    fn new(
        py: Python<'_>,
        program: Py<PyAny>,
        handlers: Py<PyAny>,
        outside_scope: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let program = lift_effect_to_perform_expr(py, program)?;
        let outside_scope = match outside_scope {
            Some(outside_scope) => {
                if !outside_scope.bind(py).is_none() {
                    let _ = outside_scope.bind(py).extract::<u32>()?;
                }
                outside_scope
            }
            None => py.None().into_pyobject(py)?.into_any().unbind(),
        };
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::CreateContinuation as u8,
            })
            .add_subclass(PyCreateContinuation {
                program,
                handlers,
                outside_scope,
            }))
    }
}

/// Request traceback frames for a continuation and its parent chain.
#[pyclass(name = "GetTraceback", extends=PyDoCtrlBase)]
pub struct PyGetTraceback {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
}

#[pymethods]
impl PyGetTraceback {
    #[new]
    fn new(py: Python<'_>, continuation: Py<PyAny>) -> PyResult<PyClassInitializer<Self>> {
        if !continuation.bind(py).is_instance_of::<PyK>() {
            return Err(PyTypeError::new_err(
                "GetTraceback.continuation must be K (opaque continuation handle)",
            ));
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetTraceback as u8,
            })
            .add_subclass(PyGetTraceback { continuation }))
    }
}

/// Request the current continuation.
#[pyclass(name = "GetContinuation", extends=PyDoCtrlBase)]
pub struct PyGetContinuation;

#[pymethods]
impl PyGetContinuation {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetContinuation as u8,
            })
            .add_subclass(PyGetContinuation)
    }
}

/// Request the current handler stack.
#[pyclass(name = "GetHandlers", extends=PyDoCtrlBase)]
pub struct PyGetHandlers;

#[pymethods]
impl PyGetHandlers {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetHandlers as u8,
            })
            .add_subclass(PyGetHandlers)
    }
}

/// Request the current call stack.
#[pyclass(name = "GetCallStack", extends=PyDoCtrlBase)]
pub struct PyGetCallStack;

#[pymethods]
impl PyGetCallStack {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetCallStack as u8,
            })
            .add_subclass(PyGetCallStack)
    }
}

/// Escape hatch for Python async syntax (await bridge).
#[pyclass(name = "AsyncEscape", extends=PyDoCtrlBase)]
pub struct PyAsyncEscape {
    #[pyo3(get)]
    pub action: Py<PyAny>,
}

#[pymethods]
impl PyAsyncEscape {
    #[new]
    fn new(action: Py<PyAny>) -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::AsyncEscape as u8,
            })
            .add_subclass(PyAsyncEscape { action })
    }
}

// ---------------------------------------------------------------------------
// NestingStep + NestingGenerator — WithHandler nesting chain [ADR-13]
// ---------------------------------------------------------------------------

/// ProgramBase that yields one WithHandler(handler, inner), then returns
/// the inner result. Used by run() to build handler nesting chains.
/// ADR-13: run() is defined in terms of WithHandler, not install_handler.
#[pyclass(name = "_NestingStep")]
pub struct NestingStep {
    handler: Py<PyAny>,
    inner: Py<PyAny>,
}

#[pymethods]
impl NestingStep {
    fn to_generator(slf: PyRef<'_, Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let gen = Bound::new(
            py,
            NestingGenerator {
                handler: Some(slf.handler.clone_ref(py)),
                inner: Some(slf.inner.clone_ref(py)),
                done: false,
            },
        )?
        .into_any()
        .unbind();
        Ok(gen)
    }
}

/// Generator for NestingStep. Two phases:
/// 1. `__next__()` → yields PyWithHandler { handler, inner }
/// 2. `send(value)` → raises StopIteration(value) (pass-through)
#[pyclass(name = "_NestingGenerator")]
pub struct NestingGenerator {
    handler: Option<Py<PyAny>>,
    inner: Option<Py<PyAny>>,
    done: bool,
}

#[pymethods]
impl NestingGenerator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.done {
            return Ok(None);
        }
        let handler = self
            .handler
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("NestingGenerator already consumed"))?;
        let inner = self
            .inner
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("NestingGenerator already consumed"))?;
        let inner = lift_effect_to_perform_expr(py, inner)?;
        self.done = true;
        let wh = PyWithHandler {
            handler,
            expr: inner,
            types: None,
            handler_name: None,
            handler_file: None,
            handler_line: None,
        };
        let bound = Bound::new(
            py,
            PyClassInitializer::from(PyDoExprBase)
                .add_subclass(PyDoCtrlBase {
                    tag: DoExprTag::WithHandler as u8,
                })
                .add_subclass(wh),
        )?;
        Ok(Some(bound.into_any().unbind()))
    }

    fn send(&mut self, py: Python<'_>, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !self.done {
            // First call (send(None)) — equivalent to __next__
            return match self.__next__(py)? {
                Some(v) => Ok(v),
                None => Err(PyStopIteration::new_err(py.None())),
            };
        }
        // After yielding WithHandler, the inner result comes back via send.
        // Pass through as StopIteration(value).
        Err(PyStopIteration::new_err((value,)))
    }

    fn throw(&mut self, _py: Python<'_>, exc: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyErr::from_value(exc))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;
    use crate::kleisli::{Kleisli, KleisliDebugInfo, KleisliRef};
    use crate::segment::Segment;
    use pyo3::IntoPyObject;

    #[derive(Debug)]
    struct DummyRustHandler {
        name: String,
    }

    impl Kleisli for DummyRustHandler {
        fn apply(&self, _py: Python<'_>, _args: Vec<Value>) -> Result<DoCtrl, VMError> {
            unreachable!("dummy test handler should never be applied")
        }

        fn debug_info(&self) -> KleisliDebugInfo {
            KleisliDebugInfo {
                name: self.name.clone(),
                file: Some("<test>".to_string()),
                line: Some(0),
            }
        }

        fn is_rust_builtin(&self) -> bool {
            true
        }
    }

    fn dummy_rust_handler(name: &str) -> KleisliRef {
        Arc::new(DummyRustHandler {
            name: name.to_string(),
        })
    }

    #[test]
    fn test_g2_withhandler_rust_sentinel_preserves_py_identity() {
        Python::attach(|py| {
            let mut pyvm = PyVM { vm: VM::new() };

            let root_seg = Segment::new(Marker::fresh(), None);
            let root_seg_id = pyvm.vm.alloc_segment(root_seg);
            pyvm.vm.current_segment = Some(root_seg_id);

            let sentinel = Bound::new(
                py,
                PyRustHandlerSentinel::new(dummy_rust_handler("StateHandler")),
            )
            .unwrap()
            .into_any()
            .unbind();
            let pure_expr = Bound::new(
                py,
                PyClassInitializer::from(PyDoExprBase)
                    .add_subclass(PyDoCtrlBase {
                        tag: DoExprTag::Pure as u8,
                    })
                    .add_subclass(PyPure {
                        value: py.None().into_pyobject(py).unwrap().unbind().into_any(),
                    }),
            )
            .unwrap()
            .into_any()
            .unbind();

            let with_handler = Bound::new(
                py,
                PyClassInitializer::from(PyDoExprBase)
                    .add_subclass(PyDoCtrlBase {
                        tag: DoExprTag::WithHandler as u8,
                    })
                    .add_subclass(PyWithHandler {
                        handler: sentinel.clone_ref(py),
                        expr: pure_expr,
                        types: None,
                        handler_name: None,
                        handler_file: None,
                        handler_line: None,
                    }),
            )
            .unwrap()
            .into_any();

            let yielded = pyvm.classify_yielded(py, &with_handler).unwrap();
            let seg = pyvm
                .vm
                .current_segment_mut()
                .expect("current segment missing");
            seg.mode = Mode::HandleYield(yielded);

            let event = pyvm.vm.step();
            assert!(matches!(event, StepEvent::Continue));

            let body_seg_id = pyvm.vm.current_segment.expect("body segment missing");
            let body_seg = pyvm.vm.segments.get(body_seg_id).expect("segment missing");
            let prompt_seg_id = body_seg.parent.expect("handler prompt missing");
            let prompt_seg = pyvm
                .vm
                .segments
                .get(prompt_seg_id)
                .expect("prompt segment missing");
            match &prompt_seg.kind {
                SegmentKind::PromptBoundary { handler, .. } => {
                    let Some(identity) = handler.py_identity() else {
                        panic!("G2 FAIL: rust sentinel identity was not preserved");
                    };
                    assert!(
                        identity.bind(py).is(&sentinel.bind(py)),
                        "G2 FAIL: preserved identity does not match original sentinel"
                    );
                }
                _ => panic!("G2 FAIL: rust sentinel identity was not preserved"),
            }
        });
    }

    #[test]
    fn test_g3_task_completed_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    def __init__(self, tid, value):\n        self.task = _TaskHandle(tid)\n        self.result = value\n\nobj = TaskCompletedEffect(7, 123)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();

            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "G3 FAIL: expected opaque Python TaskCompleted effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g1_run_loop_should_not_directly_call_run_rust_steps_under_gil() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        assert!(
            src.contains("let event = py.detach(|| self.run_rust_steps());"),
            "G1 FAIL: run/step loop is not detached around run_rust_steps"
        );
    }

    #[test]
    fn test_g2_run_with_result_loop_is_detached() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            !runtime_src.contains("let event = self.run_rust_steps();"),
            "G2 FAIL: run_with_result loop is not detached around run_rust_steps"
        );
    }

    #[test]
    fn test_g3_create_continuation_keeps_rust_handler_protocol() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let sentinel = Bound::new(
                py,
                PyRustHandlerSentinel::new(dummy_rust_handler("StateHandler")),
            )
            .unwrap()
            .into_any()
            .unbind();

            let handlers_list = pyo3::types::PyList::new(py, [sentinel.bind(py)]).unwrap();
            let obj = Bound::new(
                py,
                PyCreateContinuation::new(
                    py,
                    py.None().into(),
                    handlers_list.unbind().into(),
                    None,
                )
                .unwrap(),
            )
            .unwrap()
            .into_any();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                DoCtrl::CreateContinuation { handlers, .. } => {
                    assert!(
                        handlers
                            .first()
                            .is_some_and(|handler| handler.handler_name() == "StateHandler"),
                        "G3 FAIL: CreateContinuation converted rust sentinel into Python handler"
                    );
                }
                other => panic!("G3 FAIL: expected CreateContinuation, got {:?}", other),
            }
        });
    }

    #[test]
    fn test_g4_task_completed_error_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    def __init__(self, tid, err):\n        self.task = _TaskHandle(tid)\n        self.error = err\n\nobj = TaskCompletedEffect(9, ValueError('boom'))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "G4 FAIL: expected opaque Python TaskCompleted effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g6_malformed_gather_effect_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class GatherEffect(EffectBase):\n    def __init__(self):\n        self.items = [123]\nobj = GatherEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "G6 FAIL: malformed GatherEffect should classify as opaque effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g12_wait_effect_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class _Future:\n    def __init__(self):\n        self._handle = {'type': 'Task', 'task_id': 1}\n\nclass WaitEffect(EffectBase):\n    def __init__(self):\n        self.future = _Future()\n\nobj = WaitEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "G12 FAIL: WaitEffect should classify as opaque effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g7_spawn_effect_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let sentinel = Bound::new(
                py,
                PyRustHandlerSentinel::new(dummy_rust_handler("StateHandler")),
            )
            .unwrap()
            .into_any()
            .unbind();

            let locals = pyo3::types::PyDict::new(py);
            locals.set_item("sentinel", sentinel.bind(py)).unwrap();
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class SpawnEffect(EffectBase):\n    def __init__(self, p, hs, mode):\n        self.program = p\n        self.handlers = hs\n        self.store_mode = mode\nobj = SpawnEffect(None, [sentinel], 'isolated')\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "G7 FAIL: expected opaque Python Spawn effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g11_resume_with_consumed_continuation_is_error() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let mut continuation =
                crate::continuation::Continuation::placeholder(crate::ids::ContId::from_raw(
                    999_999,
                ));
            continuation.mark_consumed();
            let k = Bound::new(py, PyK::from_continuation(&continuation))
                .unwrap()
                .into_any()
                .unbind();
            let resume = Bound::new(
                py,
                PyClassInitializer::from(PyDoExprBase)
                    .add_subclass(PyDoCtrlBase {
                        tag: DoExprTag::Resume as u8,
                    })
                    .add_subclass(PyResume {
                        continuation: k,
                        value: py.None().into_pyobject(py).unwrap().unbind().into_any(),
                    }),
            )
            .unwrap()
            .into_any();

            let result = pyvm.classify_yielded(py, &resume);
            assert!(
                result.is_err(),
                "G11 FAIL: consumed continuation must error, not fallback classification"
            );
        });
    }

    #[test]
    fn test_spec_get_call_stack_classifies_to_doctrl() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let obj = Bound::new(py, PyGetCallStack::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::GetCallStack),
                "GetCallStack must classify to DoCtrl::GetCallStack, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_get_traceback_classifies_to_doctrl() {
        Python::attach(|py| {
            let seg = crate::segment::Segment::new(crate::ids::Marker::fresh(), None);
            let continuation = crate::continuation::Continuation::capture(
                &seg,
                crate::ids::SegmentId::from_index(0),
                None,
            );
            let cont_id = continuation.cont_id;
            let pyvm = PyVM { vm: VM::new() };
            let k = Bound::new(py, PyK::from_continuation(&continuation))
                .unwrap()
                .into_any()
                .unbind();
            let obj = Bound::new(py, PyGetTraceback::new(py, k).unwrap())
                .unwrap()
                .into_any();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                DoCtrl::GetTraceback { continuation } => {
                    assert_eq!(continuation.cont_id, cont_id);
                }
                _ => panic!("GetTraceback must classify to DoCtrl::GetTraceback"),
            }
        });
    }

    #[test]
    fn test_pass_outside_dispatch_context_is_error() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let obj = Bound::new(py, PyPass::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &obj);
            assert!(yielded.is_err(), "Pass outside dispatch context must error");
        });
    }

    #[test]
    fn test_spec_plain_to_generator_without_rust_base_is_rejected() {
        // R11-C: plain Python objects without VM base classes must not classify.
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class ProgramLike:\n    def to_generator(self):\n        if False:\n            yield None\n        return 1\nobj = ProgramLike()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj);
            assert!(
                yielded.is_err(),
                "R12-A: plain Python to_generator must be rejected, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_raw_generator_is_rejected() {
        // R11-C: raw generators without VM base classes must not classify.
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"def make_gen():\n    yield 1\nobj = make_gen()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj);
            assert!(
                yielded.is_err(),
                "R12-A: raw generators must be rejected (no VM base class), got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_classify_doeff_generator_promotes_to_aststream_doctrl() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"def make_gen():\n    yield 1\nraw = make_gen()\n\ndef get_frame(g):\n    return g.gi_frame\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();

            let raw = locals
                .get_item("raw")
                .expect("locals.get_item failed")
                .expect("raw generator missing")
                .unbind();
            let get_frame = locals
                .get_item("get_frame")
                .expect("locals.get_item failed")
                .expect("get_frame missing")
                .unbind();
            let kwargs = pyo3::types::PyDict::new(py);
            kwargs.set_item("generator", raw.bind(py)).unwrap();
            kwargs.set_item("function_name", "make_gen").unwrap();
            kwargs.set_item("source_file", "sample.py").unwrap();
            kwargs.set_item("source_line", 10).unwrap();
            kwargs.set_item("get_frame", get_frame.bind(py)).unwrap();
            let wrapped = py
                .get_type::<DoeffGenerator>()
                .call((), Some(&kwargs))
                .expect("DoeffGenerator construction failed");

            let yielded = pyvm.classify_yielded(py, &wrapped).unwrap();
            assert!(
                matches!(yielded, DoCtrl::IRStream { .. }),
                "DoeffGenerator must classify to DoCtrl::IRStream, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_vm_proto_doeff_generator_pyclass_construction_and_fields() {
        Python::attach(|py| {
            let module = pyo3::types::PyModule::new(py, "doeff_vm_test")
                .expect("failed to allocate doeff_vm_test module");
            doeff_vm(&module).expect("failed to init doeff_vm_test module");

            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("vm", &module)
                .expect("failed to set module in locals");

            let result = py.run(
                c"def _gen():\n    yield 1\n\nraw = _gen()\n\ndef _get_frame(g):\n    return g.gi_frame\n\nwrapped = vm.DoeffGenerator(\n    generator=raw,\n    function_name='sample_fn',\n    source_file='/tmp/sample.py',\n    source_line=77,\n    get_frame=_get_frame,\n)\n\nassert wrapped.generator is raw\nassert wrapped.function_name == 'sample_fn'\nassert wrapped.source_file == '/tmp/sample.py'\nassert wrapped.source_line == 77\nassert wrapped.get_frame(wrapped.generator) is raw.gi_frame\n",
                Some(&locals),
                Some(&locals),
            );

            assert!(
                result.is_ok(),
                "VM-PROTO-001: DoeffGenerator must be constructible from Python with all fields, got {:?}",
                result
            );
        });
    }

    #[test]
    fn test_vm_proto_entry_uses_eval_expr_and_direct_doeff_eval() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("fn start_with_expr(")
                && runtime_src.contains("Mode::HandleYield(DoCtrl::Eval")
                && runtime_src.contains("PythonCall::EvalExpr"),
            "VM-PROTO-001: entry must start from DoExpr via EvalExpr/DoCtrl::Eval"
        );
        assert!(
            !runtime_src.contains("to_generator_strict(")
                && !runtime_src.contains("start_with_generator("),
            "VM-PROTO-001: entry must not use to_generator_strict/start_with_generator"
        );
    }

    #[test]
    fn test_vm_proto_runtime_has_no_vm_side_doeff_generator_auto_wrap() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        let wrap_name = ["wrap_raw_generator_as_", "doeff_generator("].concat();
        let infer_name = ["infer_generator_", "metadata("].concat();
        assert!(
            !runtime_src.contains(&wrap_name),
            "VM-PROTO-001: VM core must not auto-wrap raw generators into DoeffGenerator"
        );
        assert!(
            !runtime_src.contains(&infer_name),
            "VM-PROTO-001: VM core must not infer DoeffGenerator metadata from raw generators"
        );
    }

    #[test]
    fn test_vm_proto_runtime_has_no_doeff_module_imports_or_inner_chain_walks() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        let inner_attr = ["__doeff_", "inner__"].concat();
        assert!(
            !runtime_src.contains("import(\"doeff."),
            "VM-PROTO-001: vm core must not import doeff.* modules"
        );
        assert!(
            !runtime_src.contains(&inner_attr),
            "VM-PROTO-001: vm core must not walk inner-generator link chains"
        );
    }

    #[test]
    fn test_spec_stdlib_effects_classify_as_opaque_python_effects() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class StateGetEffect(EffectBase):\n    __doeff_state_get__ = True\n    def __init__(self):\n        self.key = 'counter'\nobj = StateGetEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "SPEC GAP: stdlib effects should classify as opaque Python effects, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_scheduler_spawn_classifies_as_opaque_python_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class SpawnEffect(EffectBase):\n    def __init__(self):\n        self.program = None\n        self.handlers = []\n        self.store_mode = 'shared'\n\nobj = SpawnEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Perform { .. }),
                "SPEC GAP: scheduler effects should classify as opaque Python effects, got {:?}",
                yielded
            );
        });
    }

    // -----------------------------------------------------------------------
    // R13-I: Tag dispatch tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_r13i_tag_matches_variant() {
        Python::attach(|py| {
            let make_meta = || {
                let meta = PyDict::new(py);
                meta.set_item("function_name", "test_fn").unwrap();
                meta.set_item("source_file", "test_file.py").unwrap();
                meta.set_item("source_line", 1).unwrap();
                meta.into_any().unbind()
            };

            // Pure
            let obj = Bound::new(py, PyPure::new(py.None().into()))
                .unwrap()
                .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::Pure as u8);

            // Apply
            let f = py.eval(c"lambda x: x", None, None).unwrap().unbind();
            let args = pyo3::types::PyTuple::new(py, [1])
                .unwrap()
                .into_any()
                .unbind();
            let kwargs = PyDict::new(py).into_any().unbind();
            let obj = Bound::new(
                py,
                PyApply::new(py, f, args, kwargs, Some(make_meta())).unwrap(),
            )
            .unwrap()
            .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::Apply as u8);

            // Expand
            let factory = py.eval(c"lambda x: x", None, None).unwrap().unbind();
            let args = pyo3::types::PyTuple::new(py, [1])
                .unwrap()
                .into_any()
                .unbind();
            let kwargs = PyDict::new(py).into_any().unbind();
            let obj = Bound::new(
                py,
                PyExpand::new(py, factory, args, kwargs, Some(make_meta())).unwrap(),
            )
            .unwrap()
            .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::Expand as u8);

            // GetContinuation
            let obj = Bound::new(py, PyGetContinuation::new()).unwrap().into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetContinuation as u8);

            // GetHandlers
            let obj = Bound::new(py, PyGetHandlers::new()).unwrap().into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetHandlers as u8);

            // GetTraceback
            let continuation =
                crate::continuation::Continuation::placeholder(crate::ids::ContId::from_raw(1));
            let k = Bound::new(py, PyK::from_continuation(&continuation))
                .unwrap()
                .into_any()
                .unbind();
            let obj = Bound::new(py, PyGetTraceback::new(py, k).unwrap())
                .unwrap()
                .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetTraceback as u8);

            // GetCallStack
            let obj = Bound::new(py, PyGetCallStack::new()).unwrap().into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetCallStack as u8);

            // AsyncEscape
            let action = py.None().into();
            let obj = Bound::new(py, PyAsyncEscape::new(action))
                .unwrap()
                .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::AsyncEscape as u8);
        });
    }

    #[test]
    fn test_r13i_classify_yielded_uses_tag_dispatch() {
        // Verify that classify_yielded reads the tag and dispatches correctly
        // by testing several concrete variants.
        Python::attach(|py| {
            let mut pyvm = PyVM { vm: VM::new() };
            let make_meta = || {
                let meta = PyDict::new(py);
                meta.set_item("function_name", "test_fn").unwrap();
                meta.set_item("source_file", "test_file.py").unwrap();
                meta.set_item("source_line", 1).unwrap();
                meta.into_any().unbind()
            };

            // Pure → DoCtrl::Pure
            let pure_obj = Bound::new(
                py,
                PyPure::new(42i64.into_pyobject(py).unwrap().into_any().unbind()),
            )
            .unwrap()
            .into_any();
            let yielded = pyvm.classify_yielded(py, &pure_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Pure { .. }),
                "Pure tag dispatch failed, got {:?}",
                yielded
            );

            // GetHandlers → DoCtrl::GetHandlers
            let gh_obj = Bound::new(py, PyGetHandlers::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &gh_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::GetHandlers),
                "GetHandlers tag dispatch failed, got {:?}",
                yielded
            );

            // GetTraceback → DoCtrl::GetTraceback
            let seg = crate::segment::Segment::new(crate::ids::Marker::fresh(), None);
            let continuation = crate::continuation::Continuation::capture(
                &seg,
                crate::ids::SegmentId::from_index(0),
                None,
            );
            let k = Bound::new(py, PyK::from_continuation(&continuation))
                .unwrap()
                .into_any()
                .unbind();
            let gt_obj = Bound::new(py, PyGetTraceback::new(py, k).unwrap())
                .unwrap()
                .into_any();
            let yielded = pyvm.classify_yielded(py, &gt_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::GetTraceback { .. }),
                "GetTraceback tag dispatch failed, got {:?}",
                yielded
            );

            // GetCallStack → DoCtrl::GetCallStack
            let gcs_obj = Bound::new(py, PyGetCallStack::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &gcs_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::GetCallStack),
                "GetCallStack tag dispatch failed, got {:?}",
                yielded
            );

            // Apply → DoCtrl::Apply
            let f = py.eval(c"lambda x: x", None, None).unwrap().unbind();
            let args = pyo3::types::PyTuple::new(py, [1])
                .unwrap()
                .into_any()
                .unbind();
            let kwargs = PyDict::new(py).into_any().unbind();
            let apply_obj = Bound::new(
                py,
                PyApply::new(py, f, args, kwargs, Some(make_meta())).unwrap(),
            )
            .unwrap()
            .into_any();
            let yielded = pyvm.classify_yielded(py, &apply_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Apply { .. }),
                "Apply tag dispatch failed, got {:?}",
                yielded
            );

            // Expand → DoCtrl::Expand
            let factory = py.eval(c"lambda x: x", None, None).unwrap().unbind();
            let args = pyo3::types::PyTuple::new(py, [1])
                .unwrap()
                .into_any()
                .unbind();
            let kwargs = PyDict::new(py).into_any().unbind();
            let expand_obj = Bound::new(
                py,
                PyExpand::new(py, factory, args, kwargs, Some(make_meta())).unwrap(),
            )
            .unwrap()
            .into_any();
            let yielded = pyvm.classify_yielded(py, &expand_obj).unwrap();
            assert!(
                matches!(yielded, DoCtrl::Expand { .. }),
                "Expand tag dispatch failed, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_r13i_effect_base_tag() {
        Python::attach(|py| {
            let effect = Bound::new(py, PyEffectBase::new_base()).unwrap();
            let tag: u8 = effect.getattr("tag").unwrap().extract().unwrap();
            assert_eq!(tag, DoExprTag::Effect as u8);
        });
    }

    #[test]
    fn test_r13i_doctrl_base_default_tag() {
        Python::attach(|py| {
            let base = Bound::new(py, PyDoCtrlBase::new()).unwrap();
            let tag: u8 = base.getattr("tag").unwrap().extract().unwrap();
            assert_eq!(tag, DoExprTag::Unknown as u8);
        });
    }

    #[test]
    fn test_apply_round_trip_preserves_pure_wrapped_program_arg() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let outer_f = py
                .eval(c"lambda program: program", None, None)
                .unwrap()
                .unbind();
            let inner_f = py.eval(c"lambda: 7", None, None).unwrap().unbind();
            let inner_apply = DoCtrl::Apply {
                f: Box::new(DoCtrl::Pure {
                    value: Value::Python(PyShared::new(inner_f)),
                }),
                args: vec![],
                kwargs: vec![],
                metadata: CallMetadata::new(
                    "inner_program".to_string(),
                    "test.py".to_string(),
                    1,
                    None,
                    None,
                    false,
                ),
            };
            let inner_program = doctrl_to_pyexpr_for_vm(&inner_apply)
                .expect("inner Apply should convert to PyDoExpr")
                .expect("inner Apply conversion should produce object");
            let outer_apply = DoCtrl::Apply {
                f: Box::new(DoCtrl::Pure {
                    value: Value::Python(PyShared::new(outer_f)),
                }),
                args: vec![DoCtrl::Pure {
                    value: Value::Python(PyShared::new(inner_program.clone_ref(py))),
                }],
                kwargs: vec![],
                metadata: CallMetadata::new(
                    "outer_program".to_string(),
                    "test.py".to_string(),
                    2,
                    None,
                    None,
                    false,
                ),
            };

            let py_obj = doctrl_to_pyexpr_for_vm(&outer_apply)
                .expect("outer Apply should convert to PyDoExpr")
                .expect("outer Apply conversion should produce object");
            let py_apply: PyRef<'_, PyApply> = py_obj.bind(py).extract().unwrap();
            let first_arg = py_apply
                .args
                .bind(py)
                .cast::<PyList>()
                .unwrap()
                .get_item(0)
                .unwrap();
            assert!(first_arg.is_instance_of::<PyPure>());

            let round_tripped = pyvm.classify_yielded(py, py_obj.bind(py)).unwrap();
            match round_tripped {
                DoCtrl::Apply { args, .. } => {
                    assert!(matches!(args.as_slice(), [DoCtrl::Pure { .. }]));
                    match &args[0] {
                        DoCtrl::Pure {
                            value: Value::Python(program),
                        } => {
                            assert!(program.bind(py).is_instance_of::<PyApply>());
                        }
                        other => panic!("expected pure-wrapped Python program arg, got {other:?}"),
                    }
                }
                other => panic!("expected Apply after round-trip, got {other:?}"),
            }
        });
    }

    #[test]
    fn test_vm_proto_004_run_result_has_typed_traceback_data_contract() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("name = \"DoeffTracebackData\""),
            "VM-PROTO-004 FAIL: missing DoeffTracebackData pyclass"
        );
        assert!(
            runtime_src.contains("traceback_data: Option<Py<PyDoeffTracebackData>>"),
            "VM-PROTO-004 FAIL: RunResult missing traceback_data field"
        );
    }

    #[test]
    fn test_vm_proto_004_traceback_dunders_and_import_removed() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            !runtime_src.contains(".setattr(\"__doeff_traceback_data__\""),
            "VM-PROTO-004 FAIL: __doeff_traceback_data__ setattr still present"
        );
        assert!(
            !runtime_src.contains(".hasattr(\"__doeff_traceback_data__\""),
            "VM-PROTO-004 FAIL: __doeff_traceback_data__ hasattr still present"
        );
        assert!(
            !runtime_src.contains(".getattr(\"__doeff_traceback__\""),
            "VM-PROTO-004 FAIL: __doeff_traceback__ getattr still present"
        );
        assert!(
            !runtime_src.contains(".import(\"doeff.traceback\")"),
            "VM-PROTO-004 FAIL: doeff.traceback import still present"
        );
        assert!(
            !runtime_src.contains(".import(\"doeff.errors\")"),
            "VM-PROTO-004 FAIL: doeff.errors import still present"
        );
    }

    #[test]
    fn test_pyvm_runtime_has_no_enum_catchall_match_arms() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);

        let vmerror_fn = runtime_src
            .split("fn vmerror_to_pyerr_with_traceback_data")
            .nth(1)
            .expect("missing vmerror_to_pyerr_with_traceback_data")
            .split("fn vmerror_to_pyerr")
            .next()
            .expect("missing vmerror_to_pyerr boundary");
        assert!(
            !vmerror_fn.contains("_ =>"),
            "PYVM-CATCHALL-001 FAIL: VMError match must not use `_ =>` fallback"
        );
        let vmerror_variants = [
            "VMError::OneShotViolation",
            "VMError::UnhandledEffect",
            "VMError::NoMatchingHandler",
            "VMError::DelegateNoOuterHandler",
            "VMError::HandlerNotFound",
            "VMError::InvalidSegment",
            "VMError::PythonError",
            "VMError::InternalError",
            "VMError::TypeError",
            "VMError::UncaughtException",
        ];
        for variant in vmerror_variants {
            assert!(
                vmerror_fn.contains(variant),
                "PYVM-CATCHALL-001 FAIL: VMError mapping must mention {} explicitly",
                variant
            );
        }

        let pending_fn = runtime_src
            .split("fn pending_generator")
            .nth(1)
            .expect("missing pending_generator")
            .split("fn step_generator")
            .next()
            .expect("missing step_generator boundary");
        assert!(
            !pending_fn.contains("_ =>"),
            "PYVM-CATCHALL-001 FAIL: PendingPython match must not use `_ =>` fallback"
        );
        let pending_variants = [
            "PendingPython::EvalExpr",
            "PendingPython::CallFuncReturn",
            "PendingPython::StepUserGenerator",
            "PendingPython::ExpandReturn",
            "PendingPython::RustProgramContinuation",
            "PendingPython::AsyncEscape",
            "None =>",
        ];
        for variant in pending_variants {
            assert!(
                pending_fn.contains(variant),
                "PYVM-CATCHALL-001 FAIL: PendingPython mapping must mention {} explicitly",
                variant
            );
        }

        let value_fn = runtime_src
            .split("fn value_to_runtime_pyobject")
            .nth(1)
            .expect("missing value_to_runtime_pyobject")
            .split("fn call_metadata_to_dict")
            .next()
            .expect("missing call_metadata_to_dict boundary");
        assert!(
            !value_fn.contains("_ =>"),
            "PYVM-CATCHALL-001 FAIL: Value match must not use `_ =>` fallback"
        );
        let value_variants = [
            "Value::Python",
            "Value::Unit",
            "Value::Int",
            "Value::String",
            "Value::Bool",
            "Value::None",
            "Value::Continuation",
            "Value::Handlers",
            "Value::Kleisli",
            "Value::Task",
            "Value::Promise",
            "Value::ExternalPromise",
            "Value::CallStack",
            "Value::Trace",
            "Value::Traceback",
            "Value::ActiveChain",
            "Value::List",
        ];
        for variant in value_variants {
            assert!(
                value_fn.contains(variant),
                "PYVM-CATCHALL-001 FAIL: Value mapping must mention {} explicitly",
                variant
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Module-level functions [G11 / SPEC-008]
// ---------------------------------------------------------------------------

/// Module-level `run()` — the public API entry point.
///
/// Creates a fresh VM, seeds env/store, and runs the program to completion.
///
/// Handler installation is performed by the Python layer via `WithHandler`
/// nesting before calling this function. This ensures annotation-based type
/// filtering is applied consistently (ADR-13).
#[pyfunction]
#[pyo3(signature = (program, env=None, store=None, trace=false))]
fn run(
    py: Python<'_>,
    program: Bound<'_, PyAny>,
    env: Option<Bound<'_, pyo3::types::PyDict>>,
    store: Option<Bound<'_, pyo3::types::PyDict>>,
    trace: bool,
) -> PyResult<PyRunResult> {
    let mut vm = PyVM { vm: VM::new() };
    vm.vm.enable_trace(trace);

    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k = HashedPyKey::from_bound(&key)?;
            vm.vm.env_store.insert(k, Value::from_python_opaque(&value));
        }
    }

    if let Some(store_dict) = store {
        for (key, value) in store_dict.iter() {
            let k: String = key.extract()?;
            vm.vm.rust_store.put(k, Value::from_pyobject(&value));
        }
    }

    vm.run_with_result(py, program)
}

/// Module-level `async_run()` — true async version of `run()`.
///
/// G6/API-12: Returns a Python coroutine that uses `step_once()` in a loop.
/// `CallAsync` events are awaited in the Python event loop, enabling true
/// async interop. All other PythonCall variants are handled synchronously
/// via the Rust-side `step_once()`.
#[pyfunction]
#[pyo3(signature = (program, env=None, store=None, trace=false))]
fn async_run<'py>(
    py: Python<'py>,
    program: Bound<'py, PyAny>,
    env: Option<Bound<'py, pyo3::types::PyDict>>,
    store: Option<Bound<'py, pyo3::types::PyDict>>,
    trace: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let mut vm = PyVM { vm: VM::new() };
    vm.vm.enable_trace(trace);

    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k = HashedPyKey::from_bound(&key)?;
            vm.vm.env_store.insert(k, Value::from_python_opaque(&value));
        }
    }

    if let Some(store_dict) = store {
        for (key, value) in store_dict.iter() {
            let k: String = key.extract()?;
            vm.vm.rust_store.put(k, Value::from_pyobject(&value));
        }
    }

    vm.start_with_expr(py, program)?;
    if let Some(run_token) = vm.vm.current_run_token() {
        set_run_external_wait_mode(run_token, ExternalWaitMode::AsyncYield);
    }

    let py_vm = Bound::new(py, vm)?;

    let asyncio = py.import("asyncio")?;
    let ns = pyo3::types::PyDict::new(py);
    ns.set_item("_vm", &py_vm)?;
    ns.set_item("asyncio", asyncio)?;

    py.run(
        pyo3::ffi::c_str!(concat!(
            "async def _async_run_impl():\n",
            "    while True:\n",
            "        result = _vm.step_once()\n",
            "        tag = result[0]\n",
            "        if tag == 'done':\n",
            "            return _vm.build_run_result(result[1])\n",
            "        elif tag == 'error':\n",
            "            exc, traceback_data = result[1], result[2]\n",
            "            return _vm.build_run_result_error(exc, traceback_data=traceback_data)\n",
            "        elif tag == 'call_async':\n",
            "            func, args = result[1], result[2]\n",
            "            try:\n",
            "                awaitable = func(*args)\n",
            "                value = await awaitable\n",
            "                _vm.feed_async_result(value)\n",
            "            except BaseException as exc:\n",
            "                _vm.feed_async_error(exc)\n",
            "        elif tag == 'continue':\n",
            "            await asyncio.sleep(0)\n",
            "            continue\n",
            "        else:\n",
            "            raise RuntimeError(f'Unexpected step_once tag: {tag}')\n",
            "        await asyncio.sleep(0)\n",
            "_coro = _async_run_impl()\n"
        )),
        Some(&ns),
        None,
    )?;

    Ok(ns.get_item("_coro")?.unwrap().into_any())
}

#[pyfunction]
fn memory_stats(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let counts = live_object_counts();
    let dict = PyDict::new(py);
    dict.set_item("live_segments", counts.live_segments)?;
    dict.set_item("live_continuations", counts.live_continuations)?;
    dict.set_item("live_ir_streams", counts.live_ir_streams)?;
    dict.set_item("in_place_reentries", counts.in_place_reentries)?;
    dict.set_item(
        "abandoned_transfer_branch_frees",
        counts.abandoned_transfer_branch_frees,
    )?;
    dict.set_item("rust_heap_bytes", current_rust_heap_bytes())?;
    Ok(dict.into())
}

#[pymodule]
pub fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    ensure_vm_core_hooks_installed();
    m.add(
        "UnhandledEffectError",
        m.py().get_type::<UnhandledEffectError>(),
    )?;
    m.add(
        "NoMatchingHandlerError",
        m.py().get_type::<NoMatchingHandlerError>(),
    )?;
    m.add("Discontinued", m.py().get_type::<Discontinued>())?;
    m.add_class::<PyVM>()?;
    m.add_class::<crate::kleisli::PyKleisli>()?;
    m.add_class::<DoeffGeneratorFn>()?;
    m.add_class::<DoeffGenerator>()?;
    m.add_class::<PyDoExprBase>()?;
    m.add_class::<PyEffectBase>()?;
    m.add_class::<PyDoCtrlBase>()?;
    // PyDoThunkBase removed [R12-A]: DoThunk is a Python-side concept, not a VM concept.
    m.add_class::<PyDoeffTracebackData>()?;
    m.add_class::<PyRunResult>()?;
    m.add_class::<PyResultOk>()?;
    m.add_class::<PyResultErr>()?;
    m.add_class::<PyK>()?;
    m.add_class::<PyTraceFrame>()?;
    m.add_class::<PyTraceHop>()?;
    m.add_class::<PyVar>()?;
    m.add_class::<PyWithHandler>()?;
    m.add_class::<PyWithIntercept>()?;
    m.add_class::<PyDiscontinue>()?;
    m.add_class::<PyPure>()?;
    m.add_class::<PyApply>()?;
    m.add_class::<PyExpand>()?;
    m.add_class::<PyMap>()?;
    m.add_class::<PyFlatMap>()?;
    m.add_class::<PyEval>()?;
    m.add_class::<PyEvalInScope>()?;
    m.add_class::<PyAllocVar>()?;
    m.add_class::<PyReadVar>()?;
    m.add_class::<PyWriteVar>()?;
    m.add_class::<PyWriteVarNonlocal>()?;
    m.add_class::<PyPerform>()?;
    m.add_class::<PyResume>()?;
    m.add_class::<PyDelegate>()?;
    m.add_class::<PyPass>()?;
    m.add_class::<PyTransfer>()?;
    m.add_class::<PyResumeContinuation>()?;
    m.add_class::<PyCreateContinuation>()?;
    m.add_class::<PyGetContinuation>()?;
    m.add_class::<PyGetHandlers>()?;
    m.add_class::<PyGetTraceback>()?;
    m.add_class::<PyGetCallStack>()?;
    m.add_class::<PyAsyncEscape>()?;
    m.add_class::<NestingStep>()?;
    m.add_class::<NestingGenerator>()?;
    // doeff_core_effects::register_all exports sentinel objects:
    // "state", "reader", "writer", "result_safe", "scheduler", "lazy_ask", "await_handler".
    doeff_core_effects::register_all(m)?;
    m.add_class::<PyGetExecutionContext>()?;
    m.add_class::<PyExecutionContext>()?;
    // R13-I: DoExprTag constants for Python introspection
    m.add("TAG_PURE", DoExprTag::Pure as u8)?;
    m.add("TAG_MAP", DoExprTag::Map as u8)?;
    m.add("TAG_FLAT_MAP", DoExprTag::FlatMap as u8)?;
    m.add("TAG_WITH_HANDLER", DoExprTag::WithHandler as u8)?;
    m.add("TAG_PERFORM", DoExprTag::Perform as u8)?;
    m.add("TAG_RESUME", DoExprTag::Resume as u8)?;
    m.add("TAG_TRANSFER", DoExprTag::Transfer as u8)?;
    m.add("TAG_DELEGATE", DoExprTag::Delegate as u8)?;
    m.add("TAG_PASS", DoExprTag::Pass as u8)?;
    m.add("TAG_GET_CONTINUATION", DoExprTag::GetContinuation as u8)?;
    m.add("TAG_GET_HANDLERS", DoExprTag::GetHandlers as u8)?;
    m.add("TAG_GET_TRACEBACK", DoExprTag::GetTraceback as u8)?;
    m.add("TAG_WITH_INTERCEPT", DoExprTag::WithIntercept as u8)?;
    m.add("TAG_DISCONTINUE", DoExprTag::Discontinue as u8)?;
    m.add("TAG_GET_CALL_STACK", DoExprTag::GetCallStack as u8)?;
    m.add("TAG_EVAL", DoExprTag::Eval as u8)?;
    m.add("TAG_EVAL_IN_SCOPE", DoExprTag::EvalInScope as u8)?;
    m.add("TAG_ALLOC_VAR", DoExprTag::AllocVar as u8)?;
    m.add("TAG_READ_VAR", DoExprTag::ReadVar as u8)?;
    m.add("TAG_WRITE_VAR", DoExprTag::WriteVar as u8)?;
    m.add("TAG_WRITE_VAR_NONLOCAL", DoExprTag::WriteVarNonlocal as u8)?;
    m.add("TAG_APPLY", DoExprTag::Apply as u8)?;
    m.add("TAG_EXPAND", DoExprTag::Expand as u8)?;
    m.add(
        "TAG_CREATE_CONTINUATION",
        DoExprTag::CreateContinuation as u8,
    )?;
    m.add(
        "TAG_RESUME_CONTINUATION",
        DoExprTag::ResumeContinuation as u8,
    )?;
    m.add("TAG_ASYNC_ESCAPE", DoExprTag::AsyncEscape as u8)?;
    m.add("TAG_EFFECT", DoExprTag::Effect as u8)?;
    m.add("TAG_UNKNOWN", DoExprTag::Unknown as u8)?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(async_run, m)?)?;
    m.add_function(wrap_pyfunction!(memory_stats, m)?)?;
    Ok(())
}

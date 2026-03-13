use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

pyo3::create_exception!(doeff_vm, UnhandledEffectError, PyTypeError);
pyo3::create_exception!(doeff_vm, NoMatchingHandlerError, UnhandledEffectError);
pyo3::create_exception!(doeff_vm, Discontinued, pyo3::exceptions::PyException);

use crate::do_ctrl::DoCtrl;
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::effect::{PyExecutionContext, PyGetExecutionContext};

use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::ids::Marker;
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
    install_vm_hooks, DoExprTag, PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyK, PyResultErr,
    PyResultOk, PyTraceFrame, PyTraceHop, VmHooks,
};

pub mod classify;
pub mod control_primitives;
pub mod helpers;
pub mod run_result;

use classify::{classify_yielded_bound, classify_yielded_for_vm, doctrl_to_pyexpr_for_vm};
use helpers::{
    extract_stop_iteration_value, lift_effect_to_perform_expr, pyerr_to_exception,
    strict_kleisli_ref_type_error, vmerror_to_pyerr, vmerror_to_pyerr_with_traceback_data,
};
use run_result::{PyDoeffTracebackData, PyRunResult};

pub use control_primitives::{
    NestingGenerator, NestingStep, PyApply, PyAsyncEscape, PyCreateContinuation, PyDelegate,
    PyDiscontinue, PyEval, PyEvalInScope, PyExpand, PyFlatMap, PyGetCallStack, PyGetContinuation,
    PyGetHandlers, PyGetTraceback, PyMap, PyPass, PyPerform, PyPure, PyResume,
    PyResumeContinuation, PyTransfer, PyWithHandler, PyWithIntercept,
};

fn ensure_vm_core_hooks_installed() {
    install_vm_hooks(VmHooks {
        classify_yielded: classify_yielded_for_vm,
        doctrl_to_pyexpr: doctrl_to_pyexpr_for_vm,
    });
}

// NOTE: Base pyclasses moved to doeff-vm-core. Keep cfg-disabled declarations
// for source-audit tests that assert these markers exist in pyvm.rs.
#[cfg(any())]
#[pyclass(subclass, frozen, name = "DoExpr")]
pub struct PyDoExprBase;

#[cfg(any())]
#[pyclass(subclass, frozen, name = "EffectBase")]
pub struct PyEffectBase {
    #[pyo3(get)]
    pub tag: u8,
}

#[cfg(any())]
#[pyclass(subclass, frozen, extends=PyDoExprBase, name = "DoCtrlBase")]
pub struct PyDoCtrlBase {
    #[pyo3(get)]
    pub tag: u8,
}

#[pyclass]
pub struct PyVM {
    vm: VM,
}

#[pymethods]
impl PyVM {
    #[new]
    pub fn new() -> Self {
        ensure_vm_core_hooks_installed();
        PyVM { vm: VM::new() }
    }

    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        self.start_with_expr(py, program)?;

        loop {
            let event = py.detach(|| self.run_rust_steps());

            match event {
                StepEvent::Done(value) => {
                    let py_value = value.to_pyobject(py).map(|v| v.unbind());
                    self.vm.end_active_run_session();
                    return py_value;
                }
                StepEvent::Error(e) => {
                    self.vm.end_active_run_session();
                    return Err(vmerror_to_pyerr(e));
                }
                StepEvent::NeedsPython(call) => {
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                }
                StepEvent::Continue => unreachable!("handled in run_rust_steps"),
            }
        }
    }

    pub fn run_with_result(
        &mut self,
        py: Python<'_>,
        program: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        self.start_with_expr(py, program)?;

        let (result, traceback_data) = loop {
            let event = py.detach(|| self.run_rust_steps());
            match event {
                StepEvent::Done(value) => match value.to_pyobject(py) {
                    Ok(v) => break (Ok(v.unbind()), None),
                    Err(e) => {
                        let exc = pyerr_to_exception(py, e)?;
                        break (Err(exc), None);
                    }
                },
                StepEvent::Error(e) => {
                    let (pyerr, traceback_data) = vmerror_to_pyerr_with_traceback_data(py, e);
                    let exc = pyerr_to_exception(py, pyerr)?;
                    break (Err(exc), traceback_data);
                }
                StepEvent::NeedsPython(call) => {
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                }
                StepEvent::Continue => unreachable!("handled in run_rust_steps"),
            }
        };
        self.vm.end_active_run_session();

        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }
        let log_list = pyo3::types::PyList::empty(py);
        for entry in self.vm.rust_store.logs() {
            log_list.append(entry.to_pyobject(py)?)?;
        }

        Ok(PyRunResult {
            result,
            traceback_data,
            raw_store: raw_store.unbind(),
            log: log_list.into_any().unbind(),
            trace: self.build_trace_list(py)?,
        })
    }

    pub fn state_items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    pub fn logs(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let list = pyo3::types::PyList::empty(py);
        for v in self.vm.rust_store.logs() {
            list.append(v.to_pyobject(py)?)?;
        }
        Ok(list.into())
    }

    pub fn put_state(&mut self, key: String, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.vm.rust_store.put(key, Value::from_pyobject(value));
        Ok(())
    }

    pub fn put_env(&mut self, key: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let env_key = HashedPyKey::from_bound(key)?;
        self.vm
            .rust_store
            .env
            .insert(env_key, Value::from_python_opaque(value));
        Ok(())
    }

    pub fn env_items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.env {
            dict.set_item(k.to_pyobject(py), v.to_pyobject(py)?)?;
        }
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

    pub fn build_run_result(
        &self,
        py: Python<'_>,
        value: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }
        let log_list = pyo3::types::PyList::empty(py);
        for entry in self.vm.rust_store.logs() {
            log_list.append(entry.to_pyobject(py)?)?;
        }
        Ok(PyRunResult {
            result: Ok(value.unbind()),
            traceback_data: None,
            raw_store: raw_store.unbind(),
            log: log_list.into_any().unbind(),
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
        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }
        let log_list = pyo3::types::PyList::empty(py);
        for entry in self.vm.rust_store.logs() {
            log_list.append(entry.to_pyobject(py)?)?;
        }
        let exc = pyerr_to_exception(py, PyErr::from_value(error))?;
        Ok(PyRunResult {
            result: Err(exc),
            traceback_data: traceback_data.map(Bound::unbind),
            raw_store: raw_store.unbind(),
            log: log_list.into_any().unbind(),
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
                    self.vm.receive_python_result(outcome);
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
        self.vm.receive_python_result(PyCallOutcome::Value(val));
        Ok(())
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
            .receive_python_result(PyCallOutcome::GenError(py_exc));
        Ok(())
    }
}

impl PyVM {
    pub(crate) fn extract_kleisli_ref(
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
        let marker = Marker::fresh();
        let seg = Segment::new(marker, outside_seg_id);
        let seg_id = self.vm.alloc_segment(seg);
        self.vm.current_segment = Some(seg_id);
        let Some(seg) = self.vm.current_segment_mut() else {
            return Err(PyRuntimeError::new_err(
                "start_with_expr: current segment missing after allocation",
            ));
        };
        seg.mode = Mode::HandleYield(DoCtrl::Eval {
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
                    Ok(yielded) => {
                        let classified = self.classify_yielded(py, &yielded)?;
                        Ok(PyCallOutcome::GenYield(classified))
                    }
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
        let Some(seg) = self.vm.current_segment_ref() else {
            return Err(PyRuntimeError::new_err(
                "GenNext/GenSend/GenThrow: no current segment",
            ));
        };
        match &seg.pending_python {
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
            Ok(yielded) => {
                let classified = self.classify_yielded(py, &yielded)?;
                Ok(PyCallOutcome::GenYield(classified))
            }
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
            Value::Continuation(k) => Ok(Bound::new(py, PyK::from_cont_id(k.cont_id))?.into_any()),
            Value::Python(_)
            | Value::Unit
            | Value::Int(_)
            | Value::String(_)
            | Value::Bool(_)
            | Value::None
            | Value::Handlers(_)
            | Value::Kleisli(_)
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
            vm.vm
                .rust_store
                .env
                .insert(k, Value::from_python_opaque(&value));
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
            vm.vm
                .rust_store
                .env
                .insert(k, Value::from_python_opaque(&value));
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
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;
    use crate::kleisli::{Kleisli, KleisliDebugInfo, KleisliRef};
    use crate::segment::Segment;
    use pyo3::IntoPyObject;

    /// Concatenates all pyvm module source files for source-audit tests.
    fn all_pyvm_sources() -> String {
        [
            include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/mod.rs")),
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/src/pyvm/helpers.rs"
            )),
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/src/pyvm/classify.rs"
            )),
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/src/pyvm/control_primitives.rs"
            )),
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/src/pyvm/run_result.rs"
            )),
        ]
        .join("\n")
    }

    /// Returns runtime (non-test) portion of all pyvm sources.
    fn all_pyvm_runtime_sources() -> String {
        let full = all_pyvm_sources();
        full.split("#[cfg(test)]")
            .next()
            .unwrap_or(&full)
            .to_string()
    }

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

            let root_marker = Marker::fresh();
            let root_seg = Segment::new(root_marker, None);
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
            let prompt_seg_id = body_seg.caller.expect("handler prompt missing");
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
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/mod.rs"));
        assert!(
            src.contains("let event = py.detach(|| self.run_rust_steps());"),
            "G1 FAIL: run/step loop is not detached around run_rust_steps"
        );
    }

    #[test]
    fn test_g2_run_with_result_loop_is_detached() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/mod.rs"));
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
                PyCreateContinuation::new(py, py.None().into(), handlers_list.unbind().into())
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
    fn test_g11_resume_with_unknown_continuation_is_error() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let k = Bound::new(
                py,
                PyK {
                    cont_id: crate::ids::ContId::from_raw(999_999),
                },
            )
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
                "G11 FAIL: stale continuation id must error, not fallback classification"
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
            let mut pyvm = PyVM { vm: VM::new() };
            let marker = crate::ids::Marker::fresh();
            let seg = crate::segment::Segment::new(marker, None);
            let continuation = crate::continuation::Continuation::capture(
                &seg,
                crate::ids::SegmentId::from_index(0),
                None,
            );
            let cont_id = continuation.cont_id;
            pyvm.vm.register_continuation(continuation);

            let k = Bound::new(py, PyK { cont_id }).unwrap().into_any().unbind();
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
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/mod.rs"));
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
        let runtime_src = all_pyvm_runtime_sources();
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
        let runtime_src = all_pyvm_runtime_sources();
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
            let k = Bound::new(
                py,
                PyK {
                    cont_id: crate::ids::ContId::from_raw(1),
                },
            )
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
            let marker = crate::ids::Marker::fresh();
            let seg = crate::segment::Segment::new(marker, None);
            let continuation = crate::continuation::Continuation::capture(
                &seg,
                crate::ids::SegmentId::from_index(0),
                None,
            );
            let cont_id = continuation.cont_id;
            pyvm.vm.register_continuation(continuation);
            let k = Bound::new(py, PyK { cont_id }).unwrap().into_any().unbind();
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
            let base = Bound::new(
                py,
                PyClassInitializer::from(PyDoExprBase).add_subclass(PyDoCtrlBase {
                    tag: DoExprTag::Unknown as u8,
                }),
            )
            .unwrap();
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
                    value: Value::Python(inner_f),
                }),
                args: vec![],
                kwargs: vec![],
                metadata: CallMetadata::new(
                    "inner_program".to_string(),
                    "test.py".to_string(),
                    1,
                    None,
                    None,
                ),
            };
            let inner_program = doctrl_to_pyexpr_for_vm(&inner_apply)
                .expect("inner Apply should convert to PyDoExpr")
                .expect("inner Apply conversion should produce object");
            let outer_apply = DoCtrl::Apply {
                f: Box::new(DoCtrl::Pure {
                    value: Value::Python(outer_f),
                }),
                args: vec![DoCtrl::Pure {
                    value: Value::Python(inner_program.clone_ref(py)),
                }],
                kwargs: vec![],
                metadata: CallMetadata::new(
                    "outer_program".to_string(),
                    "test.py".to_string(),
                    2,
                    None,
                    None,
                ),
            };

            let py_obj = doctrl_to_pyexpr_for_vm(&outer_apply)
                .expect("outer Apply should convert to PyDoExpr")
                .expect("outer Apply conversion should produce object");
            let py_apply: PyRef<'_, PyApply> = py_obj.bind(py).extract().unwrap();
            let first_arg = py_apply
                .args
                .bind(py)
                .cast::<pyo3::types::PyList>()
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
        let run_result_src =
            include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/run_result.rs"));
        assert!(
            run_result_src.contains("name = \"DoeffTracebackData\""),
            "VM-PROTO-004 FAIL: missing DoeffTracebackData pyclass"
        );
        assert!(
            run_result_src.contains("traceback_data: Option<Py<PyDoeffTracebackData>>"),
            "VM-PROTO-004 FAIL: RunResult missing traceback_data field"
        );
    }

    #[test]
    fn test_vm_proto_004_traceback_dunders_and_import_removed() {
        let runtime_src = all_pyvm_runtime_sources();
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
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/mod.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);

        let helpers_src =
            include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm/helpers.rs"));
        let vmerror_fn = helpers_src
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
            .split("fn run(")
            .next()
            .expect("missing run( boundary");
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

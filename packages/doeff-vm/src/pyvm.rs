use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple, PyType};

use crate::ast_stream::{ASTStream, PythonGeneratorStream};
use crate::do_ctrl::{CallArg, DoCtrl, InterceptMode};
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::effect::{
    dispatch_from_shared, dispatch_ref_as_python, PyAcquireSemaphore, PyAsk, PyCancelEffect,
    PyCompletePromise, PyCreateExternalPromise, PyCreatePromise, PyCreateSemaphore,
    PyExecutionContext, PyFailPromise, PyGather, PyGet, PyGetExecutionContext, PyLocal, PyModify,
    PyProgramCallFrame, PyProgramCallStack, PyProgramTrace, PyPut, PyPythonAsyncioAwaitEffect,
    PyRace, PyReleaseSemaphore, PyResultSafeEffect, PySpawn, PyTaskCompleted, PyTell,
};

// ---------------------------------------------------------------------------
// R13-I: GIL-free tag dispatch
// ---------------------------------------------------------------------------

/// Discriminant stored as `tag: u8` on [`PyDoCtrlBase`] and [`PyEffectBase`].
///
/// A single `u8` read on a frozen struct requires no GIL contention, enabling
/// `classify_yielded` to dispatch in O(1) instead of sequential isinstance
/// checks.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoExprTag {
    Pure = 0,
    Map = 2,
    FlatMap = 3,
    WithHandler = 4,
    Perform = 5,
    Resume = 6,
    Transfer = 7,
    Delegate = 8,
    GetContinuation = 9,
    GetHandlers = 10,
    GetCallStack = 11,
    Eval = 12,
    CreateContinuation = 13,
    ResumeContinuation = 14,
    AsyncEscape = 15,
    Apply = 16,
    Expand = 17,
    GetTrace = 18,
    Pass = 19,
    GetTraceback = 20,
    WithIntercept = 21,
    Effect = 128,
    Unknown = 255,
}

impl TryFrom<u8> for DoExprTag {
    type Error = u8;
    fn try_from(v: u8) -> Result<Self, u8> {
        match v {
            0 => Ok(DoExprTag::Pure),
            2 => Ok(DoExprTag::Map),
            3 => Ok(DoExprTag::FlatMap),
            4 => Ok(DoExprTag::WithHandler),
            5 => Ok(DoExprTag::Perform),
            6 => Ok(DoExprTag::Resume),
            7 => Ok(DoExprTag::Transfer),
            8 => Ok(DoExprTag::Delegate),
            9 => Ok(DoExprTag::GetContinuation),
            10 => Ok(DoExprTag::GetHandlers),
            11 => Ok(DoExprTag::GetCallStack),
            12 => Ok(DoExprTag::Eval),
            13 => Ok(DoExprTag::CreateContinuation),
            14 => Ok(DoExprTag::ResumeContinuation),
            15 => Ok(DoExprTag::AsyncEscape),
            16 => Ok(DoExprTag::Apply),
            17 => Ok(DoExprTag::Expand),
            18 => Ok(DoExprTag::GetTrace),
            19 => Ok(DoExprTag::Pass),
            20 => Ok(DoExprTag::GetTraceback),
            21 => Ok(DoExprTag::WithIntercept),
            128 => Ok(DoExprTag::Effect),
            255 => Ok(DoExprTag::Unknown),
            other => Err(other),
        }
    }
}
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    AwaitHandlerFactory, Handler, HandlerEntry, HandlerRef, LazyAskHandlerFactory, PythonHandler,
    ReaderHandlerFactory, ResultSafeHandlerFactory, StateHandlerFactory, WriterHandlerFactory,
};
use crate::ids::Marker;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::scheduler::SchedulerHandler;
use crate::segment::Segment;
use crate::step::{Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent};
use crate::value::Value;
use crate::vm::VM;

fn build_traceback_data_pyobject(
    py: Python<'_>,
    trace: Vec<crate::capture::TraceEntry>,
    active_chain: Vec<crate::capture::ActiveChainEntry>,
) -> Option<Py<PyAny>> {
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
    Some(data.into_any().unbind())
}

fn vmerror_to_pyerr_with_traceback_data(py: Python<'_>, e: VMError) -> (PyErr, Option<Py<PyAny>>) {
    match e {
        VMError::UnhandledEffect { .. } | VMError::NoMatchingHandler { .. } => (
            PyTypeError::new_err(format!("UnhandledEffect: {}", e)),
            None,
        ),
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
        _ => (PyRuntimeError::new_err(e.to_string()), None),
    }
}

fn vmerror_to_pyerr(e: VMError) -> PyErr {
    // SAFETY: vmerror_to_pyerr is always called from GIL-holding contexts (run/step_once)
    let py = unsafe { Python::assume_attached() };
    vmerror_to_pyerr_with_traceback_data(py, e).0
}

fn is_effect_base_like(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(obj.is_instance_of::<PyEffectBase>())
}

fn classify_call_arg(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<CallArg> {
    if obj.is_instance_of::<PyDoExprBase>() || is_effect_base_like(py, obj)? {
        Ok(CallArg::Expr(PyShared::new(obj.clone().unbind())))
    } else {
        Ok(CallArg::Value(Value::from_pyobject(obj)))
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

#[pyclass]
pub struct PyVM {
    vm: VM,
}

#[pyclass(subclass, frozen, name = "DoExpr")]
pub struct PyDoExprBase;

impl PyDoExprBase {
    fn new_base() -> Self {
        PyDoExprBase
    }
}

#[pymethods]
impl PyDoExprBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>) -> Self {
        PyDoExprBase::new_base()
    }

    fn to_generator(slf: Py<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let expr = slf.into_any();
        let gen = Bound::new(
            py,
            DoExprOnceGenerator {
                expr: Some(expr),
                done: false,
            },
        )?
        .into_any()
        .unbind();
        Ok(gen)
    }
}

#[pyclass(name = "_DoExprOnceGenerator")]
struct DoExprOnceGenerator {
    expr: Option<Py<PyAny>>,
    done: bool,
}

#[pymethods]
impl DoExprOnceGenerator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.done {
            return Ok(None);
        }
        self.done = true;
        let expr = self
            .expr
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("DoExprOnceGenerator already consumed"))?;
        let expr = lift_effect_to_perform_expr(py, expr)?;
        Ok(Some(expr))
    }

    fn send(&mut self, py: Python<'_>, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !self.done {
            return match self.__next__(py)? {
                Some(v) => Ok(v),
                None => Err(PyStopIteration::new_err(py.None())),
            };
        }
        Err(PyStopIteration::new_err((value,)))
    }

    fn throw(&mut self, _py: Python<'_>, exc: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyErr::from_value(exc))
    }
}

#[pyclass(subclass, frozen, name = "EffectBase")]
pub struct PyEffectBase {
    #[pyo3(get)]
    pub tag: u8,
}

impl PyEffectBase {
    fn new_base() -> Self {
        PyEffectBase {
            tag: DoExprTag::Effect as u8,
        }
    }
}

#[pymethods]
impl PyEffectBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>) -> Self {
        PyEffectBase::new_base()
    }
}

#[pyclass(subclass, frozen, extends=PyDoExprBase, name = "DoCtrlBase")]
pub struct PyDoCtrlBase {
    #[pyo3(get)]
    pub tag: u8,
}

#[pymethods]
impl PyDoCtrlBase {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase).add_subclass(PyDoCtrlBase {
            tag: DoExprTag::Unknown as u8,
        })
    }
}

#[pyclass]
pub struct PyStdlib {
    state_marker: Option<Marker>,
    reader_marker: Option<Marker>,
    writer_marker: Option<Marker>,
}

#[pyclass]
pub struct PySchedulerHandler {
    handler: SchedulerHandler,
    marker: Option<Marker>,
}

#[pymethods]
impl PyVM {
    #[new]
    pub fn new() -> Self {
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

    pub fn stdlib(&mut self) -> PyStdlib {
        PyStdlib {
            state_marker: None,
            reader_marker: None,
            writer_marker: None,
        }
    }

    pub fn scheduler(&self) -> PySchedulerHandler {
        PySchedulerHandler {
            handler: SchedulerHandler::new(),
            marker: None,
        }
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
            .insert(env_key, Value::from_pyobject(value));
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
        traceback_data: Option<Bound<'_, PyAny>>,
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
        let traceback_data_obj = traceback_data
            .filter(|obj| !obj.is_none())
            .map(Bound::unbind);
        Ok(PyRunResult {
            result: Err(exc),
            traceback_data: traceback_data_obj,
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
                let traceback_obj = traceback_data.unwrap_or_else(|| py.None());
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

    #[pyo3(signature = (program, state=false, reader=false, writer=false))]
    pub fn run_scoped(
        &mut self,
        py: Python<'_>,
        program: Bound<'_, PyAny>,
        state: bool,
        reader: bool,
        writer: bool,
    ) -> PyResult<Py<PyAny>> {
        // Track markers installed in this scope so we can clean them up
        let mut scoped_markers: Vec<Marker> = Vec::new();

        if state {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(StateHandlerFactory), prompt_seg_id),
            );
            scoped_markers.push(marker);
        }

        if reader {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(ReaderHandlerFactory), prompt_seg_id),
            );
            scoped_markers.push(marker);
        }

        if writer {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(WriterHandlerFactory), prompt_seg_id),
            );
            scoped_markers.push(marker);
        }

        // Run the program
        let result = self.run(py, program);

        // Clean up: remove handlers installed in this scope
        for marker in &scoped_markers {
            self.vm.remove_handler(*marker);
        }

        result
    }
}

impl PyVM {
    fn classify_handler_object(
        _py: Python<'_>,
        obj: &Bound<'_, PyAny>,
        context: &str,
    ) -> PyResult<(Handler, Option<PyShared>)> {
        if obj.is_instance_of::<PyRustHandlerSentinel>() {
            let sentinel: PyRef<'_, PyRustHandlerSentinel> = obj.extract()?;
            return Ok((
                sentinel.factory.clone(),
                Some(PyShared::new(obj.clone().unbind())),
            ));
        }

        if obj.is_instance_of::<DoeffGeneratorFn>() {
            let dgfn = obj.extract::<Py<DoeffGeneratorFn>>()?;
            let callable_identity = {
                let dgfn_ref = dgfn.bind(_py).borrow();
                dgfn_ref.callable.clone_ref(_py)
            };
            let handler: Handler = Arc::new(PythonHandler::from_dgfn(dgfn));
            return Ok((handler, Some(PyShared::new(callable_identity))));
        }

        let base_message = format!("{context} handler must be DoeffGeneratorFn or RustHandler");
        if obj.is_callable() {
            return Err(PyTypeError::new_err(base_message));
        }

        let ty = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        Err(PyTypeError::new_err(format!("{base_message}, got {ty}")))
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

        let marker = Marker::fresh();
        let installed_markers = self.vm.installed_handler_markers();
        let mut scope_chain = vec![marker];
        scope_chain.extend(installed_markers);

        let seg = Segment::new(marker, None, scope_chain);
        let seg_id = self.vm.alloc_segment(seg);
        self.vm.current_segment = Some(seg_id);
        self.vm.mode = Mode::HandleYield(DoCtrl::Eval {
            expr: PyShared::new(expr),
            handlers: vec![],
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
                        py_kwargs.set_item(key, val.to_pyobject(py)?)?;
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
                let py_value = value.to_pyobject(py)?;
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
        match &self.vm.pending_python {
            Some(PendingPython::StepUserGenerator { stream, .. }) => {
                let guard = stream
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("ASTStream lock poisoned"))?;
                let Some(generator) = guard.python_generator() else {
                    return Err(PyRuntimeError::new_err(
                        "GenNext/GenSend/GenThrow: pending stream is not PythonGeneratorStream",
                    ));
                };
                Ok(generator.clone_ref(py))
            }
            _ => Err(PyRuntimeError::new_err(
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
            .map(|v| v.to_pyobject(py))
            .collect::<PyResult<_>>()?;
        Ok(PyTuple::new(py, py_values)?)
    }
}

#[pymethods]
impl PyStdlib {
    #[getter]
    pub fn state(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.state_marker.is_none() {
            self.state_marker = Some(Marker::fresh());
        }
        Ok(py.None())
    }

    #[getter]
    pub fn reader(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.reader_marker.is_none() {
            self.reader_marker = Some(Marker::fresh());
        }
        Ok(py.None())
    }

    #[getter]
    pub fn writer(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.writer_marker.is_none() {
            self.writer_marker = Some(Marker::fresh());
        }
        Ok(py.None())
    }

    pub fn install_state(&self, vm: &mut PyVM) {
        if let Some(marker) = self.state_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(StateHandlerFactory), prompt_seg_id),
            );
        }
    }

    pub fn install_reader(&self, vm: &mut PyVM) {
        if let Some(marker) = self.reader_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(ReaderHandlerFactory), prompt_seg_id),
            );
        }
    }

    pub fn install_writer(&self, vm: &mut PyVM) {
        if let Some(marker) = self.writer_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(WriterHandlerFactory), prompt_seg_id),
            );
        }
    }
}

#[pymethods]
impl PySchedulerHandler {
    pub fn install(&mut self, vm: &mut PyVM) {
        if self.marker.is_none() {
            self.marker = Some(Marker::fresh());
        }
        if let Some(marker) = self.marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Arc::new(self.handler.clone()), prompt_seg_id),
            );
        }
    }
}

fn parse_intercept_mode(mode: &str) -> Result<InterceptMode, String> {
    match mode.to_ascii_lowercase().as_str() {
        "include" => Ok(InterceptMode::Include),
        "exclude" => Ok(InterceptMode::Exclude),
        other => Err(format!(
            "WithIntercept.mode must be 'include' or 'exclude', got {other:?}"
        )),
    }
}

fn intercept_mode_to_string(mode: InterceptMode) -> String {
    match mode {
        InterceptMode::Include => "include".to_string(),
        InterceptMode::Exclude => "exclude".to_string(),
    }
}

fn call_metadata_from_callable(
    py: Python<'_>,
    callable: &Bound<'_, PyAny>,
    fallback_name: &str,
) -> CallMetadata {
    let function_name = callable
        .getattr("__qualname__")
        .ok()
        .and_then(|name| name.extract::<String>().ok())
        .or_else(|| {
            callable
                .getattr("__name__")
                .ok()
                .and_then(|name| name.extract::<String>().ok())
        })
        .unwrap_or_else(|| fallback_name.to_string());

    let (source_file, source_line) = callable
        .getattr("__code__")
        .ok()
        .map(|code| {
            let file = code
                .getattr("co_filename")
                .ok()
                .and_then(|value| value.extract::<String>().ok())
                .unwrap_or_else(|| "<unknown>".to_string());
            let line = code
                .getattr("co_firstlineno")
                .ok()
                .and_then(|value| value.extract::<u32>().ok())
                .unwrap_or(0);
            (file, line)
        })
        .unwrap_or_else(|| ("<unknown>".to_string(), 0));

    let _ = py;
    CallMetadata::new(function_name, source_file, source_line, None, None)
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
    Ok(dict.into_any().unbind())
}

fn call_arg_to_pyobject(py: Python<'_>, arg: &CallArg) -> PyResult<Py<PyAny>> {
    match arg {
        CallArg::Value(value) => Ok(value.to_pyobject(py)?.unbind()),
        CallArg::Expr(expr) => Ok(expr.clone_ref(py)),
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
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
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
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
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
            DoCtrl::TransferThrow { .. } | DoCtrl::ResumeThrow { .. } => None,
            DoCtrl::WithHandler {
                handler,
                expr,
                py_identity,
            } => {
                let debug = handler.handler_debug_info();
                let handler_obj = py_identity
                    .as_ref()
                    .map(|identity| identity.clone_ref(py))
                    .or_else(|| handler.py_identity().map(|identity| identity.clone_ref(py)))
                    .unwrap_or_else(|| py.None());
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::WithHandler as u8,
                            })
                            .add_subclass(PyWithHandler {
                                handler: handler_obj,
                                expr: expr.clone_ref(py),
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
                expr,
                types,
                mode,
                ..
            } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::WithIntercept as u8,
                        })
                        .add_subclass(PyWithIntercept {
                            f: interceptor.clone_ref(py),
                            expr: expr.clone_ref(py),
                            types: types.clone_ref(py),
                            mode: intercept_mode_to_string(*mode),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
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
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
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
            } => {
                let list = PyList::empty(py);
                for (idx, handler) in handlers.iter().enumerate() {
                    if let Some(Some(identity)) = handler_identities.get(idx) {
                        list.append(identity.bind(py))
                            .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                    } else if let Some(identity) = handler.py_identity() {
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
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
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
                        .append(call_arg_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_arg_to_pyobject(py, value)?.bind(py))
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
                                f: call_arg_to_pyobject(py, f)?,
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
                        .append(call_arg_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_arg_to_pyobject(py, value)?.bind(py))
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
                                factory: call_arg_to_pyobject(py, factory)?,
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
            DoCtrl::ASTStream { .. } => None,
            DoCtrl::Eval { expr, handlers, .. } => {
                let list = PyList::empty(py);
                for handler in handlers {
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
                                tag: DoExprTag::Eval as u8,
                            })
                            .add_subclass(PyEval {
                                expr: expr.clone_ref(py),
                                handlers: list.into_any().unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
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
            DoCtrl::GetTrace => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetTrace as u8,
                        })
                        .add_subclass(PyGetTrace),
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
        )),
    }
}

fn classify_doeff_generator_as_aststream(
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

    let stream: Arc<Mutex<Box<dyn ASTStream>>> =
        Arc::new(Mutex::new(Box::new(PythonGeneratorStream::new(
            PyShared::new(wrapped.generator.clone_ref(py)),
            PyShared::new(wrapped.get_frame.clone_ref(py)),
        )) as Box<dyn ASTStream>));

    Ok(DoCtrl::ASTStream {
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
                let (handler, py_identity) =
                    PyVM::classify_handler_object(py, handler_bound, "WithHandler")?;
                Ok(DoCtrl::WithHandler {
                    handler,
                    expr: wh.expr.clone_ref(py),
                    py_identity,
                })
            }
            DoExprTag::WithIntercept => {
                let wi: PyRef<'_, PyWithIntercept> = obj.extract()?;
                let mode = parse_intercept_mode(&wi.mode).map_err(PyTypeError::new_err)?;
                Ok(DoCtrl::WithIntercept {
                    interceptor: PyShared::new(wi.f.clone_ref(py)),
                    expr: wi.expr.clone_ref(py),
                    types: PyShared::new(wi.types.clone_ref(py)),
                    mode,
                    metadata: call_metadata_from_callable(
                        py,
                        wi.f.bind(py),
                        "WithIntercept.interceptor",
                    ),
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
                let f = classify_call_arg(py, a.f.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in a.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_arg(py, item.as_any())?);
                }
                let kwargs_dict = a.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_arg(py, v.as_any())?));
                }
                Ok(DoCtrl::Apply {
                    f,
                    args,
                    kwargs,
                    metadata: call_metadata_from_pyapply(py, &a)?,
                })
            }
            DoExprTag::Expand => {
                let e: PyRef<'_, PyExpand> = obj.extract()?;
                let factory = classify_call_arg(py, e.factory.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in e.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_arg(py, item.as_any())?);
                }
                let kwargs_dict = e.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_arg(py, v.as_any())?));
                }
                Ok(DoCtrl::Expand {
                    factory,
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
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "Resume with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
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
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "Transfer with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
                Ok(DoCtrl::Transfer {
                    continuation: k,
                    value: Value::from_pyobject(t.value.bind(py)),
                })
            }
            DoExprTag::Delegate => {
                let _d: PyRef<'_, PyDelegate> = obj.extract()?;
                let effect = vm
                    .dispatch_stack
                    .last()
                    .map(|ctx| ctx.effect.clone())
                    .ok_or_else(|| {
                        PyRuntimeError::new_err("Delegate called outside dispatch context")
                    })?;
                Ok(DoCtrl::Delegate { effect })
            }
            DoExprTag::Pass => {
                let _p: PyRef<'_, PyPass> = obj.extract()?;
                let effect = vm
                    .dispatch_stack
                    .last()
                    .map(|ctx| ctx.effect.clone())
                    .ok_or_else(|| {
                        PyRuntimeError::new_err("Pass called outside dispatch context")
                    })?;
                Ok(DoCtrl::Pass { effect })
            }
            DoExprTag::ResumeContinuation => {
                let rc: PyRef<'_, PyResumeContinuation> = obj.extract()?;
                let k_pyobj = rc.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "ResumeContinuation.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "ResumeContinuation with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
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
                    let (handler, identity) =
                        PyVM::classify_handler_object(py, &item, "CreateContinuation")?;
                    handlers.push(handler);
                    handler_identities.push(identity);
                }
                Ok(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities,
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
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "GetTraceback with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
                Ok(DoCtrl::GetTraceback { continuation: k })
            }
            DoExprTag::GetCallStack => Ok(DoCtrl::GetCallStack),
            DoExprTag::GetTrace => Ok(DoCtrl::GetTrace),
            DoExprTag::Eval => {
                let eval: PyRef<'_, PyEval> = obj.extract()?;
                let expr = eval.expr.clone_ref(py);
                let handlers_list = eval.handlers.bind(py);
                let mut handlers = Vec::new();
                for item in handlers_list.try_iter()? {
                    let item = item?;
                    let (handler, _) = PyVM::classify_handler_object(py, &item, "Eval")?;
                    handlers.push(handler);
                }
                Ok(DoCtrl::Eval {
                    expr: PyShared::new(expr),
                    handlers,
                    metadata: None,
                })
            }
            DoExprTag::AsyncEscape => {
                let ae: PyRef<'_, PyAsyncEscape> = obj.extract()?;
                Ok(DoCtrl::PythonAsyncSyntaxEscape {
                    action: ae.action.clone_ref(py),
                })
            }
            DoExprTag::Effect | DoExprTag::Unknown => Err(PyTypeError::new_err(
                "yielded DoCtrlBase has unrecognized tag",
            )),
        };
    }

    if obj.is_instance_of::<DoeffGenerator>() {
        return classify_doeff_generator_as_aststream(py, obj, None, "yielded value");
    }

    if obj.is_instance_of::<PyDoExprBase>() {
        let to_generator = obj.getattr("to_generator").map_err(|_| {
            PyTypeError::new_err("DoExpr object is missing callable to_generator()")
        })?;
        if !to_generator.is_callable() {
            return Err(PyTypeError::new_err("DoExpr.to_generator must be callable"));
        }
        let ty_name = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "DoExpr".to_string());
        let generated = to_generator.call0()?;
        let metadata = CallMetadata::new(
            format!("{ty_name}.to_generator"),
            "<doexpr>".to_string(),
            0,
            None,
            Some(PyShared::new(obj.clone().unbind())),
        );
        return classify_doeff_generator_as_aststream(
            py,
            generated.as_any(),
            Some(metadata),
            "DoExpr.to_generator",
        );
    }

    // Fallback: bare effect -> auto-lift to Perform (R14-C)
    if is_effect_base_like(py, obj)? {
        if obj.is_instance_of::<PyProgramTrace>() {
            return Ok(DoCtrl::GetTrace);
        }
        if obj.is_instance_of::<PyProgramCallStack>() {
            return Ok(DoCtrl::GetCallStack);
        }
        return Ok(DoCtrl::Perform {
            effect: dispatch_from_shared(PyShared::new(obj.clone().unbind())),
        });
    }

    Err(PyTypeError::new_err(
        "yielded value must be EffectBase or DoExpr",
    ))
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
    CallMetadata::new(
        function_name,
        source_file,
        source_line,
        args_repr,
        program_call,
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

// D9: Ok/Err wrapper types for RunResult.result (spec says Ok(val)/Err(exc) objects)
#[pyclass(frozen, name = "Ok")]
pub struct PyResultOk {
    pub(crate) value: Py<PyAny>,
}

#[pymethods]
impl PyResultOk {
    #[new]
    fn new(value: Py<PyAny>) -> Self {
        PyResultOk { value }
    }

    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("value",)
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> Py<PyAny> {
        self.value.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        true
    }

    fn is_err(&self) -> bool {
        false
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let val_repr = self.value.bind(py).repr()?.to_string();
        Ok(format!("Ok({})", val_repr))
    }

    fn __bool__(&self) -> bool {
        true
    }
}

#[pyclass(frozen, name = "Err")]
pub struct PyResultErr {
    pub(crate) error: Py<PyAny>,
    pub(crate) captured_traceback: Py<PyAny>,
}

#[pymethods]
impl PyResultErr {
    #[new]
    #[pyo3(signature = (error, captured_traceback=None))]
    fn new(py: Python<'_>, error: Py<PyAny>, captured_traceback: Option<Py<PyAny>>) -> Self {
        PyResultErr {
            error,
            captured_traceback: captured_traceback.unwrap_or_else(|| py.None()),
        }
    }

    #[classattr]
    fn __match_args__() -> (&'static str,) {
        ("error",)
    }

    #[getter]
    fn error(&self, py: Python<'_>) -> Py<PyAny> {
        self.error.clone_ref(py)
    }

    #[getter]
    fn captured_traceback(&self, py: Python<'_>) -> Py<PyAny> {
        self.captured_traceback.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        false
    }

    fn is_err(&self) -> bool {
        true
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let err_repr = self.error.bind(py).repr()?.to_string();
        Ok(format!("Err({})", err_repr))
    }

    fn __bool__(&self) -> bool {
        false
    }
}

#[pyclass(frozen, name = "RunResult")]
pub struct PyRunResult {
    result: Result<Py<PyAny>, PyException>,
    #[pyo3(get)]
    traceback_data: Option<Py<PyAny>>,
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

    fn format_traceback_data_preview(traceback_data: &Bound<'_, PyAny>, verbose: bool) -> String {
        let mut lines: Vec<String> = Vec::new();
        let max_items = if verbose { 32 } else { 8 };

        if let Ok(active_chain) = traceback_data.getattr("active_chain") {
            if !active_chain.is_none() {
                lines.push("ActiveChain:".to_string());
                lines.push(Self::preview_sequence(&active_chain, max_items));
            }
        }

        if let Ok(entries) = traceback_data.getattr("entries") {
            let entry_count = entries.len().ok();
            if verbose {
                lines.push("TraceEntries:".to_string());
                lines.push(Self::preview_sequence(&entries, max_items));
            } else if let Some(count) = entry_count {
                lines.push(format!("TraceEntries: {count}"));
            } else {
                lines.push("TraceEntries: <unknown>".to_string());
            }
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

/// Opaque continuation handle passed to Python handlers.
#[pyclass(name = "K")]
pub struct PyK {
    cont_id: crate::ids::ContId,
}

impl PyK {
    pub(crate) fn from_cont_id(cont_id: crate::ids::ContId) -> Self {
        PyK { cont_id }
    }
}

#[pymethods]
impl PyK {
    fn __repr__(&self) -> String {
        format!("K({})", self.cont_id.raw())
    }
}

/// Composition primitive — usable in any Program.
#[pyclass(name = "WithHandler", extends=PyDoCtrlBase)]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub expr: Py<PyAny>,
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
    #[pyo3(signature = (handler, expr, handler_name=None, handler_file=None, handler_line=None))]
    fn new(
        py: Python<'_>,
        handler: Py<PyAny>,
        expr: Py<PyAny>,
        handler_name: Option<String>,
        handler_file: Option<String>,
        handler_line: Option<u32>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let handler_obj = handler.bind(py);
        let is_rust_handler = handler_obj.is_instance_of::<PyRustHandlerSentinel>();
        let is_dgfn = handler_obj.is_instance_of::<DoeffGeneratorFn>();
        if !is_rust_handler && !is_dgfn {
            if handler_obj.is_callable() {
                return Err(PyTypeError::new_err(
                    "WithHandler handler must be DoeffGeneratorFn or RustHandler",
                ));
            }
            let ty = handler_obj
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "<unknown>".to_string());
            return Err(PyTypeError::new_err(format!(
                "WithHandler handler must be DoeffGeneratorFn or RustHandler, got {ty}"
            )));
        }

        let expr = lift_effect_to_perform_expr(py, expr)?;

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
    pub types: Py<PyAny>,
    #[pyo3(get)]
    pub mode: String,
}

#[pymethods]
impl PyWithIntercept {
    #[new]
    #[pyo3(signature = (f, expr, types=None, mode=None))]
    fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        expr: Py<PyAny>,
        types: Option<Py<PyAny>>,
        mode: Option<String>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !f.bind(py).is_callable() {
            return Err(PyTypeError::new_err("WithIntercept.f must be callable"));
        }

        let types = types.unwrap_or_else(|| PyTuple::empty(py).into_any().unbind());
        let types_bound = types.bind(py);
        if !types_bound.is_instance_of::<PyTuple>() {
            return Err(PyTypeError::new_err(
                "WithIntercept.types must be tuple[type, ...]",
            ));
        }
        for item in types_bound.try_iter()? {
            let item = item?;
            if !item.is_instance_of::<PyType>() {
                return Err(PyTypeError::new_err(
                    "WithIntercept.types must contain only Python type objects",
                ));
            }
        }

        let normalized_mode = parse_intercept_mode(mode.as_deref().unwrap_or("include"))
            .map_err(PyTypeError::new_err)?;
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
                types,
                mode: intercept_mode_to_string(normalized_mode),
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
    #[pyo3(get)]
    pub handlers: Py<PyAny>,
}

#[pymethods]
impl PyEval {
    #[new]
    fn new(
        py: Python<'_>,
        expr: Py<PyAny>,
        handlers: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let expr = lift_effect_to_perform_expr(py, expr)?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Eval as u8,
            })
            .add_subclass(PyEval { expr, handlers }))
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
}

#[pymethods]
impl PyCreateContinuation {
    #[new]
    fn new(
        py: Python<'_>,
        program: Py<PyAny>,
        handlers: Py<PyAny>,
    ) -> PyResult<PyClassInitializer<Self>> {
        let program = lift_effect_to_perform_expr(py, program)?;
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::CreateContinuation as u8,
            })
            .add_subclass(PyCreateContinuation { program, handlers }))
    }
}

#[pyclass(frozen, name = "TraceFrame")]
pub struct PyTraceFrame {
    #[pyo3(get)]
    pub func_name: String,
    #[pyo3(get)]
    pub source_file: String,
    #[pyo3(get)]
    pub source_line: u32,
}

#[pymethods]
impl PyTraceFrame {
    #[new]
    fn new(func_name: String, source_file: String, source_line: u32) -> Self {
        Self {
            func_name,
            source_file,
            source_line,
        }
    }
}

#[pyclass(frozen, name = "TraceHop")]
pub struct PyTraceHop {
    #[pyo3(get)]
    pub frames: Vec<Py<PyTraceFrame>>,
}

#[pymethods]
impl PyTraceHop {
    #[new]
    fn new(frames: Vec<Py<PyTraceFrame>>) -> Self {
        Self { frames }
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

/// Request the current unified execution trace.
#[pyclass(name = "GetTrace", extends=PyDoCtrlBase)]
pub struct PyGetTrace;

#[pymethods]
impl PyGetTrace {
    #[new]
    fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::GetTrace as u8,
            })
            .add_subclass(PyGetTrace)
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
// PyRustHandlerSentinel — opaque handler sentinel [ADR-14]
// ---------------------------------------------------------------------------

/// Opaque sentinel wrapping a Rust handler factory.
/// Python users see this as an opaque handler value (e.g., `state`, `reader`).
/// Passed to `run(handlers=[...])` and recognized by classify_yielded in
/// WithHandler arms. ADR-14: no string-based shortcuts.
#[pyclass(frozen, name = "RustHandler")]
pub struct PyRustHandlerSentinel {
    pub(crate) factory: HandlerRef,
}

impl PyRustHandlerSentinel {
    pub(crate) fn factory_ref(&self) -> HandlerRef {
        self.factory.clone()
    }
}

#[pymethods]
impl PyRustHandlerSentinel {
    fn __repr__(&self) -> String {
        format!("RustHandler({:?})", self.factory)
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
    use crate::segment::Segment;
    use pyo3::IntoPyObject;

    #[test]
    fn test_g2_withhandler_rust_sentinel_preserves_py_identity() {
        Python::attach(|py| {
            let mut pyvm = PyVM { vm: VM::new() };

            let root_marker = Marker::fresh();
            let root_seg = Segment::new(root_marker, None, vec![]);
            let root_seg_id = pyvm.vm.alloc_segment(root_seg);
            pyvm.vm.current_segment = Some(root_seg_id);

            let sentinel = Bound::new(
                py,
                PyRustHandlerSentinel {
                    factory: Arc::new(StateHandlerFactory),
                },
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
                        expr: py.None().into_pyobject(py).unwrap().unbind().into_any(),
                        handler_name: None,
                        handler_file: None,
                        handler_line: None,
                    }),
            )
            .unwrap()
            .into_any();

            let yielded = pyvm.classify_yielded(py, &with_handler).unwrap();
            pyvm.vm.mode = Mode::HandleYield(yielded);

            let event = pyvm.vm.step();
            assert!(matches!(event, StepEvent::NeedsPython(_)));

            let body_seg_id = pyvm.vm.current_segment.expect("body segment missing");
            let body_seg = pyvm.vm.segments.get(body_seg_id).expect("segment missing");
            let handler_marker = *body_seg
                .scope_chain
                .first()
                .expect("handler marker missing on body scope");
            let entry = pyvm
                .vm
                .handlers
                .get(&handler_marker)
                .expect("handler entry missing");

            let identity = entry
                .py_identity
                .as_ref()
                .expect("G2 FAIL: rust sentinel identity was not preserved");
            assert!(
                identity.bind(py).is(&sentinel.bind(py)),
                "G2 FAIL: preserved identity does not match original sentinel"
            );
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
                PyRustHandlerSentinel {
                    factory: Arc::new(StateHandlerFactory),
                },
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
                PyRustHandlerSentinel {
                    factory: Arc::new(StateHandlerFactory),
                },
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
            let seg = crate::segment::Segment::new(marker, None, vec![marker]);
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
                matches!(yielded, DoCtrl::ASTStream { .. }),
                "DoeffGenerator must classify to DoCtrl::ASTStream, got {:?}",
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
            let marker = crate::ids::Marker::fresh();
            let seg = crate::segment::Segment::new(marker, None, vec![marker]);
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
            let base = Bound::new(py, PyDoCtrlBase::new()).unwrap();
            let tag: u8 = base.getattr("tag").unwrap().extract().unwrap();
            assert_eq!(tag, DoExprTag::Unknown as u8);
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
            runtime_src.contains("traceback_data: Option<Py<PyAny>>"),
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
    }
}

// ---------------------------------------------------------------------------
// Module-level functions [G11 / SPEC-008]
// ---------------------------------------------------------------------------

#[pyfunction]
fn _notify_semaphore_handle_dropped(state_id: u64, semaphore_id: u64) {
    crate::scheduler::notify_semaphore_handle_dropped(state_id, semaphore_id);
}

#[pyfunction]
fn _debug_scheduler_semaphore_count(state_id: u64) -> Option<usize> {
    crate::scheduler::debug_semaphore_count_for_state(state_id)
}

/// Module-level `run()` — the public API entry point.
///
/// Creates a fresh VM, seeds env/store, wraps the program in a WithHandler
/// nesting chain, and returns a `RunResult`.
///
/// ADR-13: Handler installation goes through WithHandler nesting, not
/// install_handler bypass. `run(prog, handlers=[h0, h1, h2])` is semantically
/// equivalent to `WithHandler(h0, WithHandler(h1, WithHandler(h2, prog)))`.
///
/// `handlers` accepts a list of:
///   - `RustHandler` sentinels: `state`, `reader`, `writer`, `result_safe`, `scheduler`, `lazy_ask`
///   - Python handler callables
#[pyfunction]
#[pyo3(signature = (program, handlers=None, env=None, store=None, trace=false))]
fn run(
    py: Python<'_>,
    program: Bound<'_, PyAny>,
    handlers: Option<Bound<'_, pyo3::types::PyList>>,
    env: Option<Bound<'_, pyo3::types::PyDict>>,
    store: Option<Bound<'_, pyo3::types::PyDict>>,
    trace: bool,
) -> PyResult<PyRunResult> {
    let mut vm = PyVM { vm: VM::new() };
    vm.vm.enable_trace(trace);

    // Seed env
    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k = HashedPyKey::from_bound(&key)?;
            vm.vm.rust_store.env.insert(k, Value::from_pyobject(&value));
        }
    }

    // Seed store
    if let Some(store_dict) = store {
        for (key, value) in store_dict.iter() {
            let k: String = key.extract()?;
            vm.vm.rust_store.put(k, Value::from_pyobject(&value));
        }
    }

    // Build WithHandler nesting chain directly as DoCtrl objects.
    // handlers=[h0, h1, h2] → WithHandler(h0, WithHandler(h1, WithHandler(h2, program)))
    let mut wrapped: Py<PyAny> = program.unbind();

    if let Some(handler_list) = handlers {
        let items: Vec<_> = handler_list.iter().collect();
        for handler_obj in items.into_iter().rev() {
            wrapped = lift_effect_to_perform_expr(py, wrapped)?;
            let wh = PyWithHandler {
                handler: handler_obj.unbind(),
                expr: wrapped,
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
            wrapped = bound.into_any().unbind();
        }
    }

    vm.run_with_result(py, wrapped.bind(py).clone())
}

/// Module-level `async_run()` — true async version of `run()`.
///
/// G6/API-12: Returns a Python coroutine that uses `step_once()` in a loop.
/// `CallAsync` events are awaited in the Python event loop, enabling true
/// async interop. All other PythonCall variants are handled synchronously
/// via the Rust-side `step_once()`.
#[pyfunction]
#[pyo3(signature = (program, handlers=None, env=None, store=None, trace=false))]
fn async_run<'py>(
    py: Python<'py>,
    program: Bound<'py, PyAny>,
    handlers: Option<Bound<'py, pyo3::types::PyList>>,
    env: Option<Bound<'py, pyo3::types::PyDict>>,
    store: Option<Bound<'py, pyo3::types::PyDict>>,
    trace: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let mut vm = PyVM { vm: VM::new() };
    vm.vm.enable_trace(trace);

    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k = HashedPyKey::from_bound(&key)?;
            vm.vm.rust_store.env.insert(k, Value::from_pyobject(&value));
        }
    }

    if let Some(store_dict) = store {
        for (key, value) in store_dict.iter() {
            let k: String = key.extract()?;
            vm.vm.rust_store.put(k, Value::from_pyobject(&value));
        }
    }

    let mut wrapped: Py<PyAny> = program.unbind();

    if let Some(handler_list) = handlers {
        let items: Vec<_> = handler_list.iter().collect();
        for handler_obj in items.into_iter().rev() {
            wrapped = lift_effect_to_perform_expr(py, wrapped)?;
            let wh = PyWithHandler {
                handler: handler_obj.unbind(),
                expr: wrapped,
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
            wrapped = bound.into_any().unbind();
        }
    }

    vm.start_with_expr(py, wrapped.bind(py).clone())?;

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
    m.add_class::<PyVM>()?;
    m.add_class::<DoeffGeneratorFn>()?;
    m.add_class::<DoeffGenerator>()?;
    m.add_class::<PyDoExprBase>()?;
    m.add_class::<PyEffectBase>()?;
    m.add_class::<PyDoCtrlBase>()?;
    // PyDoThunkBase removed [R12-A]: DoThunk is a Python-side concept, not a VM concept.
    m.add_class::<PyStdlib>()?;
    m.add_class::<PySchedulerHandler>()?;
    m.add_class::<PyDoeffTracebackData>()?;
    m.add_class::<PyRunResult>()?;
    m.add_class::<PyResultOk>()?;
    m.add_class::<PyResultErr>()?;
    m.add_class::<PyK>()?;
    m.add_class::<PyTraceFrame>()?;
    m.add_class::<PyTraceHop>()?;
    m.add_class::<PyWithHandler>()?;
    m.add_class::<PyWithIntercept>()?;
    m.add_class::<PyPure>()?;
    m.add_class::<PyApply>()?;
    m.add_class::<PyExpand>()?;
    m.add_class::<PyMap>()?;
    m.add_class::<PyFlatMap>()?;
    m.add_class::<PyEval>()?;
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
    m.add_class::<PyGetTrace>()?;
    m.add_class::<PyAsyncEscape>()?;
    m.add_class::<PyRustHandlerSentinel>()?;
    m.add_class::<NestingStep>()?;
    m.add_class::<NestingGenerator>()?;
    // ADR-14: Module-level sentinel handler objects
    m.add(
        "state",
        PyRustHandlerSentinel {
            factory: Arc::new(StateHandlerFactory),
        },
    )?;
    m.add(
        "reader",
        PyRustHandlerSentinel {
            factory: Arc::new(ReaderHandlerFactory),
        },
    )?;
    m.add(
        "writer",
        PyRustHandlerSentinel {
            factory: Arc::new(WriterHandlerFactory),
        },
    )?;
    m.add(
        "result_safe",
        PyRustHandlerSentinel {
            factory: Arc::new(ResultSafeHandlerFactory),
        },
    )?;
    // R11-A: #[pyclass] effect structs for isinstance checks
    m.add_class::<PyGet>()?;
    m.add_class::<PyPut>()?;
    m.add_class::<PyModify>()?;
    m.add_class::<PyAsk>()?;
    m.add_class::<PyLocal>()?;
    m.add_class::<PyTell>()?;
    m.add_class::<PySpawn>()?;
    m.add_class::<PyGather>()?;
    m.add_class::<PyRace>()?;
    m.add_class::<PyCreatePromise>()?;
    m.add_class::<PyCompletePromise>()?;
    m.add_class::<PyFailPromise>()?;
    m.add_class::<PyCreateExternalPromise>()?;
    m.add_class::<PyCancelEffect>()?;
    m.add_class::<PyTaskCompleted>()?;
    m.add_class::<PyCreateSemaphore>()?;
    m.add_class::<PyAcquireSemaphore>()?;
    m.add_class::<PyReleaseSemaphore>()?;
    m.add_class::<PyPythonAsyncioAwaitEffect>()?;
    m.add_class::<PyResultSafeEffect>()?;
    m.add_class::<PyProgramTrace>()?;
    m.add_class::<PyProgramCallStack>()?;
    m.add_class::<PyProgramCallFrame>()?;
    m.add_class::<PyGetExecutionContext>()?;
    m.add_class::<PyExecutionContext>()?;
    // G14: scheduler sentinel
    m.add(
        "scheduler",
        PyRustHandlerSentinel {
            factory: Arc::new(SchedulerHandler::new()),
        },
    )?;
    m.add(
        "lazy_ask",
        PyRustHandlerSentinel {
            factory: Arc::new(LazyAskHandlerFactory::new()),
        },
    )?;
    m.add(
        "await_handler",
        PyRustHandlerSentinel {
            factory: Arc::new(AwaitHandlerFactory),
        },
    )?;
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
    m.add("TAG_GET_CALL_STACK", DoExprTag::GetCallStack as u8)?;
    m.add("TAG_GET_TRACE", DoExprTag::GetTrace as u8)?;
    m.add("TAG_EVAL", DoExprTag::Eval as u8)?;
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
    m.add_function(wrap_pyfunction!(_notify_semaphore_handle_dropped, m)?)?;
    m.add_function(wrap_pyfunction!(_debug_scheduler_semaphore_count, m)?)?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(async_run, m)?)?;
    Ok(())
}

use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::do_ctrl::{CallArg, DoCtrl};
use crate::doeff_generator::DoeffGenerator;
use crate::effect::{
    dispatch_from_shared, dispatch_to_pyobject, PyAcquireSemaphore, PyAsk, PyCancelEffect,
    PyCompletePromise, PyCreateExternalPromise, PyCreatePromise, PyCreateSemaphore, PyFailPromise,
    PyGather, PyGet, PyLocal, PyModify, PyProgramCallFrame, PyProgramCallStack, PyProgramTrace,
    PyPut, PyPythonAsyncioAwaitEffect, PyRace, PyReleaseSemaphore, PyResultSafeEffect, PySpawn,
    PyTaskCompleted, PyTell,
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
    Call = 1,
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
    GetTrace = 18,
    Eval = 12,
    CreateContinuation = 13,
    ResumeContinuation = 14,
    AsyncEscape = 15,
    Effect = 128,
    Unknown = 255,
}

impl TryFrom<u8> for DoExprTag {
    type Error = u8;
    fn try_from(v: u8) -> Result<Self, u8> {
        match v {
            0 => Ok(DoExprTag::Pure),
            1 => Ok(DoExprTag::Call),
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
            18 => Ok(DoExprTag::GetTrace),
            12 => Ok(DoExprTag::Eval),
            13 => Ok(DoExprTag::CreateContinuation),
            14 => Ok(DoExprTag::ResumeContinuation),
            15 => Ok(DoExprTag::AsyncEscape),
            128 => Ok(DoExprTag::Effect),
            255 => Ok(DoExprTag::Unknown),
            other => Err(other),
        }
    }
}
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    AwaitHandlerFactory, Handler, HandlerEntry, LazyAskHandlerFactory, ReaderHandlerFactory,
    ResultSafeHandlerFactory, RustProgramHandlerRef, StateHandlerFactory, WriterHandlerFactory,
};
use crate::ids::Marker;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::scheduler::SchedulerHandler;
use crate::segment::Segment;
use crate::step::{
    Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent, Yielded,
};
use crate::value::Value;
use crate::vm::VM;

fn build_traceback_data_pyobject(
    py: Python<'_>,
    trace: Vec<crate::capture::TraceEntry>,
    active_chain: Vec<crate::capture::ActiveChainEntry>,
) -> Option<Py<PyAny>> {
    let entries = Value::Trace(trace).to_pyobject(py).ok()?.unbind();
    let active_chain = Value::ActiveChain(active_chain)
        .to_pyobject(py)
        .ok()?
        .unbind();
    let data = Bound::new(
        py,
        PyDoeffTracebackData {
            entries,
            active_chain,
        },
    )
    .ok()?;
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
        let gen = self.to_generator_strict(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;

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
        let gen = self.to_generator_strict(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;

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
        let gen = self.to_generator_strict(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;
        Ok(())
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
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(StateHandlerFactory)),
                    prompt_seg_id,
                ),
            );
            scoped_markers.push(marker);
        }

        if reader {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(ReaderHandlerFactory)),
                    prompt_seg_id,
                ),
            );
            scoped_markers.push(marker);
        }

        if writer {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(WriterHandlerFactory)),
                    prompt_seg_id,
                ),
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
    fn is_python_generator_object(obj: &Bound<'_, PyAny>) -> bool {
        let py = obj.py();
        py.import("inspect")
            .and_then(|m| m.getattr("isgenerator"))
            .and_then(|f| f.call1((obj.clone(),)))
            .ok()
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false)
    }

    fn is_doeff_generator_object(obj: &Bound<'_, PyAny>) -> bool {
        obj.is_instance_of::<DoeffGenerator>()
    }

    fn require_doeff_generator(
        &self,
        py: Python<'_>,
        candidate: Py<PyAny>,
        context: &str,
    ) -> PyResult<Py<PyAny>> {
        let candidate_bound = candidate.bind(py);
        if Self::is_doeff_generator_object(candidate_bound) {
            return Ok(candidate);
        }

        let ty = candidate_bound
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        if Self::is_python_generator_object(candidate_bound) {
            return Err(PyTypeError::new_err(format!(
                "{context}: raw generators are not accepted; expected DoeffGenerator"
            )));
        }
        Err(PyTypeError::new_err(format!(
            "{context}: expected DoeffGenerator, got {ty}"
        )))
    }

    fn extract_doeff_generator(
        &self,
        py: Python<'_>,
        gen: Bound<'_, PyAny>,
        context: &str,
    ) -> PyResult<(PyShared, PyShared, CallMetadata)> {
        let wrapped: PyRef<'_, DoeffGenerator> = gen.extract().map_err(|_| {
            let ty = gen
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

        let metadata = CallMetadata::new(
            wrapped.function_name.clone(),
            wrapped.source_file.clone(),
            wrapped.source_line,
            None,
            None,
        );
        Ok((
            PyShared::new(wrapped.generator.clone_ref(py)),
            PyShared::new(wrapped.get_frame.clone_ref(py)),
            metadata,
        ))
    }

    fn start_with_generator(&mut self, gen: Bound<'_, PyAny>) -> PyResult<()> {
        self.vm.end_active_run_session();
        self.vm.begin_run_session();

        let py = gen.py();
        let (generator, get_frame, metadata) =
            self.extract_doeff_generator(py, gen, "start_with_generator")?;

        let marker = Marker::fresh();
        let installed_markers = self.vm.installed_handler_markers();
        let mut scope_chain = vec![marker];
        scope_chain.extend(installed_markers);

        let seg = Segment::new(marker, None, scope_chain);
        let seg_id = self.vm.alloc_segment(seg);
        self.vm.current_segment = Some(seg_id);

        if let Some(seg) = self.vm.current_segment_mut() {
            seg.push_frame(crate::frame::Frame::PythonGenerator {
                generator,
                get_frame,
                started: false,
                metadata: Some(metadata),
            });
        }
        self.vm.mode = Mode::Deliver(Value::Unit);
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
            PythonCall::StartProgram { program } => {
                // D5: Strict only — no callable fallback. Spec requires ProgramBase.
                match self.to_generator_strict(py, program.clone_ref(py)) {
                    Ok(gen) => Ok(PyCallOutcome::Value(Value::Python(gen))),
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                }
            }
            PythonCall::CallFunc { func, args, kwargs } => {
                let py_args = self.values_to_tuple(py, &args)?;
                if kwargs.is_empty() {
                    match func.bind(py).call1(py_args) {
                        Ok(result) => {
                            if Self::is_python_generator_object(&result)
                                || Self::is_doeff_generator_object(&result)
                            {
                                Ok(PyCallOutcome::Value(Value::Python(result.unbind())))
                            } else {
                                Ok(PyCallOutcome::Value(Value::from_pyobject(&result)))
                            }
                        }
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    }
                } else {
                    let py_kwargs = PyDict::new(py);
                    for (key, val) in &kwargs {
                        py_kwargs.set_item(key, val.to_pyobject(py)?)?;
                    }
                    match func.bind(py).call(py_args, Some(&py_kwargs)) {
                        Ok(result) => {
                            if Self::is_python_generator_object(&result)
                                || Self::is_doeff_generator_object(&result)
                            {
                                Ok(PyCallOutcome::Value(Value::Python(result.unbind())))
                            } else {
                                Ok(PyCallOutcome::Value(Value::from_pyobject(&result)))
                            }
                        }
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    }
                }
            }
            PythonCall::CallHandler {
                handler,
                effect,
                continuation,
            } => {
                let py_effect = dispatch_to_pyobject(py, &effect)?;
                let py_k = Bound::new(
                    py,
                    PyK {
                        cont_id: continuation.cont_id,
                    },
                )?
                .into_any();
                match handler.bind(py).call1((py_effect, py_k)) {
                    Ok(result) => match self.require_doeff_generator(
                        py,
                        result.unbind(),
                        "CallHandler(handler result)",
                    ) {
                        Ok(gen) => Ok(PyCallOutcome::Value(Value::Python(gen))),
                        Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                    },
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
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
            Some(PendingPython::StepUserGenerator { generator, .. }) => Ok(generator.clone_ref(py)),
            _ => Err(PyRuntimeError::new_err(
                "GenNext/GenSend/GenThrow: expected StepUserGenerator in pending_python",
            )),
        }
    }

    /// Strict boundary: accept only Rust runtime DoExpr bases.
    fn to_generator_strict(&self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
        let program = lift_effect_to_perform_expr(py, program)?;
        let program_bound = program.bind(py);

        if Self::is_doeff_generator_object(program_bound) {
            return Ok(program);
        }

        if Self::is_python_generator_object(program_bound) {
            let ty = program_bound
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "<unknown>".to_string());
            return Err(PyTypeError::new_err(
                format!(
                    "to_generator_strict(program): expected DoeffGenerator, got raw generator type {ty}"
                ),
            ));
        }

        if program_bound.is_instance_of::<PyDoExprBase>() {
            let gen = program_bound.call_method0("to_generator")?;
            return self.require_doeff_generator(py, gen.unbind(), "DoExpr.to_generator");
        }

        let is_nesting_step = program_bound
            .get_type()
            .name()
            .map(|n| n.to_string_lossy().as_ref() == "_NestingStep")
            .unwrap_or(false);
        if is_nesting_step || program_bound.is_instance_of::<NestingStep>() {
            let gen = program_bound.call_method0("to_generator")?;
            return self.require_doeff_generator(py, gen.unbind(), "NestingStep.to_generator");
        }

        let ty = program_bound
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "program must be DoExpr; got {ty}"
        )))
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

    fn classify_yielded(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<Yielded> {
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
                    let (handler, py_identity) = if handler_bound
                        .is_instance_of::<PyRustHandlerSentinel>()
                    {
                        let sentinel: PyRef<'_, PyRustHandlerSentinel> = handler_bound.extract()?;
                        (
                            Handler::RustProgram(sentinel.factory.clone()),
                            Some(PyShared::new(wh.handler.clone_ref(py))),
                        )
                    } else {
                        let mut python_handler = Handler::python_from_callable(handler_bound);
                        if let Handler::Python {
                            handler_name,
                            handler_file,
                            handler_line,
                            ..
                        } = &mut python_handler
                        {
                            if let Some(name) = &wh.handler_name {
                                *handler_name = name.clone();
                            }
                            if wh.handler_file.is_some() {
                                *handler_file = wh.handler_file.clone();
                            }
                            if wh.handler_line.is_some() {
                                *handler_line = wh.handler_line;
                            }
                        }
                        (python_handler, None)
                    };
                    Ok(Yielded::DoCtrl(DoCtrl::WithHandler {
                        handler,
                        expr: wh.expr.clone_ref(py),
                        py_identity,
                    }))
                }
                DoExprTag::Pure => {
                    let p: PyRef<'_, PyPure> = obj.extract()?;
                    Ok(Yielded::DoCtrl(DoCtrl::Pure {
                        value: Value::from_pyobject(p.value.bind(py)),
                    }))
                }
                DoExprTag::Call => {
                    let c: PyRef<'_, PyCall> = obj.extract()?;
                    let f = classify_call_arg(py, c.f.bind(py).as_any())?;
                    let mut args = Vec::new();
                    for item in c.args.bind(py).try_iter()? {
                        let item = item?;
                        args.push(classify_call_arg(py, item.as_any())?);
                    }
                    let kwargs_dict = c.kwargs.bind(py).cast::<PyDict>()?;
                    let mut kwargs = Vec::new();
                    for (k, v) in kwargs_dict.iter() {
                        let key = k.str()?.to_str()?.to_string();
                        kwargs.push((key, classify_call_arg(py, v.as_any())?));
                    }
                    Ok(Yielded::DoCtrl(DoCtrl::Call {
                        f,
                        args,
                        kwargs,
                        metadata: call_metadata_from_pycall(py, &c)?,
                    }))
                }
                DoExprTag::Map => {
                    let m: PyRef<'_, PyMap> = obj.extract()?;
                    Ok(Yielded::DoCtrl(DoCtrl::Map {
                        source: PyShared::new(m.source.clone_ref(py)),
                        mapper: PyShared::new(m.mapper.clone_ref(py)),
                        mapper_meta: call_metadata_from_meta_obj(m.mapper_meta.bind(py)),
                    }))
                }
                DoExprTag::FlatMap => {
                    let fm: PyRef<'_, PyFlatMap> = obj.extract()?;
                    Ok(Yielded::DoCtrl(DoCtrl::FlatMap {
                        source: PyShared::new(fm.source.clone_ref(py)),
                        binder: PyShared::new(fm.binder.clone_ref(py)),
                        binder_meta: call_metadata_from_meta_obj(fm.binder_meta.bind(py)),
                    }))
                }
                DoExprTag::Perform => {
                    let pf: PyRef<'_, PyPerform> = obj.extract()?;
                    Ok(Yielded::DoCtrl(DoCtrl::Perform {
                        effect: dispatch_from_shared(PyShared::new(pf.effect.clone_ref(py))),
                    }))
                }
                DoExprTag::Resume => {
                    let r: PyRef<'_, PyResume> = obj.extract()?;
                    let k_pyobj = r.continuation.bind(py).cast::<PyK>().map_err(|_| {
                        PyTypeError::new_err(
                            "Resume.continuation must be K (opaque continuation handle)",
                        )
                    })?;
                    let cont_id = k_pyobj.borrow().cont_id;
                    let k = self
                        .vm
                        .lookup_continuation(cont_id)
                        .cloned()
                        .ok_or_else(|| {
                            PyRuntimeError::new_err(format!(
                                "Resume with unknown continuation id {}",
                                cont_id.raw()
                            ))
                        })?;
                    Ok(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: k,
                        value: Value::from_pyobject(r.value.bind(py)),
                    }))
                }
                DoExprTag::Transfer => {
                    let t: PyRef<'_, PyTransfer> = obj.extract()?;
                    let k_pyobj = t.continuation.bind(py).cast::<PyK>().map_err(|_| {
                        PyTypeError::new_err(
                            "Transfer.continuation must be K (opaque continuation handle)",
                        )
                    })?;
                    let cont_id = k_pyobj.borrow().cont_id;
                    let k = self
                        .vm
                        .lookup_continuation(cont_id)
                        .cloned()
                        .ok_or_else(|| {
                            PyRuntimeError::new_err(format!(
                                "Transfer with unknown continuation id {}",
                                cont_id.raw()
                            ))
                        })?;
                    Ok(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k,
                        value: Value::from_pyobject(t.value.bind(py)),
                    }))
                }
                DoExprTag::Delegate => {
                    let d: PyRef<'_, PyDelegate> = obj.extract()?;
                    let effect = if let Some(ref eff) = d.effect {
                        dispatch_from_shared(PyShared::new(eff.clone_ref(py)))
                    } else {
                        self.vm
                            .dispatch_stack
                            .last()
                            .map(|ctx| ctx.effect.clone())
                            .ok_or_else(|| {
                                PyRuntimeError::new_err(
                                    "Delegate without effect called outside dispatch context",
                                )
                            })?
                    };
                    Ok(Yielded::DoCtrl(DoCtrl::Delegate { effect }))
                }
                DoExprTag::ResumeContinuation => {
                    let rc: PyRef<'_, PyResumeContinuation> = obj.extract()?;
                    let k_pyobj = rc.continuation.bind(py).cast::<PyK>().map_err(|_| {
                        PyTypeError::new_err("ResumeContinuation.continuation must be K (opaque continuation handle)")
                    })?;
                    let cont_id = k_pyobj.borrow().cont_id;
                    let k = self
                        .vm
                        .lookup_continuation(cont_id)
                        .cloned()
                        .ok_or_else(|| {
                            PyRuntimeError::new_err(format!(
                                "ResumeContinuation with unknown continuation id {}",
                                cont_id.raw()
                            ))
                        })?;
                    Ok(Yielded::DoCtrl(DoCtrl::ResumeContinuation {
                        continuation: k,
                        value: Value::from_pyobject(rc.value.bind(py)),
                    }))
                }
                DoExprTag::CreateContinuation => {
                    let cc: PyRef<'_, PyCreateContinuation> = obj.extract()?;
                    let program = cc.program.clone_ref(py);
                    let handlers_list = cc.handlers.bind(py);
                    let mut handlers = Vec::new();
                    let mut handler_identities = Vec::new();
                    for item in handlers_list.try_iter()? {
                        let item = item?;
                        if item.is_instance_of::<PyRustHandlerSentinel>() {
                            let sentinel: PyRef<'_, PyRustHandlerSentinel> = item.extract()?;
                            handlers.push(Handler::RustProgram(sentinel.factory.clone()));
                            handler_identities.push(Some(PyShared::new(item.unbind())));
                        } else {
                            handlers.push(Handler::python_from_callable(&item));
                            handler_identities.push(None);
                        }
                    }
                    Ok(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                        expr: PyShared::new(program),
                        handlers,
                        handler_identities,
                    }))
                }
                DoExprTag::GetContinuation => Ok(Yielded::DoCtrl(DoCtrl::GetContinuation)),
                DoExprTag::GetHandlers => Ok(Yielded::DoCtrl(DoCtrl::GetHandlers)),
                DoExprTag::GetCallStack => Ok(Yielded::DoCtrl(DoCtrl::GetCallStack)),
                DoExprTag::GetTrace => Ok(Yielded::DoCtrl(DoCtrl::GetTrace)),
                DoExprTag::Eval => {
                    let eval: PyRef<'_, PyEval> = obj.extract()?;
                    let expr = eval.expr.clone_ref(py);
                    let handlers_list = eval.handlers.bind(py);
                    let mut handlers = Vec::new();
                    for item in handlers_list.try_iter()? {
                        let item = item?;
                        if item.is_instance_of::<PyRustHandlerSentinel>() {
                            let sentinel: PyRef<'_, PyRustHandlerSentinel> = item.extract()?;
                            handlers.push(Handler::RustProgram(sentinel.factory.clone()));
                        } else {
                            handlers.push(Handler::python_from_callable(&item));
                        }
                    }
                    Ok(Yielded::DoCtrl(DoCtrl::Eval {
                        expr: PyShared::new(expr),
                        handlers,
                        metadata: None,
                    }))
                }
                DoExprTag::AsyncEscape => {
                    let ae: PyRef<'_, PyAsyncEscape> = obj.extract()?;
                    Ok(Yielded::DoCtrl(DoCtrl::PythonAsyncSyntaxEscape {
                        action: ae.action.clone_ref(py),
                    }))
                }
                DoExprTag::Effect | DoExprTag::Unknown => {
                    // Unknown tag on a DoCtrlBase — treat as error
                    Err(PyTypeError::new_err(
                        "yielded DoCtrlBase has unrecognized tag",
                    ))
                }
            };
        }

        // Fallback: bare effect → auto-lift to Perform (R14-C)
        if is_effect_base_like(py, obj)? {
            if obj.is_instance_of::<PyProgramTrace>() {
                return Ok(Yielded::DoCtrl(DoCtrl::GetTrace));
            }
            if obj.is_instance_of::<PyProgramCallStack>() {
                return Ok(Yielded::DoCtrl(DoCtrl::GetCallStack));
            }
            return Ok(Yielded::DoCtrl(DoCtrl::Perform {
                effect: dispatch_from_shared(PyShared::new(obj.clone().unbind())),
            }));
        }

        Err(PyTypeError::new_err(
            "yielded value must be EffectBase or DoExpr",
        ))
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
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(StateHandlerFactory)),
                    prompt_seg_id,
                ),
            );
        }
    }

    pub fn install_reader(&self, vm: &mut PyVM) {
        if let Some(marker) = self.reader_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(ReaderHandlerFactory)),
                    prompt_seg_id,
                ),
            );
        }
    }

    pub fn install_writer(&self, vm: &mut PyVM) {
        if let Some(marker) = self.writer_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(WriterHandlerFactory)),
                    prompt_seg_id,
                ),
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
                HandlerEntry::new(
                    Handler::RustProgram(std::sync::Arc::new(self.handler.clone())),
                    prompt_seg_id,
                ),
            );
        }
    }
}

fn pyerr_to_exception(py: Python<'_>, e: PyErr) -> PyResult<PyException> {
    let exc_type = e.get_type(py).into_any().unbind();
    let exc_value = e.value(py).clone().into_any().unbind();
    let exc_tb = e.traceback(py).map(|tb| tb.into_any().unbind());
    let exc = PyException::new(exc_type, exc_value, exc_tb);
    crate::scheduler::preserve_exception_origin(&exc);
    Ok(exc)
}

fn extract_stop_iteration_value(py: Python<'_>, e: &PyErr) -> PyResult<Value> {
    let value = e.value(py).getattr("value")?;
    Ok(Value::from_pyobject(&value))
}

fn metadata_attr_as_string(meta: &Bound<'_, PyAny>, key: &str) -> Option<String> {
    if let Ok(dict) = meta.cast::<PyDict>() {
        return dict
            .get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<String>().ok());
    }
    meta.getattr(key)
        .ok()
        .and_then(|v| v.extract::<String>().ok())
}

fn metadata_attr_as_u32(meta: &Bound<'_, PyAny>, key: &str) -> Option<u32> {
    if let Ok(dict) = meta.cast::<PyDict>() {
        return dict
            .get_item(key)
            .ok()
            .flatten()
            .and_then(|v| v.extract::<u32>().ok());
    }
    meta.getattr(key).ok().and_then(|v| v.extract::<u32>().ok())
}

fn metadata_attr_as_py(meta: &Bound<'_, PyAny>, key: &str) -> Option<PyShared> {
    if let Ok(dict) = meta.cast::<PyDict>() {
        return dict.get_item(key).ok().flatten().and_then(|v| {
            if v.is_none() {
                None
            } else {
                Some(PyShared::new(v.unbind()))
            }
        });
    }
    meta.getattr(key).ok().and_then(|v| {
        if v.is_none() {
            None
        } else {
            Some(PyShared::new(v.unbind()))
        }
    })
}

fn callable_diagnostic_label(callable: &Bound<'_, PyAny>) -> String {
    let type_name = callable
        .get_type()
        .name()
        .ok()
        .map(|name| name.to_string())
        .unwrap_or_else(|| "<unknown>".to_string());
    let repr = callable
        .repr()
        .ok()
        .and_then(|r| r.to_str().ok().map(|s| s.to_string()))
        .unwrap_or_else(|| "<unrepresentable callable>".to_string());
    format!("{repr} [type={type_name}]")
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

fn call_metadata_from_callable(callable: &Bound<'_, PyAny>) -> PyResult<CallMetadata> {
    if let Ok(code) = callable.getattr("__code__") {
        let function_name = callable
            .getattr("__name__")
            .ok()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_else(|| "<anonymous>".to_string());
        let source_file = code
            .getattr("co_filename")
            .ok()
            .and_then(|v| v.extract::<String>().ok())
            .unwrap_or_else(|| "<unknown>".to_string());
        let source_line = code
            .getattr("co_firstlineno")
            .ok()
            .and_then(|v| v.extract::<u32>().ok())
            .unwrap_or(0);
        return Ok(CallMetadata::new(
            function_name,
            source_file,
            source_line,
            None,
            None,
        ));
    }

    Err(PyTypeError::new_err(format!(
        "Cannot derive call metadata: callable {} lacks __code__. \
Provide explicit metadata with function_name/source_file/source_line.",
        callable_diagnostic_label(callable)
    )))
}

fn call_metadata_from_pycall(py: Python<'_>, call: &PyRef<'_, PyCall>) -> PyResult<CallMetadata> {
    if let Some(meta) = &call.meta {
        return Ok(call_metadata_from_meta_obj(meta.bind(py)));
    }

    let f_obj = call.f.bind(py);
    if let Ok(pure) = f_obj.extract::<PyRef<'_, PyPure>>() {
        let value = pure.value.bind(py);
        if value.is_callable() {
            return call_metadata_from_callable(value);
        }
    }
    if f_obj.is_callable() {
        return call_metadata_from_callable(f_obj);
    }
    Err(PyTypeError::new_err(format!(
        "Cannot derive call metadata from Call.f {}. \
Supply Call(..., meta={{function_name, source_file, source_line}}).",
        callable_diagnostic_label(f_obj)
    )))
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
        if !(handler_obj.is_instance_of::<PyRustHandlerSentinel>() || handler_obj.is_callable()) {
            return Err(PyTypeError::new_err(
                "WithHandler.handler must be callable or built-in handler sentinel",
            ));
        }

        let expr = lift_effect_to_perform_expr(py, expr)?;

        let expr_obj = expr.bind(py);
        if !expr_obj.is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("WithHandler.expr must be DoExpr"));
        }

        let (resolved_handler_name, resolved_handler_file, resolved_handler_line) =
            if handler_obj.is_instance_of::<PyRustHandlerSentinel>() {
                (None, None, None)
            } else {
                let mut derived_name = None;
                let mut derived_file = None;
                let mut derived_line = None;
                if let Handler::Python {
                    handler_name,
                    handler_file,
                    handler_line,
                    ..
                } = Handler::python_from_callable(handler_obj)
                {
                    derived_name = Some(handler_name);
                    derived_file = handler_file;
                    derived_line = handler_line;
                }
                (
                    handler_name.or(derived_name),
                    handler_file.or(derived_file),
                    handler_line.or(derived_line),
                )
            };

        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::WithHandler as u8,
            })
            .add_subclass(PyWithHandler {
                handler,
                expr,
                handler_name: resolved_handler_name,
                handler_file: resolved_handler_file,
                handler_line: resolved_handler_line,
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

#[pyclass(name = "Call", extends=PyDoCtrlBase)]
pub struct PyCall {
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
impl PyCall {
    #[new]
    #[pyo3(signature = (f, args, kwargs, meta=None))]
    fn new(
        py: Python<'_>,
        f: Py<PyAny>,
        args: Py<PyAny>,
        kwargs: Py<PyAny>,
        meta: Option<Py<PyAny>>,
    ) -> PyResult<PyClassInitializer<Self>> {
        if !f.bind(py).is_instance_of::<PyDoExprBase>() {
            return Err(PyTypeError::new_err("Call.f must be DoExpr"));
        }
        if args.bind(py).try_iter().is_err() {
            return Err(PyTypeError::new_err("Call.args must be iterable"));
        }
        for item in args.bind(py).try_iter()? {
            let item = item?;
            if !item.is_instance_of::<PyDoExprBase>() {
                return Err(PyTypeError::new_err("Call.args values must be DoExpr"));
            }
        }
        if !kwargs.bind(py).is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err("Call.kwargs must be dict"));
        }
        let kwargs_dict = kwargs.bind(py).cast::<PyDict>()?;
        for (_, value) in kwargs_dict.iter() {
            if !value.is_instance_of::<PyDoExprBase>() {
                return Err(PyTypeError::new_err("Call.kwargs values must be DoExpr"));
            }
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Call as u8,
            })
            .add_subclass(PyCall {
                f,
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
pub struct PyDelegate {
    #[pyo3(get)]
    pub effect: Option<Py<PyAny>>,
}

#[pymethods]
impl PyDelegate {
    #[new]
    #[pyo3(signature = (effect=None))]
    fn new(py: Python<'_>, effect: Option<Py<PyAny>>) -> PyResult<PyClassInitializer<Self>> {
        if let Some(ref eff) = effect {
            if !is_effect_base_like(py, eff.bind(py))? {
                return Err(PyTypeError::new_err(
                    "Delegate.effect must be EffectBase when provided",
                ));
            }
        }
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Delegate as u8,
            })
            .add_subclass(PyDelegate { effect }))
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
    pub(crate) factory: RustProgramHandlerRef,
}

impl PyRustHandlerSentinel {
    pub(crate) fn factory_ref(&self) -> RustProgramHandlerRef {
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
        let (handler_name, handler_file, handler_line) =
            if handler.bind(py).is_instance_of::<PyRustHandlerSentinel>() {
                (None, None, None)
            } else {
                match Handler::python_from_callable(handler.bind(py)) {
                    Handler::Python {
                        handler_name,
                        handler_file,
                        handler_line,
                        ..
                    } => (Some(handler_name), handler_file, handler_line),
                    Handler::RustProgram(_) => (None, None, None),
                }
            };
        let wh = PyWithHandler {
            handler,
            expr: inner,
            handler_name,
            handler_file,
            handler_line,
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
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    __doeff_scheduler_task_completed__ = True\n    def __init__(self, tid, value):\n        self.task = _TaskHandle(tid)\n        self.result = value\n\nobj = TaskCompletedEffect(7, 123)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();

            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                Yielded::DoCtrl(DoCtrl::CreateContinuation { handlers, .. }) => {
                    assert!(
                        matches!(
                            handlers.first(),
                            Some(crate::handler::Handler::RustProgram(_))
                        ),
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
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    __doeff_scheduler_task_completed__ = True\n    def __init__(self, tid, err):\n        self.task = _TaskHandle(tid)\n        self.error = err\n\nobj = TaskCompletedEffect(9, ValueError('boom'))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                c"class GatherEffect(EffectBase):\n    __doeff_scheduler_gather__ = True\n    def __init__(self):\n        self.items = [123]\nobj = GatherEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                c"class _Future:\n    def __init__(self):\n        self._handle = {'type': 'Task', 'task_id': 1}\n\nclass WaitEffect(EffectBase):\n    __doeff_scheduler_wait__ = True\n    def __init__(self):\n        self.future = _Future()\n\nobj = WaitEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                c"class SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self, p, hs, mode):\n        self.program = p\n        self.handlers = hs\n        self.store_mode = mode\nobj = SpawnEffect(None, [sentinel], 'isolated')\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                matches!(yielded, Yielded::DoCtrl(DoCtrl::GetCallStack)),
                "GetCallStack must classify to DoCtrl::GetCallStack, got {:?}",
                yielded
            );
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
    fn test_vm_proto_to_generator_strict_rejects_raw_generator() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"def _raw_gen():\n    yield 1\nraw = _raw_gen()\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to create raw generator");
            let raw = locals
                .get_item("raw")
                .expect("locals.get_item failed")
                .expect("raw generator missing");
            let result = pyvm.to_generator_strict(py, raw.unbind());
            assert!(
                result.is_err(),
                "VM-PROTO-001: to_generator_strict must reject raw generators (require DoeffGenerator), got {:?}",
                result
            );
            let msg = result.err().expect("expected TypeError").to_string();
            assert!(
                msg.contains("DoeffGenerator"),
                "VM-PROTO-001: rejection error should mention DoeffGenerator, got {msg}"
            );
        });
    }

    #[test]
    fn test_vm_proto_to_generator_strict_rejects_program_to_generator_returning_raw_generator() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let module = pyo3::types::PyModule::new(py, "vm_proto_test_mod")
                .expect("failed to allocate vm_proto_test_mod");
            doeff_vm(&module).expect("failed to init vm module");
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("vm", &module)
                .expect("failed to bind vm module");
            py.run(
                c"class ProgramLike(vm.DoExpr):\n    def to_generator(self):\n        def _raw_gen():\n            yield 1\n        return _raw_gen()\n\nobj = ProgramLike()\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to create ProgramLike");
            let obj = locals
                .get_item("obj")
                .expect("locals.get_item failed")
                .expect("obj missing");
            let result = pyvm.to_generator_strict(py, obj.unbind());
            assert!(
                result.is_err(),
                "VM-PROTO-001: to_generator_strict must reject Program.to_generator() raw generator returns, got {:?}",
                result
            );
            let msg = result.err().expect("expected TypeError").to_string();
            assert!(
                msg.contains("DoeffGenerator") && msg.contains("DoExpr.to_generator"),
                "VM-PROTO-001: error should mention DoeffGenerator and DoExpr.to_generator boundary, got {msg}"
            );
        });
    }

    #[test]
    fn test_vm_proto_start_with_generator_uses_doeff_generator_extraction() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pyvm.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("extract_doeff_generator("),
            "VM-PROTO-001: pyvm start path must extract fields from DoeffGenerator"
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
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
                c"class SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self):\n        self.program = None\n        self.handlers = []\n        self.store_mode = 'shared'\n\nobj = SpawnEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Perform { .. })),
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
            // Pure
            let obj = Bound::new(py, PyPure::new(py.None().into()))
                .unwrap()
                .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::Pure as u8);

            // Call
            let kernel = py.eval(c"lambda: None", None, None).unwrap().unbind();
            let f = Bound::new(py, PyPure::new(kernel))
                .unwrap()
                .into_any()
                .unbind();
            let args = pyo3::types::PyTuple::empty(py).into_any().unbind();
            let kwargs = PyDict::new(py).into_any().unbind();
            let obj = Bound::new(py, PyCall::new(py, f, args, kwargs, None).unwrap())
                .unwrap()
                .into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::Call as u8);

            // GetContinuation
            let obj = Bound::new(py, PyGetContinuation::new()).unwrap().into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetContinuation as u8);

            // GetHandlers
            let obj = Bound::new(py, PyGetHandlers::new()).unwrap().into_any();
            let base: PyRef<'_, PyDoCtrlBase> = obj.extract().unwrap();
            assert_eq!(base.tag, DoExprTag::GetHandlers as u8);

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
            let pyvm = PyVM { vm: VM::new() };

            // Pure → DoCtrl::Pure
            let pure_obj = Bound::new(
                py,
                PyPure::new(42i64.into_pyobject(py).unwrap().into_any().unbind()),
            )
            .unwrap()
            .into_any();
            let yielded = pyvm.classify_yielded(py, &pure_obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::Pure { .. })),
                "Pure tag dispatch failed, got {:?}",
                yielded
            );

            // GetHandlers → DoCtrl::GetHandlers
            let gh_obj = Bound::new(py, PyGetHandlers::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &gh_obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::GetHandlers)),
                "GetHandlers tag dispatch failed, got {:?}",
                yielded
            );

            // GetCallStack → DoCtrl::GetCallStack
            let gcs_obj = Bound::new(py, PyGetCallStack::new()).unwrap().into_any();
            let yielded = pyvm.classify_yielded(py, &gcs_obj).unwrap();
            assert!(
                matches!(yielded, Yielded::DoCtrl(DoCtrl::GetCallStack)),
                "GetCallStack tag dispatch failed, got {:?}",
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
            let (handler_name, handler_file, handler_line) =
                if handler_obj.is_instance_of::<PyRustHandlerSentinel>() {
                    (None, None, None)
                } else {
                    match Handler::python_from_callable(&handler_obj) {
                        Handler::Python {
                            handler_name,
                            handler_file,
                            handler_line,
                            ..
                        } => (Some(handler_name), handler_file, handler_line),
                        Handler::RustProgram(_) => (None, None, None),
                    }
                };
            let wh = PyWithHandler {
                handler: handler_obj.unbind(),
                expr: wrapped,
                handler_name,
                handler_file,
                handler_line,
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
            let (handler_name, handler_file, handler_line) =
                if handler_obj.is_instance_of::<PyRustHandlerSentinel>() {
                    (None, None, None)
                } else {
                    match Handler::python_from_callable(&handler_obj) {
                        Handler::Python {
                            handler_name,
                            handler_file,
                            handler_line,
                            ..
                        } => (Some(handler_name), handler_file, handler_line),
                        Handler::RustProgram(_) => (None, None, None),
                    }
                };
            let wh = PyWithHandler {
                handler: handler_obj.unbind(),
                expr: wrapped,
                handler_name,
                handler_file,
                handler_line,
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

    let gen = vm.to_generator_strict(py, wrapped)?;
    let gen_bound = gen.bind(py).clone();
    vm.start_with_generator(gen_bound)?;

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
    m.add_class::<PyWithHandler>()?;
    m.add_class::<PyPure>()?;
    m.add_class::<PyCall>()?;
    m.add_class::<PyMap>()?;
    m.add_class::<PyFlatMap>()?;
    m.add_class::<PyEval>()?;
    m.add_class::<PyPerform>()?;
    m.add_class::<PyResume>()?;
    m.add_class::<PyDelegate>()?;
    m.add_class::<PyTransfer>()?;
    m.add_class::<PyResumeContinuation>()?;
    m.add_class::<PyCreateContinuation>()?;
    m.add_class::<PyGetContinuation>()?;
    m.add_class::<PyGetHandlers>()?;
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
    m.add("TAG_CALL", DoExprTag::Call as u8)?;
    m.add("TAG_MAP", DoExprTag::Map as u8)?;
    m.add("TAG_FLAT_MAP", DoExprTag::FlatMap as u8)?;
    m.add("TAG_WITH_HANDLER", DoExprTag::WithHandler as u8)?;
    m.add("TAG_PERFORM", DoExprTag::Perform as u8)?;
    m.add("TAG_RESUME", DoExprTag::Resume as u8)?;
    m.add("TAG_TRANSFER", DoExprTag::Transfer as u8)?;
    m.add("TAG_DELEGATE", DoExprTag::Delegate as u8)?;
    m.add("TAG_GET_CONTINUATION", DoExprTag::GetContinuation as u8)?;
    m.add("TAG_GET_HANDLERS", DoExprTag::GetHandlers as u8)?;
    m.add("TAG_GET_CALL_STACK", DoExprTag::GetCallStack as u8)?;
    m.add("TAG_GET_TRACE", DoExprTag::GetTrace as u8)?;
    m.add("TAG_EVAL", DoExprTag::Eval as u8)?;
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

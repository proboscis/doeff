use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::effect::{Effect, KpcArg, KpcCallEffect};
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    Handler, HandlerEntry, KpcHandlerFactory, ReaderHandlerFactory, RustProgramHandlerRef, StateHandlerFactory,
    WriterHandlerFactory,
};
use crate::ids::{ContId, Marker};
use crate::py_shared::PyShared;
use crate::scheduler::{SchedulerEffect, SchedulerHandler, StoreMergePolicy, StoreMode};
use crate::segment::Segment;
use crate::step::{
    DoCtrl, Mode, PendingPython, PyCallOutcome, PyException, PythonCall, StepEvent, Yielded,
};
use crate::value::Value;
use crate::vm::VM;

fn vmerror_to_pyerr(e: VMError) -> PyErr {
    match e {
        VMError::TypeError { .. } => PyTypeError::new_err(e.to_string()),
        VMError::UncaughtException { exception } => {
            // SAFETY: vmerror_to_pyerr is always called from GIL-holding contexts (run/step_once)
            let py = unsafe { Python::assume_attached() };
            exception.to_pyerr(py)
        }
        _ => PyRuntimeError::new_err(e.to_string()),
    }
}

#[pyclass]
pub struct PyVM {
    vm: VM,
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
                    return value.to_pyobject(py).map(|v| v.unbind());
                }
                StepEvent::Error(e) => {
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

        let result = loop {
            let event = py.detach(|| self.run_rust_steps());
            match event {
                StepEvent::Done(value) => match value.to_pyobject(py) {
                    Ok(v) => break Ok(v.unbind()),
                    Err(e) => {
                        let exc = pyerr_to_exception(py, e)?;
                        break Err(exc);
                    }
                },
                StepEvent::Error(e) => {
                    let pyerr = vmerror_to_pyerr(e);
                    let exc = pyerr_to_exception(py, pyerr)?;
                    break Err(exc);
                }
                StepEvent::NeedsPython(call) => {
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                }
                StepEvent::Continue => unreachable!("handled in run_rust_steps"),
            }
        };

        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }

        Ok(PyRunResult {
            result,
            raw_store: raw_store.unbind(),
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

    pub fn put_env(&mut self, key: String, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.vm
            .rust_store
            .env
            .insert(key, Value::from_pyobject(value));
        Ok(())
    }

    pub fn env_items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.env {
            dict.set_item(k, v.to_pyobject(py)?)?;
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

    pub fn build_run_result(
        &self,
        py: Python<'_>,
        value: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(PyRunResult {
            result: Ok(value.unbind()),
            raw_store: raw_store.unbind(),
        })
    }

    pub fn build_run_result_error(
        &self,
        py: Python<'_>,
        error: Bound<'_, PyAny>,
    ) -> PyResult<PyRunResult> {
        let raw_store = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            raw_store.set_item(k, v.to_pyobject(py)?)?;
        }
        let exc = pyerr_to_exception(py, PyErr::from_value(error))?;
        Ok(PyRunResult {
            result: Err(exc),
            raw_store: raw_store.unbind(),
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
                let py_val = value.to_pyobject(py)?;
                let elems: Vec<Bound<'_, pyo3::PyAny>> =
                    vec!["done".into_pyobject(py)?.into_any(), py_val];
                let tuple = PyTuple::new(py, elems)?;
                Ok(tuple.into())
            }
            StepEvent::Error(e) => Err(vmerror_to_pyerr(e)),
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
    fn start_with_generator(&mut self, gen: Bound<'_, PyAny>) -> PyResult<()> {
        let marker = Marker::fresh();
        let installed_markers = self.vm.installed_handler_markers();
        let mut scope_chain = vec![marker];
        scope_chain.extend(installed_markers);

        let seg = Segment::new(marker, None, scope_chain);
        let seg_id = self.vm.alloc_segment(seg);
        self.vm.current_segment = Some(seg_id);

        if let Some(seg) = self.vm.current_segment_mut() {
            seg.push_frame(crate::frame::Frame::PythonGenerator {
                generator: PyShared::new(gen.unbind()),
                started: false,
                metadata: None,
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
                let gen = self.to_generator_strict(py, program.clone_ref(py))?;
                Ok(PyCallOutcome::Value(Value::Python(gen)))
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
            PythonCall::CallHandler {
                handler,
                effect,
                continuation,
            } => {
                let py_effect = effect.to_pyobject(py)?;
                let py_k = continuation.to_pyobject(py)?;
                match handler.bind(py).call1((py_effect, py_k)) {
                    Ok(result) => {
                        let gen = self.to_generator_strict(py, result.unbind())?;
                        Ok(PyCallOutcome::Value(Value::Python(gen)))
                    }
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
            PythonCall::CallAsync { .. } => Err(pyo3::exceptions::PyTypeError::new_err(
                "CallAsync requires async_run (PythonAsyncSyntaxEscape not supported in sync mode)",
            )),
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

    fn to_generator_strict(&self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
        let program_bound = program.bind(py);
        let type_name = program_bound.get_type().name()?;
        if type_name.to_string().contains("generator") {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "Expected ProgramBase (with to_generator method), got raw generator. \
                 Yield a ProgramBase object (e.g. decorated with @do) instead of a raw generator.",
            ));
        }
        let to_gen = program_bound.getattr("to_generator")?;
        let gen = to_gen.call0()?;
        Ok(gen.unbind())
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

    fn classify_yielded(&self, _py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<Yielded> {
        // R8-C: Check for Rust pyclass primitives first (fast isinstance check)
        if obj.is_instance_of::<PyWithHandler>() {
            let wh: PyRef<'_, PyWithHandler> = obj.extract()?;
            let handler_bound = wh.handler.bind(_py);
            let (handler, py_identity) = if handler_bound.is_instance_of::<PyRustHandlerSentinel>() {
                let sentinel: PyRef<'_, PyRustHandlerSentinel> = handler_bound.extract()?;
                (
                    Handler::RustProgram(sentinel.factory.clone()),
                    Some(PyShared::new(wh.handler.clone_ref(_py))),
                )
            } else {
                (Handler::Python(PyShared::new(wh.handler.clone_ref(_py))), None)
            };
            return Ok(Yielded::DoCtrl(DoCtrl::WithHandler {
                handler,
                expr: wh.program.clone_ref(_py),
                py_identity,
            }));
        }
        if obj.is_instance_of::<PyResume>() {
            let r: PyRef<'_, PyResume> = obj.extract()?;
            if let Ok(k_pyobj) = r.continuation.bind(_py).cast::<PyK>() {
                let cont_id = k_pyobj.borrow().cont_id;
                if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                    return Ok(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: k,
                        value: Value::from_pyobject(r.value.bind(_py)),
                    }));
                }
                return Err(PyRuntimeError::new_err(format!(
                    "Resume with unknown continuation id {}",
                    cont_id.raw()
                )));
            }
            return Err(PyTypeError::new_err(
                "Resume.continuation must be K (opaque continuation handle)",
            ));
        }
        if obj.is_instance_of::<PyTransfer>() {
            let t: PyRef<'_, PyTransfer> = obj.extract()?;
            if let Ok(k_pyobj) = t.continuation.bind(_py).cast::<PyK>() {
                let cont_id = k_pyobj.borrow().cont_id;
                if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                    return Ok(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k,
                        value: Value::from_pyobject(t.value.bind(_py)),
                    }));
                }
                return Err(PyRuntimeError::new_err(format!(
                    "Transfer with unknown continuation id {}",
                    cont_id.raw()
                )));
            }
            return Err(PyTypeError::new_err(
                "Transfer.continuation must be K (opaque continuation handle)",
            ));
        }
        if obj.is_instance_of::<PyDelegate>() {
            let d: PyRef<'_, PyDelegate> = obj.extract()?;
            let effect = if let Some(ref eff) = d.effect {
                Effect::Python(PyShared::new(eff.clone_ref(_py)))
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
            return Ok(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
        }

        // Fallback: string-based type name parsing (for Python dataclasses)
        if let Ok(type_name) = obj.get_type().name() {
            let type_str: &str = type_name.extract()?;
            if self.vm.debug.is_enabled() {
                eprintln!("[classify_yielded] type_str = {:?}", type_str);
            }
            match type_str {
                "Resume" => {
                    let k_obj = obj.getattr("continuation")?;
                    let cont_id_raw = k_obj
                        .getattr("cont_id")
                        .or_else(|_| k_obj.get_item("cont_id"))?;
                    let cont_id_val = cont_id_raw.extract::<u64>()?;
                    let cont_id = ContId::from_raw(cont_id_val);
                    let k = self.vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                        PyRuntimeError::new_err(format!(
                            "Resume with unknown continuation id {}",
                            cont_id.raw()
                        ))
                    })?;
                    let value = obj.getattr("value")?;
                    return Ok(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: k,
                        value: Value::from_pyobject(&value),
                    }));
                }
                "Transfer" => {
                    let k_obj = obj.getattr("continuation")?;
                    let cont_id_raw = k_obj
                        .getattr("cont_id")
                        .or_else(|_| k_obj.get_item("cont_id"))?;
                    let cont_id_val = cont_id_raw.extract::<u64>()?;
                    let cont_id = ContId::from_raw(cont_id_val);
                    let k = self.vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                        PyRuntimeError::new_err(format!(
                            "Transfer with unknown continuation id {}",
                            cont_id.raw()
                        ))
                    })?;
                    let value = obj.getattr("value")?;
                    return Ok(Yielded::DoCtrl(DoCtrl::Transfer {
                        continuation: k,
                        value: Value::from_pyobject(&value),
                    }));
                }
                "WithHandler" => {
                    let handler_obj = obj.getattr("handler")?;
                    let program = obj.getattr("program").or_else(|_| obj.getattr("body"))?;
                    let (handler, py_identity) =
                        if handler_obj.is_instance_of::<PyRustHandlerSentinel>() {
                            let sentinel: PyRef<'_, PyRustHandlerSentinel> = handler_obj.extract()?;
                            (
                                Handler::RustProgram(sentinel.factory.clone()),
                                Some(PyShared::new(handler_obj.clone().unbind())),
                            )
                        } else {
                            (Handler::Python(PyShared::new(handler_obj.unbind())), None)
                        };
                    return Ok(Yielded::DoCtrl(DoCtrl::WithHandler {
                        handler,
                        expr: program.unbind(),
                        py_identity,
                    }));
                }
                "Delegate" => {
                    let effect = if let Ok(eff_obj) = obj.getattr("effect") {
                        if !eff_obj.is_none() {
                            Effect::Python(PyShared::new(eff_obj.unbind()))
                        } else {
                            // No explicit effect — use current dispatch effect
                            self.vm
                                .dispatch_stack
                                .last()
                                .map(|ctx| ctx.effect.clone())
                                .ok_or_else(|| {
                                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                                        "Delegate without effect called outside dispatch context",
                                    )
                                })?
                        }
                    } else {
                        // No effect attribute — use current dispatch effect
                        self.vm
                            .dispatch_stack
                            .last()
                            .map(|ctx| ctx.effect.clone())
                            .ok_or_else(|| {
                                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                                    "Delegate without effect called outside dispatch context",
                                )
                            })?
                    };
                    return Ok(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
                }
                "GetContinuation" => {
                    return Ok(Yielded::DoCtrl(DoCtrl::GetContinuation));
                }
                "GetHandlers" => {
                    return Ok(Yielded::DoCtrl(DoCtrl::GetHandlers));
                }
                "CreateContinuation" => {
                    let program = obj.getattr("program")?.unbind();
                    let handlers_list = obj.getattr("handlers")?;
                    let mut handlers = Vec::new();
                    let mut handler_identities = Vec::new();
                    for item in handlers_list.try_iter()? {
                        let item = item?;
                        if item.is_instance_of::<PyRustHandlerSentinel>() {
                            let sentinel: PyRef<'_, PyRustHandlerSentinel> = item.extract()?;
                            handlers.push(crate::handler::Handler::RustProgram(
                                sentinel.factory.clone(),
                            ));
                            handler_identities.push(Some(PyShared::new(item.unbind())));
                        } else {
                            handlers.push(crate::handler::Handler::Python(PyShared::new(
                                item.unbind(),
                            )));
                            handler_identities.push(None);
                        }
                    }
                    return Ok(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                        expr: PyShared::new(program),
                        handlers,
                        handler_identities,
                    }));
                }
                "ResumeContinuation" => {
                    let k_obj = obj.getattr("continuation")?;
                    let cont_id_raw = k_obj
                        .getattr("cont_id")
                        .or_else(|_| k_obj.get_item("cont_id"))?;
                    let cont_id_val = cont_id_raw.extract::<u64>()?;
                    let cont_id = ContId::from_raw(cont_id_val);
                    let k = self.vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                        PyRuntimeError::new_err(format!(
                            "ResumeContinuation with unknown continuation id {}",
                            cont_id.raw()
                        ))
                    })?;
                    let value = obj.getattr("value")?;
                    return Ok(Yielded::DoCtrl(DoCtrl::ResumeContinuation {
                        continuation: k,
                        value: Value::from_pyobject(&value),
                    }));
                }
                "StateGetEffect" | "Get" => {
                    let key: String = obj.getattr("key")?.extract()?;
                    return Ok(Yielded::Effect(Effect::Get { key }));
                }
                "StatePutEffect" | "Put" => {
                    let key: String = obj.getattr("key")?.extract()?;
                    let value = obj.getattr("value")?;
                    return Ok(Yielded::Effect(Effect::Put {
                        key,
                        value: Value::from_pyobject(&value),
                    }));
                }
                "StateModifyEffect" | "Modify" => {
                    let key: String = obj.getattr("key")?.extract()?;
                    let modifier = obj.getattr("func")?;
                    return Ok(Yielded::Effect(Effect::Modify {
                        key,
                        modifier: PyShared::new(modifier.unbind()),
                    }));
                }
                "AskEffect" | "Ask" => {
                    let key: String = obj.getattr("key")?.extract()?;
                    return Ok(Yielded::Effect(Effect::Ask { key }));
                }
                "WriterTellEffect" | "Tell" => {
                    let message = obj.getattr("message")?;
                    return Ok(Yielded::Effect(Effect::Tell {
                        message: Value::from_pyobject(&message),
                    }));
                }
                "CreatePromise" | "SchedulerCreatePromise" => {
                    return Ok(Yielded::Effect(Effect::Scheduler(
                        SchedulerEffect::CreatePromise,
                    )));
                }
                "CreateExternalPromise" | "SchedulerCreateExternalPromise" => {
                    return Ok(Yielded::Effect(Effect::Scheduler(
                        SchedulerEffect::CreateExternalPromise,
                    )));
                }
                "PythonAsyncSyntaxEscape" => {
                    let action = obj.getattr("action")?.unbind();
                    return Ok(Yielded::DoCtrl(DoCtrl::PythonAsyncSyntaxEscape { action }));
                }
                "KleisliProgramCall" => {
                    let kpc = Self::extract_kpc_effect(_py, obj)?;
                    return Ok(Yielded::Effect(Effect::KpcCall(kpc)));
                }
                // D6: Scheduler effects — extract fields into typed SchedulerEffect variants.
                // Must be classified BEFORE to_generator fallback (EffectBase has to_generator).
                "SpawnEffect" | "SchedulerSpawn" => {
                    let program = obj.getattr("program")?.unbind();
                    let handlers = if let Ok(handlers_obj) = obj.getattr("handlers") {
                        Self::extract_handlers_from_python(_py, &handlers_obj)?
                    } else {
                        vec![]
                    };
                    let store_mode = if let Ok(mode_obj) = obj.getattr("store_mode") {
                        Self::parse_store_mode(&mode_obj)?
                    } else {
                        StoreMode::Shared
                    };
                    return Ok(Yielded::Effect(Effect::Scheduler(SchedulerEffect::Spawn {
                        program,
                        handlers,
                        store_mode,
                    })));
                }
                "GatherEffect" | "SchedulerGather" => {
                    let items_obj = obj.getattr("items")?;
                    let mut waitables = Vec::new();
                    for item in items_obj.try_iter()? {
                        let item = item?;
                        match Self::extract_waitable(_py, &item) {
                            Some(w) => waitables.push(w),
                            None => {
                                return Err(PyTypeError::new_err(
                                    "GatherEffect.items must be waitable handles",
                                ));
                            }
                        }
                    }
                    return Ok(Yielded::Effect(Effect::Scheduler(
                        SchedulerEffect::Gather { items: waitables },
                    )));
                }
                "RaceEffect" | "SchedulerRace" => {
                    let futures_obj = obj.getattr("futures")?;
                    let mut waitables = Vec::new();
                    for item in futures_obj.try_iter()? {
                        let item = item?;
                        match Self::extract_waitable(_py, &item) {
                            Some(w) => waitables.push(w),
                            None => {
                                return Err(PyTypeError::new_err(
                                    "RaceEffect.futures must be waitable handles",
                                ));
                            }
                        }
                    }
                    return Ok(Yielded::Effect(Effect::Scheduler(SchedulerEffect::Race {
                        items: waitables,
                    })));
                }
                "CompletePromiseEffect" | "SchedulerCompletePromise" => {
                    let promise_obj = obj.getattr("promise")?;
                    if let Some(pid) = Self::extract_promise_id(_py, &promise_obj) {
                        let value = obj.getattr("value")?;
                        return Ok(Yielded::Effect(Effect::Scheduler(
                            SchedulerEffect::CompletePromise {
                                promise: pid,
                                value: Value::from_pyobject(&value),
                            },
                        )));
                    }
                    return Err(PyTypeError::new_err(
                        "CompletePromiseEffect.promise must carry _promise_handle.promise_id",
                    ));
                }
                "FailPromiseEffect" | "SchedulerFailPromise" => {
                    let promise_obj = obj.getattr("promise")?;
                    if let Some(pid) = Self::extract_promise_id(_py, &promise_obj) {
                        let error_obj = obj.getattr("error")?;
                        let exc = pyerr_to_exception(_py, PyErr::from_value(error_obj))?;
                        return Ok(Yielded::Effect(Effect::Scheduler(
                            SchedulerEffect::FailPromise {
                                promise: pid,
                                error: exc,
                            },
                        )));
                    }
                    return Err(PyTypeError::new_err(
                        "FailPromiseEffect.promise must carry _promise_handle.promise_id",
                    ));
                }
                "WaitEffect" => {
                    let future_obj = obj.getattr("future")?;
                    if let Some(w) = Self::extract_waitable(_py, &future_obj) {
                        return Ok(Yielded::Effect(Effect::Scheduler(
                            SchedulerEffect::Gather { items: vec![w] },
                        )));
                    }
                    return Err(PyTypeError::new_err("WaitEffect.future must be waitable"));
                }
                "TaskCompletedEffect"
                | "SchedulerTaskCompleted"
                | "TaskCancelEffect"
                | "TaskIsDoneEffect"
                | "WaitForExternalCompletion" => {
                    if type_str == "TaskCompletedEffect" || type_str == "SchedulerTaskCompleted" {
                        if let Ok(task_obj) = obj.getattr("task") {
                            if let Some(task) = Self::extract_task_id(_py, &task_obj) {
                                if let Ok(error_obj) = obj.getattr("error") {
                                    let exc = pyerr_to_exception(
                                        _py,
                                        PyErr::from_value(error_obj.clone()),
                                    )?;
                                    return Ok(Yielded::Effect(Effect::Scheduler(
                                        SchedulerEffect::TaskCompleted {
                                            task,
                                            result: Err(exc),
                                        },
                                    )));
                                }
                                if let Ok(result_obj) = obj.getattr("result") {
                                    return Ok(Yielded::Effect(Effect::Scheduler(
                                        SchedulerEffect::TaskCompleted {
                                            task,
                                            result: Ok(Value::from_pyobject(&result_obj)),
                                        },
                                    )));
                                }
                            }
                        }
                    }
                    return Err(PyTypeError::new_err(
                        "TaskCompletedEffect/SchedulerTaskCompleted requires task + result or error",
                    ));
                }
                _ => {
                    if type_str.starts_with("_Scheduler") {
                        return Err(PyTypeError::new_err(
                            "Unknown _Scheduler* effect type is not supported",
                        ));
                    }
                }
            }
        }

        if obj.hasattr("to_generator")? {
            if let Some(metadata) = Self::extract_call_metadata(_py, obj) {
                return Ok(Yielded::DoCtrl(DoCtrl::Call {
                    f: PyShared::new(obj.clone().unbind()),
                    args: vec![],
                    kwargs: vec![],
                    metadata,
                }));
            }
            return Ok(Yielded::Program(obj.clone().unbind()));
        }

        if obj.hasattr("__iter__")? && obj.hasattr("__next__")? {
            return Ok(Yielded::Program(obj.clone().unbind()));
        }

        // Primitive Python types (int, str, float, bool, None) are not valid effects.
        // Class instances are treated as custom Python effects for dispatch.
        if let Ok(type_name) = obj.get_type().name() {
            let ts: &str = type_name.extract().unwrap_or("");
            match ts {
                "int" | "float" | "str" | "bool" | "NoneType" | "bytes" | "list" | "tuple"
                | "dict" | "set" => {
                    return Ok(Yielded::Unknown(obj.clone().unbind()));
                }
                _ => {}
            }
        }
        Ok(Yielded::Effect(Effect::Python(PyShared::new(
            obj.clone().unbind(),
        ))))
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

    /// Convert a Python Waitable (Task, Future, Promise) to a Rust Waitable.
    /// Python objects store `_handle` as a dict with `{"type": "Task"/"Promise"/"ExternalPromise", "task_id"/"promise_id": u64}`.
    fn extract_waitable(
        _py: Python<'_>,
        obj: &Bound<'_, PyAny>,
    ) -> Option<crate::scheduler::Waitable> {
        let handle = obj.getattr("_handle").ok()?;
        let type_val = handle.get_item("type").ok()?;
        let type_str: String = type_val.extract().ok()?;
        match type_str.as_str() {
            "Task" => {
                let raw: u64 = handle.get_item("task_id").ok()?.extract().ok()?;
                Some(crate::scheduler::Waitable::Task(
                    crate::ids::TaskId::from_raw(raw),
                ))
            }
            "Promise" => {
                let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
                Some(crate::scheduler::Waitable::Promise(
                    crate::ids::PromiseId::from_raw(raw),
                ))
            }
            "ExternalPromise" => {
                let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
                Some(crate::scheduler::Waitable::ExternalPromise(
                    crate::ids::PromiseId::from_raw(raw),
                ))
            }
            _ => None,
        }
    }

    /// Extract a PromiseId from a Python Promise object.
    /// Promise stores `_promise_handle` as the dict from the VM.
    fn extract_promise_id(
        _py: Python<'_>,
        obj: &Bound<'_, PyAny>,
    ) -> Option<crate::ids::PromiseId> {
        let handle = obj.getattr("_promise_handle").ok()?;
        let raw: u64 = handle.get_item("promise_id").ok()?.extract().ok()?;
        Some(crate::ids::PromiseId::from_raw(raw))
    }

    fn extract_task_id(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> Option<crate::ids::TaskId> {
        if let Ok(raw) = obj.getattr("task_id").and_then(|v| v.extract::<u64>()) {
            return Some(crate::ids::TaskId::from_raw(raw));
        }
        if let Ok(handle) = obj.getattr("_handle") {
            if let Ok(raw) = handle.get_item("task_id").and_then(|v| v.extract::<u64>()) {
                return Some(crate::ids::TaskId::from_raw(raw));
            }
        }
        None
    }

    fn extract_handlers_from_python(
        _py: Python<'_>,
        obj: &Bound<'_, PyAny>,
    ) -> PyResult<Vec<Handler>> {
        let mut handlers = Vec::new();
        for item in obj.try_iter()? {
            let item = item?;
            if item.is_instance_of::<PyRustHandlerSentinel>() {
                let sentinel: PyRef<'_, PyRustHandlerSentinel> = item.extract()?;
                handlers.push(Handler::RustProgram(sentinel.factory.clone()));
            } else {
                handlers.push(Handler::Python(PyShared::new(item.unbind())));
            }
        }
        Ok(handlers)
    }

    fn parse_store_mode(obj: &Bound<'_, PyAny>) -> PyResult<StoreMode> {
        if let Ok(mode) = obj.extract::<String>() {
            return match mode.to_lowercase().as_str() {
                "shared" => Ok(StoreMode::Shared),
                "isolated" => Ok(StoreMode::Isolated {
                    merge: StoreMergePolicy::LogsOnly,
                }),
                other => Err(PyTypeError::new_err(format!(
                    "unsupported store_mode '{other}'"
                ))),
            };
        }
        Ok(StoreMode::Shared)
    }

    fn extract_kpc_effect(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<KpcCallEffect> {
        let metadata = Self::extract_call_metadata(_py, obj)
            .or_else(|| Self::extract_call_metadata_fallback(_py, obj))
            .ok_or_else(|| {
                PyTypeError::new_err(
                    "KleisliProgramCall missing required metadata for call tracking",
                )
            })?;

        let kernel_obj = obj
            .getattr("execution_kernel")
            .or_else(|_| obj.getattr("kernel"))
            .map_err(|_| PyTypeError::new_err("KleisliProgramCall missing execution kernel"))?;
        let kernel = PyShared::new(kernel_obj.unbind());

        let strategy = obj.getattr("auto_unwrap_strategy").ok();

        let mut args = Vec::new();
        if let Ok(args_obj) = obj.getattr("args") {
            for (idx, item) in args_obj.try_iter()?.enumerate() {
                let item = item?;
                let should_unwrap = Self::strategy_should_unwrap_positional(strategy.as_ref(), idx)?;
                args.push(Self::extract_kpc_arg(_py, &item, should_unwrap)?);
            }
        }

        let mut kwargs = Vec::new();
        if let Ok(kwargs_obj) = obj.getattr("kwargs") {
            for (k, v) in kwargs_obj.cast::<pyo3::types::PyDict>()?.iter() {
                let key: String = k.extract()?;
                let should_unwrap =
                    Self::strategy_should_unwrap_keyword(strategy.as_ref(), key.as_str())?;
                kwargs.push((key, Self::extract_kpc_arg(_py, &v, should_unwrap)?));
            }
        }

        Ok(KpcCallEffect {
            call: PyShared::new(obj.clone().unbind()),
            kernel,
            args,
            kwargs,
            metadata,
        })
    }

    fn strategy_should_unwrap_positional(
        strategy: Option<&Bound<'_, PyAny>>,
        idx: usize,
    ) -> PyResult<bool> {
        let Some(strategy) = strategy else {
            return Ok(true);
        };
        let result = strategy
            .call_method1("should_unwrap_positional", (idx,))
            .and_then(|v| v.extract::<bool>());
        Ok(result.unwrap_or(true))
    }

    fn strategy_should_unwrap_keyword(
        strategy: Option<&Bound<'_, PyAny>>,
        key: &str,
    ) -> PyResult<bool> {
        let Some(strategy) = strategy else {
            return Ok(true);
        };
        let result = strategy
            .call_method1("should_unwrap_keyword", (key,))
            .and_then(|v| v.extract::<bool>());
        Ok(result.unwrap_or(true))
    }

    fn extract_kpc_arg(_py: Python<'_>, obj: &Bound<'_, PyAny>, should_unwrap: bool) -> PyResult<KpcArg> {
        if should_unwrap && Self::is_do_expr_candidate(obj)? {
            return Ok(KpcArg::Expr(PyShared::new(obj.clone().unbind())));
        }
        Ok(KpcArg::Value(Value::from_pyobject(obj)))
    }

    fn is_do_expr_candidate(obj: &Bound<'_, PyAny>) -> PyResult<bool> {
        if obj.hasattr("to_generator")? {
            return Ok(true);
        }
        let Ok(type_name) = obj.get_type().name() else {
            return Ok(false);
        };
        let type_str: &str = type_name.extract()?;
        Ok(type_str.ends_with("Effect")
            || matches!(
                type_str,
                "Get"
                    | "Put"
                    | "Modify"
                    | "Ask"
                    | "Tell"
                    | "WithHandler"
                    | "Resume"
                    | "Transfer"
                    | "Delegate"
                    | "CreateContinuation"
                    | "ResumeContinuation"
                    | "KleisliProgramCall"
            ))
    }

    fn extract_call_metadata_fallback(
        _py: Python<'_>,
        obj: &Bound<'_, PyAny>,
    ) -> Option<CallMetadata> {
        let function_name = obj
            .getattr("function_name")
            .ok()?
            .extract::<String>()
            .ok()?;
        let source_file = obj.getattr("source_file").ok()?.extract::<String>().ok()?;
        let source_line = obj.getattr("source_line").ok()?.extract::<u32>().ok()?;
        Some(CallMetadata {
            function_name,
            source_file,
            source_line,
            program_call: Some(PyShared::new(obj.clone().unbind())),
        })
    }

    fn extract_call_metadata(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> Option<CallMetadata> {
        let function_name = obj
            .getattr("function_name")
            .ok()?
            .extract::<String>()
            .ok()?;
        let kleisli = obj.getattr("kleisli_source").ok()?;
        let func = kleisli.getattr("original_func").ok()?;
        let code = func.getattr("__code__").ok()?;
        let source_file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;
        let source_line = code.getattr("co_firstlineno").ok()?.extract::<u32>().ok()?;
        Some(CallMetadata {
            function_name,
            source_file,
            source_line,
            program_call: Some(PyShared::new(obj.clone().unbind())),
        })
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
    Ok(PyException::new(exc_type, exc_value, exc_tb))
}

fn extract_stop_iteration_value(py: Python<'_>, e: &PyErr) -> PyResult<Value> {
    let value = e.value(py).getattr("value")?;
    Ok(Value::from_pyobject(&value))
}

// ---------------------------------------------------------------------------
// PyRunResult — execution output [R8-J]
// ---------------------------------------------------------------------------

// D9: Ok/Err wrapper types for RunResult.result (spec says Ok(val)/Err(exc) objects)
#[pyclass(frozen, name = "Ok")]
pub struct PyResultOk {
    value: Py<PyAny>,
}

#[pymethods]
impl PyResultOk {
    #[getter]
    fn value(&self, py: Python<'_>) -> Py<PyAny> {
        self.value.clone_ref(py)
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
    error: Py<PyAny>,
}

#[pymethods]
impl PyResultErr {
    #[getter]
    fn error(&self, py: Python<'_>) -> Py<PyAny> {
        self.error.clone_ref(py)
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
    raw_store: Py<pyo3::types::PyDict>,
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
                let err_obj = Bound::new(
                    py,
                    PyResultErr {
                        error: e.value_clone_ref(py),
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

    fn is_ok(&self) -> bool {
        self.result.is_ok()
    }

    fn is_err(&self) -> bool {
        self.result.is_err()
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
#[pyclass(name = "WithHandler")]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub program: Py<PyAny>,
}

#[pymethods]
impl PyWithHandler {
    #[new]
    fn new(handler: Py<PyAny>, program: Py<PyAny>) -> Self {
        PyWithHandler { handler, program }
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Resume")]
pub struct PyResume {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyResume {
    #[new]
    fn new(continuation: Py<PyAny>, value: Py<PyAny>) -> Self {
        PyResume {
            continuation,
            value,
        }
    }
}

/// Dispatch primitive — handler-only.
#[pyclass(name = "Delegate")]
pub struct PyDelegate {
    #[pyo3(get)]
    pub effect: Option<Py<PyAny>>,
}

#[pymethods]
impl PyDelegate {
    #[new]
    #[pyo3(signature = (effect=None))]
    fn new(effect: Option<Py<PyAny>>) -> Self {
        PyDelegate { effect }
    }
}

/// Dispatch primitive — handler-only, one-shot.
#[pyclass(name = "Transfer")]
pub struct PyTransfer {
    #[pyo3(get)]
    pub continuation: Py<PyAny>,
    #[pyo3(get)]
    pub value: Py<PyAny>,
}

#[pymethods]
impl PyTransfer {
    #[new]
    fn new(continuation: Py<PyAny>, value: Py<PyAny>) -> Self {
        PyTransfer {
            continuation,
            value,
        }
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
    factory: RustProgramHandlerRef,
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
    fn to_generator(slf: PyRef<'_, Self>, py: Python<'_>) -> PyResult<NestingGenerator> {
        Ok(NestingGenerator {
            handler: Some(slf.handler.clone_ref(py)),
            inner: Some(slf.inner.clone_ref(py)),
            done: false,
        })
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
        self.done = true;
        let wh = PyWithHandler {
            handler,
            program: inner,
        };
        let bound = Bound::new(py, wh)?;
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
        Err(PyStopIteration::new_err(value))
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
                PyWithHandler {
                    handler: sentinel.clone_ref(py),
                    program: py.None().into_pyobject(py).unwrap().unbind().into_any(),
                },
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
    fn test_g3_task_completed_classifies_to_typed_scheduler_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect:\n    def __init__(self, tid, value):\n        self.task = _TaskHandle(tid)\n        self.result = value\n\nobj = TaskCompletedEffect(7, 123)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();

            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                Yielded::Effect(Effect::Scheduler(SchedulerEffect::TaskCompleted {
                    task,
                    result,
                })) => {
                    assert_eq!(task.raw(), 7);
                    match result {
                        Ok(Value::Int(v)) => assert_eq!(v, 123),
                        other => panic!("G3 FAIL: unexpected TaskCompleted result: {:?}", other),
                    }
                }
                other => panic!(
                    "G3 FAIL: expected typed TaskCompleted scheduler effect, got {:?}",
                    other
                ),
            }
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

            let locals = pyo3::types::PyDict::new(py);
            locals.set_item("sentinel", sentinel.bind(py)).unwrap();
            py.run(
                c"class CreateContinuation:\n    def __init__(self, program, handlers):\n        self.program = program\n        self.handlers = handlers\nobj = CreateContinuation(None, [sentinel])\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                Yielded::DoCtrl(DoCtrl::CreateContinuation { handlers, .. }) => {
                    assert!(
                        matches!(handlers.first(), Some(crate::handler::Handler::RustProgram(_))),
                        "G3 FAIL: CreateContinuation converted rust sentinel into Python handler"
                    );
                }
                other => panic!("G3 FAIL: expected CreateContinuation, got {:?}", other),
            }
        });
    }

    #[test]
    fn test_g4_task_completed_error_classifies_to_typed_err() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect:\n    def __init__(self, tid, err):\n        self.task = _TaskHandle(tid)\n        self.error = err\n\nobj = TaskCompletedEffect(9, ValueError('boom'))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                Yielded::Effect(Effect::Scheduler(SchedulerEffect::TaskCompleted { task, result })) => {
                    assert_eq!(task.raw(), 9);
                    assert!(result.is_err(), "G4 FAIL: expected Err result for TaskCompleted.error");
                }
                other => panic!("G4 FAIL: expected typed TaskCompleted, got {:?}", other),
            }
        });
    }

    #[test]
    fn test_g5_kpc_classifies_as_effect_not_direct_call() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class _S:\n    def should_unwrap_positional(self, i):\n        return True\n    def should_unwrap_keyword(self, k):\n        return True\n\nclass KleisliProgramCall:\n    function_name = 'f'\n    source_file = 'x.py'\n    source_line = 1\n    kleisli_source = None\n    def __init__(self):\n        self.args = (1,)\n        self.kwargs = {}\n        self.auto_unwrap_strategy = _S()\n        self.execution_kernel = (lambda x: x)\n    def to_generator(self):\n        if False:\n            yield None\n\nobj = KleisliProgramCall()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(_)),
                "G5 FAIL: KleisliProgramCall should classify as Effect (handler-dispatched), got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g6_malformed_gather_effect_raises_classification_error() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class GatherEffect:\n    def __init__(self):\n        self.items = [123]\nobj = GatherEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let res = pyvm.classify_yielded(py, &obj);
            assert!(
                res.is_err(),
                "G6 FAIL: malformed GatherEffect should raise classification error, got {:?}",
                res
            );
        });
    }

    #[test]
    fn test_g7_spawn_effect_extracts_handlers_and_store_mode() {
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
            py.run(
                c"class SpawnEffect:\n    def __init__(self, p, hs, mode):\n        self.program = p\n        self.handlers = hs\n        self.store_mode = mode\nobj = SpawnEffect(None, [sentinel], 'isolated')\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            match yielded {
                Yielded::Effect(Effect::Scheduler(SchedulerEffect::Spawn {
                    handlers,
                    store_mode,
                    ..
                })) => {
                    assert_eq!(handlers.len(), 1, "G7 FAIL: Spawn handlers not extracted");
                    assert!(
                        matches!(handlers.first(), Some(crate::handler::Handler::RustProgram(_))),
                        "G7 FAIL: Spawn handler should preserve rust sentinel protocol"
                    );
                    assert!(
                        matches!(
                            store_mode,
                            crate::scheduler::StoreMode::Isolated { merge: _ }
                        ),
                        "G7 FAIL: Spawn store_mode should parse isolated"
                    );
                }
                other => panic!(
                    "G7 FAIL: expected typed Spawn scheduler effect, got {:?}",
                    other
                ),
            }
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
                PyResume {
                    continuation: k,
                    value: py.None().into_pyobject(py).unwrap().unbind().into_any(),
                },
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
}

// ---------------------------------------------------------------------------
// Module-level functions [G11 / SPEC-008]
// ---------------------------------------------------------------------------

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
///   - `RustHandler` sentinels: `state`, `reader`, `writer`
///   - Python handler callables
#[pyfunction]
#[pyo3(signature = (program, handlers=None, env=None, store=None))]
fn run(
    py: Python<'_>,
    program: Bound<'_, PyAny>,
    handlers: Option<Bound<'_, pyo3::types::PyList>>,
    env: Option<Bound<'_, pyo3::types::PyDict>>,
    store: Option<Bound<'_, pyo3::types::PyDict>>,
) -> PyResult<PyRunResult> {
    let mut vm = PyVM { vm: VM::new() };

    // Seed env
    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k: String = key.extract()?;
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

    // ADR-13: Build WithHandler nesting chain.
    // handlers=[h0, h1, h2] → WithHandler(h0, WithHandler(h1, WithHandler(h2, program)))
    // Build inside-out: wrap h2 first, then h1, then h0.
    let mut wrapped: Py<PyAny> = program.unbind();

    // Install default KPC handler as innermost wrapper.
    let kpc_sentinel = Bound::new(
        py,
        PyRustHandlerSentinel {
            factory: Arc::new(KpcHandlerFactory),
        },
    )?
    .into_any()
    .unbind();
    let kpc_step = NestingStep {
        handler: kpc_sentinel,
        inner: wrapped,
    };
    wrapped = Bound::new(py, kpc_step)?.into_any().unbind();

    if let Some(handler_list) = handlers {
        let items: Vec<_> = handler_list.iter().collect();
        for handler_obj in items.into_iter().rev() {
            let step = NestingStep {
                handler: handler_obj.unbind(),
                inner: wrapped,
            };
            let bound = Bound::new(py, step)?;
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
#[pyo3(signature = (program, handlers=None, env=None, store=None))]
fn async_run<'py>(
    py: Python<'py>,
    program: Bound<'py, PyAny>,
    handlers: Option<Bound<'py, pyo3::types::PyList>>,
    env: Option<Bound<'py, pyo3::types::PyDict>>,
    store: Option<Bound<'py, pyo3::types::PyDict>>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut vm = PyVM { vm: VM::new() };

    if let Some(env_dict) = env {
        for (key, value) in env_dict.iter() {
            let k: String = key.extract()?;
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

    // Install default KPC handler as innermost wrapper.
    let kpc_sentinel = Bound::new(
        py,
        PyRustHandlerSentinel {
            factory: Arc::new(KpcHandlerFactory),
        },
    )?
    .into_any()
    .unbind();
    let kpc_step = NestingStep {
        handler: kpc_sentinel,
        inner: wrapped,
    };
    wrapped = Bound::new(py, kpc_step)?.into_any().unbind();

    if let Some(handler_list) = handlers {
        let items: Vec<_> = handler_list.iter().collect();
        for handler_obj in items.into_iter().rev() {
            let step = NestingStep {
                handler: handler_obj.unbind(),
                inner: wrapped,
            };
            let bound = Bound::new(py, step)?;
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
            "        try:\n",
            "            result = _vm.step_once()\n",
            "        except BaseException as exc:\n",
            "            return _vm.build_run_result_error(exc)\n",
            "        tag = result[0]\n",
            "        if tag == 'done':\n",
            "            return _vm.build_run_result(result[1])\n",
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
    m.add_class::<PyStdlib>()?;
    m.add_class::<PySchedulerHandler>()?;
    m.add_class::<PyRunResult>()?;
    m.add_class::<PyResultOk>()?;
    m.add_class::<PyResultErr>()?;
    m.add_class::<PyK>()?;
    m.add_class::<PyWithHandler>()?;
    m.add_class::<PyResume>()?;
    m.add_class::<PyDelegate>()?;
    m.add_class::<PyTransfer>()?;
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
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(async_run, m)?)?;
    Ok(())
}

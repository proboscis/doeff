use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::effect::Effect;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    Handler, HandlerEntry, ReaderHandlerFactory, RustProgramHandlerRef, StateHandlerFactory,
    WriterHandlerFactory,
};
use crate::ids::{ContId, Marker};
use crate::scheduler::{SchedulerEffect, SchedulerHandler};
use crate::segment::Segment;
use crate::step::{
    ControlPrimitive, Mode, PyCallOutcome, PyException, PythonCall, StepEvent, Yielded,
};
use crate::value::Value;
use crate::vm::VM;

fn vmerror_to_pyerr(e: VMError) -> PyErr {
    match e {
        VMError::TypeError { .. } => PyTypeError::new_err(e.to_string()),
        VMError::UncaughtException { exception } => {
            // SAFETY: vmerror_to_pyerr is always called from GIL-holding contexts (run/step_once)
            let py = unsafe { Python::assume_attached() };
            PyErr::from_value(exception.exc_value.bind(py).clone())
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
        // Convert program to generator - handles KleisliProgramCall and other Program types
        // Lenient: accepts raw generators for user convenience at the entry point.
        let gen = self.to_generator_lenient(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;

        loop {
            // GIL note: We cannot use py.allow_threads(|| self.run_rust_steps())
            // here because VM.step() clones Py<PyAny> values internally (generator
            // references, Mode values, Yielded snapshots). On free-threaded Python
            // (3.14t) with PyO3's py-clone feature, Py::clone() asserts the GIL is
            // held. To enable allow_threads, step() would need to be refactored to
            // use move/swap semantics instead of clone for all Py<PyAny> fields.
            // PyVM is Send+Sync (unsendable removed), so the structural prerequisite
            // is satisfied -- only the internal clone barrier remains.
            let event = self.run_rust_steps();

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
        let gen = self.to_generator_lenient(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;

        let result = loop {
            let event = self.run_rust_steps();
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

    pub fn start_program(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<()> {
        let gen = self.to_generator_lenient(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;
        Ok(())
    }

    pub fn step_once(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        // See run() for GIL/allow_threads note.
        let event = self.run_rust_steps();

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
                generator: gen.unbind(),
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
                // Try strict first (ProgramBase with to_generator method).
                // If that fails (e.g. raw generator function), fall back to
                // calling as a factory function. Raw generator objects are
                // still rejected by to_generator_strict's explicit check.
                match self.to_generator_strict(py, program.clone()) {
                    Ok(gen) => Ok(PyCallOutcome::Value(Value::Python(gen))),
                    Err(strict_err) => {
                        // Fallback: call as generator factory function, but
                        // only if the object is callable (rejects raw generator
                        // objects which are not callable).
                        let program_bound = program.bind(py);
                        if program_bound.is_callable() {
                            match program_bound.call0() {
                                Ok(result) => {
                                    Ok(PyCallOutcome::Value(Value::Python(result.unbind())))
                                }
                                Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                            }
                        } else {
                            Err(strict_err)
                        }
                    }
                }
            }
            PythonCall::CallFunc { func, args } => {
                let py_args = self.values_to_tuple(py, &args)?;
                match func.bind(py).call1(py_args) {
                    Ok(result) => Ok(PyCallOutcome::Value(Value::from_pyobject(&result))),
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
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
                        let gen = self.to_generator_lenient(py, result.unbind())?;
                        Ok(PyCallOutcome::Value(Value::Python(gen)))
                    }
                    Err(e) => Ok(PyCallOutcome::GenError(pyerr_to_exception(py, e)?)),
                }
            }
            PythonCall::GenNext { gen } => self.step_generator(py, gen, None),
            PythonCall::GenSend { gen, value } => {
                let py_value = value.to_pyobject(py)?;
                self.step_generator(py, gen, Some(py_value))
            }
            PythonCall::GenThrow { gen, exc } => {
                let exc_bound = exc.bind(py);
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

    /// Strict: requires ProgramBase (has `to_generator` method). Rejects raw generators.
    /// Used for `StartProgram` (when `Yielded::Program` is processed internally).
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

    /// Lenient: accepts raw generators or ProgramBase. Used for `run()` / `start_program`.
    fn to_generator_lenient(&self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
        let program_bound = program.bind(py);
        let type_name = program_bound.get_type().name()?;
        if type_name.to_string().contains("generator") {
            return Ok(program);
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
            let handler = if handler_bound.is_instance_of::<PyRustHandlerSentinel>() {
                let sentinel: PyRef<'_, PyRustHandlerSentinel> = handler_bound.extract()?;
                Handler::RustProgram(sentinel.factory.clone())
            } else {
                Handler::Python(wh.handler.clone_ref(_py))
            };
            return Ok(Yielded::Primitive(ControlPrimitive::WithHandler {
                handler,
                program: wh.program.clone_ref(_py),
            }));
        }
        if obj.is_instance_of::<PyResume>() {
            let r: PyRef<'_, PyResume> = obj.extract()?;
            if let Ok(k_pyobj) = r.continuation.bind(_py).downcast::<PyK>() {
                let cont_id = k_pyobj.borrow().cont_id;
                if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                    return Ok(Yielded::Primitive(ControlPrimitive::Resume {
                        continuation: k,
                        value: Value::from_pyobject(r.value.bind(_py)),
                    }));
                }
            }
            // Fall through to string-based parsing if K is not a PyK instance
        }
        if obj.is_instance_of::<PyTransfer>() {
            let t: PyRef<'_, PyTransfer> = obj.extract()?;
            if let Ok(k_pyobj) = t.continuation.bind(_py).downcast::<PyK>() {
                let cont_id = k_pyobj.borrow().cont_id;
                if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                    return Ok(Yielded::Primitive(ControlPrimitive::Transfer {
                        continuation: k,
                        value: Value::from_pyobject(t.value.bind(_py)),
                    }));
                }
            }
        }
        if obj.is_instance_of::<PyDelegate>() {
            let d: PyRef<'_, PyDelegate> = obj.extract()?;
            let effect = if let Some(ref eff) = d.effect {
                Effect::Python(eff.clone_ref(_py))
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
            return Ok(Yielded::Primitive(ControlPrimitive::Delegate { effect }));
        }

        // Fallback: string-based type name parsing (for Python dataclasses)
        if let Ok(type_name) = obj.get_type().name() {
            let type_str: &str = type_name.extract()?;
            if self.vm.debug.is_enabled() {
                eprintln!("[classify_yielded] type_str = {:?}", type_str);
            }
            match type_str {
                "Resume" => {
                    if let Ok(k_obj) = obj.getattr("continuation") {
                        let cont_id_raw = k_obj
                            .getattr("cont_id")
                            .or_else(|_| k_obj.get_item("cont_id"))?;
                        if let Ok(cont_id_val) = cont_id_raw.extract::<u64>() {
                            let cont_id = ContId::from_raw(cont_id_val);
                            if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                                let value = obj.getattr("value")?;
                                return Ok(Yielded::Primitive(ControlPrimitive::Resume {
                                    continuation: k,
                                    value: Value::from_pyobject(&value),
                                }));
                            }
                        }
                    }
                }
                "Transfer" => {
                    if let Ok(k_obj) = obj.getattr("continuation") {
                        let cont_id_raw = k_obj
                            .getattr("cont_id")
                            .or_else(|_| k_obj.get_item("cont_id"))?;
                        if let Ok(cont_id_val) = cont_id_raw.extract::<u64>() {
                            let cont_id = ContId::from_raw(cont_id_val);
                            if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                                let value = obj.getattr("value")?;
                                return Ok(Yielded::Primitive(ControlPrimitive::Transfer {
                                    continuation: k,
                                    value: Value::from_pyobject(&value),
                                }));
                            }
                        }
                    }
                }
                "WithHandler" => {
                    let handler_obj = obj.getattr("handler")?;
                    let program = obj.getattr("program").or_else(|_| obj.getattr("body"))?;
                    let handler = if handler_obj.is_instance_of::<PyRustHandlerSentinel>() {
                        let sentinel: PyRef<'_, PyRustHandlerSentinel> = handler_obj.extract()?;
                        Handler::RustProgram(sentinel.factory.clone())
                    } else {
                        Handler::Python(handler_obj.unbind())
                    };
                    return Ok(Yielded::Primitive(ControlPrimitive::WithHandler {
                        handler,
                        program: program.unbind(),
                    }));
                }
                "Delegate" => {
                    let effect = if let Ok(eff_obj) = obj.getattr("effect") {
                        if !eff_obj.is_none() {
                            Effect::Python(eff_obj.unbind())
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
                    return Ok(Yielded::Primitive(ControlPrimitive::Delegate { effect }));
                }
                "GetContinuation" => {
                    return Ok(Yielded::Primitive(ControlPrimitive::GetContinuation));
                }
                "GetHandlers" => {
                    return Ok(Yielded::Primitive(ControlPrimitive::GetHandlers));
                }
                "CreateContinuation" => {
                    let program = obj.getattr("program")?.unbind();
                    let handlers_list = obj.getattr("handlers")?;
                    let mut handlers = Vec::new();
                    for item in handlers_list.try_iter()? {
                        let item = item?;
                        handlers.push(crate::handler::Handler::Python(item.unbind()));
                    }
                    return Ok(Yielded::Primitive(ControlPrimitive::CreateContinuation {
                        program,
                        handlers,
                    }));
                }
                "ResumeContinuation" => {
                    if let Ok(k_obj) = obj.getattr("continuation") {
                        let cont_id_raw = k_obj
                            .getattr("cont_id")
                            .or_else(|_| k_obj.get_item("cont_id"))?;
                        if let Ok(cont_id_val) = cont_id_raw.extract::<u64>() {
                            let cont_id = ContId::from_raw(cont_id_val);
                            if let Some(k) = self.vm.lookup_continuation(cont_id).cloned() {
                                let value = obj.getattr("value")?;
                                return Ok(Yielded::Primitive(
                                    ControlPrimitive::ResumeContinuation {
                                        continuation: k,
                                        value: Value::from_pyobject(&value),
                                    },
                                ));
                            }
                        }
                    }
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
                        modifier: modifier.unbind(),
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
                    return Ok(Yielded::Primitive(
                        ControlPrimitive::PythonAsyncSyntaxEscape { action },
                    ));
                }
                // INV-17: Scheduler effects — must be classified BEFORE to_generator
                // fallback because EffectBase extends ProgramBase (all effects have
                // to_generator). Without these arms, scheduler effects would be
                // misclassified as Yielded::Program, causing infinite loops.
                //
                // These are classified as Effect::Python (not Effect::Scheduler)
                // because schedulers are user-space handlers (R5-C in SPEC-009).
                // The Python scheduler handler receives the raw Python effect
                // objects. The Rust SchedulerEffect enum is for Rust-internal
                // operations only, not for classifying Python user-facing effects.
                "SpawnEffect"
                | "SchedulerSpawn"
                | "GatherEffect"
                | "SchedulerGather"
                | "RaceEffect"
                | "SchedulerRace"
                | "CompletePromiseEffect"
                | "SchedulerCompletePromise"
                | "FailPromiseEffect"
                | "SchedulerFailPromise"
                | "TaskCompletedEffect"
                | "SchedulerTaskCompleted"
                | "WaitEffect"
                | "TaskCancelEffect"
                | "TaskIsDoneEffect"
                | "WaitForExternalCompletion" => {
                    return Ok(Yielded::Effect(Effect::Python(obj.clone().unbind())));
                }
                _ => {
                    // Catch internal _Scheduler* effects (EffectBase subclasses)
                    if type_str.starts_with("_Scheduler") {
                        return Ok(Yielded::Effect(Effect::Python(obj.clone().unbind())));
                    }
                }
            }
        }

        if obj.hasattr("to_generator")? {
            if let Some(metadata) = Self::extract_call_metadata(_py, obj) {
                return Ok(Yielded::Primitive(ControlPrimitive::Call {
                    program: obj.clone().unbind(),
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
        Ok(Yielded::Effect(Effect::Python(obj.clone().unbind())))
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
            program_call: Some(obj.clone().unbind()),
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

#[pyclass(frozen, name = "RunResult")]
pub struct PyRunResult {
    result: Result<Py<PyAny>, PyException>,
    raw_store: Py<pyo3::types::PyDict>,
}

#[pymethods]
impl PyRunResult {
    /// Returns the Ok value or raises the stored exception.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)),
            Err(e) => Err(PyErr::from_value(e.exc_value.bind(py).clone())),
        }
    }

    /// Returns the stored exception value, or raises ValueError if Ok.
    #[getter]
    fn error(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Err(e) => Ok(e.exc_value.clone_ref(py)),
            Ok(_) => Err(pyo3::exceptions::PyValueError::new_err(
                "RunResult is Ok, not Err",
            )),
        }
    }

    /// Returns Ok(value) or Err(exception) as a Python tuple (tag, payload).
    #[getter]
    fn result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => {
                let tuple = PyTuple::new(
                    py,
                    [
                        "ok".into_pyobject(py)?.into_any(),
                        v.bind(py).clone().into_any(),
                    ],
                )?;
                Ok(tuple.into())
            }
            Err(e) => {
                let tuple = PyTuple::new(
                    py,
                    [
                        "err".into_pyobject(py)?.into_any(),
                        e.exc_value.bind(py).clone().into_any(),
                    ],
                )?;
                Ok(tuple.into())
            }
        }
    }

    /// State-only store snapshot (does not include env or logs).
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

/// Module-level `async_run()` — async version of `run()`.
///
/// API-12: Returns a true Python coroutine that yields control to the event
/// loop. The coroutine awaits `asyncio.sleep(0)` before running the sync VM
/// driver, giving other tasks a chance to execute.
#[pyfunction]
#[pyo3(signature = (program, handlers=None, env=None, store=None))]
fn async_run<'py>(
    py: Python<'py>,
    program: Bound<'py, PyAny>,
    handlers: Option<Bound<'py, pyo3::types::PyList>>,
    env: Option<Bound<'py, pyo3::types::PyDict>>,
    store: Option<Bound<'py, pyo3::types::PyDict>>,
) -> PyResult<Bound<'py, PyAny>> {
    let run_fn = wrap_pyfunction!(run, py)?;
    let args_tuple = {
        let mut args: Vec<Bound<'py, PyAny>> = vec![program.into_any()];
        args.push(match handlers {
            Some(h) => h.into_any(),
            None => py.None().into_bound(py),
        });
        args.push(match env {
            Some(e) => e.into_any(),
            None => py.None().into_bound(py),
        });
        args.push(match store {
            Some(s) => s.into_any(),
            None => py.None().into_bound(py),
        });
        PyTuple::new(py, args)?
    };

    let asyncio = py.import("asyncio")?;
    let ns = pyo3::types::PyDict::new(py);
    ns.set_item("_run_fn", run_fn)?;
    ns.set_item("_args", args_tuple)?;
    ns.set_item("asyncio", asyncio)?;

    // API-12: true async — yields to event loop via sleep(0) before sync execution.
    // Pass ns as globals so nested async def can see asyncio, _run_fn, _args.
    py.run(pyo3::ffi::c_str!(
        "async def _async_run_impl():\n    await asyncio.sleep(0)\n    return _run_fn(*_args)\n_coro = _async_run_impl()\n"
    ), Some(&ns), None)?;

    Ok(ns.get_item("_coro")?.unwrap().into_any())
}

#[pymodule]
pub fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyVM>()?;
    m.add_class::<PyStdlib>()?;
    m.add_class::<PySchedulerHandler>()?;
    m.add_class::<PyRunResult>()?;
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

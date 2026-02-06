use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::effect::Effect;
use crate::handler::{Handler, HandlerEntry, StdlibHandler};
use crate::ids::{ContId, Marker};
use crate::scheduler::{SchedulerEffect, SchedulerHandler};
use crate::segment::Segment;
use crate::step::{
    ControlPrimitive, Mode, PyCallOutcome, PyException, PythonCall, StepEvent, Yielded,
};
use crate::error::VMError;
use crate::value::Value;
use crate::vm::VM;

fn vmerror_to_pyerr(e: VMError) -> PyErr {
    match e {
        VMError::TypeError { .. } => PyTypeError::new_err(e.to_string()),
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

    pub fn set_store(&mut self, py: Python<'_>, key: &str, value: Bound<'_, PyAny>) -> PyResult<()> {
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
                let elems: Vec<Bound<'_, pyo3::PyAny>> = vec![
                    "done".into_pyobject(py)?.into_any(),
                    py_val,
                ];
                let tuple = PyTuple::new(py, elems)?;
                Ok(tuple.into())
            }
            StepEvent::Error(e) => {
                Err(vmerror_to_pyerr(e))
            }
            StepEvent::NeedsPython(call) => {
                if let PythonCall::CallAsync { func, args } = call {
                    let py_func = func.bind(py).clone().into_any();
                    let py_args = self.values_to_tuple(py, &args)?.into_any();
                    let elems: Vec<Bound<'_, pyo3::PyAny>> = vec![
                        "call_async".into_pyobject(py)?.into_any(),
                        py_func,
                        py_args,
                    ];
                    let tuple = PyTuple::new(py, elems)?;
                    Ok(tuple.into())
                } else {
                    // Handle synchronously like run() does
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                    let elems: Vec<Bound<'_, pyo3::PyAny>> = vec![
                        "continue".into_pyobject(py)?.into_any(),
                    ];
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

    pub fn feed_async_error(&mut self, py: Python<'_>, error_value: Bound<'_, PyAny>) -> PyResult<()> {
        // Build a PyException from the error value.
        // error_value is expected to be a Python exception instance.
        let exc_type = error_value.get_type().into_any().unbind();
        let exc_value = error_value.clone().unbind();
        let exc_tb = py.None();
        let py_exc = crate::step::PyException::new(exc_type, exc_value, Some(exc_tb));
        self.vm.receive_python_result(PyCallOutcome::GenError(py_exc));
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
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::State), prompt_seg_id),
            );
            scoped_markers.push(marker);
        }

        if reader {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::Reader), prompt_seg_id),
            );
            scoped_markers.push(marker);
        }

        if writer {
            let marker = Marker::fresh();
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = self.vm.alloc_segment(seg);
            self.vm.install_handler(
                marker,
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::Writer), prompt_seg_id),
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
                                Ok(result) => Ok(PyCallOutcome::Value(Value::Python(result.unbind()))),
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
            PythonCall::CallAsync { .. } => {
                Err(pyo3::exceptions::PyTypeError::new_err(
                    "CallAsync requires async_run (PythonAsyncSyntaxEscape not supported in sync mode)"
                ))
            }
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
                 Yield a ProgramBase object (e.g. decorated with @do) instead of a raw generator."
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
        if let Ok(type_name) = obj.get_type().name() {
            let type_str: &str = type_name.extract()?;
            if self.vm.debug.is_enabled() {
                eprintln!("[classify_yielded] type_str = {:?}", type_str);
            }
            match type_str {
                "PureEffect" | "Pure" => {
                    let value = obj.getattr("value")?;
                    return Ok(Yielded::Primitive(ControlPrimitive::Pure(
                        Value::from_pyobject(&value),
                    )));
                }
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
                                    k,
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
                                    k,
                                    value: Value::from_pyobject(&value),
                                }));
                            }
                        }
                    }
                }
                "WithHandler" => {
                    let handler = obj.getattr("handler")?;
                    let body = obj.getattr("body")?;
                    return Ok(Yielded::Primitive(ControlPrimitive::WithHandler {
                        handler: handler.unbind(),
                        body: body.unbind(),
                    }));
                }
                "Delegate" => {
                    let effect = if let Ok(eff_obj) = obj.getattr("effect") {
                        if !eff_obj.is_none() {
                            Effect::Python(eff_obj.unbind())
                        } else {
                            // No explicit effect — use current dispatch effect
                            self.vm.dispatch_stack.last()
                                .map(|ctx| ctx.effect.clone())
                                .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                                    "Delegate without effect called outside dispatch context",
                                ))?
                        }
                    } else {
                        // No effect attribute — use current dispatch effect
                        self.vm.dispatch_stack.last()
                            .map(|ctx| ctx.effect.clone())
                            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                                "Delegate without effect called outside dispatch context",
                            ))?
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
                                return Ok(Yielded::Primitive(ControlPrimitive::ResumeContinuation {
                                    k,
                                    value: Value::from_pyobject(&value),
                                }));
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
                    return Ok(Yielded::Primitive(ControlPrimitive::PythonAsyncSyntaxEscape { action }));
                }
                _ => {}
            }
        }

        if obj.hasattr("to_generator")? {
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
                "int" | "float" | "str" | "bool" | "NoneType" | "bytes" | "list" | "tuple" | "dict" | "set" => {
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
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::State), prompt_seg_id),
            );
        }
    }

    pub fn install_reader(&self, vm: &mut PyVM) {
        if let Some(marker) = self.reader_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::Reader), prompt_seg_id),
            );
        }
    }

    pub fn install_writer(&self, vm: &mut PyVM) {
        if let Some(marker) = self.writer_marker {
            let seg = Segment::new(marker, None, vec![]);
            let prompt_seg_id = vm.vm.alloc_segment(seg);
            vm.vm.install_handler(
                marker,
                HandlerEntry::new(Handler::Stdlib(StdlibHandler::Writer), prompt_seg_id),
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

#[pymodule]
pub fn doeff_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyVM>()?;
    m.add_class::<PyStdlib>()?;
    m.add_class::<PySchedulerHandler>()?;
    Ok(())
}

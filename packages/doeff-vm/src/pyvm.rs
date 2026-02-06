use pyo3::exceptions::{PyRuntimeError, PyStopIteration};
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
use crate::value::Value;
use crate::vm::VM;

#[pyclass(unsendable)]
pub struct PyVM {
    vm: VM,
}

#[pyclass]
pub struct PyStdlib {
    state_marker: Option<Marker>,
    reader_marker: Option<Marker>,
    writer_marker: Option<Marker>,
}

#[pyclass(unsendable)]
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

    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<PyObject> {
        // Convert program to generator - handles KleisliProgramCall and other Program types
        let gen = self.to_generator(py, program.unbind())?;
        let gen_bound = gen.bind(py).clone();
        self.start_with_generator(gen_bound)?;

        loop {
            let event = self.run_rust_steps();

            match event {
                StepEvent::Done(value) => {
                    return value.to_pyobject(py).map(|v| v.unbind());
                }
                StepEvent::Error(e) => {
                    return Err(PyRuntimeError::new_err(e.to_string()));
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

    pub fn state_items(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = pyo3::types::PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    pub fn logs(&self, py: Python<'_>) -> PyResult<PyObject> {
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
                let gen = self.to_generator(py, program)?;
                Ok(PyCallOutcome::Value(Value::Python(gen)))
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
                    Ok(result) => Ok(PyCallOutcome::Value(Value::Python(result.unbind()))),
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
        }
    }

    fn to_generator(&self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
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
                    return Ok(Yielded::Primitive(ControlPrimitive::Delegate));
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
                _ => {}
            }
        }

        if obj.hasattr("to_generator")? {
            return Ok(Yielded::Program(obj.clone().unbind()));
        }

        if obj.hasattr("__iter__")? && obj.hasattr("__next__")? {
            return Ok(Yielded::Program(obj.clone().unbind()));
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
    pub fn state(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        if self.state_marker.is_none() {
            self.state_marker = Some(Marker::fresh());
        }
        Ok(py.None())
    }

    #[getter]
    pub fn reader(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        if self.reader_marker.is_none() {
            self.reader_marker = Some(Marker::fresh());
        }
        Ok(py.None())
    }

    #[getter]
    pub fn writer(&mut self, py: Python<'_>) -> PyResult<PyObject> {
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

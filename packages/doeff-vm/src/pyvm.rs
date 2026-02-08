use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::effect::{dispatch_from_shared, dispatch_to_pyobject, PyGet, PyPut, PyModify, PyAsk, PyTell, PyKPC};
#[cfg(test)]
use crate::effect::Effect;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::{
    Handler, HandlerEntry, KpcHandlerFactory, ReaderHandlerFactory, RustProgramHandlerRef, StateHandlerFactory,
    WriterHandlerFactory,
};
use crate::ids::{ContId, Marker};
use crate::py_shared::PyShared;
use crate::scheduler::SchedulerHandler;
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

#[pyclass(subclass, frozen, name = "EffectBase")]
pub struct PyEffectBase;

#[pyclass(subclass, frozen, name = "DoCtrlBase")]
pub struct PyDoCtrlBase;

#[pyclass(subclass, frozen, name = "DoThunkBase")]
pub struct PyDoThunkBase;

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
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![]);
        let prompt_seg_id = vm.alloc_segment(seg);
        vm.install_handler(
            marker,
            HandlerEntry::new(
                Handler::RustProgram(std::sync::Arc::new(KpcHandlerFactory)),
                prompt_seg_id,
            ),
        );
        PyVM { vm }
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
                let py_effect = dispatch_to_pyobject(py, &effect)?;
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
        let to_generator_method = program_bound.get_type().getattr("to_generator").ok();

        if let Some(to_gen) = to_generator_method {
            let gen = to_gen.call1((program_bound,))?;
            return Ok(gen.unbind());
        }

        let callable_type = program_bound
            .get_type()
            .name()
            .ok()
            .and_then(|n| n.extract::<String>().ok())
            .unwrap_or_default();
        if (callable_type == "function" || callable_type == "method") && program_bound.is_callable() {
            if let Ok(next) = program_bound.call0() {
                return self.to_generator_strict(py, next.unbind());
            }
        }

        let is_kpc = if let Ok(program_mod) = py.import("doeff.program") {
            if let Ok(kpc_cls) = program_mod.getattr("KleisliProgramCall") {
                program_bound.is_instance(&kpc_cls).unwrap_or(false)
            } else {
                false
            }
        } else {
            false
        };
        if is_kpc {
            let ns = pyo3::types::PyDict::new(py);
            ns.set_item("expr", program_bound)?;
            py.run(
                c"def _doexpr_to_generator():\n    value = yield expr\n    return value\n_gen = _doexpr_to_generator()",
                Some(&ns),
                Some(&ns),
            )?;
            let gen = ns
                .get_item("_gen")?
                .ok_or_else(|| PyRuntimeError::new_err("failed to construct top-level KPC generator"))?;
            return Ok(gen.into_any().unbind());
        }

        let classified = self.classify_yielded(py, &program_bound)?;
        match classified {
            Yielded::DoCtrl(_) | Yielded::Effect(_) => {
                let ns = pyo3::types::PyDict::new(py);
                ns.set_item("expr", program_bound)?;
                py.run(
                    c"def _doexpr_to_generator():\n    value = yield expr\n    return value\n_gen = _doexpr_to_generator()",
                    Some(&ns),
                    Some(&ns),
                )?;
                let gen = ns
                    .get_item("_gen")?
                    .ok_or_else(|| PyRuntimeError::new_err("failed to construct top-level DoExpr generator"))?;
                Ok(gen.into_any().unbind())
            }
            Yielded::Unknown(_) => Err(pyo3::exceptions::PyTypeError::new_err(
                "Expected ProgramBase (to_generator) or DoExpr (Effect/DoCtrl).",
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
                dispatch_from_shared(PyShared::new(eff.clone_ref(_py)))
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

        if obj.is_instance_of::<PyCreateContinuation>() {
            let cc: PyRef<'_, PyCreateContinuation> = obj.extract()?;
            let program = cc.program.clone_ref(_py);
            let handlers_list = cc.handlers.bind(_py);
            let mut handlers = Vec::new();
            let mut handler_identities = Vec::new();
            for item in handlers_list.try_iter()? {
                let item = item?;
                if item.is_instance_of::<PyRustHandlerSentinel>() {
                    let sentinel: PyRef<'_, PyRustHandlerSentinel> = item.extract()?;
                    handlers.push(Handler::RustProgram(sentinel.factory.clone()));
                    handler_identities.push(Some(PyShared::new(item.unbind())));
                } else {
                    handlers.push(Handler::Python(PyShared::new(item.unbind())));
                    handler_identities.push(None);
                }
            }
            return Ok(Yielded::DoCtrl(DoCtrl::CreateContinuation {
                expr: PyShared::new(program),
                handlers,
                handler_identities,
            }));
        }
        if obj.is_instance_of::<PyGetContinuation>() {
            return Ok(Yielded::DoCtrl(DoCtrl::GetContinuation));
        }
        if obj.is_instance_of::<PyGetHandlers>() {
            return Ok(Yielded::DoCtrl(DoCtrl::GetHandlers));
        }
        if obj.is_instance_of::<PyGetCallStack>() {
            return Ok(Yielded::DoCtrl(DoCtrl::GetCallStack));
        }
        if obj.is_instance_of::<PyAsyncEscape>() {
            let ae: PyRef<'_, PyAsyncEscape> = obj.extract()?;
            return Ok(Yielded::DoCtrl(DoCtrl::PythonAsyncSyntaxEscape {
                action: ae.action.clone_ref(_py),
            }));
        }

        if Self::is_effect_object(_py, obj)? {
            return Ok(Yielded::Effect(dispatch_from_shared(PyShared::new(
                obj.clone().unbind(),
            ))));
        }

        // R11-C: Program detection via isinstance (DoThunkBase = has to_generator)
        if obj.is_instance_of::<PyDoThunkBase>() {
            let metadata = Self::extract_call_metadata(_py, obj)
                .or_else(|| Self::extract_call_metadata_fallback(_py, obj))
                .unwrap_or_else(CallMetadata::anonymous);
            return Ok(Yielded::DoCtrl(DoCtrl::Call {
                f: PyShared::new(obj.clone().unbind()),
                args: vec![],
                kwargs: vec![],
                metadata,
            }));
        }

        Ok(Yielded::Unknown(obj.clone().unbind()))
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

    fn is_effect_object(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<bool> {
        if obj.is_instance_of::<PyEffectBase>() {
            return Ok(true);
        }

        if let Ok(types_mod) = py.import("doeff.types") {
            if let Ok(effect_base) = types_mod.getattr("EffectBase") {
                if obj.is_instance(&effect_base)? {
                    return Ok(true);
                }
            }
        }

        let ty = obj.get_type();
        let is_effect_base = ty
            .getattr("__doeff_effect_base__")
            .and_then(|v| v.extract::<bool>())
            .unwrap_or(false);
        let is_kpc = ty
            .getattr("__doeff_kpc__")
            .and_then(|v| v.extract::<bool>())
            .unwrap_or(false);

        if is_effect_base || is_kpc {
            return Ok(true);
        }

        Ok(false)
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
#[pyclass(name = "WithHandler", extends=PyDoCtrlBase)]
pub struct PyWithHandler {
    #[pyo3(get)]
    pub handler: Py<PyAny>,
    #[pyo3(get)]
    pub program: Py<PyAny>,
}

#[pymethods]
impl PyWithHandler {
    #[new]
    fn new(handler: Py<PyAny>, program: Py<PyAny>) -> (Self, PyDoCtrlBase) {
        (PyWithHandler { handler, program }, PyDoCtrlBase)
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
    fn new(program: Py<PyAny>, handlers: Py<PyAny>) -> (Self, PyDoCtrlBase) {
        (PyCreateContinuation { program, handlers }, PyDoCtrlBase)
    }
}

/// Request the current continuation.
#[pyclass(name = "GetContinuation", extends=PyDoCtrlBase)]
pub struct PyGetContinuation;

#[pymethods]
impl PyGetContinuation {
    #[new]
    fn new() -> (Self, PyDoCtrlBase) {
        (PyGetContinuation, PyDoCtrlBase)
    }
}

/// Request the current handler stack.
#[pyclass(name = "GetHandlers", extends=PyDoCtrlBase)]
pub struct PyGetHandlers;

#[pymethods]
impl PyGetHandlers {
    #[new]
    fn new() -> (Self, PyDoCtrlBase) {
        (PyGetHandlers, PyDoCtrlBase)
    }
}

/// Request the current call stack.
#[pyclass(name = "GetCallStack", extends=PyDoCtrlBase)]
pub struct PyGetCallStack;

#[pymethods]
impl PyGetCallStack {
    #[new]
    fn new() -> (Self, PyDoCtrlBase) {
        (PyGetCallStack, PyDoCtrlBase)
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
    fn new(action: Py<PyAny>) -> (Self, PyDoCtrlBase) {
        (PyAsyncEscape { action }, PyDoCtrlBase)
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
        let bound = Bound::new(py, PyClassInitializer::from(PyDoCtrlBase).add_subclass(wh))?;
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
                PyClassInitializer::from(PyDoCtrlBase).add_subclass(PyWithHandler {
                    handler: sentinel.clone_ref(py),
                    program: py.None().into_pyobject(py).unwrap().unbind().into_any(),
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
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    __doeff_scheduler_task_completed__ = True\n    def __init__(self, tid, value):\n        self.task = _TaskHandle(tid)\n        self.result = value\n\nobj = TaskCompletedEffect(7, 123)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();

            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
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
                PyCreateContinuation::new(py.None().into(), handlers_list.unbind().into()),
            )
            .unwrap()
            .into_any();
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
    fn test_g4_task_completed_error_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass _TaskHandle:\n    def __init__(self, tid):\n        self.task_id = tid\n\nclass TaskCompletedEffect(EffectBase):\n    __doeff_scheduler_task_completed__ = True\n    def __init__(self, tid, err):\n        self.task = _TaskHandle(tid)\n        self.error = err\n\nobj = TaskCompletedEffect(9, ValueError('boom'))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
                "G4 FAIL: expected opaque Python TaskCompleted effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_g5_kpc_classifies_as_effect_not_direct_call() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass _S:\n    def should_unwrap_positional(self, i):\n        return True\n    def should_unwrap_keyword(self, k):\n        return True\n\nclass KleisliProgramCall(EffectBase):\n    __doeff_kpc__ = True\n    function_name = 'f'\n    source_file = 'x.py'\n    source_line = 1\n    kleisli_source = None\n    def __init__(self):\n        self.args = (1,)\n        self.kwargs = {}\n        self.auto_unwrap_strategy = _S()\n        self.execution_kernel = (lambda x: x)\n    def to_generator(self):\n        if False:\n            yield None\n\nobj = KleisliProgramCall()\n",
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
    fn test_g6_malformed_gather_effect_classifies_as_opaque_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass GatherEffect(EffectBase):\n    __doeff_scheduler_gather__ = True\n    def __init__(self):\n        self.items = [123]\nobj = GatherEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
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
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass _Future:\n    def __init__(self):\n        self._handle = {'type': 'Task', 'task_id': 1}\n\nclass WaitEffect(EffectBase):\n    __doeff_scheduler_wait__ = True\n    def __init__(self):\n        self.future = _Future()\n\nobj = WaitEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
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
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self, p, hs, mode):\n        self.program = p\n        self.handlers = hs\n        self.store_mode = mode\nobj = SpawnEffect(None, [sentinel], 'isolated')\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
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
    fn test_spec_plain_to_generator_without_rust_base_classifies_as_unknown() {
        // R11-C: Plain Python objects with to_generator but no Rust base class
        // classify as Unknown. The Python _normalize_program layer wraps these.
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
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Unknown(_)),
                "R11-C: plain Python to_generator without DoThunkBase should be Unknown, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_raw_generator_classifies_as_unknown() {
        // R11-C: Raw generators without Rust base class classify as Unknown.
        // The Python _normalize_program layer rejects these.
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
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Unknown(_)),
                "R11-C: raw generators without DoThunkBase should be Unknown, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_stdlib_effects_classify_as_opaque_python_effects() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass StateGetEffect(EffectBase):\n    __doeff_state_get__ = True\n    def __init__(self):\n        self.key = 'counter'\nobj = StateGetEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
                "SPEC GAP: stdlib effects should classify as opaque Python effects, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_kpc_classifies_as_opaque_python_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass _S:\n    def should_unwrap_positional(self, i):\n        return True\n    def should_unwrap_keyword(self, k):\n        return True\n\nclass KleisliProgramCall(EffectBase):\n    __doeff_kpc__ = True\n    function_name = 'f'\n    source_file = 'x.py'\n    source_line = 1\n    kleisli_source = None\n    def __init__(self):\n        self.args = (1,)\n        self.kwargs = {}\n        self.auto_unwrap_strategy = _S()\n        self.execution_kernel = (lambda x: x)\n    def to_generator(self):\n        if False:\n            yield None\n\nobj = KleisliProgramCall()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
                "SPEC GAP: KPC should classify as opaque Python effect, got {:?}",
                yielded
            );
        });
    }

    #[test]
    fn test_spec_scheduler_spawn_classifies_as_opaque_python_effect() {
        Python::attach(|py| {
            let pyvm = PyVM { vm: VM::new() };
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class EffectBase:\n    __doeff_effect_base__ = True\n\nclass SpawnEffect(EffectBase):\n    __doeff_scheduler_spawn__ = True\n    def __init__(self):\n        self.program = None\n        self.handlers = []\n        self.store_mode = 'shared'\n\nobj = SpawnEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap();
            let yielded = pyvm.classify_yielded(py, &obj).unwrap();
            assert!(
                matches!(yielded, Yielded::Effect(Effect::Python(_))),
                "SPEC GAP: scheduler effects should classify as opaque Python effects, got {:?}",
                yielded
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
    m.add_class::<PyEffectBase>()?;
    m.add_class::<PyDoCtrlBase>()?;
    m.add_class::<PyDoThunkBase>()?;
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
    m.add_class::<PyCreateContinuation>()?;
    m.add_class::<PyGetContinuation>()?;
    m.add_class::<PyGetHandlers>()?;
    m.add_class::<PyGetCallStack>()?;
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
    // R11-A: #[pyclass] effect structs for isinstance checks
    m.add_class::<PyGet>()?;
    m.add_class::<PyPut>()?;
    m.add_class::<PyModify>()?;
    m.add_class::<PyAsk>()?;
    m.add_class::<PyTell>()?;
    m.add_class::<PyKPC>()?;
    // G09: KleisliProgramCall alias for PyKPC
    m.add("KleisliProgramCall", m.getattr("PyKPC")?)?;
    // KPC handler sentinel — explicit, not auto-installed
    m.add(
        "kpc",
        PyRustHandlerSentinel {
            factory: Arc::new(KpcHandlerFactory),
        },
    )?;
    // G14: scheduler sentinel
    m.add(
        "scheduler",
        PyRustHandlerSentinel {
            factory: Arc::new(SchedulerHandler::new()),
        },
    )?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(async_run, m)?)?;
    Ok(())
}

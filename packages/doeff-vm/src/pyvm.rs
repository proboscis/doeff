//! PyVM — Python entry point for running programs on the VM.
//!
//! Provides the `run()` function that Python calls to execute a doeff program.

use pyo3::prelude::*;

use doeff_vm_core::continuation::PyK;
use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::driver::{Mode, StepResult};
use doeff_vm_core::frame::Frame;
use doeff_vm_core::ir_stream::IRStreamRef;
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::segment::Fiber;
use doeff_vm_core::value::{CallableRef, Value};
use doeff_vm_core::VM;

use crate::python_generator_stream::{python_to_value, value_to_python, PythonGeneratorStream};

/// The Python-visible VM wrapper.
#[pyclass(name = "PyVM")]
pub struct PyVM {
    vm: VM,
}

#[pymethods]
impl PyVM {
    #[new]
    fn new() -> Self {
        PyVM { vm: VM::new() }
    }

    /// Run a Python generator program to completion.
    fn run(&mut self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
        self.vm.begin_run_session();

        let stream = PythonGeneratorStream::new(PyShared::new(program));
        let stream_ref = IRStreamRef::new(Box::new(stream));

        let mut root_fiber = Fiber::new(None);
        root_fiber.push_frame(Frame::program(stream_ref, None));
        let root_fid = self.vm.alloc_segment(root_fiber);
        self.vm.current_segment = Some(root_fid);
        self.vm.mode = Mode::Send(Value::Unit);

        let result = self.step_loop(py)?;
        self.vm.end_active_run_session();

        Ok(value_to_python(py, result).unbind())
    }

    /// Run a program with a handler.
    fn run_with_handler(
        &mut self,
        py: Python<'_>,
        handler: Py<PyAny>,
        program: Py<PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.vm.begin_run_session();

        let handler_callable = PythonCallable::new(handler.clone_ref(py));
        let handler_value = Value::Callable(std::sync::Arc::new(handler_callable) as CallableRef);

        let body_stream = PythonGeneratorStream::new(PyShared::new(program));
        let body_ref = IRStreamRef::new(Box::new(body_stream));
        let body_value = Value::Stream(body_ref);

        let root_stream = WithHandlerRootStream {
            handler: Some(handler_value),
            body: Some(body_value),
            done: false,
        };
        let root_ref = IRStreamRef::new(Box::new(root_stream));

        let mut root_fiber = Fiber::new(None);
        root_fiber.push_frame(Frame::program(root_ref, None));
        let root_fid = self.vm.alloc_segment(root_fiber);
        self.vm.current_segment = Some(root_fid);
        self.vm.mode = Mode::Send(Value::Unit);

        let result = self.step_loop(py)?;
        self.vm.end_active_run_session();

        Ok(value_to_python(py, result).unbind())
    }
}

impl PyVM {
    fn step_loop(&mut self, _py: Python<'_>) -> PyResult<Value> {
        for _ in 0..100_000 {
            match self.vm.step() {
                StepResult::Continue => continue,
                StepResult::Done(value) => return Ok(value),
                StepResult::Error(err) => {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!("{}", err)));
                }
                StepResult::External(call) => {
                    match call.callable {
                        Value::Callable(callable) => {
                            match callable.call(call.args) {
                                Ok(value) => self.vm.receive_external_result(Ok(value)),
                                Err(err) => {
                                    return Err(pyo3::exceptions::PyRuntimeError::new_err(
                                        format!("{}", err),
                                    ));
                                }
                            }
                        }
                        _ => {
                            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                                "external call: not callable",
                            ));
                        }
                    }
                }
            }
        }
        Err(pyo3::exceptions::PyRuntimeError::new_err("step limit exceeded"))
    }
}

// ---------------------------------------------------------------------------
// PythonCallable — wraps a Python callable as a Callable trait
// ---------------------------------------------------------------------------

#[derive(Debug)]
struct PythonCallable {
    callable: Py<PyAny>,
}

impl PythonCallable {
    fn new(callable: Py<PyAny>) -> Self {
        Self { callable }
    }
}

impl doeff_vm_core::value::Callable for PythonCallable {
    fn call(&self, args: Vec<Value>) -> Result<Value, doeff_vm_core::VMError> {
        Python::attach(|py| {
            let py_args: Vec<Py<PyAny>> = args
                .into_iter()
                .map(|v| value_to_python(py, v).unbind())
                .collect();
            let py_tuple = pyo3::types::PyTuple::new(py, &py_args)
                .map_err(|e| doeff_vm_core::VMError::python_error(format!("{e}")))?;

            match self.callable.call(py, py_tuple, None) {
                Ok(result) => {
                    let bound = result.bind(py);
                    if bound.hasattr("send").unwrap_or(false)
                        && bound.hasattr("throw").unwrap_or(false)
                    {
                        let stream = PythonGeneratorStream::new(PyShared::new(result.clone_ref(py)));
                        let stream_ref = IRStreamRef::new(Box::new(stream));
                        Ok(Value::Stream(stream_ref))
                    } else {
                        Ok(python_to_value(py, bound))
                    }
                }
                Err(err) => Err(doeff_vm_core::VMError::python_error(format!("{err}"))),
            }
        })
    }
}

// ---------------------------------------------------------------------------
// WithHandlerRootStream
// ---------------------------------------------------------------------------

#[derive(Debug)]
struct WithHandlerRootStream {
    handler: Option<Value>,
    body: Option<Value>,
    done: bool,
}

impl doeff_vm_core::ir_stream::IRStream for WithHandlerRootStream {
    fn resume(&mut self, value: Value) -> doeff_vm_core::ir_stream::StreamStep {
        if !self.done {
            self.done = true;
            doeff_vm_core::ir_stream::StreamStep::Instruction(DoCtrl::WithHandler {
                handler: self.handler.take().unwrap(),
                body: self.body.take().unwrap(),
            })
        } else {
            doeff_vm_core::ir_stream::StreamStep::Done(value)
        }
    }

    fn throw(&mut self, error: Value) -> doeff_vm_core::ir_stream::StreamStep {
        doeff_vm_core::ir_stream::StreamStep::Error(error)
    }
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register_pyvm(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_class::<PyVM>()?;
    m.add_class::<PyK>()?;
    Ok(())
}

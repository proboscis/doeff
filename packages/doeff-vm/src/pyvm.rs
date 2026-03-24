//! PyVM — Python entry point for running programs on the VM.

use pyo3::prelude::*;

use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::driver::{Mode, StepResult};
use doeff_vm_core::frame::Frame;
use doeff_vm_core::ir_stream::IRStreamRef;
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::segment::Fiber;
use doeff_vm_core::value::{CallableRef, Value};
use doeff_vm_core::VM;

use crate::python_generator_stream::{
    classify_python_object, python_to_value, value_to_python, PythonCallable, PythonGeneratorStream,
};

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

    /// Run a DoExpr program to completion.
    /// `program` must be a DoExpr (Python object with `tag` attribute).
    fn run(&mut self, py: Python<'_>, program: Py<PyAny>) -> PyResult<Py<PyAny>> {
        let doctrl = classify_program(py, &program)?;
        self.run_doctrl(py, doctrl)
    }

    /// Run a DoExpr program under a handler.
}

impl PyVM {
    /// Run a DoCtrl to completion.
    fn run_doctrl(&mut self, py: Python<'_>, doctrl: DoCtrl) -> PyResult<Py<PyAny>> {
        self.vm.begin_run_session();

        // Create root fiber
        let root_fiber = Fiber::new(None);
        let root_fid = self.vm.alloc_segment(root_fiber);
        self.vm.current_segment = Some(root_fid);
        self.vm.mode = Mode::Eval(doctrl);

        let result = self.step_loop()?;
        self.vm.end_active_run_session();

        Ok(value_to_python(py, result).unbind())
    }

    fn step_loop(&mut self) -> PyResult<Value> {
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
// classify_program
// ---------------------------------------------------------------------------

fn classify_program(py: Python<'_>, program: &Py<PyAny>) -> PyResult<DoCtrl> {
    classify_python_object(py, program.bind(py))
        .map_err(|msg| pyo3::exceptions::PyTypeError::new_err(msg))
}

// PythonCallable lives in python_generator_stream.rs

// ---------------------------------------------------------------------------
// DoctrlStream — wraps a DoCtrl as a single-instruction IRStream
// ---------------------------------------------------------------------------

/// An IRStream that yields a single DoCtrl instruction, then returns the result.
#[derive(Debug)]
struct DoctrlStream {
    doctrl: Option<DoCtrl>,
}

impl doeff_vm_core::ir_stream::IRStream for DoctrlStream {
    fn resume(&mut self, value: Value) -> doeff_vm_core::ir_stream::StreamStep {
        match self.doctrl.take() {
            Some(doctrl) => doeff_vm_core::ir_stream::StreamStep::Instruction(doctrl),
            None => doeff_vm_core::ir_stream::StreamStep::Done(value),
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
    m.add_class::<doeff_vm_core::continuation::PyK>()?;
    m.add_class::<crate::python_generator_stream::PythonCallable>()?;
    m.add_class::<crate::python_generator_stream::PyEffectBase>()?;
    m.add_class::<crate::python_generator_stream::PyIRStream>()?;
    m.add_class::<crate::result::PyResultOk>()?;
    m.add_class::<crate::result::PyResultErr>()?;
    Ok(())
}

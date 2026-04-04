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

    /// Return arena diagnostics: (live_fibers, slot_count, free_list_len, var_cells).
    fn arena_stats(&self) -> (usize, usize, usize, usize) {
        (
            self.vm.segments.len(),
            self.vm.segments.slot_count(),
            self.vm.segments.capacity(),
            self.vm.var_store.cells.len(),
        )
    }

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

    /// Convert a VMError to a Python exception.
    /// For UncaughtException with a Python error inside, re-raise the original.
    /// For unhandled/no-matching handler errors, include the effect type name
    /// and attach __doeff_traceback__ from the VM's last_error_context.
    fn convert_vm_error(&mut self, err: doeff_vm_core::VMError) -> pyo3::PyErr {
        match err {
            doeff_vm_core::VMError::UncaughtException { exception } => {
                let ctx = self.vm.last_error_context.take();
                Python::attach(|py| {
                    let py_obj = value_to_python(py, exception);
                    // Attach VM-captured traceback to the exception
                    if let Some(frames) = ctx {
                        if !frames.is_empty() {
                            let py_frames: Vec<_> = frames.into_iter()
                                .map(|v| value_to_python(py, v))
                                .collect();
                            if let Ok(tb_list) = pyo3::types::PyList::new(py, &py_frames) {
                                let _ = py_obj.setattr("__doeff_traceback__", tb_list);
                            }
                        }
                    }
                    if py_obj.is_instance_of::<pyo3::exceptions::PyBaseException>() {
                        pyo3::PyErr::from_value(py_obj.unbind().into_bound(py))
                    } else {
                        pyo3::exceptions::PyRuntimeError::new_err(
                            format!("uncaught exception: {:?}", py_obj)
                        )
                    }
                })
            }
            doeff_vm_core::VMError::UnhandledEffect { effect } => {
                self.make_effect_error("unhandled effect", &effect)
            }
            doeff_vm_core::VMError::NoMatchingHandler { effect } => {
                self.make_effect_error("no handler found for effect", &effect)
            }
            doeff_vm_core::VMError::DelegateNoOuterHandler { effect } => {
                self.make_effect_error("Pass: no outer handler", &effect)
            }
            other => pyo3::exceptions::PyRuntimeError::new_err(format!("{}", other)),
        }
    }

    /// Create a RuntimeError for an unhandled effect, with __doeff_traceback__ attached.
    fn make_effect_error(&mut self, label: &str, effect: &Value) -> pyo3::PyErr {
        let ctx = self.vm.last_error_context.take();
        Python::attach(|py| {
            let desc = Self::describe_effect(py, effect);
            let msg = format!("{}: {}", label, desc);
            let err = pyo3::exceptions::PyRuntimeError::new_err(msg);
            // Attach doeff traceback if captured
            if let Some(frames) = ctx {
                let py_frames: Vec<_> = frames.into_iter()
                    .map(|v| value_to_python(py, v))
                    .collect();
                let tb_list = pyo3::types::PyList::new(py, &py_frames).unwrap();
                let exc_val = err.value(py);
                let _ = exc_val.setattr("__doeff_traceback__", tb_list);
            }
            err
        })
    }

    /// Get a human-readable description of an effect value (type name + repr).
    fn describe_effect(py: Python<'_>, effect: &Value) -> String {
        match effect {
            Value::Opaque(shared) => {
                let obj = shared.inner().bind(py);
                let type_name = obj.get_type().qualname()
                    .map(|n| n.to_string())
                    .unwrap_or_else(|_| "<unknown>".to_string());
                let repr = obj.repr()
                    .map(|r| r.to_string())
                    .unwrap_or_else(|_| type_name.clone());
                if repr == type_name {
                    type_name
                } else {
                    format!("{} ({})", type_name, repr)
                }
            }
            other => format!("{:?}", other),
        }
    }

    fn step_loop(&mut self) -> PyResult<Value> {
        loop {
            match self.vm.step() {
                StepResult::Continue => continue,
                StepResult::Done(value) => return Ok(value),
                StepResult::Error(err) => {
                    // Ensure context is captured for ALL error paths
                    if self.vm.last_error_context.is_none() {
                        self.vm.last_error_context =
                            Some(self.vm.collect_rich_execution_context());
                    }
                    return Err(self.convert_vm_error(err));
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
    // DoExpr pyclasses
    m.add_class::<crate::do_expr::PyPure>()?;
    m.add_class::<crate::do_expr::PyPerform>()?;
    m.add_class::<crate::do_expr::PyResume>()?;
    m.add_class::<crate::do_expr::PyTransfer>()?;
    m.add_class::<crate::do_expr::PyApply>()?;
    m.add_class::<crate::do_expr::PyExpand>()?;
    m.add_class::<crate::do_expr::PyPass>()?;
    m.add_class::<crate::do_expr::PyWithHandler>()?;
    m.add_class::<crate::do_expr::PyResumeThrow>()?;
    m.add_class::<crate::do_expr::PyTransferThrow>()?;
    m.add_class::<crate::do_expr::PyWithObserve>()?;
    m.add_class::<crate::do_expr::PyGetTraceback>()?;
    m.add_class::<crate::do_expr::PyGetExecutionContext>()?;
    m.add_class::<crate::do_expr::PyGetHandlers>()?;
    Ok(())
}

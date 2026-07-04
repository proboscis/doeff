//! PyVM — Python entry point for running programs on the VM.

use pyo3::create_exception;
use pyo3::prelude::*;

use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::driver::{Signal, StepResult};
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::segment::Fiber;
use doeff_vm_core::value::Value;
use doeff_vm_core::VM;

use crate::python_generator_stream::{classify_python_object, value_to_python};

create_exception!(
    doeff_vm,
    UnhandledEffect,
    pyo3::exceptions::PyRuntimeError,
    "Raised when an effect reaches the top of the handler stack with no handler. \
     The exception's args[0] is the human-readable message; the original effect \
     is available via the doeff traceback attached to ``__doeff_traceback__``."
);

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

        let result = self.step_loop(Signal::eval(doctrl))?;
        self.vm.end_active_run_session();

        Ok(value_to_python(py, result).unbind())
    }

    /// Convert a VMError to a Python exception.
    /// For UncaughtException with a Python error inside, re-raise the original.
    /// For unhandled/no-matching handler errors, include the effect type name
    /// and attach __doeff_traceback__ from the threaded diagnostic context.
    fn convert_vm_error(
        &mut self,
        py: Python<'_>,
        err: doeff_vm_core::VMError,
        context: Option<Vec<Value>>,
    ) -> pyo3::PyErr {
        match err {
            doeff_vm_core::VMError::OneShotViolation { fiber_id } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "one-shot violation: continuation {:?} already consumed",
                    fiber_id
                ))
            }
            doeff_vm_core::VMError::UncaughtException { exception } => {
                let py_obj = value_to_python(py, exception);
                // Attach VM-captured traceback to the exception
                if let Some(frames) = context {
                    if !frames.is_empty() {
                        let py_frames: Vec<_> =
                            frames.into_iter().map(|v| value_to_python(py, v)).collect();
                        if let Ok(tb_list) = pyo3::types::PyList::new(py, &py_frames) {
                            let _ = py_obj.setattr("__doeff_traceback__", tb_list);
                        }
                    }
                }
                if py_obj.is_instance_of::<pyo3::exceptions::PyBaseException>() {
                    pyo3::PyErr::from_value(py_obj.unbind().into_bound(py))
                } else {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "uncaught exception: {:?}",
                        py_obj
                    ))
                }
            }
            doeff_vm_core::VMError::UnhandledEffect { effect } => {
                self.make_unhandled_effect_error(py, "unhandled effect", &effect, context)
            }
            doeff_vm_core::VMError::NoMatchingHandler { effect } => self
                .make_unhandled_effect_error(py, "no handler found for effect", &effect, context),
            doeff_vm_core::VMError::DelegateNoOuterHandler { effect } => {
                self.make_unhandled_effect_error(py, "Pass: no outer handler", &effect, context)
            }
            doeff_vm_core::VMError::HandlerNotFound { marker } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "handler not found for marker {}",
                    marker.raw()
                ))
            }
            doeff_vm_core::VMError::InvalidSegment { message } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!("invalid segment: {}", message))
            }
            doeff_vm_core::VMError::PythonError { message } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Python error: {}", message))
            }
            doeff_vm_core::VMError::InternalError { message } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!("internal error: {}", message))
            }
            doeff_vm_core::VMError::TypeError { message } => {
                pyo3::exceptions::PyRuntimeError::new_err(format!("type error: {}", message))
            }
        }
    }

    /// Create an UnhandledEffect for an unhandled effect, with __doeff_traceback__ attached.
    fn make_unhandled_effect_error(
        &self,
        py: Python<'_>,
        label: &str,
        effect: &Value,
        context: Option<Vec<Value>>,
    ) -> pyo3::PyErr {
        let desc = Self::describe_effect(py, effect);
        let chain: Vec<String> = context
            .as_ref()
            .and_then(|c| Self::extract_handler_chain(c))
            .unwrap_or_default();
        let msg = if chain.is_empty() {
            format!("{}: {} (no handlers in scope)", label, desc)
        } else {
            format!(
                "{}: {}\n  handlers in scope (innermost→outermost): {}",
                label,
                desc,
                chain.join(" → "),
            )
        };
        let err = UnhandledEffect::new_err(msg);
        // Attach doeff traceback if captured
        if let Some(frames) = context {
            let py_frames: Vec<_> = frames.into_iter().map(|v| value_to_python(py, v)).collect();
            let tb_list = pyo3::types::PyList::new(py, &py_frames).unwrap();
            let exc_val = err.value(py);
            let _ = exc_val.setattr("__doeff_traceback__", tb_list);
        }
        err
    }

    /// Returns true for VMErrors that should be re-raised into the IRStream
    /// so user @do try/except blocks can catch them.
    fn should_raise_into_stream(error: &doeff_vm_core::VMError) -> bool {
        matches!(
            error,
            doeff_vm_core::VMError::UnhandledEffect { .. }
                | doeff_vm_core::VMError::NoMatchingHandler { .. }
                | doeff_vm_core::VMError::DelegateNoOuterHandler { .. }
        )
    }

    /// Serializes a VMError into a Python-exception Value::Opaque for
    /// re-injection into the IRStream.
    fn vm_error_to_exception_value(
        &mut self,
        error: doeff_vm_core::VMError,
        context: Option<Vec<Value>>,
    ) -> Value {
        Python::attach(|py| {
            let err = self.convert_vm_error(py, error, context);
            Value::Opaque(PyShared::new(err.value(py).clone().into_any().unbind()))
        })
    }

    /// Pull the handler-chain entry out of a captured execution context.
    ///
    /// The VM stores the chain as ``["handler", "chain", [name1, name2, ...]]``
    /// in the threaded diagnostic context. We strip consecutive duplicates to avoid the
    /// ``handler.<locals>.clause`` repeats that Python closures produce.
    fn extract_handler_chain(ctx: &[Value]) -> Option<Vec<String>> {
        for entry in ctx {
            let Value::List(items) = entry else { continue };
            if items.len() < 3 {
                continue;
            }
            let (Value::String(kind), Value::String(subkind)) = (&items[0], &items[1]) else {
                continue;
            };
            if kind != "handler" || subkind != "chain" {
                continue;
            }
            let Value::List(names) = &items[2] else {
                continue;
            };
            let mut out: Vec<String> = Vec::new();
            for n in names {
                let Value::String(raw) = n else { continue };
                // Mirror the traceback renderer: take everything before the
                // first ``.<locals>.``. This surfaces the factory name for
                // the core handler pattern (``lazy_ask.<locals>.handler``
                // → ``lazy_ask``) while keeping unqualified names intact.
                let base = match raw.split_once(".<locals>.") {
                    Some((outer, _)) => outer,
                    None => raw.as_str(),
                };
                // Drop module prefixes: ``pkg.mod.foo`` → ``foo``.
                let trimmed = base.rsplit('.').next().unwrap_or(base).to_string();
                if out.last().map_or(true, |prev| prev != &trimmed) {
                    out.push(trimmed);
                }
            }
            return Some(out);
        }
        None
    }

    /// Get a human-readable description of an effect value (type name + repr).
    fn describe_effect(py: Python<'_>, effect: &Value) -> String {
        match effect {
            Value::Opaque(shared) => {
                let obj = shared.inner().bind(py);
                let type_name = obj
                    .get_type()
                    .qualname()
                    .map(|n| n.to_string())
                    .unwrap_or_else(|_| "<unknown>".to_string());
                let repr = obj
                    .repr()
                    .map(|r| r.to_string())
                    .unwrap_or_else(|_| type_name.clone());
                if repr == type_name {
                    type_name
                } else {
                    format!("{} ({})", type_name, repr)
                }
            }
            Value::Unit => "Unit".to_string(),
            Value::Int(value) => format!("Int({})", value),
            Value::Bool(value) => format!("Bool({})", value),
            Value::String(value) => format!("String({:?})", value),
            Value::None => "None".to_string(),
            Value::Callable(callable) => callable
                .name()
                .map(|name| format!("Callable({})", name))
                .unwrap_or_else(|| "Callable(<anonymous>)".to_string()),
            Value::Stream(_) => "Stream(<opaque>)".to_string(),
            Value::Continuation(_) => "Continuation(<detached>)".to_string(),
            Value::Var(var) => format!("Var({:?})", var),
            Value::List(items) => format!("List(len={})", items.len()),
        }
    }

    fn step_loop(&mut self, mut signal: Signal) -> PyResult<Value> {
        loop {
            match self.vm.step(signal) {
                StepResult::Continue(next_signal) => {
                    signal = next_signal;
                }
                StepResult::Done(value) => return Ok(value),
                StepResult::Error { error, context } => {
                    let context =
                        context.or_else(|| Some(self.vm.collect_rich_execution_context()));
                    if Self::should_raise_into_stream(&error) && self.vm.current_segment.is_some() {
                        let exception = self.vm_error_to_exception_value(error, context.clone());
                        signal = Signal::raise(exception).with_error_context(context);
                        continue;
                    }
                    return Err(Python::attach(|py| {
                        self.convert_vm_error(py, error, context)
                    }));
                }
                StepResult::External { call, context } => {
                    match call.callable {
                        Value::Callable(callable) => {
                            match callable.call(call.args) {
                                Ok(value) => {
                                    signal = Signal::from_external_result(Ok(value))
                                        .with_error_context(context);
                                }
                                Err(doeff_vm_core::VMError::UncaughtException { exception }) => {
                                    // Route Python exceptions through VM error
                                    // handling so try/except blocks can catch them.
                                    signal = Signal::from_external_result(Err(exception))
                                        .with_error_context(context);
                                }
                                Err(err) => {
                                    return Err(pyo3::exceptions::PyRuntimeError::new_err(
                                        format!("{}", err),
                                    ));
                                }
                            }
                        }
                        Value::Unit
                        | Value::Int(_)
                        | Value::Bool(_)
                        | Value::String(_)
                        | Value::None
                        | Value::Stream(_)
                        | Value::Continuation(_)
                        | Value::Var(_)
                        | Value::List(_)
                        | Value::Opaque(_) => {
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
    m.add("UnhandledEffect", m.py().get_type::<UnhandledEffect>())?;
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
    m.add_class::<crate::do_expr::PyGetObservers>()?;
    m.add_class::<crate::do_expr::PyGetOuterHandlers>()?;
    m.add_class::<crate::do_expr::PyTailEval>()?;
    Ok(())
}

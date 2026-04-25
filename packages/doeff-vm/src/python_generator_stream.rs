//! PythonGeneratorStream — adapts a Python generator to the VM's IRStream trait.
//!
//! A Python generator that yields DoCtrl-like objects (Resume, Transfer, Perform, etc.)
//! is wrapped into an IRStream that the VM can step through.
//!
//! The key operation: generator.send(value) → classify the yielded Python object → DoCtrl.

use pyo3::exceptions::PyStopIteration;
use pyo3::prelude::*;
use pyo3::types::PyString;

use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::ir_stream::{IRStream, StreamStep};
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::value::Value;

/// Base class for Python effects. Subclass this in Python to define effects.
/// The Rust side uses `is_instance_of::<PyEffectBase>()` for classification.
///
/// Yielding an EffectBase from a generator is implicitly treated as Perform(effect).
#[pyclass(name = "EffectBase", subclass, dict, module = "doeff_vm.doeff_vm")]
#[derive(Debug)]
pub struct PyEffectBase;

#[pymethods]
impl PyEffectBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(
        _args: &Bound<'_, pyo3::types::PyTuple>,
        _kwargs: Option<&Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self
    }

    /// Pickle support: return (copyreg.__newobj__, (cls,), __dict__).
    /// copyreg.__newobj__(cls) calls cls.__new__(cls), then pickle sets
    /// obj.__dict__.update(state) for the third element.
    fn __reduce_ex__(slf: &Bound<'_, Self>, _protocol: i32) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let copyreg = py.import("copyreg")?;
        let newobj = copyreg.getattr("__newobj__")?;
        let cls = slf.get_type();
        let args = pyo3::types::PyTuple::new(py, &[cls.as_any()])?;
        let state = slf.getattr("__dict__")?;
        Ok(
            pyo3::types::PyTuple::new(py, &[newobj.as_any(), args.as_any(), &state])?
                .into_any()
                .unbind(),
        )
    }
}

/// Wraps a Python callable as a VM Callable.
/// Exported to Python as `Callable` — users must explicitly wrap.
#[pyclass(name = "Callable", module = "doeff_vm.doeff_vm")]
#[derive(Debug)]
pub struct PythonCallable {
    pub callable: Py<PyAny>,
}

#[pymethods]
impl PythonCallable {
    #[new]
    pub fn new(callable: Py<PyAny>) -> Self {
        Self { callable }
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<(Py<PyAny>, (Py<PyAny>,))> {
        let cls = py.get_type::<Self>().into_any().unbind();
        Ok((cls, (self.callable.clone_ref(py),)))
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
                    Ok(python_to_value(py, bound))
                }
                Err(err) => Err(doeff_vm_core::VMError::uncaught_exception(Value::Opaque(
                    PyShared::new(err.value(py).clone().into_any().unbind()),
                ))),
            }
        })
    }

    fn name(&self) -> Option<String> {
        Python::attach(|py| {
            let obj = self.callable.bind(py);
            obj.getattr("__qualname__")
                .or_else(|_| obj.getattr("__name__"))
                .ok()
                .and_then(|n| n.extract::<String>().ok())
        })
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn call_handler(
        &self,
        args: Vec<Value>,
    ) -> Result<doeff_vm_core::do_ctrl::DoCtrl, doeff_vm_core::VMError> {
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
                    classify_python_object(py, &bound).map_err(|msg| {
                        doeff_vm_core::VMError::type_error(format!(
                            "handler must return DoExpr: {}",
                            msg
                        ))
                    })
                }
                Err(err) => Err(doeff_vm_core::VMError::uncaught_exception(Value::Opaque(
                    PyShared::new(err.value(py).clone().into_any().unbind()),
                ))),
            }
        })
    }
}

/// Python-visible wrapper: creates a PythonGeneratorStream from a generator.
/// Recognized by python_to_value → Value::Stream.
#[pyclass(name = "IRStream", module = "doeff_vm.doeff_vm")]
#[derive(Debug)]
pub struct PyIRStream {
    pub generator: Py<PyAny>,
    pub tail_resume_lines: Vec<u32>,
}

#[pymethods]
impl PyIRStream {
    #[new]
    #[pyo3(signature = (generator, tail_resume_lines=None))]
    pub fn new(generator: Py<PyAny>, tail_resume_lines: Option<Vec<u32>>) -> Self {
        Self {
            generator,
            tail_resume_lines: tail_resume_lines.unwrap_or_default(),
        }
    }
}

/// A Python generator wrapped as an IRStream.
///
/// The generator yields Python objects that are classified into DoCtrl instructions.
/// When the generator returns (StopIteration), the stream is done.
#[derive(Debug)]
pub struct PythonGeneratorStream {
    generator: PyShared,
    tail_resume_lines: Vec<u32>,
    exhausted: bool,
    /// Last known source location, preserved after generator exhaustion.
    last_location: Option<doeff_vm_core::ir_stream::StreamSourceLocation>,
}

impl PythonGeneratorStream {
    pub fn new(generator: PyShared, tail_resume_lines: Vec<u32>) -> Self {
        Self {
            generator,
            tail_resume_lines,
            exhausted: false,
            last_location: None,
        }
    }

    /// Extract source location from exception's __traceback__ + generator's gi_code.
    /// Called just before marking exhausted, to preserve the error site location.
    fn location_from_exception(
        py: Python<'_>,
        generator: &PyShared,
        err: &pyo3::PyErr,
    ) -> Option<doeff_vm_core::ir_stream::StreamSourceLocation> {
        let gen = generator.bind(py);
        let code = gen.getattr("gi_code").ok()?;
        let func_name = code
            .getattr("co_qualname")
            .or_else(|_| code.getattr("co_name"))
            .ok()?
            .extract::<String>()
            .ok()?;
        let source_file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;

        // Get line number from the exception's traceback (most accurate for raise site)
        let source_line = err
            .traceback(py)
            .and_then(|tb| tb.getattr("tb_lineno").ok())
            .and_then(|l| l.extract::<u32>().ok())
            .unwrap_or_else(|| {
                code.getattr("co_firstlineno")
                    .ok()
                    .and_then(|l| l.extract::<u32>().ok())
                    .unwrap_or(0)
            });

        Some(doeff_vm_core::ir_stream::StreamSourceLocation {
            func_name,
            source_file,
            source_line,
        })
    }

    /// Call generator.send(value) and classify the result.
    fn send_to_generator(&mut self, py_value: &Bound<'_, PyAny>) -> StreamStep {
        Python::attach(|py| {
            let gen = self.generator.bind(py);
            match gen.call_method1("send", (py_value,)) {
                Ok(yielded) => self.classify_yielded(py, &yielded),
                Err(err) if err.is_instance_of::<PyStopIteration>(py) => {
                    self.exhausted = true;
                    let return_value = err
                        .value(py)
                        .getattr("value")
                        .ok()
                        .map(|v| python_to_value(py, &v))
                        .unwrap_or(Value::Unit);
                    StreamStep::Done(return_value)
                }
                Err(err) => {
                    self.last_location = Self::location_from_exception(py, &self.generator, &err);
                    self.exhausted = true;
                    StreamStep::Error(Value::Opaque(PyShared::new(
                        err.value(py).clone().into_any().unbind(),
                    )))
                }
            }
        })
    }

    /// Call generator.throw(error) and classify the result.
    fn throw_to_generator(&mut self, py_error: &Bound<'_, PyAny>) -> StreamStep {
        Python::attach(|py| {
            let gen = self.generator.bind(py);
            match gen.call_method1("throw", (py_error,)) {
                Ok(yielded) => self.classify_yielded(py, &yielded),
                Err(err) if err.is_instance_of::<PyStopIteration>(py) => {
                    self.exhausted = true;
                    let return_value = err
                        .value(py)
                        .getattr("value")
                        .ok()
                        .map(|v| python_to_value(py, &v))
                        .unwrap_or(Value::Unit);
                    StreamStep::Done(return_value)
                }
                Err(err) => {
                    self.last_location = Self::location_from_exception(py, &self.generator, &err);
                    self.exhausted = true;
                    StreamStep::Error(Value::Opaque(PyShared::new(
                        err.value(py).clone().into_any().unbind(),
                    )))
                }
            }
        })
    }

    /// Classify a yielded Python object into a DoCtrl instruction.
    ///
    /// classify_python_object handles all cases:
    /// DoExpr (has tag), EffectBase (implicit Perform), or error.
    fn classify_yielded(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> StreamStep {
        if is_tail_resume_candidate(obj) {
            if let Some(line) = generator_current_line(py, &self.generator) {
                if self.tail_resume_lines.contains(&line) {
                    match classify_tail_resume(py, obj) {
                        Ok(Some(doctrl)) => return StreamStep::Instruction(doctrl),
                        Ok(None) => {}
                        Err(msg) => return StreamStep::Error(Value::String(msg)),
                    }
                }
            }
        }
        match classify_python_object(py, obj) {
            Ok(doctrl) => StreamStep::Instruction(doctrl),
            Err(msg) => StreamStep::Error(Value::String(msg)),
        }
    }
}

impl IRStream for PythonGeneratorStream {
    fn resume(&mut self, value: Value) -> StreamStep {
        if self.exhausted {
            return StreamStep::Done(Value::Unit);
        }
        Python::attach(|py| {
            let py_value = value_to_python(py, value);
            self.send_to_generator(&py_value)
        })
    }

    fn throw(&mut self, error: Value) -> StreamStep {
        if self.exhausted {
            return StreamStep::Error(error);
        }
        Python::attach(|py| {
            let py_error = value_to_python(py, error);
            // gen.throw() requires a BaseException. If the value isn't one
            // (e.g., a string from a VM internal error), wrap it in RuntimeError.
            if py_error.is_instance_of::<pyo3::exceptions::PyBaseException>() {
                self.throw_to_generator(&py_error)
            } else {
                let msg = py_error
                    .repr()
                    .map(|r| r.to_string())
                    .unwrap_or_else(|_| format!("{:?}", py_error));
                let exc = pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "VM error (non-exception value in Raise): {}",
                    msg
                ));
                let exc_val = exc.value(py);
                self.throw_to_generator(exc_val)
            }
        })
    }

    fn source_location(&self) -> Option<doeff_vm_core::ir_stream::StreamSourceLocation> {
        if self.exhausted {
            // Generator done — return last known location (captured before exhaustion)
            return self.last_location.clone();
        }
        Python::attach(|py| {
            let gen = self.generator.bind(py);

            // func_name and source_file from gi_code (stable)
            let code = gen.getattr("gi_code").ok()?;
            let func_name = code
                .getattr("co_qualname")
                .or_else(|_| code.getattr("co_name"))
                .ok()?
                .extract::<String>()
                .ok()?;
            let source_file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;

            // source_line from gi_frame.f_lineno (live — current yield site)
            let source_line = gen
                .getattr("gi_frame")
                .ok()
                .and_then(|frame| frame.getattr("f_lineno").ok())
                .and_then(|lineno| lineno.extract::<u32>().ok())
                .unwrap_or_else(|| {
                    // Fallback to definition line
                    code.getattr("co_firstlineno")
                        .ok()
                        .and_then(|l| l.extract::<u32>().ok())
                        .unwrap_or(0)
                });

            Some(doeff_vm_core::ir_stream::StreamSourceLocation {
                func_name,
                source_file,
                source_line,
            })
        })
    }
}

// ---------------------------------------------------------------------------
// classify_python_object — top-level classification for run()
// ---------------------------------------------------------------------------

fn is_tail_resume_candidate(obj: &Bound<'_, PyAny>) -> bool {
    use crate::do_expr::{PyResume, PyResumeThrow};

    obj.downcast::<PyResume>().is_ok() || obj.downcast::<PyResumeThrow>().is_ok()
}

fn generator_current_line(py: Python<'_>, generator: &PyShared) -> Option<u32> {
    let gen = generator.bind(py);
    let frame = gen.getattr("gi_frame").ok()?;
    if frame.is_none() {
        return None;
    }
    frame
        .getattr("f_lineno")
        .ok()
        .and_then(|line| line.extract::<u32>().ok())
}

fn classify_tail_resume(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Result<Option<DoCtrl>, String> {
    use crate::do_expr::{PyResume, PyResumeThrow};

    if let Ok(r) = obj.downcast::<PyResume>() {
        let r = r.get();
        let k = take_continuation(py, &r.continuation, "Transfer")?;
        let value = python_to_value(py, &r.value.bind(py));
        return Ok(Some(DoCtrl::Transfer { k, value }));
    }

    if let Ok(rt) = obj.downcast::<PyResumeThrow>() {
        let rt = rt.get();
        let k = take_continuation(py, &rt.continuation, "TransferThrow")?;
        let exception = Value::Opaque(PyShared::new(rt.exception.clone_ref(py)));
        return Ok(Some(DoCtrl::TransferThrow { k, exception }));
    }

    Ok(None)
}

/// Extract continuation from PyK. No one-shot enforcement here — that's the VM core's job
/// (continue_k in dispatch.rs, where self.current_segment is available for diagnostics).
/// If PyK is already consumed, returns Continuation::consumed() sentinel.
fn take_continuation(
    py: Python<'_>,
    k: &Py<doeff_vm_core::continuation::PyK>,
    label: &str,
) -> Result<doeff_vm_core::Continuation, String> {
    let k_ref = k.bind(py);
    let mut k_borrowed = k_ref.borrow_mut();
    match k_borrowed.take() {
        Some(doeff_vm_core::OwnedControlContinuation::Started(k)) => Ok(k),
        Some(doeff_vm_core::OwnedControlContinuation::Pending(_)) => Err(format!(
            "{}: expected started continuation, got pending",
            label
        )),
        None => Ok(doeff_vm_core::Continuation::empty()),
    }
}

fn continuation_traceback_value(
    py: Python<'_>,
    k: &Py<doeff_vm_core::continuation::PyK>,
    label: &str,
) -> Result<Value, String> {
    let k_ref = k.bind(py);
    let k_borrowed = k_ref.borrow();
    let Some(doeff_vm_core::OwnedControlContinuation::Started(k)) = k_borrowed.continuation_ref()
    else {
        return Err(format!(
            "{}: continuation has no detached fiber chain",
            label
        ));
    };
    let frames = k
        .collect_traceback()
        .ok_or_else(|| format!("{}: continuation has no detached fiber chain", label))?
        .into_iter()
        .map(|loc| {
            Value::List(vec![
                Value::String(loc.func_name),
                Value::String(loc.source_file),
                Value::Int(loc.source_line as i64),
            ])
        })
        .collect();
    Ok(Value::List(frames))
}

fn continuation_handlers_value(
    py: Python<'_>,
    k: &Py<doeff_vm_core::continuation::PyK>,
    label: &str,
) -> Result<Value, String> {
    let k_ref = k.bind(py);
    let k_borrowed = k_ref.borrow();
    let Some(doeff_vm_core::OwnedControlContinuation::Started(k)) = k_borrowed.continuation_ref()
    else {
        return Err(format!(
            "{}: continuation has no detached fiber chain",
            label
        ));
    };
    let handlers = k
        .handler_callables()
        .ok_or_else(|| format!("{}: continuation has no detached fiber chain", label))?
        .into_iter()
        .map(Value::Callable)
        .collect();
    Ok(Value::List(handlers))
}

/// Wrap a handler callable as Value::Callable.
fn wrap_handler(py: Python<'_>, handler: &Py<PyAny>) -> Value {
    let callable = PythonCallable::new(handler.clone_ref(py));
    Value::Callable(std::sync::Arc::new(callable) as doeff_vm_core::value::CallableRef)
}

/// Classify a Python object into a DoCtrl.
///
/// Priority order:
/// 1. Rust pyclass DoExpr → downcast (fast pointer-type-check)
/// 2. Legacy Python DoExpr with `tag` → fallback tag-based dispatch
/// 3. EffectBase (no tag) → implicit Perform(effect)
/// 4. Anything else → error
pub fn classify_python_object(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Result<DoCtrl, String> {
    use crate::do_expr::*;

    // --- Rust pyclass DoExpr (fast path) ---

    if let Ok(p) = obj.downcast::<PyPure>() {
        let value = python_to_value(py, &p.get().value.bind(py));
        return Ok(DoCtrl::Pure { value });
    }
    if let Ok(p) = obj.downcast::<PyPerform>() {
        return Ok(DoCtrl::Perform {
            effect: Value::Opaque(PyShared::new(p.get().effect.clone_ref(py))),
        });
    }
    if let Ok(r) = obj.downcast::<PyResume>() {
        let r = r.get();
        let k = take_continuation(py, &r.continuation, "Resume")?;
        let value = python_to_value(py, &r.value.bind(py));
        return Ok(DoCtrl::Resume { k, value });
    }
    if let Ok(t) = obj.downcast::<PyTransfer>() {
        let t = t.get();
        let k = take_continuation(py, &t.continuation, "Transfer")?;
        let value = python_to_value(py, &t.value.bind(py));
        return Ok(DoCtrl::Transfer { k, value });
    }
    if let Ok(a) = obj.downcast::<PyApply>() {
        let a = a.get();
        let f_doctrl = classify_python_object(py, &a.f.bind(py))?;
        let args_obj = a.args.bind(py);
        let mut args = Vec::new();
        if let Ok(seq) = args_obj.downcast::<pyo3::types::PyList>() {
            for (i, item) in seq.iter().enumerate() {
                args.push(
                    classify_python_object(py, &item)
                        .map_err(|e| format!("Apply: arg[{}]: {}", i, e))?,
                );
            }
        }
        return Ok(DoCtrl::Apply {
            f: Box::new(f_doctrl),
            args,
        });
    }
    if let Ok(e) = obj.downcast::<PyExpand>() {
        let expr_doctrl = classify_python_object(py, &e.get().expr.bind(py))?;
        return Ok(DoCtrl::Expand {
            expr: Box::new(expr_doctrl),
        });
    }
    if let Ok(p) = obj.downcast::<PyPass>() {
        let p = p.get();
        let effect = Value::Opaque(PyShared::new(p.effect.clone_ref(py)));
        let k = take_continuation(py, &p.continuation, "Pass")?;
        return Ok(DoCtrl::Pass { effect, k });
    }
    if let Ok(wh) = obj.downcast::<PyWithHandler>() {
        let wh = wh.get();
        let handler_value = wrap_handler(py, &wh.handler);
        let body_doctrl = classify_python_object(py, &wh.body.bind(py))
            .map_err(|e| format!("WithHandler body: {}", e))?;
        return Ok(DoCtrl::WithHandler {
            handler: handler_value,
            body: Box::new(body_doctrl),
        });
    }
    if let Ok(rt) = obj.downcast::<PyResumeThrow>() {
        let rt = rt.get();
        let k = take_continuation(py, &rt.continuation, "ResumeThrow")?;
        let exception = Value::Opaque(PyShared::new(rt.exception.clone_ref(py)));
        return Ok(DoCtrl::ResumeThrow { k, exception });
    }
    if let Ok(tt) = obj.downcast::<PyTransferThrow>() {
        let tt = tt.get();
        let k = take_continuation(py, &tt.continuation, "TransferThrow")?;
        let exception = Value::Opaque(PyShared::new(tt.exception.clone_ref(py)));
        return Ok(DoCtrl::TransferThrow { k, exception });
    }
    if let Ok(wo) = obj.downcast::<PyWithObserve>() {
        let wo = wo.get();
        let observer = python_to_value(py, &wo.observer.bind(py));
        let body_doctrl = classify_python_object(py, &wo.body.bind(py))
            .map_err(|e| format!("WithObserve body: {}", e))?;
        return Ok(DoCtrl::WithObserve {
            observer,
            body: Box::new(body_doctrl),
        });
    }
    if let Ok(gt) = obj.downcast::<PyGetTraceback>() {
        let value = continuation_traceback_value(py, &gt.get().continuation, "GetTraceback")?;
        return Ok(DoCtrl::Pure { value });
    }
    if obj.downcast::<PyGetExecutionContext>().is_ok() {
        return Ok(DoCtrl::GetExecutionContext);
    }
    if let Ok(gh) = obj.downcast::<PyGetHandlers>() {
        let value = continuation_handlers_value(py, &gh.get().continuation, "GetHandlers")?;
        return Ok(DoCtrl::Pure { value });
    }
    if obj.downcast::<crate::do_expr::PyGetOuterHandlers>().is_ok() {
        return Ok(DoCtrl::GetOuterHandlers);
    }
    if let Ok(te) = obj.downcast::<crate::do_expr::PyTailEval>() {
        let inner = te.get().expr.bind(py);
        let inner_doctrl = classify_python_object(py, &inner)?;
        return Ok(DoCtrl::TailEval {
            expr: Box::new(inner_doctrl),
        });
    }

    // --- EffectBase (no tag) → implicit Perform ---
    if obj.is_instance_of::<PyEffectBase>() {
        return Ok(DoCtrl::Perform {
            effect: Value::Opaque(PyShared::new(obj.clone().unbind())),
        });
    }

    // --- Legacy tag-based fallback (for any remaining Python DoExpr classes) ---
    if let Ok(tag_attr) = obj.getattr("tag") {
        if let Ok(tag) = tag_attr.extract::<u8>() {
            return classify_tagged_to_doctrl(py, obj, tag);
        }
    }

    Err(format!(
        "expected DoExpr or EffectBase, got: {:?}",
        obj.get_type()
    ))
}

/// Legacy tag-based classification fallback.
/// Kept for backward compatibility with any code still using plain Python DoExpr classes.
fn classify_tagged_to_doctrl(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    tag: u8,
) -> Result<DoCtrl, String> {
    match tag {
        0 => {
            let value = obj
                .getattr("value")
                .ok()
                .map(|v| python_to_value(py, &v))
                .unwrap_or(Value::Unit);
            Ok(DoCtrl::Pure { value })
        }
        5 | 128 => {
            let effect = if let Ok(e) = obj.getattr("effect") {
                Value::Opaque(PyShared::new(e.unbind()))
            } else {
                Value::Opaque(PyShared::new(obj.clone().unbind()))
            };
            Ok(DoCtrl::Perform { effect })
        }
        6 => extract_continuation_and_value(py, obj)
            .map(|(k, v)| DoCtrl::Resume { k, value: v })
            .map_err(|_| "Resume: failed to extract continuation/value".to_string()),
        7 => extract_continuation_and_value(py, obj)
            .map(|(k, v)| DoCtrl::Transfer { k, value: v })
            .map_err(|_| "Transfer: failed to extract continuation/value".to_string()),
        8 => extract_effect_and_continuation(py, obj)
            .map(|(effect, k)| DoCtrl::Delegate { effect, k })
            .map_err(|_| "Delegate: failed to extract effect/continuation".to_string()),
        16 => {
            let f_obj = obj
                .getattr("f")
                .map_err(|_| "Apply: missing 'f' attribute".to_string())?;
            let f_doctrl = classify_python_object(py, &f_obj)?;
            let args_list = obj
                .getattr("args")
                .map_err(|_| "Apply: missing 'args' attribute".to_string())?;
            let mut args = Vec::new();
            if let Ok(seq) = args_list.downcast::<pyo3::types::PyList>() {
                for (i, item) in seq.iter().enumerate() {
                    args.push(
                        classify_python_object(py, &item)
                            .map_err(|e| format!("Apply: arg[{}]: {}", i, e))?,
                    );
                }
            }
            Ok(DoCtrl::Apply {
                f: Box::new(f_doctrl),
                args,
            })
        }
        17 => {
            let expr_obj = obj
                .getattr("expr")
                .map_err(|_| "Expand: missing 'expr' attribute".to_string())?;
            let expr_doctrl = classify_python_object(py, &expr_obj)?;
            Ok(DoCtrl::Expand {
                expr: Box::new(expr_doctrl),
            })
        }
        19 => extract_effect_and_continuation(py, obj)
            .map(|(effect, k)| DoCtrl::Pass { effect, k })
            .map_err(|_| "Pass: failed to extract effect/continuation".to_string()),
        20 => {
            let handler_obj = obj
                .getattr("handler")
                .map_err(|_| "WithHandler: missing 'handler' attribute".to_string())?;
            let handler_callable = PythonCallable::new(handler_obj.unbind());
            let handler_value = Value::Callable(
                std::sync::Arc::new(handler_callable) as doeff_vm_core::value::CallableRef
            );
            let body_obj = obj
                .getattr("body")
                .map_err(|_| "WithHandler: missing 'body' attribute".to_string())?;
            let body_doctrl = classify_python_object(py, &body_obj)
                .map_err(|e| format!("WithHandler body: {}", e))?;
            Ok(DoCtrl::WithHandler {
                handler: handler_value,
                body: Box::new(body_doctrl),
            })
        }
        21 => extract_continuation_and_exception(py, obj)
            .map(|(k, exc)| DoCtrl::ResumeThrow { k, exception: exc })
            .map_err(|_| "ResumeThrow: failed to extract continuation/exception".to_string()),
        22 => extract_continuation_and_exception(py, obj)
            .map(|(k, exc)| DoCtrl::TransferThrow { k, exception: exc })
            .map_err(|_| "TransferThrow: failed to extract continuation/exception".to_string()),
        23 => {
            let k_obj = obj
                .getattr("continuation")
                .map_err(|_| "GetTraceback: missing 'continuation' attribute".to_string())?;
            let k = k_obj
                .downcast::<doeff_vm_core::continuation::PyK>()
                .map_err(|_| "GetTraceback: continuation must be K".to_string())?;
            let value = {
                let k_borrowed = k.borrow();
                let Some(doeff_vm_core::OwnedControlContinuation::Started(k)) =
                    k_borrowed.continuation_ref()
                else {
                    return Err(
                        "GetTraceback: continuation has no detached fiber chain".to_string()
                    );
                };
                let frames = k
                    .collect_traceback()
                    .ok_or_else(|| {
                        "GetTraceback: continuation has no detached fiber chain".to_string()
                    })?
                    .into_iter()
                    .map(|loc| {
                        Value::List(vec![
                            Value::String(loc.func_name),
                            Value::String(loc.source_file),
                            Value::Int(loc.source_line as i64),
                        ])
                    })
                    .collect();
                Value::List(frames)
            };
            Ok(DoCtrl::Pure { value })
        }
        24 => {
            let observer_obj = obj
                .getattr("observer")
                .map_err(|_| "WithObserve: missing 'observer' attribute".to_string())?;
            let observer_value = python_to_value(py, &observer_obj);
            let body_obj = obj
                .getattr("body")
                .map_err(|_| "WithObserve: missing 'body' attribute".to_string())?;
            let body_doctrl = classify_python_object(py, &body_obj)
                .map_err(|e| format!("WithObserve body: {}", e))?;
            Ok(DoCtrl::WithObserve {
                observer: observer_value,
                body: Box::new(body_doctrl),
            })
        }
        25 => Ok(DoCtrl::GetExecutionContext),
        26 => {
            let k_obj = obj
                .getattr("continuation")
                .map_err(|_| "GetHandlers: missing 'continuation' attribute".to_string())?;
            let k = k_obj
                .downcast::<doeff_vm_core::continuation::PyK>()
                .map_err(|_| "GetHandlers: continuation must be K".to_string())?;
            let value = {
                let k_borrowed = k.borrow();
                let Some(doeff_vm_core::OwnedControlContinuation::Started(k)) =
                    k_borrowed.continuation_ref()
                else {
                    return Err("GetHandlers: continuation has no detached fiber chain".to_string());
                };
                let handlers = k
                    .handler_callables()
                    .ok_or_else(|| {
                        "GetHandlers: continuation has no detached fiber chain".to_string()
                    })?
                    .into_iter()
                    .map(Value::Callable)
                    .collect();
                Value::List(handlers)
            };
            Ok(DoCtrl::Pure { value })
        }
        27 => Ok(DoCtrl::GetOuterHandlers),
        _ => Err(format!("unknown DoExpr tag: {}", tag)),
    }
}

fn extract_continuation_and_value(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<(doeff_vm_core::Continuation, Value), Value> {
    let k_obj = obj
        .getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj
        .downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed
        .take()
        .ok_or_else(|| Value::String("continuation consumed".into()))?;
    let continuation = match k {
        doeff_vm_core::OwnedControlContinuation::Started(k) => k,
        _ => return Err(Value::String("expected started continuation".into())),
    };
    let value = obj
        .getattr("value")
        .ok()
        .map(|v| python_to_value(py, &v))
        .unwrap_or(Value::Unit);
    Ok((continuation, value))
}

fn extract_effect_and_continuation(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<(Value, doeff_vm_core::Continuation), Value> {
    let effect = obj
        .getattr("effect")
        .ok()
        .map(|e| Value::Opaque(PyShared::new(e.unbind())))
        .unwrap_or(Value::Unit);
    let k_obj = obj
        .getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj
        .downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed
        .take()
        .ok_or_else(|| Value::String("continuation consumed".into()))?;
    let continuation = match k {
        doeff_vm_core::OwnedControlContinuation::Started(k) => k,
        _ => return Err(Value::String("expected started continuation".into())),
    };
    Ok((effect, continuation))
}

fn extract_continuation_and_exception(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<(doeff_vm_core::Continuation, Value), Value> {
    let k_obj = obj
        .getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj
        .downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed
        .take()
        .ok_or_else(|| Value::String("continuation consumed".into()))?;
    let continuation = match k {
        doeff_vm_core::OwnedControlContinuation::Started(k) => k,
        _ => return Err(Value::String("expected started continuation".into())),
    };
    let exception = obj
        .getattr("exception")
        .ok()
        .map(|e| Value::Opaque(PyShared::new(e.unbind())))
        .unwrap_or(Value::String("unknown exception".into()));
    Ok((continuation, exception))
}

// ---------------------------------------------------------------------------
// Value ↔ Python conversion
// ---------------------------------------------------------------------------

/// Convert a Python object to a VM Value.
///
/// NO auto-conversion. Everything is Value::Opaque unless it's an explicit
/// VM type (PythonCallable → Value::Callable, PyK → Value::Continuation).
/// The Python side is responsible for explicit conversion.
pub fn python_to_value(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> Value {
    // PythonCallable pyclass → Value::Callable
    if let Ok(pc) = obj.downcast::<PythonCallable>() {
        let inner = pc.borrow().callable.clone_ref(_py);
        let callable = PythonCallable::new(inner);
        return Value::Callable(std::sync::Arc::new(callable) as doeff_vm_core::value::CallableRef);
    }
    // PyK → Value::Continuation
    if let Ok(k) = obj.downcast::<doeff_vm_core::continuation::PyK>() {
        let mut k_borrowed = k.borrow_mut();
        if let Some(owned) = k_borrowed.take() {
            if let doeff_vm_core::OwnedControlContinuation::Started(continuation) = owned {
                return Value::Continuation(continuation);
            }
        }
    }
    // PyIRStream → Value::Stream
    if let Ok(s) = obj.downcast::<PyIRStream>() {
        let (gen, tail_resume_lines) = {
            let stream = s.borrow();
            (
                stream.generator.clone_ref(_py),
                stream.tail_resume_lines.clone(),
            )
        };
        let stream = PythonGeneratorStream::new(PyShared::new(gen), tail_resume_lines);
        let stream_ref = doeff_vm_core::ir_stream::IRStreamRef::new(Box::new(stream));
        return Value::Stream(stream_ref);
    }
    // Everything else: opaque Python object
    Value::Opaque(PyShared::new(obj.clone().unbind()))
}

/// Convert a VM Value to a Python object.
pub fn value_to_python(py: Python<'_>, value: Value) -> Bound<'_, PyAny> {
    match value {
        Value::Unit => py.None().into_bound(py),
        Value::None => py.None().into_bound(py),
        Value::Int(i) => i.into_pyobject(py).unwrap().into_any(),
        Value::Bool(b) => b.into_pyobject(py).unwrap().to_owned().into_any(),
        Value::String(s) => PyString::new(py, &s).into_any(),
        Value::Opaque(obj) => obj.bind(py).clone(),
        Value::Continuation(k) => {
            Bound::new(py, doeff_vm_core::continuation::PyK::from_continuation(k))
                .unwrap()
                .into_any()
        }
        Value::Var(var) => format!("Var({:?})", var)
            .into_pyobject(py)
            .unwrap()
            .into_any(),
        Value::Callable(c) => {
            if let Some(pc) = c.as_any().downcast_ref::<PythonCallable>() {
                pc.callable.bind(py).clone().into_any()
            } else {
                "<callable>".into_pyobject(py).unwrap().into_any()
            }
        }
        Value::Stream(_) => "<stream>".into_pyobject(py).unwrap().into_any(),
        Value::List(items) => {
            let py_items: Vec<_> = items
                .into_iter()
                .map(|v| value_to_python(py, v).unbind())
                .collect();
            pyo3::types::PyList::new(py, &py_items).unwrap().into_any()
        }
    }
}

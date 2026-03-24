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
use doeff_vm_core::driver::ExternalCall;
use doeff_vm_core::ir_stream::{IRStream, StreamStep};
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::value::Value;

/// A Python generator wrapped as an IRStream.
///
/// The generator yields Python objects that are classified into DoCtrl instructions.
/// When the generator returns (StopIteration), the stream is done.
#[derive(Debug)]
pub struct PythonGeneratorStream {
    generator: PyShared,
    exhausted: bool,
}

impl PythonGeneratorStream {
    pub fn new(generator: PyShared) -> Self {
        Self {
            generator,
            exhausted: false,
        }
    }

    /// Call generator.send(value) and classify the result.
    fn send_to_generator(&mut self, py_value: &Bound<'_, PyAny>) -> StreamStep {
        Python::attach(|py| {
            let gen = self.generator.bind(py);
            match gen.call_method1("send", (py_value,)) {
                Ok(yielded) => self.classify_yielded(py, &yielded),
                Err(err) if err.is_instance_of::<PyStopIteration>(py) => {
                    self.exhausted = true;
                    // Extract return value from StopIteration
                    let return_value = err
                        .value(py)
                        .getattr("value")
                        .ok()
                        .map(|v| python_to_value(py, &v))
                        .unwrap_or(Value::Unit);
                    StreamStep::Done(return_value)
                }
                Err(err) => {
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
                    self.exhausted = true;
                    StreamStep::Error(Value::Opaque(PyShared::new(
                        err.value(py).clone().into_any().unbind(),
                    )))
                }
            }
        })
    }

    /// Classify a yielded Python object into a DoCtrl instruction.
    /// The yielded object MUST be a DoExpr (has `tag`). Anything else is an error.
    fn classify_yielded(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> StreamStep {
        match classify_python_object(py, obj) {
            Ok(doctrl) => StreamStep::Instruction(doctrl),
            Err(msg) => StreamStep::Error(Value::String(format!(
                "generator yielded non-DoExpr: {}. Use @do decorator or yield a DoExpr.",
                msg
            ))),
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
            self.throw_to_generator(&py_error)
        })
    }
}

// ---------------------------------------------------------------------------
// classify_python_object — top-level classification for run()
// ---------------------------------------------------------------------------

/// Classify a Python DoExpr object into a DoCtrl.
/// Used by PyVM.run() to convert the top-level program into an instruction.
///
/// The object MUST have a `tag` attribute. Raw generators and untagged objects
/// are errors — the Python @do layer is responsible for wrapping them.
pub fn classify_python_object(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Result<DoCtrl, String> {
    // Must have a tag attribute
    let tag = obj.getattr("tag")
        .map_err(|_| format!("DoExpr expected (must have 'tag' attribute), got: {:?}", obj.get_type()))?
        .extract::<u8>()
        .map_err(|_| "DoExpr tag must be u8".to_string())?;

    classify_tagged_to_doctrl(py, obj, tag)
        .ok_or_else(|| format!("unknown DoExpr tag: {}", tag))
}

/// Classify a tagged Python object into a DoCtrl (without StreamStep wrapper).
fn classify_tagged_to_doctrl(py: Python<'_>, obj: &Bound<'_, PyAny>, tag: u8) -> Option<DoCtrl> {
    match tag {
        0 => {
            // Pure
            let value = obj.getattr("value").ok()
                .map(|v| python_to_value(py, &v))
                .unwrap_or(Value::Unit);
            Some(DoCtrl::Pure { value })
        }
        5 | 128 => {
            // Perform / Effect
            let effect = if let Ok(e) = obj.getattr("effect") {
                Value::Opaque(PyShared::new(e.unbind()))
            } else {
                // The object itself is the effect
                Value::Opaque(PyShared::new(obj.clone().unbind()))
            };
            Some(DoCtrl::Perform { effect })
        }
        6 => {
            // Resume
            extract_continuation_and_value(py, obj)
                .map(|(k, v)| DoCtrl::Resume { k, value: v })
                .ok()
        }
        7 => {
            // Transfer
            extract_continuation_and_value(py, obj)
                .map(|(k, v)| DoCtrl::Transfer { k, value: v })
                .ok()
        }
        19 => {
            // Pass
            extract_effect_and_continuation(py, obj)
                .map(|(effect, k)| DoCtrl::Pass { effect, k })
                .ok()
        }
        8 => {
            // Delegate
            extract_effect_and_continuation(py, obj)
                .map(|(effect, k)| DoCtrl::Delegate { effect, k })
                .ok()
        }
        22 => {
            // Discontinue (= TransferThrow)
            extract_continuation_and_exception(py, obj)
                .map(|(k, exc)| DoCtrl::TransferThrow { k, exception: exc })
                .ok()
        }
        _ => None,
    }
}

fn extract_continuation_and_value(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<(doeff_vm_core::Continuation, Value), Value> {
    let k_obj = obj.getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj.downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed.take()
        .ok_or_else(|| Value::String("continuation consumed".into()))?;
    let continuation = match k {
        doeff_vm_core::OwnedControlContinuation::Started(k) => k,
        _ => return Err(Value::String("expected started continuation".into())),
    };
    let value = obj.getattr("value").ok()
        .map(|v| python_to_value(py, &v))
        .unwrap_or(Value::Unit);
    Ok((continuation, value))
}

fn extract_effect_and_continuation(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<(Value, doeff_vm_core::Continuation), Value> {
    let effect = obj.getattr("effect").ok()
        .map(|e| Value::Opaque(PyShared::new(e.unbind())))
        .unwrap_or(Value::Unit);
    let k_obj = obj.getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj.downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed.take()
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
    let k_obj = obj.getattr("continuation")
        .map_err(|e| Value::Opaque(PyShared::new(e.value(py).clone().into_any().unbind())))?;
    let k_ref = k_obj.downcast::<doeff_vm_core::continuation::PyK>()
        .map_err(|_| Value::String("expected K".into()))?;
    let mut k_borrowed = k_ref.borrow_mut();
    let k = k_borrowed.take()
        .ok_or_else(|| Value::String("continuation consumed".into()))?;
    let continuation = match k {
        doeff_vm_core::OwnedControlContinuation::Started(k) => k,
        _ => return Err(Value::String("expected started continuation".into())),
    };
    let exception = obj.getattr("exception").ok()
        .map(|e| Value::Opaque(PyShared::new(e.unbind())))
        .unwrap_or(Value::String("unknown exception".into()));
    Ok((continuation, exception))
}

// ---------------------------------------------------------------------------
// Value ↔ Python conversion
// ---------------------------------------------------------------------------

/// Convert a Python object to a VM Value.
pub fn python_to_value(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Value {
    if obj.is_none() {
        return Value::None;
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Value::Int(i);
    }
    if let Ok(b) = obj.extract::<bool>() {
        return Value::Bool(b);
    }
    if let Ok(s) = obj.extract::<String>() {
        return Value::String(s);
    }
    // Default: opaque Python object
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
        Value::Var(var) => format!("Var({:?})", var).into_pyobject(py).unwrap().into_any(),
        Value::Callable(_) => "<callable>".into_pyobject(py).unwrap().into_any(),
        Value::Stream(_) => "<stream>".into_pyobject(py).unwrap().into_any(),
        Value::List(items) => {
            let py_items: Vec<_> = items.into_iter()
                .map(|v| value_to_python(py, v).unbind())
                .collect();
            pyo3::types::PyList::new(py, &py_items).unwrap().into_any()
        }
    }
}

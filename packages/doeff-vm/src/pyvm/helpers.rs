use pyo3::exceptions::{PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyType};

use crate::do_ctrl::InterceptMode;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::py_shared::PyShared;
use crate::step::PyException;
use crate::value::Value;
use crate::vm::VM;
use doeff_vm_core::{PyDoCtrlBase, PyDoExprBase, PyEffectBase};

use super::control_primitives::PyPerform;
use super::run_result::PyDoeffTracebackData;
use super::{NoMatchingHandlerError, UnhandledEffectError};
use doeff_vm_core::DoExprTag;

pub(crate) fn build_traceback_data_pyobject(
    py: Python<'_>,
    trace: Vec<crate::capture::TraceEntry>,
    active_chain: Vec<crate::capture::ActiveChainEntry>,
) -> Option<Py<PyDoeffTracebackData>> {
    let entries = match Value::Trace(trace).to_pyobject(py) {
        Ok(value) => value.unbind(),
        Err(err) => {
            eprintln!("[VM WARNING] traceback serialization failed for entries: {err}");
            return None;
        }
    };
    let active_chain = match Value::ActiveChain(active_chain).to_pyobject(py) {
        Ok(value) => value.unbind(),
        Err(err) => {
            eprintln!("[VM WARNING] traceback serialization failed for active_chain: {err}");
            return None;
        }
    };
    let data = match Bound::new(
        py,
        PyDoeffTracebackData {
            entries,
            active_chain,
        },
    ) {
        Ok(value) => value,
        Err(err) => {
            eprintln!(
                "[VM WARNING] traceback serialization failed for traceback_data object: {err}"
            );
            return None;
        }
    };
    Some(data.unbind())
}

pub(crate) fn vmerror_to_pyerr_with_traceback_data(
    py: Python<'_>,
    e: VMError,
) -> (PyErr, Option<Py<PyDoeffTracebackData>>) {
    match e {
        VMError::OneShotViolation { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::UnhandledEffect { .. } => (UnhandledEffectError::new_err(e.to_string()), None),
        VMError::NoMatchingHandler { .. } => (NoMatchingHandlerError::new_err(e.to_string()), None),
        VMError::DelegateNoOuterHandler { .. } => {
            (NoMatchingHandlerError::new_err(e.to_string()), None)
        }
        VMError::HandlerNotFound { .. } => (NoMatchingHandlerError::new_err(e.to_string()), None),
        VMError::InvalidSegment { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::PythonError { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::InternalError { .. } => (PyRuntimeError::new_err(e.to_string()), None),
        VMError::TypeError { .. } => (PyTypeError::new_err(e.to_string()), None),
        VMError::UncaughtException {
            exception,
            trace,
            active_chain,
        } => {
            let exc_value = exception.value_clone_ref(py);
            let traceback_data = build_traceback_data_pyobject(py, trace, active_chain);
            (
                PyErr::from_value(exc_value.bind(py).clone()),
                traceback_data,
            )
        }
    }
}

pub(crate) fn vmerror_to_pyerr(e: VMError) -> PyErr {
    // SAFETY: vmerror_to_pyerr is always called from GIL-holding contexts (run/step_once)
    let py = unsafe { Python::assume_attached() };
    vmerror_to_pyerr_with_traceback_data(py, e).0
}

pub(crate) const HANDLER_HELP_URL: &str = "https://docs.doeff.dev/handlers";

pub(crate) fn py_type_name(obj: &Bound<'_, PyAny>) -> String {
    obj.get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "<unknown>".to_string())
}

pub(crate) fn py_repr_text(obj: &Bound<'_, PyAny>) -> String {
    obj.repr()
        .map(|value| value.to_string())
        .unwrap_or_else(|_| "<unrepresentable>".to_string())
}

pub(crate) fn strict_handler_type_error(
    api_name: &str,
    role: &str,
    obj: &Bound<'_, PyAny>,
) -> PyErr {
    let got_repr = py_repr_text(obj);
    let ty = py_type_name(obj);
    let fix_block = if role == "handler" {
        "  To fix, decorate your handler with @do:\n\n\
    from doeff import do\n\
    from doeff.effects.base import Effect\n\n\
    @do\n\
    def my_handler(effect: Effect, k):\n\
        ...\n\
        yield Resume(k, value)\n"
    } else {
        "  To fix, decorate your interceptor with @do:\n\n\
    from doeff import do\n\
    from doeff.effects.base import Effect\n\n\
    @do\n\
    def my_interceptor(effect: Effect):\n\
        return effect\n"
    };
    PyTypeError::new_err(format!(
        "{api_name} {role} must be a @do decorated function, PyKleisli, or RustHandler.\n\n\
  Got: {got_repr} (type: {ty})\n\n\
{fix_block}\n\
  See: {HANDLER_HELP_URL}"
    ))
}

pub(crate) fn strict_kleisli_ref_type_error(context: &str, obj: &Bound<'_, PyAny>) -> PyErr {
    if context.starts_with("WithHandler") {
        return strict_handler_type_error("WithHandler", "handler", obj);
    }
    if context.starts_with("WithIntercept") {
        return strict_handler_type_error("WithIntercept", "interceptor", obj);
    }
    let ty = py_type_name(obj);
    let repr = py_repr_text(obj);
    PyTypeError::new_err(format!(
        "{context} must be DoeffGeneratorFn, PyKleisli, or RustHandler, got {repr} (type: {ty})"
    ))
}

pub(crate) fn lookup_continuation_for_control(
    vm: &VM,
    cont_id: crate::ids::ContId,
    control_name: &str,
) -> PyResult<doeff_vm_core::Continuation> {
    if let Some(k) = vm.lookup_continuation(cont_id).cloned() {
        return Ok(k);
    }
    if vm.is_one_shot_consumed(cont_id) {
        return Err(PyRuntimeError::new_err(format!(
            "one-shot violation: continuation {} already consumed",
            cont_id.raw()
        )));
    }
    Err(PyRuntimeError::new_err(format!(
        "{control_name} with unknown continuation id {}",
        cont_id.raw()
    )))
}

pub(crate) fn is_effect_base_like(_py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(obj.is_instance_of::<PyEffectBase>())
}

pub(crate) fn lift_effect_to_perform_expr(py: Python<'_>, expr: Py<PyAny>) -> PyResult<Py<PyAny>> {
    if !is_effect_base_like(py, expr.bind(py))? {
        return Ok(expr);
    }
    let perform = Bound::new(
        py,
        PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase {
                tag: DoExprTag::Perform as u8,
            })
            .add_subclass(PyPerform {
                effect: expr.clone_ref(py),
            }),
    )?;
    Ok(perform.into_any().unbind())
}

pub(crate) fn intercept_mode_from_str(mode: &str) -> PyResult<InterceptMode> {
    InterceptMode::from_str(mode).ok_or_else(|| {
        PyTypeError::new_err(format!(
            "WithIntercept.mode must be 'include' or 'exclude', got '{mode}'"
        ))
    })
}

pub(crate) fn normalize_intercept_types_obj(
    py: Python<'_>,
    types: Option<Py<PyAny>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let types_bound = types_obj.bind(py);
    let iter = types_bound.try_iter().map_err(|_| {
        PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithIntercept.types must contain only Python type objects",
            ));
        }
        normalized.push(item.unbind());
    }

    let tuple = PyTuple::new(py, normalized)?;
    Ok(Some(tuple.into_any().unbind()))
}

pub(crate) fn normalize_handler_types_obj(
    py: Python<'_>,
    types: Option<Py<PyAny>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let types_bound = types_obj.bind(py);
    let iter = types_bound.try_iter().map_err(|_| {
        PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithHandler.types must contain only Python type objects",
            ));
        }
        normalized.push(item.unbind());
    }

    let tuple = PyTuple::new(py, normalized)?;
    Ok(Some(tuple.into_any().unbind()))
}

pub(crate) fn intercept_types_from_pyobj(
    py: Python<'_>,
    types: &Option<Py<PyAny>>,
) -> PyResult<Option<Vec<PyShared>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let iter = types_obj.bind(py).try_iter().map_err(|_| {
        PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithIntercept.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithIntercept.types must contain only Python type objects",
            ));
        }
        normalized.push(PyShared::new(item.unbind()));
    }
    Ok(Some(normalized))
}

pub(crate) fn handler_types_from_pyobj(
    py: Python<'_>,
    types: &Option<Py<PyAny>>,
) -> PyResult<Option<Vec<PyShared>>> {
    let Some(types_obj) = types else {
        return Ok(None);
    };
    let iter = types_obj.bind(py).try_iter().map_err(|_| {
        PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
    })?;

    let mut normalized = Vec::new();
    for item in iter {
        let item = item.map_err(|_| {
            PyTypeError::new_err("WithHandler.types must be an iterable of type objects")
        })?;
        if !item.is_instance_of::<PyType>() {
            return Err(PyTypeError::new_err(
                "WithHandler.types must contain only Python type objects",
            ));
        }
        normalized.push(PyShared::new(item.unbind()));
    }
    Ok(Some(normalized))
}

pub(crate) fn intercept_types_to_pyobj(
    py: Python<'_>,
    types: &Option<Vec<PyShared>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types) = types else {
        return Ok(None);
    };
    let tuple = PyTuple::new(py, types.iter().map(|item| item.clone_ref(py)))?;
    Ok(Some(tuple.into_any().unbind()))
}

pub(crate) fn handler_types_to_pyobj(
    py: Python<'_>,
    types: &Option<Vec<PyShared>>,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(types) = types else {
        return Ok(None);
    };
    let tuple = PyTuple::new(py, types.iter().map(|item| item.clone_ref(py)))?;
    Ok(Some(tuple.into_any().unbind()))
}

pub(crate) fn pyerr_to_exception(py: Python<'_>, e: PyErr) -> PyResult<PyException> {
    let exc_type = e.get_type(py).into_any().unbind();
    let exc_value = e.value(py).clone().into_any().unbind();
    let exc_tb = e.traceback(py).map(|tb| tb.into_any().unbind());
    Ok(PyException::new(exc_type, exc_value, exc_tb))
}

pub(crate) fn default_discontinued_exception(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let err = super::Discontinued::new_err(());
    Ok(err.value(py).clone().into_any().unbind())
}

pub(crate) fn extract_stop_iteration_value(py: Python<'_>, e: &PyErr) -> PyResult<Value> {
    let value = e.value(py).getattr("value")?;
    Ok(Value::from_pyobject(&value))
}

pub(crate) fn metadata_attr_as_string(meta: &Bound<'_, PyAny>, key: &str) -> Option<String> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| v.extract::<String>().ok())
}

pub(crate) fn metadata_attr_as_u32(meta: &Bound<'_, PyAny>, key: &str) -> Option<u32> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| v.extract::<u32>().ok())
}

pub(crate) fn metadata_attr_as_py(meta: &Bound<'_, PyAny>, key: &str) -> Option<PyShared> {
    meta.cast::<PyDict>()
        .ok()
        .and_then(|dict| dict.get_item(key).ok().flatten())
        .and_then(|v| {
            if v.is_none() {
                None
            } else {
                Some(PyShared::new(v.unbind()))
            }
        })
}

pub(crate) fn call_metadata_from_meta_obj(meta_obj: &Bound<'_, PyAny>) -> CallMetadata {
    let function_name = metadata_attr_as_string(meta_obj, "function_name")
        .unwrap_or_else(|| "<anonymous>".to_string());
    let source_file =
        metadata_attr_as_string(meta_obj, "source_file").unwrap_or_else(|| "<unknown>".to_string());
    let source_line = metadata_attr_as_u32(meta_obj, "source_line").unwrap_or(0);
    let args_repr = metadata_attr_as_string(meta_obj, "args_repr");
    let program_call = metadata_attr_as_py(meta_obj, "program_call");
    CallMetadata::new(
        function_name,
        source_file,
        source_line,
        args_repr,
        program_call,
    )
}

pub(crate) fn call_metadata_from_required_meta(
    py: Python<'_>,
    meta: &Option<Py<PyAny>>,
    ctrl_name: &str,
) -> PyResult<CallMetadata> {
    if let Some(meta) = meta {
        let meta_obj = meta.bind(py);
        if !meta_obj.is_instance_of::<PyDict>() {
            return Err(PyTypeError::new_err(format!(
                "{ctrl_name}.meta must be dict with function_name/source_file/source_line"
            )));
        }
        return Ok(call_metadata_from_meta_obj(meta_obj));
    }

    Err(PyTypeError::new_err(format!(
        "{ctrl_name}.meta is required. \
Supply {ctrl_name}(..., meta={{function_name, source_file, source_line}})."
    )))
}

pub(crate) fn call_metadata_from_optional_meta(
    py: Python<'_>,
    meta: &Option<Py<PyAny>>,
    ctrl_name: &str,
) -> PyResult<Option<CallMetadata>> {
    let Some(meta_obj) = meta else {
        return Ok(None);
    };
    let bound = meta_obj.bind(py);
    if !bound.is_instance_of::<PyDict>() {
        return Err(PyTypeError::new_err(format!(
            "{ctrl_name}.meta must be dict with function_name/source_file/source_line"
        )));
    }
    Ok(Some(call_metadata_from_meta_obj(bound)))
}

pub(crate) fn call_metadata_to_dict(
    py: Python<'_>,
    metadata: &CallMetadata,
) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("function_name", metadata.function_name.as_str())?;
    dict.set_item("source_file", metadata.source_file.as_str())?;
    dict.set_item("source_line", metadata.source_line)?;
    if let Some(args_repr) = &metadata.args_repr {
        dict.set_item("args_repr", args_repr.as_str())?;
    }
    if let Some(program_call) = &metadata.program_call {
        dict.set_item("program_call", program_call.bind(py))?;
    }
    Ok(dict.into_any().unbind())
}

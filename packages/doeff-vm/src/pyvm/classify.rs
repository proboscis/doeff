use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyAttributeError, PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::do_ctrl::DoCtrl;
use crate::doeff_generator::{DoeffGenerator, DoeffGeneratorFn};
use crate::effect::{dispatch_from_shared, dispatch_ref_as_python, PyProgramCallStack};
use crate::frame::CallMetadata;
use crate::ir_stream::{IRStream, PythonGeneratorStream};
use crate::py_shared::PyShared;
use crate::step::PyException;
use crate::value::Value;
use crate::vm::VM;
use doeff_vm_core::{DoExprTag, PyDoCtrlBase, PyDoExprBase, PyK};

use super::control_primitives::{
    PyApply, PyAsyncEscape, PyCreateContinuation, PyDelegate, PyDiscontinue, PyEval,
    PyEvalInScope, PyExpand, PyFlatMap, PyGetCallStack, PyGetContinuation, PyGetHandlers,
    PyGetTraceback, PyMap, PyPass, PyPerform, PyPure, PyResume, PyResumeContinuation, PyTransfer,
    PyWithHandler, PyWithIntercept,
};
use super::helpers::{
    call_metadata_from_meta_obj, call_metadata_from_optional_meta, call_metadata_from_required_meta,
    call_metadata_to_dict, handler_types_from_pyobj, handler_types_to_pyobj,
    intercept_mode_from_str, intercept_types_from_pyobj, intercept_types_to_pyobj,
    is_effect_base_like, lookup_continuation_for_control, pyerr_to_exception,
};
use super::PyVM;

fn classify_call_expr(vm: &VM, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<DoCtrl> {
    if obj.is_instance_of::<PyDoExprBase>() || is_effect_base_like(py, obj)? {
        classify_yielded_bound(vm, py, obj)
    } else {
        Ok(DoCtrl::Pure {
            value: Value::from_pyobject(obj),
        })
    }
}

fn call_expr_to_pyobject(py: Python<'_>, expr: &DoCtrl) -> PyResult<Py<PyAny>> {
    match doctrl_to_pyexpr_for_vm(expr) {
        Ok(Some(obj)) => Ok(obj),
        Ok(None) => Err(PyTypeError::new_err(
            "Apply/Expand argument DoExpr cannot be represented as Python object",
        )),
        Err(exc) => Err(exc.to_pyerr(py)),
    }
}

pub(crate) fn doctrl_to_pyexpr_for_vm(yielded: &DoCtrl) -> Result<Option<Py<PyAny>>, PyException> {
    Python::attach(|py| {
        let obj = match yielded {
            DoCtrl::Pure { value } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Pure as u8,
                        })
                        .add_subclass(PyPure {
                            value: value.to_pyobject(py)?.unbind(),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Map {
                source,
                mapper,
                mapper_meta,
            } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Map as u8,
                        })
                        .add_subclass(PyMap {
                            source: source.clone_ref(py),
                            mapper: mapper.clone_ref(py),
                            mapper_meta: call_metadata_to_dict(py, mapper_meta)?,
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::FlatMap {
                source,
                binder,
                binder_meta,
            } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::FlatMap as u8,
                        })
                        .add_subclass(PyFlatMap {
                            source: source.clone_ref(py),
                            binder: binder.clone_ref(py),
                            binder_meta: call_metadata_to_dict(py, binder_meta)?,
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Perform { effect } => {
                dispatch_ref_as_python(effect).map(|value| value.clone_ref(py))
            }
            DoCtrl::Resume {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Resume as u8,
                            })
                            .add_subclass(PyResume {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Transfer {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Transfer as u8,
                            })
                            .add_subclass(PyTransfer {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Discontinue {
                continuation,
                exception,
            } => {
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Discontinue as u8,
                            })
                            .add_subclass(PyDiscontinue {
                                continuation: k,
                                exception: exception.value_clone_ref(py),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::TransferThrow { .. } | DoCtrl::ResumeThrow { .. } => None,
            DoCtrl::WithHandler {
                handler,
                body,
                types,
            } => {
                let debug = handler.debug_info();
                let handler_obj = handler
                    .py_identity()
                    .map(|identity| identity.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let body_obj = doctrl_to_pyexpr_for_vm(body)?.ok_or_else(|| {
                    PyException::type_error("WithHandler.body must convert to DoExpr")
                })?;
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::WithHandler as u8,
                            })
                            .add_subclass(PyWithHandler {
                                handler: handler_obj,
                                expr: body_obj,
                                types: handler_types_to_pyobj(py, types)?,
                                handler_name: Some(debug.name),
                                handler_file: debug.file,
                                handler_line: debug.line,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::WithIntercept {
                interceptor,
                body,
                types,
                mode,
                metadata,
            } => {
                let interceptor_obj = interceptor
                    .py_identity()
                    .map(|identity| identity.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let body_obj = doctrl_to_pyexpr_for_vm(body)?.ok_or_else(|| {
                    PyException::type_error("WithIntercept.body must convert to DoExpr")
                })?;
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::WithIntercept as u8,
                            })
                            .add_subclass(PyWithIntercept {
                                f: interceptor_obj,
                                expr: body_obj,
                                types: intercept_types_to_pyobj(py, types)?,
                                mode: mode.as_str().to_string(),
                                meta: metadata
                                    .as_ref()
                                    .map(|meta| call_metadata_to_dict(py, meta))
                                    .transpose()?,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Delegate { .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Delegate as u8,
                        })
                        .add_subclass(PyDelegate {}),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Pass { .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Pass as u8,
                        })
                        .add_subclass(PyPass {}),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetContinuation => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetContinuation as u8,
                        })
                        .add_subclass(PyGetContinuation),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetHandlers => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetHandlers as u8,
                        })
                        .add_subclass(PyGetHandlers),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::GetTraceback { continuation } => {
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::GetTraceback as u8,
                            })
                            .add_subclass(PyGetTraceback { continuation: k }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::CreateContinuation {
                expr,
                handlers,
                handler_identities,
            } => {
                let list = PyList::empty(py);
                for (idx, handler) in handlers.iter().enumerate() {
                    if let Some(identity_opt) = handler_identities.get(idx) {
                        if let Some(identity) = identity_opt {
                            list.append(identity.bind(py))
                                .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                            continue;
                        }
                    }
                    if let Some(identity) = handler.py_identity() {
                        list.append(identity.bind(py))
                            .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                    } else {
                        list.append(py.None())
                            .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                    }
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::CreateContinuation as u8,
                            })
                            .add_subclass(PyCreateContinuation {
                                program: expr.clone_ref(py),
                                handlers: list.into_any().unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => {
                let k = Bound::new(py, PyK::from_cont_id(continuation.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::ResumeContinuation as u8,
                            })
                            .add_subclass(PyResumeContinuation {
                                continuation: k,
                                value: value.to_pyobject(py)?.unbind(),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::PythonAsyncSyntaxEscape { action } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::AsyncEscape as u8,
                        })
                        .add_subclass(PyAsyncEscape {
                            action: action.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::Apply {
                f,
                args,
                kwargs,
                metadata,
            } => {
                let py_args = PyList::empty(py);
                for arg in args {
                    py_args
                        .append(call_expr_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_expr_to_pyobject(py, value)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Apply as u8,
                            })
                            .add_subclass(PyApply {
                                f: call_expr_to_pyobject(py, f)?,
                                args: py_args.into_any().unbind(),
                                kwargs: py_kwargs.into_any().unbind(),
                                meta: Some(call_metadata_to_dict(py, metadata)?),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => {
                let py_args = PyList::empty(py);
                for arg in args {
                    py_args
                        .append(call_expr_to_pyobject(py, arg)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                let py_kwargs = PyDict::new(py);
                for (key, value) in kwargs {
                    py_kwargs
                        .set_item(key, call_expr_to_pyobject(py, value)?.bind(py))
                        .map_err(|err| PyException::runtime_error(format!("{err}")))?;
                }
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::Expand as u8,
                            })
                            .add_subclass(PyExpand {
                                factory: call_expr_to_pyobject(py, factory)?,
                                args: py_args.into_any().unbind(),
                                kwargs: py_kwargs.into_any().unbind(),
                                meta: Some(call_metadata_to_dict(py, metadata)?),
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::IRStream { .. } => None,
            DoCtrl::Eval { expr, .. } => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::Eval as u8,
                        })
                        .add_subclass(PyEval {
                            expr: expr.clone_ref(py),
                        }),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
            DoCtrl::EvalInScope { expr, scope, .. } => {
                let k = Bound::new(py, PyK::from_cont_id(scope.cont_id))
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind();
                Some(
                    Bound::new(
                        py,
                        PyClassInitializer::from(PyDoExprBase)
                            .add_subclass(PyDoCtrlBase {
                                tag: DoExprTag::EvalInScope as u8,
                            })
                            .add_subclass(PyEvalInScope {
                                expr: expr.clone_ref(py),
                                scope: k,
                            }),
                    )
                    .map_err(|err| PyException::runtime_error(format!("{err}")))?
                    .into_any()
                    .unbind(),
                )
            }
            DoCtrl::GetCallStack => Some(
                Bound::new(
                    py,
                    PyClassInitializer::from(PyDoExprBase)
                        .add_subclass(PyDoCtrlBase {
                            tag: DoExprTag::GetCallStack as u8,
                        })
                        .add_subclass(PyGetCallStack),
                )
                .map_err(|err| PyException::runtime_error(format!("{err}")))?
                .into_any()
                .unbind(),
            ),
        };

        Ok(obj)
    })
}

fn merged_metadata_from_doeff(
    inherited: Option<CallMetadata>,
    function_name: String,
    source_file: String,
    source_line: u32,
) -> Option<CallMetadata> {
    match inherited {
        Some(metadata) => Some(metadata),
        None => Some(CallMetadata::new(
            function_name,
            source_file,
            source_line,
            None,
            None,
        )),
    }
}

fn classify_doeff_generator_as_irstream(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    inherited_metadata: Option<CallMetadata>,
    context: &str,
) -> PyResult<DoCtrl> {
    let wrapped: PyRef<'_, DoeffGenerator> = obj.extract().map_err(|_| {
        let ty = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "<unknown>".to_string());
        PyTypeError::new_err(format!("{context}: expected DoeffGenerator, got {ty}"))
    })?;

    if !wrapped.get_frame.bind(py).is_callable() {
        return Err(PyTypeError::new_err(format!(
            "{context}: DoeffGenerator.get_frame must be callable"
        )));
    }

    let stream: Arc<Mutex<Box<dyn IRStream>>> =
        Arc::new(Mutex::new(Box::new(PythonGeneratorStream::new(
            PyShared::new(wrapped.generator.clone_ref(py)),
            PyShared::new(wrapped.get_frame.clone_ref(py)),
        )) as Box<dyn IRStream>));

    Ok(DoCtrl::IRStream {
        stream,
        metadata: merged_metadata_from_doeff(
            inherited_metadata,
            wrapped.factory_function_name().to_string(),
            wrapped.factory_source_file().to_string(),
            wrapped.factory_source_line(),
        ),
    })
}

pub(crate) fn classify_yielded_bound(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> PyResult<DoCtrl> {
    // R13-I: GIL-free tag dispatch.
    //
    // 1. Single isinstance check: extract PyDoCtrlBase
    // 2. Read tag (u8 on frozen struct — no GIL contention)
    // 3. Match on DoExprTag → single targeted extract for the variant
    // 4. EffectBase instances are wrapped as DoCtrl::Perform
    //
    // Reduces average isinstance checks from ~8 to 2, worst case from 16 to 2.
    if let Ok(base) = obj.extract::<PyRef<'_, PyDoCtrlBase>>() {
        let tag = DoExprTag::try_from(base.tag).unwrap_or(DoExprTag::Unknown);
        return match tag {
            DoExprTag::WithHandler => {
                let wh: PyRef<'_, PyWithHandler> = obj.extract()?;
                let handler_bound = wh.handler.bind(py);
                let handler = PyVM::extract_kleisli_ref(py, handler_bound, "WithHandler.handler")?;
                let body = classify_yielded_bound(vm, py, wh.expr.bind(py))?;
                let types = handler_types_from_pyobj(py, &wh.types)?;
                Ok(DoCtrl::WithHandler {
                    handler,
                    body: Box::new(body),
                    types,
                })
            }
            DoExprTag::WithIntercept => {
                let wi: PyRef<'_, PyWithIntercept> = obj.extract()?;
                let interceptor = PyVM::extract_kleisli_ref(py, wi.f.bind(py), "WithIntercept.f")?;
                let body = classify_yielded_bound(vm, py, wi.expr.bind(py))?;
                let types = intercept_types_from_pyobj(py, &wi.types)?;
                let mode = intercept_mode_from_str(&wi.mode)?;
                Ok(DoCtrl::WithIntercept {
                    interceptor,
                    body: Box::new(body),
                    types,
                    mode,
                    metadata: call_metadata_from_optional_meta(py, &wi.meta, "WithIntercept")?,
                })
            }
            DoExprTag::Discontinue => {
                let d: PyRef<'_, PyDiscontinue> = obj.extract()?;
                let k_pyobj = d.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Discontinue.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "Discontinue with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
                let bound_exception = d.exception.bind(py);
                if !bound_exception.is_instance_of::<pyo3::exceptions::PyBaseException>() {
                    return Err(PyTypeError::new_err(
                        "Discontinue.exception must be a BaseException instance",
                    ));
                }
                Ok(DoCtrl::Discontinue {
                    continuation: k,
                    exception: pyerr_to_exception(py, PyErr::from_value(bound_exception.clone()))?,
                })
            }
            DoExprTag::Pure => {
                let p: PyRef<'_, PyPure> = obj.extract()?;
                Ok(DoCtrl::Pure {
                    value: Value::from_pyobject(p.value.bind(py)),
                })
            }
            DoExprTag::Apply => {
                let a: PyRef<'_, PyApply> = obj.extract()?;
                let f = classify_call_expr(vm, py, a.f.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in a.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_expr(vm, py, item.as_any())?);
                }
                let kwargs_dict = a.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_expr(vm, py, v.as_any())?));
                }
                Ok(DoCtrl::Apply {
                    f: Box::new(f),
                    args,
                    kwargs,
                    metadata: call_metadata_from_pyapply(py, &a)?,
                })
            }
            DoExprTag::Expand => {
                let e: PyRef<'_, PyExpand> = obj.extract()?;
                let factory = classify_call_expr(vm, py, e.factory.bind(py).as_any())?;
                let mut args = Vec::new();
                for item in e.args.bind(py).try_iter()? {
                    let item = item?;
                    args.push(classify_call_expr(vm, py, item.as_any())?);
                }
                let kwargs_dict = e.kwargs.bind(py).cast::<PyDict>()?;
                let mut kwargs = Vec::new();
                for (k, v) in kwargs_dict.iter() {
                    let key = k.str()?.to_str()?.to_string();
                    kwargs.push((key, classify_call_expr(vm, py, v.as_any())?));
                }
                Ok(DoCtrl::Expand {
                    factory: Box::new(factory),
                    args,
                    kwargs,
                    metadata: call_metadata_from_pyexpand(py, &e)?,
                })
            }
            DoExprTag::Map => {
                let m: PyRef<'_, PyMap> = obj.extract()?;
                Ok(DoCtrl::Map {
                    source: PyShared::new(m.source.clone_ref(py)),
                    mapper: PyShared::new(m.mapper.clone_ref(py)),
                    mapper_meta: call_metadata_from_meta_obj(m.mapper_meta.bind(py)),
                })
            }
            DoExprTag::FlatMap => {
                let fm: PyRef<'_, PyFlatMap> = obj.extract()?;
                Ok(DoCtrl::FlatMap {
                    source: PyShared::new(fm.source.clone_ref(py)),
                    binder: PyShared::new(fm.binder.clone_ref(py)),
                    binder_meta: call_metadata_from_meta_obj(fm.binder_meta.bind(py)),
                })
            }
            DoExprTag::Perform => {
                let pf: PyRef<'_, PyPerform> = obj.extract()?;
                Ok(DoCtrl::Perform {
                    effect: dispatch_from_shared(PyShared::new(pf.effect.clone_ref(py))),
                })
            }
            DoExprTag::Resume => {
                let r: PyRef<'_, PyResume> = obj.extract()?;
                let k_pyobj = r.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Resume.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = lookup_continuation_for_control(vm, cont_id, "Resume")?;
                Ok(DoCtrl::Resume {
                    continuation: k,
                    value: Value::from_pyobject(r.value.bind(py)),
                })
            }
            DoExprTag::Transfer => {
                let t: PyRef<'_, PyTransfer> = obj.extract()?;
                let k_pyobj = t.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "Transfer.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = lookup_continuation_for_control(vm, cont_id, "Transfer")?;
                Ok(DoCtrl::Transfer {
                    continuation: k,
                    value: Value::from_pyobject(t.value.bind(py)),
                })
            }
            DoExprTag::Delegate => {
                let _d: PyRef<'_, PyDelegate> = obj.extract()?;
                let dispatch_id = vm.current_dispatch_id().ok_or_else(|| {
                    PyRuntimeError::new_err("Delegate called outside dispatch context")
                })?;
                let effect = vm.effect_for_dispatch(dispatch_id).ok_or_else(|| {
                    PyRuntimeError::new_err("Delegate dispatch context not found")
                })?;
                Ok(DoCtrl::Delegate { effect })
            }
            DoExprTag::Pass => {
                let _p: PyRef<'_, PyPass> = obj.extract()?;
                let dispatch_id = vm.current_dispatch_id().ok_or_else(|| {
                    PyRuntimeError::new_err("Pass called outside dispatch context")
                })?;
                let effect = vm
                    .effect_for_dispatch(dispatch_id)
                    .ok_or_else(|| PyRuntimeError::new_err("Pass dispatch context not found"))?;
                Ok(DoCtrl::Pass { effect })
            }
            DoExprTag::ResumeContinuation => {
                let rc: PyRef<'_, PyResumeContinuation> = obj.extract()?;
                let k_pyobj = rc.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "ResumeContinuation.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = lookup_continuation_for_control(vm, cont_id, "ResumeContinuation")?;
                Ok(DoCtrl::ResumeContinuation {
                    continuation: k,
                    value: Value::from_pyobject(rc.value.bind(py)),
                })
            }
            DoExprTag::CreateContinuation => {
                let cc: PyRef<'_, PyCreateContinuation> = obj.extract()?;
                let program = cc.program.clone_ref(py);
                let handlers_list = cc.handlers.bind(py);
                let mut handlers = Vec::new();
                let mut handler_identities = Vec::new();
                for item in handlers_list.try_iter()? {
                    let item = item?;
                    let kleisli = PyVM::extract_kleisli_ref(py, &item, "CreateContinuation")?;
                    let identity = kleisli
                        .py_identity()
                        .or_else(|| Some(PyShared::new(item.clone().unbind())));
                    handlers.push(kleisli);
                    handler_identities.push(identity);
                }
                Ok(DoCtrl::CreateContinuation {
                    expr: PyShared::new(program),
                    handlers,
                    handler_identities,
                })
            }
            DoExprTag::GetContinuation => Ok(DoCtrl::GetContinuation),
            DoExprTag::GetHandlers => Ok(DoCtrl::GetHandlers),
            DoExprTag::GetTraceback => {
                let gt: PyRef<'_, PyGetTraceback> = obj.extract()?;
                let k_pyobj = gt.continuation.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err(
                        "GetTraceback.continuation must be K (opaque continuation handle)",
                    )
                })?;
                let cont_id = k_pyobj.borrow().cont_id;
                let k = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "GetTraceback with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
                Ok(DoCtrl::GetTraceback { continuation: k })
            }
            DoExprTag::GetCallStack => Ok(DoCtrl::GetCallStack),
            DoExprTag::Eval => {
                let eval: PyRef<'_, PyEval> = obj.extract()?;
                let expr = eval.expr.clone_ref(py);
                Ok(DoCtrl::Eval {
                    expr: PyShared::new(expr),
                    metadata: None,
                })
            }
            DoExprTag::EvalInScope => {
                let eval: PyRef<'_, PyEvalInScope> = obj.extract()?;
                let expr = eval.expr.clone_ref(py);
                let scope_obj = eval.scope.bind(py).cast::<PyK>().map_err(|_| {
                    PyTypeError::new_err("EvalInScope.scope must be K (opaque continuation handle)")
                })?;
                let cont_id = scope_obj.borrow().cont_id;
                let scope = vm.lookup_continuation(cont_id).cloned().ok_or_else(|| {
                    PyRuntimeError::new_err(format!(
                        "EvalInScope with unknown continuation id {}",
                        cont_id.raw()
                    ))
                })?;
                Ok(DoCtrl::EvalInScope {
                    expr: PyShared::new(expr),
                    scope,
                    metadata: None,
                })
            }
            DoExprTag::AsyncEscape => {
                let ae: PyRef<'_, PyAsyncEscape> = obj.extract()?;
                Ok(DoCtrl::PythonAsyncSyntaxEscape {
                    action: ae.action.clone_ref(py),
                })
            }
            DoExprTag::Effect | DoExprTag::Unknown => Err(PyTypeError::new_err(
                "yielded DoCtrlBase has unrecognized tag",
            )),
        };
    }

    if obj.is_instance_of::<DoeffGenerator>() {
        return classify_doeff_generator_as_irstream(py, obj, None, "yielded value");
    }

    if obj.is_instance_of::<PyDoExprBase>() {
        let generated = obj.call_method0("to_generator").map_err(|err| {
            if err
                .matches(py, py.get_type::<PyAttributeError>())
                .unwrap_or(false)
            {
                PyTypeError::new_err("DoExpr object is missing to_generator()")
            } else {
                err
            }
        })?;
        let ty_name = obj
            .get_type()
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "DoExpr".to_string());
        let metadata = CallMetadata::new(
            format!("{ty_name}.to_generator"),
            "<doexpr>".to_string(),
            0,
            None,
            Some(PyShared::new(obj.clone().unbind())),
        );
        return classify_doeff_generator_as_irstream(
            py,
            generated.as_any(),
            Some(metadata),
            "DoExpr.to_generator",
        );
    }

    // Fallback: bare effect -> auto-lift to Perform (R14-C)
    if is_effect_base_like(py, obj)? {
        if obj.is_instance_of::<PyProgramCallStack>() {
            return Ok(DoCtrl::GetCallStack);
        }
        return Ok(DoCtrl::Perform {
            effect: dispatch_from_shared(PyShared::new(obj.clone().unbind())),
        });
    }

    Err(PyTypeError::new_err(
        "yielded value must be EffectBase or DoExpr",
    ))
}

pub(crate) fn classify_yielded_for_vm(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<DoCtrl, PyException> {
    classify_yielded_bound(vm, py, obj).map_err(|err| {
        pyerr_to_exception(py, err)
            .unwrap_or_else(|conv_err| PyException::runtime_error(format!("{conv_err}")))
    })
}

fn call_metadata_from_pyapply(
    py: Python<'_>,
    apply: &PyRef<'_, PyApply>,
) -> PyResult<CallMetadata> {
    call_metadata_from_required_meta(py, &apply.meta, "Apply")
}

fn call_metadata_from_pyexpand(
    py: Python<'_>,
    expand: &PyRef<'_, PyExpand>,
) -> PyResult<CallMetadata> {
    call_metadata_from_required_meta(py, &expand.meta, "Expand")
}

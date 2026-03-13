//! Handler types for effect handling.
//!
//! Important: even Rust-implemented handlers in this module are user-space
//! handler implementations. They are dispatched by the VM, not part of VM core
//! stepping semantics.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::sync::mpsc::sync_channel;
use std::thread::{self, JoinHandle};

use pyo3::prelude::*;
use pyo3::types::{PyCFunction, PyDict, PyTuple};

use crate::continuation::Continuation;
use crate::do_ctrl::DoCtrl;
#[cfg(test)]
use crate::effect::Effect;
use crate::effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, DispatchEffect, PyAcquireSemaphore,
    PyAsk, PyCreateExternalPromise, PyCreateSemaphore, PyGather, PyGet, PyLocal,
    PyModify, PyPut, PyPythonAsyncioAwaitEffect, PyReleaseSemaphore, PyResultSafeEffect, PyTell,
};
use crate::error::VMError;
use crate::ir_stream::{IRStream, IRStreamStep, StreamLocation};
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyResultErr, PyResultOk};
use crate::ids::HandlerScopeId;
use doeff_vm_core::rust_store::HandlerStateKey;
use crate::segment::ScopeStore;
use crate::step::{PyException, PythonCall};
use crate::value::Value;
use crate::vm::RustStore;
use doeff_vm_core::{IRStreamFactory, IRStreamProgram, IRStreamProgramRef};

enum ParsedStateEffect {
    Get { key: String },
    Put { key: String, value: Value },
    Modify { key: String, modifier: PyShared },
}

fn parse_state_python_effect(effect: &PyShared) -> Result<Option<ParsedStateEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if let Ok(get) = obj.extract::<PyRef<'_, PyGet>>() {
            return Ok(Some(ParsedStateEffect::Get {
                key: get.key.clone(),
            }));
        }

        if let Ok(put) = obj.extract::<PyRef<'_, PyPut>>() {
            return Ok(Some(ParsedStateEffect::Put {
                key: put.key.clone(),
                value: Value::from_pyobject(put.value.bind(py)),
            }));
        }

        if let Ok(modify) = obj.extract::<PyRef<'_, PyModify>>() {
            return Ok(Some(ParsedStateEffect::Modify {
                key: modify.key.clone(),
                modifier: PyShared::new(modify.func.clone_ref(py)),
            }));
        }

        Ok(None)
    })
}

fn parse_reader_python_effect(effect: &PyShared) -> Result<Option<HashedPyKey>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if obj.is_instance_of::<PyAsk>() {
            let key_obj = obj.getattr("key").map_err(|e| e.to_string())?;
            let hashed = HashedPyKey::from_bound(&key_obj)
                .map_err(|e| format!("Ask key is not hashable: {e}"))?;
            return Ok(Some(hashed));
        }

        Ok(None)
    })
}

#[derive(Debug)]
struct ParsedLocalEffect {
    overrides: HashMap<HashedPyKey, Value>,
    sub_program: PyShared,
}

fn is_local_python_effect(effect: &PyShared) -> bool {
    Python::attach(|py| effect.bind(py).is_instance_of::<PyLocal>())
}

fn parse_local_python_effect(effect: &PyShared) -> Result<Option<ParsedLocalEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if !obj.is_instance_of::<PyLocal>() {
            return Ok(None);
        }

        let env_update = obj.getattr("env_update").map_err(|e| e.to_string())?;
        let env_update = env_update
            .cast::<PyDict>()
            .map_err(|_| "Local env_update must be a dict".to_string())?;

        let mut overrides = HashMap::new();
        for (key, value) in env_update.iter() {
            let key = HashedPyKey::from_bound(&key)
                .map_err(|e| format!("Local env key is not hashable: {e}"))?;
            overrides.insert(key, Value::from_python_opaque(&value));
        }

        let sub_program = obj.getattr("sub_program").map_err(|e| e.to_string())?;

        Ok(Some(ParsedLocalEffect {
            overrides,
            sub_program: PyShared::new(sub_program.unbind()),
        }))
    })
}

fn ask_from_scope_or_env(
    store: &RustStore,
    scope: &ScopeStore,
    key: &HashedPyKey,
) -> Option<Value> {
    for layer in scope.scope_bindings.iter().rev() {
        if let Some(value) = layer.get(key) {
            return Some(value.clone());
        }
    }
    store.ask(key).cloned()
}

fn missing_env_key_error(key: &HashedPyKey) -> PyException {
    Python::attach(|py| {
        let maybe_exc = (|| -> PyResult<Py<PyAny>> {
            let cls = py.import("doeff.errors")?.getattr("MissingEnvKeyError")?;
            let value = cls.call1((key.to_pyobject(py),))?;
            Ok(value.unbind())
        })();

        match maybe_exc {
            Ok(exc_value) => {
                let exc_type = exc_value.bind(py).get_type().into_any().unbind();
                PyException::new(exc_type, exc_value, None)
            }
            Err(_) => {
                let err = pyo3::exceptions::PyKeyError::new_err(key.display_for_error());
                let exc_value = err.value(py).clone().into_any().unbind();
                let exc_type = exc_value.bind(py).get_type().into_any().unbind();
                PyException::new(exc_type, exc_value, None)
            }
        }
    })
}

fn missing_state_key_error(key: &str) -> PyException {
    Python::attach(|py| {
        let err = pyo3::exceptions::PyKeyError::new_err(key.to_string());
        let exc_value = err.value(py).clone().into_any().unbind();
        let exc_type = exc_value.bind(py).get_type().into_any().unbind();
        PyException::new(exc_type, exc_value, None)
    })
}

fn pyerr_to_exception(py: Python<'_>, err: PyErr) -> PyException {
    let exc_type = err.get_type(py).into_any().unbind();
    let exc_value = err.value(py).clone().into_any().unbind();
    let exc_tb = err.traceback(py).map(|tb| tb.into_any().unbind());
    PyException::new(exc_type, exc_value, exc_tb)
}

fn wrap_value_as_result_ok(value: Value) -> Result<Value, PyException> {
    Python::attach(|py| {
        let py_value = value
            .to_pyobject(py)
            .map_err(|e| pyerr_to_exception(py, e))?;
        let wrapped = Bound::new(
            py,
            PyResultOk {
                value: py_value.unbind(),
            },
        )
        .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(Value::Python(wrapped.into_any().unbind()))
    })
}

fn wrap_exception_as_result_err(error: PyException) -> Result<Value, PyException> {
    Python::attach(|py| {
        let wrapped = Bound::new(
            py,
            PyResultErr {
                error: error.value_clone_ref(py),
                captured_traceback: py.None(),
            },
        )
        .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(Value::Python(wrapped.into_any().unbind()))
    })
}

fn as_lazy_eval_expr(value: &Value) -> Option<PyShared> {
    let Value::Python(obj) = value else {
        return None;
    };

    Python::attach(|py| {
        let bound = obj.bind(py);

        let is_doctrl = bound.is_instance_of::<PyDoCtrlBase>();
        let is_doexpr = bound.is_instance_of::<PyDoExprBase>();
        let is_effect = bound.is_instance_of::<PyEffectBase>();

        if is_doctrl || is_doexpr || is_effect {
            Some(PyShared::new(obj.clone_ref(py)))
        } else {
            None
        }
    })
}

fn lazy_source_id(value: &Value) -> Option<usize> {
    let Value::Python(obj) = value else {
        return None;
    };
    Python::attach(|py| Some(obj.bind(py).as_ptr() as usize))
}

fn lazy_ask_create_semaphore_effect() -> Result<DispatchEffect, PyException> {
    Python::attach(|py| {
        let effect = py
            .get_type::<PyCreateSemaphore>()
            .call1((1_i64,))
            .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(dispatch_from_shared(PyShared::new(effect.unbind())))
    })
}

fn lazy_ask_acquire_semaphore_effect(semaphore: &Value) -> Result<DispatchEffect, PyException> {
    let Value::Python(semaphore_obj) = semaphore else {
        return Err(PyException::type_error(format!(
            "CreateSemaphore returned non-semaphore value: {:?}",
            semaphore
        )));
    };

    Python::attach(|py| {
        let effect = py
            .get_type::<PyAcquireSemaphore>()
            .call1((semaphore_obj.bind(py),))
            .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(dispatch_from_shared(PyShared::new(effect.unbind())))
    })
}

fn lazy_ask_release_semaphore_effect(semaphore: &Value) -> Result<DispatchEffect, PyException> {
    let Value::Python(semaphore_obj) = semaphore else {
        return Err(PyException::type_error(format!(
            "CreateSemaphore returned non-semaphore value: {:?}",
            semaphore
        )));
    };

    Python::attach(|py| {
        let effect = py
            .get_type::<PyReleaseSemaphore>()
            .call1((semaphore_obj.bind(py),))
            .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(dispatch_from_shared(PyShared::new(effect.unbind())))
    })
}

fn parse_writer_python_effect(effect: &PyShared) -> Result<Option<Value>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if obj.is_instance_of::<PyTell>() {
            let message = obj.getattr("message").map_err(|e| e.to_string())?;
            return Ok(Some(Value::from_pyobject(&message)));
        }
        Ok(None)
    })
}

fn parse_await_python_effect(effect: &PyShared) -> Result<Option<Py<PyAny>>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if let Ok(await_effect) = obj.extract::<PyRef<'_, PyPythonAsyncioAwaitEffect>>() {
            return Ok(Some(await_effect.awaitable.clone_ref(py)));
        }
        Ok(None)
    })
}

fn parse_result_safe_python_effect(effect: &PyShared) -> Result<Option<PyShared>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if let Ok(result_safe) = obj.extract::<PyRef<'_, PyResultSafeEffect>>() {
            return Ok(Some(PyShared::new(result_safe.sub_program.clone_ref(py))));
        }
        Ok(None)
    })
}

fn make_create_external_promise_effect() -> Result<DispatchEffect, PyException> {
    Python::attach(|py| {
        let effect = py
            .get_type::<PyCreateExternalPromise>()
            .call0()
            .map_err(|e| pyerr_to_exception(py, e))?;
        Ok(dispatch_from_shared(PyShared::new(effect.unbind())))
    })
}

fn make_gather_single_waitable_effect(promise_obj: &Py<PyAny>) -> Result<DispatchEffect, PyException> {
    Python::attach(|py| {
        let future = promise_obj
            .bind(py)
            .getattr("future")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let items = pyo3::types::PyList::empty(py);
        items.append(&future).map_err(|e| pyerr_to_exception(py, e))?;
        let effect = Bound::new(py, PyGather::create(py, items.into_any().unbind(), None))
            .map_err(|e| pyerr_to_exception(py, e))?
            .into_any();
        Ok(dispatch_from_shared(PyShared::new(effect.unbind())))
    })
}

const AWAIT_RUNTIME_KEY: HandlerStateKey = HandlerStateKey::new("await_runtime");

fn reject_loop_affine_awaitable(py: Python<'_>, awaitable: &Bound<'_, PyAny>) -> Result<(), PyException> {
    let close_awaitable = || {
        if let Ok(close) = awaitable.getattr("close") {
            let _ = close.call0();
        }
    };
    let asyncio = py.import("asyncio").map_err(|e| pyerr_to_exception(py, e))?;
    let future_type = asyncio
        .getattr("Future")
        .map_err(|e| pyerr_to_exception(py, e))?;
    if awaitable
        .is_instance(&future_type)
        .map_err(|e| pyerr_to_exception(py, e))?
    {
        close_awaitable();
        return Err(PyException::runtime_error(
            "Await(asyncio.Future) is not safe under Spawn/Gather in sync run(); use CreateSemaphore or async_run"
                .to_string(),
        ));
    }

    let qualname = awaitable
        .getattr("__qualname__")
        .ok()
        .and_then(|v| v.extract::<String>().ok())
        .or_else(|| {
            awaitable
                .getattr("cr_code")
                .ok()
                .and_then(|code| code.getattr("co_qualname").ok())
                .and_then(|v| v.extract::<String>().ok())
        });
    let Some(qualname) = qualname else {
        return Ok(());
    };

    const LOOP_AFFINE_PREFIXES: &[&str] = &[
        "Semaphore",
        "BoundedSemaphore",
        "Lock",
        "Event",
        "Condition",
        "Queue",
        "PriorityQueue",
        "LifoQueue",
    ];
    if LOOP_AFFINE_PREFIXES
        .iter()
        .any(|prefix| qualname == *prefix || qualname.starts_with(&format!("{prefix}.")))
    {
        close_awaitable();
        return Err(PyException::runtime_error(format!(
            "Await({qualname}) is not safe under Spawn/Gather in sync run(); use CreateSemaphore"
        )));
    }

    Ok(())
}

#[derive(Debug)]
struct AwaitRuntime {
    loop_obj: Py<PyAny>,
    thread: Option<JoinHandle<()>>,
}

impl AwaitRuntime {
    fn new() -> Result<Self, PyException> {
        let (tx, rx) = sync_channel::<Result<Py<PyAny>, String>>(1);
        let thread = thread::Builder::new()
            .name("doeff-await-runtime".to_string())
            .spawn(move || {
                Python::attach(|py| {
                    let result = (|| -> Result<Py<PyAny>, String> {
                        let asyncio = py.import("asyncio").map_err(|e| e.to_string())?;
                        let loop_obj = asyncio
                            .call_method0("new_event_loop")
                            .map_err(|e| e.to_string())?;
                        asyncio
                            .call_method1("set_event_loop", (loop_obj.clone(),))
                            .map_err(|e| e.to_string())?;
                        let loop_py = loop_obj.unbind();
                        tx.send(Ok(loop_py.clone_ref(py))).map_err(|e| e.to_string())?;
                        let _ = loop_py.bind(py).call_method0("run_forever");
                        let _ = asyncio.call_method1("set_event_loop", (py.None(),));
                        let _ = loop_py.bind(py).call_method0("close");
                        Ok(loop_py)
                    })();
                    if let Err(err) = result {
                        let _ = tx.send(Err(err));
                    }
                });
            })
            .map_err(|err| {
                PyException::runtime_error(format!(
                    "failed to spawn sync Await runtime thread: {err}"
                ))
            })?;

        let loop_obj = rx
            .recv()
            .map_err(|err| {
                PyException::runtime_error(format!(
                    "failed to initialize sync Await runtime: {err}"
                ))
            })?
            .map_err(PyException::runtime_error)?;

        Ok(Self {
            loop_obj,
            thread: Some(thread),
        })
    }

    fn shutdown(&mut self) {
        Python::attach(|py| {
            if let Ok(stop) = self.loop_obj.bind(py).getattr("stop") {
                let _ = self
                    .loop_obj
                    .bind(py)
                    .call_method1("call_soon_threadsafe", (stop,));
            }
        });
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    fn submit(&self, py: Python<'_>, awaitable: Py<PyAny>, promise: Py<PyAny>) -> Result<(), PyException> {
        reject_loop_affine_awaitable(py, &awaitable.bind(py))?;

        let loop_obj = self.loop_obj.clone_ref(py);
        let schedule = PyCFunction::new_closure(py, None, None, move |args: &Bound<'_, PyTuple>, _kwargs| -> PyResult<()> {
            let py = args.py();
            let asyncio = py.import("asyncio")?;
            let kwargs = PyDict::new(py);
            kwargs.set_item("loop", loop_obj.bind(py))?;

            let promise_for_done = promise.clone_ref(py);
            let done_cb = PyCFunction::new_closure(py, None, None, move |args: &Bound<'_, PyTuple>, _kwargs| -> PyResult<()> {
                let py = args.py();
                let future = args.get_item(0)?;
                match future.call_method0("result") {
                    Ok(value) => {
                        promise_for_done.bind(py).call_method1("complete", (value,))?;
                    }
                    Err(err) => {
                        promise_for_done.bind(py).call_method1(
                            "fail",
                            (err.value(py).clone().into_any().unbind(),),
                        )?;
                    }
                }
                Ok(())
            })?;

            let future =
                asyncio.call_method("ensure_future", (awaitable.bind(py),), Some(&kwargs))?;
            future.call_method1("add_done_callback", (&done_cb,))?;
            Ok(())
        })
        .map_err(|e| pyerr_to_exception(py, e))?;

        self.loop_obj
            .bind(py)
            .call_method1("call_soon_threadsafe", (&schedule,))?;
        Ok(())
    }
}

impl Drop for AwaitRuntime {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn await_runtime_for_scope(
    store: &mut RustStore,
    scope_id: HandlerScopeId,
) -> Result<&Mutex<AwaitRuntime>, PyException> {
    if store
        .handler_rust_get::<Mutex<AwaitRuntime>>(scope_id, AWAIT_RUNTIME_KEY)
        .is_none()
    {
        store.handler_rust_set(
            scope_id,
            AWAIT_RUNTIME_KEY,
            Mutex::new(AwaitRuntime::new()?),
        );
    }
    store
        .handler_rust_get::<Mutex<AwaitRuntime>>(scope_id, AWAIT_RUNTIME_KEY)
        .ok_or_else(|| PyException::runtime_error("missing sync Await runtime".to_string()))
}

// ---------------------------------------------------------------------------
// AwaitHandlerFactory + AwaitHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct AwaitHandlerFactory;

impl IRStreamFactory for AwaitHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };
        parse_await_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "AwaitHandler can_handle failed to parse effect: {msg}"
                ))
            })
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(AwaitHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "AwaitHandler"
    }
}

#[derive(Debug)]
enum AwaitPhase {
    Idle,
    AwaitExternalPromise {
        continuation: Continuation,
        awaitable: Py<PyAny>,
    },
    AwaitResult {
        continuation: Continuation,
    },
}

#[derive(Debug)]
struct AwaitHandlerProgram {
    phase: AwaitPhase,
    handler_scope_id: Option<HandlerScopeId>,
}

impl AwaitHandlerProgram {
    fn new() -> Self {
        AwaitHandlerProgram {
            phase: AwaitPhase::Idle,
            handler_scope_id: None,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        match self.phase {
            AwaitPhase::Idle => "Idle",
            AwaitPhase::AwaitExternalPromise { .. } => "AwaitExternalPromise",
            AwaitPhase::AwaitResult { .. } => "AwaitResult",
        }
    }
}

impl IRStreamProgram for AwaitHandlerProgram {
    fn start(
        &mut self,
        py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        self.handler_scope_id = k.handler_scope_id;
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_await_python_effect(&obj) {
                Ok(Some(awaitable)) => {
                    self.phase = AwaitPhase::AwaitExternalPromise {
                        continuation: k,
                        awaitable: awaitable.clone_ref(py),
                    };
                    match make_create_external_promise_effect() {
                        Ok(effect) => IRStreamStep::Yield(DoCtrl::Delegate { effect }),
                        Err(error) => IRStreamStep::Throw(error),
                    }
                }
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse await effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        let phase = std::mem::replace(&mut self.phase, AwaitPhase::Idle);
        match phase {
            AwaitPhase::AwaitExternalPromise {
                continuation,
                awaitable,
            } => {
                let Value::Python(promise_obj) = value else {
                    return IRStreamStep::Throw(PyException::type_error(
                        "AwaitHandler expected ExternalPromise from CreateExternalPromise"
                            .to_string(),
                    ));
                };
                let promise_clone = Python::attach(|py| promise_obj.clone_ref(py));
                let Some(scope_id) = self.handler_scope_id else {
                    return IRStreamStep::Throw(PyException::runtime_error(
                        "sync Await handler missing handler scope id".to_string(),
                    ));
                };
                let submit_result = Python::attach(|py| {
                    let runtime = await_runtime_for_scope(store, scope_id)?;
                    let runtime = runtime.lock().expect("Await runtime lock poisoned");
                    runtime.submit(py, awaitable.clone_ref(py), promise_obj.clone_ref(py))
                });
                if let Err(error) = submit_result {
                    return IRStreamStep::Throw(error);
                }
                self.phase = AwaitPhase::AwaitResult { continuation };
                match make_gather_single_waitable_effect(&promise_clone) {
                    Ok(effect) => IRStreamStep::Yield(DoCtrl::Delegate { effect }),
                    Err(error) => IRStreamStep::Throw(error),
                }
            }
            AwaitPhase::AwaitResult { continuation } => {
                let value = match value {
                    Value::List(mut items) => {
                        if items.len() != 1 {
                            return IRStreamStep::Throw(PyException::type_error(
                                "AwaitHandler expected Gather([future]) to return exactly one value"
                                    .to_string(),
                            ));
                        }
                        items.remove(0)
                    }
                    other => other,
                };
                IRStreamStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                })
            }
            AwaitPhase::Idle => IRStreamStep::Return(value),
        }
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        let phase = std::mem::replace(&mut self.phase, AwaitPhase::Idle);
        match phase {
            AwaitPhase::AwaitExternalPromise { continuation, .. }
            | AwaitPhase::AwaitResult { continuation } => {
                IRStreamStep::Yield(DoCtrl::TransferThrow {
                    continuation,
                    exception: exc,
                })
            }
            AwaitPhase::Idle => IRStreamStep::Throw(exc),
        }
    }
}

impl IRStream for AwaitHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "AwaitHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// StateHandlerFactory + StateHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct StateHandlerFactory;

impl IRStreamFactory for StateHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        #[cfg(test)]
        if matches!(
            effect,
            Effect::Get { .. } | Effect::Put { .. } | Effect::Modify { .. }
        ) {
            return Ok(true);
        }

        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        parse_state_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "StateHandler can_handle failed to parse effect: {msg}"
                ))
            })
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(StateHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "StateHandler"
    }
}

struct StateHandlerProgram {
    pending_key: Option<String>,
    pending_k: Option<Continuation>,
    pending_old_value: Option<Value>,
}

impl std::fmt::Debug for StateHandlerProgram {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StateHandlerProgram").finish()
    }
}

impl StateHandlerProgram {
    fn new() -> Self {
        StateHandlerProgram {
            pending_key: None,
            pending_k: None,
            pending_old_value: None,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        if self.pending_key.is_some() {
            "ModifyApply"
        } else {
            "Idle"
        }
    }
}

impl IRStreamProgram for StateHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        #[cfg(test)]
        if let Effect::Get { key } = effect.clone() {
            let Some(value) = store.get(&key).cloned() else {
                return IRStreamStep::Throw(missing_state_key_error(&key));
            };
            return IRStreamStep::Yield(DoCtrl::Resume {
                continuation: k,
                value,
            });
        }

        #[cfg(test)]
        if let Effect::Put { key, value } = effect.clone() {
            store.put(key, value);
            return IRStreamStep::Yield(DoCtrl::Resume {
                continuation: k,
                value: Value::Unit,
            });
        }

        #[cfg(test)]
        if let Effect::Modify { key, modifier } = effect.clone() {
            let old_value = store.get(&key).cloned().unwrap_or(Value::None);
            self.pending_key = Some(key);
            self.pending_k = Some(k);
            self.pending_old_value = Some(old_value.clone());
            return IRStreamStep::NeedsPython(PythonCall::CallFunc {
                func: modifier,
                args: vec![old_value],
                kwargs: vec![],
            });
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_state_python_effect(&obj) {
                Ok(Some(parsed)) => match parsed {
                    ParsedStateEffect::Get { key } => {
                        let Some(value) = store.get(&key).cloned() else {
                            return IRStreamStep::Throw(missing_state_key_error(&key));
                        };
                        IRStreamStep::Yield(DoCtrl::Resume {
                            continuation: k,
                            value,
                        })
                    }
                    ParsedStateEffect::Put { key, value } => {
                        store.put(key, value);
                        IRStreamStep::Yield(DoCtrl::Resume {
                            continuation: k,
                            value: Value::Unit,
                        })
                    }
                    ParsedStateEffect::Modify { key, modifier } => {
                        let old_value = store.get(&key).cloned().unwrap_or(Value::None);
                        self.pending_key = Some(key);
                        self.pending_k = Some(k);
                        self.pending_old_value = Some(old_value.clone());
                        IRStreamStep::NeedsPython(PythonCall::CallFunc {
                            func: modifier,
                            args: vec![old_value],
                            kwargs: vec![],
                        })
                    }
                },
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse state effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        if self.pending_key.is_none() {
            // Terminal case (Get/Put): handler is done, pass through return value
            return IRStreamStep::Return(value);
        }
        // Modify case: store modifier result but resume caller with OLD value.
        // SPEC-008 L1271: Modify is read-then-modify, returns the old value.
        let key = self.pending_key.take().expect(
            "StateHandler Modify invariant violated: pending key missing during resume",
        );
        let continuation = self.pending_k.take().expect(
            "StateHandler Modify invariant violated: pending continuation missing during resume",
        );
        let old_value = self.pending_old_value.take().expect(
            "StateHandler Modify invariant violated: pending old_value missing during resume",
        );
        store.put(key, value);
        IRStreamStep::Yield(DoCtrl::Resume {
            continuation,
            value: old_value,
        })
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        IRStreamStep::Throw(exc)
    }
}

impl IRStream for StateHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "StateHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// LazyAskHandlerFactory + LazyAskHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct LazyCacheEntry {
    source_id: usize,
    value: Value,
}

#[derive(Debug, Clone)]
struct LazySemaphoreEntry {
    source_id: usize,
    semaphore: Value,
}

#[derive(Debug, Clone, Default)]
struct LazyAskState {
    cache: HashMap<HashedPyKey, LazyCacheEntry>,
    semaphores: HashMap<HashedPyKey, LazySemaphoreEntry>,
}

const LAZY_ASK_STATE_KEY: HandlerStateKey = HandlerStateKey::new("lazy_ask_state");

#[derive(Clone, Default)]
pub struct LazyAskHandlerFactory;

impl std::fmt::Debug for LazyAskHandlerFactory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LazyAskHandlerFactory").finish()
    }
}

impl LazyAskHandlerFactory {
    pub fn new() -> Self {
        LazyAskHandlerFactory
    }
}

impl IRStreamFactory for LazyAskHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        #[cfg(test)]
        if matches!(effect, Effect::Ask { .. }) {
            return Ok(true);
        }

        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        let is_reader = parse_reader_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "LazyAskHandler can_handle failed to parse reader effect: {msg}"
                ))
            })?;

        Ok(is_reader || is_local_python_effect(obj))
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(LazyAskHandlerProgram::new())))
    }

    fn create_program_for_run(
        &self,
        run_context: Option<doeff_vm_core::handler::RunContext>,
    ) -> IRStreamProgramRef {
        let _ = run_context;
        Arc::new(Mutex::new(Box::new(LazyAskHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "LazyAskHandler"
    }
}

#[derive(Debug)]
enum LazyAskPhase {
    Idle,
    AwaitDelegate {
        continuation: Continuation,
    },
    AwaitLocalEval {
        continuation: Continuation,
        cache_snapshot: HashMap<HashedPyKey, LazyCacheEntry>,
        semaphore_snapshot: HashMap<HashedPyKey, LazySemaphoreEntry>,
    },
    AwaitAcquire {
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Option<Value>,
    },
    AwaitEval {
        key: HashedPyKey,
        continuation: Continuation,
        source_id: usize,
        semaphore: Value,
    },
    AwaitRelease {
        continuation: Continuation,
        outcome: Result<Value, PyException>,
    },
}

#[derive(Debug)]
struct LazyAskHandlerProgram {
    phase: LazyAskPhase,
    handler_scope_id: Option<HandlerScopeId>,
}

impl LazyAskHandlerProgram {
    fn new() -> Self {
        LazyAskHandlerProgram {
            phase: LazyAskPhase::Idle,
            handler_scope_id: None,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        match self.phase {
            LazyAskPhase::Idle => "Idle",
            LazyAskPhase::AwaitDelegate { .. } => "AwaitDelegate",
            LazyAskPhase::AwaitLocalEval { .. } => "AwaitLocalEval",
            LazyAskPhase::AwaitAcquire { .. } => "AwaitAcquire",
            LazyAskPhase::AwaitEval { .. } => "AwaitEval",
            LazyAskPhase::AwaitRelease { .. } => "AwaitRelease",
        }
    }

    fn yield_perform(effect: Result<DispatchEffect, PyException>) -> IRStreamStep {
        match effect {
            Ok(effect) => IRStreamStep::Yield(DoCtrl::Perform { effect }),
            Err(exc) => IRStreamStep::Throw(exc),
        }
    }

    fn transfer_throw(continuation: Continuation, exception: PyException) -> IRStreamStep {
        IRStreamStep::Yield(DoCtrl::TransferThrow {
            continuation,
            exception,
        })
    }

    fn begin_delegate_phase(&mut self, continuation: Continuation, effect: DispatchEffect) -> IRStreamStep {
        self.phase = LazyAskPhase::AwaitDelegate { continuation };
        IRStreamStep::Yield(DoCtrl::Delegate { effect })
    }

    fn scope_id(&self) -> Result<HandlerScopeId, PyException> {
        self.handler_scope_id.ok_or_else(|| {
            PyException::runtime_error("LazyAskHandler missing handler scope id".to_string())
        })
    }

    fn snapshot_lazy_state(
        &self,
        store: &RustStore,
    ) -> Result<
        (
        HashMap<HashedPyKey, LazyCacheEntry>,
        HashMap<HashedPyKey, LazySemaphoreEntry>,
    ),
        PyException,
    > {
        let scope_id = self.scope_id()?;
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .map(|state| state.lock().expect("LazyAsk lock poisoned").clone())
            .unwrap_or_default();
        Ok((state.cache, state.semaphores))
    }

    fn restore_lazy_state(
        &self,
        store: &mut RustStore,
        cache_snapshot: HashMap<HashedPyKey, LazyCacheEntry>,
        semaphore_snapshot: HashMap<HashedPyKey, LazySemaphoreEntry>,
    ) -> Result<(), PyException> {
        let scope_id = self.scope_id()?;
        if store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .is_none()
        {
            store.handler_rust_set(scope_id, LAZY_ASK_STATE_KEY, Mutex::new(LazyAskState::default()));
        }
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .expect("LazyAsk state must exist after initialization");
        let mut state = state.lock().expect("LazyAsk lock poisoned");
        state.cache = cache_snapshot;
        state.semaphores = semaphore_snapshot;
        Ok(())
    }

    fn exit_local_scope(
        &self,
        store: &mut RustStore,
        scope: &mut ScopeStore,
        cache_snapshot: HashMap<HashedPyKey, LazyCacheEntry>,
        semaphore_snapshot: HashMap<HashedPyKey, LazySemaphoreEntry>,
    ) -> Result<(), PyException> {
        if scope.scope_bindings.pop().is_none() {
            return Err(PyException::runtime_error(
                "Local scope stack underflow in LazyAskHandler".to_string(),
            ));
        }
        self.restore_lazy_state(store, cache_snapshot, semaphore_snapshot)
    }

    fn lazy_cache_get(&self, store: &RustStore, key: &HashedPyKey, source_id: usize) -> Option<Value> {
        let scope_id = self.handler_scope_id?;
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)?
            .lock()
            .expect("LazyAsk lock poisoned");
        let entry = state.cache.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.value.clone());
        }
        None
    }

    fn lazy_cache_put(&self, store: &mut RustStore, key: HashedPyKey, source_id: usize, value: Value) {
        let Ok(scope_id) = self.scope_id() else {
            return;
        };
        if store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .is_none()
        {
            store.handler_rust_set(scope_id, LAZY_ASK_STATE_KEY, Mutex::new(LazyAskState::default()));
        }
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .expect("LazyAsk state must exist after initialization");
        state
            .lock()
            .expect("LazyAsk lock poisoned")
            .cache
            .insert(key, LazyCacheEntry { source_id, value });
    }

    fn lazy_semaphore_get(
        &self,
        store: &RustStore,
        key: &HashedPyKey,
        source_id: usize,
    ) -> Option<Value> {
        let scope_id = self.handler_scope_id?;
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)?
            .lock()
            .expect("LazyAsk lock poisoned");
        let entry = state.semaphores.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.semaphore.clone());
        }
        None
    }

    fn lazy_semaphore_put(
        &self,
        store: &mut RustStore,
        key: HashedPyKey,
        source_id: usize,
        semaphore: Value,
    ) {
        let Ok(scope_id) = self.scope_id() else {
            return;
        };
        if store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .is_none()
        {
            store.handler_rust_set(scope_id, LAZY_ASK_STATE_KEY, Mutex::new(LazyAskState::default()));
        }
        let state = store
            .handler_rust_get::<Mutex<LazyAskState>>(scope_id, LAZY_ASK_STATE_KEY)
            .expect("LazyAsk state must exist after initialization");
        state.lock().expect("LazyAsk lock poisoned").semaphores.insert(
            key,
            LazySemaphoreEntry {
                source_id,
                semaphore,
            },
        );
    }

    fn begin_create_then_acquire_phase(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
    ) -> IRStreamStep {
        self.phase = LazyAskPhase::AwaitAcquire {
            key,
            continuation,
            expr,
            source_id,
            semaphore: None,
        };
        Self::yield_perform(lazy_ask_create_semaphore_effect())
    }

    fn begin_acquire_phase(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Value,
    ) -> IRStreamStep {
        let effect = lazy_ask_acquire_semaphore_effect(&semaphore);
        self.phase = LazyAskPhase::AwaitAcquire {
            key,
            continuation,
            expr,
            source_id,
            semaphore: Some(semaphore),
        };
        Self::yield_perform(effect)
    }

    fn begin_release_phase(
        &mut self,
        continuation: Continuation,
        outcome: Result<Value, PyException>,
        semaphore: Value,
    ) -> IRStreamStep {
        self.phase = LazyAskPhase::AwaitRelease {
            continuation,
            outcome,
        };
        Self::yield_perform(lazy_ask_release_semaphore_effect(&semaphore))
    }

    fn handle_ask_value(
        &mut self,
        store: &mut RustStore,
        key: HashedPyKey,
        continuation: Continuation,
        value: Value,
    ) -> IRStreamStep {
        let Some(expr) = as_lazy_eval_expr(&value) else {
            return IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            });
        };

        let source_id = lazy_source_id(&value).unwrap_or_default();

        if let Some(cached) = self.lazy_cache_get(store, &key, source_id) {
            return IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value: cached,
            });
        }

        if let Some(semaphore) = self.lazy_semaphore_get(store, &key, source_id) {
            return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
        }

        self.begin_create_then_acquire_phase(key, continuation, expr, source_id)
    }
}

impl IRStreamProgram for LazyAskHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep {
        self.handler_scope_id = k.handler_scope_id;
        #[cfg(test)]
        if let Effect::Ask { key } = effect.clone() {
            let hashed_key = HashedPyKey::from_test_string(key);
            let Some(value) = ask_from_scope_or_env(store, scope, &hashed_key) else {
                return IRStreamStep::Throw(missing_env_key_error(&hashed_key));
            };
            return self.handle_ask_value(store, hashed_key, k, value);
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            match parse_local_python_effect(&obj) {
                Ok(Some(local_effect)) => {
                    let (cache_snapshot, semaphore_snapshot) = match self.snapshot_lazy_state(store) {
                        Ok(value) => value,
                        Err(exc) => return IRStreamStep::Throw(exc),
                    };
                    scope.scope_bindings.push(Arc::new(local_effect.overrides));
                    let eval_scope = k.clone();
                    self.phase = LazyAskPhase::AwaitLocalEval {
                        continuation: k,
                        cache_snapshot,
                        semaphore_snapshot,
                    };
                    return IRStreamStep::Yield(DoCtrl::EvalInScope {
                        expr: local_effect.sub_program,
                        scope: eval_scope,
                        metadata: None,
                    });
                }
                Ok(None) => {}
                Err(msg) => {
                    return IRStreamStep::Throw(PyException::type_error(format!(
                        "failed to parse Local effect: {msg}"
                    )));
                }
            }

            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => {
                    if let Some(value) = ask_from_scope_or_env(store, scope, &key) {
                        return self.handle_ask_value(store, key, k, value);
                    }
                    self.begin_delegate_phase(k, dispatch_from_shared(obj))
                }
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse lazy Ask effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match std::mem::replace(&mut self.phase, LazyAskPhase::Idle) {
            LazyAskPhase::AwaitDelegate { continuation } => IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }),
            LazyAskPhase::AwaitLocalEval {
                continuation,
                cache_snapshot,
                semaphore_snapshot,
            } => {
                if let Err(exc) = self.exit_local_scope(store, scope, cache_snapshot, semaphore_snapshot) {
                    return IRStreamStep::Throw(exc);
                }
                IRStreamStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                })
            }
            LazyAskPhase::AwaitAcquire {
                key,
                continuation,
                expr,
                source_id,
                semaphore,
            } => {
                let Some(semaphore) = semaphore else {
                    let semaphore = match value {
                        Value::Python(_) => value,
                        Value::Unit
                        | Value::Int(_)
                        | Value::String(_)
                        | Value::Bool(_)
                        | Value::None
                        | Value::Continuation(_)
                        | Value::Handlers(_)
                        | Value::Kleisli(_)
                        | Value::Task(_)
                        | Value::Promise(_)
                        | Value::ExternalPromise(_)
                        | Value::CallStack(_)
                        | Value::Trace(_)
                        | Value::Traceback(_)
                        | Value::ActiveChain(_)
                        | Value::List(_) => {
                            return IRStreamStep::Throw(PyException::type_error(
                                "CreateSemaphore must return a semaphore handle".to_string(),
                            ));
                        }
                    };
                    self.lazy_semaphore_put(store, key.clone(), source_id, semaphore.clone());
                    return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
                };

                if let Some(cached) = self.lazy_cache_get(store, &key, source_id) {
                    return self.begin_release_phase(continuation, Ok(cached), semaphore);
                }

                let eval_scope = continuation.clone();
                self.phase = LazyAskPhase::AwaitEval {
                    key,
                    continuation,
                    source_id,
                    semaphore,
                };
                IRStreamStep::Yield(DoCtrl::EvalInScope {
                    expr,
                    scope: eval_scope,
                    metadata: None,
                })
            }
            LazyAskPhase::AwaitEval {
                key,
                continuation,
                source_id,
                semaphore,
            } => {
                self.lazy_cache_put(store, key, source_id, value.clone());
                self.begin_release_phase(continuation, Ok(value), semaphore)
            }
            LazyAskPhase::AwaitRelease {
                continuation,
                outcome,
            } => match outcome {
                Ok(value) => IRStreamStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                }),
                Err(exception) => Self::transfer_throw(continuation, exception),
            },
            LazyAskPhase::Idle => IRStreamStep::Return(value),
        }
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match std::mem::replace(&mut self.phase, LazyAskPhase::Idle) {
            LazyAskPhase::AwaitDelegate { continuation } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitLocalEval {
                continuation,
                cache_snapshot,
                semaphore_snapshot,
            } => {
                if let Err(scope_exc) =
                    self.exit_local_scope(store, scope, cache_snapshot, semaphore_snapshot)
                {
                    return IRStreamStep::Throw(scope_exc);
                }
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitAcquire { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitEval {
                continuation,
                semaphore,
                ..
            } => self.begin_release_phase(continuation, Err(exc), semaphore),
            LazyAskPhase::AwaitRelease { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::Idle => IRStreamStep::Throw(exc),
        }
    }
}

impl IRStream for LazyAskHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "LazyAskHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// ReaderHandlerFactory + ReaderHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct ReaderHandlerFactory;

impl IRStreamFactory for ReaderHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        #[cfg(test)]
        if matches!(effect, Effect::Ask { .. }) {
            return Ok(true);
        }

        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        parse_reader_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "ReaderHandler can_handle failed to parse effect: {msg}"
                ))
            })
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram)))
    }

    fn handler_name(&self) -> &'static str {
        "ReaderHandler"
    }
}

#[derive(Debug)]
struct ReaderHandlerProgram;

impl ReaderHandlerProgram {
    fn handle_ask(
        key: HashedPyKey,
        continuation: Continuation,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep {
        let Some(value) = ask_from_scope_or_env(store, scope, &key) else {
            return IRStreamStep::Throw(missing_env_key_error(&key));
        };

        IRStreamStep::Yield(DoCtrl::Resume {
            continuation,
            value,
        })
    }

    fn current_phase_name(&self) -> &'static str {
        "AskApply"
    }
}

impl IRStreamProgram for ReaderHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep {
        #[cfg(test)]
        if let Effect::Ask { key } = effect.clone() {
            return ReaderHandlerProgram::handle_ask(
                HashedPyKey::from_test_string(key),
                k,
                store,
                scope,
            );
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => ReaderHandlerProgram::handle_ask(key, k, store, scope),
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse reader effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(
        &mut self,
        value: Value,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        if false {
            unreachable!("ReaderHandler never yields mid-handling");
        }
        IRStreamStep::Return(value)
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        IRStreamStep::Throw(exc)
    }
}

impl IRStream for ReaderHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "ReaderHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// WriterHandlerFactory + WriterHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct WriterHandlerFactory;

impl IRStreamFactory for WriterHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        #[cfg(test)]
        if matches!(effect, Effect::Tell { .. }) {
            return Ok(true);
        }

        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        parse_writer_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "WriterHandler can_handle failed to parse effect: {msg}"
                ))
            })
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(WriterHandlerProgram)))
    }

    fn handler_name(&self) -> &'static str {
        "WriterHandler"
    }
}

#[derive(Debug)]
struct WriterHandlerProgram;

impl WriterHandlerProgram {
    fn current_phase_name(&self) -> &'static str {
        "TellApply"
    }
}

impl IRStreamProgram for WriterHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        #[cfg(test)]
        if let Effect::Tell { message } = effect.clone() {
            store.tell(message);
            return IRStreamStep::Yield(DoCtrl::Resume {
                continuation: k,
                value: Value::Unit,
            });
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_writer_python_effect(&obj) {
                Ok(Some(message)) => {
                    store.tell(message);
                    IRStreamStep::Yield(DoCtrl::Resume {
                        continuation: k,
                        value: Value::Unit,
                    })
                }
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse writer effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _: &mut RustStore, _scope: &mut ScopeStore) -> IRStreamStep {
        if false {
            unreachable!("WriterHandler never yields mid-handling");
        }
        IRStreamStep::Return(value)
    }

    fn throw(
        &mut self,
        exc: PyException,
        _: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        IRStreamStep::Throw(exc)
    }
}

impl IRStream for WriterHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "WriterHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// ResultSafeHandlerFactory + ResultSafeHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct ResultSafeHandlerFactory;

impl IRStreamFactory for ResultSafeHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        let Some(obj) = dispatch_ref_as_python(effect) else {
            return Ok(false);
        };

        parse_result_safe_python_effect(obj)
            .map(|parsed| parsed.is_some())
            .map_err(|msg| {
                VMError::internal(format!(
                    "ResultSafeHandler can_handle failed to parse effect: {msg}"
                ))
            })
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(ResultSafeHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "ResultSafeHandler"
    }
}

#[derive(Debug)]
enum ResultSafePhase {
    Idle,
    AwaitEval { continuation: Continuation },
}

#[derive(Debug)]
struct ResultSafeHandlerProgram {
    phase: ResultSafePhase,
}

impl ResultSafeHandlerProgram {
    fn new() -> Self {
        ResultSafeHandlerProgram {
            phase: ResultSafePhase::Idle,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        match self.phase {
            ResultSafePhase::Idle => "Idle",
            ResultSafePhase::AwaitEval { .. } => "AwaitEval",
        }
    }

    fn finish_ok(&self, continuation: Continuation, value: Value) -> IRStreamStep {
        match wrap_value_as_result_ok(value) {
            Ok(wrapped) => IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value: wrapped,
            }),
            Err(exc) => IRStreamStep::Throw(exc),
        }
    }

    fn finish_err(&self, continuation: Continuation, error: PyException) -> IRStreamStep {
        match wrap_exception_as_result_err(error) {
            Ok(wrapped) => IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value: wrapped,
            }),
            Err(exc) => IRStreamStep::Throw(exc),
        }
    }
}

impl IRStreamProgram for ResultSafeHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_result_safe_python_effect(&obj) {
                Ok(Some(sub_program)) => {
                    let eval_scope = k.clone();
                    self.phase = ResultSafePhase::AwaitEval { continuation: k };
                    IRStreamStep::Yield(DoCtrl::EvalInScope {
                        expr: sub_program,
                        scope: eval_scope,
                        metadata: None,
                    })
                }
                Ok(None) => IRStreamStep::Yield(DoCtrl::Pass {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => IRStreamStep::Throw(PyException::type_error(format!(
                    "failed to parse ResultSafe effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return IRStreamStep::Yield(DoCtrl::Pass { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(
        &mut self,
        value: Value,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match std::mem::replace(&mut self.phase, ResultSafePhase::Idle) {
            ResultSafePhase::AwaitEval { continuation } => self.finish_ok(continuation, value),
            ResultSafePhase::Idle => IRStreamStep::Return(value),
        }
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match std::mem::replace(&mut self.phase, ResultSafePhase::Idle) {
            ResultSafePhase::AwaitEval { continuation } => self.finish_err(continuation, exc),
            ResultSafePhase::Idle => IRStreamStep::Throw(exc),
        }
    }
}

impl IRStream for ResultSafeHandlerProgram {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::resume(self, value, store, _scope)
    }

    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        <Self as IRStreamProgram>::throw(self, exc, store, _scope)
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Some(StreamLocation {
            function_name: "ResultSafeHandler".to_string(),
            source_file: "<rust>".to_string(),
            source_line: 0,
            phase: Some(self.current_phase_name().to_string()),
        })
    }
}

// ---------------------------------------------------------------------------
// DoubleCallHandlerFactory — test handler that does NeedsPython from resume()
// ---------------------------------------------------------------------------

/// Test-only handler that requires TWO Python calls per effect.
/// start() stores k, returns NeedsPython(call1).
/// First resume() stores result1, returns NeedsPython(call2) — THE CRITICAL PATH.
/// Second resume() yields Resume with combined result.
/// Used to test that the VM correctly handles NeedsPython from resume().
#[cfg(test)]
#[derive(Debug, Clone)]
pub(crate) struct DoubleCallHandlerFactory;

#[cfg(test)]
impl IRStreamFactory for DoubleCallHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        Ok(matches!(effect, Effect::Modify { .. }))
    }

    fn create_program(&self) -> IRStreamProgramRef {
        Arc::new(Mutex::new(Box::new(DoubleCallHandlerProgram {
            phase: DoubleCallPhase::Init,
        })))
    }

    fn handler_name(&self) -> &'static str {
        "DoubleCallHandler"
    }
}

#[cfg(test)]
#[cfg(test)]
#[derive(Debug)]
enum DoubleCallPhase {
    Init,
    AwaitingFirstResult {
        k: Continuation,
        modifier: PyShared,
    },
    AwaitingSecondResult {
        k: Continuation,
        first_result: Value,
    },
    Done,
}

#[cfg(test)]
struct DoubleCallHandlerProgram {
    phase: DoubleCallPhase,
}

#[cfg(test)]
impl std::fmt::Debug for DoubleCallHandlerProgram {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DoubleCallHandlerProgram").finish()
    }
}

#[cfg(test)]
impl IRStreamProgram for DoubleCallHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match effect {
            Effect::Modify { modifier, .. } => {
                // Store k and modifier for later. First Python call: modifier(10)
                self.phase = DoubleCallPhase::AwaitingFirstResult {
                    k,
                    modifier: modifier.clone(),
                };
                IRStreamStep::NeedsPython(PythonCall::CallFunc {
                    func: modifier,
                    args: vec![Value::Int(10)],
                    kwargs: vec![],
                })
            }
            other => IRStreamStep::Yield(DoCtrl::Pass { effect: other }),
        }
    }

    fn resume(
        &mut self,
        value: Value,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        match std::mem::replace(&mut self.phase, DoubleCallPhase::Done) {
            DoubleCallPhase::AwaitingFirstResult { k, modifier } => {
                // Got first result. Now do a SECOND Python call: modifier(first_result).
                // This is the critical path: NeedsPython from resume().
                self.phase = DoubleCallPhase::AwaitingSecondResult {
                    k,
                    first_result: value.clone(),
                };
                IRStreamStep::NeedsPython(PythonCall::CallFunc {
                    func: modifier,
                    args: vec![value],
                    kwargs: vec![],
                })
            }
            DoubleCallPhase::AwaitingSecondResult { k, first_result } => {
                // Got second result. Combine and yield Resume.
                let combined =
                    Value::Int(first_result.as_int().unwrap_or(0) + value.as_int().unwrap_or(0));
                IRStreamStep::Yield(DoCtrl::Resume {
                    continuation: k,
                    value: combined,
                })
            }
            DoubleCallPhase::Done | DoubleCallPhase::Init => IRStreamStep::Return(value),
        }
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        IRStreamStep::Throw(exc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::Marker;
    use crate::ir_stream::{IRStream, IRStreamStep};
    use crate::segment::Segment;
    use pyo3::types::PyDictMethods;
    use pyo3::{IntoPyObject, Python};

    fn make_test_continuation() -> Continuation {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None);
        let seg_id = crate::ids::SegmentId::from_index(0);
        Continuation::capture(&seg, seg_id, None)
    }

    #[test]
    fn test_rust_program_handler_ref_is_clone() {
        // Verify that Rust handler references are Clone via Arc
        // (Can't easily instantiate a trait object in unit test, but verify types compile)
        let _: fn() -> IRStreamFactoryRef = || unreachable!();
    }

    #[test]
    fn test_await_ast_stream_resume_sequence() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        let mut program = AwaitHandlerProgram::new();
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.pending_k = Some(continuation);

        let location = IRStream::debug_location(&program).expect("await debug location");
        assert_eq!(location.function_name, "AwaitHandler");
        assert_eq!(location.phase.as_deref(), Some("AwaitBridgeResult"));

        let step = IRStream::resume(&mut program, Value::Int(12), &mut store, &mut scope);
        match step {
            IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(12));
            }
            _ => panic!("expected IRStream Yield(Resume)"),
        }

        let location = IRStream::debug_location(&program).expect("await debug location");
        assert_eq!(location.phase.as_deref(), Some("Idle"));
    }

    #[test]
    fn test_state_ast_stream_modify_resume_sequence() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        store.put("count".to_string(), Value::Int(5));

        let mut program = StateHandlerProgram::new();
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.pending_key = Some("count".to_string());
        program.pending_k = Some(continuation);
        program.pending_old_value = Some(Value::Int(5));

        let location = IRStream::debug_location(&program).expect("state debug location");
        assert_eq!(location.function_name, "StateHandler");
        assert_eq!(location.phase.as_deref(), Some("ModifyApply"));

        let step = IRStream::resume(&mut program, Value::Int(8), &mut store, &mut scope);
        match step {
            IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(5));
            }
            _ => panic!("expected IRStream Yield(Resume)"),
        }

        assert_eq!(store.get("count").and_then(Value::as_int), Some(8));
        let location = IRStream::debug_location(&program).expect("state debug location");
        assert_eq!(location.phase.as_deref(), Some("Idle"));
    }

    #[test]
    #[should_panic(
        expected = "StateHandler Modify invariant violated: pending continuation missing during resume"
    )]
    fn test_state_modify_resume_invariant_message_for_missing_continuation() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        let mut program = StateHandlerProgram::new();

        program.pending_key = Some("count".to_string());
        program.pending_old_value = Some(Value::Int(5));

        let _ = IRStream::resume(&mut program, Value::Int(8), &mut store, &mut scope);
    }

    #[test]
    fn test_reader_ast_stream_throw_sequence() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        let mut program = ReaderHandlerProgram;

        let location = IRStream::debug_location(&program).expect("reader debug location");
        assert_eq!(location.function_name, "ReaderHandler");
        assert_eq!(location.phase.as_deref(), Some("AskApply"));

        let step = IRStream::throw(
            &mut program,
            PyException::runtime_error("boom"),
            &mut store,
            &mut scope,
        );
        assert!(matches!(step, IRStreamStep::Throw(_)));
    }

    #[test]
    fn test_writer_ast_stream_throw_sequence() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        let mut program = WriterHandlerProgram;

        let location = IRStream::debug_location(&program).expect("writer debug location");
        assert_eq!(location.function_name, "WriterHandler");
        assert_eq!(location.phase.as_deref(), Some("TellApply"));

        let step = IRStream::throw(
            &mut program,
            PyException::runtime_error("boom"),
            &mut store,
            &mut scope,
        );
        assert!(matches!(step, IRStreamStep::Throw(_)));
    }

    #[test]
    fn test_lazy_ask_ast_stream_release_sequence() {
        let mut store = RustStore::new();
        let mut scope = ScopeStore::default();
        let mut program = LazyAskHandlerProgram::new();
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.phase = LazyAskPhase::AwaitRelease {
            continuation,
            outcome: Ok(Value::Int(44)),
        };

        let location = IRStream::debug_location(&program).expect("lazy ask debug location");
        assert_eq!(location.function_name, "LazyAskHandler");
        assert_eq!(location.phase.as_deref(), Some("AwaitRelease"));

        let step = IRStream::resume(&mut program, Value::Unit, &mut store, &mut scope);
        match step {
            IRStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(44));
            }
            _ => panic!("expected IRStream Yield(Resume)"),
        }
    }

    #[test]
    fn test_result_safe_ast_stream_eval_in_scope_sequence() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let mut program = ResultSafeHandlerProgram::new();
            let continuation = make_test_continuation();
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item(
                    "ResultSafeEffect",
                    py.get_type::<crate::effect::PyResultSafeEffect>(),
                )
                .unwrap();
            py.run(
                c"obj = ResultSafeEffect(None)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));

            let step = IRStreamProgram::start(
                &mut program,
                py,
                effect,
                continuation,
                &mut store,
                &mut scope,
            );
            assert!(matches!(
                step,
                IRStreamStep::Yield(DoCtrl::EvalInScope { .. })
            ));

            let location = IRStream::debug_location(&program).expect("result safe debug location");
            assert_eq!(location.phase.as_deref(), Some("AwaitEval"));
        });
    }

    // --- Factory-based handler tests (R8) ---

    #[test]
    fn test_state_factory_can_handle() {
        let f = StateHandlerFactory;
        assert!(IRStreamFactory::can_handle(
            &f,
            &Effect::Get {
                key: "x".to_string()
            }
        )
        .unwrap());
        assert!(IRStreamFactory::can_handle(
            &f,
            &Effect::Put {
                key: "x".to_string(),
                value: Value::Unit
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Ask {
                key: "x".to_string()
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Tell {
                message: Value::Unit
            }
        )
        .unwrap());
    }

    #[test]
    fn test_state_factory_get() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            store.put("key".to_string(), Value::Int(42));
            let k = make_test_continuation();
            let program_ref = StateHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Get {
                        key: "key".to_string(),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            match step {
                IRStreamStep::Yield(DoCtrl::Resume { value, .. }) => {
                    assert_eq!(value.as_int(), Some(42));
                }
                _ => panic!(
                    "Expected Yield(Resume), got {:?}",
                    std::mem::discriminant(&step)
                ),
            }
        });
    }

    #[test]
    fn test_state_factory_put() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let k = make_test_continuation();
            let program_ref = StateHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Put {
                        key: "key".to_string(),
                        value: Value::Int(99),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            assert!(matches!(
                step,
                IRStreamStep::Yield(DoCtrl::Resume {
                    value: Value::Unit,
                    ..
                })
            ));
            assert_eq!(store.get("key").unwrap().as_int(), Some(99));
        });
    }

    #[test]
    fn test_state_factory_modify_needs_python() {
        use pyo3::Python;
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            store.put("key".to_string(), Value::Int(10));
            let k = make_test_continuation();
            let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let program_ref = StateHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Modify {
                        key: "key".to_string(),
                        modifier: PyShared::new(modifier),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            match step {
                IRStreamStep::NeedsPython(PythonCall::CallFunc { args, .. }) => {
                    assert_eq!(args.len(), 1);
                    assert_eq!(args[0].as_int(), Some(10));
                }
                _ => panic!("Expected NeedsPython(CallFunc)"),
            }
        });
    }

    #[test]
    fn test_state_factory_modify_resume() {
        use pyo3::Python;
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            store.put("key".to_string(), Value::Int(10));
            let k = make_test_continuation();
            let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();
            let program_ref = StateHandlerFactory.create_program();
            // start: returns NeedsPython
            {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Modify {
                        key: "key".to_string(),
                        modifier: PyShared::new(modifier),
                    },
                    k,
                    &mut store,
                    &mut scope,
                );
            }
            // resume with new value
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(20), &mut store, &mut scope)
            };
            match step {
                IRStreamStep::Yield(DoCtrl::Resume { value, .. }) => {
                    assert_eq!(value.as_int(), Some(10)); // old_value returned (SPEC-008 L1271)
                }
                _ => panic!("Expected Yield(Resume) with old_value"),
            }
            assert_eq!(store.get("key").unwrap().as_int(), Some(20)); // new value stored
        });
    }

    #[test]
    fn test_reader_factory_can_handle() {
        let f = ReaderHandlerFactory;
        assert!(IRStreamFactory::can_handle(
            &f,
            &Effect::Ask {
                key: "x".to_string()
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Get {
                key: "x".to_string()
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Tell {
                message: Value::Unit
            }
        )
        .unwrap());
    }

    #[test]
    fn test_reader_factory_ask() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            store.set_env_str("config", Value::String("value".to_string()));
            let k = make_test_continuation();
            let program_ref = ReaderHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Ask {
                        key: "config".to_string(),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            match step {
                IRStreamStep::Yield(DoCtrl::Resume { value, .. }) => {
                    assert_eq!(value.as_str(), Some("value"));
                }
                _ => panic!("Expected Yield(Resume)"),
            }
        });
    }

    #[test]
    fn test_writer_factory_can_handle() {
        let f = WriterHandlerFactory;
        assert!(IRStreamFactory::can_handle(
            &f,
            &Effect::Tell {
                message: Value::Unit
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Get {
                key: "x".to_string()
            }
        )
        .unwrap());
        assert!(!IRStreamFactory::can_handle(
            &f,
            &Effect::Ask {
                key: "x".to_string()
            }
        )
        .unwrap());
    }

    #[test]
    fn test_writer_factory_tell() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let k = make_test_continuation();
            let program_ref = WriterHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Tell {
                        message: Value::String("log".to_string()),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            assert!(matches!(
                step,
                IRStreamStep::Yield(DoCtrl::Resume {
                    value: Value::Unit,
                    ..
                })
            ));
            assert_eq!(store.logs().len(), 1);
        });
    }

    #[test]
    fn test_result_safe_factory_can_handle_python_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item(
                    "ResultSafeEffect",
                    py.get_type::<crate::effect::PyResultSafeEffect>(),
                )
                .unwrap();
            py.run(
                c"obj = ResultSafeEffect(None)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));
            let f = ResultSafeHandlerFactory;
            assert!(
                IRStreamFactory::can_handle(&f, &effect).unwrap(),
                "ResultSafe handler should claim ResultSafeEffect"
            );
        });
    }

    #[test]
    fn test_result_safe_handler_wraps_return_and_exception() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let k = make_test_continuation();

            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item(
                    "ResultSafeEffect",
                    py.get_type::<crate::effect::PyResultSafeEffect>(),
                )
                .unwrap();
            py.run(
                c"obj = ResultSafeEffect(None)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));

            let ok_program = ResultSafeHandlerFactory.create_program();
            let start_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.start(py, effect.clone(), k.clone(), &mut store, &mut scope)
            };
            assert!(matches!(
                start_step,
                IRStreamStep::Yield(DoCtrl::EvalInScope { .. })
            ));

            let ok_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.resume(Value::Int(42), &mut store, &mut scope)
            };
            match ok_step {
                IRStreamStep::Yield(DoCtrl::Resume {
                    value: Value::Python(obj),
                    ..
                }) => {
                    let bound = obj.bind(py);
                    let is_ok: bool = bound.call_method0("is_ok").unwrap().extract().unwrap();
                    let inner = bound.getattr("value").unwrap();
                    assert!(is_ok);
                    assert_eq!(inner.extract::<i64>().unwrap(), 42);
                }
                _ => panic!("expected Resume with Ok(value)"),
            }

            let err_program = ResultSafeHandlerFactory.create_program();
            let _ = {
                let mut guard = err_program.lock().unwrap();
                guard.start(py, effect, k, &mut store, &mut scope)
            };

            let err_step = {
                let mut guard = err_program.lock().unwrap();
                guard.throw(PyException::runtime_error("boom"), &mut store, &mut scope)
            };

            match err_step {
                IRStreamStep::Yield(DoCtrl::Resume {
                    value: Value::Python(obj),
                    ..
                }) => {
                    let bound = obj.bind(py);
                    let is_err: bool = bound.call_method0("is_err").unwrap().extract().unwrap();
                    let error = bound.getattr("error").unwrap();
                    let msg = error.str().unwrap().to_str().unwrap().to_string();
                    assert!(is_err);
                    assert!(msg.contains("boom"));
                }
                _ => panic!("expected Resume with Err(exception)"),
            }
        });
    }

    /// G5/G6 TDD: DoubleCallHandlerProgram requires TWO NeedsPython round-trips.
    /// start() returns NeedsPython, first resume() returns NeedsPython again,
    /// second resume() yields Resume with combined result.
    /// This test verifies the handler protocol at the program level.
    #[test]
    fn test_double_call_handler_protocol() {
        use pyo3::Python;
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let k = make_test_continuation();
            let modifier = py.None().into_pyobject(py).unwrap().unbind().into_any();

            let program_ref = DoubleCallHandlerFactory.create_program();

            // Step 1: start() returns NeedsPython
            let step1 = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(
                    py,
                    Effect::Modify {
                        key: "key".to_string(),
                        modifier: PyShared::new(modifier),
                    },
                    k,
                    &mut store,
                    &mut scope,
                )
            };
            assert!(matches!(
                step1,
                IRStreamStep::NeedsPython(PythonCall::CallFunc { .. })
            ));

            // Step 2: first resume() returns NeedsPython AGAIN (the critical path)
            let step2 = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(100), &mut store, &mut scope)
            };
            assert!(
                matches!(
                    step2,
                    IRStreamStep::NeedsPython(PythonCall::CallFunc { .. })
                ),
                "Expected NeedsPython from resume(), got something else"
            );

            // Step 3: second resume() yields Resume with combined result
            let step3 = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(200), &mut store, &mut scope)
            };
            match step3 {
                IRStreamStep::Yield(DoCtrl::Resume { value, .. }) => {
                    // 100 + 200 = 300
                    assert_eq!(value.as_int(), Some(300));
                }
                _ => panic!("Expected Yield(Resume) with combined value 300"),
            }
        });
    }
}

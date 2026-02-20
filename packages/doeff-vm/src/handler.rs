//! Handler types for effect handling.
//!
//! Important: even Rust-implemented handlers in this module are user-space
//! handler implementations. They are dispatched by the VM, not part of VM core
//! stepping semantics.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

use crate::ast_stream::{ASTStream, ASTStreamStep, StreamLocation};
use crate::continuation::Continuation;
use crate::do_ctrl::CallArg;
use crate::doeff_generator::DoeffGeneratorFn;
#[cfg(test)]
use crate::effect::Effect;
use crate::effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, dispatch_to_pyobject,
    DispatchEffect, PyAsk, PyGet, PyLocal, PyModify, PyPut, PyPythonAsyncioAwaitEffect,
    PyResultSafeEffect, PyTell,
};
use crate::frame::CallMetadata;
use crate::ids::SegmentId;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyK, PyResultErr, PyResultOk};
use crate::step::{DoCtrl, PyException, PythonCall};
use crate::value::Value;
use crate::vm::RustStore;

/// Result of stepping a Rust handler program.
pub enum RustProgramStep {
    /// Yield a control primitive / effect / program
    Yield(DoCtrl),
    /// Return a value (like generator return)
    Return(Value),
    /// Throw an exception into the VM
    Throw(PyException),
    /// Need to call a Python function (e.g., Modify calling modifier).
    /// The program is suspended; result feeds back via resume().
    NeedsPython(PythonCall),
}

fn rust_program_step_to_ast_stream_step(step: RustProgramStep) -> ASTStreamStep {
    match step {
        RustProgramStep::Yield(ctrl) => ASTStreamStep::Yield(ctrl),
        RustProgramStep::Return(value) => ASTStreamStep::Return(value),
        RustProgramStep::Throw(exc) => ASTStreamStep::Throw(exc),
        RustProgramStep::NeedsPython(call) => ASTStreamStep::NeedsPython(call),
    }
}

/// A Rust handler program instance (generator-like).
/// start/resume/throw mirror Python generator protocol but run in Rust.
pub trait RustHandlerProgram: std::fmt::Debug + Send {
    fn start(
        &mut self,
        py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

/// Factory for Rust handler programs. Each dispatch creates a fresh instance.
pub trait RustProgramHandler: std::fmt::Debug + Send + Sync {
    fn can_handle(&self, effect: &DispatchEffect) -> bool;
    fn create_program(&self) -> RustProgramRef;
    fn handler_name(&self) -> &'static str {
        std::any::type_name::<Self>()
    }

    /// Create a handler program for a specific VM run token.
    ///
    /// Handlers that keep per-run state (for example, scheduler internals)
    /// can override this to isolate state between distinct top-level runs.
    fn create_program_for_run(&self, _run_token: Option<u64>) -> RustProgramRef {
        self.create_program()
    }

    /// Notification that a top-level VM run has completed.
    ///
    /// Default is no-op. Stateful handlers can override this to release
    /// run-scoped resources.
    fn on_run_end(&self, _run_token: u64) {}
}

pub type DoExpr = DoCtrl;

#[derive(Debug, Clone)]
pub struct HandlerDebugInfo {
    pub name: String,
    pub file: Option<String>,
    pub line: Option<u32>,
}

pub trait HandlerInvoke: std::fmt::Debug + Send + Sync {
    fn can_handle(&self, effect: &DispatchEffect) -> bool;
    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr;
    fn handler_name(&self) -> &str;
    fn handler_debug_info(&self) -> HandlerDebugInfo;

    fn py_identity(&self) -> Option<PyShared> {
        None
    }

    fn on_run_end(&self, _run_token: u64) {}
}

pub type HandlerRef = Arc<dyn HandlerInvoke>;
pub type Handler = HandlerRef;

/// Shared reference to a Rust program handler factory.
pub type RustProgramHandlerRef = Arc<dyn RustProgramHandler + Send + Sync>;

/// Shared reference to a running Rust handler program (cloneable for continuations).
pub type RustProgramRef = Arc<Mutex<Box<dyn RustHandlerProgram + Send>>>;

#[derive(Debug, Clone)]
pub struct HandlerEntry {
    pub handler: HandlerRef,
    pub prompt_seg_id: SegmentId,
    pub py_identity: Option<PyShared>,
}

impl HandlerEntry {
    pub fn new(handler: HandlerRef, prompt_seg_id: SegmentId) -> Self {
        HandlerEntry {
            handler,
            prompt_seg_id,
            py_identity: None,
        }
    }

    pub fn with_identity(
        handler: HandlerRef,
        prompt_seg_id: SegmentId,
        py_identity: PyShared,
    ) -> Self {
        HandlerEntry {
            handler,
            prompt_seg_id,
            py_identity: Some(py_identity),
        }
    }
}

#[derive(Debug, Clone)]
pub struct RustProgramInvocation {
    pub factory: RustProgramHandlerRef,
    pub effect: Box<DispatchEffect>,
    pub continuation: Continuation,
}

fn metadata_from_debug_info(debug: HandlerDebugInfo) -> CallMetadata {
    CallMetadata::new(
        debug.name,
        debug.file.unwrap_or_else(|| "<unknown>".to_string()),
        debug.line.unwrap_or(0),
        None,
        None,
    )
}

fn rust_program_apply_expr(
    factory: RustProgramHandlerRef,
    effect: DispatchEffect,
    continuation: Continuation,
    metadata: CallMetadata,
) -> DoExpr {
    DoCtrl::Apply {
        f: CallArg::Value(Value::RustProgramInvocation(RustProgramInvocation {
            factory,
            effect: Box::new(effect),
            continuation,
        })),
        args: vec![],
        kwargs: vec![],
        metadata,
    }
}

fn rust_program_expand_expr(
    factory: RustProgramHandlerRef,
    effect: DispatchEffect,
    continuation: Continuation,
    metadata: CallMetadata,
) -> DoExpr {
    DoCtrl::Expand {
        factory: CallArg::Value(Value::RustProgramInvocation(RustProgramInvocation {
            factory,
            effect: Box::new(effect),
            continuation,
        })),
        args: vec![],
        kwargs: vec![],
        metadata,
    }
}

#[derive(Debug, Clone)]
pub struct PythonHandler {
    pub dgfn: Py<DoeffGeneratorFn>,
    handler_name: String,
    handler_file: Option<String>,
    handler_line: Option<u32>,
}

impl PythonHandler {
    pub fn from_dgfn(dgfn: Py<DoeffGeneratorFn>) -> Self {
        Python::attach(|py| {
            let borrowed = dgfn.bind(py).borrow();
            PythonHandler {
                dgfn,
                handler_name: borrowed.function_name.clone(),
                handler_file: Some(borrowed.source_file.clone()),
                handler_line: Some(borrowed.source_line),
            }
        })
    }
}

impl HandlerInvoke for PythonHandler {
    fn can_handle(&self, _effect: &DispatchEffect) -> bool {
        true
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        let py_effect = match Python::attach(|py| dispatch_to_pyobject(py, &effect).map(|obj| obj.unbind()))
        {
            Ok(obj) => obj,
            Err(err) => {
                return DoCtrl::TransferThrow {
                    continuation: k,
                    exception: PyException::type_error(format!(
                        "PythonHandler invoke failed to convert effect: {err}"
                    )),
                };
            }
        };

        let py_k = match Python::attach(|py| {
            Bound::new(py, PyK::from_cont_id(k.cont_id)).map(|bound| bound.into_any().unbind())
        }) {
            Ok(obj) => obj,
            Err(err) => {
                return DoCtrl::TransferThrow {
                    continuation: k,
                    exception: PyException::type_error(format!(
                        "PythonHandler invoke failed to create continuation handle: {err}"
                    )),
                };
            }
        };

        let metadata = metadata_from_debug_info(self.handler_debug_info());
        DoCtrl::Expand {
            factory: CallArg::Value(Value::PythonHandlerCallable(Python::attach(|py| {
                self.dgfn.clone_ref(py).into_any()
            }))),
            args: vec![
                CallArg::Value(Value::Python(py_effect)),
                CallArg::Value(Value::Python(py_k)),
            ],
            kwargs: vec![],
            metadata,
        }
    }

    fn handler_name(&self) -> &str {
        self.handler_name.as_str()
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: self.handler_name.clone(),
            file: self.handler_file.clone(),
            line: self.handler_line,
        }
    }

    fn py_identity(&self) -> Option<PyShared> {
        Some(PyShared::new(Python::attach(|py| {
            let borrowed = self.dgfn.bind(py).borrow();
            borrowed.callable.clone_ref(py)
        })))
    }
}

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
            overrides.insert(key, Value::from_pyobject(&value));
        }

        let sub_program = obj.getattr("sub_program").map_err(|e| e.to_string())?;

        Ok(Some(ParsedLocalEffect {
            overrides,
            sub_program: PyShared::new(sub_program.unbind()),
        }))
    })
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
    let exc = PyException::new(exc_type, exc_value, exc_tb);
    crate::scheduler::preserve_exception_origin(&exc);
    exc
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
        let semaphore_module = py
            .import("doeff.effects.semaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let create = semaphore_module
            .getattr("CreateSemaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let effect = create
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
        let semaphore_module = py
            .import("doeff.effects.semaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let acquire = semaphore_module
            .getattr("AcquireSemaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let effect = acquire
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
        let semaphore_module = py
            .import("doeff.effects.semaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let release = semaphore_module
            .getattr("ReleaseSemaphore")
            .map_err(|e| pyerr_to_exception(py, e))?;
        let effect = release
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

fn get_sync_await_runner() -> Result<PyShared, String> {
    Python::attach(|py| {
        let module = PyModule::from_code(
            py,
            c"import asyncio\n\ndef _run_awaitable_sync(awaitable):\n    return asyncio.run(awaitable)\n",
            c"_doeff_await_bridge",
            c"_doeff_await_bridge",
        )
        .map_err(|e| e.to_string())?;
        let runner = module
            .getattr("_run_awaitable_sync")
            .map_err(|e| e.to_string())?;
        Ok(PyShared::new(runner.unbind()))
    })
}

// ---------------------------------------------------------------------------
// AwaitHandlerFactory + AwaitHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct AwaitHandlerFactory;

impl RustProgramHandler for AwaitHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_await_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(AwaitHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "AwaitHandler"
    }
}

impl HandlerInvoke for AwaitHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        rust_program_expand_expr(
            Arc::new(self.clone()),
            effect,
            k,
            metadata_from_debug_info(self.handler_debug_info()),
        )
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
struct AwaitHandlerProgram {
    pending_k: Option<Continuation>,
}

impl AwaitHandlerProgram {
    fn new() -> Self {
        AwaitHandlerProgram { pending_k: None }
    }

    fn current_phase_name(&self) -> &'static str {
        if self.pending_k.is_some() {
            "AwaitBridgeResult"
        } else {
            "Idle"
        }
    }
}

impl RustHandlerProgram for AwaitHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_await_python_effect(&obj) {
                Ok(Some(awaitable)) => {
                    let runner = match get_sync_await_runner() {
                        Ok(func) => func,
                        Err(msg) => {
                            return RustProgramStep::Throw(PyException::type_error(format!(
                                "failed to initialize await runner: {msg}"
                            )));
                        }
                    };
                    self.pending_k = Some(k);
                    RustProgramStep::NeedsPython(PythonCall::CallFunc {
                        func: runner,
                        args: vec![Value::Python(awaitable)],
                        kwargs: vec![],
                    })
                }
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse await effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        if let Some(continuation) = self.pending_k.take() {
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            });
        }
        RustProgramStep::Return(value)
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        if let Some(continuation) = self.pending_k.take() {
            return RustProgramStep::Yield(DoCtrl::TransferThrow {
                continuation,
                exception: exc,
            });
        }
        RustProgramStep::Throw(exc)
    }
}

impl ASTStream for AwaitHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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

impl RustProgramHandler for StateHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        #[cfg(test)]
        if matches!(
            effect,
            Effect::Get { .. } | Effect::Put { .. } | Effect::Modify { .. }
        ) {
            return true;
        }

        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_state_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(StateHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "StateHandler"
    }
}

impl HandlerInvoke for StateHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        let is_modify = {
            #[cfg(test)]
            if matches!(&effect, Effect::Modify { .. }) {
                true
            } else {
                dispatch_ref_as_python(&effect).is_some_and(|obj| {
                    matches!(
                        parse_state_python_effect(obj),
                        Ok(Some(ParsedStateEffect::Modify { .. }))
                    )
                })
            }
            #[cfg(not(test))]
            {
                dispatch_ref_as_python(&effect).is_some_and(|obj| {
                    matches!(
                        parse_state_python_effect(obj),
                        Ok(Some(ParsedStateEffect::Modify { .. }))
                    )
                })
            }
        };

        let metadata = metadata_from_debug_info(self.handler_debug_info());
        if is_modify {
            rust_program_expand_expr(Arc::new(self.clone()), effect, k, metadata)
        } else {
            rust_program_apply_expr(Arc::new(self.clone()), effect, k, metadata)
        }
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
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

impl RustHandlerProgram for StateHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        #[cfg(test)]
        if let Effect::Get { key } = effect.clone() {
            let Some(value) = store.get(&key).cloned() else {
                return RustProgramStep::Throw(missing_state_key_error(&key));
            };
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation: k,
                value,
            });
        }

        #[cfg(test)]
        if let Effect::Put { key, value } = effect.clone() {
            store.put(key, value);
            return RustProgramStep::Yield(DoCtrl::Resume {
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
            return RustProgramStep::NeedsPython(PythonCall::CallFunc {
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
                            return RustProgramStep::Throw(missing_state_key_error(&key));
                        };
                        RustProgramStep::Yield(DoCtrl::Resume {
                            continuation: k,
                            value,
                        })
                    }
                    ParsedStateEffect::Put { key, value } => {
                        store.put(key, value);
                        RustProgramStep::Yield(DoCtrl::Resume {
                            continuation: k,
                            value: Value::Unit,
                        })
                    }
                    ParsedStateEffect::Modify { key, modifier } => {
                        let old_value = store.get(&key).cloned().unwrap_or(Value::None);
                        self.pending_key = Some(key);
                        self.pending_k = Some(k);
                        self.pending_old_value = Some(old_value.clone());
                        RustProgramStep::NeedsPython(PythonCall::CallFunc {
                            func: modifier,
                            args: vec![old_value],
                            kwargs: vec![],
                        })
                    }
                },
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse state effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep {
        if self.pending_key.is_none() {
            // Terminal case (Get/Put): handler is done, pass through return value
            return RustProgramStep::Return(value);
        }
        // Modify case: store modifier result but resume caller with OLD value.
        // SPEC-008 L1271: Modify is read-then-modify, returns the old value.
        let key = self.pending_key.take().unwrap();
        let continuation = self.pending_k.take().unwrap();
        let old_value = self.pending_old_value.take().unwrap();
        store.put(key, value);
        RustProgramStep::Yield(DoCtrl::Resume {
            continuation,
            value: old_value,
        })
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

impl ASTStream for StateHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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

#[derive(Debug, Default)]
struct LazyAskState {
    cache: HashMap<HashedPyKey, LazyCacheEntry>,
    semaphores: HashMap<HashedPyKey, LazySemaphoreEntry>,
    scope_cache: Vec<HashMap<HashedPyKey, LazyCacheEntry>>,
    scope_semaphores: Vec<HashMap<HashedPyKey, LazySemaphoreEntry>>,
}

#[derive(Clone)]
pub struct LazyAskHandlerFactory {
    default_state: Arc<Mutex<LazyAskState>>,
    run_states: Arc<Mutex<HashMap<u64, Arc<Mutex<LazyAskState>>>>>,
}

impl std::fmt::Debug for LazyAskHandlerFactory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LazyAskHandlerFactory").finish()
    }
}

impl LazyAskHandlerFactory {
    pub fn new() -> Self {
        LazyAskHandlerFactory {
            default_state: Arc::new(Mutex::new(LazyAskState::default())),
            run_states: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn state_for_run(&self, run_token: Option<u64>) -> Arc<Mutex<LazyAskState>> {
        match run_token {
            Some(token) => {
                let mut states = self.run_states.lock().expect("LazyAsk lock poisoned");
                states
                    .entry(token)
                    .or_insert_with(|| Arc::new(Mutex::new(LazyAskState::default())))
                    .clone()
            }
            None => self.default_state.clone(),
        }
    }
}

impl Default for LazyAskHandlerFactory {
    fn default() -> Self {
        Self::new()
    }
}

impl RustProgramHandler for LazyAskHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        #[cfg(test)]
        if matches!(effect, Effect::Ask { .. }) {
            return true;
        }

        dispatch_ref_as_python(effect).is_some_and(|obj| {
            parse_reader_python_effect(obj).ok().flatten().is_some() || is_local_python_effect(obj)
        })
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(LazyAskHandlerProgram::new(
            self.state_for_run(None),
        ))))
    }

    fn create_program_for_run(&self, run_token: Option<u64>) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(LazyAskHandlerProgram::new(
            self.state_for_run(run_token),
        ))))
    }

    fn handler_name(&self) -> &'static str {
        "LazyAskHandler"
    }

    fn on_run_end(&self, run_token: u64) {
        let mut states = self.run_states.lock().expect("LazyAsk lock poisoned");
        states.remove(&run_token);
    }
}

impl HandlerInvoke for LazyAskHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        let is_direct_ask = {
            #[cfg(test)]
            if matches!(&effect, Effect::Ask { .. }) {
                true
            } else {
                dispatch_ref_as_python(&effect)
                    .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
            }
            #[cfg(not(test))]
            {
                dispatch_ref_as_python(&effect)
                    .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
            }
        };

        let metadata = metadata_from_debug_info(self.handler_debug_info());
        if is_direct_ask {
            rust_program_apply_expr(Arc::new(self.clone()), effect, k, metadata)
        } else {
            rust_program_expand_expr(Arc::new(self.clone()), effect, k, metadata)
        }
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }

    fn on_run_end(&self, run_token: u64) {
        <Self as RustProgramHandler>::on_run_end(self, run_token);
    }
}

#[derive(Debug, Default)]
struct LazyLocalScopeState {
    cache: HashMap<HashedPyKey, LazyCacheEntry>,
    semaphores: HashMap<HashedPyKey, LazySemaphoreEntry>,
}

#[derive(Debug, Clone)]
struct LazyLocalScopeFactory {
    overrides: Arc<HashMap<HashedPyKey, Value>>,
    scope_state: Arc<Mutex<LazyLocalScopeState>>,
}

impl LazyLocalScopeFactory {
    fn new(overrides: HashMap<HashedPyKey, Value>) -> Self {
        LazyLocalScopeFactory {
            overrides: Arc::new(overrides),
            scope_state: Arc::new(Mutex::new(LazyLocalScopeState::default())),
        }
    }
}

impl RustProgramHandler for LazyLocalScopeFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        #[cfg(test)]
        if matches!(effect, Effect::Ask { .. }) {
            return true;
        }

        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(LazyLocalScopeProgram::new(
            self.overrides.clone(),
            self.scope_state.clone(),
        ))))
    }

    fn handler_name(&self) -> &'static str {
        "LazyLocalScopeHandler"
    }
}

impl HandlerInvoke for LazyLocalScopeFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        rust_program_expand_expr(
            Arc::new(self.clone()),
            effect,
            k,
            metadata_from_debug_info(self.handler_debug_info()),
        )
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
enum LazyLocalScopePhase {
    Idle,
    AwaitAcquire {
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Option<Value>,
    },
    AwaitCache {
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Value,
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
struct LazyLocalScopeProgram {
    phase: LazyLocalScopePhase,
    overrides: Arc<HashMap<HashedPyKey, Value>>,
    scope_state: Arc<Mutex<LazyLocalScopeState>>,
}

impl LazyLocalScopeProgram {
    fn new(
        overrides: Arc<HashMap<HashedPyKey, Value>>,
        scope_state: Arc<Mutex<LazyLocalScopeState>>,
    ) -> Self {
        LazyLocalScopeProgram {
            phase: LazyLocalScopePhase::Idle,
            overrides,
            scope_state,
        }
    }

    fn yield_perform(effect: Result<DispatchEffect, PyException>) -> RustProgramStep {
        match effect {
            Ok(effect) => RustProgramStep::Yield(DoCtrl::Perform { effect }),
            Err(exc) => RustProgramStep::Throw(exc),
        }
    }

    fn transfer_throw(continuation: Continuation, exception: PyException) -> RustProgramStep {
        RustProgramStep::Yield(DoCtrl::TransferThrow {
            continuation,
            exception,
        })
    }

    fn local_cache_get(&self, key: &HashedPyKey, source_id: usize) -> Option<Value> {
        let state = self
            .scope_state
            .lock()
            .expect("LazyLocalScope lock poisoned");
        let entry = state.cache.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.value.clone());
        }
        None
    }

    fn local_cache_put(&self, key: HashedPyKey, source_id: usize, value: Value) {
        let mut state = self
            .scope_state
            .lock()
            .expect("LazyLocalScope lock poisoned");
        state.cache.insert(key, LazyCacheEntry { source_id, value });
    }

    fn local_semaphore_get(&self, key: &HashedPyKey, source_id: usize) -> Option<Value> {
        let state = self
            .scope_state
            .lock()
            .expect("LazyLocalScope lock poisoned");
        let entry = state.semaphores.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.semaphore.clone());
        }
        None
    }

    fn local_semaphore_put(&self, key: HashedPyKey, source_id: usize, semaphore: Value) {
        let mut state = self
            .scope_state
            .lock()
            .expect("LazyLocalScope lock poisoned");
        state.semaphores.insert(
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
    ) -> RustProgramStep {
        self.phase = LazyLocalScopePhase::AwaitAcquire {
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
    ) -> RustProgramStep {
        let effect = lazy_ask_acquire_semaphore_effect(&semaphore);
        self.phase = LazyLocalScopePhase::AwaitAcquire {
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
    ) -> RustProgramStep {
        self.phase = LazyLocalScopePhase::AwaitRelease {
            continuation,
            outcome,
        };
        Self::yield_perform(lazy_ask_release_semaphore_effect(&semaphore))
    }

    fn handle_override_ask(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        value: Value,
    ) -> RustProgramStep {
        let Some(expr) = as_lazy_eval_expr(&value) else {
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            });
        };

        let source_id = lazy_source_id(&value).unwrap_or_default();

        if let Some(cached) = self.local_cache_get(&key, source_id) {
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value: cached,
            });
        }

        if let Some(semaphore) = self.local_semaphore_get(&key, source_id) {
            return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
        }

        self.begin_create_then_acquire_phase(key, continuation, expr, source_id)
    }

    fn handle_ask(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        effect: DispatchEffect,
    ) -> RustProgramStep {
        let Some(value) = self.overrides.get(&key).cloned() else {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        };

        self.handle_override_ask(key, continuation, value)
    }
}

impl RustHandlerProgram for LazyLocalScopeProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        #[cfg(test)]
        if let Effect::Ask { key } = effect.clone() {
            let key = HashedPyKey::from_test_string(key);
            return self.handle_ask(key, k, effect);
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => self.handle_ask(key, k, dispatch_from_shared(obj)),
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse lazy local Ask effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, LazyLocalScopePhase::Idle) {
            LazyLocalScopePhase::AwaitAcquire {
                key,
                continuation,
                expr,
                source_id,
                semaphore,
            } => {
                let Some(semaphore) = semaphore else {
                    let semaphore = match value {
                        Value::Python(_) => value,
                        _ => {
                            return RustProgramStep::Throw(PyException::type_error(
                                "CreateSemaphore must return a semaphore handle".to_string(),
                            ));
                        }
                    };
                    self.local_semaphore_put(key.clone(), source_id, semaphore.clone());
                    return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
                };

                if let Some(cached) = self.local_cache_get(&key, source_id) {
                    return self.begin_release_phase(continuation, Ok(cached), semaphore);
                }

                self.phase = LazyLocalScopePhase::AwaitCache {
                    key,
                    continuation,
                    expr,
                    source_id,
                    semaphore,
                };
                RustProgramStep::Yield(DoCtrl::GetHandlers)
            }
            LazyLocalScopePhase::AwaitCache {
                key,
                continuation,
                expr,
                source_id,
                semaphore,
            } => {
                let handlers = match value {
                    Value::Handlers(handlers) => handlers,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "lazy local scope expected handlers from GetHandlers".to_string(),
                        ));
                    }
                };

                self.phase = LazyLocalScopePhase::AwaitEval {
                    key,
                    continuation,
                    source_id,
                    semaphore,
                };
                RustProgramStep::Yield(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                })
            }
            LazyLocalScopePhase::AwaitEval {
                key,
                continuation,
                source_id,
                semaphore,
            } => {
                self.local_cache_put(key, source_id, value.clone());
                self.begin_release_phase(continuation, Ok(value), semaphore)
            }
            LazyLocalScopePhase::AwaitRelease {
                continuation,
                outcome,
            } => match outcome {
                Ok(value) => RustProgramStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                }),
                Err(exception) => Self::transfer_throw(continuation, exception),
            },
            LazyLocalScopePhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, LazyLocalScopePhase::Idle) {
            LazyLocalScopePhase::AwaitAcquire { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyLocalScopePhase::AwaitCache {
                continuation,
                semaphore,
                ..
            } => self.begin_release_phase(continuation, Err(exc), semaphore),
            LazyLocalScopePhase::AwaitEval {
                continuation,
                semaphore,
                ..
            } => self.begin_release_phase(continuation, Err(exc), semaphore),
            LazyLocalScopePhase::AwaitRelease { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyLocalScopePhase::Idle => RustProgramStep::Throw(exc),
        }
    }
}

#[derive(Debug)]
enum LazyAskPhase {
    Idle,
    AwaitLocalHandlers {
        continuation: Continuation,
        scope: LazyLocalScopeFactory,
        sub_program: PyShared,
    },
    AwaitLocalEval {
        continuation: Continuation,
    },
    AwaitDelegate {
        key: HashedPyKey,
        continuation: Continuation,
    },
    AwaitAcquire {
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Option<Value>,
    },
    AwaitCache {
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
        semaphore: Value,
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
    state: Arc<Mutex<LazyAskState>>,
}

impl LazyAskHandlerProgram {
    fn new(state: Arc<Mutex<LazyAskState>>) -> Self {
        LazyAskHandlerProgram {
            phase: LazyAskPhase::Idle,
            state,
        }
    }

    fn current_phase_name(&self) -> &'static str {
        match self.phase {
            LazyAskPhase::Idle => "Idle",
            LazyAskPhase::AwaitLocalHandlers { .. } => "AwaitLocalHandlers",
            LazyAskPhase::AwaitLocalEval { .. } => "AwaitLocalEval",
            LazyAskPhase::AwaitDelegate { .. } => "AwaitDelegate",
            LazyAskPhase::AwaitAcquire { .. } => "AwaitAcquire",
            LazyAskPhase::AwaitCache { .. } => "AwaitCache",
            LazyAskPhase::AwaitEval { .. } => "AwaitEval",
            LazyAskPhase::AwaitRelease { .. } => "AwaitRelease",
        }
    }

    fn yield_perform(effect: Result<DispatchEffect, PyException>) -> RustProgramStep {
        match effect {
            Ok(effect) => RustProgramStep::Yield(DoCtrl::Perform { effect }),
            Err(exc) => RustProgramStep::Throw(exc),
        }
    }

    fn transfer_throw(continuation: Continuation, exception: PyException) -> RustProgramStep {
        RustProgramStep::Yield(DoCtrl::TransferThrow {
            continuation,
            exception,
        })
    }

    fn activate_scope_cache(&self) {
        let mut state = self.state.lock().expect("LazyAsk lock poisoned");
        state.scope_cache.push(HashMap::new());
        state.scope_semaphores.push(HashMap::new());
    }

    fn deactivate_scope_cache(&self) {
        let mut state = self.state.lock().expect("LazyAsk lock poisoned");
        state.scope_cache.pop();
        state.scope_semaphores.pop();
    }

    fn lazy_cache_get(&self, key: &HashedPyKey, source_id: usize) -> Option<Value> {
        let state = self.state.lock().expect("LazyAsk lock poisoned");
        if let Some(scope_cache) = state.scope_cache.last() {
            if let Some(entry) = scope_cache.get(key) {
                if entry.source_id == source_id {
                    return Some(entry.value.clone());
                }
            }
        }
        let entry = state.cache.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.value.clone());
        }
        None
    }

    fn lazy_cache_put(&self, key: HashedPyKey, source_id: usize, value: Value) {
        let mut state = self.state.lock().expect("LazyAsk lock poisoned");
        let entry = LazyCacheEntry { source_id, value };
        if let Some(scope_cache) = state.scope_cache.last_mut() {
            scope_cache.insert(key, entry);
        } else {
            state.cache.insert(key, entry);
        }
    }

    fn lazy_semaphore_get(&self, key: &HashedPyKey, source_id: usize) -> Option<Value> {
        let state = self.state.lock().expect("LazyAsk lock poisoned");
        if let Some(scope_semaphores) = state.scope_semaphores.last() {
            if let Some(entry) = scope_semaphores.get(key) {
                if entry.source_id == source_id {
                    return Some(entry.semaphore.clone());
                }
            }
        }
        let entry = state.semaphores.get(key)?;
        if entry.source_id == source_id {
            return Some(entry.semaphore.clone());
        }
        None
    }

    fn lazy_semaphore_put(&self, key: HashedPyKey, source_id: usize, semaphore: Value) {
        let mut state = self.state.lock().expect("LazyAsk lock poisoned");
        let entry = LazySemaphoreEntry {
            source_id,
            semaphore,
        };
        if let Some(scope_semaphores) = state.scope_semaphores.last_mut() {
            scope_semaphores.insert(key, entry);
        } else {
            state.semaphores.insert(key, entry);
        }
    }

    fn begin_delegate_phase(
        &mut self,
        effect: DispatchEffect,
        key: HashedPyKey,
        continuation: Continuation,
    ) -> RustProgramStep {
        self.phase = LazyAskPhase::AwaitDelegate { key, continuation };
        RustProgramStep::Yield(DoCtrl::Perform { effect })
    }

    fn begin_create_then_acquire_phase(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
    ) -> RustProgramStep {
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
    ) -> RustProgramStep {
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
    ) -> RustProgramStep {
        self.phase = LazyAskPhase::AwaitRelease {
            continuation,
            outcome,
        };
        Self::yield_perform(lazy_ask_release_semaphore_effect(&semaphore))
    }

    fn handle_delegated_ask_value(
        &mut self,
        key: HashedPyKey,
        continuation: Continuation,
        value: Value,
    ) -> RustProgramStep {
        let Some(expr) = as_lazy_eval_expr(&value) else {
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            });
        };

        let source_id = lazy_source_id(&value).unwrap_or_default();

        if let Some(cached) = self.lazy_cache_get(&key, source_id) {
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value: cached,
            });
        }

        if let Some(semaphore) = self.lazy_semaphore_get(&key, source_id) {
            return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
        }

        self.begin_create_then_acquire_phase(key, continuation, expr, source_id)
    }
}

impl RustHandlerProgram for LazyAskHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        #[cfg(test)]
        if let Effect::Ask { key } = effect.clone() {
            let hashed_key = HashedPyKey::from_test_string(key);
            let Some(value) = _store.ask(&hashed_key).cloned() else {
                return RustProgramStep::Throw(missing_env_key_error(&hashed_key));
            };
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation: k,
                value,
            });
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            match parse_local_python_effect(&obj) {
                Ok(Some(local_effect)) => {
                    let scope = LazyLocalScopeFactory::new(local_effect.overrides);
                    self.activate_scope_cache();
                    self.phase = LazyAskPhase::AwaitLocalHandlers {
                        continuation: k,
                        scope,
                        sub_program: local_effect.sub_program,
                    };
                    return RustProgramStep::Yield(DoCtrl::GetHandlers);
                }
                Ok(None) => {}
                Err(msg) => {
                    return RustProgramStep::Throw(PyException::type_error(format!(
                        "failed to parse Local effect: {msg}"
                    )));
                }
            }

            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => self.begin_delegate_phase(dispatch_from_shared(obj), key, k),
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse lazy Ask effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, LazyAskPhase::Idle) {
            LazyAskPhase::AwaitLocalHandlers {
                continuation,
                scope,
                sub_program,
            } => {
                let mut handlers = match value {
                    Value::Handlers(handlers) => handlers,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "lazy Ask Local expected handlers from GetHandlers".to_string(),
                        ));
                    }
                };
                handlers.insert(0, Arc::new(scope.clone()));
                self.phase = LazyAskPhase::AwaitLocalEval { continuation };
                RustProgramStep::Yield(DoCtrl::Eval {
                    expr: sub_program,
                    handlers,
                    metadata: None,
                })
            }
            LazyAskPhase::AwaitLocalEval { continuation } => {
                self.deactivate_scope_cache();
                RustProgramStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                })
            }
            LazyAskPhase::AwaitDelegate { key, continuation } => {
                self.handle_delegated_ask_value(key, continuation, value)
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
                        _ => {
                            return RustProgramStep::Throw(PyException::type_error(
                                "CreateSemaphore must return a semaphore handle".to_string(),
                            ));
                        }
                    };
                    self.lazy_semaphore_put(key.clone(), source_id, semaphore.clone());
                    return self.begin_acquire_phase(key, continuation, expr, source_id, semaphore);
                };

                if let Some(cached) = self.lazy_cache_get(&key, source_id) {
                    return self.begin_release_phase(continuation, Ok(cached), semaphore);
                }

                self.phase = LazyAskPhase::AwaitCache {
                    key,
                    continuation,
                    expr,
                    source_id,
                    semaphore,
                };
                RustProgramStep::Yield(DoCtrl::GetHandlers)
            }
            LazyAskPhase::AwaitCache {
                key,
                continuation,
                expr,
                source_id,
                semaphore,
            } => {
                let handlers = match value {
                    Value::Handlers(handlers) => handlers,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "lazy Ask expected handlers from GetHandlers".to_string(),
                        ));
                    }
                };

                self.phase = LazyAskPhase::AwaitEval {
                    key,
                    continuation,
                    source_id,
                    semaphore,
                };
                RustProgramStep::Yield(DoCtrl::Eval {
                    expr,
                    handlers,
                    metadata: None,
                })
            }
            LazyAskPhase::AwaitEval {
                key,
                continuation,
                source_id,
                semaphore,
            } => {
                self.lazy_cache_put(key, source_id, value.clone());
                self.begin_release_phase(continuation, Ok(value), semaphore)
            }
            LazyAskPhase::AwaitRelease {
                continuation,
                outcome,
            } => match outcome {
                Ok(value) => RustProgramStep::Yield(DoCtrl::Resume {
                    continuation,
                    value,
                }),
                Err(exception) => Self::transfer_throw(continuation, exception),
            },
            LazyAskPhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, LazyAskPhase::Idle) {
            LazyAskPhase::AwaitLocalHandlers { continuation, .. }
            | LazyAskPhase::AwaitLocalEval { continuation } => {
                self.deactivate_scope_cache();
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitDelegate { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitAcquire { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::AwaitCache {
                continuation,
                semaphore,
                ..
            } => self.begin_release_phase(continuation, Err(exc), semaphore),
            LazyAskPhase::AwaitEval {
                continuation,
                semaphore,
                ..
            } => self.begin_release_phase(continuation, Err(exc), semaphore),
            LazyAskPhase::AwaitRelease { continuation, .. } => {
                Self::transfer_throw(continuation, exc)
            }
            LazyAskPhase::Idle => RustProgramStep::Throw(exc),
        }
    }
}

impl ASTStream for LazyAskHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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

impl RustProgramHandler for ReaderHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        #[cfg(test)]
        if matches!(effect, Effect::Ask { .. }) {
            return true;
        }

        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram)))
    }

    fn handler_name(&self) -> &'static str {
        "ReaderHandler"
    }
}

impl HandlerInvoke for ReaderHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        let is_ask = {
            #[cfg(test)]
            if matches!(&effect, Effect::Ask { .. }) {
                true
            } else {
                dispatch_ref_as_python(&effect)
                    .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
            }
            #[cfg(not(test))]
            {
                dispatch_ref_as_python(&effect)
                    .is_some_and(|obj| parse_reader_python_effect(obj).ok().flatten().is_some())
            }
        };

        let metadata = metadata_from_debug_info(self.handler_debug_info());
        if is_ask {
            rust_program_apply_expr(Arc::new(self.clone()), effect, k, metadata)
        } else {
            rust_program_expand_expr(Arc::new(self.clone()), effect, k, metadata)
        }
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
struct ReaderHandlerProgram;

impl ReaderHandlerProgram {
    fn handle_ask(
        key: HashedPyKey,
        continuation: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        let Some(value) = store.ask(&key).cloned() else {
            return RustProgramStep::Throw(missing_env_key_error(&key));
        };

        RustProgramStep::Yield(DoCtrl::Resume {
            continuation,
            value,
        })
    }

    fn current_phase_name(&self) -> &'static str {
        "AskApply"
    }
}

impl RustHandlerProgram for ReaderHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        #[cfg(test)]
        if let Effect::Ask { key } = effect.clone() {
            return ReaderHandlerProgram::handle_ask(HashedPyKey::from_test_string(key), k, store);
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => ReaderHandlerProgram::handle_ask(key, k, store),
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse reader effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, _value: Value, _store: &mut RustStore) -> RustProgramStep {
        unreachable!("ReaderHandler never yields mid-handling")
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

impl ASTStream for ReaderHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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

impl RustProgramHandler for WriterHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        #[cfg(test)]
        if matches!(effect, Effect::Tell { .. }) {
            return true;
        }

        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_writer_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(WriterHandlerProgram)))
    }

    fn handler_name(&self) -> &'static str {
        "WriterHandler"
    }
}

impl HandlerInvoke for WriterHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        rust_program_apply_expr(
            Arc::new(self.clone()),
            effect,
            k,
            metadata_from_debug_info(self.handler_debug_info()),
        )
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
struct WriterHandlerProgram;

impl WriterHandlerProgram {
    fn current_phase_name(&self) -> &'static str {
        "TellApply"
    }
}

impl RustHandlerProgram for WriterHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        #[cfg(test)]
        if let Effect::Tell { message } = effect.clone() {
            store.tell(message);
            return RustProgramStep::Yield(DoCtrl::Resume {
                continuation: k,
                value: Value::Unit,
            });
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_writer_python_effect(&obj) {
                Ok(Some(message)) => {
                    store.tell(message);
                    RustProgramStep::Yield(DoCtrl::Resume {
                        continuation: k,
                        value: Value::Unit,
                    })
                }
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse writer effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, _value: Value, _: &mut RustStore) -> RustProgramStep {
        unreachable!("WriterHandler never yields mid-handling")
    }

    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

impl ASTStream for WriterHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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

impl RustProgramHandler for ResultSafeHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        dispatch_ref_as_python(effect).is_some_and(|obj| {
            parse_result_safe_python_effect(obj)
                .ok()
                .flatten()
                .is_some()
        })
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ResultSafeHandlerProgram::new())))
    }

    fn handler_name(&self) -> &'static str {
        "ResultSafeHandler"
    }
}

impl HandlerInvoke for ResultSafeHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        rust_program_expand_expr(
            Arc::new(self.clone()),
            effect,
            k,
            metadata_from_debug_info(self.handler_debug_info()),
        )
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

#[derive(Debug)]
enum ResultSafePhase {
    Idle,
    AwaitHandlers {
        continuation: Continuation,
        sub_program: PyShared,
    },
    AwaitEval {
        continuation: Continuation,
    },
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
            ResultSafePhase::AwaitHandlers { .. } => "AwaitHandlers",
            ResultSafePhase::AwaitEval { .. } => "AwaitEval",
        }
    }

    fn finish_ok(&self, continuation: Continuation, value: Value) -> RustProgramStep {
        match wrap_value_as_result_ok(value) {
            Ok(wrapped) => RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value: wrapped,
            }),
            Err(exc) => RustProgramStep::Throw(exc),
        }
    }

    fn finish_err(&self, continuation: Continuation, error: PyException) -> RustProgramStep {
        match wrap_exception_as_result_err(error) {
            Ok(wrapped) => RustProgramStep::Yield(DoCtrl::Resume {
                continuation,
                value: wrapped,
            }),
            Err(exc) => RustProgramStep::Throw(exc),
        }
    }
}

impl RustHandlerProgram for ResultSafeHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_result_safe_python_effect(&obj) {
                Ok(Some(sub_program)) => {
                    self.phase = ResultSafePhase::AwaitHandlers {
                        continuation: k,
                        sub_program,
                    };
                    RustProgramStep::Yield(DoCtrl::GetHandlers)
                }
                Ok(None) => RustProgramStep::Yield(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                }),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse ResultSafe effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(DoCtrl::Delegate { effect });
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, ResultSafePhase::Idle) {
            ResultSafePhase::AwaitHandlers {
                continuation,
                sub_program,
            } => {
                let handlers = match value {
                    Value::Handlers(handlers) => handlers,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "ResultSafe handler expected GetHandlers result",
                        ));
                    }
                };
                self.phase = ResultSafePhase::AwaitEval { continuation };
                RustProgramStep::Yield(DoCtrl::Eval {
                    expr: sub_program,
                    handlers,
                    metadata: None,
                })
            }
            ResultSafePhase::AwaitEval { continuation } => self.finish_ok(continuation, value),
            ResultSafePhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, ResultSafePhase::Idle) {
            ResultSafePhase::AwaitHandlers { continuation, .. }
            | ResultSafePhase::AwaitEval { continuation } => self.finish_err(continuation, exc),
            ResultSafePhase::Idle => RustProgramStep::Throw(exc),
        }
    }
}

impl ASTStream for ResultSafeHandlerProgram {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::resume(
            self, value, store,
        ))
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep {
        rust_program_step_to_ast_stream_step(<Self as RustHandlerProgram>::throw(self, exc, store))
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
impl RustProgramHandler for DoubleCallHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        matches!(effect, Effect::Modify { .. })
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(DoubleCallHandlerProgram {
            phase: DoubleCallPhase::Init,
        })))
    }

    fn handler_name(&self) -> &'static str {
        "DoubleCallHandler"
    }
}

#[cfg(test)]
impl HandlerInvoke for DoubleCallHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        <Self as RustProgramHandler>::can_handle(self, effect)
    }

    fn invoke(&self, effect: DispatchEffect, k: Continuation) -> DoExpr {
        rust_program_expand_expr(
            Arc::new(self.clone()),
            effect,
            k,
            metadata_from_debug_info(self.handler_debug_info()),
        )
    }

    fn handler_name(&self) -> &str {
        <Self as RustProgramHandler>::handler_name(self)
    }

    fn handler_debug_info(&self) -> HandlerDebugInfo {
        HandlerDebugInfo {
            name: <Self as RustProgramHandler>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }
}

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
impl RustHandlerProgram for DoubleCallHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        match effect {
            Effect::Modify { modifier, .. } => {
                // Store k and modifier for later. First Python call: modifier(10)
                self.phase = DoubleCallPhase::AwaitingFirstResult {
                    k,
                    modifier: modifier.clone(),
                };
                RustProgramStep::NeedsPython(PythonCall::CallFunc {
                    func: modifier,
                    args: vec![Value::Int(10)],
                    kwargs: vec![],
                })
            }
            other => RustProgramStep::Yield(DoCtrl::Delegate { effect: other }),
        }
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, DoubleCallPhase::Done) {
            DoubleCallPhase::AwaitingFirstResult { k, modifier } => {
                // Got first result. Now do a SECOND Python call: modifier(first_result).
                // This is the critical path: NeedsPython from resume().
                self.phase = DoubleCallPhase::AwaitingSecondResult {
                    k,
                    first_result: value.clone(),
                };
                RustProgramStep::NeedsPython(PythonCall::CallFunc {
                    func: modifier,
                    args: vec![value],
                    kwargs: vec![],
                })
            }
            DoubleCallPhase::AwaitingSecondResult { k, first_result } => {
                // Got second result. Combine and yield Resume.
                let combined =
                    Value::Int(first_result.as_int().unwrap_or(0) + value.as_int().unwrap_or(0));
                RustProgramStep::Yield(DoCtrl::Resume {
                    continuation: k,
                    value: combined,
                })
            }
            DoubleCallPhase::Done | DoubleCallPhase::Init => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast_stream::{ASTStream, ASTStreamStep};
    use crate::ids::Marker;
    use crate::segment::Segment;
    use pyo3::types::PyDictMethods;
    use pyo3::{IntoPyObject, Python};

    fn make_test_continuation() -> Continuation {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![marker]);
        let seg_id = SegmentId::from_index(0);
        Continuation::capture(&seg, seg_id, None)
    }

    #[test]
    fn test_handler_entry_creation() {
        let handler: Handler = Arc::new(StateHandlerFactory);
        let prompt_seg_id = SegmentId::from_index(5);
        let entry = HandlerEntry::new(handler, prompt_seg_id);

        assert_eq!(entry.prompt_seg_id, prompt_seg_id);
        assert_eq!(entry.handler.handler_name(), "StateHandler");
    }

    #[test]
    fn test_rust_program_handler_ref_is_clone() {
        // Verify that Rust handler references are Clone via Arc
        // (Can't easily instantiate a trait object in unit test, but verify types compile)
        let _: fn() -> RustProgramHandlerRef = || unreachable!();
    }

    #[test]
    fn test_await_ast_stream_resume_sequence() {
        let mut store = RustStore::new();
        let mut program = AwaitHandlerProgram::new();
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.pending_k = Some(continuation);

        let location = ASTStream::debug_location(&program).expect("await debug location");
        assert_eq!(location.function_name, "AwaitHandler");
        assert_eq!(location.phase.as_deref(), Some("AwaitBridgeResult"));

        let step = ASTStream::resume(&mut program, Value::Int(12), &mut store);
        match step {
            ASTStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(12));
            }
            _ => panic!("expected ASTStream Yield(Resume)"),
        }

        let location = ASTStream::debug_location(&program).expect("await debug location");
        assert_eq!(location.phase.as_deref(), Some("Idle"));
    }

    #[test]
    fn test_state_ast_stream_modify_resume_sequence() {
        let mut store = RustStore::new();
        store.put("count".to_string(), Value::Int(5));

        let mut program = StateHandlerProgram::new();
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.pending_key = Some("count".to_string());
        program.pending_k = Some(continuation);
        program.pending_old_value = Some(Value::Int(5));

        let location = ASTStream::debug_location(&program).expect("state debug location");
        assert_eq!(location.function_name, "StateHandler");
        assert_eq!(location.phase.as_deref(), Some("ModifyApply"));

        let step = ASTStream::resume(&mut program, Value::Int(8), &mut store);
        match step {
            ASTStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(5));
            }
            _ => panic!("expected ASTStream Yield(Resume)"),
        }

        assert_eq!(store.get("count").and_then(Value::as_int), Some(8));
        let location = ASTStream::debug_location(&program).expect("state debug location");
        assert_eq!(location.phase.as_deref(), Some("Idle"));
    }

    #[test]
    fn test_reader_ast_stream_throw_sequence() {
        let mut store = RustStore::new();
        let mut program = ReaderHandlerProgram;

        let location = ASTStream::debug_location(&program).expect("reader debug location");
        assert_eq!(location.function_name, "ReaderHandler");
        assert_eq!(location.phase.as_deref(), Some("AskApply"));

        let step = ASTStream::throw(&mut program, PyException::runtime_error("boom"), &mut store);
        assert!(matches!(step, ASTStreamStep::Throw(_)));
    }

    #[test]
    fn test_writer_ast_stream_throw_sequence() {
        let mut store = RustStore::new();
        let mut program = WriterHandlerProgram;

        let location = ASTStream::debug_location(&program).expect("writer debug location");
        assert_eq!(location.function_name, "WriterHandler");
        assert_eq!(location.phase.as_deref(), Some("TellApply"));

        let step = ASTStream::throw(&mut program, PyException::runtime_error("boom"), &mut store);
        assert!(matches!(step, ASTStreamStep::Throw(_)));
    }

    #[test]
    fn test_lazy_ask_ast_stream_release_sequence() {
        let mut store = RustStore::new();
        let mut program = LazyAskHandlerProgram::new(Arc::new(Mutex::new(LazyAskState::default())));
        let continuation = make_test_continuation();
        let continuation_id = continuation.cont_id;
        program.phase = LazyAskPhase::AwaitRelease {
            continuation,
            outcome: Ok(Value::Int(44)),
        };

        let location = ASTStream::debug_location(&program).expect("lazy ask debug location");
        assert_eq!(location.function_name, "LazyAskHandler");
        assert_eq!(location.phase.as_deref(), Some("AwaitRelease"));

        let step = ASTStream::resume(&mut program, Value::Unit, &mut store);
        match step {
            ASTStreamStep::Yield(DoCtrl::Resume {
                continuation,
                value,
            }) => {
                assert_eq!(continuation.cont_id, continuation_id);
                assert_eq!(value.as_int(), Some(44));
            }
            _ => panic!("expected ASTStream Yield(Resume)"),
        }
    }

    #[test]
    fn test_result_safe_ast_stream_handlers_to_eval_sequence() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let mut program = ResultSafeHandlerProgram::new();
            let continuation = make_test_continuation();
            let sub_program =
                PyShared::new(py.None().into_pyobject(py).unwrap().unbind().into_any());
            program.phase = ResultSafePhase::AwaitHandlers {
                continuation,
                sub_program,
            };

            let location = ASTStream::debug_location(&program).expect("result safe debug location");
            assert_eq!(location.function_name, "ResultSafeHandler");
            assert_eq!(location.phase.as_deref(), Some("AwaitHandlers"));

            let step = ASTStream::resume(&mut program, Value::Handlers(vec![]), &mut store);
            assert!(matches!(step, ASTStreamStep::Yield(DoCtrl::Eval { .. })));

            let location = ASTStream::debug_location(&program).expect("result safe debug location");
            assert_eq!(location.phase.as_deref(), Some("AwaitEval"));
        });
    }

    // --- Factory-based handler tests (R8) ---

    #[test]
    fn test_state_factory_can_handle() {
        let f = StateHandlerFactory;
        assert!(HandlerInvoke::can_handle(&f, &Effect::Get {
            key: "x".to_string()
        }));
        assert!(HandlerInvoke::can_handle(&f, &Effect::Put {
            key: "x".to_string(),
            value: Value::Unit
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Ask {
            key: "x".to_string()
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Tell {
            message: Value::Unit
        }));
    }

    #[test]
    fn test_state_factory_get() {
        Python::attach(|py| {
            let mut store = RustStore::new();
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
                )
            };
            match step {
                RustProgramStep::Yield(DoCtrl::Resume { value, .. }) => {
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
                )
            };
            assert!(matches!(
                step,
                RustProgramStep::Yield(DoCtrl::Resume {
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
                )
            };
            match step {
                RustProgramStep::NeedsPython(PythonCall::CallFunc { args, .. }) => {
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
                );
            }
            // resume with new value
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(20), &mut store)
            };
            match step {
                RustProgramStep::Yield(DoCtrl::Resume { value, .. }) => {
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
        assert!(HandlerInvoke::can_handle(&f, &Effect::Ask {
            key: "x".to_string()
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Get {
            key: "x".to_string()
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Tell {
            message: Value::Unit
        }));
    }

    #[test]
    fn test_reader_factory_ask() {
        Python::attach(|py| {
            let mut store = RustStore::new();
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
                )
            };
            match step {
                RustProgramStep::Yield(DoCtrl::Resume { value, .. }) => {
                    assert_eq!(value.as_str(), Some("value"));
                }
                _ => panic!("Expected Yield(Resume)"),
            }
        });
    }

    #[test]
    fn test_writer_factory_can_handle() {
        let f = WriterHandlerFactory;
        assert!(HandlerInvoke::can_handle(&f, &Effect::Tell {
            message: Value::Unit
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Get {
            key: "x".to_string()
        }));
        assert!(!HandlerInvoke::can_handle(&f, &Effect::Ask {
            key: "x".to_string()
        }));
    }

    #[test]
    fn test_writer_factory_tell() {
        Python::attach(|py| {
            let mut store = RustStore::new();
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
                )
            };
            assert!(matches!(
                step,
                RustProgramStep::Yield(DoCtrl::Resume {
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
                HandlerInvoke::can_handle(&f, &effect),
                "ResultSafe handler should claim ResultSafeEffect"
            );
        });
    }

    #[test]
    fn test_result_safe_handler_wraps_return_and_exception() {
        Python::attach(|py| {
            let mut store = RustStore::new();
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
                guard.start(py, effect.clone(), k.clone(), &mut store)
            };
            assert!(matches!(
                start_step,
                RustProgramStep::Yield(DoCtrl::GetHandlers)
            ));

            let await_eval_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.resume(Value::Handlers(vec![]), &mut store)
            };
            assert!(matches!(
                await_eval_step,
                RustProgramStep::Yield(DoCtrl::Eval { .. })
            ));

            let ok_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.resume(Value::Int(42), &mut store)
            };
            match ok_step {
                RustProgramStep::Yield(DoCtrl::Resume {
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
                guard.start(py, effect, k, &mut store)
            };

            let err_step = {
                let mut guard = err_program.lock().unwrap();
                guard.throw(PyException::runtime_error("boom"), &mut store)
            };

            match err_step {
                RustProgramStep::Yield(DoCtrl::Resume {
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
                )
            };
            assert!(matches!(
                step1,
                RustProgramStep::NeedsPython(PythonCall::CallFunc { .. })
            ));

            // Step 2: first resume() returns NeedsPython AGAIN (the critical path)
            let step2 = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(100), &mut store)
            };
            assert!(
                matches!(
                    step2,
                    RustProgramStep::NeedsPython(PythonCall::CallFunc { .. })
                ),
                "Expected NeedsPython from resume(), got something else"
            );

            // Step 3: second resume() yields Resume with combined result
            let step3 = {
                let mut guard = program_ref.lock().unwrap();
                guard.resume(Value::Int(200), &mut store)
            };
            match step3 {
                RustProgramStep::Yield(DoCtrl::Resume { value, .. }) => {
                    // 100 + 200 = 300
                    assert_eq!(value.as_int(), Some(300));
                }
                _ => panic!("Expected Yield(Resume) with combined value 300"),
            }
        });
    }
}

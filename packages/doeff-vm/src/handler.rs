//! Handler types for effect handling.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::PyModule;

use crate::continuation::Continuation;
#[cfg(test)]
use crate::effect::Effect;
use crate::effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, DispatchEffect, KpcArg,
    KpcCallEffect, PyKPC,
};
use crate::frame::CallMetadata;
use crate::ids::SegmentId;
use crate::py_shared::PyShared;
use crate::pyvm::{PyDoCtrlBase, PyDoExprBase, PyEffectBase, PyResultErr, PyResultOk};
use crate::step::{DoCtrl, PyException, PythonCall, Yielded};
use crate::value::Value;
use crate::vm::RustStore;

#[derive(Debug, Clone)]
pub enum Handler {
    RustProgram(RustProgramHandlerRef),
    Python(PyShared),
}

/// Result of stepping a Rust handler program.
pub enum RustProgramStep {
    /// Yield a control primitive / effect / program
    Yield(Yielded),
    /// Return a value (like generator return)
    Return(Value),
    /// Throw an exception into the VM
    Throw(PyException),
    /// Need to call a Python function (e.g., Modify calling modifier).
    /// The program is suspended; result feeds back via resume().
    NeedsPython(PythonCall),
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
}

/// Shared reference to a Rust program handler factory.
pub type RustProgramHandlerRef = Arc<dyn RustProgramHandler + Send + Sync>;

/// Shared reference to a running Rust handler program (cloneable for continuations).
pub type RustProgramRef = Arc<Mutex<Box<dyn RustHandlerProgram + Send>>>;

#[derive(Debug, Clone)]
pub struct HandlerEntry {
    pub handler: Handler,
    pub prompt_seg_id: SegmentId,
    pub py_identity: Option<PyShared>,
}

impl HandlerEntry {
    pub fn new(handler: Handler, prompt_seg_id: SegmentId) -> Self {
        HandlerEntry {
            handler,
            prompt_seg_id,
            py_identity: None,
        }
    }

    pub fn with_identity(
        handler: Handler,
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

impl Handler {
    pub fn can_handle(&self, effect: &DispatchEffect) -> bool {
        match self {
            Handler::RustProgram(h) => h.can_handle(effect),
            Handler::Python(_) => true,
        }
    }
}

fn has_true_attr(obj: &Bound<'_, PyAny>, attr: &str) -> bool {
    obj.getattr(attr)
        .and_then(|v| v.extract::<bool>())
        .unwrap_or(false)
}

fn is_instance_from(obj: &Bound<'_, PyAny>, module: &str, class_name: &str) -> bool {
    let py = obj.py();
    let Ok(mod_) = py.import(module) else {
        return false;
    };
    let Ok(cls) = mod_.getattr(class_name) else {
        return false;
    };
    obj.is_instance(&cls).unwrap_or(false)
}

enum ParsedStateEffect {
    Get { key: String },
    Put { key: String, value: Value },
    Modify { key: String, modifier: PyShared },
}

fn parse_state_python_effect(effect: &PyShared) -> Result<Option<ParsedStateEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if is_instance_from(obj, "doeff.effects.state", "StateGetEffect") {
            let key: String = obj
                .getattr("key")
                .map_err(|e| e.to_string())?
                .extract::<String>()
                .map_err(|e| e.to_string())?;
            return Ok(Some(ParsedStateEffect::Get { key }));
        }

        if is_instance_from(obj, "doeff.effects.state", "StatePutEffect") {
            let key: String = obj
                .getattr("key")
                .map_err(|e| e.to_string())?
                .extract::<String>()
                .map_err(|e| e.to_string())?;
            let value = obj.getattr("value").map_err(|e| e.to_string())?;
            return Ok(Some(ParsedStateEffect::Put {
                key,
                value: Value::from_pyobject(&value),
            }));
        }

        if is_instance_from(obj, "doeff.effects.state", "StateModifyEffect") {
            let key: String = obj
                .getattr("key")
                .map_err(|e| e.to_string())?
                .extract::<String>()
                .map_err(|e| e.to_string())?;
            let modifier = obj
                .getattr("func")
                .or_else(|_| obj.getattr("modifier"))
                .map_err(|e| e.to_string())?;
            return Ok(Some(ParsedStateEffect::Modify {
                key,
                modifier: PyShared::new(modifier.unbind()),
            }));
        }

        Ok(None)
    })
}

fn parse_reader_python_effect(effect: &PyShared) -> Result<Option<String>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if is_instance_from(obj, "doeff.effects.reader", "AskEffect") {
            let key: String = obj
                .getattr("key")
                .map_err(|e| e.to_string())?
                .extract::<String>()
                .map_err(|e| e.to_string())?;
            return Ok(Some(key));
        }

        if is_instance_from(obj, "doeff.effects.reader", "HashableAskEffect") {
            let key_obj = obj.getattr("key").map_err(|e| e.to_string())?;
            let key = key_obj
                .str()
                .map_err(|e| e.to_string())?
                .to_str()
                .map_err(|e| e.to_string())?
                .to_string();
            return Ok(Some(key));
        }

        Ok(None)
    })
}

fn missing_env_key_error(key: &str) -> PyException {
    Python::attach(|py| {
        let maybe_exc = (|| -> PyResult<Py<PyAny>> {
            let cls = py.import("doeff.errors")?.getattr("MissingEnvKeyError")?;
            let value = cls.call1((key,))?;
            Ok(value.unbind())
        })();

        match maybe_exc {
            Ok(exc_value) => {
                let exc_type = exc_value.bind(py).get_type().into_any().unbind();
                PyException::new(exc_type, exc_value, None)
            }
            Err(_) => {
                let err = pyo3::exceptions::PyKeyError::new_err(key.to_string());
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

        let is_doexpr = bound.is_instance_of::<PyDoExprBase>();
        let is_effect = bound.is_instance_of::<PyEffectBase>();

        if is_doexpr || is_effect {
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

fn parse_writer_python_effect(effect: &PyShared) -> Result<Option<Value>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if is_instance_from(obj, "doeff.effects.writer", "WriterTellEffect") {
            let message = obj.getattr("message").map_err(|e| e.to_string())?;
            return Ok(Some(Value::from_pyobject(&message)));
        }
        Ok(None)
    })
}

fn parse_await_python_effect(effect: &PyShared) -> Result<Option<Py<PyAny>>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        if is_instance_from(obj, "doeff.effects.future", "PythonAsyncioAwaitEffect") {
            let awaitable = obj.getattr("awaitable").map_err(|e| e.to_string())?;
            return Ok(Some(awaitable.unbind()));
        }
        Ok(None)
    })
}

fn parse_result_safe_python_effect(effect: &PyShared) -> Result<Option<PyShared>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        let is_result_safe = {
            #[cfg(test)]
            {
                is_instance_from(obj, "doeff.effects.result", "ResultSafeEffect")
                    || has_true_attr(obj, "__doeff_result_safe__")
            }
            #[cfg(not(test))]
            {
                is_instance_from(obj, "doeff.effects.result", "ResultSafeEffect")
            }
        };

        if is_result_safe {
            let sub_program = obj.getattr("sub_program").map_err(|e| e.to_string())?;
            return Ok(Some(PyShared::new(sub_program.unbind())));
        }
        Ok(None)
    })
}

fn get_blocking_await_runner() -> Result<PyShared, String> {
    Python::attach(|py| {
        let module = PyModule::from_code(
            py,
            c"import asyncio\nimport concurrent.futures\n\ndef _run_awaitable_blocking(awaitable):\n    def _runner():\n        return asyncio.run(awaitable)\n\n    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:\n        return executor.submit(_runner).result()\n",
            c"_doeff_await_bridge",
            c"_doeff_await_bridge",
        )
        .map_err(|e| e.to_string())?;
        let runner = module
            .getattr("_run_awaitable_blocking")
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
}

#[derive(Debug)]
struct AwaitHandlerProgram {
    pending_k: Option<Continuation>,
}

impl AwaitHandlerProgram {
    fn new() -> Self {
        AwaitHandlerProgram { pending_k: None }
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
                    let runner = match get_blocking_await_runner() {
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
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse await effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        if let Some(continuation) = self.pending_k.take() {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation,
                value,
            }));
        }
        RustProgramStep::Return(value)
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        if let Some(continuation) = self.pending_k.take() {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::TransferThrow {
                continuation,
                exception: exc,
            }));
        }
        RustProgramStep::Throw(exc)
    }
}

fn parse_kpc_python_effect(effect: &PyShared) -> Result<Option<KpcCallEffect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        let Ok(kpc) = obj.extract::<PyRef<'_, PyKPC>>() else {
            return Ok(None);
        };

        let metadata = extract_kpc_call_metadata(obj)?;
        let kernel = PyShared::new(kpc.execution_kernel.clone_ref(py));
        let strategy = py
            .import("doeff.program")
            .ok()
            .and_then(|mod_program| mod_program.getattr("_build_auto_unwrap_strategy").ok())
            .and_then(|builder| builder.call1((kpc.kleisli_source.bind(py),)).ok());

        let mut args = Vec::new();
        for (idx, item) in kpc
            .args
            .bind(py)
            .try_iter()
            .map_err(|e| e.to_string())?
            .enumerate()
        {
            let item = item.map_err(|e| e.to_string())?;
            let should_unwrap = kpc_strategy_should_unwrap_positional(strategy.as_ref(), idx)?;
            args.push(extract_kpc_arg(&item, should_unwrap)?);
        }

        let kwargs_dict = kpc
            .kwargs
            .bind(py)
            .cast::<pyo3::types::PyDict>()
            .map_err(|e| e.to_string())?;
        let mut kwargs = Vec::new();
        for (k, v) in kwargs_dict.iter() {
            let key: String = k.extract::<String>().map_err(|e| e.to_string())?;
            let should_unwrap =
                kpc_strategy_should_unwrap_keyword(strategy.as_ref(), key.as_str())?;
            kwargs.push((key, extract_kpc_arg(&v, should_unwrap)?));
        }

        Ok(Some(KpcCallEffect {
            call: PyShared::new(obj.clone().unbind()),
            kernel,
            args,
            kwargs,
            metadata,
        }))
    })
}

fn extract_kpc_call_metadata(obj: &Bound<'_, PyAny>) -> Result<CallMetadata, String> {
    let function_name = obj
        .getattr("function_name")
        .ok()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_else(|| "<anonymous>".to_string());

    if let Ok(kleisli) = obj.getattr("kleisli_source") {
        if let Ok(func) = kleisli.getattr("original_func") {
            if let Ok(code) = func.getattr("__code__") {
                let source_file = code
                    .getattr("co_filename")
                    .ok()
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_else(|| "<unknown>".to_string());
                let source_line = code
                    .getattr("co_firstlineno")
                    .ok()
                    .and_then(|v| v.extract::<u32>().ok())
                    .unwrap_or(0);
                return Ok(CallMetadata {
                    function_name,
                    source_file,
                    source_line,
                    program_call: Some(PyShared::new(obj.clone().unbind())),
                });
            }
        }
    }

    let source_file = obj
        .getattr("source_file")
        .ok()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_else(|| "<unknown>".to_string());
    let source_line = obj
        .getattr("source_line")
        .ok()
        .and_then(|v| v.extract::<u32>().ok())
        .unwrap_or(0);

    Ok(CallMetadata {
        function_name,
        source_file,
        source_line,
        program_call: Some(PyShared::new(obj.clone().unbind())),
    })
}

fn kpc_strategy_should_unwrap_positional(
    strategy: Option<&Bound<'_, PyAny>>,
    idx: usize,
) -> Result<bool, String> {
    let Some(strategy) = strategy else {
        return Ok(true);
    };
    Ok(strategy
        .call_method1("should_unwrap_positional", (idx,))
        .and_then(|v| v.extract::<bool>())
        .unwrap_or(true))
}

fn kpc_strategy_should_unwrap_keyword(
    strategy: Option<&Bound<'_, PyAny>>,
    key: &str,
) -> Result<bool, String> {
    let Some(strategy) = strategy else {
        return Ok(true);
    };
    Ok(strategy
        .call_method1("should_unwrap_keyword", (key,))
        .and_then(|v| v.extract::<bool>())
        .unwrap_or(true))
}

fn extract_kpc_arg(obj: &Bound<'_, PyAny>, should_unwrap: bool) -> Result<KpcArg, String> {
    if should_unwrap && is_do_expr_candidate(obj)? {
        return Ok(KpcArg::Expr(PyShared::new(obj.clone().unbind())));
    }
    Ok(KpcArg::Value(Value::from_pyobject(obj)))
}

fn is_do_expr_candidate(obj: &Bound<'_, PyAny>) -> Result<bool, String> {
    Ok(obj.is_instance_of::<PyEffectBase>()
        || obj.is_instance_of::<PyDoCtrlBase>()
        || obj.is_instance_of::<PyKPC>())
}

// ---------------------------------------------------------------------------
// KpcHandlerFactory + KpcHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct KpcHandlerFactory;

impl RustProgramHandler for KpcHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_kpc_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(KpcHandlerProgram::new())))
    }
}

#[derive(Debug, Clone)]
enum KpcPending {
    Positional,
    Keyword(String),
    KernelCall,
    EvalResult,
}

#[derive(Debug, Clone)]
struct KpcResolution {
    k_user: Continuation,
    kernel: PyShared,
    metadata: CallMetadata,
    handlers: Vec<Handler>,
    args: Vec<KpcArg>,
    kwargs: Vec<(String, KpcArg)>,
    arg_idx: usize,
    kw_idx: usize,
    resolved_args: Vec<Value>,
    resolved_kwargs: Vec<(String, Value)>,
    pending: Option<KpcPending>,
}

#[derive(Debug)]
enum KpcPhase {
    Idle,
    AwaitHandlers {
        k_user: Continuation,
        kpc: KpcCallEffect,
    },
    Running(KpcResolution),
}

#[derive(Debug)]
struct KpcHandlerProgram {
    phase: KpcPhase,
}

impl KpcHandlerProgram {
    fn new() -> Self {
        KpcHandlerProgram {
            phase: KpcPhase::Idle,
        }
    }

    fn advance_running(
        &mut self,
        mut state: KpcResolution,
        input: Option<Value>,
    ) -> RustProgramStep {
        if let Some(value) = input {
            match state.pending.take() {
                Some(KpcPending::Positional) => state.resolved_args.push(value),
                Some(KpcPending::Keyword(key)) => state.resolved_kwargs.push((key, value)),
                Some(KpcPending::KernelCall) => {
                    // R12-A Phase 2: kernel returned — Eval the generator with handlers.
                    match value {
                        Value::Python(gen) => {
                            state.pending = Some(KpcPending::EvalResult);
                            let expr = PyShared::new(gen);
                            let handlers = state.handlers.clone();
                            self.phase = KpcPhase::Running(state);
                            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval {
                                expr,
                                handlers,
                            }));
                        }
                        other => {
                            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                                continuation: state.k_user,
                                value: other,
                            }));
                        }
                    }
                }
                Some(KpcPending::EvalResult) => {
                    return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: state.k_user,
                        value,
                    }));
                }
                None => {
                    return RustProgramStep::Throw(PyException::runtime_error(
                        "KPC handler resumed without pending step",
                    ));
                }
            }
        }

        loop {
            if state.arg_idx < state.args.len() {
                match state.args[state.arg_idx].clone() {
                    KpcArg::Value(v) => {
                        state.arg_idx += 1;
                        state.resolved_args.push(v);
                        continue;
                    }
                    KpcArg::Expr(expr) => {
                        state.arg_idx += 1;
                        state.pending = Some(KpcPending::Positional);
                        let handlers = state.handlers.clone();
                        self.phase = KpcPhase::Running(state);
                        return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval {
                            expr,
                            handlers,
                        }));
                    }
                }
            }

            if state.kw_idx < state.kwargs.len() {
                let (key, arg) = state.kwargs[state.kw_idx].clone();
                match arg {
                    KpcArg::Value(v) => {
                        state.kw_idx += 1;
                        state.resolved_kwargs.push((key, v));
                        continue;
                    }
                    KpcArg::Expr(expr) => {
                        state.kw_idx += 1;
                        state.pending = Some(KpcPending::Keyword(key));
                        let handlers = state.handlers.clone();
                        self.phase = KpcPhase::Running(state);
                        return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval {
                            expr,
                            handlers,
                        }));
                    }
                }
            }

            // R12-A: Two-phase kernel invocation.
            // Phase 1: Call kernel(*args, **kwargs) via NeedsPython to get the generator.
            state.pending = Some(KpcPending::KernelCall);
            let func = state.kernel.clone();
            let args = state.resolved_args.clone();
            let kwargs = state.resolved_kwargs.clone();
            self.phase = KpcPhase::Running(state);
            return RustProgramStep::NeedsPython(PythonCall::CallFunc { func, args, kwargs });
        }
    }
}

impl RustHandlerProgram for KpcHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_kpc_python_effect(&obj) {
                Ok(Some(kpc)) => {
                    self.phase = KpcPhase::AwaitHandlers { k_user: k, kpc };
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
                }
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse KleisliProgramCall effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, KpcPhase::Idle) {
            KpcPhase::AwaitHandlers { k_user, kpc } => {
                let handlers = match value {
                    Value::Handlers(hs) => hs,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "KPC handler expected GetHandlers result",
                        ));
                    }
                };
                let state = KpcResolution {
                    k_user,
                    kernel: kpc.kernel,
                    metadata: kpc.metadata,
                    handlers,
                    args: kpc.args,
                    kwargs: kpc.kwargs,
                    arg_idx: 0,
                    kw_idx: 0,
                    resolved_args: vec![],
                    resolved_kwargs: vec![],
                    pending: None,
                };
                self.advance_running(state, None)
            }
            KpcPhase::Running(state) => self.advance_running(state, Some(value)),
            KpcPhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, KpcPhase::Idle) {
            KpcPhase::AwaitHandlers { k_user, .. } => {
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::TransferThrow {
                    continuation: k_user,
                    exception: exc,
                }))
            }
            KpcPhase::Running(state) => {
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::TransferThrow {
                    continuation: state.k_user,
                    exception: exc,
                }))
            }
            KpcPhase::Idle => RustProgramStep::Throw(exc),
        }
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
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation: k,
                value,
            }));
        }

        #[cfg(test)]
        if let Effect::Put { key, value } = effect.clone() {
            store.put(key, value);
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation: k,
                value: Value::Unit,
            }));
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
                        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                            continuation: k,
                            value,
                        }))
                    }
                    ParsedStateEffect::Put { key, value } => {
                        store.put(key, value);
                        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                            continuation: k,
                            value: Value::Unit,
                        }))
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
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse state effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
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
        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
            continuation,
            value: old_value,
        }))
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
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
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram::new())))
    }
}

#[derive(Debug)]
enum ReaderPhase {
    Idle,
    AwaitHandlers {
        key: String,
        continuation: Continuation,
        expr: PyShared,
        source_id: usize,
    },
    AwaitEval {
        key: String,
        continuation: Continuation,
        source_id: usize,
    },
}

#[derive(Debug)]
struct ReaderHandlerProgram {
    phase: ReaderPhase,
}

impl ReaderHandlerProgram {
    fn new() -> Self {
        ReaderHandlerProgram {
            phase: ReaderPhase::Idle,
        }
    }

    fn handle_ask(
        &mut self,
        key: String,
        continuation: Continuation,
        store: &mut RustStore,
    ) -> RustProgramStep {
        let Some(value) = store.ask(&key).cloned() else {
            return RustProgramStep::Throw(missing_env_key_error(&key));
        };

        if let Some(expr) = as_lazy_eval_expr(&value) {
            let source_id = lazy_source_id(&value).unwrap_or_default();

            if let Some(cached) = store.lazy_cache_get(&key, source_id) {
                store.env.insert(key, cached.clone());
                return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation,
                    value: cached,
                }));
            }

            if store.lazy_active_contains(&key, source_id) {
                return RustProgramStep::Throw(PyException::runtime_error(format!(
                    "circular lazy Ask dependency detected for key '{}'",
                    key
                )));
            }

            store.lazy_active_insert(key.clone(), source_id);

            self.phase = ReaderPhase::AwaitHandlers {
                key,
                continuation,
                expr,
                source_id,
            };
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers));
        }

        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
            continuation,
            value,
        }))
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
            return self.handle_ask(key, k, store);
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_reader_python_effect(&obj) {
                Ok(Some(key)) => self.handle_ask(key, k, store),
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse reader effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, ReaderPhase::Idle) {
            ReaderPhase::AwaitHandlers {
                key,
                continuation,
                expr,
                source_id,
            } => {
                let handlers = match value {
                    Value::Handlers(handlers) => handlers,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "reader lazy Ask expected handlers from GetHandlers".to_string(),
                        ));
                    }
                };

                self.phase = ReaderPhase::AwaitEval {
                    key,
                    continuation,
                    source_id,
                };
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval { expr, handlers }))
            }
            ReaderPhase::AwaitEval {
                key,
                continuation,
                source_id,
            } => {
                store.lazy_active_remove(&key, source_id);
                store.env.insert(key.clone(), value.clone());
                store.lazy_cache_put(key.clone(), source_id, value.clone());
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation,
                    value,
                }))
            }
            ReaderPhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, ReaderPhase::Idle) {
            ReaderPhase::AwaitHandlers {
                key,
                continuation,
                source_id,
                ..
            } => {
                store.lazy_active_remove(&key, source_id);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::TransferThrow {
                    continuation,
                    exception: exc,
                }))
            }
            ReaderPhase::AwaitEval {
                key,
                continuation,
                source_id,
            } => {
                store.lazy_active_remove(&key, source_id);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::TransferThrow {
                    continuation,
                    exception: exc,
                }))
            }
            ReaderPhase::Idle => RustProgramStep::Throw(exc),
        }
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
}

#[derive(Debug)]
struct WriterHandlerProgram;

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
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation: k,
                value: Value::Unit,
            }));
        }

        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_writer_python_effect(&obj) {
                Ok(Some(message)) => {
                    store.tell(message);
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: k,
                        value: Value::Unit,
                    }))
                }
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse writer effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
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

    fn finish_ok(&self, continuation: Continuation, value: Value) -> RustProgramStep {
        match wrap_value_as_result_ok(value) {
            Ok(wrapped) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation,
                value: wrapped,
            })),
            Err(exc) => RustProgramStep::Throw(exc),
        }
    }

    fn finish_err(&self, continuation: Continuation, error: PyException) -> RustProgramStep {
        match wrap_exception_as_result_err(error) {
            Ok(wrapped) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                continuation,
                value: wrapped,
            })),
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
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
                }
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse ResultSafe effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval {
                    expr: sub_program,
                    handlers,
                }))
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
            other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect: other })),
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k,
                    value: combined,
                }))
            }
            DoubleCallPhase::Done | DoubleCallPhase::Init => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

// ---------------------------------------------------------------------------
// ConcurrentKpcHandlerFactory + ConcurrentKpcHandlerProgram
// ---------------------------------------------------------------------------
//
// Like KpcHandlerFactory but resolves KpcArg::Expr args in parallel via
// Perform(SpawnEffect) + Perform(GatherEffect). Requires a scheduler handler
// installed in an outer scope.

#[derive(Debug, Clone)]
pub struct ConcurrentKpcHandlerFactory;

impl RustProgramHandler for ConcurrentKpcHandlerFactory {
    fn can_handle(&self, effect: &DispatchEffect) -> bool {
        dispatch_ref_as_python(effect)
            .is_some_and(|obj| parse_kpc_python_effect(obj).ok().flatten().is_some())
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ConcurrentKpcHandlerProgram::new())))
    }
}

/// Tracks whether an arg slot is already resolved or awaiting an Eval result.
#[derive(Debug, Clone)]
enum ConcurrentArgSlot {
    Resolved(Value),
    Pending(usize), // index into eval results
}

#[derive(Debug)]
enum ConcurrentKpcPhase {
    Idle,
    AwaitHandlers {
        k_user: Continuation,
        kpc: KpcCallEffect,
    },
    /// Evaluating Expr args one-by-one via DoCtrl::Eval.
    Evaluating {
        k_user: Continuation,
        kernel: PyShared,
        handlers: Vec<Handler>,
        positional_slots: Vec<ConcurrentArgSlot>,
        keyword_slots: Vec<(String, ConcurrentArgSlot)>,
        eval_queue: VecDeque<PyShared>,
        results: Vec<Value>,
    },
    KernelCall {
        k_user: Continuation,
        handlers: Vec<Handler>,
    },
    EvalResult {
        k_user: Continuation,
    },
}

#[derive(Debug)]
struct ConcurrentKpcHandlerProgram {
    phase: ConcurrentKpcPhase,
}

impl ConcurrentKpcHandlerProgram {
    fn new() -> Self {
        ConcurrentKpcHandlerProgram {
            phase: ConcurrentKpcPhase::Idle,
        }
    }

    /// Classify args into resolved values and pending Eval exprs.
    fn classify_args(
        args: &[KpcArg],
        kwargs: &[(String, KpcArg)],
    ) -> (
        Vec<ConcurrentArgSlot>,
        Vec<(String, ConcurrentArgSlot)>,
        VecDeque<PyShared>,
    ) {
        let mut positional_slots = Vec::new();
        let mut keyword_slots = Vec::new();
        let mut eval_exprs = VecDeque::new();

        for arg in args {
            match arg {
                KpcArg::Value(v) => {
                    positional_slots.push(ConcurrentArgSlot::Resolved(v.clone()));
                }
                KpcArg::Expr(e) => {
                    let idx = eval_exprs.len();
                    eval_exprs.push_back(e.clone());
                    positional_slots.push(ConcurrentArgSlot::Pending(idx));
                }
            }
        }

        for (key, arg) in kwargs {
            match arg {
                KpcArg::Value(v) => {
                    keyword_slots.push((key.clone(), ConcurrentArgSlot::Resolved(v.clone())));
                }
                KpcArg::Expr(e) => {
                    let idx = eval_exprs.len();
                    eval_exprs.push_back(e.clone());
                    keyword_slots.push((key.clone(), ConcurrentArgSlot::Pending(idx)));
                }
            }
        }

        (positional_slots, keyword_slots, eval_exprs)
    }

    /// Reconstruct final arg vectors from slots + eval results.
    fn resolve_slots(
        positional_slots: &[ConcurrentArgSlot],
        keyword_slots: &[(String, ConcurrentArgSlot)],
        results: &[Value],
    ) -> (Vec<Value>, Vec<(String, Value)>) {
        let args = positional_slots
            .iter()
            .map(|slot| match slot {
                ConcurrentArgSlot::Resolved(v) => v.clone(),
                ConcurrentArgSlot::Pending(idx) => results[*idx].clone(),
            })
            .collect();

        let kwargs = keyword_slots
            .iter()
            .map(|(key, slot)| {
                let v = match slot {
                    ConcurrentArgSlot::Resolved(v) => v.clone(),
                    ConcurrentArgSlot::Pending(idx) => results[*idx].clone(),
                };
                (key.clone(), v)
            })
            .collect();

        (args, kwargs)
    }

    /// Yield the next DoCtrl::Eval or transition to KernelCall.
    fn advance_evaluating(
        &mut self,
        k_user: Continuation,
        kernel: PyShared,
        handlers: Vec<Handler>,
        positional_slots: Vec<ConcurrentArgSlot>,
        keyword_slots: Vec<(String, ConcurrentArgSlot)>,
        mut eval_queue: VecDeque<PyShared>,
        results: Vec<Value>,
    ) -> RustProgramStep {
        if let Some(expr) = eval_queue.pop_front() {
            self.phase = ConcurrentKpcPhase::Evaluating {
                k_user,
                kernel,
                handlers: handlers.clone(),
                positional_slots,
                keyword_slots,
                eval_queue,
                results,
            };

            RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval { expr, handlers }))
        } else {
            // All exprs evaluated — resolve slots and call kernel
            let (args, kwargs) = Self::resolve_slots(&positional_slots, &keyword_slots, &results);

            self.phase = ConcurrentKpcPhase::KernelCall { k_user, handlers };
            RustProgramStep::NeedsPython(PythonCall::CallFunc {
                func: kernel,
                args,
                kwargs,
            })
        }
    }
}

impl RustHandlerProgram for ConcurrentKpcHandlerProgram {
    fn start(
        &mut self,
        _py: Python<'_>,
        effect: DispatchEffect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        if let Some(obj) = dispatch_into_python(effect.clone()) {
            return match parse_kpc_python_effect(&obj) {
                Ok(Some(kpc)) => {
                    self.phase = ConcurrentKpcPhase::AwaitHandlers { k_user: k, kpc };
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
                }
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: dispatch_from_shared(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse KleisliProgramCall effect: {msg}"
                ))),
            };
        }

        #[cfg(test)]
        {
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect }));
        }

        #[cfg(not(test))]
        unreachable!("runtime Effect is always Python")
    }

    fn resume(&mut self, value: Value, _store: &mut RustStore) -> RustProgramStep {
        match std::mem::replace(&mut self.phase, ConcurrentKpcPhase::Idle) {
            ConcurrentKpcPhase::AwaitHandlers { k_user, kpc } => {
                let handlers = match value {
                    Value::Handlers(hs) => hs,
                    _ => {
                        return RustProgramStep::Throw(PyException::type_error(
                            "ConcurrentKPC handler expected GetHandlers result",
                        ));
                    }
                };

                let (positional_slots, keyword_slots, spawn_exprs) =
                    Self::classify_args(&kpc.args, &kpc.kwargs);

                self.advance_evaluating(
                    k_user,
                    kpc.kernel,
                    handlers,
                    positional_slots,
                    keyword_slots,
                    spawn_exprs,
                    Vec::new(),
                )
            }

            ConcurrentKpcPhase::Evaluating {
                k_user,
                kernel,
                handlers,
                positional_slots,
                keyword_slots,
                eval_queue,
                mut results,
            } => {
                // Got back the result from DoCtrl::Eval
                results.push(value);

                self.advance_evaluating(
                    k_user,
                    kernel,
                    handlers,
                    positional_slots,
                    keyword_slots,
                    eval_queue,
                    results,
                )
            }

            ConcurrentKpcPhase::KernelCall { k_user, handlers } => {
                // R12-A Phase 2: kernel returned — Eval the generator with handlers.
                match value {
                    Value::Python(gen) => {
                        self.phase = ConcurrentKpcPhase::EvalResult { k_user };
                        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval {
                            expr: PyShared::new(gen),
                            handlers,
                        }))
                    }
                    other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                        continuation: k_user,
                        value: other,
                    })),
                }
            }

            ConcurrentKpcPhase::EvalResult { k_user } => {
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k_user,
                    value,
                }))
            }

            ConcurrentKpcPhase::Idle => RustProgramStep::Return(value),
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
        let handler = Handler::RustProgram(Arc::new(StateHandlerFactory));
        let prompt_seg_id = SegmentId::from_index(5);
        let entry = HandlerEntry::new(handler, prompt_seg_id);

        assert_eq!(entry.prompt_seg_id, prompt_seg_id);
        assert!(matches!(entry.handler, Handler::RustProgram(_)));
    }

    #[test]
    fn test_rust_program_handler_ref_is_clone() {
        // Verify that Handler::RustProgram is Clone via Arc
        // (Can't easily instantiate a trait object in unit test, but verify types compile)
        let _: fn() -> RustProgramHandlerRef = || unreachable!();
    }

    // --- Factory-based handler tests (R8) ---

    #[test]
    fn test_kpc_factory_can_handle_python_kpc_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            locals.set_item("PyKPC", py.get_type::<PyKPC>()).unwrap();
            py.run(
                c"obj = PyKPC(None, (1,), {}, 'f', (lambda x: x))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));
            let f = KpcHandlerFactory;
            assert!(
                f.can_handle(&effect),
                "SPEC GAP: KPC handler should claim opaque KPC effect"
            );
        });
    }

    #[test]
    fn test_kpc_handler_start_from_python_kpc_effect() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let k = make_test_continuation();

            let locals = pyo3::types::PyDict::new(py);
            locals.set_item("PyKPC", py.get_type::<PyKPC>()).unwrap();
            py.run(
                c"obj = PyKPC(None, (1,), {}, 'f', (lambda x: x))\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));

            let program_ref = KpcHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(py, effect, k, &mut store)
            };
            assert!(
                matches!(
                    step,
                    RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
                ),
                "SPEC GAP: KPC opaque effect should start via GetHandlers"
            );
        });
    }

    #[test]
    fn test_state_factory_can_handle() {
        let f = StateHandlerFactory;
        assert!(f.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
        assert!(f.can_handle(&Effect::Put {
            key: "x".to_string(),
            value: Value::Unit
        }));
        assert!(!f.can_handle(&Effect::Ask {
            key: "x".to_string()
        }));
        assert!(!f.can_handle(&Effect::Tell {
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    value: Value::Unit,
                    ..
                }))
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
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
        assert!(f.can_handle(&Effect::Ask {
            key: "x".to_string()
        }));
        assert!(!f.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
        assert!(!f.can_handle(&Effect::Tell {
            message: Value::Unit
        }));
    }

    #[test]
    fn test_reader_factory_ask() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            store
                .env
                .insert("config".to_string(), Value::String("value".to_string()));
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
                    assert_eq!(value.as_str(), Some("value"));
                }
                _ => panic!("Expected Yield(Resume)"),
            }
        });
    }

    #[test]
    fn test_writer_factory_can_handle() {
        let f = WriterHandlerFactory;
        assert!(f.can_handle(&Effect::Tell {
            message: Value::Unit
        }));
        assert!(!f.can_handle(&Effect::Get {
            key: "x".to_string()
        }));
        assert!(!f.can_handle(&Effect::Ask {
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    value: Value::Unit,
                    ..
                }))
            ));
            assert_eq!(store.logs().len(), 1);
        });
    }

    #[test]
    fn test_result_safe_factory_can_handle_python_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            locals
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class ResultSafeEffect(EffectBase):\n    __doeff_result_safe__ = True\n    def __init__(self, sub_program):\n        self.sub_program = sub_program\n\nobj = ResultSafeEffect(None)\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::from_shared(PyShared::new(obj));
            let f = ResultSafeHandlerFactory;
            assert!(
                f.can_handle(&effect),
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
                .set_item("EffectBase", py.get_type::<PyEffectBase>())
                .unwrap();
            py.run(
                c"class ResultSafeEffect(EffectBase):\n    __doeff_result_safe__ = True\n    def __init__(self, sub_program):\n        self.sub_program = sub_program\n\nobj = ResultSafeEffect(None)\n",
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
            ));

            let await_eval_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.resume(Value::Handlers(vec![]), &mut store)
            };
            assert!(matches!(
                await_eval_step,
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Eval { .. }))
            ));

            let ok_step = {
                let mut guard = ok_program.lock().unwrap();
                guard.resume(Value::Int(42), &mut store)
            };
            match ok_step {
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    value: Value::Python(obj),
                    ..
                })) => {
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    value: Value::Python(obj),
                    ..
                })) => {
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
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume { value, .. })) => {
                    // 100 + 200 = 300
                    assert_eq!(value.as_int(), Some(300));
                }
                _ => panic!("Expected Yield(Resume) with combined value 300"),
            }
        });
    }
}

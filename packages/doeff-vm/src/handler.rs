//! Handler types for effect handling.

use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::{Effect, KpcArg, KpcCallEffect};
use crate::frame::CallMetadata;
use crate::ids::SegmentId;
use crate::py_shared::PyShared;
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
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

/// Factory for Rust handler programs. Each dispatch creates a fresh instance.
pub trait RustProgramHandler: std::fmt::Debug + Send + Sync {
    fn can_handle(&self, effect: &Effect) -> bool;
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
    pub fn can_handle(&self, effect: &Effect) -> bool {
        match self {
            Handler::RustProgram(h) => h.can_handle(effect),
            Handler::Python(_) => true,
        }
    }
}

fn python_effect_type_name(effect: &PyShared) -> Option<String> {
    Python::attach(|py| {
        effect
            .bind(py)
            .get_type()
            .name()
            .ok()?
            .extract::<String>()
            .ok()
    })
}

fn parse_state_python_effect(effect: &PyShared) -> Result<Option<Effect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        let type_name: String = obj
            .get_type()
            .name()
            .map_err(|e| e.to_string())?
            .extract::<String>()
            .map_err(|e| e.to_string())?;

        match type_name.as_str() {
            "StateGetEffect" | "Get" => {
                let key: String = obj
                    .getattr("key")
                    .map_err(|e| e.to_string())?
                    .extract::<String>()
                    .map_err(|e| e.to_string())?;
                Ok(Some(Effect::Get { key }))
            }
            "StatePutEffect" | "Put" => {
                let key: String = obj
                    .getattr("key")
                    .map_err(|e| e.to_string())?
                    .extract::<String>()
                    .map_err(|e| e.to_string())?;
                let value = obj.getattr("value").map_err(|e| e.to_string())?;
                Ok(Some(Effect::Put {
                    key,
                    value: Value::from_pyobject(&value),
                }))
            }
            "StateModifyEffect" | "Modify" => {
                let key: String = obj
                    .getattr("key")
                    .map_err(|e| e.to_string())?
                    .extract::<String>()
                    .map_err(|e| e.to_string())?;
                let modifier = obj
                    .getattr("func")
                    .or_else(|_| obj.getattr("modifier"))
                    .map_err(|e| e.to_string())?;
                Ok(Some(Effect::Modify {
                    key,
                    modifier: PyShared::new(modifier.unbind()),
                }))
            }
            _ => Ok(None),
        }
    })
}

fn parse_reader_python_effect(effect: &PyShared) -> Result<Option<Effect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        let type_name: String = obj
            .get_type()
            .name()
            .map_err(|e| e.to_string())?
            .extract::<String>()
            .map_err(|e| e.to_string())?;
        match type_name.as_str() {
            "AskEffect" | "Ask" => {
                let key: String = obj
                    .getattr("key")
                    .map_err(|e| e.to_string())?
                    .extract::<String>()
                    .map_err(|e| e.to_string())?;
                Ok(Some(Effect::Ask { key }))
            }
            _ => Ok(None),
        }
    })
}

fn parse_writer_python_effect(effect: &PyShared) -> Result<Option<Effect>, String> {
    Python::attach(|py| {
        let obj = effect.bind(py);
        let type_name: String = obj
            .get_type()
            .name()
            .map_err(|e| e.to_string())?
            .extract::<String>()
            .map_err(|e| e.to_string())?;
        match type_name.as_str() {
            "WriterTellEffect" | "Tell" => {
                let message = obj.getattr("message").map_err(|e| e.to_string())?;
                Ok(Some(Effect::Tell {
                    message: Value::from_pyobject(&message),
                }))
            }
            _ => Ok(None),
        }
    })
}

// ---------------------------------------------------------------------------
// KpcHandlerFactory + KpcHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct KpcHandlerFactory;

impl RustProgramHandler for KpcHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::KpcCall(_))
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(KpcHandlerProgram::new())))
    }
}

#[derive(Debug, Clone)]
enum KpcPending {
    Positional,
    Keyword(String),
    CallResult,
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
                Some(KpcPending::CallResult) => {
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

            state.pending = Some(KpcPending::CallResult);
            let f = state.kernel.clone();
            let args = state.resolved_args.clone();
            let kwargs = state.resolved_kwargs.clone();
            let metadata = state.metadata.clone();
            self.phase = KpcPhase::Running(state);
            return RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Call {
                f,
                args,
                kwargs,
                metadata,
            }));
        }
    }
}

impl RustHandlerProgram for KpcHandlerProgram {
    fn start(
        &mut self,
        effect: Effect,
        k: Continuation,
        _store: &mut RustStore,
    ) -> RustProgramStep {
        match effect {
            Effect::KpcCall(kpc) => {
                self.phase = KpcPhase::AwaitHandlers { k_user: k, kpc };
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::GetHandlers))
            }
            other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect: other })),
        }
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
        RustProgramStep::Throw(exc)
    }
}

// ---------------------------------------------------------------------------
// StateHandlerFactory + StateHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct StateHandlerFactory;

impl RustProgramHandler for StateHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Get { .. } | Effect::Put { .. } | Effect::Modify { .. })
            || matches!(
                effect,
                Effect::Python(obj)
                    if matches!(
                        python_effect_type_name(obj).as_deref(),
                        Some("StateGetEffect" | "Get" | "StatePutEffect" | "Put" | "StateModifyEffect" | "Modify")
                    )
            )
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
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep {
        match effect {
            Effect::Get { key } => {
                let value = store.get(&key).cloned().unwrap_or(Value::None);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k,
                    value,
                }))
            }
            Effect::Put { key, value } => {
                store.put(key, value);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k,
                    value: Value::Unit,
                }))
            }
            Effect::Modify { key, modifier } => {
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
            Effect::Python(obj) => match parse_state_python_effect(&obj) {
                Ok(Some(parsed)) => self.start(parsed, k, store),
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: Effect::Python(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse state effect: {msg}"
                ))),
            },
            other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect: other })),
        }
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep {
        if self.pending_key.is_none() {
            // Terminal case (Get/Put): handler is done, pass through return value
            return RustProgramStep::Return(value);
        }
        // Modify case: store modifier result and resume caller with new value
        let key = self.pending_key.take().unwrap();
        let continuation = self.pending_k.take().unwrap();
        let _old_value = self.pending_old_value.take().unwrap();
        let new_value = value.clone();
        store.put(key, value);
        RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
            continuation,
            value: new_value,
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
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Ask { .. })
            || matches!(
                effect,
                Effect::Python(obj)
                    if matches!(python_effect_type_name(obj).as_deref(), Some("AskEffect" | "Ask"))
            )
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram)))
    }
}

#[derive(Debug)]
struct ReaderHandlerProgram;

impl RustHandlerProgram for ReaderHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep {
        match effect {
            Effect::Ask { key } => {
                let value = store.ask(&key).cloned().unwrap_or(Value::None);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k,
                    value,
                }))
            }
            Effect::Python(obj) => match parse_reader_python_effect(&obj) {
                Ok(Some(parsed)) => self.start(parsed, k, store),
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: Effect::Python(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse reader effect: {msg}"
                ))),
            },
            other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect: other })),
        }
    }

    fn resume(&mut self, value: Value, _: &mut RustStore) -> RustProgramStep {
        // Terminal: handler is done, pass through return value
        RustProgramStep::Return(value)
    }

    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

// ---------------------------------------------------------------------------
// WriterHandlerFactory + WriterHandlerProgram
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct WriterHandlerFactory;

impl RustProgramHandler for WriterHandlerFactory {
    fn can_handle(&self, effect: &Effect) -> bool {
        matches!(effect, Effect::Tell { .. })
            || matches!(
                effect,
                Effect::Python(obj)
                    if matches!(python_effect_type_name(obj).as_deref(), Some("WriterTellEffect" | "Tell"))
            )
    }

    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(WriterHandlerProgram)))
    }
}

#[derive(Debug)]
struct WriterHandlerProgram;

impl RustHandlerProgram for WriterHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep {
        match effect {
            Effect::Tell { message } => {
                store.tell(message);
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    continuation: k,
                    value: Value::Unit,
                }))
            }
            Effect::Python(obj) => match parse_writer_python_effect(&obj) {
                Ok(Some(parsed)) => self.start(parsed, k, store),
                Ok(None) => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate {
                    effect: Effect::Python(obj),
                })),
                Err(msg) => RustProgramStep::Throw(PyException::type_error(format!(
                    "failed to parse writer effect: {msg}"
                ))),
            },
            other => RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Delegate { effect: other })),
        }
    }

    fn resume(&mut self, value: Value, _: &mut RustStore) -> RustProgramStep {
        // Terminal: handler is done, pass through return value
        RustProgramStep::Return(value)
    }

    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
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
    fn can_handle(&self, effect: &Effect) -> bool {
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
        effect: Effect,
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
    fn test_state_factory_can_handle_python_state_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class StateGetEffect:\n    def __init__(self):\n        self.key = 'x'\nobj = StateGetEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::Python(PyShared::new(obj));
            let f = StateHandlerFactory;
            assert!(
                f.can_handle(&effect),
                "SPEC GAP: state handler should claim opaque Python state effects"
            );
        });
    }

    #[test]
    fn test_state_factory_get() {
        let mut store = RustStore::new();
        store.put("key".to_string(), Value::Int(42));
        let k = make_test_continuation();
        let program_ref = StateHandlerFactory.create_program();
        let step = {
            let mut guard = program_ref.lock().unwrap();
            guard.start(
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
    }

    #[test]
    fn test_state_factory_put() {
        let mut store = RustStore::new();
        let k = make_test_continuation();
        let program_ref = StateHandlerFactory.create_program();
        let step = {
            let mut guard = program_ref.lock().unwrap();
            guard.start(
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
    }

    #[test]
    fn test_state_factory_put_from_python_effect_object() {
        Python::attach(|py| {
            let mut store = RustStore::new();
            let k = make_test_continuation();
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class StatePutEffect:\n    def __init__(self):\n        self.key = 'key'\n        self.value = 77\nobj = StatePutEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::Python(PyShared::new(obj));

            let program_ref = StateHandlerFactory.create_program();
            let step = {
                let mut guard = program_ref.lock().unwrap();
                guard.start(effect, k, &mut store)
            };
            assert!(matches!(
                step,
                RustProgramStep::Yield(Yielded::DoCtrl(DoCtrl::Resume {
                    value: Value::Unit,
                    ..
                }))
            ));
            assert_eq!(store.get("key").unwrap().as_int(), Some(77));
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
                    assert_eq!(value.as_int(), Some(20)); // new_value returned (modifier result)
                }
                _ => panic!("Expected Yield(Resume) with new_value"),
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
    fn test_reader_factory_can_handle_python_ask_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class AskEffect:\n    def __init__(self):\n        self.key = 'cfg'\nobj = AskEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::Python(PyShared::new(obj));
            let f = ReaderHandlerFactory;
            assert!(
                f.can_handle(&effect),
                "SPEC GAP: reader handler should claim opaque Python ask effects"
            );
        });
    }

    #[test]
    fn test_reader_factory_ask() {
        let mut store = RustStore::new();
        store
            .env
            .insert("config".to_string(), Value::String("value".to_string()));
        let k = make_test_continuation();
        let program_ref = ReaderHandlerFactory.create_program();
        let step = {
            let mut guard = program_ref.lock().unwrap();
            guard.start(
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
    fn test_writer_factory_can_handle_python_tell_effect() {
        Python::attach(|py| {
            let locals = pyo3::types::PyDict::new(py);
            py.run(
                c"class WriterTellEffect:\n    def __init__(self):\n        self.message = 'log'\nobj = WriterTellEffect()\n",
                Some(&locals),
                Some(&locals),
            )
            .unwrap();
            let obj = locals.get_item("obj").unwrap().unwrap().unbind();
            let effect = Effect::Python(PyShared::new(obj));
            let f = WriterHandlerFactory;
            assert!(
                f.can_handle(&effect),
                "SPEC GAP: writer handler should claim opaque Python tell effects"
            );
        });
    }

    #[test]
    fn test_writer_factory_tell() {
        let mut store = RustStore::new();
        let k = make_test_continuation();
        let program_ref = WriterHandlerFactory.create_program();
        let step = {
            let mut guard = program_ref.lock().unwrap();
            guard.start(
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

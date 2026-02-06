//! Step state machine types and execution.

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::effect::Effect;
use crate::error::VMError;
use crate::frame::CallMetadata;
use crate::handler::Handler;
use crate::value::Value;

#[derive(Debug, Clone)]
pub struct PyException {
    pub exc_type: Py<PyAny>,
    pub exc_value: Py<PyAny>,
    pub exc_tb: Option<Py<PyAny>>,
}

#[derive(Debug, Clone)]
pub enum Mode {
    Deliver(Value),
    Throw(PyException),
    HandleYield(Yielded),
    Return(Value),
}

#[derive(Debug)]
pub enum StepEvent {
    Continue,
    NeedsPython(PythonCall),
    Done(Value),
    Error(VMError),
}

#[derive(Debug, Clone)]
pub enum PythonCall {
    StartProgram {
        program: Py<PyAny>,
    },
    CallFunc {
        func: Py<PyAny>,
        args: Vec<Value>,
        kwargs: Vec<(String, Value)>,
    },
    CallHandler {
        handler: Py<PyAny>,
        effect: Effect,
        continuation: Continuation,
    },
    GenNext {
        gen: Py<PyAny>,
    },
    GenSend {
        gen: Py<PyAny>,
        value: Value,
    },
    GenThrow {
        gen: Py<PyAny>,
        exc: Py<PyAny>,
    },
    CallAsync {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
}

#[derive(Debug, Clone)]
pub enum PendingPython {
    StartProgramFrame {
        metadata: Option<CallMetadata>,
    },
    StepUserGenerator {
        generator: Py<PyAny>,
        metadata: Option<CallMetadata>,
    },
    CallPythonHandler {
        k_user: Continuation,
        effect: Effect,
    },
    RustProgramContinuation {
        marker: crate::ids::Marker,
        k: crate::continuation::Continuation,
    },
    AsyncEscape,
}

#[derive(Debug, Clone)]
pub enum Yielded {
    DoCtrl(DoCtrl),
    Effect(Effect),
    Program(Py<PyAny>),
    Unknown(Py<PyAny>),
}

#[derive(Debug, Clone)]
pub enum DoCtrl {
    Resume {
        continuation: Continuation,
        value: Value,
    },
    Transfer {
        continuation: Continuation,
        value: Value,
    },
    WithHandler {
        handler: Handler,
        expr: Py<PyAny>,
    },
    Delegate {
        effect: Effect,
    },
    GetContinuation,
    GetHandlers,
    CreateContinuation {
        expr: Py<PyAny>,
        handlers: Vec<Handler>,
    },
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },
    PythonAsyncSyntaxEscape {
        action: Py<PyAny>,
    },
    Call {
        f: Py<PyAny>,
        args: Vec<Value>,
        kwargs: Vec<(String, Value)>,
        metadata: CallMetadata,
    },
    Eval {
        expr: Py<PyAny>,
        handlers: Vec<Handler>,
    },
    GetCallStack,
}

#[derive(Debug, Clone)]
pub enum PyCallOutcome {
    Value(Value),
    GenYield(Yielded),
    GenReturn(Value),
    GenError(PyException),
}

impl PyException {
    pub fn new(exc_type: Py<PyAny>, exc_value: Py<PyAny>, exc_tb: Option<Py<PyAny>>) -> Self {
        PyException {
            exc_type,
            exc_value,
            exc_tb,
        }
    }
}

impl Mode {
    pub fn deliver(value: impl Into<Value>) -> Self {
        Mode::Deliver(value.into())
    }

    pub fn return_value(value: impl Into<Value>) -> Self {
        Mode::Return(value.into())
    }

    pub fn is_deliver(&self) -> bool {
        matches!(self, Mode::Deliver(_))
    }

    pub fn is_throw(&self) -> bool {
        matches!(self, Mode::Throw(_))
    }

    pub fn is_return(&self) -> bool {
        matches!(self, Mode::Return(_))
    }
}

impl StepEvent {
    pub fn is_done(&self) -> bool {
        matches!(self, StepEvent::Done(_))
    }

    pub fn is_error(&self) -> bool {
        matches!(self, StepEvent::Error(_))
    }

    pub fn is_needs_python(&self) -> bool {
        matches!(self, StepEvent::NeedsPython(_))
    }
}

impl Yielded {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            Yielded::DoCtrl(p) => Yielded::DoCtrl(p.clone_ref(py)),
            Yielded::Effect(e) => Yielded::Effect(e.clone()),
            Yielded::Program(p) => Yielded::Program(p.clone_ref(py)),
            Yielded::Unknown(p) => Yielded::Unknown(p.clone_ref(py)),
        }
    }
}

impl DoCtrl {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            DoCtrl::Resume {
                continuation,
                value,
            } => DoCtrl::Resume {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            DoCtrl::Transfer {
                continuation,
                value,
            } => DoCtrl::Transfer {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            DoCtrl::WithHandler { handler, expr } => DoCtrl::WithHandler {
                handler: handler.clone(),
                expr: expr.clone_ref(py),
            },
            DoCtrl::Delegate { ref effect } => DoCtrl::Delegate {
                effect: effect.clone(),
            },
            DoCtrl::GetContinuation => DoCtrl::GetContinuation,
            DoCtrl::GetHandlers => DoCtrl::GetHandlers,
            DoCtrl::CreateContinuation { expr, handlers } => DoCtrl::CreateContinuation {
                expr: expr.clone_ref(py),
                handlers: handlers.clone(),
            },
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => DoCtrl::ResumeContinuation {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            DoCtrl::PythonAsyncSyntaxEscape { action } => DoCtrl::PythonAsyncSyntaxEscape {
                action: action.clone_ref(py),
            },
            DoCtrl::Call {
                f,
                args,
                kwargs,
                metadata,
            } => DoCtrl::Call {
                f: f.clone_ref(py),
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::Eval { expr, handlers } => DoCtrl::Eval {
                expr: expr.clone_ref(py),
                handlers: handlers.clone(),
            },
            DoCtrl::GetCallStack => DoCtrl::GetCallStack,
        }
    }
}

impl PyException {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        PyException {
            exc_type: self.exc_type.clone_ref(py),
            exc_value: self.exc_value.clone_ref(py),
            exc_tb: self.exc_tb.as_ref().map(|t| t.clone_ref(py)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_deliver() {
        let mode = Mode::deliver(42i64);
        assert!(mode.is_deliver());
        assert!(!mode.is_throw());
        assert!(!mode.is_return());
    }

    #[test]
    fn test_mode_return() {
        let mode = Mode::return_value("done");
        assert!(mode.is_return());
        assert!(!mode.is_deliver());
    }

    #[test]
    fn test_step_event_checks() {
        let done = StepEvent::Done(Value::Int(1));
        assert!(done.is_done());
        assert!(!done.is_error());

        let err = StepEvent::Error(VMError::internal("test"));
        assert!(err.is_error());
        assert!(!err.is_done());

        let cont = StepEvent::Continue;
        assert!(!cont.is_done());
        assert!(!cont.is_error());
    }

    /// G1-G3: DoCtrl uses spec field names `continuation` (not `k`)
    /// and `program` (not `body`).
    #[test]
    fn test_do_ctrl_spec_field_names() {
        use crate::continuation::Continuation;
        use crate::ids::{ContId, Marker, SegmentId};

        let k = Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId::from_index(0),
            frames_snapshot: std::sync::Arc::new(Vec::new()),
            scope_chain: std::sync::Arc::new(Vec::new()),
            marker: Marker::fresh(),
            dispatch_id: None,
            started: true,
            program: None,
            handlers: Vec::new(),
        };

        // Resume uses `continuation` field
        let resume = DoCtrl::Resume {
            continuation: k.clone(),
            value: Value::Int(1),
        };
        match resume {
            DoCtrl::Resume {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(1));
            }
            _ => panic!("Expected Resume"),
        }

        // Transfer uses `continuation` field
        let transfer = DoCtrl::Transfer {
            continuation: k.clone(),
            value: Value::Int(2),
        };
        match transfer {
            DoCtrl::Transfer {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(2));
            }
            _ => panic!("Expected Transfer"),
        }

        // ResumeContinuation uses `continuation` field
        let resume_cont = DoCtrl::ResumeContinuation {
            continuation: k.clone(),
            value: Value::Int(3),
        };
        match resume_cont {
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(3));
            }
            _ => panic!("Expected ResumeContinuation"),
        }
    }

    /// G4: Pure variant should not exist in DoCtrl.
    /// This test verifies that DoCtrl has exactly the spec variants.
    #[test]
    fn test_do_ctrl_no_pure_variant() {
        // This is a compile-time check. If Pure existed, this match would
        // be non-exhaustive. Since we removed it, this compiles.
        let prim = DoCtrl::GetContinuation;
        match prim {
            DoCtrl::Resume { .. } => {}
            DoCtrl::Transfer { .. } => {}
            DoCtrl::WithHandler { .. } => {}
            DoCtrl::Delegate { .. } => {}
            DoCtrl::GetContinuation => {}
            DoCtrl::GetHandlers => {}
            DoCtrl::CreateContinuation { .. } => {}
            DoCtrl::ResumeContinuation { .. } => {}
            DoCtrl::PythonAsyncSyntaxEscape { .. } => {}
            DoCtrl::Call { .. } => {}
            DoCtrl::Eval { .. } => {}
            DoCtrl::GetCallStack => {}
        }
    }
}

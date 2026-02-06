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
    Primitive(ControlPrimitive),
    Effect(Effect),
    Program(Py<PyAny>),
    Unknown(Py<PyAny>),
}

#[derive(Debug, Clone)]
pub enum ControlPrimitive {
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
        program: Py<PyAny>,
    },
    Delegate {
        effect: Effect,
    },
    GetContinuation,
    GetHandlers,
    CreateContinuation {
        program: Py<PyAny>,
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
        program: Py<PyAny>,
        metadata: CallMetadata,
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
            Yielded::Primitive(p) => Yielded::Primitive(p.clone_ref(py)),
            Yielded::Effect(e) => Yielded::Effect(e.clone()),
            Yielded::Program(p) => Yielded::Program(p.clone_ref(py)),
            Yielded::Unknown(p) => Yielded::Unknown(p.clone_ref(py)),
        }
    }
}

impl ControlPrimitive {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            ControlPrimitive::Resume {
                continuation,
                value,
            } => ControlPrimitive::Resume {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            ControlPrimitive::Transfer {
                continuation,
                value,
            } => ControlPrimitive::Transfer {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            ControlPrimitive::WithHandler { handler, program } => ControlPrimitive::WithHandler {
                handler: handler.clone(),
                program: program.clone_ref(py),
            },
            ControlPrimitive::Delegate { ref effect } => ControlPrimitive::Delegate {
                effect: effect.clone(),
            },
            ControlPrimitive::GetContinuation => ControlPrimitive::GetContinuation,
            ControlPrimitive::GetHandlers => ControlPrimitive::GetHandlers,
            ControlPrimitive::CreateContinuation { program, handlers } => {
                ControlPrimitive::CreateContinuation {
                    program: program.clone_ref(py),
                    handlers: handlers.clone(),
                }
            }
            ControlPrimitive::ResumeContinuation {
                continuation,
                value,
            } => ControlPrimitive::ResumeContinuation {
                continuation: continuation.clone(),
                value: value.clone(),
            },
            ControlPrimitive::PythonAsyncSyntaxEscape { action } => {
                ControlPrimitive::PythonAsyncSyntaxEscape {
                    action: action.clone_ref(py),
                }
            }
            ControlPrimitive::Call { program, metadata } => ControlPrimitive::Call {
                program: program.clone_ref(py),
                metadata: metadata.clone(),
            },
            ControlPrimitive::GetCallStack => ControlPrimitive::GetCallStack,
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

    /// G1-G3: ControlPrimitive uses spec field names `continuation` (not `k`)
    /// and `program` (not `body`).
    #[test]
    fn test_control_primitive_spec_field_names() {
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
        let resume = ControlPrimitive::Resume {
            continuation: k.clone(),
            value: Value::Int(1),
        };
        match resume {
            ControlPrimitive::Resume {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(1));
            }
            _ => panic!("Expected Resume"),
        }

        // Transfer uses `continuation` field
        let transfer = ControlPrimitive::Transfer {
            continuation: k.clone(),
            value: Value::Int(2),
        };
        match transfer {
            ControlPrimitive::Transfer {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(2));
            }
            _ => panic!("Expected Transfer"),
        }

        // ResumeContinuation uses `continuation` field
        let resume_cont = ControlPrimitive::ResumeContinuation {
            continuation: k.clone(),
            value: Value::Int(3),
        };
        match resume_cont {
            ControlPrimitive::ResumeContinuation {
                continuation,
                value,
            } => {
                assert_eq!(continuation.cont_id, k.cont_id);
                assert_eq!(value.as_int(), Some(3));
            }
            _ => panic!("Expected ResumeContinuation"),
        }
    }

    /// G4: Pure variant should not exist in ControlPrimitive.
    /// This test verifies that ControlPrimitive has exactly the spec variants.
    #[test]
    fn test_control_primitive_no_pure_variant() {
        // This is a compile-time check. If Pure existed, this match would
        // be non-exhaustive. Since we removed it, this compiles.
        let prim = ControlPrimitive::GetContinuation;
        match prim {
            ControlPrimitive::Resume { .. } => {}
            ControlPrimitive::Transfer { .. } => {}
            ControlPrimitive::WithHandler { .. } => {}
            ControlPrimitive::Delegate { .. } => {}
            ControlPrimitive::GetContinuation => {}
            ControlPrimitive::GetHandlers => {}
            ControlPrimitive::CreateContinuation { .. } => {}
            ControlPrimitive::ResumeContinuation { .. } => {}
            ControlPrimitive::PythonAsyncSyntaxEscape { .. } => {}
            ControlPrimitive::Call { .. } => {}
            ControlPrimitive::GetCallStack => {}
        }
    }
}

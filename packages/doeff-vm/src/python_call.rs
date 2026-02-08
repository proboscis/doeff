//! Python bridge call protocol.

use crate::continuation::Continuation;
use crate::driver::PyException;
use crate::effect::DispatchEffect;
use crate::frame::CallMetadata;
use crate::ids::Marker;
use crate::py_shared::PyShared;
use crate::value::Value;
use crate::yielded::Yielded;

#[derive(Debug, Clone)]
pub enum PythonCall {
    StartProgram {
        program: PyShared,
    },
    CallFunc {
        func: PyShared,
        args: Vec<Value>,
        kwargs: Vec<(String, Value)>,
    },
    CallHandler {
        handler: PyShared,
        effect: DispatchEffect,
        continuation: Continuation,
    },
    GenNext,
    GenSend {
        value: Value,
    },
    GenThrow {
        exc: PyException,
    },
    CallAsync {
        func: PyShared,
        args: Vec<Value>,
    },
}

#[derive(Debug, Clone)]
pub enum PendingPython {
    StartProgramFrame {
        metadata: Option<CallMetadata>,
    },
    CallFuncReturn,
    StepUserGenerator {
        generator: PyShared,
        metadata: Option<CallMetadata>,
    },
    CallPythonHandler {
        k_user: Continuation,
        effect: DispatchEffect,
    },
    RustProgramContinuation {
        marker: Marker,
        k: Continuation,
    },
    AsyncEscape,
}

#[derive(Debug, Clone)]
pub enum PyCallOutcome {
    Value(Value),
    GenYield(Yielded),
    GenReturn(Value),
    GenError(PyException),
}

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
    CallFuncReturn {
        metadata: Option<CallMetadata>,
    },
    StepUserGenerator {
        generator: PyShared,
        metadata: Option<CallMetadata>,
        get_frame: PyShared,
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

#[cfg(test)]
mod tests {
    #[test]
    fn test_vm_proto_pending_step_user_generator_has_get_frame_field() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/python_call.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("StepUserGenerator {")
                && runtime_src.contains("get_frame: PyShared"),
            "VM-PROTO-001: PendingPython::StepUserGenerator must carry get_frame callback"
        );
    }
}

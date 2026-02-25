//! Python bridge call protocol.

use crate::ast_stream::ASTStreamRef;
use crate::continuation::Continuation;
use crate::do_ctrl::DoCtrl;
use crate::driver::PyException;
use crate::frame::CallMetadata;
use crate::ids::Marker;
use crate::py_shared::PyShared;
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum PythonCall {
    EvalExpr {
        expr: PyShared,
    },
    CallFunc {
        func: PyShared,
        args: Vec<Value>,
        kwargs: Vec<(String, Value)>,
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
    EvalExpr {
        metadata: Option<CallMetadata>,
    },
    CallFuncReturn {
        metadata: Option<CallMetadata>,
        evaluate_result: bool,
    },
    StepUserGenerator {
        stream: ASTStreamRef,
        metadata: Option<CallMetadata>,
    },
    ExpandReturn {
        metadata: Option<CallMetadata>,
        handler_return: bool,
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
    GenYield(DoCtrl),
    GenReturn(Value),
    GenError(PyException),
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_vm_proto_pending_step_user_generator_has_stream_field() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/python_call.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("StepUserGenerator {")
                && runtime_src.contains("stream: ASTStreamRef"),
            "VM-PROTO-001: PendingPython::StepUserGenerator must carry ASTStreamRef"
        );
    }
}

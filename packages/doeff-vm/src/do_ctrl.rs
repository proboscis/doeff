//! DoCtrl primitives.

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::driver::PyException;
use crate::effect::DispatchEffect;
use crate::frame::CallMetadata;
use crate::handler::Handler;
use crate::py_shared::PyShared;
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum CallArg {
    Value(Value),
    Expr(PyShared),
}

#[derive(Debug, Clone)]
pub enum DoCtrl {
    Pure {
        value: Value,
    },
    Map {
        source: PyShared,
        mapper: PyShared,
        mapper_meta: CallMetadata,
    },
    FlatMap {
        source: PyShared,
        binder: PyShared,
        binder_meta: CallMetadata,
    },
    Perform {
        effect: DispatchEffect,
    },
    Resume {
        continuation: Continuation,
        value: Value,
    },
    Transfer {
        continuation: Continuation,
        value: Value,
    },
    TransferThrow {
        continuation: Continuation,
        exception: PyException,
    },
    WithHandler {
        handler: Handler,
        expr: Py<PyAny>,
        py_identity: Option<PyShared>,
    },
    Delegate {
        effect: DispatchEffect,
    },
    Pass {
        effect: DispatchEffect,
    },
    GetContinuation,
    GetHandlers,
    CreateContinuation {
        expr: PyShared,
        handlers: Vec<Handler>,
        handler_identities: Vec<Option<PyShared>>,
    },
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },
    PythonAsyncSyntaxEscape {
        action: Py<PyAny>,
    },
    Apply {
        f: CallArg,
        args: Vec<CallArg>,
        kwargs: Vec<(String, CallArg)>,
        metadata: CallMetadata,
    },
    Expand {
        factory: CallArg,
        args: Vec<CallArg>,
        kwargs: Vec<(String, CallArg)>,
        metadata: CallMetadata,
    },
    Eval {
        expr: PyShared,
        handlers: Vec<Handler>,
        metadata: Option<CallMetadata>,
    },
    GetCallStack,
    GetTrace,
}

impl DoCtrl {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            DoCtrl::Pure { value } => DoCtrl::Pure {
                value: value.clone(),
            },
            DoCtrl::Map {
                source,
                mapper,
                mapper_meta,
            } => DoCtrl::Map {
                source: source.clone(),
                mapper: mapper.clone(),
                mapper_meta: mapper_meta.clone(),
            },
            DoCtrl::FlatMap {
                source,
                binder,
                binder_meta,
            } => DoCtrl::FlatMap {
                source: source.clone(),
                binder: binder.clone(),
                binder_meta: binder_meta.clone(),
            },
            DoCtrl::Perform { effect } => DoCtrl::Perform {
                effect: effect.clone(),
            },
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
            DoCtrl::TransferThrow {
                continuation,
                exception,
            } => DoCtrl::TransferThrow {
                continuation: continuation.clone(),
                exception: exception.clone_ref(py),
            },
            DoCtrl::WithHandler {
                handler,
                expr,
                py_identity,
            } => DoCtrl::WithHandler {
                handler: handler.clone(),
                expr: expr.clone_ref(py),
                py_identity: py_identity.clone(),
            },
            DoCtrl::Delegate { effect } => DoCtrl::Delegate {
                effect: effect.clone(),
            },
            DoCtrl::Pass { effect } => DoCtrl::Pass {
                effect: effect.clone(),
            },
            DoCtrl::GetContinuation => DoCtrl::GetContinuation,
            DoCtrl::GetHandlers => DoCtrl::GetHandlers,
            DoCtrl::CreateContinuation {
                expr,
                handlers,
                handler_identities,
            } => DoCtrl::CreateContinuation {
                expr: PyShared::new(expr.clone_ref(py)),
                handlers: handlers.clone(),
                handler_identities: handler_identities.clone(),
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
            DoCtrl::Apply {
                f,
                args,
                kwargs,
                metadata,
            } => DoCtrl::Apply {
                f: f.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::Expand {
                factory,
                args,
                kwargs,
                metadata,
            } => DoCtrl::Expand {
                factory: factory.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::Eval {
                expr,
                handlers,
                metadata,
            } => DoCtrl::Eval {
                expr: PyShared::new(expr.clone_ref(py)),
                handlers: handlers.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::GetCallStack => DoCtrl::GetCallStack,
            DoCtrl::GetTrace => DoCtrl::GetTrace,
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_vm_proto_005_map_variant_includes_mapper_meta() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/do_ctrl.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("mapper_meta: CallMetadata"),
            "VM-PROTO-005: DoCtrl::Map must carry mapper_meta: CallMetadata"
        );
    }

    #[test]
    fn test_vm_proto_005_flat_map_variant_includes_binder_meta() {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/do_ctrl.rs"));
        let runtime_src = src.split("#[cfg(test)]").next().unwrap_or(src);
        assert!(
            runtime_src.contains("binder_meta: CallMetadata"),
            "VM-PROTO-005: DoCtrl::FlatMap must carry binder_meta: CallMetadata"
        );
    }
}

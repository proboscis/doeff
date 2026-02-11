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
pub enum DoCtrl {
    Pure {
        value: Value,
    },
    Map {
        source: PyShared,
        mapper: PyShared,
    },
    FlatMap {
        source: PyShared,
        binder: PyShared,
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
    Call {
        f: PyShared,
        args: Vec<Value>,
        kwargs: Vec<(String, Value)>,
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
            DoCtrl::Map { source, mapper } => DoCtrl::Map {
                source: source.clone(),
                mapper: mapper.clone(),
            },
            DoCtrl::FlatMap { source, binder } => DoCtrl::FlatMap {
                source: source.clone(),
                binder: binder.clone(),
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
            DoCtrl::Call {
                f,
                args,
                kwargs,
                metadata,
            } => DoCtrl::Call {
                f: f.clone(),
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

//! DoCtrl primitives.

use std::collections::HashMap;

use pyo3::exceptions::{PyRuntimeError, PyStopIteration};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::continuation::Continuation;
use crate::driver::PyException;
use crate::effect::DispatchEffect;
use crate::frame::CallMetadata;
use crate::ids::{SegmentId, VarId};
use crate::ir_stream::IRStreamRef;
use crate::kleisli::KleisliRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::value::Value;

/// Discriminant stored as `tag: u8` on control/effect base classes.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoExprTag {
    Pure = 0,
    Map = 2,
    FlatMap = 3,
    WithHandler = 4,
    Perform = 5,
    Resume = 6,
    Transfer = 7,
    Delegate = 8,
    GetContinuation = 9,
    GetHandlers = 10,
    GetCallStack = 11,
    Eval = 12,
    CreateContinuation = 13,
    ResumeContinuation = 14,
    AsyncEscape = 15,
    Apply = 16,
    Expand = 17,
    Pass = 19,
    GetTraceback = 20,
    WithIntercept = 21,
    Discontinue = 22,
    EvalInScope = 23,
    AllocVar = 24,
    ReadVar = 25,
    WriteVar = 26,
    WriteVarNonlocal = 27,
    Effect = 128,
    Unknown = 255,
}

impl TryFrom<u8> for DoExprTag {
    type Error = u8;

    fn try_from(v: u8) -> Result<Self, u8> {
        match v {
            0 => Ok(DoExprTag::Pure),
            2 => Ok(DoExprTag::Map),
            3 => Ok(DoExprTag::FlatMap),
            4 => Ok(DoExprTag::WithHandler),
            5 => Ok(DoExprTag::Perform),
            6 => Ok(DoExprTag::Resume),
            7 => Ok(DoExprTag::Transfer),
            8 => Ok(DoExprTag::Delegate),
            9 => Ok(DoExprTag::GetContinuation),
            10 => Ok(DoExprTag::GetHandlers),
            11 => Ok(DoExprTag::GetCallStack),
            12 => Ok(DoExprTag::Eval),
            13 => Ok(DoExprTag::CreateContinuation),
            14 => Ok(DoExprTag::ResumeContinuation),
            15 => Ok(DoExprTag::AsyncEscape),
            16 => Ok(DoExprTag::Apply),
            17 => Ok(DoExprTag::Expand),
            19 => Ok(DoExprTag::Pass),
            20 => Ok(DoExprTag::GetTraceback),
            21 => Ok(DoExprTag::WithIntercept),
            22 => Ok(DoExprTag::Discontinue),
            23 => Ok(DoExprTag::EvalInScope),
            24 => Ok(DoExprTag::AllocVar),
            25 => Ok(DoExprTag::ReadVar),
            26 => Ok(DoExprTag::WriteVar),
            27 => Ok(DoExprTag::WriteVarNonlocal),
            128 => Ok(DoExprTag::Effect),
            255 => Ok(DoExprTag::Unknown),
            other => Err(other),
        }
    }
}

#[pyclass(subclass, frozen, name = "DoExpr")]
pub struct PyDoExprBase;

impl PyDoExprBase {
    fn new_base() -> Self {
        PyDoExprBase
    }
}

#[pymethods]
impl PyDoExprBase {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>) -> Self {
        PyDoExprBase::new_base()
    }

    fn to_generator(slf: Py<Self>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let expr = slf.into_any();
        let gen = Bound::new(
            py,
            DoExprOnceGenerator {
                expr: Some(expr),
                done: false,
            },
        )?
        .into_any()
        .unbind();
        Ok(gen)
    }
}

#[pyclass(name = "_DoExprOnceGenerator")]
struct DoExprOnceGenerator {
    expr: Option<Py<PyAny>>,
    done: bool,
}

#[pymethods]
impl DoExprOnceGenerator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.done {
            return Ok(None);
        }
        self.done = true;
        let expr = self
            .expr
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("DoExprOnceGenerator already consumed"))?;
        let _ = py;
        Ok(Some(expr))
    }

    fn send(&mut self, py: Python<'_>, value: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !self.done {
            return match self.__next__(py)? {
                Some(v) => Ok(v),
                None => Err(PyStopIteration::new_err(py.None())),
            };
        }
        Err(PyStopIteration::new_err((value,)))
    }

    fn throw(&mut self, _py: Python<'_>, exc: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyErr::from_value(exc))
    }
}

#[pyclass(subclass, frozen, extends=PyDoExprBase, name = "DoCtrlBase")]
pub struct PyDoCtrlBase {
    #[pyo3(get)]
    pub tag: u8,
}

#[pymethods]
impl PyDoCtrlBase {
    #[new]
    pub fn new() -> PyClassInitializer<Self> {
        PyClassInitializer::from(PyDoExprBase).add_subclass(PyDoCtrlBase {
            tag: DoExprTag::Unknown as u8,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterceptMode {
    Include,
    Exclude,
}

impl InterceptMode {
    pub fn from_str(mode: &str) -> Option<Self> {
        match mode {
            "include" => Some(Self::Include),
            "exclude" => Some(Self::Exclude),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Include => "include",
            Self::Exclude => "exclude",
        }
    }

    pub fn should_invoke(self, matches_filter: bool) -> bool {
        match self {
            Self::Include => matches_filter,
            Self::Exclude => !matches_filter,
        }
    }
}

#[derive(Debug)]
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
    ResumeThrow {
        continuation: Continuation,
        exception: PyException,
    },
    WithHandler {
        handler: KleisliRef,
        body: Box<DoCtrl>,
        types: Option<Vec<PyShared>>,
    },
    WithIntercept {
        interceptor: KleisliRef,
        body: Box<DoCtrl>,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    },
    Discontinue {
        continuation: Continuation,
        exception: PyException,
    },
    Delegate {
        effect: DispatchEffect,
    },
    Pass {
        effect: DispatchEffect,
    },
    GetContinuation,
    GetHandlers,
    GetTraceback {
        continuation: Continuation,
    },
    CreateContinuation {
        expr: PyShared,
        handlers: Vec<KleisliRef>,
        handler_identities: Vec<Option<PyShared>>,
        outside_scope: Option<SegmentId>,
    },
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },
    PythonAsyncSyntaxEscape {
        action: PyShared,
    },
    Apply {
        f: Box<DoCtrl>,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    },
    Expand {
        factory: Box<DoCtrl>,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
    },
    IRStream {
        stream: IRStreamRef,
        metadata: Option<CallMetadata>,
    },
    Eval {
        expr: PyShared,
        metadata: Option<CallMetadata>,
    },
    EvalInScope {
        expr: PyShared,
        scope: Continuation,
        bindings: HashMap<HashedPyKey, Value>,
        metadata: Option<CallMetadata>,
    },
    AllocVar {
        initial: Value,
    },
    ReadVar {
        var: VarId,
    },
    WriteVar {
        var: VarId,
        value: Value,
    },
    WriteVarNonlocal {
        var: VarId,
        value: Value,
    },
    ReadHandlerState {
        key: String,
        missing_is_none: bool,
    },
    WriteHandlerState {
        key: String,
        value: Value,
    },
    AppendHandlerLog {
        message: Value,
    },
    // DEPRECATED (INTROSPECT-UNIFY-001): use GetExecutionContext for handler-aware introspection.
    GetCallStack,
}

impl Clone for DoCtrl {
    fn clone(&self) -> Self {
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
                continuation: continuation.clone_handle(),
                value: value.clone(),
            },
            DoCtrl::Transfer {
                continuation,
                value,
            } => DoCtrl::Transfer {
                continuation: continuation.clone_handle(),
                value: value.clone(),
            },
            DoCtrl::TransferThrow {
                continuation,
                exception,
            } => DoCtrl::TransferThrow {
                continuation: continuation.clone_handle(),
                exception: exception.clone(),
            },
            DoCtrl::ResumeThrow {
                continuation,
                exception,
            } => DoCtrl::ResumeThrow {
                continuation: continuation.clone_handle(),
                exception: exception.clone(),
            },
            DoCtrl::WithHandler {
                handler,
                body,
                types,
            } => DoCtrl::WithHandler {
                handler: handler.clone(),
                body: body.clone(),
                types: types.clone(),
            },
            DoCtrl::WithIntercept {
                interceptor,
                body,
                types,
                mode,
                metadata,
            } => DoCtrl::WithIntercept {
                interceptor: interceptor.clone(),
                body: body.clone(),
                types: types.clone(),
                mode: *mode,
                metadata: metadata.clone(),
            },
            DoCtrl::Discontinue {
                continuation,
                exception,
            } => DoCtrl::Discontinue {
                continuation: continuation.clone_handle(),
                exception: exception.clone(),
            },
            DoCtrl::Delegate { effect } => DoCtrl::Delegate {
                effect: effect.clone(),
            },
            DoCtrl::Pass { effect } => DoCtrl::Pass {
                effect: effect.clone(),
            },
            DoCtrl::GetContinuation => DoCtrl::GetContinuation,
            DoCtrl::GetHandlers => DoCtrl::GetHandlers,
            DoCtrl::GetTraceback { continuation } => DoCtrl::GetTraceback {
                continuation: continuation.clone_handle(),
            },
            DoCtrl::CreateContinuation {
                expr,
                handlers,
                handler_identities,
                outside_scope,
            } => DoCtrl::CreateContinuation {
                expr: expr.clone(),
                handlers: handlers.clone(),
                handler_identities: handler_identities.clone(),
                outside_scope: *outside_scope,
            },
            DoCtrl::ResumeContinuation {
                continuation,
                value,
            } => DoCtrl::ResumeContinuation {
                continuation: continuation.clone_handle(),
                value: value.clone(),
            },
            DoCtrl::PythonAsyncSyntaxEscape { action } => DoCtrl::PythonAsyncSyntaxEscape {
                action: action.clone(),
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
            DoCtrl::IRStream { stream, metadata } => DoCtrl::IRStream {
                stream: stream.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::Eval { expr, metadata } => DoCtrl::Eval {
                expr: expr.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::EvalInScope {
                expr,
                scope,
                bindings,
                metadata,
            } => DoCtrl::EvalInScope {
                expr: expr.clone(),
                scope: scope.clone_handle(),
                bindings: bindings.clone(),
                metadata: metadata.clone(),
            },
            DoCtrl::AllocVar { initial } => DoCtrl::AllocVar {
                initial: initial.clone(),
            },
            DoCtrl::ReadVar { var } => DoCtrl::ReadVar { var: *var },
            DoCtrl::WriteVar { var, value } => DoCtrl::WriteVar {
                var: *var,
                value: value.clone(),
            },
            DoCtrl::WriteVarNonlocal { var, value } => DoCtrl::WriteVarNonlocal {
                var: *var,
                value: value.clone(),
            },
            DoCtrl::ReadHandlerState {
                key,
                missing_is_none,
            } => DoCtrl::ReadHandlerState {
                key: key.clone(),
                missing_is_none: *missing_is_none,
            },
            DoCtrl::WriteHandlerState { key, value } => DoCtrl::WriteHandlerState {
                key: key.clone(),
                value: value.clone(),
            },
            DoCtrl::AppendHandlerLog { message } => DoCtrl::AppendHandlerLog {
                message: message.clone(),
            },
            DoCtrl::GetCallStack => DoCtrl::GetCallStack,
        }
    }
}

impl DoCtrl {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        let _ = py;
        self.clone()
    }
}

#[cfg(test)]
mod tests {
    fn runtime_src() -> &'static str {
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/do_ctrl.rs"));
        src.split("#[cfg(test)]").next().unwrap_or(src)
    }

    fn doctrl_enum_body(src: &str) -> &str {
        let enum_start = src
            .find("pub enum DoCtrl")
            .expect("DoCtrl enum definition must exist");
        let enum_src = &src[enum_start..];
        let open_rel = enum_src.find('{').expect("DoCtrl enum must contain '{'");
        let body_start = enum_start + open_rel + 1;

        let mut depth = 1usize;
        for (idx, ch) in src[body_start..].char_indices() {
            match ch {
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        let body_end = body_start + idx;
                        return &src[body_start..body_end];
                    }
                }
                _ => {}
            }
        }

        panic!("DoCtrl enum body must be balanced");
    }

    fn count_top_level_variants(enum_body: &str) -> usize {
        let mut depth = 0usize;
        let mut count = 0usize;

        for line in enum_body.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with("//") {
                continue;
            }

            if depth == 0 {
                let starts_with_identifier = trimmed
                    .chars()
                    .next()
                    .is_some_and(|ch| ch.is_ascii_alphabetic() || ch == '_');
                if starts_with_identifier {
                    count += 1;
                }
            }

            for ch in trimmed.chars() {
                match ch {
                    '{' => depth += 1,
                    '}' => depth -= 1,
                    _ => {}
                }
            }
        }

        count
    }

    #[test]
    fn test_vm_proto_005_map_variant_includes_mapper_meta() {
        assert!(
            runtime_src().contains("mapper_meta: CallMetadata"),
            "VM-PROTO-005: DoCtrl::Map must carry mapper_meta: CallMetadata"
        );
    }

    #[test]
    fn test_vm_proto_005_flat_map_variant_includes_binder_meta() {
        assert!(
            runtime_src().contains("binder_meta: CallMetadata"),
            "VM-PROTO-005: DoCtrl::FlatMap must carry binder_meta: CallMetadata"
        );
    }

    #[test]
    fn test_doctrl_does_not_include_resume_then_transfer() {
        let removed_variant_name = ["ResumeThen", "Transfer"].concat();
        assert!(
            !runtime_src().contains(&removed_variant_name),
            "removed legacy variant must not exist in DoCtrl enum"
        );
    }

    #[test]
    fn test_doctrl_includes_discontinue() {
        assert!(
            runtime_src().contains("Discontinue"),
            "Discontinue must exist in DoCtrl enum"
        );
    }

    #[test]
    fn test_doctrl_variant_count_guard() {
        // DoCtrl is a controlled API surface. New variants require human approval.
        // Do NOT bump this number without discussing with the maintainer.
        let src = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/do_ctrl.rs"));
        let enum_body = doctrl_enum_body(src);
        let variant_count = count_top_level_variants(enum_body);
        assert_eq!(
            variant_count, 32,
            "DoCtrl variant count changed! New variants require explicit human approval."
        );
    }
}

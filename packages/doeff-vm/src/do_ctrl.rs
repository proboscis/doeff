//! DoCtrl primitives.

use pyo3::prelude::*;

use crate::continuation::Continuation;
use crate::driver::PyException;
use crate::effect::DispatchEffect;
use crate::frame::CallMetadata;
use crate::ir_stream::IRStreamRef;
use crate::kleisli::KleisliRef;
use crate::py_shared::PyShared;
use crate::value::Value;

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
    ResumeThrow {
        continuation: Continuation,
        exception: PyException,
    },
    WithHandler {
        handler: KleisliRef,
        body: Box<DoCtrl>,
        types: Option<Vec<PyShared>>,
        return_clause: Option<PyShared>,
    },
    WithIntercept {
        interceptor: KleisliRef,
        body: Box<DoCtrl>,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    },
    Finally {
        cleanup: PyShared,
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
    },
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },
    PythonAsyncSyntaxEscape {
        action: Py<PyAny>,
    },
    Apply {
        f: Box<DoCtrl>,
        args: Vec<DoCtrl>,
        kwargs: Vec<(String, DoCtrl)>,
        metadata: CallMetadata,
        evaluate_result: bool,
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
        handlers: Vec<KleisliRef>,
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
            DoCtrl::ResumeThrow {
                continuation,
                exception,
            } => DoCtrl::ResumeThrow {
                continuation: continuation.clone(),
                exception: exception.clone_ref(py),
            },
            DoCtrl::WithHandler {
                handler,
                body,
                types,
                return_clause,
            } => DoCtrl::WithHandler {
                handler: handler.clone(),
                body: body.clone(),
                types: types.clone(),
                return_clause: return_clause.clone(),
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
            DoCtrl::Finally { cleanup } => DoCtrl::Finally {
                cleanup: cleanup.clone(),
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
                continuation: continuation.clone(),
            },
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
                evaluate_result,
            } => DoCtrl::Apply {
                f: f.clone(),
                args: args.clone(),
                kwargs: kwargs.clone(),
                metadata: metadata.clone(),
                evaluate_result: *evaluate_result,
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
    fn test_doctrl_includes_resume_throw() {
        assert!(
            runtime_src().contains("ResumeThrow"),
            "ResumeThrow must exist in DoCtrl enum"
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
            variant_count, 25,
            "DoCtrl variant count changed! New variants require explicit human approval."
        );
    }
}

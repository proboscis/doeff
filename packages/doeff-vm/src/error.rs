//! Error types for the VM.

use crate::capture::{ActiveChainEntry, TraceEntry};
use crate::effect::DispatchEffect;
use crate::ids::{ContId, Marker};
use crate::step::PyException;

#[derive(Debug, Clone)]
pub enum VMError {
    OneShotViolation {
        cont_id: ContId,
    },
    UnhandledEffect {
        effect: DispatchEffect,
    },
    NoMatchingHandler {
        effect: DispatchEffect,
    },
    DelegateNoOuterHandler {
        effect: DispatchEffect,
    },
    HandlerNotFound {
        marker: Marker,
    },
    InvalidSegment {
        message: String,
    },
    PythonError {
        message: String,
    },
    InternalError {
        message: String,
    },
    TypeError {
        message: String,
    },
    UncaughtException {
        exception: PyException,
        trace: Vec<TraceEntry>,
        active_chain: Vec<ActiveChainEntry>,
    },
}

impl std::fmt::Display for VMError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VMError::OneShotViolation { cont_id } => {
                write!(
                    f,
                    "one-shot violation: continuation {} already consumed",
                    cont_id.raw()
                )
            }
            VMError::UnhandledEffect { effect } => {
                write!(f, "unhandled effect: {:?}", effect)
            }
            VMError::NoMatchingHandler { effect } => {
                write!(f, "no matching handler for effect: {:?}", effect)
            }
            VMError::DelegateNoOuterHandler { effect } => {
                write!(f, "delegate: no outer handler for effect: {:?}", effect)
            }
            VMError::HandlerNotFound { marker } => {
                write!(f, "handler not found for marker {}", marker.raw())
            }
            VMError::InvalidSegment { message } => write!(f, "invalid segment: {}", message),
            VMError::PythonError { message } => write!(f, "Python error: {}", message),
            VMError::InternalError { message } => write!(f, "internal error: {}", message),
            VMError::TypeError { message } => write!(f, "type error: {}", message),
            VMError::UncaughtException { .. } => write!(f, "uncaught exception"),
        }
    }
}

impl std::error::Error for VMError {}

impl VMError {
    pub fn one_shot_violation(cont_id: ContId) -> Self {
        VMError::OneShotViolation { cont_id }
    }

    pub fn unhandled_effect(effect: DispatchEffect) -> Self {
        VMError::UnhandledEffect { effect }
    }

    pub fn no_matching_handler(effect: DispatchEffect) -> Self {
        VMError::NoMatchingHandler { effect }
    }

    pub fn delegate_no_outer_handler(effect: DispatchEffect) -> Self {
        VMError::DelegateNoOuterHandler { effect }
    }

    pub fn handler_not_found(marker: Marker) -> Self {
        VMError::HandlerNotFound { marker }
    }

    pub fn invalid_segment(message: impl Into<String>) -> Self {
        VMError::InvalidSegment {
            message: message.into(),
        }
    }

    pub fn python_error(message: impl Into<String>) -> Self {
        VMError::PythonError {
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        VMError::InternalError {
            message: message.into(),
        }
    }

    pub fn type_error(message: impl Into<String>) -> Self {
        VMError::TypeError {
            message: message.into(),
        }
    }

    pub fn uncaught_exception(
        exception: PyException,
        trace: Vec<TraceEntry>,
        active_chain: Vec<ActiveChainEntry>,
    ) -> Self {
        VMError::UncaughtException {
            exception,
            trace,
            active_chain,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display() {
        let err = VMError::one_shot_violation(ContId::fresh());
        assert!(err.to_string().contains("one-shot violation"));

        let err = VMError::python_error("test error");
        assert!(err.to_string().contains("Python error: test error"));
    }
}

//! Dispatch-facing effect aliases re-exported for compatibility.

pub use crate::effect::{
    dispatch_from_shared, dispatch_into_python, dispatch_ref_as_python, dispatch_to_pyobject,
    make_execution_context_object, make_get_execution_context_effect, DispatchEffect, Effect,
    PyEffectBase, PyExecutionContext, PyGetExecutionContext,
};

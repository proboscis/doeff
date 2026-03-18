pub mod effects;
pub mod handlers;
pub mod scheduler;
pub mod sentinels;

pub mod continuation {
    pub use doeff_vm_core::continuation::*;
}
pub mod do_ctrl {
    pub use doeff_vm_core::do_ctrl::*;
}
pub mod doeff_generator {
    pub use doeff_vm_core::doeff_generator::*;
}
pub mod capture {
    pub use doeff_vm_core::capture::*;
}
pub mod error {
    pub use doeff_vm_core::error::*;
}
pub mod frame {
    pub use doeff_vm_core::frame::*;
}
pub mod ids {
    pub use doeff_vm_core::ids::*;
}
pub mod ir_stream {
    pub use doeff_vm_core::ir_stream::*;
}
pub mod kleisli {
    pub use doeff_vm_core::kleisli::*;
}
pub mod py_key {
    pub use doeff_vm_core::py_key::*;
}
pub mod py_shared {
    pub use doeff_vm_core::py_shared::*;
}
pub mod segment {
    pub use doeff_vm_core::segment::*;
}
pub mod scope {
    pub use doeff_vm_core::scope::*;
}
pub mod step {
    pub use doeff_vm_core::{DoCtrl, Mode, PendingPython, PyCallOutcome, PyException, PythonCall};
}
pub mod value {
    pub use doeff_vm_core::value::*;
}
pub mod vm {
    pub use doeff_vm_core::{RustStore, VM};
}
pub mod pyvm {
    pub use crate::sentinels::PyRustHandlerSentinel;
    pub use doeff_vm_core::effect::PyEffectBase;
    pub use doeff_vm_core::{DoExprTag, PyDoCtrlBase, PyDoExprBase, PyResultErr, PyResultOk};
}

pub mod effect {
    pub use crate::effects::*;
    pub use doeff_vm_core::effect::{
        make_execution_context_object, make_get_execution_context_effect, PyExecutionContext,
        PyGetExecutionContext,
    };
}

pub mod handler {
    pub use crate::handlers::*;
    pub use doeff_vm_core::{
        IRStreamFactory, IRStreamFactoryRef, IRStreamProgram, IRStreamProgramRef,
    };
}

pub fn register_all(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    effects::register_effect_classes(m)?;
    sentinels::register_sentinels(m)?;
    Ok(())
}

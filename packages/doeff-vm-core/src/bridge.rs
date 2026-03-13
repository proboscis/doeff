use std::sync::OnceLock;

use pyo3::prelude::*;

use crate::do_ctrl::{DoCtrl, DoExprTag, PyDoCtrlBase, PyDoExprBase};
use crate::doeff_generator::DoeffGenerator;
use crate::driver::PyException;
use crate::effect::PyEffectBase;
use crate::vm::VM;

pub fn is_effect_base_like(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyEffectBase>()
}

pub fn is_doexpr_like(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyDoExprBase>() || obj.is_instance_of::<DoeffGenerator>()
}

pub fn doctrl_tag(obj: &Bound<'_, PyAny>) -> Option<DoExprTag> {
    obj.extract::<PyRef<'_, PyDoCtrlBase>>()
        .ok()
        .and_then(|base| DoExprTag::try_from(base.tag).ok())
}

pub type ClassifyYieldedHook =
    for<'py> fn(&VM, Python<'py>, &Bound<'py, PyAny>) -> Result<DoCtrl, PyException>;

pub type DoctrlToPyexprHook = fn(&DoCtrl) -> Result<Option<Py<PyAny>>, PyException>;

#[derive(Clone, Copy)]
pub struct VmHooks {
    pub classify_yielded: ClassifyYieldedHook,
    pub doctrl_to_pyexpr: DoctrlToPyexprHook,
}

static VM_HOOKS: OnceLock<VmHooks> = OnceLock::new();

pub fn install_vm_hooks(hooks: VmHooks) {
    let _ = VM_HOOKS.set(hooks);
}

pub fn classify_yielded_for_vm(
    vm: &VM,
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> Result<DoCtrl, PyException> {
    let hooks = VM_HOOKS
        .get()
        .ok_or_else(|| PyException::runtime_error("VM hooks not installed: classify_yielded"))?;
    (hooks.classify_yielded)(vm, py, obj)
}

pub fn doctrl_to_pyexpr_for_vm(yielded: &DoCtrl) -> Result<Option<Py<PyAny>>, PyException> {
    let hooks = VM_HOOKS
        .get()
        .ok_or_else(|| PyException::runtime_error("VM hooks not installed: doctrl_to_pyexpr"))?;
    (hooks.doctrl_to_pyexpr)(yielded)
}

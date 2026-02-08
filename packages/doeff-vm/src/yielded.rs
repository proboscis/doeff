//! Values yielded by the VM.

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::effect::DispatchEffect;

#[derive(Debug, Clone)]
pub enum Yielded {
    DoCtrl(DoCtrl),
    Effect(DispatchEffect),
}

impl Yielded {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            Yielded::DoCtrl(p) => Yielded::DoCtrl(p.clone_ref(py)),
            Yielded::Effect(e) => Yielded::Effect(e.clone()),
        }
    }
}

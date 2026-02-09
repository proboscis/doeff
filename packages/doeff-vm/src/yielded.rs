//! Values yielded by the VM.

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;

#[derive(Debug, Clone)]
pub enum Yielded {
    DoCtrl(DoCtrl),
}

impl Yielded {
    pub fn clone_ref(&self, py: Python<'_>) -> Self {
        match self {
            Yielded::DoCtrl(p) => Yielded::DoCtrl(p.clone_ref(py)),
        }
    }
}

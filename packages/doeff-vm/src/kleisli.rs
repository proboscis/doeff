use std::sync::Arc;

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::error::VMError;
use crate::py_shared::PyShared;

#[derive(Debug, Clone)]
pub struct KleisliDebugInfo {
    pub name: String,
    pub file: Option<String>,
    pub line: Option<u32>,
}

pub trait Kleisli: std::fmt::Debug + Send + Sync {
    fn apply(&self, py: Python<'_>, args: Vec<crate::value::Value>) -> Result<DoCtrl, VMError>;
    fn debug_info(&self) -> KleisliDebugInfo;
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

pub type KleisliRef = Arc<dyn Kleisli>;

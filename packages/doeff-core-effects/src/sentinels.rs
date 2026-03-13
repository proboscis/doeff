use std::sync::Arc;

use crate::handlers::{
    AwaitHandlerFactory, LazyAskHandlerFactory, ReaderHandlerFactory, ResultSafeHandlerFactory,
    StateHandlerFactory, WriterHandlerFactory,
};
use crate::scheduler::SchedulerHandler;
use doeff_vm_core::KleisliRef;
use doeff_vm_core::RustKleisli;
use pyo3::prelude::*;

/// Opaque sentinel wrapping a Rust handler factory.
#[pyclass(frozen, name = "RustHandler")]
pub struct PyRustHandlerSentinel {
    kleisli: KleisliRef,
}

impl PyRustHandlerSentinel {
    pub fn new(kleisli: KleisliRef) -> Self {
        Self { kleisli }
    }

    pub fn kleisli_ref(&self) -> KleisliRef {
        self.kleisli.clone()
    }
}

#[pymethods]
impl PyRustHandlerSentinel {
    fn __repr__(&self) -> String {
        let debug = self.kleisli.debug_info();
        format!("RustHandler({})", debug.name)
    }
}

pub fn register_sentinels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRustHandlerSentinel>()?;
    m.add(
        "state",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(StateHandlerFactory),
            "StateHandler".to_string(),
        ))),
    )?;
    m.add(
        "reader",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(ReaderHandlerFactory),
            "ReaderHandler".to_string(),
        ))),
    )?;
    m.add(
        "writer",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(WriterHandlerFactory),
            "WriterHandler".to_string(),
        ))),
    )?;
    m.add(
        "result_safe",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(ResultSafeHandlerFactory),
            "ResultSafeHandler".to_string(),
        ))),
    )?;
    m.add(
        "scheduler",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(SchedulerHandler::new()),
            "SchedulerHandler".to_string(),
        ))),
    )?;
    m.add(
        "lazy_ask",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(LazyAskHandlerFactory::new()),
            "LazyAskHandler".to_string(),
        ))),
    )?;
    m.add(
        "await_handler",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(AwaitHandlerFactory),
            "AwaitHandler".to_string(),
        ))),
    )?;
    m.add(
        "sync_await_handler",
        PyRustHandlerSentinel::new(Arc::new(RustKleisli::new(
            Arc::new(AwaitHandlerFactory),
            "sync_await_handler".to_string(),
        ))),
    )?;
    Ok(())
}

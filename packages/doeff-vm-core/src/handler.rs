//! Handler protocol traits shared by VM core and effect crates.

use std::sync::{Arc, Mutex};

use crate::continuation::Continuation;
use crate::do_ctrl::DoCtrl;
use crate::effect::DispatchEffect;
use crate::error::VMError;
use crate::ir_stream::IRStreamStep;
use crate::kleisli::{Kleisli, KleisliDebugInfo, RustKleisli};
use crate::rust_store::RustStore;
use crate::segment::ScopeStore;
use crate::step::PyException;
use crate::value::Value;

/// A Rust handler program instance (generator-like).
/// start/resume/throw mirror Python generator protocol but run in Rust.
pub trait IRStreamProgram: std::fmt::Debug + Send {
    fn start(
        &mut self,
        effect: DispatchEffect,
        k: Continuation,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep;
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep;
    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep;
}

/// Factory for Rust handler programs. Each dispatch creates a fresh instance.
pub trait IRStreamFactory: std::fmt::Debug + Send + Sync {
    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError>;
    fn create_program(&self) -> IRStreamProgramRef;
    fn handler_name(&self) -> &'static str {
        std::any::type_name::<Self>()
    }

    /// Create a handler program for a specific VM run token.
    ///
    /// Handlers that keep per-run state can override this to isolate state
    /// between distinct top-level runs.
    fn create_program_for_run(&self, _run_token: Option<u64>) -> IRStreamProgramRef {
        self.create_program()
    }

    /// Notification that a top-level VM run has completed.
    fn on_run_end(&self, _run_token: u64) {}

    fn supports_error_context_conversion(&self) -> bool {
        false
    }
}

/// Shared reference to a Rust program handler factory.
pub type IRStreamFactoryRef = Arc<dyn IRStreamFactory + Send + Sync>;

/// Shared reference to a running Rust handler program (cloneable for continuations).
pub type IRStreamProgramRef = Arc<Mutex<Box<dyn IRStreamProgram + Send>>>;

impl<T> Kleisli for T
where
    T: IRStreamFactory + Clone + std::fmt::Debug + Send + Sync + 'static,
{
    fn apply(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        self.apply_with_run_token(args, None)
    }

    fn apply_with_run_token(
        &self,
        args: Vec<Value>,
        run_token: Option<u64>,
    ) -> Result<DoCtrl, VMError> {
        let kleisli = RustKleisli::new(
            Arc::new(self.clone()),
            <Self as IRStreamFactory>::handler_name(self).to_string(),
        );
        kleisli.apply_with_run_token(args, run_token)
    }

    fn debug_info(&self) -> KleisliDebugInfo {
        KleisliDebugInfo {
            name: <Self as IRStreamFactory>::handler_name(self).to_string(),
            file: None,
            line: None,
        }
    }

    fn can_handle(&self, effect: &DispatchEffect) -> Result<bool, VMError> {
        <Self as IRStreamFactory>::can_handle(self, effect)
    }

    fn is_rust_builtin(&self) -> bool {
        true
    }

    fn supports_error_context_conversion(&self) -> bool {
        <Self as IRStreamFactory>::supports_error_context_conversion(self)
    }

    fn on_run_end(&self, run_token: u64) {
        <Self as IRStreamFactory>::on_run_end(self, run_token);
    }
}

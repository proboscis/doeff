//! doeff-core-effects: user-space effect handlers.
//!
//! Currently stubbed — handlers (LazyAsk, ResultSafe, scheduler)
//! will be rewritten for the new VM architecture.

// pub mod effects;   // TODO: rewrite for new Value-based effects
// pub mod handlers;  // TODO: rewrite for new Callable-based handlers
// pub mod sentinels; // TODO: rewrite

pub fn register_all(_m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    // Effects and sentinels registration stubbed out
    Ok(())
}

//! Whole-repo facts built exactly once, when first requested (single-file runs).
//!
//! A class's frozen-ness is decided by declarations in files the written file does not name (a reverse dependency — it depends on whether a class of the same name exists anywhere in the repo). Reading only the modules the written file
//! names could change the answer, so the facts are built from the whole repo — but only for files that **request** them. Files that do not request them (no handler argument whose type is
//! judged) need not build them. When built, they are built by the same procedure as before, so the answer does not change (files that request them read as before,
//! files that do not request them read nothing).
//! The declaration of a failure type (a defeffect in another file) and the Program passed as an argument (callers in another file) are also reverse dependencies, but the name to look up is fixed and any file holding the facts always contains that name
//! literally in its text, so they are built not from the whole repo but only from files that spell the name (`spelled_failures`, `spelled_callers`).

use std::sync::OnceLock;

/// A value built exactly once with `build` when first requested. The caller supplies the build procedure (the existing function that builds the whole-repo facts).
pub struct Deferred<'a, T> {
    cell: OnceLock<T>,
    build: Box<dyn Fn() -> T + Send + Sync + 'a>,
}

impl<'a, T> Deferred<'a, T> {
    pub fn new(build: impl Fn() -> T + Send + Sync + 'a) -> Self {
        Deferred { cell: OnceLock::new(), build: Box::new(build) }
    }

    /// The value (built on first request).
    pub fn get(&self) -> &T {
        self.cell.get_or_init(|| (self.build)())
    }

    /// Whether it has been built already (for tests).
    pub fn is_built(&self) -> bool {
        self.cell.get().is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn the_value_is_built_once_and_only_when_asked() {
        let calls = AtomicUsize::new(0);
        let deferred = Deferred::new(|| {
            calls.fetch_add(1, Ordering::SeqCst);
            41 + 1
        });
        assert!(!deferred.is_built());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        assert_eq!((*deferred.get(), *deferred.get()), (42, 42));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}

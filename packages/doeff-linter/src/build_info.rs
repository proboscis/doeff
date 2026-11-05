//! Information about the build of this binary (build.rs passes it through env). Readers inside the crate read this, not lib.rs — if a child module
//! read back from the crate root (lib.rs), every module declared by lib.rs would form a single dependency cycle.

/// The doeff commit this binary was built from (decided by build.rs — either the env DOEFF_LINTER_BUILD_COMMIT passed by an automated rebuild, or
/// the git HEAD with `+dirty` for a hand build, or `unknown` when there is no git). Reported by `--version` and by `linter` in editor-json.
pub const BUILD_COMMIT: &str = env!("DOEFF_LINTER_COMMIT");
/// The `--version` text (`<version> (doeff <commit>)`).
pub const VERSION_TEXT: &str = concat!(env!("CARGO_PKG_VERSION"), " (doeff ", env!("DOEFF_LINTER_COMMIT"), ")");

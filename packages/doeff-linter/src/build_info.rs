//! Information about the build of this binary (build.rs passes it through env). Readers inside the crate read this, not lib.rs — if a child module
//! read back from the crate root (lib.rs), every module declared by lib.rs would form a single dependency cycle.

/// The doeff commit this binary was built from (decided by build.rs — either the env DOEFF_LINTER_BUILD_COMMIT passed by an automated rebuild, or
/// the git HEAD with `+dirty` for a hand build, or `unknown` when there is no git). Reported by `--version` and by `linter` in editor-json.
pub const BUILD_COMMIT: &str = env!("DOEFF_LINTER_COMMIT");
/// The `-V` text (`<version> (doeff <commit>)`).
pub const VERSION_TEXT: &str = concat!(env!("CARGO_PKG_VERSION"), " (doeff ", env!("DOEFF_LINTER_COMMIT"), ")");
/// The key of the build inputs of BUILD_COMMIT (decided by build.rs — env DOEFF_LINTER_BUILD_INPUT_KEY passed by the build entry, or `unknown`). What the binary was
/// built from, unlike the commit, which only names where: downstream hooks compare this key with the key of their doeff checkout (ADR-DOE-BUILD-001 R4e).
pub const INPUT_KEY: &str = env!("DOEFF_LINTER_INPUT_KEY");
/// The `--version` text: VERSION_TEXT, then `inputs: <INPUT_KEY>` on the second line (readers of the commit read the first line only).
pub const LONG_VERSION_TEXT: &str =
    concat!(env!("CARGO_PKG_VERSION"), " (doeff ", env!("DOEFF_LINTER_COMMIT"), ")\ninputs: ", env!("DOEFF_LINTER_INPUT_KEY"));

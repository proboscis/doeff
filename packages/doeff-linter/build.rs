//! Embeds the doeff commit this binary was built from as env DOEFF_LINTER_COMMIT (reported by `--version` and by `linter` in editor-json).
//!
//! - Automatic rebuilds (dotfiles agentcli doeff_linter_follow) pass a 40-digit sha in env DOEFF_LINTER_BUILD_COMMIT — it is used as is.
//! - For a manual build, use `git rev-parse HEAD`. If the linter or indexer dir has uncommitted changes, append `+dirty`
//!   (so a binary carrying local changes does not claim the mainline commit). Without git, `unknown`.
use std::path::{Path, PathBuf};
use std::process::Command;

/// Runs git in the crate dir and returns its stdout (trimmed) on success.
///
/// Env `GIT_*` is removed: when built inside another repo's git hook (e.g. that repo's hook syncs the environment with `uv run`), the
/// `GIT_DIR` etc. that git passes to the hook would take precedence over `-C`, and that repo's HEAD would be reported as the doeff commit.
fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let mut command = Command::new("git");
    for (name, _) in std::env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") {
            command.env_remove(&name);
        }
    }
    let out = command.arg("-C").arg(dir).args(args).output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn main() {
    println!("cargo:rerun-if-env-changed=DOEFF_LINTER_BUILD_COMMIT");
    let crate_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo passes CARGO_MANIFEST_DIR"));
    let indexer_dir = crate_dir.join("../doeff-indexer");
    // So a manual build does not report a stale commit, re-run build.rs on HEAD movement (commit, checkout) and source changes.
    for dir in [crate_dir.join("src"), indexer_dir.join("src")] {
        println!("cargo:rerun-if-changed={}", dir.display());
    }
    if let Some(git_dir) = git(&crate_dir, &["rev-parse", "--absolute-git-dir"]) {
        for name in ["HEAD", "index", "logs/HEAD"] {
            let path = Path::new(&git_dir).join(name);
            if path.exists() {
                println!("cargo:rerun-if-changed={}", path.display());
            }
        }
    }
    let commit = match std::env::var("DOEFF_LINTER_BUILD_COMMIT") {
        Ok(given) if !given.trim().is_empty() => given.trim().to_string(),
        _ => match git(&crate_dir, &["rev-parse", "HEAD"]) {
            Some(head) => {
                let dirty = git(&crate_dir, &["status", "--porcelain", "--untracked-files=no", "--", ".", "../doeff-indexer"])
                    .is_some_and(|s| !s.is_empty());
                if dirty { format!("{}+dirty", head) } else { head }
            }
            None => "unknown".to_string(),
        },
    };
    println!("cargo:rustc-env=DOEFF_LINTER_COMMIT={}", commit);
}

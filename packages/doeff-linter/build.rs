//! この binary を組んだ doeff の commit を env DOEFF_LINTER_COMMIT として埋める(`--version` と editor-json の `linter` が名乗る・
//! agora-redesign #848)。
//!
//! - 自動の組み直し(dotfiles の agentcli doeff_linter_follow)は env DOEFF_LINTER_BUILD_COMMIT に 40 桁の sha を渡す — それをそのまま使う。
//! - 手で組んだ時は `git rev-parse HEAD`。linter か indexer の dir に commit していない変更が在れば `+dirty` を付ける
//!   (手元の変更を載せた binary が本線の commit を名乗らないため)。git が無ければ `unknown`。
use std::path::{Path, PathBuf};
use std::process::Command;

/// crate の dir で git を撃ち、成功した時の stdout(前後の空白を除く)を返す。
fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git").arg("-C").arg(dir).args(args).output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn main() {
    println!("cargo:rerun-if-env-changed=DOEFF_LINTER_BUILD_COMMIT");
    let crate_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo が CARGO_MANIFEST_DIR を渡す"));
    let indexer_dir = crate_dir.join("../doeff-indexer");
    // 手で組む時に古い commit を名乗らないよう、HEAD の動き(commit・checkout)と source の変更で build.rs を撃ち直させる。
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

//! Test of the doeff commit embedded in the version (build.rs) -- runs the build script built by cargo as is,
//! and checks that even when another repo's `GIT_DIR` is in the env, it names the HEAD of the doeff that contains this crate.
//!
//! Counterexample scenario: a git hook in an agora working tree syncs the environment with `uv run` and rebuilds the linter. If the `GIT_DIR` that git passes to the hook beats build.rs's
//! `git -C <crate>`, it names agora's HEAD as the doeff commit, and agora's hook reconciliation stops at `git merge-base` 128.

use std::path::{Path, PathBuf};
use std::process::Command;

/// The build script of this crate (the most recently built one) in the same target as this test's binary.
fn build_script() -> PathBuf {
    let exe = std::env::current_exe().expect("path of the test binary");
    let profile_dir = exe.parent().and_then(Path::parent).expect("above target/<profile>/deps");
    std::fs::read_dir(profile_dir.join("build"))
        .expect("target/<profile>/build")
        .filter_map(Result::ok)
        .filter(|entry| entry.file_name().to_string_lossy().starts_with("doeff-linter-"))
        .map(|entry| entry.path().join("build-script-build"))
        .filter(|path| path.exists())
        .max_by_key(|path| path.metadata().and_then(|m| m.modified()).ok())
        .expect("doeff-linter build script built by cargo")
}

/// Runs git with the `GIT_*` env vars removed (a read not affected by the env outside the test).
fn clean_git(dir: &Path, args: &[&str]) -> String {
    let mut command = Command::new("git");
    for (name, _) in std::env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") {
            command.env_remove(&name);
        }
    }
    let out = command.arg("-C").arg(dir).args(args).output().expect("git");
    assert!(out.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// Creates a temporary git repo holding one empty commit and returns its HEAD.
fn repo_with_one_commit(dir: &Path, message: &str) -> String {
    clean_git(dir, &["init", "-q"]);
    clean_git(dir, &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message]);
    clean_git(dir, &["rev-parse", "HEAD"])
}

/// The stand-in for the doeff checkout is a temporary repo (shaped like packages/doeff-linter) -- it does not rely on the `.git` of the tree this test runs in. The daily full verification
/// syncs the doeff tree without `.git` and runs it, so reading this crate dir's HEAD with git always failed in that environment.
#[test]
fn another_repos_git_dir_does_not_name_its_head_as_the_doeff_commit() {
    let doeff = tempfile::tempdir().expect("temporary repo standing in for doeff");
    let doeff_head = repo_with_one_commit(doeff.path(), "doeff");
    let crate_dir = doeff.path().join("packages").join("doeff-linter");
    std::fs::create_dir_all(crate_dir.join("src")).expect("crate dir");
    let other = tempfile::tempdir().expect("temporary dir");
    let other_head = repo_with_one_commit(other.path(), "other");
    let out_dir = tempfile::tempdir().expect("temporary OUT_DIR");

    let out = Command::new(build_script())
        .env("CARGO_MANIFEST_DIR", &crate_dir)
        .env("OUT_DIR", out_dir.path())
        .env_remove("DOEFF_LINTER_BUILD_COMMIT")
        .env("GIT_DIR", other.path().join(".git"))
        .env("GIT_WORK_TREE", other.path())
        .env("GIT_INDEX_FILE", other.path().join(".git").join("index"))
        .output()
        .expect("run the build script");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let printed = String::from_utf8_lossy(&out.stdout);
    let named = printed
        .lines()
        .find_map(|line| line.strip_prefix("cargo:rustc-env=DOEFF_LINTER_COMMIT="))
        .expect("names DOEFF_LINTER_COMMIT");
    assert!(!named.starts_with(&other_head), "named the HEAD {} of another repo", other_head);
    assert_eq!(named, doeff_head);
}

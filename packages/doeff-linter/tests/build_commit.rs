//! 版に埋める doeff の commit(build.rs)の検(agora-redesign #1481)— cargo が組んだ build script をそのまま走らせ、
//! 別の repo の `GIT_DIR` が env に在っても、この crate の在る doeff の HEAD を名乗るかを見る。
//!
//! 反例の場面: agora の作業木の git の hook が `uv run` で環境を同期し、linter を組み直す。git が hook に渡す `GIT_DIR` が build.rs の
//! `git -C <crate>` に勝つと、agora の HEAD を doeff の commit として名乗り、agora の hook の突き合わせが `git merge-base` 128 で止まる。

use std::path::{Path, PathBuf};
use std::process::Command;

/// この検の binary と同じ target の、この crate の build script(一番新しく組まれた物)。
fn build_script() -> PathBuf {
    let exe = std::env::current_exe().expect("検の binary の path");
    let profile_dir = exe.parent().and_then(Path::parent).expect("target/<profile>/deps の上");
    std::fs::read_dir(profile_dir.join("build"))
        .expect("target/<profile>/build")
        .filter_map(Result::ok)
        .filter(|entry| entry.file_name().to_string_lossy().starts_with("doeff-linter-"))
        .map(|entry| entry.path().join("build-script-build"))
        .filter(|path| path.exists())
        .max_by_key(|path| path.metadata().and_then(|m| m.modified()).ok())
        .expect("cargo が組んだ doeff-linter の build script")
}

/// env の `GIT_*` を外して git を撃つ(検の外の env に左右されない読み)。
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

#[test]
fn another_repos_git_dir_does_not_name_its_head_as_the_doeff_commit() {
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let other = tempfile::tempdir().expect("一時の dir");
    clean_git(other.path(), &["init", "-q"]);
    clean_git(other.path(), &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "other"]);
    let other_head = clean_git(other.path(), &["rev-parse", "HEAD"]);
    let doeff_head = clean_git(crate_dir, &["rev-parse", "HEAD"]);
    let out_dir = tempfile::tempdir().expect("一時の OUT_DIR");

    let out = Command::new(build_script())
        .env("CARGO_MANIFEST_DIR", crate_dir)
        .env("OUT_DIR", out_dir.path())
        .env_remove("DOEFF_LINTER_BUILD_COMMIT")
        .env("GIT_DIR", other.path().join(".git"))
        .env("GIT_WORK_TREE", other.path())
        .env("GIT_INDEX_FILE", other.path().join(".git").join("index"))
        .output()
        .expect("build script を走らせる");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let printed = String::from_utf8_lossy(&out.stdout);
    let named = printed
        .lines()
        .find_map(|line| line.strip_prefix("cargo:rustc-env=DOEFF_LINTER_COMMIT="))
        .expect("DOEFF_LINTER_COMMIT を名乗る");
    assert!(!named.starts_with(&other_head), "別の repo の HEAD {} を名乗った", other_head);
    assert_eq!(named.trim_end_matches("+dirty"), doeff_head);
}

//! A test that a single-file run does not read the whole registry dir (one key per file) every time.
//!
//! The registry dir in agora-controllers has 447 files, and on a cold worktree every single file waits for the disk. 83% of the opens in one run were this dir
//! (2026-10-17, Pod IO pressure 55-66%, 494 of 596 opens). If the (name, size, modification time) of the files in the dir are the same as in the previous run,
//! the previous read is used without reading the contents. The answer does not change — if the contents change (one of name, size or modification time changes), it is read again.
//!
//! The observations are the run stage name (`registry.read`, which `DOEFF_LINTER_TIMING` writes to stderr) and editor-json's `registered`.

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

const CONFIG: &str = r#"
[tool.doeff-linter]
enable = ["DOEFF104"]

[tool.doeff-linter.layers]
order = ["core"]
paths = { core = "app/core" }

[tool.doeff-linter.registry]
dirs = ["registry/BREACHES"]
"#;

const KEYS: usize = 60;

/// A temporary repo: an untagged module (DOEFF104 fires) and a registry of 60 keys (one of which is untagged.hy::DOEFF104).
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/untagged.hy"), "(defn decide [x] x)\n").unwrap();
    std::fs::create_dir_all(dir.path().join("registry/BREACHES")).unwrap();
    for n in 0..KEYS {
        let key = if n == 7 { "app/core/untagged.hy::DOEFF104".to_string() } else { format!("app/core/other{n}.hy::DOEFF104") };
        std::fs::write(dir.path().join(format!("registry/BREACHES/k{n:02}.txt")), format!("{key}\nreason {n}\n")).unwrap();
    }
    age_registry(dir.path());
    dir
}

/// Sets the registry files' modification time to one hour ago (a just-updated file "cannot be told apart from the current write", so it is not remembered — to remove that safeguard).
fn age_registry(root: &Path) {
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in std::fs::read_dir(root.join("registry/BREACHES")).unwrap().filter_map(Result::ok) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
}

/// A single-file run (`--stdin --path`). Returns (editor-json, stderr).
fn single(root: &Path, cache: &Path, rel: &str) -> (Value, String) {
    let source = std::fs::read_to_string(root.join(rel)).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log", "--stdin", "--path"])
        .arg(root.join(rel))
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_TIMING", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(source.as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    (serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({e}): {stdout}\n{stderr}")), stderr)
}

fn violation<'a>(report: &'a Value, key: &str) -> &'a Value {
    report["violations"].as_array().unwrap().iter().find(|v| v["key"] == key).unwrap_or_else(|| panic!("{key} is missing: {report}"))
}

/// The number of times registry files were actually read (occurrences of the stage `registry.read`).
fn reads(stderr: &str) -> usize {
    stderr.lines().filter(|line| line.contains("timing: registry.read ")).count()
}

#[test]
fn a_registry_dir_that_did_not_change_is_not_read_again() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, first_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&first_log), 1, "the first run reads the registry\n{first_log}");
    assert_eq!(violation(&first, "app/core/untagged.hy::DOEFF104")["registered"], true);
    let (second, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 0, "an unchanged registry is not read on the second run\n{second_log}");
    assert_eq!(second["violations"], first["violations"], "the answer is the same even with the remembered registry");
}

#[test]
fn a_registry_file_that_changed_is_read_again_and_the_answer_follows() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, _) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(violation(&first, "app/core/untagged.hy::DOEFF104")["registered"], true);
    // Replace a key file with a different key of the same size (the size is the same; only the modification time changes).
    std::fs::write(dir.path().join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF105\nreason 7\n").unwrap();
    age_registry(dir.path());
    let (second, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 1, "a registry whose contents were replaced is read again\n{second_log}");
    assert_eq!(violation(&second, "app/core/untagged.hy::DOEFF104")["registered"], false, "a key removed from the registry is no longer registered");
}

#[test]
fn a_registry_file_added_or_removed_is_read_again() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    single(dir.path(), cache.path(), "app/core/untagged.hy");
    std::fs::remove_file(dir.path().join("registry/BREACHES/k07.txt")).unwrap();
    let (removed, removed_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&removed_log), 1, "{removed_log}");
    assert_eq!(violation(&removed, "app/core/untagged.hy::DOEFF104")["registered"], false);
    std::fs::write(dir.path().join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF104\nreason 7\n").unwrap();
    age_registry(dir.path());
    let (added, added_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&added_log), 1, "{added_log}");
    assert_eq!(violation(&added, "app/core/untagged.hy::DOEFF104")["registered"], true);
}

/// A just-updated file cannot be told apart by modification time from another write in the same instant — it is not remembered and is read every time (the same safeguard as git's racy handling).
#[test]
fn a_registry_file_written_a_moment_ago_is_always_read() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("registry/BREACHES/k08.txt"), "app/core/other8.hy::DOEFF104\nreason 8\n").unwrap(); // modification time = now
    let (_, first_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    let (_, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!((reads(&first_log), reads(&second_log)), (1, 1), "{first_log}\n{second_log}");
}

/// Run git (not pulled off course by the caller's env such as `GIT_DIR`).
fn git(dir: &Path, args: &[&str]) {
    let status = Command::new("git")
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"])
        .args(args)
        .current_dir(dir)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .status()
        .unwrap();
    assert!(status.success(), "git {args:?}");
}

/// Builds `repo` up to a commit and adds a new worktree of the same commit (a different path, inode and modification time per worktree — the shape of a cold new worktree).
fn repo_with_fresh_worktree() -> (tempfile::TempDir, tempfile::TempDir, std::path::PathBuf) {
    let main = repo();
    git(main.path(), &["init", "-q", "-b", "main"]);
    git(main.path(), &["add", "-A"]);
    git(main.path(), &["commit", "-q", "-m", "base"]);
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    // A file in a just-made worktree is not remembered within 2 seconds of its update (it may be written while being read). As git normally does, put the stat of aged files back into the index
    // (the shape after running `git status` or similar in the worktree).
    age_registry(&fresh);
    git(&fresh, &["update-index", "-q", "--refresh"]);
    (main, parent, fresh)
}

/// The registry of a new worktree, if the git index's blob id is the same as in another worktree, uses the result read by the other worktree and does not read the contents
/// (the wait of reading 449 files in a cold new worktree is 0.8-4.8 seconds). The answer is the same as when read.
#[test]
fn a_fresh_worktree_does_not_read_the_registry_another_worktree_read() {
    let (main, _parent, fresh) = repo_with_fresh_worktree();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, first_log) = single(main.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&first_log), 1, "the first worktree reads the registry\n{first_log}");
    let (second, second_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 0, "the new worktree re-read the registry that another worktree had read\n{second_log}");
    assert_eq!(violation(&second, "app/core/untagged.hy::DOEFF104")["registered"], true);
    assert_eq!(without_paths(&second), without_paths(&first), "the answer is the same without reading (excluding absolute paths, which differ per worktree)");
}

/// From the list of violations, removes the absolute path fields that differ per worktree (to compare answers across worktrees).
fn without_paths(report: &Value) -> Vec<Value> {
    report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|violation| {
            let mut violation = violation.clone();
            violation.as_object_mut().unwrap().remove("path");
            violation
        })
        .collect()
}

/// A registry file rewritten in the new worktree (contents different from the index's blob) and a file not in the index are read without using the other worktree's result — the answer follows.
#[test]
fn a_registry_file_changed_or_added_in_a_fresh_worktree_is_read() {
    let (main, _parent, fresh) = repo_with_fresh_worktree();
    let cache = tempfile::TempDir::new().unwrap();
    single(main.path(), cache.path(), "app/core/untagged.hy");
    std::fs::write(fresh.join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF105\nreason 7\n").unwrap();
    let (changed, changed_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&changed_log), 1, "the rewritten registry was not read\n{changed_log}");
    assert_eq!(violation(&changed, "app/core/untagged.hy::DOEFF104")["registered"], false);
    std::fs::write(fresh.join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF104\nreason 7\n").unwrap();
    git(&fresh, &["checkout", "--", "registry/BREACHES/k07.txt"]);
    std::fs::write(fresh.join("registry/BREACHES/k99.txt"), "app/core/untagged.hy::DOEFF104\nadded reason\n").unwrap();
    std::fs::remove_file(fresh.join("registry/BREACHES/k07.txt")).unwrap();
    let (added, added_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&added_log), 1, "the registry file not in the index was not read\n{added_log}");
    assert_eq!(violation(&added, "app/core/untagged.hy::DOEFF104")["registered"], true, "the key of the added file has no effect");
}

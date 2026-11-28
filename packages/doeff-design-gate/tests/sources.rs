//! Where the tree after the change is read: the work tree (default), the staged index (`--index`, honouring `GIT_INDEX_FILE`), or a commit (`--head <commit>`).
//! Each test makes the work tree and what is read differ, so a gate that read the wrong one would give the other answer.
//!   * `--index` refuses a staged DOEFF215/216 change under a clean work tree, also from an index `GIT_INDEX_FILE` names; it passes a clean index under a dirty work tree.
//!   * `--head <commit>` judges that commit, not the work tree or HEAD.
//!   * `--index` with `--head`, and a `--head` that is not a commit, cannot be judged (2).
//!   * 265 changed paths are judged in under a second from the index and from a commit.

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use doeff_linter::project::design_evidence::{shapes_of, ExistingShapes};

const DECLARATION: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"
                    :guard {:base "main" :approvals "design/approvals.toml" :approval-pattern r"^req-[0-9]+$"}})
"#;

const LENDING_REL: &str = "app/core/lending.hy";
const LENDING: &str = "(defrecord LendingState\n  (#^ tuple returning)\n  (#^ tuple unreturned)\n  (#^ tuple extending)\n  (#^ tuple touching)\n  (#^ int last-seq)\n  (#^ tuple leases))\n\n\
                       (deff next-seq [state]\n  (LendingState :returning state.returning :unreturned state.unreturned :extending state.extending\n\
                       :touching state.touching :last-seq (+ 1 state.last-seq) :leases state.leases))\n";

/// LendingState with a seventh field its module never uses (DOEFF215 and DOEFF216).
fn grown() -> String {
    LENDING.replace("(#^ tuple leases))", "(#^ tuple leases)\n  (setv #^ frozenset paused-credentials (frozenset)))")
}

fn git(dir: &Path, args: &[&str]) {
    git_with(dir, args, &[]);
}

fn git_with(dir: &Path, args: &[&str], env: &[(&str, &Path)]) {
    let output = Command::new("git").args(args).envs(env.iter().copied()).current_dir(dir).output().unwrap();
    assert!(output.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&output.stderr));
}

fn write(dir: &Path, rel: &str, text: &str) {
    let path = dir.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

fn ledger(files: &[(&str, &str)]) -> String {
    let shapes: Vec<(&str, Vec<_>)> = files.iter().map(|(rel, text)| (*rel, shapes_of(rel, text).unwrap())).collect();
    ExistingShapes::spell(shapes.iter().map(|(rel, s)| (*rel, s.as_slice())))
}

/// A repo whose main holds the declaration and `files` (all in the ledger), with the work tree on a branch off main.
fn base(files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    write(root, "pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF212\", \"DOEFF214\"]\n");
    write(root, "architecture.hy", DECLARATION);
    for (rel, text) in files {
        write(root, rel, text);
    }
    write(root, "design-existing.toml", &ledger(files));
    write(root, "design/approvals.toml", "");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    git(root, &["checkout", "-q", "-b", "work"]);
    dir
}

/// Runs the gate against main with `extra` before `--paths` and `env` set (exit code, stdout and stderr).
fn gate(root: &Path, extra: &[&str], env: &[(&str, &Path)], paths: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-design-gate"))
        .args(["--base", "main"])
        .args(extra)
        .arg("--paths")
        .args(paths)
        .env_remove("GIT_INDEX_FILE")
        .envs(env.iter().copied())
        .current_dir(root)
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)))
}

fn refused_for_the_seventh_field(code: i32, out: &str) {
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF215 LendingState:width") && out.contains("DOEFF216 LendingState:unused-in-module"), "{}", out);
}

#[test]
fn the_index_is_judged_not_a_clean_work_tree() {
    let dir = base(&[(LENDING_REL, LENDING)]);
    let root = dir.path();
    write(root, LENDING_REL, &grown());
    git(root, &["add", LENDING_REL]);
    write(root, LENDING_REL, LENDING);
    let (code, out) = gate(root, &[], &[], &[LENDING_REL]);
    assert_eq!(code, 0, "the work tree is the base's: {}", out);
    let (code, out) = gate(root, &["--index"], &[], &[LENDING_REL]);
    refused_for_the_seventh_field(code, &out);
}

#[test]
fn the_index_git_index_file_names_is_judged() {
    let dir = base(&[(LENDING_REL, LENDING)]);
    let root = dir.path();
    let other = root.join(".git").join("other-index");
    std::fs::copy(root.join(".git").join("index"), &other).unwrap();
    write(root, LENDING_REL, &grown());
    git_with(root, &["add", LENDING_REL], &[("GIT_INDEX_FILE", &other)]);
    write(root, LENDING_REL, LENDING);
    let (code, out) = gate(root, &["--index"], &[], &[LENDING_REL]);
    assert_eq!(code, 0, "the repo's own index is clean: {}", out);
    let (code, out) = gate(root, &["--index"], &[("GIT_INDEX_FILE", &other)], &[LENDING_REL]);
    refused_for_the_seventh_field(code, &out);
}

#[test]
fn a_clean_index_passes_under_a_dirty_work_tree() {
    let dir = base(&[(LENDING_REL, LENDING)]);
    let root = dir.path();
    write(root, LENDING_REL, &grown());
    let (code, out) = gate(root, &[], &[], &[LENDING_REL]);
    refused_for_the_seventh_field(code, &out);
    let (code, out) = gate(root, &["--index"], &[], &[LENDING_REL]);
    assert_eq!(code, 0, "the index is the base's: {}", out);
}

#[test]
fn a_commit_given_by_head_is_judged_not_the_work_tree_or_head() {
    let dir = base(&[(LENDING_REL, LENDING)]);
    let root = dir.path();
    write(root, LENDING_REL, &grown());
    git(root, &["commit", "-q", "-am", "grow"]);
    let output = Command::new("git").args(["rev-parse", "HEAD"]).current_dir(root).output().unwrap();
    let grown_commit = String::from_utf8_lossy(&output.stdout).trim().to_string();
    write(root, LENDING_REL, LENDING);
    git(root, &["commit", "-q", "-am", "back"]);
    let (code, out) = gate(root, &[], &[], &[LENDING_REL]);
    assert_eq!(code, 0, "the work tree and HEAD are the base's: {}", out);
    let (code, out) = gate(root, &["--head", &grown_commit], &[], &[LENDING_REL]);
    refused_for_the_seventh_field(code, &out);
    write(root, LENDING_REL, &grown());
    let (code, out) = gate(root, &["--head", "HEAD"], &[], &[LENDING_REL]);
    assert_eq!(code, 0, "HEAD is the base's though the work tree grew: {}", out);
}

#[test]
fn index_with_head_and_a_head_that_is_not_a_commit_cannot_be_judged() {
    let dir = base(&[(LENDING_REL, LENDING)]);
    let root = dir.path();
    let (code, out) = gate(root, &["--index", "--head", "HEAD"], &[], &[LENDING_REL]);
    assert_eq!(code, 2, "{}", out);
    assert!(out.contains("--index and --head"), "{}", out);
    let (code, out) = gate(root, &["--head", "no-such-ref"], &[], &[LENDING_REL]);
    assert_eq!(code, 2, "{}", out);
}

#[test]
fn the_index_and_a_commit_of_265_paths_take_under_a_second() {
    let files: Vec<(String, String)> =
        (0..265).map(|i| (format!("app/m{}/kept_{}.hy", i % 13, i), format!("(defrecord Kept{}\n  (#^ str card))\n", i))).collect();
    let refs: Vec<(&str, &str)> = files.iter().map(|(r, t)| (r.as_str(), t.as_str())).collect();
    let dir = base(&refs);
    let root = dir.path();
    for (rel, text) in &files {
        write(root, rel, &format!(";; touched\n{}", text));
    }
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "touch"]);
    let paths: Vec<&str> = files.iter().map(|(r, _)| r.as_str()).collect();
    for extra in [&["--index"][..], &["--head", "HEAD"][..]] {
        let started = Instant::now();
        let (code, out) = gate(root, extra, &[], &paths);
        let took = started.elapsed();
        eprintln!("doeff-design-gate {:?} over {} paths: {:?}", extra, paths.len(), took);
        assert_eq!(code, 0, "{}", out);
        assert!(took < Duration::from_secs(1), "{:?} over {} paths took {:?}", extra, paths.len(), took);
    }
}

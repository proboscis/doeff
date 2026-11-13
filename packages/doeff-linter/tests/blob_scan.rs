//! Test that the first run in a cold working tree does not read all of the repo's Hy.
//!
//! A single-file run, when the written file has a definition that calls its own argument bare, reads all of the repo's roughly 2900 Hy files to look for callers
//! (`program-params.scan`). Each working tree has a different inode, so a cold working tree waits on disk file by file, and on a busy Pod it took 3-5 seconds,
//! exceeding the hook deadline (4 seconds) (of 70 cold first runs, 7 of the 8 that exceeded 4 seconds were at this stage).
//! The blob id held by git's index determines the contents, so a result that another working tree read and stored for the same contents can be looked up without reading the contents.
//!
//! Observation is `program-params.scan read=<number of files whose contents were read>` that `DOEFF_LINTER_TIMING` writes to stderr, plus the violations of editor-json.

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

const FILLERS: usize = 80;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF126"]

[tool.doeff-linter.definitions]
paths = ["app"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "core" :roles [program] :imports [core])]
  :handler-arguments {:files ["app/**/*.hy"] :exclude ["**/tests/**"] :keep-mark "keep:"})
"#;

const TAGS: &str = "{:tags {:context \"c\" :role \"program\"}}";
const HOF: &str = "app/core/hof.hy";

fn write(dir: &Path, rel: &str, text: &str) {
    std::fs::create_dir_all(dir.join(rel).parent().unwrap()).unwrap();
    std::fs::write(dir.join(rel), text).unwrap();
}

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

/// Builds, up to a commit, a repo where the caller (user.hy) passes a Program to the hof's rows-by-text. Plus FILLERS other modules.
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", CONFIG);
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, HOF, &format!("(defk rows-by-text [rows field-of] {TAGS}\n  (for [row rows]\n    (setv value (field-of row)))\n  rows)\n"));
    write(
        root,
        "app/core/user.hy",
        &format!("(import app.core.hof [rows-by-text])\n(defk tag-rows-of [tags] {TAGS}\n  (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p \"subject\")))))\n"),
    );
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
    }
    // A file right after an update is "indistinguishable from the current write" (the same guard as git's racy handling), so set it to one hour ago before adding it to the index.
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

/// A single-file run (`--stdin --path`). (editor-json, stderr).
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

/// The number of files whose contents were actually read in the caller search (`program-params.scan read=N`).
fn scan_reads(stderr: &str) -> usize {
    stderr
        .lines()
        .find_map(|line| line.strip_prefix("doeff-linter-timing: program-params.scan read="))
        .unwrap_or_else(|| panic!("the caller search did not run:\n{stderr}"))
        .trim()
        .parse()
        .unwrap()
}

/// The gauge count of the caller search read (`program-params.scan <field>=N`).
fn scan_count(stderr: &str, field: &str) -> usize {
    let prefix = format!("doeff-linter-timing: program-params.scan {field}=");
    stderr
        .lines()
        .find_map(|line| line.strip_prefix(prefix.as_str()))
        .unwrap_or_else(|| panic!("{field} of program-params.scan is not output:\n{stderr}"))
        .trim()
        .parse()
        .unwrap()
}

fn keys(report: &Value) -> Vec<String> {
    let mut keys: Vec<String> = report["violations"].as_array().unwrap().iter().filter_map(|v| v["key"].as_str()).map(str::to_string).collect();
    keys.sort();
    keys
}

/// If another working tree has read and stored the same contents, the first run in a new working tree reads almost none of the repo's Hy. The answer is the same as when read.
#[test]
fn a_fresh_worktree_does_not_read_files_another_worktree_already_read() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), HOF);
    assert!(scan_reads(&stderr) >= FILLERS, "the first working tree reads everything: {stderr}");

    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HOF);
    let read = scan_reads(&stderr);
    assert!(read <= FILLERS / 4, "read the contents of {read} files in a cold working tree (of {} files): {stderr}", FILLERS + 3);
    assert_eq!(keys(&first), keys(&second), "the answer is the same without reading");
}

/// A file rewritten in the working tree (its blob id differs from the index) is read for its current contents rather than answered from the cache -- when the caller disappears, the hit disappears too.
#[test]
fn a_file_changed_in_the_worktree_is_read_not_remembered() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (before, _) = single(main.path(), cache.path(), HOF);
    assert!(!keys(&before).is_empty() || before["violations"].is_array());

    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    // The caller stops calling rows-by-text (do not keep the same size).
    write(&fresh, "app/core/user.hy", "(defn plain [] 1)\n");
    let (after, stderr) = single(&fresh, cache.path(), HOF);
    let whole = {
        let none = tempfile::TempDir::new().unwrap();
        single(&fresh, none.path(), HOF).0
    };
    assert_eq!(keys(&after), keys(&whole), "the answer using the cache is the same as a run without the cache: {stderr}");
    assert!(scan_reads(&stderr) >= 1, "the rewritten file is read: {stderr}");
}

/// In the first run in a new working tree, the spelling memo is looked up from per-chunk files of the index order rather than from per-file storage files -- in a cold working tree
/// it does not open as many storage files as there are Hy files in the repo and wait on disk (about 4600 opens in a cold new working tree). The answer is the same as when read.
#[test]
fn a_fresh_worktree_reads_the_spellings_from_a_few_chunks_not_one_object_per_file() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, _) = single(main.path(), cache.path(), HOF);
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HOF);
    let chunks = scan_count(&stderr, "chunks");
    let missed = scan_count(&stderr, "missed");
    assert!(chunks >= 1 && chunks <= 8, "read {chunks} chunks (of {} files): {stderr}", FILLERS + 3);
    assert_eq!(missed, 0, "{missed} blobs not in the chunk table -- not looked up from chunks: {stderr}");
    assert_eq!(keys(&first), keys(&second), "the answer is the same even when looked up from chunks");
}

/// A repo built up to a commit with a file that looks up failure types (the type of a match arm is declared in another file's defeffect `:failure`), the file with that declaration, and FILLERS other
/// modules.
fn failure_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", &CONFIG.replace(r#"enable = ["DOEFF126"]"#, r#"enable = ["DOEFF122", "DOEFF126"]"#));
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/core/errs.hy", "(defrecord Unreachable [reason])\n(defrecord Accepted [word])\n");
    write(root, "app/core/effs.hy", "(import app.core.errs)\n(defeffect ReadRow {:fields [key] :answer None :failure [app.core.errs.Unreachable]})\n");
    write(root, "app/core/other_effs.hy", "(defeffect Other {:fields [key] :answer None :absent [Gone]})\n");
    write(
        root,
        NEEDS,
        &format!(
            "(import app.core.errs [Unreachable Accepted])\n(defk decide [x] {TAGS}\n  (<- said (| Accepted Unreachable) (verdict x))\n  (match said\n    (Unreachable) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail)))\n                      (return refused))\n    (Accepted) None))\n"
        ),
    );
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
    }
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

const NEEDS: &str = "app/core/needs.hy";

/// The gauge count of stage `label` (`<label> <field>=N`).
fn count_of(stderr: &str, label: &str, field: &str) -> usize {
    let prefix = format!("doeff-linter-timing: {label} {field}=");
    stderr.lines().find_map(|line| line.strip_prefix(prefix.as_str())).unwrap_or_else(|| panic!("{label} {field} is not output:\n{stderr}")).trim().parse().unwrap()
}

/// The walk that looks for failure-type declarations also, in the first run in a new working tree, does not read the contents of files another working tree has read (the spelling memo is looked up from chunks) -- in a cold working tree,
/// the wait to read all of the repo's Hy was 3 seconds. Even a file containing the declaration's spelling is not read if the memo says it does not spell the type's name. The answer is the same as when read.
#[test]
fn a_fresh_worktree_finds_the_failure_declarations_without_reading_the_repo() {
    let main = failure_repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), NEEDS);
    assert!(keys(&first).iter().any(|k| k.contains("DOEFF122")), "DOEFF122 does not hit needs.hy (error in the test's material)\n{stderr}");
    assert!(count_of(&stderr, "failure-types.collect", "read") >= FILLERS, "the first working tree reads everything: {stderr}");
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), NEEDS);
    let read = count_of(&stderr, "failure-types.collect", "read");
    assert!(read <= FILLERS / 4, "read the contents of {read} files in a cold working tree (of {} files): {stderr}", FILLERS + 4);
    assert_eq!(count_of(&stderr, "failure-types.collect", "missed"), 0, "there were blobs not in the chunk table: {stderr}");
    assert_eq!(keys(&first), keys(&second), "the answer is the same without reading");
}

/// A repo, built up to a commit, with a handler file that looks up a class's frozen-ness (an argument judged by type `#^ Cfg cfg`), a frozen class and a non-frozen class of the same name, a Python class, and
/// FILLERS other modules.
fn class_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", &CONFIG.replace(r#"enable = ["DOEFF126"]"#, r#"enable = ["DOEFF142"]"#));
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/core/cfgs.hy", "(defrecord Cfg [#^ str url])\n");
    write(root, "app/core/legacy.hy", "(defclass Cfg [] (setv x 1))\n");
    write(root, "tools/settings.py", "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass Settings:\n    x: int\n");
    write(root, HANDLERS, "(import app.core.cfgs [Cfg])\n(defhandler h [#^ Cfg cfg]\n  (Ping [e k] (k 1)))\n");
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
        write(root, &format!("tools/filler{i}.py"), &format!("def filler_{i}(x):\n    return x + {i}\n"));
    }
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

const HANDLERS: &str = "app/core/handlers.hy";

/// A single-file run that looks up a class's frozen-ness also, in the first run in a new working tree, does not read the contents of Hy and Python files that another working tree has read (
/// it used to walk in order and read all of the repo). The answer is the same as when read (a non-frozen class of the same name exists, so Cfg is not frozen).
#[test]
fn a_fresh_worktree_builds_the_class_index_without_reading_the_repo() {
    let main = class_repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), HANDLERS);
    assert!(keys(&first).iter().any(|k| k.contains("DOEFF142")), "DOEFF142 does not hit handlers.hy (error in the test's material)\n{stderr}");
    assert!(count_of(&stderr, "handler-argument-classes.scan", "read") >= 2 * FILLERS, "the first working tree reads everything: {stderr}");
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HANDLERS);
    let read = count_of(&stderr, "handler-argument-classes.scan", "read");
    assert!(read <= FILLERS / 2, "read the contents of {read} files in a cold working tree (of {} files): {stderr}", 2 * FILLERS + 4);
    assert_eq!(keys(&first), keys(&second), "the answer is the same without reading");
}

/// A working tree built from a different commit (after changing one file and committing) also looks up the chunk table, and the only blob not in the table is the changed file's -- chunk names are
/// decided by the path at the head of the chunk and the table adds blob rows, so files unchanged across commits hit. The answer is the same as when read.
#[test]
fn a_worktree_of_a_later_commit_misses_only_the_changed_file() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    single(main.path(), cache.path(), HOF);
    // Also change the size (git does not compare the ns of mtime, and treats a rewrite of the same second and same size as unchanged).
    write(main.path(), "app/core/filler7.hy", "(defn filler-7 [x] (* x 7 7))\n");
    let old = SystemTime::now() - Duration::from_secs(3600);
    std::fs::File::options().write(true).open(main.path().join("app/core/filler7.hy")).unwrap().set_modified(old).unwrap();
    git(main.path(), &["commit", "-q", "-am", "change one file"]);
    let parent = tempfile::TempDir::new().unwrap();
    let later = parent.path().join("later");
    git(main.path(), &["worktree", "add", "-q", "--detach", later.to_str().unwrap()]);
    for entry in walkdir::WalkDir::new(&later).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file() && !e.path().components().any(|c| c.as_os_str() == ".git")) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(&later, &["update-index", "-q", "--refresh"]);
    let (report, stderr) = single(&later, cache.path(), HOF);
    assert_eq!(scan_count(&stderr, "missed"), 1, "the blob not in the table is not just the one changed file: {stderr}");
    // The only contents read are the changed file and the 2 files (hof.hy, user.hy) read for analysis because they spell the callee's name.
    assert!(scan_reads(&stderr) <= 3, "read the contents of files other than the changed file and the files that spell the callee: {stderr}");
    let whole = {
        let none = tempfile::TempDir::new().unwrap();
        single(&later, none.path(), HOF).0
    };
    assert_eq!(keys(&report), keys(&whole), "the answer looked up from the table differs from the run without the cache");
}

/// Blob-backed scans read all declaration fragments, including a changed vocabulary file;
/// cached source facts cannot freeze the declaration's previous rule inputs.
#[test]
fn split_declarations_are_read_in_a_fresh_blob_backed_worktree() {
    let main = repo();
    write(main.path(), "pyproject.toml", &CONFIG
        .replace(r#"architecture = "architecture.hy""#, r#"architecture = ["architecture.hy", "declarations/*.hy"]"#)
        .replace(r#"enable = ["DOEFF126"]"#, r#"enable = ["DOEFF126", "DOEFF150"]"#));
    write(main.path(), "declarations/words.hy", r#"(defarchitecture-lists sample
      :retired-words [(retired-words "old-name" :words ["rows-by-text"] :files ["app/**/*.hy"] :in lines :instead "rows-of")])"#);
    git(main.path(), &["add", "-A"]);
    git(main.path(), &["commit", "-qm", "split declarations"]);
    let cache = tempfile::tempdir().unwrap();
    let (first, _) = single(main.path(), cache.path(), HOF);
    assert!(first["violations"].as_array().unwrap().iter().any(|v| v["rule"] == "DOEFF150"), "{first}");
    let parent = tempfile::tempdir().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HOF);
    assert_eq!(keys(&first), keys(&second));
    assert!(scan_reads(&stderr) <= FILLERS / 4, "{stderr}");
    write(&fresh, "declarations/words.hy", "(defarchitecture-lists sample :retired-words [])\n");
    let (changed, _) = single(&fresh, cache.path(), HOF);
    assert!(!changed["violations"].as_array().unwrap().iter().any(|v| v["rule"] == "DOEFF150"), "{changed}");
    let empty = tempfile::tempdir().unwrap();
    let (uncached, _) = single(&fresh, empty.path(), HOF);
    assert_eq!(keys(&changed), keys(&uncached));
}

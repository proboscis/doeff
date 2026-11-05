//! A test that the facts cache is shared among worktrees — runs the binary in a temporary store and counts the bytes newly written to the store.
//!
//! On zeus, hundreds of worktrees are made per day and a checkout refreshes every file's modification time, so the whole facts cache (about 208MB) was rebuilt per worktree,
//! writing about 3.4GB per hour to a USB SSD. A fact is determined only by the file's contents, its path from the root, and the linter's marker, so facts of files with the same contents are
//! shared as one even across different worktrees, and per worktree only a small table of file -> fact key is put.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF106", "DOEFF122", "DOEFF126", "DOEFF127", "DOEFF129", "DOEFF130"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample :root "app" :layers [(layer core :summary "Core") (layer foundation :summary "Foundation")] :foundation foundation)
"#;

const MODULES: usize = 24;

/// The contents of one module — has a dozen or so definitions (a size where the facts become several KB per file). Modules call each other, so the inference world also spans files.
fn module_source(i: usize) -> String {
    let mut text = String::from("(import time)\n");
    // A real module has a dozen or so imports (the module-dependency fact becomes larger than one row of the table).
    for k in 1..=12 {
        text.push_str(&format!("(import app.core.mod{})\n", (i + k) % MODULES));
    }
    text.push_str(&format!("(defrecord Box{i} [value])\n(defeffect Ping{i} {{:fields [(: n int)] :answer None}})\n"));
    for j in 0..10 {
        text.push_str(&format!(
            "(defk step-{i}-{j} [x] {{:tags {{:context \"c\" :role \"program\"}} :effects [Ping{i}]}}\n  (<- y (Ping{i} {j}))\n  (<- z (Ping{i} (+ y {j})))\n  [y z x])\n"
        ));
    }
    let next = (i + 1) % MODULES;
    text.push_str(&format!(
        "(defk run-{i} [x] {{:tags {{:context \"c\" :role \"program\"}}}}\n  (<- a (step-{i}-0 x))\n  (<- b (step-{i}-9 a))\n  (<- c (app.core.mod{next}.step-{next}-0 b))\n  c)\n"
    ));
    text.push_str(&format!("(defn now-{i} [] (time.time))\n"));
    text
}

/// Builds one worktree's repo in dir. Built with the same function, it is a tree with the same contents and only different modification times (a re-checked-out worktree).
fn checkout(dir: &Path) {
    std::fs::write(dir.join("pyproject.toml"), CONFIG).unwrap();
    std::fs::write(dir.join("architecture.hy"), ARCHITECTURE).unwrap();
    std::fs::create_dir_all(dir.join("app/core")).unwrap();
    for i in 0..MODULES {
        std::fs::write(dir.join(format!("app/core/mod{i}.hy")), module_source(i)).unwrap();
    }
}

/// Runs the binary at root and returns the output (editor-json). If cache = Some(dir) it uses the cache in that store, and if None it uses none.
/// `DOEFF_LINTER_CHECK_SLOT` is the re-entry marker of the machine's check slot — this test runs without waiting for the machine's slot.
fn lint(root: &Path, cache: Option<&Path>) -> String {
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env_remove("DOEFF_LINTER_NO_CACHE");
    match cache {
        Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir).env("DOEFF_LINTER_SWEEP_INLINE", "1"),
        None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
    };
    let output = command.output().unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8(output.stdout).unwrap()
}

/// (size, modification time) of the files in the store (per path).
fn snapshot(cache: &Path) -> HashMap<PathBuf, (u64, SystemTime)> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| {
            let meta = e.metadata().unwrap();
            (e.path().to_path_buf(), (meta.len(), meta.modified().unwrap()))
        })
        .collect()
}

fn total(snapshot: &HashMap<PathBuf, (u64, SystemTime)>) -> u64 {
    snapshot.values().map(|(len, _)| len).sum()
}

/// Bytes newly written to the store after before (the size of a new file or an updated file).
fn written_since(before: &HashMap<PathBuf, (u64, SystemTime)>, cache: &Path) -> u64 {
    snapshot(cache).iter().filter(|(path, stamp)| before.get(*path) != Some(stamp)).map(|(_, (len, _))| len).sum()
}

/// Paths of files in the store (excluding the root record `root.path`) that contain text somewhere in their contents.
fn files_containing(cache: &Path, text: &str) -> Vec<PathBuf> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        // The root dir's `root.path` records that root's path (read by the cleanup of dirs whose root is gone) — not a shared fact.
        .filter(|e| e.file_name() != "root.path")
        .filter(|e| std::fs::read(e.path()).is_ok_and(|bytes| bytes.windows(text.len()).any(|w| w == text.as_bytes())))
        .map(|e| e.path().to_path_buf())
        .collect()
}

/// Acceptance test: in 2 worktrees checked out at the same commit, the bytes the 2nd newly writes under the facts cache are under 10% of the 1st's.
/// The answer is the same as a run that uses no cache (location paths point to the 2nd worktree's files).
#[test]
fn a_second_checkout_of_the_same_tree_writes_less_than_a_tenth_of_the_first() {
    let cache = tempfile::TempDir::new().unwrap();
    let (first, second) = (tempfile::TempDir::new().unwrap(), tempfile::TempDir::new().unwrap());
    checkout(first.path());
    checkout(second.path());

    lint(first.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);
    assert!(written_first > 100_000, "the facts put by the 1st are too small to compare: {written_first} bytes");
    // The kinds of facts (Hy index, signature facts and inference, defk names, failure types, record fields, argument calls) are put.
    for kind in ["hy-index.bin", "signature-facts.bin", "signature-summaries.bin", "defk-names.json", "program-params.json"] {
        assert!(
            after_first.keys().any(|path| path.components().any(|c| c.as_os_str() == kind)),
            "no fact of {kind} was put: {:?}",
            after_first.keys().collect::<Vec<_>>()
        );
    }

    let cached = lint(second.path(), Some(cache.path()));
    let written_second = written_since(&after_first, cache.path());
    assert!(
        written_second * 10 < written_first,
        "the bytes {written_second} newly written by the 2nd worktree under the facts cache are 10% or more of the 1st's {written_first}"
    );
    assert_eq!(cached, lint(second.path(), None), "the answer from the shared facts differs from a run that uses no cache");
}

/// No worktree absolute path remains in the store (if it did, a fact read by another worktree would point at a file of a different worktree).
#[test]
fn no_working_tree_path_is_left_in_the_shared_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    let output = lint(tree.path(), Some(cache.path()));
    // Confirm that this run emits location paths (= it passes through the kinds of facts that hold paths).
    let canonical = std::fs::canonicalize(tree.path()).unwrap();
    assert!(output.contains(canonical.to_str().unwrap()), "the output has no worktree path — this is not a test that passes through location paths");
    for text in [canonical.to_str().unwrap(), tree.path().to_str().unwrap()] {
        assert_eq!(files_containing(cache.path(), text), Vec::<PathBuf>::new(), "the worktree path {text} remained in the store");
    }
}

/// A worktree that rewrote 1 file newly writes only that file's fact and the table — facts of other files are not rebuilt.
#[test]
fn editing_one_file_writes_only_that_files_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    lint(tree.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);

    std::thread::sleep(std::time::Duration::from_millis(20));
    let edited = tree.path().join("app/core/mod3.hy");
    let mut text = std::fs::read_to_string(&edited).unwrap();
    // A rewrite that does not add a defk (if the fingerprint of defk names changes, all argument-call facts are rebuilt — a different matter).
    text.push_str("(defn added [x] (+ x 1))\n");
    std::fs::write(&edited, text).unwrap();
    let cached = lint(tree.path(), Some(cache.path()));
    let written = written_since(&after_first, cache.path());
    assert!(written * 10 < written_first, "the bytes {written} newly written by rewriting 1 file are 10% or more of the total {written_first}");
    assert_eq!(cached, lint(tree.path(), None), "the answer after the rewrite differs from a run that uses no cache");
}

/// The 2nd run of the same worktree writes nothing (neither facts nor the table).
#[test]
fn a_second_run_in_the_same_tree_writes_no_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    lint(tree.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    lint(tree.path(), Some(cache.path()));
    let rewritten: Vec<PathBuf> = snapshot(cache.path())
        .into_iter()
        .filter(|(path, stamp)| after_first.get(path) != Some(stamp))
        // The used marker (empty contents) and the record of the store scan time are updated every time.
        .filter(|(path, _)| !matches!(path.file_name().and_then(|n| n.to_str()), Some("used" | ".swept" | ".swept-cap")))
        .map(|(path, _)| path)
        .collect();
    assert_eq!(rewritten, Vec::<PathBuf>::new(), "the 2nd run of an unchanged worktree rewrote the cache");
}

/// `--affected-tests` (the module-dependency fact — `module-dependencies`) is also shared across worktrees. The population is the files git knows,
/// so the worktree is made a git repo.
#[test]
fn module_dependencies_are_shared_between_checkouts_too() {
    let git = |dir: &Path, args: &[&str]| {
        let status = Command::new("git").arg("-C").arg(dir).args(args).status().unwrap();
        assert!(status.success(), "git {args:?}");
    };
    let affected = |root: &Path, cache: Option<&Path>| {
        let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
        command
            .args(["--affected-tests", "app/core/mod0.hy", "--test-pattern", "test_*.hy", "--no-log"])
            .current_dir(root)
            .env("DOEFF_LINTER_CHECK_SLOT", "1")
            .env_remove("DOEFF_LINTER_NO_CACHE");
        match cache {
            Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir).env("DOEFF_LINTER_SWEEP_INLINE", "1"),
            None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
        };
        let output = command.output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8(output.stdout).unwrap()
    };
    let cache = tempfile::TempDir::new().unwrap();
    let (first, second) = (tempfile::TempDir::new().unwrap(), tempfile::TempDir::new().unwrap());
    for tree in [first.path(), second.path()] {
        checkout(tree);
        std::fs::create_dir_all(tree.join("tests")).unwrap();
        std::fs::write(tree.join("tests/test_mod0.hy"), "(import app.core.mod0 [run-0])\n(deftest test-run (assert (run-0 1)))\n").unwrap();
        git(tree, &["init", "-q"]);
        git(tree, &["add", "-A"]);
    }
    affected(first.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);
    assert!(after_first.keys().any(|path| path.components().any(|c| c.as_os_str() == "module-dependencies.json")), "the module-dependency fact was not put");

    let cached = affected(second.path(), Some(cache.path()));
    // What the 2nd writes is only the per-root table — it writes no facts (under `objects`). This test's module-dependency facts are small (several hundred bytes per file),
    // the same order as one table row, so it is checked by the presence of facts rather than 10% of the total (the ratio of the total is the test above).
    let written_facts: Vec<PathBuf> = snapshot(cache.path())
        .into_iter()
        .filter(|(path, stamp)| {
            // The used marker `used` (empty contents) is updated on every run.
            path.components().any(|c| c.as_os_str() == "objects") && path.file_name() != Some("used".as_ref()) && after_first.get(path) != Some(stamp)
        })
        .map(|(path, _)| path)
        .collect();
    assert_eq!(written_facts, Vec::<PathBuf>::new(), "the 2nd worktree re-put the module-dependency facts of files with the same contents");
    let written_second = written_since(&after_first, cache.path());
    assert!(written_second * 4 < written_first, "the table of {written_second} bytes newly written by the 2nd worktree is 1/4 or more of the 1st's {written_first}");
    assert_eq!(cached, affected(second.path(), None));
    assert!(cached.contains("tests/test_mod0.hy"), "the test file was not reached: {cached}");
}

// ── Lifetime of facts when several worktrees share them─────────────────────────────
// Facts are shared across roots, so one root's table alone cannot decide "no longer needed". If the lookup path deleted facts on the grounds that "this root no longer reads them",
// all the presentation checks (comparing answers, rewriting one worktree) would pass while facts used by another worktree vanished and had to be rewritten. Only cleanup
// (limit, oldest first) may delete.

/// After running worktrees A and B at the same commit, rewrite 1 file in A and run. Then B runs once more without changing anything. A's run does not
/// delete the facts B uses, and B's 2nd run writes no facts.
#[test]
fn editing_in_one_tree_does_not_remove_facts_another_tree_uses() {
    let cache = tempfile::TempDir::new().unwrap();
    let (a, b) = (tempfile::TempDir::new().unwrap(), tempfile::TempDir::new().unwrap());
    checkout(a.path());
    checkout(b.path());
    lint(a.path(), Some(cache.path()));
    lint(b.path(), Some(cache.path()));
    let facts_before = facts(cache.path());

    std::thread::sleep(std::time::Duration::from_millis(20));
    let edited = a.path().join("app/core/mod3.hy");
    let mut text = std::fs::read_to_string(&edited).unwrap();
    text.push_str("(defn added [x] (+ x 1))\n");
    std::fs::write(&edited, text).unwrap();
    lint(a.path(), Some(cache.path()));
    let facts_after_a = facts(cache.path());
    let removed: Vec<&PathBuf> = facts_before.keys().filter(|path| !facts_after_a.contains_key(*path)).collect();
    assert!(removed.is_empty(), "A's run deleted {} facts that B also uses: {:?}", removed.len(), removed);

    let after_a = snapshot(cache.path());
    let cached_b = lint(b.path(), Some(cache.path()));
    assert_eq!(cached_b, lint(b.path(), None), "B's answer differs from a run that uses no cache");
    let rewritten = written_facts_since(&after_a, cache.path());
    assert_eq!(rewritten, 0, "B's 2nd run of an unchanged worktree rewrote {rewritten} bytes of facts");
}

/// After worktree A moves to a different commit and runs, a new worktree C of the original commit runs. The bytes C newly writes are under 10% of A's 1st
/// (A did not delete the facts of the previous commit).
#[test]
fn a_tree_that_moves_on_does_not_make_the_next_checkout_of_its_old_commit_rewrite() {
    let cache = tempfile::TempDir::new().unwrap();
    let a = tempfile::TempDir::new().unwrap();
    checkout(a.path());
    lint(a.path(), Some(cache.path()));
    let written_first = total(&snapshot(cache.path()));

    std::thread::sleep(std::time::Duration::from_millis(20));
    for i in 0..MODULES {
        let path = a.path().join(format!("app/core/mod{i}.hy"));
        let mut text = std::fs::read_to_string(&path).unwrap();
        text.push_str(&format!("(defn moved-{i} [] {i})\n"));
        std::fs::write(&path, text).unwrap();
    }
    lint(a.path(), Some(cache.path()));
    let after_move = snapshot(cache.path());

    let c = tempfile::TempDir::new().unwrap();
    checkout(c.path());
    let cached_c = lint(c.path(), Some(cache.path()));
    let written_c = written_since(&after_move, cache.path());
    assert!(written_c * 10 < written_first, "the bytes {written_c} written by the new worktree C of the original commit are 10% or more of A's 1st {written_first}");
    assert_eq!(cached_c, lint(c.path(), None), "C's answer differs from a run that uses no cache");
}

/// Facts one by one in the store (under `objects/`, excluding the used marker `used` and temporary files).
fn facts(cache: &Path) -> HashMap<PathBuf, (u64, SystemTime)> {
    snapshot(&cache.join("objects"))
        .into_iter()
        .filter(|(path, _)| !path.file_name().and_then(|n| n.to_str()).is_some_and(|name| name == "used" || name.ends_with(".tmp")))
        .collect()
}

/// Fact bytes newly written under `objects/` after before.
fn written_facts_since(before: &HashMap<PathBuf, (u64, SystemTime)>, cache: &Path) -> u64 {
    facts(cache).iter().filter(|(path, stamp)| before.get(*path) != Some(stamp)).map(|(_, (len, _))| len).sum()
}

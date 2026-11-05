//! Tests for the range read by a single-file run (`--output-format editor-json --stdin --path <file>` — the hook right after a write, and the editor).
//!
//! A single-file check has its answer decided by the file written and the definitions that file names. It used to read all the repo's Hy files (for each kind of fact,
//! one file at a time, about 10,000 opens, 0.5-1.5 s CPU per run in agora-controllers) and then judge the one written file — on a busy Pod (load 40-100)
//! it took 2-7 seconds of wall time waiting on reads, and the advice was cut off by the hook's deadline (4 seconds) (7 of 31 measured runs on 10-12).
//!
//! Two things are checked:
//! - Answer: the violations of a single-file run are the same as the violations a whole-repo run gives for that file (the answer does not change between a run that reads everything and one that reads only the named files).
//!   Failure types, Programs passed as arguments, and class frozen-ness are decided by declarations in files that are not named (reverse dependencies), so even a file that needs them gets the same answer.
//! - Range read: facts of modules that are not named are not placed in the cache. A file that needs no reverse dependencies places no facts of those kinds.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Stdio};

const MODULES: usize = 24;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF101", "DOEFF106", "DOEFF122", "DOEFF126", "DOEFF127", "DOEFF129", "DOEFF130", "DOEFF142"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "core" :roles [program] :imports [core]) (layer foundation :summary "foundation" :roles [foundation] :imports [foundation])]
  :foundation foundation
  :handler-arguments {:files ["app/**/*.hy"] :exclude ["**/tests/**"] :keep-mark "keep:"})
"#;

const TAGS: &str = "{:tags {:context \"c\" :role \"program\"}}";

/// The written file (W). modules 0-2 call each other, and 3 onward are another cycle (not reachable from W by calls).
fn module_source(i: usize) -> String {
    let mut text = String::from("(import time)\n");
    if i == 0 {
        // core imports foundation (DOEFF101) — a file that is foundation by location, and a file in a core dir that the tag's role pushes to foundation.
        text.push_str("(import app.core.mod1)\n(import app.foundation.f)\n(import app.core.proxy)\n");
    }
    text.push_str(&format!("(defrecord Box{i} [value])\n(defeffect Ping{i} {{:fields [(: n int)] :answer None}})\n"));
    text.push_str(&format!("(defk step-{i}-0 [x] {{:tags {{:context \"c\" :role \"program\"}} :effects [Ping{i}]}}\n  (<- y (Ping{i} {i}))\n  [y x])\n"));
    // 0 -> 1 -> 2 call. 3 onward are the cycle 3 -> 4 -> ... -> 3.
    let next = match i {
        0 => Some(1),
        1 => Some(2),
        2 => None,
        n if n + 1 < MODULES => Some(n + 1),
        _ => Some(3),
    };
    match next {
        Some(next) => text.push_str(&format!(
            "(defk run-{i} [x] {TAGS}\n  (<- a (step-{i}-0 x))\n  (<- c (app.core.mod{next}.step-{next}-0 a))\n  c)\n"
        )),
        None => text.push_str(&format!("(defk run-{i} [x] {TAGS}\n  (<- a (step-{i}-0 x))\n  a)\n")),
    }
    if i == 0 {
        // A bare call of a defk (DOEFF126), and a raw side effect outside the layers (DOEFF106).
        text.push_str(&format!("(defk bare-0 [x] {TAGS}\n  (+ 1 (app.core.mod1.step-1-0 x)))\n(defn now-0 [] (time.time))\n"));
    } else {
        text.push_str(&format!("(defn now-{i} [] (time.time))\n"));
    }
    text
}

/// Files whose answer is decided by declarations in files that are not named (reverse dependencies), and the files of those declarations.
fn reverse_files() -> Vec<(&'static str, String)> {
    vec![
        ("app/foundation/f.hy", "(defn f [] 1)\n".to_string()),
        ("app/core/proxy.hy", "(val MODULE-TAGS {:context \"c\" :role \"foundation\"})\n(defn p [] 1)\n".to_string()),
        // Failure type: Unreachable in errs is declared by the :failure of a defeffect in effs (errs does not declare it itself).
        ("app/core/errs.hy", "(defrecord Unreachable [reason])\n(defrecord Accepted [word])\n".to_string()),
        (
            "app/core/effs.hy",
            "(import app.core.errs)\n(defeffect ReadRow {:fields [key] :answer None :failure [app.core.errs.Unreachable]})\n".to_string(),
        ),
        (
            "app/core/needs.hy",
            format!(
                "(import app.core.errs [Unreachable Accepted])\n(defk decide [x] {TAGS}\n  (<- said (| Accepted Unreachable) (verdict x))\n  (match said\n    (Unreachable) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail)))\n                      (return refused))\n    (Accepted) None))\n"
            ),
        ),
        // Program passed as an argument: the user passes fnk to field-of of hof.
        (
            "app/core/hof.hy",
            format!("(defk rows-by-text [rows field-of] {TAGS}\n  (for [row rows]\n    (setv value (field-of row)))\n  rows)\n"),
        ),
        (
            "app/core/user.hy",
            format!(
                "(import app.core.hof [rows-by-text])\n(defk tag-rows-of [tags] {TAGS}\n  (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p \"subject\")))))\n"
            ),
        ),
        // class frozen: handlers uses the argument type Cfg. Cfg in cfgs, which it imports, is frozen; the same-named Cfg in legacy, which it does not import, is not frozen
        // (the index looked up by name says "not frozen if there is even one same name that is not frozen").
        ("app/core/cfgs.hy", "(defrecord Cfg [#^ str url])\n".to_string()),
        ("app/core/legacy.hy", "(defclass Cfg [] (setv x 1))\n".to_string()),
        (
            "app/core/handlers.hy",
            "(import app.core.cfgs [Cfg])\n(defhandler h [#^ Cfg cfg]\n  (Ping0 [e k] (k 1)))\n".to_string(),
        ),
    ]
}

fn checkout(dir: &Path) {
    std::fs::write(dir.join("pyproject.toml"), CONFIG).unwrap();
    std::fs::write(dir.join("architecture.hy"), ARCHITECTURE).unwrap();
    std::fs::create_dir_all(dir.join("app/core")).unwrap();
    for i in 0..MODULES {
        std::fs::write(dir.join(format!("app/core/mod{i}.hy")), module_source(i)).unwrap();
    }
    for (rel, text) in reverse_files() {
        std::fs::create_dir_all(dir.join(rel).parent().unwrap()).unwrap();
        std::fs::write(dir.join(rel), text).unwrap();
    }
}

fn editor(root: &Path, cache: &Path, args: &[&str], stdin: Option<&str>) -> (Value, String) {
    use std::io::Write;
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command
        .args(["--output-format", "editor-json", "--no-log"])
        .args(args)
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_TIMING", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({e}): {stdout}\n{stderr}"));
    (report, stderr)
}

/// The violation keys of file `rel` (sorted).
fn keys_of(report: &Value, rel: &str) -> Vec<String> {
    let prefix = format!("{rel}::");
    let mut keys: Vec<String> = report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v["key"].as_str())
        .filter(|k| k.starts_with(&prefix))
        .map(str::to_string)
        .collect();
    keys.sort();
    keys
}

/// Paths of files in the store (excluding the root record `root.path`) whose contents contain the text somewhere.
fn files_containing(cache: &Path, text: &str) -> Vec<std::path::PathBuf> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.file_name() != "root.path")
        .filter(|e| std::fs::read(e.path()).is_ok_and(|bytes| bytes.windows(text.len()).any(|w| w == text.as_bytes())))
        .map(|e| e.path().to_path_buf())
        .collect()
}

/// Among the kinds of facts in the store (`objects/<kind>.<shape>`), the kinds in which at least one fact was placed.
fn stored_kinds(cache: &Path) -> Vec<String> {
    let objects = cache.join("objects");
    let Ok(entries) = std::fs::read_dir(&objects) else { return Vec::new() };
    let mut kinds: Vec<String> = entries
        .filter_map(Result::ok)
        .filter(|e| walkdir::WalkDir::new(e.path()).into_iter().filter_map(Result::ok).any(|f| f.file_type().is_file()))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    kinds.sort();
    kinds
}

fn whole_report(root: &Path) -> Value {
    let cache = tempfile::TempDir::new().unwrap();
    editor(root, cache.path(), &[], None).0
}

fn single_report(root: &Path, cache: &Path, rel: &str) -> (Value, String) {
    let source = std::fs::read_to_string(root.join(rel)).unwrap();
    editor(root, cache, &["--stdin", "--path", &root.join(rel).to_string_lossy()], Some(&source))
}

/// Answer: a single-file run gives the same violations as a whole-repo run gives for that file. Every file has at least one hit (so it does not pass on an empty comparison).
#[test]
fn a_single_file_run_answers_like_the_whole_run_for_that_file() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let whole = whole_report(dir.path());
    for (rel, rules) in [
        ("app/core/mod0.hy", &["DOEFF101", "DOEFF126", "DOEFF106"][..]),
        ("app/core/needs.hy", &["DOEFF122"][..]),
        ("app/core/hof.hy", &["DOEFF126"][..]),
        ("app/core/handlers.hy", &["DOEFF142"][..]),
    ] {
        let expected = keys_of(&whole, rel);
        for rule in rules {
            assert!(expected.iter().any(|k| k.contains(rule)), "{rel} does not hit {rule} (error in the test material): {expected:?}");
        }
        let cache = tempfile::TempDir::new().unwrap();
        let (single, stderr) = single_report(dir.path(), cache.path(), rel);
        assert_eq!(keys_of(&single, rel), expected, "single-file run of {rel} (no cache)\n{stderr}");
        // The same answer after the cache is warm.
        let (again, _) = single_report(dir.path(), cache.path(), rel);
        assert_eq!(keys_of(&again, rel), expected, "single-file run of {rel} (with cache)");
    }
}

/// Range read: places only the facts of modules reachable by calls from the written file (mod0 -> mod1 -> mod2). Facts of unreachable modules (3 onward) are not placed.
#[test]
fn a_single_file_run_stores_facts_only_for_the_modules_the_file_names() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (_, stderr) = single_report(dir.path(), cache.path(), "app/core/mod0.hy");
    // The facts of reachable modules (0-2) are placed — a check that the test is not vacuous.
    for i in 1..=2 {
        assert!(!files_containing(cache.path(), &format!("Box{i}")).is_empty(), "facts of mod{i} were not placed (a reachable module)\n{stderr}");
    }
    for i in 3..MODULES {
        let found = files_containing(cache.path(), &format!("Box{i}"));
        assert!(found.is_empty(), "facts of mod{i} (not reachable from mod0) were placed: {found:?}\n{stderr}");
    }
    // A file that needs no reverse dependencies (mod0) places no facts of those kinds.
    let kinds = stored_kinds(cache.path());
    for kind in ["failure-types.json", "program-params.json", "handler-argument-classes.json"] {
        assert!(!kinds.iter().any(|k| k == kind), "{kind}, which mod0 does not need, was placed: {kinds:?}\n{stderr}");
    }
}

/// Among reverse dependencies, a single-file run of a file that needs a Program passed as an argument (the caller is another file) analyzes only files that contain the callee's name literally:
/// it does not go through the whole-repo table, and places neither the defk names of non-caller modules (3 onward) nor the facts of Programs passed as arguments in the cache (the first run on a cold worktree
/// used to rebuild all the repo's Hy and take 8-13 seconds). The answer is guarded by `a_single_file_run_answers_like_the_whole_run_for_that_file`.
#[test]
fn a_single_file_run_that_needs_the_callers_reads_only_the_files_that_spell_the_callee() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (report, stderr) = single_report(dir.path(), cache.path(), "app/core/hof.hy");
    // A check that the test is not vacuous: the caller (user.hy) passes fnk, so the bare call in hof.hy hits.
    assert!(keys_of(&report, "app/core/hof.hy").iter().any(|k| k.contains("DOEFF126")), "hof.hy does not hit DOEFF126 (error in the test material)\n{stderr}");
    assert!(!stderr.contains("doeff-linter-timing: facts-cache.resolve "), "went through the whole-repo table (rebuilding all files to find the callers)\n{stderr}");
    for i in 3..MODULES {
        let found = files_containing(cache.path(), &format!("step_{i}_0"));
        assert!(found.is_empty(), "the defk name of mod{i} (not a caller of hof.hy) was placed: {found:?}\n{stderr}");
    }
    let kinds = stored_kinds(cache.path());
    assert!(!kinds.iter().any(|k| k == "program-params.json"), "facts of Programs passed as arguments across the whole repo were placed: {kinds:?}\n{stderr}");
}

/// Among reverse dependencies, a single-file run of a file that needs a failure-type declaration (the `:failure` of a defeffect in another file) also reads only the declaration files that contain the type's name literally:
/// it does not go through the whole-repo table, and the failure-type facts placed are only for the declaration files that spell the type's name (the first run on a cold worktree took 19-27 seconds).
/// The parsed result of a declaration file is stored so that the next run does not parse the same file again (it was most of the hook's CPU on a busy machine). The store key is the same as in a whole run.
/// The answer is guarded by `a_single_file_run_answers_like_the_whole_run_for_that_file`.
#[test]
fn a_single_file_run_that_needs_the_failure_types_reads_only_the_declarations_that_spell_the_type() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (report, stderr) = single_report(dir.path(), cache.path(), "app/core/needs.hy");
    // A check that the test is not vacuous: effs.hy declares Unreachable a failure type, so the arm in needs.hy hits DOEFF122.
    assert!(keys_of(&report, "app/core/needs.hy").iter().any(|k| k.contains("DOEFF122")), "needs.hy does not hit DOEFF122 (error in the test material)\n{stderr}");
    assert!(!stderr.contains("doeff-linter-timing: facts-cache.resolve "), "went through the whole-repo table (rebuilding all files to find the declaration)\n{stderr}");
    // The placed failure-type fact is only for the declaration file that spells the type's name (the one effs.hy).
    let stored: Vec<_> = walkdir::WalkDir::new(cache.path().join("objects").join("failure-types.json"))
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .collect();
    assert_eq!(stored.len(), 1, "failure-type facts do not match the number of declaration files: {:?}\n{stderr}", stored.iter().map(|e| e.path().to_path_buf()).collect::<Vec<_>>());
    // The next run reads the stored parse (the same answer).
    let (again, _) = single_report(dir.path(), cache.path(), "app/core/needs.hy");
    assert_eq!(keys_of(&again, "app/core/needs.hy"), keys_of(&report, "app/core/needs.hy"), "the answer drawn from the stored parse differs");
}

/// A single-file run goes through neither the table that looks up facts of whole-repo files (`facts-cache.resolve`) nor the full scan of layer modules (`layer-files`).
#[test]
fn a_single_file_run_does_not_walk_the_whole_repo_tables() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (_, stderr) = single_report(dir.path(), cache.path(), "app/core/mod0.hy");
    for stage in ["facts-cache.resolve", "layer-files"] {
        assert!(!stderr.contains(&format!("doeff-linter-timing: {stage} ")), "went through {stage} (reading the whole repo)\n{stderr}");
    }
}

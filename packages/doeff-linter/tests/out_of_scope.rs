//! Check for files outside the named scope — run the binary in a temporary package whose root is `src`, and confirm that naming a Hy file outside the root (`tests/`)
//! reports it as "out of scope" and keeps it distinct from green (spec section 1, "outside the named scope").
//!
//! Failure case: write a violation (a raw side effect) in a test file outside the root and name it — previously the layer rules did not judge the file and the exit code was 0 with no output
//! (the shape in which the .hy files under doeff-cluster's tests/ were measured by neither the hook nor local checks). Now it is named in one line and the exit code is 3.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

const CONFIG: &str = r#"
[tool.doeff-linter]
root = "src"
enable = ["DOEFF106"]

[tool.doeff-linter.layers]
order = ["core", "foundation"]
paths = { core = "app/core", foundation = "app/foundation" }

[tool.doeff-linter.layers.allow_imports]
core = ["core"]
foundation = ["foundation"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const PLAIN: &str = "(defn plan [x] x)\n";
const RAW: &str = "(import time)\n(defn now [] (time.time))\n";

/// Root = a temporary package with src: one layer-core file under the root, and one Hy file and one Python file that contain violations in tests/ outside the root.
fn package() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("src/app/core")).unwrap();
    std::fs::create_dir_all(dir.path().join("src/app/foundation")).unwrap();
    std::fs::create_dir_all(dir.path().join("tests")).unwrap();
    std::fs::write(dir.path().join("src/app/core/plan.hy"), PLAIN).unwrap();
    std::fs::write(dir.path().join("tests/test_clock.hy"), RAW).unwrap();
    std::fs::write(dir.path().join("tests/test_plain.py"), "def test_x():\n    assert True\n").unwrap();
    dir
}

fn lint(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log"])
        .args(args)
        .current_dir(dir)
        .env("DOEFF_LINTER_NO_CACHE", "1")
        .output()
        .unwrap()
}

fn editor_report(output: &Output) -> Value {
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}", e, stdout))
}

/// Whether stderr names the file rel (path from the package dir) in an "out of scope" line — the named path comes out as a normalized absolute path
/// (the same shape as a violation's path), so compare using the spelling `/<rel>(`.
fn named_outside(stderr: &str, rel: &str) -> bool {
    stderr.lines().any(|line| line.contains("out of scope — ") && line.contains(&format!("/{}(", rel)))
}

#[test]
fn a_named_hy_file_outside_the_root_is_named_and_not_green() {
    // Failure case: name a Hy file in tests/ outside the root (with a raw side effect written in it) — the layer rules do not judge it, so it is named rather than silent, with exit code 3.
    let dir = package();
    let output = lint(dir.path(), &["tests/test_clock.hy"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(named_outside(&stderr, "tests/test_clock.hy"), "{}", stderr);
    assert!(stderr.contains("1 of the named Hy files are out of scope"), "{}", stderr);
    assert_eq!(output.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_named_hy_file_under_the_root_is_judged_as_before() {
    // Passing case: naming a file under the root behaves as before — no out-of-scope line appears, and the exit code is 0 if there are no violations.
    let dir = package();
    let output = lint(dir.path(), &["src/app/core/plan.hy"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains("out of scope"), "{}", stderr);
    assert_eq!(output.status.code(), Some(0), "{}", stderr);
    // Writing a violation in a file under the same root makes the layer rules judge it and exit with code 1 (distinct from out of scope).
    std::fs::write(dir.path().join("src/app/core/plan.hy"), RAW).unwrap();
    let raw = lint(dir.path(), &["src/app/core/plan.hy"]);
    assert_eq!(raw.status.code(), Some(1), "{}", String::from_utf8_lossy(&raw.stderr));
}

#[test]
fn the_declaration_file_outside_the_root_is_not_out_of_scope() {
    // Failure case: a service and a layer declaration (architecture.hy) placed outside the package root (src) are judged by the declaration rules — doeff-cluster's commit
    // hook (scripts/lint-doeff-cluster.sh) always adds this file when it passes paths, so counting it as out of scope would give exit code 3 every time.
    let dir = tempfile::TempDir::new().unwrap();
    let files = [
        ("pyproject.toml", "[tool.doeff-linter]\nroot = \"src\"\narchitecture = \"architecture.hy\"\ndisable = [\"DOEFF114\", \"DOEFF115\"]\n"),
        (
            "architecture.hy",
            "(defarchitecture pkg :root \"cluster\" :layers [(layer foundation :summary \"foundation\")] :foundation foundation)\n",
        ),
        ("src/cluster/coord.hy", "(defk run [] 1)\n"),
        ("tests/test_coord.hy", RAW),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let hook = lint(dir.path(), &["src/cluster/coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&hook.stderr);
    assert!(!stderr.contains("out of scope"), "{}", stderr);
    assert_eq!(hook.status.code(), Some(0), "{}", stderr);
    // A test file outside the root of the same package is still out of scope even when named alongside the declaration.
    let tests = lint(dir.path(), &["tests/test_coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&tests.stderr);
    assert!(named_outside(&stderr, "tests/test_coord.hy"), "{}", stderr);
    assert!(!named_outside(&stderr, "architecture.hy"), "{}", stderr);
    assert_eq!(tests.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_named_directory_opens_to_its_hy_files_and_skips_python() {
    // Naming a dir opens it and names the Hy files beneath it. Python files are not counted, because the Python rules collect and measure them from the named path.
    let dir = package();
    let output = lint(dir.path(), &["tests"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(named_outside(&stderr, "tests/test_clock.hy"), "{}", stderr);
    assert!(!stderr.contains("test_plain.py"), "{}", stderr);
    assert_eq!(output.status.code(), Some(3), "{}", stderr);
}

#[test]
fn the_editor_report_carries_the_out_of_scope_list() {
    // editor-json (a child of the commit hook) puts them in the top-level field out_of_scope with exit code 3. If only files under the root, it is an empty list with 0; if nothing is named, null.
    let dir = package();
    let outside = lint(dir.path(), &["--output-format", "editor-json", "tests/test_clock.hy"]);
    // Same as a violation's path, a normalized absolute path (one).
    let listed = editor_report(&outside)["out_of_scope"].clone();
    assert_eq!(listed.as_array().map(Vec::len), Some(1), "{}", listed);
    assert!(listed[0].as_str().is_some_and(|p| p.starts_with('/') && p.ends_with("/tests/test_clock.hy")), "{}", listed);
    assert_eq!(outside.status.code(), Some(3), "{}", String::from_utf8_lossy(&outside.stderr));
    let inside = lint(dir.path(), &["--output-format", "editor-json", "src/app/core/plan.hy"]);
    assert_eq!(editor_report(&inside)["out_of_scope"], serde_json::json!([]));
    assert_eq!(inside.status.code(), Some(0), "{}", String::from_utf8_lossy(&inside.stderr));
    let whole = lint(dir.path(), &["--output-format", "editor-json"]);
    assert_eq!(editor_report(&whole)["out_of_scope"], Value::Null);
}

#[test]
fn the_commit_hook_reads_the_out_of_scope_list() {
    // The commit hook reads the child's out_of_scope report and names it (if the field is absent or null, it is empty — even a child of an earlier version does not fail).
    let report = serde_json::json!({"violations": [], "out_of_scope": ["tests/test_clock.hy"]});
    assert_eq!(doeff_linter::commit_hook::out_of_scope_of(&report), vec!["tests/test_clock.hy".to_string()]);
    assert!(doeff_linter::commit_hook::out_of_scope_of(&serde_json::json!({"violations": []})).is_empty());
    assert!(doeff_linter::commit_hook::out_of_scope_of(&serde_json::json!({"out_of_scope": null})).is_empty());
}

// ---- Dirs added to the walked scope (the setting `include`) ----

/// Root = a package with src. Writing `include` also walks tests/ outside the root, and the rules for how definitions are written (here only critical DOEFF126) judge it.
fn walked_package(include: bool, test_source: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let settings = format!(
        "[tool.doeff-linter]\nroot = \"src\"\narchitecture = \"architecture.hy\"\n{}enable = [\"DOEFF126\"]\n\
         [tool.doeff-linter.definitions]\npaths = [\"../tests\"]\ntest_paths = [\"../tests/**/test_*.hy\"]\n",
        if include { "include = [\"tests\"]\n" } else { "" }
    );
    let files = [
        ("pyproject.toml", settings.as_str()),
        (
            "architecture.hy",
            "(defarchitecture pkg :root \"cluster\" :layers [(layer foundation :summary \"foundation\")] :foundation foundation)\n",
        ),
        ("src/cluster/coord.hy", "(defk plan [x] {:tags {:context \"c\" :role \"program\"}} x)\n"),
        ("tests/test_coord.hy", test_source),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// In a test file, call src's defk bare and use it as the thing to compare (a Program flows as a value — DOEFF126 critical).
const BARE_TEST: &str = "(import cluster.coord [plan])\n(deftest test-plan (assert (= (plan 1) 1)))\n";
/// Call the same test, answering with `!` (the fixed form).
const BOUND_TEST: &str = "(import cluster.coord [plan])\n(deftest test-plan (assert (= (! (plan 1)) 1)))\n";

fn rule_keys(report: &Value, rule: &str) -> Vec<String> {
    report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| v["rule"] == rule)
        .map(|v| v["key"].as_str().unwrap_or("").to_string())
        .collect()
}

#[test]
fn an_included_directory_outside_the_root_is_judged_by_the_definition_rules() {
    // Failure case: a test in tests/ outside the root calls src's defk bare. With include, the definition rules judge it and it is red with DOEFF126 (the claimed name contains `..`
    // relative to the root). Without include it is not judged, and naming it gives "out of scope" with exit code 3 (the earlier shape — not measured).
    let walked = walked_package(true, BARE_TEST);
    let whole = lint(walked.path(), &["--output-format", "editor-json"]);
    let report = editor_report(&whole);
    assert_eq!(rule_keys(&report, "DOEFF126"), vec!["../tests/test_coord.hy::DOEFF126::test_plan::plan".to_string()], "{}", report);
    assert_eq!(whole.status.code(), Some(1), "{}", String::from_utf8_lossy(&whole.stderr));
    let named = lint(walked.path(), &["tests/test_coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&named.stderr);
    let stdout = String::from_utf8_lossy(&named.stdout);
    assert!(!stderr.contains("out of scope"), "{}", stderr);
    assert!(stdout.contains("DOEFF126") || stderr.contains("DOEFF126"), "{}\n{}", stdout, stderr);
    assert_eq!(named.status.code(), Some(1), "{}\n{}", stdout, stderr);

    let unwalked = walked_package(false, BARE_TEST);
    let report = editor_report(&lint(unwalked.path(), &["--output-format", "editor-json"]));
    assert!(rule_keys(&report, "DOEFF126").is_empty(), "{}", report);
    let named = lint(unwalked.path(), &["tests/test_coord.hy"]);
    let stderr = String::from_utf8_lossy(&named.stderr);
    assert!(named_outside(&stderr, "tests/test_coord.hy"), "{}", stderr);
    assert_eq!(named.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_fixed_test_in_the_included_directory_is_green() {
    // Passing case: calling the same test answered with `!` is green (a file in the include dir gets the same judgment as a file under the root).
    let dir = walked_package(true, BOUND_TEST);
    let output = lint(dir.path(), &["tests/test_coord.hy", "architecture.hy"]);
    assert_eq!(output.status.code(), Some(0), "{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
}

#[test]
fn a_missing_included_directory_is_a_setting_error() {
    // Writing a nonexistent dir in include is named as a settings error instead of silently walking nothing.
    let dir = walked_package(true, BOUND_TEST);
    std::fs::remove_dir_all(dir.path().join("tests")).unwrap();
    let output = lint(dir.path(), &["--output-format", "editor-json"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("include dir"), "{}", stderr);
}

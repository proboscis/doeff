//! Test for DOEFF210 (a Hy program does not sleep on the wall clock -- doeff's ADR-DOE-TIME-001 R1, R2).
//! Runs the binary and looks at the output of `--output-format editor-json`.
//!
//!   * The set of lines marked "; fires" in the fixture tests/fixtures/program_sleep/waits.hy equals the set of hit lines (polarity -- removing the rule, removing the exclusion of the handler
//!     implementations, or adding an exclusion each make the sets disagree and turn red).
//!   * The population is decided only by the config's `[tool.doeff-linter.program_sleep]` (no section means no judgment; nothing under an excluded segment is hit).
//!   * A single-file run (the editor's `--stdin --path`) produces the same hits.
//!   * There are no hits in the Hy programs of this repo's (doeff) main code -- the section in the root pyproject.toml makes the rule effective, and the count must be 0.
//!     Writing a new bare sleep in a program turns this test red naming the file and line (the rust processing stage of the daily full verification).

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF210";
/// Marker of the lines in the fixture that should fire.
const FIRES: &str = "; fires";
/// Config that places programs under app/ (tests and examples are excluded).
const CONFIG: &str = "[tool.doeff-linter]\n\n[tool.doeff-linter.program_sleep]\npaths = [\"app\"]\nexclude_parts = [\"tests\", \"examples\"]\n";

/// The fixture file (lines that fire are marked).
fn fixture() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/program_sleep/waits.hy");
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("cannot read fixture {}: {}", path.display(), error))
}

/// The root of this repo (doeff).
fn doeff_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("the doeff root exists")
}

/// A temporary repo (pyproject.toml and a list of files).
fn repo(config: &str, files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Runs the binary at root and returns the editor-json (stdin can be passed).
fn editor(root: &Path, args: &[&str], stdin: Option<&str>) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .args(args)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(&stdout).unwrap_or_else(|error| panic!("not JSON ({}): {}\n{}", error, stdout, String::from_utf8_lossy(&output.stderr)))
}

/// The violations of this rule in a report (still as JSON values).
fn violations(report: &Value) -> Vec<&Value> {
    report["violations"].as_array().expect("list of violations").iter().filter(|v| v["rule"] == RULE).collect()
}

/// The set of lines (1-based) where this rule hit.
fn hit_lines(report: &Value) -> BTreeSet<u64> {
    violations(report).iter().map(|v| v["range"]["start"]["line"].as_u64().expect("line") + 1).collect()
}

/// The set of lines (1-based) in the source that have the marker at the end of the line.
fn marked_lines(source: &str) -> BTreeSet<u64> {
    source.lines().zip(1u64..).filter(|(line, _)| line.trim_end().ends_with(FIRES)).map(|(_, number)| number).collect()
}

/// Whether this run judged this rule (judged_rules of editor-json).
fn judged(report: &Value) -> bool {
    report["judged_rules"].as_array().expect("list of judged_rules").iter().any(|id| id == RULE)
}

/// Whether this rule's judgment is wired to the repo's config (wired in rules of editor-json -- true only when the section for the location exists).
fn wired(report: &Value) -> bool {
    report["rules"].as_array().expect("list of rules").iter().any(|rule| rule["rule"] == RULE && rule["wired"] == true)
}

#[test]
fn only_the_marked_lines_fire() {
    let source = fixture();
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 7, "the number of marked lines in the fixture changed -- if you added or removed a firing shape, fix this number too");
    let dir = repo(CONFIG, &[("app/waits.hy", &source)]);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} is not judged: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "the hit lines differ from the fixture's marked lines: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_program_and_the_callee() {
    let dir = repo(CONFIG, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    let keys: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("key")).collect();
    let expected: BTreeSet<&str> = [
        "app/waits.hy::DOEFF210::retry-after-a-pause::time.sleep",
        "app/waits.hy::DOEFF210::poll-until-ready::asyncio.sleep",
        "app/waits.hy::DOEFF210::waits-through-aliases::time.sleep",
        "app/waits.hy::DOEFF210::waits-through-aliases::asyncio.sleep",
        "app/waits.hy::DOEFF210::settle-then-go::time.sleep",
        "app/waits.hy::DOEFF210::sleeps-inside-a-nested-callable::time.sleep",
        "app/waits.hy::DOEFF210::program-without-tags::time.sleep",
    ]
    .into_iter()
    .collect();
    assert_eq!(keys, expected);
    for violation in violations(&report) {
        assert_eq!(violation["level"], "critical", "{}", violation);
        assert_eq!(violation["severity"], "error", "{}", violation);
    }
}

#[test]
fn the_rule_judges_nothing_without_its_section() {
    // Do not apply to a repo with no declaration of the location (do not let 0 findings read as "protected" -- even with other sections, wired in rules is false).
    let config = "[tool.doeff-linter]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n";
    let dir = repo(config, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "claimed the judgment of {} is wired although there is no section", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn places_outside_the_section_are_not_judged() {
    // Files outside the location (lib/) and under the excluded segments (tests, examples) are not hit.
    let source = fixture();
    let dir = repo(
        CONFIG,
        &[("lib/waits.hy", &source), ("app/tests/waits.hy", &source), ("app/pkg/examples/waits.hy", &source), ("app/pkg/waits.hy", &source)],
    );
    let report = editor(dir.path(), &[], None);
    let files: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("key").split("::").next().unwrap()).collect();
    assert_eq!(files, ["app/pkg/waits.hy"].into_iter().collect());
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // Also apply to the editor's pre-save content (--stdin --path) -- visible at the moment of writing.
    let source = fixture();
    let dir = repo(CONFIG, &[("app/waits.hy", "(defk clean [] {:pre [] :post [(: % None)]} None)\n")]);
    let report = editor(dir.path(), &["--stdin", "--path", "app/waits.hy"], Some(&source));
    assert!(judged(&report), "the single-file run did not judge {}: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // Writing it in disable means it is not judged (this test is the counterpart showing why the "this repo's main code" test below turns red on disable).
    let config = CONFIG.replace("[tool.doeff-linter]\n", "[tool.doeff-linter]\ndisable = [\"DOEFF210\"]\n");
    let dir = repo(&config, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "claimed to have judged {} although it was written in disable: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_hy_programs_of_this_repo_do_not_sleep() {
    // With the doeff root's config (pyproject.toml's [tool.doeff-linter.program_sleep]), the main code's Hy programs have no hits.
    // Removing the section or adding DOEFF210 to disable turns red with "not judged"; writing a bare sleep in a program turns red with a hit.
    let root = doeff_root();
    let report = editor(&root, &["doeff", "packages"], None);
    assert!(
        judged(&report) && wired(&report),
        "the doeff root's config does not judge {} -- check the [tool.doeff-linter.program_sleep] section and disable in pyproject.toml",
        RULE
    );
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(
        found.is_empty(),
        "a Hy program sleeps on the wall clock -- wait with a doeff-time effect (Delay, etc.) (a helper called only from handlers should declare :role foundation):\n{}",
        found.join("\n")
    );
}

#[test]
fn the_sample_of_a_sleeping_program_is_found_under_the_repo_s_own_settings() {
    // That the 0 findings above are not "because the population is empty": with the doeff root's config as is, a fixture placed once in the main code's location (pre-save content) is hit.
    let root = doeff_root();
    let source = fixture();
    let report = editor(&root, &["--stdin", "--path", "packages/doeff-traverse/doeff_traverse/sample_waits.hy"], Some(&source));
    assert_eq!(hit_lines(&report), marked_lines(&source));
    // The tests' location (tests/) is outside this rule's population (sleeping in Hy tests belongs to DOEFF211 -- tests/test_sleep.rs).
    let in_tests = editor(&root, &["--stdin", "--path", "packages/doeff-traverse/tests/sample_waits.hy"], Some(&source));
    assert!(violations(&in_tests).is_empty(), "{:#?}", violations(&in_tests));
}

//! Tests for DOEFF211 (Hy tests do not sleep on the wall clock unless they declare realtime; doeff's ADR-DOE-TIME-001 R3).
//! Runs the binary and looks at the output of `--output-format editor-json`.
//!
//!   * The set of lines marked "; fires" in the fixture tests/fixtures/test_sleep/sleeps.hy equals the set of hit lines (polarity: removing the rule, narrowing the reading of the declaration,
//!     widening it, or dropping the reading of a paraphrase each makes the sets disagree and turns the test red).
//!   * The fixture realtime_module.hy, which declares at the module level, has no hits, and removing the one declaration line makes it hit (the 0 hits are not because of an empty population).
//!   * The population is decided only by the settings' `[tool.doeff-linter.test_sleep]` (without the section nothing is judged; dir of test locations, files named as tests, and excluded dirs).
//!   * A one-file run (the editor's `--stdin --path`) produces the same hits.
//!   * This repo's (doeff) Hy tests have no hits: the section in the root pyproject.toml enables the rule, and the count is 0. Writing a bare
//!     sleep without declaring it in a Hy test turns this test red, naming the file and line (the daily full verification's stage rust). That declared tests are in 1-to-1 correspondence with the roster
//!     tests/realtime_tests.toml is checked by tests/semgrep/test_realtime_tests_registry.py.

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF211";
/// Marker for a line that should fire in a fixture.
const FIRES: &str = "; fires";
/// The one line of the module-level declaration (inside the fixture realtime_module.hy).
const MODULE_DECLARATION: &str = "(val pytestmark [pytest.mark.slow pytest.mark.realtime])\n";
/// A configuration that puts the test location at "a name under a tests dir" and "files named as tests", and excludes the rule fixtures' dir and the named script file.
const CONFIG: &str = "[tool.doeff-linter]\n\n[tool.doeff-linter.test_sleep]\nparts = [\"tests\"]\nnames = [\"test_*.hy\", \"*_test.hy\"]\nexclude = [\"pkg/tests/samples\", \"pkg/tests/stub_cli/fake_cli.hy\"]\n";

/// The fixture file (lines that should fire are marked).
fn fixture(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/test_sleep").join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("cannot read fixture {}: {}", path.display(), error))
}

/// The root of this repo (doeff).
fn doeff_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("doeff's root exists")
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

/// Runs the binary with root as the root and returns editor-json (stdin can be passed).
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

/// This rule's violations in the report (as JSON values).
fn violations(report: &Value) -> Vec<&Value> {
    report["violations"].as_array().expect("violations list").iter().filter(|v| v["rule"] == RULE).collect()
}

/// The set of lines (1-based) of this rule's hits.
fn hit_lines(report: &Value) -> BTreeSet<u64> {
    violations(report).iter().map(|v| v["range"]["start"]["line"].as_u64().expect("line") + 1).collect()
}

/// The set of files (the head of the key: path from the repo root) of this rule's hits.
fn hit_files(report: &Value) -> BTreeSet<&str> {
    violations(report).iter().map(|v| v["key"].as_str().expect("key").split("::").next().unwrap()).collect()
}

/// The set of lines (1-based) in source that are marked at the end of the line.
fn marked_lines(source: &str) -> BTreeSet<u64> {
    source.lines().zip(1u64..).filter(|(line, _)| line.trim_end().ends_with(FIRES)).map(|(_, number)| number).collect()
}

/// Whether this run judged this rule (judged_rules of editor-json).
fn judged(report: &Value) -> bool {
    report["judged_rules"].as_array().expect("judged_rules list").iter().any(|id| id == RULE)
}

/// Whether this rule's judgment is connected to the repo's settings (wired in editor-json's rules: true only when the location section exists).
fn wired(report: &Value) -> bool {
    report["rules"].as_array().expect("rules list").iter().any(|rule| rule["rule"] == RULE && rule["wired"] == true)
}

#[test]
fn only_the_marked_lines_fire() {
    let source = fixture("sleeps.hy");
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 14, "the number of marked lines in the fixture changed; if you added or removed a form that fires, fix this number too");
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", &source)]);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} was not judged: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "the hit lines differ from the fixture's marked lines: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_definition_and_the_callee() {
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    let keys: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("key")).collect();
    let expected: BTreeSet<&str> = [
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-for-the-server-by-sleeping::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::poll-until-ready::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::all-closed::asyncio.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-through-aliases::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-through-aliases::asyncio.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-rewords-the-sleep-as-an-event-nobody-sets::threading.Event.wait",
        "pkg/tests/test_waits.hy::DOEFF211::test-marked-with-another-mark::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-with-a-map-that-is-not-the-meta::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::helper-with-marks-in-its-contract::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-decorated-with-another-mark::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-with-the-mark-in-its-parameters::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::declares-inside-a-function::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::<module>::time.sleep",
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
fn a_module_that_declares_realtime_has_no_hits_and_loses_them_only_by_the_declaration() {
    let source = fixture("realtime_module.hy");
    assert!(source.contains(MODULE_DECLARATION), "the fixture's module-level declaration line changed");
    let dir = repo(CONFIG, &[("pkg/tests/support_rig.hy", &source), ("pkg/tests/undeclared_rig.hy", &source.replace(MODULE_DECLARATION, ""))]);
    let report = editor(dir.path(), &[], None);
    // The declaring file does not hit, and the same contents with the one declaration line removed get two sleeps hit.
    assert_eq!(hit_files(&report), ["pkg/tests/undeclared_rig.hy"].into_iter().collect());
    assert_eq!(violations(&report).len(), 2, "{:#?}", violations(&report));
}

#[test]
fn the_rule_judges_nothing_without_its_section() {
    // Do not apply to a repo with no location declaration (do not let 0 hits read as "protected"; even if other sections exist, wired in rules is false).
    let config = "[tool.doeff-linter]\n\n[tool.doeff-linter.program_sleep]\npaths = [\"pkg\"]\n";
    let dir = repo(config, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "claimed that the judgment of {} is wired although there is no section", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn the_population_is_the_test_places_and_the_test_names() {
    // Test locations = files that have tests as a path segment (at any depth, including tools not named as tests), and files named as tests wherever they are.
    // The body (src), dirs whose names merely contain tests, files under excluded dirs, and files excluded by name are not applied (a file next to a named file is applied).
    let source = fixture("sleeps.hy");
    let dir = repo(
        CONFIG,
        &[
            ("pkg/tests/test_waits.hy", &source),
            ("pkg/tests/deep/support_rig.hy", &source),
            ("pkg/suite/test_waits.hy", &source),
            ("pkg/suite/waits_test.hy", &source),
            ("tests/rig.hy", &source),
            ("pkg/suite/harness.hy", &source),
            ("pkg/src/app/waits.hy", &source),
            ("pkg/src/contests/waits.hy", &source),
            ("pkg/tests/samples/test_waits.hy", &source),
            ("pkg/tests/stub_cli/fake_cli.hy", &source),
            ("pkg/tests/stub_cli/other_cli.hy", &source),
        ],
    );
    let report = editor(dir.path(), &[], None);
    let expected: BTreeSet<&str> =
        ["pkg/tests/test_waits.hy", "pkg/tests/deep/support_rig.hy", "pkg/suite/test_waits.hy", "pkg/suite/waits_test.hy", "tests/rig.hy", "pkg/tests/stub_cli/other_cli.hy"]
            .into_iter()
            .collect();
    assert_eq!(hit_files(&report), expected);
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // Also apply to the editor's pre-save contents (--stdin --path): it is visible at the time of writing.
    let source = fixture("sleeps.hy");
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", "(deftest test-clean (assert True))\n")]);
    let report = editor(dir.path(), &["--stdin", "--path", "pkg/tests/test_waits.hy"], Some(&source));
    assert!(judged(&report), "the one-file run did not judge {}: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // If written in disable, it is not judged (this test is the counterpart that shows why the test "this repo's tests" below turns red on disable).
    let config = CONFIG.replace("[tool.doeff-linter]\n", "[tool.doeff-linter]\ndisable = [\"DOEFF211\"]\n");
    let dir = repo(&config, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "claimed to have judged {} written in disable: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_hy_tests_of_this_repo_sleep_only_where_they_declare_realtime() {
    // With the settings at doeff's root (pyproject.toml [tool.doeff-linter.test_sleep]), the repo's Hy tests have no hits.
    // Removing the section or adding DOEFF211 to disable turns it red with "not judged", and writing a bare sleep in an undeclared test turns it red with a hit.
    let root = doeff_root();
    let report = editor(&root, &["."], None);
    assert!(
        judged(&report) && wired(&report),
        "the settings at doeff's root did not judge {}: check the [tool.doeff-linter.test_sleep] section and disable in pyproject.toml",
        RULE
    );
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(
        found.is_empty(),
        "a Hy test sleeps on the wall clock without declaring it: wait on an event or a virtual clock. If real time is the subject, declare realtime (deftest :marks or the module's pytestmark) and list it with the reason in tests/realtime_tests.toml:\n{}",
        found.join("\n")
    );
}

#[test]
fn the_sample_of_a_sleeping_test_is_found_under_the_repo_s_own_settings() {
    // So that the 0 hits above are not "because the population is empty": with doeff's root settings as they are, a form with one fixture placed at a test location (pre-save contents) does hit.
    let root = doeff_root();
    let source = fixture("sleeps.hy");
    for place in [
        "packages/doeff-records/tests/sample_waits.hy",
        "tests/sample_waits.hy",
        "packages/doeff-cluster/tests/fixtures/sample_job.hy",
        "packages/doeff-records/tests/stub_cli/sample_cli.hy",
        "examples/sample/test_waits.hy",
    ] {
        let report = editor(&root, &["--stdin", "--path", place], Some(&source));
        assert_eq!(hit_lines(&report), marked_lines(&source), "the hits of the fixture placed at {} differ from the marked lines", place);
    }
    // The body's location (program sleeps belong to DOEFF210) and the rule fixtures' dir are outside this rule's population.
    for place in ["packages/doeff-records/src/doeff_records/sample_waits.hy", "packages/doeff-linter/tests/fixtures/test_sleep/sample_waits.hy", "tests/semgrep/fixtures/sample_waits.hy"] {
        let outside = editor(&root, &["--stdin", "--path", place], Some(&source));
        assert!(violations(&outside).is_empty(), "{}: {:#?}", place, violations(&outside));
    }
}

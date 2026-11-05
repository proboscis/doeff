//! Tests for DOEFF175 (stale branches on an effect whose declaration was switched — ADR-DOE-CORE-EFFECTS-003 R14 (4), law declarations-switch-one-effect-at-a-time).
//! Runs the binary and looks at the output of `--output-format editor-json`.
//!
//!   * The set of lines marked "; fires" in the fixture tests/fixtures/stale_outcome_branch/branches.hy equals the set of hit lines (polarity — removing the rule, narrowing or
//!     widening the shape, or dropping the declaration read all make the sets differ and turn red). Shapes that fire = match arm, isinstance, tuple of types, cond, a binding with a type annotation,
//!     `!` (the binding form and the directly-split form), a binding with :absent, module alias, a second effect, and a test caller. Shapes that do not fire = a :value type,
//!     a success type, an effect with no declaration, a defhandler clause, a same-named type in another module, another effect's declaration, a rebound name, a name hidden by an argument,
//!     an answer wrapped in maybe, a type inside a pattern field, an answer passed through defk, and quote.
//!   * The declaration is decided only by the defeffect in the fixture effects.hy (removing the declaration makes hits 0, and adding a declaration to an effect with none makes it hit).
//!   * The population is decided by the settings `[tool.doeff-linter.definitions]` (without the section it is not judged; outside the locations it does not apply).
//!   * A single-file run (the editor's `--stdin --path`) produces the same hits.
//!   * There are no hits in this repo's (doeff) production code — the settings in the root pyproject.toml enable the rule, and it stays at 0 (the daily full verification's
//!     processing stage rust). The ADRs (docs/adr) and the doeff-core-effects probe tests are outside doeff's definitions population (`paths` is only the 3 main dirs).

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF175";
/// The marker of a line in the fixture that should fire.
const FIRES: &str = "; fires";
/// Settings that put the definitions rule population in app/ (enabling only this rule).
const CONFIG: &str = "[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n";

/// A fixture file (marking the lines that fire).
fn fixture(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/stale_outcome_branch").join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("cannot read fixture {}: {}", path.display(), error))
}

/// The list of files of the fixture repo (the declaration, a same-named type in another module, and the user side).
fn files() -> Vec<(&'static str, String)> {
    vec![("app/effects.hy", fixture("effects.hy")), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))]
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

/// The fixture repo (passing the settings).
fn fixture_repo(config: &str) -> tempfile::TempDir {
    let owned = files();
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    repo(config, &borrowed)
}

/// Runs the binary at the root and returns editor-json (stdin can be passed).
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
    report["violations"].as_array().expect("array of violations").iter().filter(|v| v["rule"] == RULE).collect()
}

/// The set of this rule's hit lines (1-based).
fn hit_lines(report: &Value) -> BTreeSet<u64> {
    violations(report).iter().map(|v| v["range"]["start"]["line"].as_u64().expect("line") + 1).collect()
}

/// The set of this rule's hit keys.
fn keys(report: &Value) -> BTreeSet<String> {
    violations(report).iter().map(|v| v["key"].as_str().expect("key").to_string()).collect()
}

/// The set of lines (1-based) in the source with the marker at the end of the line.
fn marked_lines(source: &str) -> BTreeSet<u64> {
    source.lines().zip(1u64..).filter(|(line, _)| line.trim_end().ends_with(FIRES)).map(|(_, number)| number).collect()
}

/// Whether this run judged this rule (judged_rules in editor-json).
fn judged(report: &Value) -> bool {
    report["judged_rules"].as_array().expect("array of judged_rules").iter().any(|id| id == RULE)
}

/// Whether this rule's judgment is wired to the repo's settings (wired in rules of editor-json — true only when the definitions population section exists).
fn wired(report: &Value) -> bool {
    report["rules"].as_array().expect("array of rules").iter().any(|rule| rule["rule"] == RULE && rule["wired"] == true)
}

#[test]
fn only_the_marked_lines_fire() {
    let source = fixture("branches.hy");
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 17, "the number of marked lines in the fixture changed — if you added or removed a firing shape, fix this number too");
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} was not judged: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "the hit lines differ from the marked lines of the fixture: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_definition_the_name_and_the_type() {
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    let expected: BTreeSet<String> = [
        "match-an-absent-arm::found::Missing",
        "match-a-failure-arm::found::Unreachable",
        "match-an-alternation::found::Missing",
        "match-an-alternation::found::Unreachable",
        "isinstance-an-absent::found::Missing",
        "isinstance-a-tuple::found::Missing",
        "isinstance-a-tuple::found::Unreachable",
        "isinstance-a-union::found::Unreachable",
        "cond-branch::found::Unreachable",
        "typed-bind::found::Unreachable",
        "bang-bound::found::Missing",
        "bang-subject::ReadRow::Missing",
        "bang-subject::ReadRow::Unreachable",
        "absent-suffix-bind::found::Missing",
        "module-alias::found::effects.Missing",
        "a-second-effect::ack::Refused",
        "a-second-effect::ack::Gone",
        "nested-in-a-branch::found::Missing",
        "reads-a-row-in-a-test::found::Missing",
    ]
    .into_iter()
    .map(|detail| format!("app/branches.hy::{}::{}", RULE, detail.replace('-', "_")))
    .collect();
    assert_eq!(keys(&report), expected);
    for violation in violations(&report) {
        assert_eq!(violation["level"], "critical", "{}", violation);
        assert_eq!(violation["severity"], "error", "{}", violation);
    }
}

#[test]
fn the_message_names_the_effect_the_declaration_and_the_way_out() {
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    let hit = violations(&report).into_iter().find(|v| v["key"].as_str().unwrap_or("").ends_with("::match_an_absent_arm::found::Missing")).expect("a hit");
    let message = hit["message"].as_str().expect("message");
    assert!(
        message.contains("ReadRow declared Missing as absent") && message.contains("<- does not bind Missing to found, so this branch (match arm) is never taken") && message.contains("maybe / on-raise / :absent"),
        "{}",
        message
    );
    let failure = violations(&report).into_iter().find(|v| v["key"].as_str().unwrap_or("").ends_with("::a_second_effect::ack::Refused")).expect("a hit");
    assert!(failure["message"].as_str().unwrap_or("").contains("PostRow declared Refused as failure"), "{}", failure["message"]);
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // Apply it also to the editor's pre-save contents (--stdin --path) — the declaration is collected from other files (effects.hy), reading only the files that spell the name being looked up.
    let source = fixture("branches.hy");
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &["--stdin", "--path", "app/branches.hy"], Some(&source));
    assert!(judged(&report), "the single-file run did not judge {}: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
    let whole = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), keys(&whole), "the hit keys of the single-file run differ from the whole run");
}

#[test]
fn the_declaration_decides_not_the_name_of_the_type() {
    // Removing the declaration (:absent / :failure) makes hits 0 — it does not infer from the type names (Missing, Unreachable).
    let stripped = fixture("effects.hy").replace(":absent [Missing]", "").replace(":failure [Unreachable]", "").replace(":absent [Gone]", "").replace(":failure [Refused]", "");
    let owned = [("app/effects.hy", stripped), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))];
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let dir = repo(CONFIG, &borrowed);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report));
    assert!(violations(&report).is_empty(), "hit even though the declaration was removed: {:#?}", violations(&report));
    // Conversely, adding a declaration to an effect with none (PlainRead) makes that branch hit.
    let declared = fixture("effects.hy").replace(
        "   :answer (| Row Missing Unreachable)\n   :tags {:context \"sample\" :role \"intent\"}})\n",
        "   :answer (| Row Missing Unreachable)\n   :absent [Missing]\n   :tags {:context \"sample\" :role \"intent\"}})\n",
    );
    assert_ne!(declared, fixture("effects.hy"), "the fixture rewrite had no effect");
    let owned = [("app/effects.hy", declared), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))];
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let dir = repo(CONFIG, &borrowed);
    let report = editor(dir.path(), &[], None);
    let plain: Vec<String> = keys(&report).into_iter().filter(|k| k.contains("::plain_effect::")).collect();
    assert_eq!(plain, vec![format!("app/branches.hy::{}::plain_effect::found::Missing", RULE)], "{:#?}", violations(&report));
}

#[test]
fn the_rule_judges_nothing_without_the_definitions_section() {
    // Do not apply to a repo with no population declaration (do not let 0 hits be read as "protected").
    let dir = fixture_repo("[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n");
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "claimed the judgment of {} is wired even though there is no section", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn places_outside_the_population_are_not_judged_and_test_places_are() {
    // Outside the locations (lib/) it does not apply. Hy in the test locations (test_paths) does apply — test callers also go stale on a switch.
    let uses = fixture("branches.hy");
    let config = "[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\ntest_paths = [\"checks/test_*.hy\"]\n";
    let effects = fixture("effects.hy");
    let dir = repo(config, &[("app/effects.hy", &effects), ("lib/branches.hy", &uses), ("checks/test_branches.hy", &uses)]);
    let report = editor(dir.path(), &[], None);
    let places: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("key").split("::").next().unwrap()).collect();
    assert_eq!(places, ["checks/test_branches.hy"].into_iter().collect(), "{:#?}", violations(&report));
}

#[test]
fn the_registry_does_not_lower_a_hit() {
    // Listing in the registry does not lower it to a warning — that version is not landed until hits reach 0 (the gate is not opened with a known list).
    let key = format!("app/branches.hy::{}::match_an_absent_arm::found::Missing", RULE);
    let config = format!("{}\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n", CONFIG);
    let owned = files();
    let mut borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let known = format!("{}\n", key);
    borrowed.push(("known.txt", known.as_str()));
    let dir = repo(&config, &borrowed);
    let report = editor(dir.path(), &[], None);
    let hit = violations(&report).into_iter().find(|v| v["key"] == key.as_str()).expect("hits listed in the registry also appear");
    assert_eq!(hit["level"], "critical", "{}", hit);
    assert_eq!(hit["severity"], "error", "{}", hit);
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // Writing it in disable means it is not judged (this test is the counterpart showing why the test of "this repo's production code" below turns disable red).
    let config = CONFIG.replace("enable = [\"DOEFF175\"]\n", "disable = [\"DOEFF175\"]\n");
    let dir = fixture_repo(&config);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "claimed to have judged {} that was written in disable: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_production_code_of_this_repo_has_no_stale_branches() {
    // With doeff's root settings (pyproject.toml [tool.doeff-linter.definitions]), the production code has no hits.
    // Adding to disable or deleting the definitions section turns red with "not judged"; writing a stale branch on an effect whose declaration was switched turns red with a hit.
    // The ADRs (docs/adr/*.hy) and the doeff-core-effects probe tests are outside this population (the 3 main dirs of `paths`).
    let root = doeff_root();
    let report = editor(&root, &["packages"], None);
    assert!(judged(&report) && wired(&report), "doeff's root settings did not judge {} — check the pyproject.toml [tool.doeff-linter.definitions] section and disable", RULE);
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(found.is_empty(), "stale branches on an effect whose declaration was switched remain — use only the success path and move absence/failure to :absent / on-raise / maybe:\n{}", found.join("\n"));
}

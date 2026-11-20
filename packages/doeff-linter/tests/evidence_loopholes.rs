//! Tests that the two ways around DOEFF212 found in the trial are closed by the :guard of :design-evidence (DOEFF214 and DOEFF212's Grew).
//! Each test runs the same change twice in a git repo: under the declaration without :guard (the rule as it was tried — the change passes) and under the declaration with
//! :guard (the change is red). Runs the binary and looks at the output of `--output-format editor-json`.
//!
//!   * The ledger: a line added for a new type, and a fingerprint rewritten for a reshaped type, pass without :guard and are red (DOEFF214) with it; a removed line passes.
//!   * A field added to a type of the ledger with complete evidence (a one-sentence concern) passes without :guard and is red (DOEFF212 Grew) with it — for a defrecord and a
//!     Rust struct. An approval of the field added on the same branch, or one at the base whose review is not a request id of the declared spelling, does not let it pass;
//!     an approval at the base with a request id does. A variant added to a Rust enum of the ledger is not a grown field (it needs evidence only).

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

const OPEN: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy" "app/**/*.rs"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"})
"#;

const GUARDED: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy" "app/**/*.rs"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"
                    :guard {:base "main" :approvals "design/approvals.toml" :approval-pattern r"^req-[0-9]+$"}})
"#;

const KEPT_HY: &str = "(defrecord Kept\n  (#^ str card))\n";
const KEPT_GROWN_HY: &str = "(defrecord Kept\n  (#^ str card)\n  (#^ str reviewer))\n";
const KEPT_GROWN_EVIDENCE: &str = r#"schema = 1

[[element]]
name = "Kept"
kind = "record"
concern = "everything about one finished card"
key = ["card"]
fan_out = 1
review = "rv-1"

[[element.field]]
name = "card"
type = "str"
fact = "the card"

[[element.field]]
name = "reviewer"
type = "str"
fact = "who reviewed the card"

[[element.rejected]]
shape = "a separate review type"
why = "one more file"
"#;

const USER_RS: &str = "pub struct User {\n    pub id: u64,\n}\n\npub enum Role {\n    Admin,\n}\n";
const USER_GROWN_RS: &str = "pub struct User {\n    pub id: u64,\n    pub email: String,\n}\n\npub enum Role {\n    Admin,\n    Member,\n}\n";
const USER_GROWN_EVIDENCE: &str = r#"schema = 1

[[element]]
name = "User"
kind = "record"
concern = "one user and how to reach them"
key = ["id"]
fan_out = 1
review = "rv-2"

[[element.field]]
name = "id"
type = "u64"
fact = "the user"

[[element.field]]
name = "email"
type = "String"
fact = "where the user is reached"

[[element.rejected]]
shape = "a contact type"
why = "one more type"

[[element]]
name = "Role"
kind = "record"
concern = "what a user may do"
key = []
fan_out = 1
review = "rv-3"

[[element.field]]
name = "Admin"
fact = "may do everything"

[[element.field]]
name = "Member"
fact = "may do their own work"

[[element.rejected]]
shape = "a flag on User"
why = "a third role would need another flag"
"#;

const REQUEST: &str = "req-42";

fn approval(ledger: &str, key: &str, value: &str, review: &str) -> String {
    format!("[[approval]]\nledger = \"{}\"\nkey = \"{}\"\nvalue = \"{}\"\nreview = \"{}\"\nwhy = \"the user approved the field\"\n", ledger, key, value, review)
}

fn git(dir: &Path, args: &[&str]) {
    let output = Command::new("git").args(args).current_dir(dir).output().unwrap();
    assert!(output.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&output.stderr));
}

fn write(dir: &Path, rel: &str, text: &str) {
    let path = dir.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

/// A git repo whose branch main holds the declaration, the files, and the ledger written by `--print-existing-shapes` over them (the rule turned on), and whose HEAD is a
/// branch off main.
fn turned_on(architecture: &str, files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    write(root, "pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF212\", \"DOEFF214\"]\n");
    write(root, "architecture.hy", architecture);
    for (rel, text) in files {
        write(root, rel, text);
    }
    let (code, ledger, stderr) = linter(root, &["--print-existing-shapes"], None);
    assert_eq!(code, 0, "{}", stderr);
    write(root, "design-existing.toml", &ledger);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "turn the rule on"]);
    git(root, &["checkout", "-q", "-b", "work"]);
    dir
}

/// Commits the branch's change, lands one file alone on main first (an approval), and puts the branch back on top of main.
fn land_on_main(root: &Path, rel: &str, text: &str) {
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "the change"]);
    git(root, &["checkout", "-q", "main"]);
    write(root, rel, text);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "an approval lands alone"]);
    git(root, &["checkout", "-q", "work"]);
    git(root, &["rebase", "-q", "main"]);
}

fn linter(root: &Path, args: &[&str], stdin: Option<&str>) -> (i32, String, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log"])
        .args(args)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stdout).to_string(), String::from_utf8_lossy(&output.stderr).to_string())
}

/// The keys of the DOEFF212 and DOEFF214 violations of a whole run.
fn keys(root: &Path) -> BTreeSet<String> {
    let (_, stdout, stderr) = linter(root, &["--output-format", "editor-json"], None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|error| panic!("not JSON ({}): {}\n{}", error, stdout, stderr));
    report["violations"]
        .as_array()
        .expect("violations list")
        .iter()
        .filter(|v| v["rule"] == "DOEFF212" || v["rule"] == "DOEFF214")
        .map(|v| v["key"].as_str().unwrap_or("").to_string())
        .collect()
}

fn set(keys: &[&str]) -> BTreeSet<String> {
    keys.iter().map(|k| k.to_string()).collect()
}

/// The ledger's fingerprint of a key, as written by `--print-existing-shapes` over the tree now.
fn fingerprint_now(root: &Path, key: &str) -> String {
    let (_, ledger, _) = linter(root, &["--print-existing-shapes"], None);
    let line = ledger.lines().find(|line| line.starts_with(&format!("\"{}\"", key))).unwrap_or_else(|| panic!("no {} in {}", key, ledger));
    line.rsplit('"').nth(1).unwrap().to_string()
}

/// Adds a line to the ledger (the way around: a new type passes as an existing one).
fn add_ledger_line(root: &Path, key: &str, fingerprint: &str) {
    let ledger = std::fs::read_to_string(root.join("design-existing.toml")).unwrap();
    write(root, "design-existing.toml", &format!("{}\"{}\" = \"{}\"\n", ledger, key, fingerprint));
}

/// Rewrites a line of the ledger (the way around: a reshaped type passes as unchanged).
fn rewrite_ledger_line(root: &Path, key: &str, fingerprint: &str) {
    let ledger = std::fs::read_to_string(root.join("design-existing.toml")).unwrap();
    let rewritten: String = ledger
        .lines()
        .map(|line| if line.starts_with(&format!("\"{}\"", key)) { format!("\"{}\" = \"{}\"\n", key, fingerprint) } else { format!("{}\n", line) })
        .collect();
    write(root, "design-existing.toml", &rewritten);
}

/// The ledger way around, on one repo: a new type with its ledger line added, an existing type reshaped with its fingerprint rewritten, and a type removed with its line.
fn ledger_way_around(root: &Path) {
    write(root, "app/core/fresh.hy", "(defrecord Fresh\n  (#^ int n))\n");
    add_ledger_line(root, "app/core/fresh.hy::Fresh", &fingerprint_now(root, "app/core/fresh.hy::Fresh"));
    write(root, "app/core/kept.hy", "(defrecord Kept\n  (#^ int card))\n");
    rewrite_ledger_line(root, "app/core/kept.hy::Kept", &fingerprint_now(root, "app/core/kept.hy::Kept"));
    std::fs::remove_file(root.join("app/core/gone.hy")).unwrap();
    let ledger = std::fs::read_to_string(root.join("design-existing.toml")).unwrap();
    write(root, "design-existing.toml", &ledger.lines().filter(|l| !l.contains("Gone")).map(|l| format!("{}\n", l)).collect::<String>());
}

const LEDGER_FILES: &[(&str, &str)] = &[("app/core/kept.hy", KEPT_HY), ("app/core/gone.hy", "(defrecord Gone\n  (#^ int n))\n")];

#[test]
fn a_ledger_line_added_or_rewritten_passes_without_the_guard_and_is_red_with_it() {
    let open = turned_on(OPEN, LEDGER_FILES);
    ledger_way_around(open.path());
    assert_eq!(keys(open.path()), set(&[]), "without :guard the ledger lets the new and the reshaped type pass");

    let guarded = turned_on(GUARDED, LEDGER_FILES);
    assert_eq!(keys(guarded.path()), set(&[]), "the rule turned on is green");
    ledger_way_around(guarded.path());
    assert_eq!(
        keys(guarded.path()),
        set(&["design-existing.toml::DOEFF214::shapes:app/core/fresh.hy::Fresh", "design-existing.toml::DOEFF214::shapes:app/core/kept.hy::Kept"]),
        "the added and the rewritten line are red; the removed line is not"
    );
}

#[test]
fn a_field_added_to_an_existing_type_with_evidence_passes_without_the_guard_and_is_red_with_it() {
    let files: &[(&str, &str)] = &[("app/core/kept.hy", KEPT_HY), ("app/core/user.rs", USER_RS)];
    let grow = |root: &Path| {
        write(root, "app/core/kept.hy", KEPT_GROWN_HY);
        write(root, "app/core/kept.evidence.toml", KEPT_GROWN_EVIDENCE);
        write(root, "app/core/user.rs", USER_GROWN_RS);
        write(root, "app/core/user.evidence.toml", USER_GROWN_EVIDENCE);
    };
    let open = turned_on(OPEN, files);
    grow(open.path());
    assert_eq!(keys(open.path()), set(&[]), "without :guard a one-sentence evidence lets an existing type grow");

    let guarded = turned_on(GUARDED, files);
    grow(guarded.path());
    let grew = set(&["app/core/kept.hy::DOEFF212::Kept:grew", "app/core/user.rs::DOEFF212::User:grew"]);
    assert_eq!(keys(guarded.path()), grew, "with :guard the grown record and struct are red; the enum's new variant is not a grown field");

    // An approval written on the same branch does not count (and one without a request id of the declared spelling is red itself).
    let on_branch = format!("{}{}", approval("fields", "app/core/kept.hy::Kept", "reviewer", REQUEST), approval("fields", "app/core/user.rs::User", "email", "ok by me"));
    write(guarded.path(), "design/approvals.toml", &on_branch);
    let mut expected = grew.clone();
    expected.insert("design/approvals.toml::DOEFF214::approval:fields:app/core/user.rs::User:email".to_string());
    assert_eq!(keys(guarded.path()), expected, "an approval on the branch lets nothing pass");
    std::fs::remove_file(guarded.path().join("design/approvals.toml")).unwrap();

    // At the base: the approval with a request id lets its field pass; the one whose review is a remark (or a conversation id) does not.
    let at_base = format!("{}{}", approval("fields", "app/core/kept.hy::Kept", "reviewer", REQUEST), approval("fields", "app/core/user.rs::User", "email", "conversation 7"));
    land_on_main(guarded.path(), "design/approvals.toml", &at_base);
    assert_eq!(
        keys(guarded.path()),
        set(&["app/core/user.rs::DOEFF212::User:grew"]),
        "only the approval with a request id of the declared spelling, landed first, lets the field pass"
    );
}

//! Tests for DOEFF212 (a defrecord's shape changes only together with its design evidence in the sibling `<stem>.evidence.toml`).
//! Runs the binary and looks at the output of `--output-format editor-json`.
//!
//!   * A defrecord whose evidence records another shape, and a new defrecord with no evidence, are red; an existing defrecord whose fingerprint is in the :existing ledger is
//!     green, and the same defrecord turns red once its shape changes (the ledger is written by `--print-existing-shapes`).
//!   * Complete evidence of the current shape is green.
//!   * A one-file run (the editor's `--stdin --path`) gives the same hits for the .hy, and judging the evidence file from stdin reports at the .hy.
//!   * Files outside :files are not judged.

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF212";

const ARCHITECTURE: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy"] :except ["app/**/tests/**"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"})
"#;

const EXISTING_HY: &str = "(defrecord Kept\n  \"an existing type\"\n  (#^ str a))\n";
const DONE_HY: &str = "(defrecord Done\n  (#^ str card)\n  (#^ (| str None) merged-at))\n";
const DONE_EVIDENCE: &str = r#"schema = 1

[[element]]
name = "Done"
kind = "record"
concern = "one finished card"
key = ["card"]
fan_out = 2
review = "rv-7"

[[element.field]]
name = "card"
type = "str"
fact = "the card that finished"

[[element.field]]
name = "merged-at"
type = "(| str None)"
fact = "when the work reached main"
absent_when = "a card whose work is not merged (an investigation)"

[[element.rejected]]
shape = "add merged-at to the shared material type"
why = "the material type already has many fields with no owner"
"#;

/// A temporary repo (pyproject.toml, architecture.hy, and a list of files).
fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF212\"]\n").unwrap();
    std::fs::write(dir.path().join("architecture.hy"), ARCHITECTURE).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
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

fn editor(root: &Path, args: &[&str], stdin: Option<&str>) -> Value {
    let mut all = vec!["--output-format", "editor-json"];
    all.extend_from_slice(args);
    let (_, stdout, stderr) = linter(root, &all, stdin);
    serde_json::from_str(&stdout).unwrap_or_else(|error| panic!("not JSON ({}): {}\n{}", error, stdout, stderr))
}

/// (key, message) of this rule's violations.
fn hits(report: &Value) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = report["violations"]
        .as_array()
        .expect("violations list")
        .iter()
        .filter(|v| v["rule"] == RULE)
        .map(|v| (v["key"].as_str().unwrap_or("").to_string(), v["message"].as_str().unwrap_or("").to_string()))
        .collect();
    out.sort();
    out
}

fn keys(report: &Value) -> BTreeSet<String> {
    hits(report).into_iter().map(|(key, _)| key).collect()
}

fn set(keys: &[&str]) -> BTreeSet<String> {
    keys.iter().map(|k| k.to_string()).collect()
}

#[test]
fn new_and_reshaped_types_need_evidence_and_existing_unchanged_types_do_not() {
    let dir = repo(&[
        ("app/core/kept.hy", EXISTING_HY),
        ("app/core/done.hy", DONE_HY),
        ("app/core/done.evidence.toml", DONE_EVIDENCE),
        ("app/core/fresh.hy", "(defrecord Fresh (#^ int n))\n"),
        ("app/core/tests/sample.hy", "(defrecord Sample (#^ int n))\n"),
        ("other/outside.hy", "(defrecord Outside (#^ int n))\n"),
    ]);
    // The ledger written when the rule is turned on: every defrecord in the population at that time (Fresh is added after it).
    let (code, ledger, stderr) = linter(dir.path(), &["--print-existing-shapes"], None);
    assert_eq!(code, 0, "{}", stderr);
    assert!(ledger.contains("\"app/core/kept.hy::Kept\""), "{}", ledger);
    assert!(!ledger.contains("Sample") && !ledger.contains("Outside"), "only the population: {}", ledger);
    let ledger: String = ledger.lines().filter(|line| !line.contains("Fresh")).map(|line| format!("{}\n", line)).collect();
    std::fs::write(dir.path().join("design-existing.toml"), &ledger).unwrap();

    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&["app/core/fresh.hy::DOEFF212::Fresh"]), "{}", report);
    let (_, message) = &hits(&report)[0];
    assert!(message.contains("new type") && message.contains("app/core/fresh.evidence.toml"), "{}", message);

    // The existing type changes shape without evidence: red, naming the ledger's fingerprint.
    std::fs::write(dir.path().join("app/core/kept.hy"), EXISTING_HY.replace("(#^ str a))", "(#^ str a)\n  (#^ (| str None) b))")).unwrap();
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&["app/core/fresh.hy::DOEFF212::Fresh", "app/core/kept.hy::DOEFF212::Kept"]), "{}", report);
    assert!(hits(&report).iter().any(|(k, m)| k.ends_with("Kept") && m.contains("changed its shape")), "{}", report);
}

#[test]
fn evidence_must_match_the_shape_and_answer_every_question() {
    let dir = repo(&[("app/core/done.hy", DONE_HY), ("app/core/done.evidence.toml", DONE_EVIDENCE), ("design-existing.toml", "")]);
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&[]), "complete evidence of the current shape is green: {}", report);

    // A field is added to the type without touching the evidence.
    std::fs::write(dir.path().join("app/core/done.hy"), DONE_HY.replace("merged-at))", "merged-at)\n  (#^ (| str None) reviewer))")).unwrap();
    let report = editor(dir.path(), &[], None);
    let found = hits(&report);
    assert!(found.iter().any(|(_, m)| m.contains("fields not in the evidence: reviewer")), "{:?}", found);
    assert!(found.iter().any(|(_, m)| m.contains("absent_when") && m.contains("reviewer")), "{:?}", found);

    // The answers are dropped and the review id is not of the declared spelling.
    std::fs::write(dir.path().join("app/core/done.hy"), DONE_HY).unwrap();
    let thin = DONE_EVIDENCE.replace("concern = \"one finished card\"\n", "").replace("rv-7", "looks fine").replace("absent_when = \"a card whose work is not merged (an investigation)\"\n", "");
    std::fs::write(dir.path().join("app/core/done.evidence.toml"), thin).unwrap();
    let report = editor(dir.path(), &[], None);
    let found = hits(&report);
    assert_eq!(found.len(), 1, "{:?}", found);
    for word in ["concern", "absent_when", "review looks fine"] {
        assert!(found[0].1.contains(word), "no gap about {}: {}", word, found[0].1);
    }
}

#[test]
fn one_file_runs_judge_the_module_and_its_evidence() {
    let dir = repo(&[("app/core/done.hy", DONE_HY), ("app/core/done.evidence.toml", DONE_EVIDENCE), ("design-existing.toml", "")]);
    let reshaped = DONE_HY.replace("(#^ str card)", "(#^ int card)");
    let report = editor(dir.path(), &["--stdin", "--path", "app/core/done.hy"], Some(&reshaped));
    assert_eq!(keys(&report), set(&["app/core/done.hy::DOEFF212::Done:shape"]), "{}", report);
    assert!(report["judged_rules"].as_array().unwrap().iter().any(|r| r == RULE), "{}", report);

    // The evidence file edited in the editor: judged against the module on disk, reported at the module.
    let wrong = DONE_EVIDENCE.replace("name = \"Done\"", "name = \"Gone\"");
    let report = editor(dir.path(), &["--stdin", "--path", "app/core/done.evidence.toml"], Some(&wrong));
    assert_eq!(keys(&report), set(&["app/core/done.hy::DOEFF212::Done", "app/core/done.hy::DOEFF212::Gone:orphan"]), "{}", report);
}

const RUST_ARCH: &str = r#"
(defarchitecture rust-sample
  :root "src"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["src/**/*.rs"] :except ["src/**/tests/**"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"})
"#;

const RUST_EXISTING: &str = r#"
pub struct User {
    pub id: u64,
    pub name: String,
}

pub enum Role {
    Admin,
    Member(String),
}
"#;

const RUST_USER_EVIDENCE: &str = r#"schema = 1

[[element]]
name = "User"
kind = "record"
concern = "an authenticated user"
key = ["id"]
fan_out = 1
review = "rv-1"

[[element.field]]
name = "id"
type = "u64"
fact = "unique identifier"

[[element.field]]
name = "name"
type = "String"
fact = "user display name"

[[element.rejected]]
shape = "tuple struct User(u64, String)"
why = "named fields are clearer"

[[element]]
name = "Role"
kind = "record"
concern = "user access role"
key = []
fan_out = 1
review = "rv-2"

[[element.field]]
name = "Admin"
fact = "administrative role"

[[element.field]]
name = "Member"
type = "(String)"
fact = "regular member with team id"

[[element.rejected]]
shape = "boolean is_admin field"
why = "need to support team id for members"
"#;

#[test]
fn rust_struct_and_enum_evidence_and_ledger() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF212\"]\n").unwrap();
    std::fs::write(dir.path().join("architecture.hy"), RUST_ARCH).unwrap();
    let src_dir = dir.path().join("src/core");
    std::fs::create_dir_all(&src_dir).unwrap();
    std::fs::write(src_dir.join("user.rs"), RUST_EXISTING).unwrap();

    // 1. Generate ledger with --print-existing-shapes
    let (code, ledger, stderr) = linter(dir.path(), &["--print-existing-shapes"], None);
    assert_eq!(code, 0, "{}", stderr);
    assert!(ledger.contains("\"src/core/user.rs::User\""), "{}", ledger);
    assert!(ledger.contains("\"src/core/user.rs::Role\""), "{}", ledger);
    std::fs::write(dir.path().join("design-existing.toml"), &ledger).unwrap();

    // Existing types in ledger are green
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&[]), "existing types in ledger are green: {}", report);

    // 2. Add new struct without evidence -> error
    std::fs::write(src_dir.join("item.rs"), "pub struct Item {\n    pub count: u32,\n}\n").unwrap();
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&["src/core/item.rs::DOEFF212::Item"]), "new type error: {}", report);

    // 3. Add evidence matching Item -> green
    let item_evidence = r#"schema = 1

[[element]]
name = "Item"
kind = "record"
concern = "inventory item"
key = ["count"]
fan_out = 1
review = "rv-3"

[[element.field]]
name = "count"
type = "u32"
fact = "count of items"

[[element.rejected]]
shape = "u32 directly"
why = "typed container needed"
"#;
    std::fs::write(src_dir.join("item.evidence.toml"), item_evidence).unwrap();
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&[]), "complete evidence is green: {}", report);

    // 4. Change shape of User (existing type in ledger) without evidence -> error
    let modified_user = RUST_EXISTING.replace("pub name: String,", "pub name: String,\n    pub email: Option<String>,");
    std::fs::write(src_dir.join("user.rs"), &modified_user).unwrap();
    let report = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), set(&["src/core/user.rs::DOEFF212::User"]), "reshaped existing type without evidence: {}", report);

    // 5. Evidence with mismatching shape -> error
    std::fs::write(src_dir.join("user.evidence.toml"), RUST_USER_EVIDENCE).unwrap();
    // User in user.rs has email field not in evidence
    let report = editor(dir.path(), &[], None);
    let found = hits(&report);
    assert!(found.iter().any(|(k, m)| k.contains("User:shape") && m.contains("email")), "shape difference detected: {:?}", found);
}

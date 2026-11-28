//! The gate on a git repo whose base (branch main) declares :design-evidence with :guard and holds the ledger of its types; each test changes the work tree and runs
//! `doeff-design-gate --base main --paths <changed>` (exit 0 = pass, 1 = refuse, 2 = cannot judge).
//!   * A new type without evidence is refused; a new type with complete evidence passes.
//!   * A type the base already had in the same shape passes even when the ledger lacks it (the base's debt is not the change's); reshaping it is refused.
//!   * A line added to the ledger is refused; a line removed passes.
//!   * Removing or narrowing the declaration is refused unless an approval at the base covers it.
//!   * A seventh field its module does not use is refused by DOEFF215 and DOEFF216; a field added to a module of types only is not.
//!   * Fails closed: no declaration at the base, an unreadable ledger at the base, and a base that is not a commit cannot be judged (2).

use std::path::Path;
use std::process::Command;

use doeff_linter::project::design_evidence::{shapes_of, ExistingShapes};

const DECLARATION: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"
                    :guard {:base "main" :approvals "design/approvals.toml" :approval-pattern r"^req-[0-9]+$"}})
"#;

const NARROWED: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/core/*.hy"] :existing "design-existing.toml" :review-pattern r"^rv-[0-9]+$"
                    :guard {:base "main" :approvals "design/approvals.toml" :approval-pattern r"^req-[0-9]+$"}})
"#;

const UNDECLARED: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])])
"#;

const KEPT: &str = "(defrecord Kept\n  (#^ str card))\n";
const DEBT: &str = "(defrecord Debt\n  (#^ str card))\n";
const NOTE: &str = "\n(defrecord Note\n  (#^ str card))\n";
const NOTE_EVIDENCE: &str = r#"schema = 1

[[element]]
name = "Note"
kind = "record"
concern = "one note on a card"
key = ["card"]
fan_out = 1
review = "rv-1"

[[element.field]]
name = "card"
type = "str"
fact = "the card the note is on"

[[element.rejected]]
shape = "a field of Kept"
why = "a card has many notes"
"#;

fn git(dir: &Path, args: &[&str]) {
    let output = Command::new("git").args(args).current_dir(dir).output().unwrap();
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

/// A repo whose main holds the declaration, app/core/kept.hy (in the ledger) and app/core/debt.hy (not in the ledger — the base's debt), and whose work tree is a
/// branch off main.
fn base(declaration: &str, approvals: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    write(root, "pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF212\", \"DOEFF214\"]\n");
    write(root, "architecture.hy", declaration);
    write(root, "app/core/kept.hy", KEPT);
    write(root, "app/core/debt.hy", DEBT);
    write(root, "design-existing.toml", &ledger(&[("app/core/kept.hy", KEPT)]));
    write(root, "design/approvals.toml", approvals);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    git(root, &["checkout", "-q", "-b", "work"]);
    dir
}

fn gate(root: &Path, base: &str, paths: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-design-gate")).args(["--base", base, "--paths"]).args(paths).current_dir(root).output().unwrap();
    (output.status.code().unwrap_or(-1), format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)))
}

#[test]
fn a_new_type_is_refused_without_evidence_and_passes_with_it() {
    let dir = base(DECLARATION, "");
    let root = dir.path();
    write(root, "app/core/kept.hy", &format!("{}{}", KEPT, NOTE));
    let (code, out) = gate(root, "main", &["app/core/kept.hy"]);
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF212") && out.contains("Note"), "{}", out);
    write(root, "app/core/kept.evidence.toml", NOTE_EVIDENCE);
    let (code, out) = gate(root, "main", &["app/core/kept.hy", "app/core/kept.evidence.toml"]);
    assert_eq!(code, 0, "{}", out);
}

#[test]
fn a_type_the_base_had_is_not_the_change_and_reshaping_it_is() {
    let dir = base(DECLARATION, "");
    let root = dir.path();
    write(root, "app/core/debt.hy", &format!(";; a comment\n{}", DEBT));
    let (code, out) = gate(root, "main", &["app/core/debt.hy"]);
    assert_eq!(code, 0, "the unchanged Debt is the base's debt: {}", out);
    write(root, "app/core/debt.hy", "(defrecord Debt\n  (#^ str card)\n  (#^ str owner))\n");
    let (code, out) = gate(root, "main", &["app/core/debt.hy"]);
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("Debt"), "{}", out);
}

#[test]
fn the_ledger_only_shrinks() {
    let dir = base(DECLARATION, "");
    let root = dir.path();
    write(root, "design-existing.toml", &ledger(&[("app/core/kept.hy", KEPT), ("app/core/debt.hy", DEBT)]));
    let (code, out) = gate(root, "main", &["design-existing.toml"]);
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF214"), "{}", out);
    write(root, "design-existing.toml", "[shapes]\n");
    let (code, out) = gate(root, "main", &["design-existing.toml"]);
    assert_eq!(code, 0, "a removed line passes: {}", out);
}

#[test]
fn the_declaration_cannot_be_removed_or_narrowed_without_an_approval_at_the_base() {
    let dir = base(DECLARATION, "");
    let root = dir.path();
    write(root, "architecture.hy", UNDECLARED);
    let (code, out) = gate(root, "main", &["architecture.hy"]);
    assert_eq!(code, 1, "{}", out);
    write(root, "architecture.hy", NARROWED);
    let (code, out) = gate(root, "main", &["app/core/kept.hy"]);
    assert_eq!(code, 1, "a narrowed declaration is refused whatever the paths: {}", out);
    let approved = "[[approval]]\nledger = \"declaration\"\nkey = \":design-evidence\"\nvalue = \"absent\"\nreview = \"req-7\"\nwhy = \"the user retired the rule\"\n";
    let dir = base(DECLARATION, approved);
    let root = dir.path();
    write(root, "architecture.hy", UNDECLARED);
    let (code, out) = gate(root, "main", &["architecture.hy"]);
    assert_eq!(code, 0, "an approval at the base lets the removal pass: {}", out);
}

#[test]
fn what_cannot_be_read_holds_the_change() {
    let dir = base(UNDECLARED, "");
    let (code, out) = gate(dir.path(), "main", &["app/core/kept.hy"]);
    assert_eq!(code, 2, "no declaration at the base: {}", out);
    let dir = base(DECLARATION, "");
    let root = dir.path();
    git(root, &["checkout", "-q", "main"]);
    write(root, "design-existing.toml", "this is not toml [");
    git(root, &["commit", "-q", "-am", "break the ledger"]);
    let (code, out) = gate(root, "main", &["app/core/kept.hy"]);
    assert_eq!(code, 2, "an unreadable ledger at the base: {}", out);
    let (code, out) = gate(root, "no-such-ref", &["app/core/kept.hy"]);
    assert_eq!(code, 2, "a base that is not a commit: {}", out);
}

/// Commits files on main and starts the work branch again from it.
fn on_main(root: &Path, files: &[(&str, &str)]) {
    git(root, &["checkout", "-q", "main"]);
    for (rel, text) in files {
        write(root, rel, text);
    }
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "more base"]);
    git(root, &["checkout", "-q", "-B", "work"]);
}

const LENDING: &str = "(defrecord LendingState\n  (#^ tuple returning)\n  (#^ tuple unreturned)\n  (#^ tuple extending)\n  (#^ tuple touching)\n  (#^ int last-seq)\n  (#^ tuple leases))\n\n\
                       (deff next-seq [state]\n  (LendingState :returning state.returning :unreturned state.unreturned :extending state.extending\n\
                       :touching state.touching :last-seq (+ 1 state.last-seq) :leases state.leases))\n";

#[test]
fn a_seventh_field_its_module_does_not_use_is_refused_by_doeff215_and_doeff216() {
    let dir = base(DECLARATION, "");
    let root = dir.path();
    on_main(root, &[("app/core/lending.hy", LENDING), ("design-existing.toml", &ledger(&[("app/core/kept.hy", KEPT), ("app/core/lending.hy", LENDING)]))]);
    let grown = LENDING.replace("(#^ tuple leases))", "(#^ tuple leases)\n  (setv #^ frozenset paused-credentials (frozenset)))");
    write(root, "app/core/lending.hy", &grown);
    let started = std::time::Instant::now();
    let (code, out) = gate(root, "main", &["app/core/lending.hy"]);
    let spent = started.elapsed();
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF215 LendingState:width") && out.contains("7 fields"), "{}", out);
    assert!(out.contains("DOEFF216 LendingState:unused-in-module") && out.contains("paused-credentials"), "{}", out);
    assert!(spent < std::time::Duration::from_secs(1), "the gate took {:?}", spent);
}

#[test]
fn a_types_only_module_gaining_a_field_has_no_doeff215_or_doeff216_hit() {
    let intent = "(defrecord SrpTarget\n  (#^ str card)\n  (#^ str rel))\n";
    let approvals = "[[approval]]\nledger = \"fields\"\nkey = \"app/intent/types.hy::SrpTarget\"\nvalue = \"base\"\nreview = \"req-3\"\nwhy = \"the target names its base\"\n";
    let dir = base(DECLARATION, approvals);
    let root = dir.path();
    on_main(root, &[("app/intent/types.hy", intent), ("design-existing.toml", &ledger(&[("app/core/kept.hy", KEPT), ("app/intent/types.hy", intent)]))]);
    write(root, "app/intent/types.hy", &intent.replace("(#^ str rel))", "(#^ str rel)\n  (#^ str base))"));
    let (code, out) = gate(root, "main", &["app/intent/types.hy"]);
    assert!(!out.contains("DOEFF215") && !out.contains("DOEFF216"), "a module with no function bodies has no false hit: {}", out);
    assert!(code == 0 || out.contains("DOEFF212"), "only the evidence of the reshape is asked: {}", out);
}

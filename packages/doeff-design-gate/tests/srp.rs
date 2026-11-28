//! DOEFF217 and the JSON facts on a git repo whose base (branch main) declares :design-evidence over app/**/*.hy and app/**/*.rs; each test changes the work tree and
//! runs `doeff-design-gate --base main [--format json --classify-trigger] --paths <changed>`.
//!   * A function that splits a type's functions into two unrelated groups (LCOM4 1 → 2) is refused, in Hy and in Rust; `--disable DOEFF217` keeps it to the facts.
//!   * A function that joins the existing group (LCOM4 stays 1) passes, and the JSON says no review is required.
//!   * An existing type that gains a field its module never touches needs the review (existing-type-extension, DOEFF216's fact).
//!   * A design document that names modules of two directories needs the review.
//!   * 265 changed paths are judged with the JSON classification in under a second.

use std::path::Path;
use std::process::Command;
use std::time::Instant;

use serde_json::Value;

const DECLARATION: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
  :design-evidence {:files ["app/**/*.hy" "app/**/*.rs"] :review-pattern r"^rv-[0-9]+$"})
"#;

/// A record whose `note` no function touches yet (one group: the functions of holder and count).
const HY_BASE: &str = r#"
(defrecord Lending
  (#^ str holder)
  (#^ int count)
  (#^ str note))

(defn lending-move [#^ Lending state]
  (Lending :holder state.holder :count (+ state.count 1) :note "")) ; the literal names note as well
"#;

const HY_BASE_TWO: &str = r#"
(defrecord Lending
  (#^ str holder)
  (#^ int count)
  (#^ str note))

(defn lending-move [#^ Lending state]
  (+ state.count (len state.holder)))
"#;

const HY_SPLIT: &str = r#"
(defn lending-note [#^ Lending state]
  state.note)
"#;

const HY_JOINED: &str = r#"
(defn lending-holder [#^ Lending state]
  (if (> state.count 0) state.holder ""))
"#;

const RS_BASE: &str = "pub struct Lending { holder: String, count: u32, note: String }\n\nimpl Lending {\n    fn bump(&mut self) { self.count += self.holder.len() as u32; }\n}\n";
const RS_SPLIT: &str = "\nfn note_of(l: &Lending) -> &str { &l.note }\n";
const RS_JOINED: &str = "\nfn holder_of(l: &Lending) -> &str { if l.count > 0 { &l.holder } else { \"\" } }\n";

fn git(dir: &Path, args: &[&str]) {
    let output = Command::new("git").args(args).current_dir(dir).output().unwrap();
    assert!(output.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&output.stderr));
}

fn write(dir: &Path, rel: &str, text: &str) {
    let path = dir.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

fn base(files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    write(root, "pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF212\"]\n");
    write(root, "architecture.hy", DECLARATION);
    for (rel, text) in files {
        write(root, rel, text);
    }
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    git(root, &["checkout", "-q", "-b", "work"]);
    dir
}

fn gate(root: &Path, extra: &[&str], paths: &[&str]) -> (i32, String) {
    let output =
        Command::new(env!("CARGO_BIN_EXE_doeff-design-gate")).args(["--base", "main"]).args(extra).arg("--paths").args(paths).current_dir(root).output().unwrap();
    (output.status.code().unwrap_or(-1), format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)))
}

fn json(root: &Path, paths: &[&str]) -> (i32, Value) {
    let (code, out) = gate(root, &["--format", "json", "--classify-trigger"], paths);
    (code, serde_json::from_str(out.trim()).unwrap_or_else(|e| panic!("not JSON ({}): {}", e, out)))
}

fn conditions(outcome: &Value) -> Vec<String> {
    outcome["triggers"].as_array().unwrap().iter().map(|t| format!("{} {}", t["condition"].as_str().unwrap(), t["detail"].as_str().unwrap())).collect()
}

#[test]
fn hy_lcom4_from_1_to_2_is_refused_and_the_facts_say_why() {
    let dir = base(&[("app/core/lending.hy", HY_BASE_TWO)]);
    let root = dir.path();
    write(root, "app/core/lending.hy", &format!("{}{}", HY_BASE_TWO, HY_SPLIT));
    let (code, out) = gate(root, &[], &["app/core/lending.hy"]);
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF217") && out.contains("lcom4:Lending") && out.contains("[lending_note]"), "{}", out);
    let (code, outcome) = json(root, &["app/core/lending.hy"]);
    assert_eq!(code, 1, "{}", outcome);
    assert_eq!(outcome["verdict"], "reject");
    assert_eq!(outcome["srp_review_required"], true);
    let fact = &outcome["facts"][0];
    assert_eq!((fact["type_name"].as_str(), fact["lcom4_before"].as_u64(), fact["lcom4_after"].as_u64()), (Some("Lending"), Some(1), Some(2)), "{}", outcome);
    assert!(conditions(&outcome).iter().any(|c| c.contains("DOEFF217: LCOM4 1 -> 2")), "{}", outcome);
    let (code, out) = gate(root, &["--disable", "DOEFF217"], &["app/core/lending.hy"]);
    assert_eq!(code, 0, "disabled, LCOM4 is only a fact: {}", out);
}

#[test]
fn rust_lcom4_from_1_to_2_is_refused() {
    let dir = base(&[("app/core/lending.rs", RS_BASE)]);
    let root = dir.path();
    write(root, "app/core/lending.rs", &format!("{}{}", RS_BASE, RS_SPLIT));
    let (code, out) = gate(root, &[], &["app/core/lending.rs"]);
    assert_eq!(code, 1, "{}", out);
    assert!(out.contains("DOEFF217") && out.contains("[note_of]"), "{}", out);
    let (_, outcome) = json(root, &["app/core/lending.rs"]);
    assert_eq!((outcome["facts"][0]["lcom4_before"].as_u64(), outcome["facts"][0]["lcom4_after"].as_u64()), (Some(1), Some(2)), "{}", outcome);
}

#[test]
fn a_function_that_joins_the_group_passes_and_needs_no_review() {
    for (rel, was, joined) in [("app/core/lending.hy", HY_BASE, HY_JOINED), ("app/core/lending.rs", RS_BASE, RS_JOINED)] {
        let dir = base(&[(rel, was)]);
        let root = dir.path();
        write(root, rel, &format!("{}{}", was, joined));
        let (code, outcome) = json(root, &[rel]);
        assert_eq!(code, 0, "{}", outcome);
        assert_eq!(outcome["verdict"], "pass");
        assert_eq!(outcome["srp_review_required"], false, "{}", outcome);
        assert!(outcome["facts"].as_array().unwrap().is_empty(), "same shape, same LCOM4: {}", outcome);
    }
}

#[test]
fn an_existing_type_that_gains_a_field_its_module_never_touches_needs_the_review() {
    let dir = base(&[("app/core/lending.rs", RS_BASE)]);
    let root = dir.path();
    write(root, "app/core/lending.rs", &RS_BASE.replace("note: String }", "note: String, paused: bool }"));
    let (_, outcome) = json(root, &["app/core/lending.rs"]);
    assert_eq!(outcome["srp_review_required"], true, "{}", outcome);
    let fact = &outcome["facts"][0];
    assert_eq!((fact["width_before"].as_u64(), fact["width_after"].as_u64()), (Some(3), Some(4)), "{}", outcome);
    assert_eq!(fact["added_fields"], serde_json::json!(["paused"]));
    assert_eq!(fact["module_unused_fields"], serde_json::json!(["paused"]));
    let reasons = conditions(&outcome);
    assert!(reasons.iter().any(|c| c.starts_with("existing-type-extension")) && reasons.iter().any(|c| c.contains("DOEFF216")), "{:?}", reasons);
}

#[test]
fn a_design_document_across_two_module_directories_needs_the_review() {
    let dir = base(&[("app/core/lending.rs", RS_BASE)]);
    let root = dir.path();
    let doc = "docs/design/lending/artifacts/v1/design.md";
    write(root, doc, "# Lending\nTouches `controllers/agent_task/core/cli_lending.hy` and `controllers/messaging/core/question_flow.hy`.\n");
    let (code, outcome) = json(root, &[doc]);
    assert_eq!(code, 0, "{}", outcome);
    assert_eq!(outcome["srp_review_required"], true, "{}", outcome);
    assert_eq!(outcome["facts"][0]["kind"], "design-doc");
    assert_eq!(outcome["facts"][0]["cross_module_touch_count"], 2);
    write(root, doc, "# Lending\nOnly `controllers/agent_task/core/cli_lending.hy`.\n");
    let (_, outcome) = json(root, &[doc]);
    assert_eq!(outcome["srp_review_required"], false, "{}", outcome);
}

#[test]
fn the_json_classification_of_265_paths_takes_under_a_second() {
    let mut files: Vec<(String, String)> = Vec::new();
    for i in 0..265 {
        if i % 2 == 0 {
            files.push((format!("app/m{}/lending_{}.hy", i % 13, i), HY_BASE.replace("Lending", &format!("Lending{}", i))));
        } else {
            files.push((format!("app/m{}/lending_{}.rs", i % 13, i), RS_BASE.replace("Lending", &format!("Lending{}", i))));
        }
    }
    let refs: Vec<(&str, &str)> = files.iter().map(|(r, t)| (r.as_str(), t.as_str())).collect();
    let dir = base(&refs);
    let root = dir.path();
    for (i, (rel, text)) in files.iter().enumerate() {
        let joined = if rel.ends_with(".hy") { HY_JOINED } else { RS_JOINED };
        write(root, rel, &format!("{}{}", text, joined.replace("Lending", &format!("Lending{}", i))));
    }
    let paths: Vec<&str> = files.iter().map(|(r, _)| r.as_str()).collect();
    let started = Instant::now();
    let (code, outcome) = json(root, &paths);
    let took = started.elapsed();
    eprintln!("doeff-design-gate --format json --classify-trigger over {} paths: {:?}", paths.len(), took);
    assert_eq!(code, 0, "{}", outcome);
    assert_eq!(outcome["srp_review_required"], false, "{}", outcome);
    assert!(took.as_secs_f64() < 1.0, "{} paths took {:?}", paths.len(), took);
}

//! A declaration split over files must keep the same checks and source locations.
use doeff_linter::project::architecture::Architecture;
use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

const ARCH: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])]
"#;
const SERVICE: &str = r#"(defservice queue "Queue" {:layers [core] :entry-modules ["app.queue.core.main"] :invariants ["app.queue.core.main:missing"]})"#;
const LISTS: &str = r#"
  :retired-words [(retired-words "old" :words ["obsolete"] :files ["app/**/*.hy"] :in lines :instead "current")]
  :confined-spellings [(confined-spelling "forbidden" :patterns ["forbidden"] :files ["app/**/*.hy"] :why "keep out")]
  :counted-spellings [(counted-spelling "marker" :pattern "marker" :files ["app/**/*.hy"] :count 2 :why "two markers")]
  :semantic-lines [(line "pure" :rules [DOEFF201] :text "Keep judgment pure." :fires ["(defn bad [] (print 1))"] :silent ["(defn good [] 1)"])]
"#;

fn write(root: &Path, rel: &str, source: &str) {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn config(root: &Path, paths: &str) {
    write(root, "pyproject.toml", &format!("[tool.doeff-linter]\narchitecture = {paths}\nenable = [\"DOEFF016\", \"DOEFF100\", \"DOEFF150\", \"DOEFF148\", \"DOEFF161\", \"DOEFF163\"]\n[tool.doeff-linter.commit_hook]\ntimeout_s = 120\n"));
}

fn repo(split: bool) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    if split {
        // A list fragment before defarchitecture proves that input order is not significant.
        config(root, r#"["declarations/lists.hy", "architecture.hy", "declarations/services/*.hy"]"#);
        write(root, "architecture.hy", &format!("{ARCH})\n"));
        write(root, "declarations/lists.hy", &format!("(defarchitecture-lists sample{LISTS})\n"));
        write(root, "declarations/services/queue.hy", &format!("; service location\n\n{SERVICE}\n"));
    } else {
        config(root, r#""architecture.hy""#);
        write(root, "architecture.hy", &format!("{ARCH}{LISTS})\n{SERVICE}\n"));
    }
    write(root, "app/queue/core/main.hy", "(defn work [] \"obsolete forbidden marker\")\n");
    dir
}

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(args).current_dir(root)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git/linter-cache"))
        .env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("GIT_DIR").env_remove("GIT_INDEX_FILE").env_remove("GIT_WORK_TREE")
        .output().unwrap()
}

fn editor(root: &Path) -> Value {
    let output = run(root, &["--output-format", "editor-json", "--no-log"]);
    assert_ne!(output.status.code(), Some(2), "{}", String::from_utf8_lossy(&output.stderr));
    serde_json::from_slice(&output.stdout).unwrap_or_else(|e| panic!("{e}: {}", String::from_utf8_lossy(&output.stderr)))
}

fn error(root: &Path, fragment: &str) -> String {
    let output = run(root, &["--output-format", "editor-json", "--no-log"]);
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    assert_eq!(output.status.code(), Some(2), "{stderr}");
    assert!(stderr.contains(fragment), "expected {fragment}: {stderr}");
    assert!(output.stdout.is_empty(), "configuration errors must not report a clean result");
    stderr
}

#[test]
fn split_and_single_declarations_produce_the_same_findings() {
    let single = repo(false);
    let split = repo(true);
    let one = editor(single.path());
    let many = editor(split.path());
    let summarize = |report: &Value| {
        let mut found: Vec<_> = report["violations"].as_array().unwrap().iter()
            .map(|v| (v["rule"].to_string(), v["message"].to_string())).collect();
        found.sort();
        found
    };
    assert_eq!(summarize(&one), summarize(&many));
    for rule in ["DOEFF150", "DOEFF148", "DOEFF161", "DOEFF163"] {
        assert!(many["violations"].as_array().unwrap().iter().any(|v| v["rule"] == rule), "missing {rule}: {many}");
    }
    let hit = many["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF163").unwrap();
    assert!(hit["path"].as_str().unwrap().ends_with("declarations/services/queue.hy"), "{hit}");
    assert_eq!(hit["range"]["start"]["line"], 2, "{hit}");
}

#[test]
fn all_four_list_values_are_preserved_and_semantic_questions_keep_their_lines() {
    let dir = repo(true);
    let root = dir.path();
    let whole = format!("{ARCH}{LISTS})\n{SERVICE}");
    let single = Architecture::parse_at(&whole, &root.join("architecture.hy"), root).unwrap();
    let split = Architecture::load_all(&[
        root.join("declarations/lists.hy"), root.join("architecture.hy"), root.join("declarations/services/queue.hy")
    ], root).unwrap();
    // Source positions are deliberately skipped by serialization; rule inputs must be identical.
    assert_eq!(serde_json::to_value(&single).unwrap(), serde_json::to_value(&split).unwrap());
    assert_eq!(split.semantic_lines.len(), 1);
    assert_eq!(single.semantic_lines, split.semantic_lines);
}

#[test]
fn errors_name_the_service_file_and_line() {
    let dir = repo(true);
    write(dir.path(), "declarations/services/queue.hy", "\n\n(defservice queue \"Queue\" {:layers [absent]})\n");
    let stderr = error(dir.path(), "absent in :layers of service queue");
    assert!(stderr.contains("declarations/services/queue.hy:3:"), "{stderr}");
    write(dir.path(), "declarations/services/queue.hy", "\n(defservice queue \"Queue\" {:entry-modules []})\n");
    let stderr = error(dir.path(), ":entry-modules is empty");
    assert!(stderr.contains("declarations/services/queue.hy:2:"), "{stderr}");
}

#[test]
fn missing_spelling_targets_report_the_list_file() {
    let dir = repo(true);
    write(dir.path(), "declarations/lists.hy", &format!("(defarchitecture-lists sample{})", LISTS.replace("app/**/*.hy", "absent/**/*.hy")));
    let report = editor(dir.path());
    for rule in ["DOEFF148", "DOEFF161"] {
        let hit = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule).unwrap();
        assert!(hit["path"].as_str().unwrap().ends_with("declarations/lists.hy"), "{hit}");
        assert!(hit["range"]["start"]["line"].as_u64().unwrap() > 0, "{hit}");
    }
}

#[test]
fn unknown_keys_report_the_fragment_location() {
    let dir = repo(true);
    write(dir.path(), "declarations/services/queue.hy", "\n(defservice queue \"Queue\" {:layers [core] :future-setting True})\n");
    let report = editor(dir.path());
    let hit = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF100").unwrap();
    assert!(hit["path"].as_str().unwrap().ends_with("declarations/services/queue.hy"), "{hit}");
    assert_eq!(hit["range"]["start"]["line"], 1, "{hit}");
}

#[test]
fn duplicate_architectures_services_and_lists_are_errors() {
    let dir = repo(true);
    let root = dir.path();
    write(root, "declarations/services/second.hy", &format!("{ARCH})"));
    let stderr = error(root, "two defarchitecture forms");
    assert!(stderr.contains("second.hy:1:") && stderr.contains("architecture.hy:1:"), "{stderr}");
    write(root, "declarations/services/second.hy", SERVICE);
    let stderr = error(root, "service queue is declared twice");
    assert!(stderr.contains("queue.hy:3:") && stderr.contains("second.hy:1:"), "{stderr}");
    for key in [":retired-words", ":confined-spellings", ":semantic-lines", ":counted-spellings"] {
        write(root, "declarations/services/second.hy", &format!("(defarchitecture-lists sample {key} [])"));
        let stderr = error(root, &format!("{key} is written in two places"));
        assert!(stderr.contains("second.hy:1:") && stderr.contains("lists.hy:"), "{stderr}");
        write(root, "declarations/services/second.hy", "");
        write(root, "architecture.hy", &format!("{ARCH} {key} [])"));
        error(root, &format!("{key} is written in two places"));
        // A repeated key inside defarchitecture must not silently replace its first value either.
        write(root, "declarations/lists.hy", "");
        write(root, "architecture.hy", &format!("{ARCH} {key} [] {key} [])"));
        error(root, &format!("{key} is written in two places"));
        write(root, "architecture.hy", &format!("{ARCH})"));
        write(root, "declarations/lists.hy", &format!("(defarchitecture-lists sample{LISTS})"));
    }
}

#[test]
fn absent_architecture_wrong_owner_and_illegal_fragment_keys_are_errors() {
    let dir = repo(true);
    write(dir.path(), "architecture.hy", "");
    error(dir.path(), "there is no defarchitecture");
    write(dir.path(), "architecture.hy", &format!("{ARCH})"));
    write(dir.path(), "declarations/lists.hy", "(defarchitecture-lists wrong :retired-words [])");
    error(dir.path(), "defarchitecture-lists wrong");
    write(dir.path(), "declarations/lists.hy", "(defarchitecture-lists sample :root \"other\")");
    error(dir.path(), ":root cannot be written in defarchitecture-lists");
}

#[test]
fn empty_paths_unmatched_globs_and_missing_explicit_files_are_errors() {
    let dir = repo(true);
    for paths in ["[]", r#"["architecture.hy", "missing/*.hy"]"#, r#"["architecture.hy", "missing.hy"]"#] {
        config(dir.path(), paths);
        error(dir.path(), "architecture");
    }
}

#[test]
fn overlapping_paths_read_each_file_once() {
    let dir = repo(true);
    config(dir.path(), r#"["architecture.hy", "./architecture.hy", "declarations/**/*.hy", "declarations/services/queue.hy"]"#);
    editor(dir.path());
}

#[test]
fn declaration_files_outside_root_are_still_in_scope() {
    let dir = repo(true);
    let root = dir.path();
    let text = std::fs::read_to_string(root.join("pyproject.toml")).unwrap();
    write(root, "pyproject.toml", &text.replace("[tool.doeff-linter]\n", "[tool.doeff-linter]\nroot = \"app\"\n"));
    let output = run(root, &["--output-format", "editor-json", "--no-log", "declarations/services/queue.hy", "declarations/lists.hy"]);
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["out_of_scope"], serde_json::json!([]), "{report}");
}

#[test]
fn split_rules_promotes_changes_additions_and_deletions_of_any_fragment() {
    let dir = repo(true);
    for file in ["architecture.hy", "declarations/lists.hy", "declarations/services/queue.hy", "declarations/services/new.hy", "declarations/services/deleted.hy"] {
        let output = run(dir.path(), &["--split-rules", "--root", dir.path().to_str().unwrap(), file]);
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        let report: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(report["quick"], serde_json::json!([]), "{file}: {report}");
        assert!(report["whole"].as_array().unwrap().contains(&serde_json::json!("DOEFF016")), "{report}");
    }
    let output = run(dir.path(), &["--split-rules", "--root", dir.path().to_str().unwrap(), "app/queue/core/main.hy"]);
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(report["quick"].as_array().unwrap().contains(&serde_json::json!("DOEFF016")), "{report}");
}

fn git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .args(["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"])
        .args(args).current_dir(root)
        .env_remove("GIT_DIR").env_remove("GIT_INDEX_FILE").env_remove("GIT_WORK_TREE")
        .output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
}

#[test]
fn hook_checks_new_findings_caused_by_a_fragment_change() {
    let dir = repo(true);
    let root = dir.path();
    write(root, "app/queue/core/main.hy", "(defn work [] \"forbidden marker marker\")\n");
    write(root, "declarations/services/queue.hy", "(defservice queue \"Queue\" {:layers [core]})\n");
    write(root, "declarations/lists.hy", "(defarchitecture-lists sample :retired-words [])\n");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-qm", "baseline"]);
    let clean = run(root, &["--commit-hook"]);
    assert!(clean.status.success(), "{}", String::from_utf8_lossy(&clean.stderr));
    write(root, "declarations/lists.hy", &format!("(defarchitecture-lists sample{LISTS})\n"));
    git(root, &["add", "declarations/lists.hy"]);
    let output = run(root, &["--commit-hook"]);
    assert_eq!(output.status.code(), Some(1), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(String::from_utf8_lossy(&output.stderr).contains("DOEFF148"), "{}", String::from_utf8_lossy(&output.stderr));
    write(root, "MSG", "Enable the vocabulary checks\n\nLint-Baseline: declarations — migrate existing spellings separately\n");
    let output = run(root, &["--commit-hook", "--commit-message", "MSG"]);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let split = run(root, &["--split-rules", "--commit-message", "MSG", "declarations/lists.hy"]);
    let report: Value = serde_json::from_slice(&split.stdout).unwrap();
    let overlay = report["baseline_overlay"].as_array().unwrap();
    for file in ["architecture.hy", "declarations/lists.hy", "declarations/services/queue.hy"] {
        assert!(overlay.contains(&serde_json::json!(file)), "{report}");
    }
}

#[test]
fn doeff_single_file_declaration_still_loads() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let path = root.join("tools/architecture.hy");
    let single = Architecture::load(&path, &root).unwrap();
    let multiple = Architecture::load_all(&[path], &root).unwrap();
    assert_eq!(serde_json::to_value(single).unwrap(), serde_json::to_value(multiple).unwrap());
}

#[test]
fn cross_reference_and_reader_errors_include_a_source_line() {
    let dir = repo(true);
    write(dir.path(), "architecture.hy", &format!("\n{ARCH} :foundation absent)"));
    let stderr = error(dir.path(), "there is no layer");
    assert!(stderr.contains("architecture.hy:2:"), "{stderr}");
    write(dir.path(), "architecture.hy", &format!("{ARCH})"));
    write(dir.path(), "declarations/lists.hy", "\n(defarchitecture-lists sample :retired-words [");
    let stderr = error(dir.path(), "a parenthesis or string is not closed");
    assert!(stderr.contains("declarations/lists.hy:2:"), "{stderr}");
}

#[test]
fn hook_checks_glob_additions_and_deletions_against_untouched_sources() {
    for deleting in [false, true] {
        let dir = repo(true);
        let root = dir.path();
        config(root, r#"["architecture.hy", "declarations/**/*.hy"]"#);
        let settings = std::fs::read_to_string(root.join("pyproject.toml")).unwrap();
        write(root, "pyproject.toml", &settings.replace("\"DOEFF016\"", "\"DOEFF016\", \"DOEFF114\""));
        write(root, "declarations/services/queue.hy", "(defservice queue \"Queue\" {:layers [core]})\n");
        write(root, "declarations/lists.hy", "(defarchitecture-lists sample :retired-words [])\n");
        git(root, &["init", "-q", "-b", "main"]);
        git(root, &["add", "-A"]);
        git(root, &["commit", "-qm", "baseline"]);
        if deleting {
            git(root, &["rm", "declarations/services/queue.hy"]);
        } else {
            write(root, "declarations/new.hy", r#"(defarchitecture-lists sample
              :confined-spellings [(confined-spelling "forbidden" :patterns ["forbidden"] :files ["app/**/*.hy"] :why "keep out")])"#);
            git(root, &["add", "declarations/new.hy"]);
        }
        let output = run(root, &["--commit-hook"]);
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(1), "deleting={deleting}: {stderr}");
        assert!(stderr.contains(if deleting { "DOEFF114" } else { "DOEFF148" }), "{stderr}");
    }
}

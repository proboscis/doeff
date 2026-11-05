//! Tests for the CLI path of `doeff-indexer hy-index` -- `--stdin --path`, `--file`, root discovery and excluded
//! directories, and exit codes for argument errors.

use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

use serde_json::Value;

/// Write a test file (also creating the parent directory).
fn write_file(path: &Path, contents: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("create parent directories");
    }
    fs::write(path, contents).expect("write test file");
}

/// Run the binary with arguments and stdin, and return (exit code, stdout).
fn run(args: &[&str], stdin: &str) -> (i32, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-indexer"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn doeff-indexer");
    child.stdin.take().expect("stdin").write_all(stdin.as_bytes()).expect("write stdin");
    let output = child.wait_with_output().expect("wait doeff-indexer");
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Look up one file from the output's file list by path suffix.
fn file_entry<'a>(json: &'a Value, suffix: &str) -> &'a Value {
    json["files"]
        .as_array()
        .expect("files")
        .iter()
        .find(|file| file["path"].as_str().is_some_and(|path| path.ends_with(suffix)))
        .unwrap_or_else(|| panic!("{suffix} not found in the output"))
}

#[test]
fn stdin_content_is_indexed_as_the_given_path() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    write_file(&root.join("pkg/editing.hy"), "(defn saved-version [] 1)\n");
    let root_text = root.to_string_lossy();
    let (code, stdout) = run(
        &["hy-index", "--root", &root_text, "--stdin", "--path", "pkg/editing.hy"],
        "(defn unsaved-version [x]\n  \"Being edited.\"\n  (print x\n",
    );
    assert_eq!(code, 0);
    let json: Value = serde_json::from_str(&stdout).expect("json");
    assert_eq!(json["version"], 6);
    assert_eq!(json["raw_via"], "not-computed", "a single-file run does not compute via");
    let files = json["files"].as_array().expect("files");
    assert_eq!(files.len(), 1);
    let file = &files[0];
    assert_eq!(file["module"], "pkg.editing");
    assert!(file["path"].as_str().expect("path").starts_with('/'));
    let definition = &file["definitions"][0];
    assert_eq!(definition["name"], "unsaved-version");
    assert_eq!(definition["mangled"], "unsaved_version");
    assert_eq!(definition["kind"], "defn");
    assert_eq!(definition["docstring"], "Being edited.");
    assert_eq!(definition["params"], serde_json::json!(["x"]));
    assert_eq!(definition["container"], Value::Null);
    assert_eq!(definition["range"], serde_json::json!({"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 21}}));
    assert_eq!(file["errors"].as_array().expect("errors").len(), 2, "two unclosed brackets");
}

#[test]
fn root_walk_skips_excluded_directories_and_file_selects() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    write_file(&root.join("a/__init__.hy"), "(setv version 1)\n");
    write_file(&root.join("a/b.hyk"), "(defk run [] {:pre [] :post []} 1)\n");
    write_file(&root.join("c.hyp"), "(defp prog (run))\n");
    write_file(&root.join("not_hy.py"), "x = 1\n");
    for skipped in [".venv", "node_modules", "target", ".git", "__pycache__"] {
        write_file(&root.join(skipped).join("skip.hy"), "(defn skipped [] 1)\n");
    }
    let root_text = root.to_string_lossy();
    let (code, stdout) = run(&["hy-index", "--root", &root_text], "");
    assert_eq!(code, 0);
    let json: Value = serde_json::from_str(&stdout).expect("json");
    let files = json["files"].as_array().expect("files");
    assert_eq!(files.len(), 3, "{stdout}");
    assert_eq!(file_entry(&json, "a/__init__.hy")["module"], "a");
    assert_eq!(file_entry(&json, "a/b.hyk")["module"], "a.b");
    assert_eq!(file_entry(&json, "c.hyp")["definitions"][0]["kind"], "defp");

    let (code, stdout) = run(&["hy-index", "--root", &root_text, "--file", "c.hyp", "missing.hy"], "");
    assert_eq!(code, 0);
    let json: Value = serde_json::from_str(&stdout).expect("json");
    assert_eq!(json["files"].as_array().expect("files").len(), 2);
    let missing = file_entry(&json, "missing.hy");
    assert_eq!(missing["errors"].as_array().expect("errors").len(), 1, "an unreadable file is recorded in errors and processing continues");
}

#[test]
fn argument_errors_exit_with_2() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root_text = temp.path().to_string_lossy().into_owned();
    assert_eq!(run(&["hy-index", "--root", &root_text, "--stdin"], "").0, 2);
    assert_eq!(run(&["hy-index", "--root", &root_text, "--path", "x.hy"], "").0, 2);
    let missing_root = temp.path().join("no-such-dir");
    assert_eq!(run(&["hy-index", "--root", &missing_root.to_string_lossy()], "").0, 2);
}

#[test]
fn root_run_computes_raw_and_extra_catalog_is_read_from_a_file() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    write_file(
        &root.join("pkg/io.hy"),
        "(import httpx)\n(import mylib)\n(defhandler web []\n  (Fetch [u] (resume (httpx.get u))))\n(defk core [] (<- (Fetch \"u\")) (mylib.ping))\n",
    );
    write_file(&root.join("extra.json"), "{\"network\": [\"mylib\"], \"nope\": [\"x\"]}");
    let root_text = root.to_string_lossy();
    let extra = root.join("extra.json");
    let extra_text = extra.to_string_lossy();
    let (code, stdout) = run(&["hy-index", "--root", &root_text, "--raw-catalog-extra", &extra_text], "");
    assert_eq!(code, 0);
    let json: Value = serde_json::from_str(&stdout).expect("json");
    assert_eq!(json["raw_via"], "computed");
    assert_eq!(json["raw_catalog_problems"].as_array().expect("problems").len(), 1);
    let file = file_entry(&json, "pkg/io.hy");
    let core = file["definitions"].as_array().expect("definitions").iter().find(|d| d["name"] == "core").expect("core");
    assert_eq!(core["raw"]["direct"][0]["name"], "mylib.ping");
    assert_eq!(core["raw"]["direct"][0]["category"], "network");
    let web = file["definitions"].as_array().expect("definitions").iter().find(|d| d["name"] == "web").expect("web");
    assert_eq!(web["raw"]["direct"][0]["name"], "httpx.get");
    assert!(core.get("tier").is_none(), "rule verdicts (tier) are not emitted -- the linter is the source of truth for verdicts");
}

#[test]
fn unreadable_extra_catalog_is_an_argument_error() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root_text = temp.path().to_string_lossy();
    let (code, _) = run(&["hy-index", "--root", &root_text, "--raw-catalog-extra", "/no/such/extra.json"], "");
    assert_eq!(code, 2);
}

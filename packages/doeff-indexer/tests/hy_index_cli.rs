//! `doeff-indexer hy-index` の CLI の経路の検 — `--stdin --path`・`--file`・root の探索と除く
//! directory・引数の誤りの終了コード。

use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

use serde_json::Value;

/// test の file を書く(親の directory も作る)。
fn write_file(path: &Path, contents: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("create parent directories");
    }
    fs::write(path, contents).expect("write test file");
}

/// binary を引数と stdin で走らせ、(終了コード, stdout) を返す。
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

/// 出力の file の一覧から path の末尾で 1 件引く。
fn file_entry<'a>(json: &'a Value, suffix: &str) -> &'a Value {
    json["files"]
        .as_array()
        .expect("files")
        .iter()
        .find(|file| file["path"].as_str().is_some_and(|path| path.ends_with(suffix)))
        .unwrap_or_else(|| panic!("{suffix} が出力に無い"))
}

#[test]
fn stdin_content_is_indexed_as_the_given_path() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    write_file(&root.join("pkg/editing.hy"), "(defn saved-version [] 1)\n");
    let root_text = root.to_string_lossy();
    let (code, stdout) = run(
        &["hy-index", "--root", &root_text, "--stdin", "--path", "pkg/editing.hy"],
        "(defn unsaved-version [x]\n  \"編集中。\"\n  (print x\n",
    );
    assert_eq!(code, 0);
    let json: Value = serde_json::from_str(&stdout).expect("json");
    assert_eq!(json["version"], 2);
    let files = json["files"].as_array().expect("files");
    assert_eq!(files.len(), 1);
    let file = &files[0];
    assert_eq!(file["module"], "pkg.editing");
    assert!(file["path"].as_str().expect("path").starts_with('/'));
    let definition = &file["definitions"][0];
    assert_eq!(definition["name"], "unsaved-version");
    assert_eq!(definition["mangled"], "unsaved_version");
    assert_eq!(definition["kind"], "defn");
    assert_eq!(definition["docstring"], "編集中。");
    assert_eq!(definition["params"], serde_json::json!(["x"]));
    assert_eq!(definition["container"], Value::Null);
    assert_eq!(definition["range"], serde_json::json!({"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 21}}));
    assert_eq!(file["errors"].as_array().expect("errors").len(), 2, "閉じていない括弧が 2 つ");
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
    assert_eq!(missing["errors"].as_array().expect("errors").len(), 1, "読めない file は errors に積んで続ける");
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

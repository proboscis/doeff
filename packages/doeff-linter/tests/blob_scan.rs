//! 冷えた作業木の最初の 1 回が、repo の Hy の全部を読まない事の検(card ki-d56ba76422ba)。
//!
//! 1 file の実行は、書いた file が自分の引数を素で呼ぶ定義を持つ時、呼び手を探すために repo の Hy の約 2900 file を全部読む
//! (`program-params.scan`)。作業木ごとに別の inode なので、冷えた作業木では 1 file ごとに disk を待ち、混んだ Pod で 3〜5 秒かかって
//! hook の期限(4 秒)を越えた(冷えた最初の 1 回 70 回中、4 秒超の 8 回のうち 7 回がこの段)。
//! git の index が持つ blob の id は中身を決めるので、別の作業木が同じ中身を読んで置いた結果は、中身を読まずに引ける。
//!
//! 観測は、`DOEFF_LINTER_TIMING` が stderr へ出す `program-params.scan read=<中身を読んだ file の数>` と、editor-json の違反。

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

const FILLERS: usize = 80;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF126"]

[tool.doeff-linter.definitions]
paths = ["app"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "核" :roles [program] :imports [core])]
  :handler-arguments {:files ["app/**/*.hy"] :exclude ["**/tests/**"] :keep-mark "keep:"})
"#;

const TAGS: &str = "{:tags {:context \"c\" :role \"program\"}}";
const HOF: &str = "app/core/hof.hy";

fn write(dir: &Path, rel: &str, text: &str) {
    std::fs::create_dir_all(dir.join(rel).parent().unwrap()).unwrap();
    std::fs::write(dir.join(rel), text).unwrap();
}

fn git(dir: &Path, args: &[&str]) {
    let status = Command::new("git")
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"])
        .args(args)
        .current_dir(dir)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .status()
        .unwrap();
    assert!(status.success(), "git {args:?}");
}

/// 呼び手(user.hy)が hof の rows-by-text に Program を渡す repo を、commit まで作る。ほかに FILLERS 個の module。
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", CONFIG);
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, HOF, &format!("(defk rows-by-text [rows field-of] {TAGS}\n  (for [row rows]\n    (setv value (field-of row)))\n  rows)\n"));
    write(
        root,
        "app/core/user.hy",
        &format!("(import app.core.hof [rows-by-text])\n(defk tag-rows-of [tags] {TAGS}\n  (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p \"subject\")))))\n"),
    );
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
    }
    // 更新の直後の file は「今の書き込みと区別できない」(git の racy と同じ守り)ので、1 時間前にしてから index に載せる。
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

/// 1 file の実行(`--stdin --path`)。(editor-json・stderr)。
fn single(root: &Path, cache: &Path, rel: &str) -> (Value, String) {
    let source = std::fs::read_to_string(root.join(rel)).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log", "--stdin", "--path"])
        .arg(root.join(rel))
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_TIMING", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(source.as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    (serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({e}): {stdout}\n{stderr}")), stderr)
}

/// 呼び手を探す読みで、中身を実際に読んだ file の数(`program-params.scan read=N`)。
fn scan_reads(stderr: &str) -> usize {
    stderr
        .lines()
        .find_map(|line| line.strip_prefix("doeff-linter-timing: program-params.scan read="))
        .unwrap_or_else(|| panic!("呼び手を探す読みが走っていない:\n{stderr}"))
        .trim()
        .parse()
        .unwrap()
}

fn keys(report: &Value) -> Vec<String> {
    let mut keys: Vec<String> = report["violations"].as_array().unwrap().iter().filter_map(|v| v["key"].as_str()).map(str::to_string).collect();
    keys.sort();
    keys
}

/// 別の作業木が同じ中身を読んで置いていれば、新しい作業木の最初の 1 回は repo の Hy をほとんど読まない。答えは読んだ時と同じ。
#[test]
fn a_fresh_worktree_does_not_read_files_another_worktree_already_read() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), HOF);
    assert!(scan_reads(&stderr) >= FILLERS, "1 つ目の作業木は全部読む: {stderr}");

    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HOF);
    let read = scan_reads(&stderr);
    assert!(read <= FILLERS / 4, "冷えた作業木で {read} file の中身を読んだ(全 {} file): {stderr}", FILLERS + 3);
    assert_eq!(keys(&first), keys(&second), "読まなくても答えは同じ");
}

/// 作業木で書き替えた file(blob の id が index と違う)は、cache の答えでなく今の中身を読む — 呼び手が消えれば当たりも消える。
#[test]
fn a_file_changed_in_the_worktree_is_read_not_remembered() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (before, _) = single(main.path(), cache.path(), HOF);
    assert!(!keys(&before).is_empty() || before["violations"].is_array());

    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    // 呼び手が rows-by-text を呼ばなくなる(同じ大きさにはしない)。
    write(&fresh, "app/core/user.hy", "(defn plain [] 1)\n");
    let (after, stderr) = single(&fresh, cache.path(), HOF);
    let whole = {
        let none = tempfile::TempDir::new().unwrap();
        single(&fresh, none.path(), HOF).0
    };
    assert_eq!(keys(&after), keys(&whole), "cache を使った答えは、cache の無い実行と同じ: {stderr}");
    assert!(scan_reads(&stderr) >= 1, "書き替えた file は読む: {stderr}");
}

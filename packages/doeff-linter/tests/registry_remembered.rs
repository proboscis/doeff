//! 登録簿の dir(1 鍵 1 file)を、1 file の実行が毎回全部読まない事の検。
//!
//! 登録簿の dir は agora-controllers で 447 file あり、冷えた作業木では 1 file ごとに disk を待つ。1 回の実行の open の 83% がこの dir だった
//! (2026-10-17・Pod の IO の圧迫 55〜66%・open 596 件のうち 494 件)。dir の中の file の(名・大きさ・更新時刻)が前の実行と同じなら、
//! 中身を読まずに前の読みを使う。答えは変えない — 中身が変われば(名・大きさ・更新時刻のどれかが変わるので)読み直す。
//!
//! 観測は、実行の段の名(`DOEFF_LINTER_TIMING` が stderr へ出す `registry.read`)と、editor-json の `registered`。

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

const CONFIG: &str = r#"
[tool.doeff-linter]
enable = ["DOEFF104"]

[tool.doeff-linter.layers]
order = ["core"]
paths = { core = "app/core" }

[tool.doeff-linter.registry]
dirs = ["registry/BREACHES"]
"#;

const KEYS: usize = 60;

/// 一時の repo: タグの無い module(DOEFF104 が出る)と、鍵 60 個の登録簿(うち 1 つが untagged.hy::DOEFF104)。
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/untagged.hy"), "(defn decide [x] x)\n").unwrap();
    std::fs::create_dir_all(dir.path().join("registry/BREACHES")).unwrap();
    for n in 0..KEYS {
        let key = if n == 7 { "app/core/untagged.hy::DOEFF104".to_string() } else { format!("app/core/other{n}.hy::DOEFF104") };
        std::fs::write(dir.path().join(format!("registry/BREACHES/k{n:02}.txt")), format!("{key}\n理由 {n}\n")).unwrap();
    }
    age_registry(dir.path());
    dir
}

/// 登録簿の file の更新時刻を 1 時間前にする(更新の直後の file は「今の書き込みと区別できない」ので覚えない — その守りを外すため)。
fn age_registry(root: &Path) {
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in std::fs::read_dir(root.join("registry/BREACHES")).unwrap().filter_map(Result::ok) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
}

/// 1 file の実行(`--stdin --path`)。(editor-json・stderr)を返す。
fn single(root: &Path, cache: &Path, rel: &str) -> (Value, String) {
    let source = std::fs::read_to_string(root.join(rel)).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log", "--stdin", "--path"])
        .arg(root.join(rel))
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_TIMING", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
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

fn violation<'a>(report: &'a Value, key: &str) -> &'a Value {
    report["violations"].as_array().unwrap().iter().find(|v| v["key"] == key).unwrap_or_else(|| panic!("{key} が無い: {report}"))
}

/// 登録簿の file を実際に読んだ回数(段 `registry.read` の出現)。
fn reads(stderr: &str) -> usize {
    stderr.lines().filter(|line| line.contains("timing: registry.read ")).count()
}

#[test]
fn a_registry_dir_that_did_not_change_is_not_read_again() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, first_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&first_log), 1, "1 回目は登録簿を読む\n{first_log}");
    assert_eq!(violation(&first, "app/core/untagged.hy::DOEFF104")["registered"], true);
    let (second, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 0, "変わっていない登録簿は 2 回目に読まない\n{second_log}");
    assert_eq!(second["violations"], first["violations"], "覚えた登録簿でも答えは同じ");
}

#[test]
fn a_registry_file_that_changed_is_read_again_and_the_answer_follows() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, _) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(violation(&first, "app/core/untagged.hy::DOEFF104")["registered"], true);
    // 鍵の file を、同じ大きさのまま別の鍵に替える(大きさは同じ・更新時刻だけが変わる)。
    std::fs::write(dir.path().join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF105\n理由 7\n").unwrap();
    age_registry(dir.path());
    let (second, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 1, "中身を替えた登録簿は読み直す\n{second_log}");
    assert_eq!(violation(&second, "app/core/untagged.hy::DOEFF104")["registered"], false, "登録簿から外れた鍵は registered でなくなる");
}

#[test]
fn a_registry_file_added_or_removed_is_read_again() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    single(dir.path(), cache.path(), "app/core/untagged.hy");
    std::fs::remove_file(dir.path().join("registry/BREACHES/k07.txt")).unwrap();
    let (removed, removed_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&removed_log), 1, "{removed_log}");
    assert_eq!(violation(&removed, "app/core/untagged.hy::DOEFF104")["registered"], false);
    std::fs::write(dir.path().join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF104\n理由 7\n").unwrap();
    age_registry(dir.path());
    let (added, added_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&added_log), 1, "{added_log}");
    assert_eq!(violation(&added, "app/core/untagged.hy::DOEFF104")["registered"], true);
}

/// 更新の直後の file は、同じ瞬間の別の書き込みと更新時刻で区別できない — 覚えずに毎回読む(git の racy と同じ守り)。
#[test]
fn a_registry_file_written_a_moment_ago_is_always_read() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("registry/BREACHES/k08.txt"), "app/core/other8.hy::DOEFF104\n理由 8\n").unwrap(); // 更新時刻 = 今
    let (_, first_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    let (_, second_log) = single(dir.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!((reads(&first_log), reads(&second_log)), (1, 1), "{first_log}\n{second_log}");
}

/// git を撃つ(呼び手の env の `GIT_DIR` などに引かれない)。
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

/// `repo` を commit まで作り、同じ commit の新しい作業木を足す(作業木ごとに別の path・inode・更新時刻 — 冷えた新しい作業木の形)。
fn repo_with_fresh_worktree() -> (tempfile::TempDir, tempfile::TempDir, std::path::PathBuf) {
    let main = repo();
    git(main.path(), &["init", "-q", "-b", "main"]);
    git(main.path(), &["add", "-A"]);
    git(main.path(), &["commit", "-q", "-m", "base"]);
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    // 作ったばかりの作業木の file は、更新から 2 秒の内で覚えない(読む間に書かれうる)。git が普段するように、時を経た file の stat を index に
    // 載せ直しておく(作業木で `git status` などを撃った後の形)。
    age_registry(&fresh);
    git(&fresh, &["update-index", "-q", "--refresh"]);
    (main, parent, fresh)
}

/// 新しい作業木の登録簿は、git の index の blob の id が別の作業木と同じなら、別の作業木が読んだ結果を使い、中身を読まない
/// (冷えた新しい作業木で 449 file を読む待ちが 0.8〜4.8 秒)。答えは読んだ時と同じ。
#[test]
fn a_fresh_worktree_does_not_read_the_registry_another_worktree_read() {
    let (main, _parent, fresh) = repo_with_fresh_worktree();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, first_log) = single(main.path(), cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&first_log), 1, "1 つ目の作業木は登録簿を読む\n{first_log}");
    let (second, second_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&second_log), 0, "新しい作業木が、別の作業木の読んだ登録簿を読み直した\n{second_log}");
    assert_eq!(violation(&second, "app/core/untagged.hy::DOEFF104")["registered"], true);
    assert_eq!(without_paths(&second), without_paths(&first), "読まなくても答えは同じ(作業木ごとに違う絶対 path を除く)");
}

/// 違反の列から、作業木ごとに違う絶対 path の欄を除いた物(作業木をまたいで答えを比べるため)。
fn without_paths(report: &Value) -> Vec<Value> {
    report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|violation| {
            let mut violation = violation.clone();
            violation.as_object_mut().unwrap().remove("path");
            violation
        })
        .collect()
}

/// 新しい作業木で書き替えた登録簿の file(index の blob と違う中身)や、index に無い file は、別の作業木の結果を使わずに読む — 答えが付いてくる。
#[test]
fn a_registry_file_changed_or_added_in_a_fresh_worktree_is_read() {
    let (main, _parent, fresh) = repo_with_fresh_worktree();
    let cache = tempfile::TempDir::new().unwrap();
    single(main.path(), cache.path(), "app/core/untagged.hy");
    std::fs::write(fresh.join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF105\n理由 7\n").unwrap();
    let (changed, changed_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&changed_log), 1, "書き替えた登録簿を読まなかった\n{changed_log}");
    assert_eq!(violation(&changed, "app/core/untagged.hy::DOEFF104")["registered"], false);
    std::fs::write(fresh.join("registry/BREACHES/k07.txt"), "app/core/untagged.hy::DOEFF104\n理由 7\n").unwrap();
    git(&fresh, &["checkout", "--", "registry/BREACHES/k07.txt"]);
    std::fs::write(fresh.join("registry/BREACHES/k99.txt"), "app/core/untagged.hy::DOEFF104\n足した理由\n").unwrap();
    std::fs::remove_file(fresh.join("registry/BREACHES/k07.txt")).unwrap();
    let (added, added_log) = single(&fresh, cache.path(), "app/core/untagged.hy");
    assert_eq!(reads(&added_log), 1, "index に無い登録簿の file を読まなかった\n{added_log}");
    assert_eq!(violation(&added, "app/core/untagged.hy::DOEFF104")["registered"], true, "足した file の鍵が効かない");
}

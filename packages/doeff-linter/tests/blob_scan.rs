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

/// 呼び手を探す読みの計器の数(`program-params.scan <field>=N`)。
fn scan_count(stderr: &str, field: &str) -> usize {
    let prefix = format!("doeff-linter-timing: program-params.scan {field}=");
    stderr
        .lines()
        .find_map(|line| line.strip_prefix(prefix.as_str()))
        .unwrap_or_else(|| panic!("program-params.scan の {field} が出ていない:\n{stderr}"))
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

/// 新しい作業木の最初の 1 回は、綴りの覚えを file ごとの置き場の file でなく、index の並びの塊ごとの file から引く — 冷えた作業木で置き場の file を
/// repo の Hy の数だけ開いて disk を待たない(card ki-40c8ca380713 — 冷えた新しい作業木で約 4600 open)。答えは読んだ時と同じ。
#[test]
fn a_fresh_worktree_reads_the_spellings_from_a_few_chunks_not_one_object_per_file() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, _) = single(main.path(), cache.path(), HOF);
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HOF);
    let chunks = scan_count(&stderr, "chunks");
    let missed = scan_count(&stderr, "missed");
    assert!(chunks >= 1 && chunks <= 8, "塊を {chunks} 読んだ(全 {} file): {stderr}", FILLERS + 3);
    assert_eq!(missed, 0, "塊の表に無い blob が {missed} — 塊から引いていない: {stderr}");
    assert_eq!(keys(&first), keys(&second), "塊から引いても答えは同じ");
}

/// 失敗の型を引く file(match の腕の型が、別の file の defeffect の `:failure` で宣言される)と、その宣言の file・ほかに FILLERS 個の module を
/// commit まで作った repo。
fn failure_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", &CONFIG.replace(r#"enable = ["DOEFF126"]"#, r#"enable = ["DOEFF122", "DOEFF126"]"#));
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/core/errs.hy", "(defrecord Unreachable [reason])\n(defrecord Accepted [word])\n");
    write(root, "app/core/effs.hy", "(import app.core.errs)\n(defeffect ReadRow {:fields [key] :answer None :failure [app.core.errs.Unreachable]})\n");
    write(root, "app/core/other_effs.hy", "(defeffect Other {:fields [key] :answer None :absent [Gone]})\n");
    write(
        root,
        NEEDS,
        &format!(
            "(import app.core.errs [Unreachable Accepted])\n(defk decide [x] {TAGS}\n  (<- said (| Accepted Unreachable) (verdict x))\n  (match said\n    (Unreachable) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail)))\n                      (return refused))\n    (Accepted) None))\n"
        ),
    );
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
    }
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

const NEEDS: &str = "app/core/needs.hy";

/// 段 `label` の計器の数(`<label> <field>=N`)。
fn count_of(stderr: &str, label: &str, field: &str) -> usize {
    let prefix = format!("doeff-linter-timing: {label} {field}=");
    stderr.lines().find_map(|line| line.strip_prefix(prefix.as_str())).unwrap_or_else(|| panic!("{label} の {field} が出ていない:\n{stderr}")).trim().parse().unwrap()
}

/// 失敗の型の宣言を探す歩きも、新しい作業木の最初の 1 回は、別の作業木が読んだ file の中身を読まない(綴りの覚えを塊から引く)— 冷えた作業木で
/// repo の Hy の全部を読む待ちが 3 秒だった(card ki-40c8ca380713)。宣言の綴りを含む file も、型の名を綴らないと覚えが言えば読まない。答えは読んだ時と同じ。
#[test]
fn a_fresh_worktree_finds_the_failure_declarations_without_reading_the_repo() {
    let main = failure_repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), NEEDS);
    assert!(keys(&first).iter().any(|k| k.contains("DOEFF122")), "needs.hy に DOEFF122 が当たらない(検の材料の誤り)\n{stderr}");
    assert!(count_of(&stderr, "failure-types.collect", "read") >= FILLERS, "1 つ目の作業木は全部読む: {stderr}");
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), NEEDS);
    let read = count_of(&stderr, "failure-types.collect", "read");
    assert!(read <= FILLERS / 4, "冷えた作業木で {read} file の中身を読んだ(全 {} file): {stderr}", FILLERS + 4);
    assert_eq!(count_of(&stderr, "failure-types.collect", "missed"), 0, "塊の表に無い blob があった: {stderr}");
    assert_eq!(keys(&first), keys(&second), "読まなくても答えは同じ");
}

/// class の frozen を引く handler の file(型で判じる引数 `#^ Cfg cfg`)と、同じ名の frozen の class と frozen でない class・Python の class・ほかに
/// FILLERS 個の module を commit まで作った repo。
fn class_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", &CONFIG.replace(r#"enable = ["DOEFF126"]"#, r#"enable = ["DOEFF142"]"#));
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/core/cfgs.hy", "(defrecord Cfg [#^ str url])\n");
    write(root, "app/core/legacy.hy", "(defclass Cfg [] (setv x 1))\n");
    write(root, "tools/settings.py", "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass Settings:\n    x: int\n");
    write(root, HANDLERS, "(import app.core.cfgs [Cfg])\n(defhandler h [#^ Cfg cfg]\n  (Ping [e k] (k 1)))\n");
    for i in 0..FILLERS {
        write(root, &format!("app/core/filler{i}.hy"), &format!("(defn filler-{i} [x] (+ x {i}))\n"));
        write(root, &format!("tools/filler{i}.py"), &format!("def filler_{i}(x):\n    return x + {i}\n"));
    }
    let old = SystemTime::now() - Duration::from_secs(3600);
    for entry in walkdir::WalkDir::new(root).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "base"]);
    dir
}

const HANDLERS: &str = "app/core/handlers.hy";

/// class の frozen を引く 1 file の実行も、新しい作業木の最初の 1 回は、別の作業木が読んだ Hy と Python の file の中身を読まない(card ki-40c8ca380713 の (c) —
/// 順に歩いて repo の全部を読んでいた)。答えは読んだ時と同じ(同じ名の frozen でない class が在るので Cfg は frozen でない)。
#[test]
fn a_fresh_worktree_builds_the_class_index_without_reading_the_repo() {
    let main = class_repo();
    let cache = tempfile::TempDir::new().unwrap();
    let (first, stderr) = single(main.path(), cache.path(), HANDLERS);
    assert!(keys(&first).iter().any(|k| k.contains("DOEFF142")), "handlers.hy に DOEFF142 が当たらない(検の材料の誤り)\n{stderr}");
    assert!(count_of(&stderr, "handler-argument-classes.scan", "read") >= 2 * FILLERS, "1 つ目の作業木は全部読む: {stderr}");
    let parent = tempfile::TempDir::new().unwrap();
    let fresh = parent.path().join("fresh");
    git(main.path(), &["worktree", "add", "-q", "--detach", fresh.to_str().unwrap()]);
    let (second, stderr) = single(&fresh, cache.path(), HANDLERS);
    let read = count_of(&stderr, "handler-argument-classes.scan", "read");
    assert!(read <= FILLERS / 2, "冷えた作業木で {read} file の中身を読んだ(全 {} file): {stderr}", 2 * FILLERS + 4);
    assert_eq!(keys(&first), keys(&second), "読まなくても答えは同じ");
}

/// 別の commit から作った作業木(file を 1 つ変えて commit した後)も、塊の表を引き、表に無いのは変えた file の blob だけ — 塊の名は塊の頭の path で
/// 決まり、表は blob の行を足していくので、commit が違っても変わっていない file は当たる(card ki-40c8ca380713)。答えは読んだ時と同じ。
#[test]
fn a_worktree_of_a_later_commit_misses_only_the_changed_file() {
    let main = repo();
    let cache = tempfile::TempDir::new().unwrap();
    single(main.path(), cache.path(), HOF);
    // 大きさも変える(git は更新時刻の ns を比べず、同じ秒・同じ大きさの書き替えを変わっていないと見る)。
    write(main.path(), "app/core/filler7.hy", "(defn filler-7 [x] (* x 7 7))\n");
    let old = SystemTime::now() - Duration::from_secs(3600);
    std::fs::File::options().write(true).open(main.path().join("app/core/filler7.hy")).unwrap().set_modified(old).unwrap();
    git(main.path(), &["commit", "-q", "-am", "change one file"]);
    let parent = tempfile::TempDir::new().unwrap();
    let later = parent.path().join("later");
    git(main.path(), &["worktree", "add", "-q", "--detach", later.to_str().unwrap()]);
    for entry in walkdir::WalkDir::new(&later).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file() && !e.path().components().any(|c| c.as_os_str() == ".git")) {
        std::fs::File::options().write(true).open(entry.path()).unwrap().set_modified(old).unwrap();
    }
    git(&later, &["update-index", "-q", "--refresh"]);
    let (report, stderr) = single(&later, cache.path(), HOF);
    assert_eq!(scan_count(&stderr, "missed"), 1, "表に無い blob が変えた file の 1 つでない: {stderr}");
    // 中身を読むのは、変えた file と、呼び先の名を綴るので解析に読む 2 file(hof.hy・user.hy)だけ。
    assert!(scan_reads(&stderr) <= 3, "変えた file と呼び先を綴る file のほかも中身を読んだ: {stderr}");
    let whole = {
        let none = tempfile::TempDir::new().unwrap();
        single(&later, none.path(), HOF).0
    };
    assert_eq!(keys(&report), keys(&whole), "表から引いた答えが、cache の無い実行と違う");
}

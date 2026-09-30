//! 事実の cache の根の dir の片づけ(agora-redesign #1903)の検 — 一時の置き場で binary を走らせ、置き場の dir を見る。
//!
//! 根の dir には根の path が記録され、前の走査から間隔(1 時間)が過ぎた次の実行で、記録した根が無くなった dir だけが消える。
//! 間隔が過ぎた事は、置き場の走査の時刻の記録(`.swept`)を古い値に書き換えて表す。

use std::path::{Path, PathBuf};
use std::process::Command;

const CONFIG: &str = r#"
[tool.doeff-linter]
enable = ["DOEFF106"]

[tool.doeff-linter.layers]
order = ["core", "foundation"]
paths = { core = "app/core", foundation = "app/foundation" }

[tool.doeff-linter.layers.allow_imports]
core = ["core"]
foundation = ["foundation"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

/// 事実の cache を書く最小の repo(repo 全体の Hy の索引を cache する DOEFF106 を有効にする)。
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/clock.hy"), "(import time)\n(defn now [] (time.time))\n").unwrap();
    dir
}

/// binary を root で、置き場 cache の cache を使って走らせる。
fn run(root: &Path, cache: &Path) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .env("DOEFF_LINTER_CACHE_DIR", cache)
        .output()
        .unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
}

/// 記録した根の path が root の根の dir(無ければ None)。
fn root_dir_of(cache: &Path, root: &Path) -> Option<PathBuf> {
    let wanted = std::fs::canonicalize(root).unwrap();
    std::fs::read_dir(cache)
        .unwrap()
        .flatten()
        .map(|e| e.path())
        .find(|dir| std::fs::read_to_string(dir.join("root.path")).is_ok_and(|text| Path::new(&text) == wanted))
}

/// 前の走査から間隔が過ぎた事にする。
fn interval_passes(cache: &Path) {
    std::fs::write(cache.join(".swept"), "0").unwrap();
}

#[test]
fn a_vanished_root_dir_is_cleared_on_the_next_run_after_the_interval() {
    let cache = tempfile::TempDir::new().unwrap();
    let gone = repo();
    let live = repo();
    run(gone.path(), cache.path());
    let gone_dir = root_dir_of(cache.path(), gone.path()).expect("走った根の dir に根の path が記録される");
    assert!(gone_dir.join("hy-index.bin.d").is_dir(), "根の dir に事実が置かれる");
    let gone_path = gone.path().to_path_buf();
    drop(gone);
    assert!(!gone_path.exists());
    run(live.path(), cache.path());
    assert!(gone_dir.is_dir(), "間隔の内(同じ 1 時間の中)の実行は走査しない");
    interval_passes(cache.path());
    run(live.path(), cache.path());
    assert!(!gone_dir.exists(), "間隔が過ぎた次の実行で、根が無くなった dir が消える");
}

#[test]
fn a_root_dir_in_use_stays() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept = repo();
    let other = repo();
    run(kept.path(), cache.path());
    let kept_dir = root_dir_of(cache.path(), kept.path()).expect("根の dir");
    interval_passes(cache.path());
    run(other.path(), cache.path());
    assert!(kept_dir.join("hy-index.bin.d").is_dir(), "根が在る dir は、走査の後も事実ごと残る");
}

#[test]
fn a_dir_without_a_record_stays() {
    let cache = tempfile::TempDir::new().unwrap();
    // この仕組みより前に作られた根の dir(根の path の記録が無い)
    let unrecorded = cache.path().join("00000000deadbeef");
    std::fs::create_dir_all(unrecorded.join("hy-index.bin.d")).unwrap();
    std::fs::write(unrecorded.join("hy-index.bin.d").join("00"), b"old").unwrap();
    let live = repo();
    interval_passes(cache.path());
    run(live.path(), cache.path());
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "走査は行われた(走った根の dir は記録される)");
    assert!(unrecorded.join("hy-index.bin.d").join("00").is_file(), "記録の無い dir は中身ごと残る");
}

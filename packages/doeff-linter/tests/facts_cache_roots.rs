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

// ── 置き場の全体の上限(agora-redesign #2725)──────────────────────────────────────────
// 根が在り続ける作業木の dir は、根が消えた dir の片づけでは消えず、根の数だけ増え続けた(zeus で 62G)。全体が上限を超えたら、最後に
// 使われた時刻の古い dir から消して上限の内へ戻す。この実行の根と、間隔の内に使われた dir は消さない。

/// binary を root で、置き場 cache と全体の上限 cap(byte)で走らせる。
fn run_with_cap(root: &Path, cache: &Path, cap: u64) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .env("DOEFF_LINTER_CACHE_DIR", cache)
        .env("DOEFF_LINTER_CACHE_MAX_BYTES", cap.to_string())
        .output()
        .unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
}

/// 根の在る別の根の dir を作り、bytes の大きさの塊を置いて、最後に使われた時刻を ago 秒前にする(印 `used` の更新時刻)。
fn stale_root_dir(cache: &Path, name: &str, root: &Path, bytes: usize, ago: u64) -> PathBuf {
    let dir = cache.join(name);
    std::fs::create_dir_all(dir.join("hy-index.bin.d")).unwrap();
    std::fs::write(dir.join("root.path"), std::fs::canonicalize(root).unwrap().to_str().unwrap()).unwrap();
    std::fs::write(dir.join("hy-index.bin.d").join("00"), vec![0u8; bytes]).unwrap();
    let used = dir.join("used");
    std::fs::write(&used, b"").unwrap();
    let at = std::time::SystemTime::now() - std::time::Duration::from_secs(ago);
    std::fs::File::options().write(true).open(&used).unwrap().set_modified(at).unwrap();
    dir
}

#[test]
fn over_the_cap_the_least_recently_used_dirs_go_first_until_within_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    // 根が在り続ける 3 つの dir(どれも根が消えた片づけでは消えない)— 古い順に oldest・older・recent。
    let oldest = stale_root_dir(cache.path(), "00000000000000a1", kept_root.path(), 4000, 30 * 86400);
    let older = stale_root_dir(cache.path(), "00000000000000a2", kept_root.path(), 4000, 20 * 86400);
    let recent = stale_root_dir(cache.path(), "00000000000000a3", kept_root.path(), 4000, 10 * 86400);
    interval_passes(cache.path());
    // 上限 = 塊 2 つぶんより少し少ない(この実行の根の dir の分も入る)— 古い 2 つが消え、新しい 1 つが残る。
    run_with_cap(live.path(), cache.path(), 6000);
    assert!(!oldest.exists(), "上限を超えたら、最後に使われた時刻のいちばん古い dir から消える");
    assert!(!older.exists(), "上限の内に戻るまで、次に古い dir も消える");
    assert!(recent.join("hy-index.bin.d").join("00").is_file(), "上限の内に戻ったら、それより新しい dir は残る");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "この実行の根の dir は残る");
}

#[test]
fn a_dir_used_within_the_interval_stays_even_over_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    // 並走する実行が使っている根(間隔の内に使われた)— 上限を超えても消さない。
    let busy = stale_root_dir(cache.path(), "00000000000000b1", kept_root.path(), 8000, 60);
    interval_passes(cache.path());
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(busy.join("hy-index.bin.d").join("00").is_file(), "間隔の内に使われた dir は上限を超えても残る");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "この実行の根の dir は残る");
}

#[test]
fn within_the_cap_nothing_is_cleared_and_each_run_marks_its_root_used() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "00000000000000c1", kept_root.path(), 100, 30 * 86400);
    interval_passes(cache.path());
    run_with_cap(live.path(), cache.path(), 1_000_000);
    assert!(old.join("hy-index.bin.d").join("00").is_file(), "上限の内なら、古いだけの dir は消えない(時間だけを理由に消さない)");
    let live_dir = root_dir_of(cache.path(), live.path()).expect("根の dir");
    assert!(live_dir.join("used").is_file(), "実行は根の dir に使った印を付ける");
}

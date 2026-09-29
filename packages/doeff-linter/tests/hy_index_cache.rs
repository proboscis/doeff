//! repo 全体の Hy の索引の file ごとの cache(agora-redesign #1364)の検 — 一時の repo で binary を走らせ、出力の鍵を見る。
//!
//! 生の副作用の当たり(DOEFF106)は索引の生の副作用の証拠から出るので、索引がどの file の中身から組まれたかが鍵に現れる。
//! cache の鍵(file の大きさと更新時刻)を保ったまま中身だけを替えると、cache の索引が使われていれば古い中身の当たりが残る —
//! それで「変わった file だけ読み直す」を外から確かめる(実際の編集は更新時刻が進むので、この古さは起きない)。

use serde_json::Value;
use std::path::Path;
use std::process::Command;
use std::time::SystemTime;

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

const CLOCK_RAW: &str = "(import time)\n(defn now [] (time.time))\n";
// CLOCK_RAW と同じ長さで、生の副作用の無い中身(import していない名前を呼ぶ)。
const CLOCK_TAME: &str = "(import time)\n(defn now [] (tame.time))\n";
const OTHER_PLAIN: &str = "(defn plan [x] x)\n";
const OTHER_RAW: &str = "(import time)\n(defn plan [x] (time.time))\n";

fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::create_dir_all(dir.path().join("app/foundation")).unwrap();
    std::fs::write(dir.path().join("app/core/clock.hy"), CLOCK_RAW).unwrap();
    std::fs::write(dir.path().join("app/core/other.hy"), OTHER_PLAIN).unwrap();
    dir
}

/// binary を root で走らせ、DOEFF106 の鍵を並べる。cache = Some(dir) ならその置き場の cache を使い、None なら使わない。
fn raw_keys(root: &Path, cache: Option<&Path>) -> Vec<String> {
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command.args(["--output-format", "editor-json", "--no-log"]).current_dir(root).env_remove("DOEFF_LINTER_NO_CACHE");
    match cache {
        Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir),
        None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
    };
    let output = command.output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({}): {}", e, stdout));
    let mut keys: Vec<String> = report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| v["rule"] == "DOEFF106")
        .map(|v| v["key"].as_str().unwrap_or("").to_string())
        .collect();
    keys.sort();
    keys
}

/// path の中身を書き換え、更新時刻を書き換える前の値に戻す(大きさも同じなら cache の鍵は変わらない)。
fn rewrite_keeping_stamp(path: &Path, text: &str) {
    let before: SystemTime = std::fs::metadata(path).unwrap().modified().unwrap();
    std::fs::write(path, text).unwrap();
    std::fs::File::options().write(true).open(path).unwrap().set_modified(before).unwrap();
}

fn cache_files(cache: &Path) -> Vec<String> {
    let mut names: Vec<String> = walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

#[test]
fn only_the_changed_file_is_indexed_again_and_the_answer_matches_the_uncached_run() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let clock = "app/core/clock.hy::DOEFF106::now::time.time".to_string();
    let other = "app/core/other.hy::DOEFF106::plan::time.time".to_string();

    // 1 回目: cache を作る。答えは cache を使わない実行と同じ。
    let first = raw_keys(dir.path(), Some(cache.path()));
    assert_eq!(first, vec![clock.clone()]);
    assert_eq!(first, raw_keys(dir.path(), None));
    assert!(cache_files(cache.path()).contains(&"hy-index.bin".to_string()), "{:?}", cache_files(cache.path()));

    // 変わらない 2 回目も同じ答え。
    assert_eq!(raw_keys(dir.path(), Some(cache.path())), first);

    // clock.hy の鍵(大きさ・更新時刻)を保って中身だけ替える — cache の索引が使われ、古い当たりが残る。
    rewrite_keeping_stamp(&dir.path().join("app/core/clock.hy"), CLOCK_TAME);
    // other.hy は普通に書き換える(更新時刻が進む)— この file だけが読み直され、新しい当たりが出る。
    std::thread::sleep(std::time::Duration::from_millis(20));
    std::fs::write(dir.path().join("app/core/other.hy"), OTHER_RAW).unwrap();
    assert_eq!(raw_keys(dir.path(), Some(cache.path())), vec![clock.clone(), other.clone()]);

    // cache を使わない実行は、今の中身どおり(clock.hy の当たりは消えている)。
    assert_eq!(raw_keys(dir.path(), None), vec![other]);
}

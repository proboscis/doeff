//! Check of the per-file cache of the whole-repo Hy index — run the binary in a temporary repo and look at the keys in the output.
//!
//! Raw side-effect hits (DOEFF106) come from the raw side-effect evidence in the index, so the keys show which file contents the index was built from.
//! If only the contents change while the cache key (file size and modification time) is preserved, hits from the stale contents remain when the cached index is used —
//! this verifies from the outside that only changed files are re-read (a real edit advances the modification time, so this staleness would not occur).

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
// Same length as CLOCK_RAW, with contents that have no raw side effect (calls a name that is not imported).
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

/// Run the binary at root and list the DOEFF106 keys. With cache = Some(dir) the cache at that location is used; with None no cache is used.
fn raw_keys(root: &Path, cache: Option<&Path>) -> Vec<String> {
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command.args(["--output-format", "editor-json", "--no-log"]).current_dir(root).env_remove("DOEFF_LINTER_NO_CACHE");
    match cache {
        Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir).env("DOEFF_LINTER_SWEEP_INLINE", "1"),
        None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
    };
    let output = command.output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}", e, stdout));
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

/// Rewrite the contents of path and restore the modification time to its pre-rewrite value (if the size is also the same, the cache key does not change).
fn rewrite_keeping_stamp(path: &Path, text: &str) {
    let before: SystemTime = std::fs::metadata(path).unwrap().modified().unwrap();
    std::fs::write(path, text).unwrap();
    std::fs::File::options().write(true).open(path).unwrap().set_modified(before).unwrap();
}

/// List the files at the cache location as pairs of kind-dir name and chunk name (`<kind>.<shape>.d/<chunk>`) (chunks are stored separately per kind).
fn cache_files(cache: &Path) -> Vec<String> {
    let mut names: Vec<String> = walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| {
            let parent = e.path().parent().and_then(|p| p.file_name()).map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            format!("{}/{}", parent, e.file_name().to_string_lossy())
        })
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

    // First run: build the cache. The answer is the same as a run without the cache.
    let first = raw_keys(dir.path(), Some(cache.path()));
    assert_eq!(first, vec![clock.clone()]);
    assert_eq!(first, raw_keys(dir.path(), None));
    assert!(cache_files(cache.path()).iter().any(|f| f.starts_with("hy-index.bin.d/")), "{:?}", cache_files(cache.path()));

    // The unchanged second run gives the same answer.
    assert_eq!(raw_keys(dir.path(), Some(cache.path())), first);

    // Change only the contents while preserving clock.hy's key (size and modification time) — the cached index is used, and the stale hit remains.
    rewrite_keeping_stamp(&dir.path().join("app/core/clock.hy"), CLOCK_TAME);
    // other.hy is rewritten normally (the modification time advances) — only this file is re-read and the new hit appears.
    std::thread::sleep(std::time::Duration::from_millis(20));
    std::fs::write(dir.path().join("app/core/other.hy"), OTHER_RAW).unwrap();
    assert_eq!(raw_keys(dir.path(), Some(cache.path())), vec![clock.clone(), other.clone()]);

    // A run without the cache reflects the current contents (clock.hy's hit is gone).
    assert_eq!(raw_keys(dir.path(), None), vec![other]);
}

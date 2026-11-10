//! Tests for cleanup of the facts cache's root dirs: run the binary against a temporary store and look at the store's dirs.
//!
//! A root dir records the root's path, and on the next run after the interval (1 hour) has passed since the previous scan, only dirs whose recorded root no longer exists are deleted.
//! That the interval has passed is represented by rewriting the record of the store's scan time (`.swept`) to an old value.

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

/// The smallest repo that writes the facts cache (enables DOEFF106, which caches the whole repo's Hy index).
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/clock.hy"), "(import time)\n(defn now [] (time.time))\n").unwrap();
    dir
}

/// Runs the binary with root as the root and the store cache as the facts cache.
fn run(root: &Path, cache: &Path) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        // Marker for re-entry into the machine's test slot: these tests look at store cleanup, so run them without taking the machine's slot (on a busy machine it waits several minutes).
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .output()
        .unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
}

/// The root dir whose recorded root path is root (None if none).
fn root_dir_of(cache: &Path, root: &Path) -> Option<PathBuf> {
    let wanted = std::fs::canonicalize(root).unwrap();
    std::fs::read_dir(cache)
        .unwrap()
        .flatten()
        .map(|e| e.path())
        .find(|dir| std::fs::read_to_string(dir.join("root.path")).is_ok_and(|text| Path::new(&text) == wanted))
}

/// Pretends the interval has passed since the previous scan.
fn interval_passes(cache: &Path) {
    std::fs::write(cache.join(".swept"), "0").unwrap();
}

#[test]
fn a_vanished_root_dir_is_cleared_on_the_next_run_after_the_interval() {
    let cache = tempfile::TempDir::new().unwrap();
    let gone = repo();
    let live = repo();
    run(gone.path(), cache.path());
    let gone_dir = root_dir_of(cache.path(), gone.path()).expect("the root path is recorded in the root dir of the root that ran");
    assert!(gone_dir.join("hy-index.bin.d").is_dir(), "facts are placed in the root dir");
    let gone_path = gone.path().to_path_buf();
    drop(gone);
    assert!(!gone_path.exists());
    run(live.path(), cache.path());
    assert!(gone_dir.is_dir(), "a run within the interval (within the same hour) does not scan");
    interval_passes(cache.path());
    run(live.path(), cache.path());
    assert!(!gone_dir.exists(), "on the next run after the interval, the dir of a root that no longer exists is deleted");
}

#[test]
fn a_root_dir_in_use_stays() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept = repo();
    let other = repo();
    run(kept.path(), cache.path());
    let kept_dir = root_dir_of(cache.path(), kept.path()).expect("root dir");
    interval_passes(cache.path());
    run(other.path(), cache.path());
    assert!(kept_dir.join("hy-index.bin.d").is_dir(), "a dir whose root exists remains with its facts even after the scan");
}

#[test]
fn a_dir_without_a_record_stays() {
    let cache = tempfile::TempDir::new().unwrap();
    // A root dir made before this mechanism (no record of the root's path)
    let unrecorded = cache.path().join("00000000deadbeef");
    std::fs::create_dir_all(unrecorded.join("hy-index.bin.d")).unwrap();
    std::fs::write(unrecorded.join("hy-index.bin.d").join("00"), b"old").unwrap();
    let live = repo();
    interval_passes(cache.path());
    run(live.path(), cache.path());
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "the scan ran (the root dir of the root that ran is recorded)");
    assert!(unrecorded.join("hy-index.bin.d").join("00").is_file(), "a dir with no record remains with its contents");
}

// -- Overall cap of the store ---------------------------------------------
// Working-tree dirs whose roots continue to exist were not deleted by the cleanup of dirs whose roots vanished, and kept growing by the number of roots (62G on node-h). When the total exceeds the cap, dirs are deleted from the one with the oldest last-used
// time to bring it back within the cap. This run's root and dirs used within the interval are not deleted.

/// Runs the binary with root as the root, the store cache, and the overall cap (in bytes).
fn run_with_cap(root: &Path, cache: &Path, cap: u64) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        // Marker for re-entry into the machine's test slot: these tests look at store cleanup, so run them without taking the machine's slot (on a busy machine it waits several minutes).
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env("DOEFF_LINTER_CACHE_MAX_BYTES", cap.to_string())
        .output()
        .unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
}

/// Makes another root dir with an existing root, places a chunk of size bytes, and sets the last-used time to ago seconds before (the mtime of the `used` marker).
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
    // Three dirs whose roots continue to exist (none is deleted by the cleanup of vanished roots), oldest, older, recent in oldest-first order.
    let oldest = stale_root_dir(cache.path(), "00000000000000a1", kept_root.path(), 4000, 30 * 86400);
    let older = stale_root_dir(cache.path(), "00000000000000a2", kept_root.path(), 4000, 20 * 86400);
    let recent = stale_root_dir(cache.path(), "00000000000000a3", kept_root.path(), 4000, 10 * 86400);
    interval_passes(cache.path());
    // Cap = a little less than two chunks (this run's root dir counts too): the old two are deleted and the new one remains.
    run_with_cap(live.path(), cache.path(), 6000);
    assert!(!oldest.exists(), "when the cap is exceeded, deletion starts from the dir with the oldest last-used time");
    assert!(!older.exists(), "the next oldest dir is also deleted until back within the cap");
    assert!(recent.join("hy-index.bin.d").join("00").is_file(), "once back within the cap, newer dirs remain");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "this run's root dir remains");
}

#[test]
fn a_dir_used_within_the_interval_stays_even_over_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    // A root that a concurrent run is using (used within the interval) is not deleted even if the cap is exceeded.
    let busy = stale_root_dir(cache.path(), "00000000000000b1", kept_root.path(), 8000, 60);
    interval_passes(cache.path());
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(busy.join("hy-index.bin.d").join("00").is_file(), "a dir used within the interval remains even if the cap is exceeded");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "this run's root dir remains");
}

#[test]
fn within_the_cap_nothing_is_cleared_and_each_run_marks_its_root_used() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "00000000000000c1", kept_root.path(), 100, 30 * 86400);
    interval_passes(cache.path());
    run_with_cap(live.path(), cache.path(), 1_000_000);
    assert!(old.join("hy-index.bin.d").join("00").is_file(), "within the cap, a dir that is merely old is not deleted (nothing is deleted for age alone)");
    let live_dir = root_dir_of(cache.path(), live.path()).expect("root dir");
    assert!(live_dir.join("used").is_file(), "a run puts a used marker in its root dir");
}

// -- Turn for the cap cleanup ---------------------------------------
// The cap cleanup does not share its turn with `.swept`, the record of the cleanup of dirs whose roots vanished (which old versions of the linter without a cap also write). The finish time
// `.swept-cap` is written after the cleanup finishes, so a run that was cut off takes the turn but does not finish.

/// Places a file at path with a time ago seconds before (sets the marker's mtime).
fn stamped(path: &Path, text: &str, ago: u64) {
    std::fs::write(path, text).unwrap();
    let at = std::time::SystemTime::now() - std::time::Duration::from_secs(ago);
    std::fs::File::options().write(true).open(path).unwrap().set_modified(at).unwrap();
}

fn unix_now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs()
}

#[test]
fn an_old_linter_taking_the_vanished_root_turn_does_not_hold_back_the_cap_sweep() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "00000000000000d1", kept_root.path(), 8000, 30 * 86400);
    // An old version without a cap just took the turn for the cleanup of dirs whose roots vanished
    std::fs::write(cache.path().join(".swept"), unix_now().to_string()).unwrap();
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(!old.exists(), "even right after an old version took the `.swept` turn, the cap cleanup counts its turn by its own record and runs");
    assert!(cache.path().join(".swept-cap").is_file(), "the time the cap cleanup finished is recorded");
    assert!(!cache.path().join(".swept-cap.lock").exists(), "the in-progress marker of the cleanup is removed at the end");
}

#[test]
fn a_cap_sweep_cut_off_midway_leaves_no_turn_so_the_next_run_redoes_it() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "00000000000000e1", kept_root.path(), 8000, 30 * 86400);
    interval_passes(cache.path());
    // A cut-off run left only the in-progress marker (no record of a finish time): if the marker is old, the next run redoes it
    stamped(&cache.path().join(".swept-cap.lock"), "", 3600);
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(!old.exists(), "the old in-progress marker of a cut-off run does not stop the next run's cleanup");
}

#[test]
fn a_cap_sweep_in_progress_or_done_within_the_interval_is_not_repeated() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "00000000000000f1", kept_root.path(), 8000, 30 * 86400);
    interval_passes(cache.path());
    // A concurrent run just started the cleanup
    stamped(&cache.path().join(".swept-cap.lock"), "", 0);
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(old.exists(), "while the cleanup's in-progress marker is new, another run does not overlap the same cleanup");
    std::fs::remove_file(cache.path().join(".swept-cap.lock")).unwrap();
    // The cap cleanup just finished
    std::fs::write(cache.path().join(".swept-cap"), unix_now().to_string()).unwrap();
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(old.exists(), "within the interval (within the same hour), the cap cleanup is not repeated");
}

// -- Check the cap when growing (agora card: node-h's doeff-linter store was 7.9G; from 2026-10-10 14:34 node-h's disk fell below the new version's
// preparation lower bound) ----------------------------------------------------------------------------------
// The store as a whole grows when a new root dir is made (about 176MB per working tree). If the cap cleanup is checked only on a once-per-hour turn, working trees increase in between
// and it stays over the cap. A run that newly makes a root dir checks the cap on the spot regardless of the turn interval (no patrol cleanup is added;
// one point at write time). Only dirs used within 10 minutes are protected as roots in use (with 1 hour, nearly everything is protected when there is a lot of work, and nothing can be deleted).

#[test]
fn a_new_root_over_the_cap_clears_old_dirs_even_right_after_a_cap_sweep() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    let old = stale_root_dir(cache.path(), "0000000000000101", kept_root.path(), 8000, 30 * 86400);
    interval_passes(cache.path());
    // The cap cleanup just finished (within the turn interval), and a new root dir is added to it
    std::fs::write(cache.path().join(".swept-cap"), unix_now().to_string()).unwrap();
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(!old.exists(), "a run that makes a new root dir checks the cap and deletes the old dir even within the turn interval");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "this run's root dir remains");
}

#[test]
fn a_dir_last_used_more_than_ten_minutes_ago_goes_over_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    // A root used 20 minutes ago: only roots used within 10 minutes are protected as in use
    let idle = stale_root_dir(cache.path(), "0000000000000201", kept_root.path(), 8000, 20 * 60);
    let busy = stale_root_dir(cache.path(), "0000000000000202", kept_root.path(), 8000, 60);
    interval_passes(cache.path());
    run_with_cap(live.path(), cache.path(), 9000);
    assert!(!idle.exists(), "a dir used more than 10 minutes ago is deleted if the cap is exceeded");
    assert!(busy.join("hy-index.bin.d").join("00").is_file(), "a dir used within 10 minutes remains");
}

// -- The fact store `objects`, shared across working trees --------------------------
// Facts are one file each and are not deleted per root dir. When the total exceeds the cap, root dirs go first (oldest last-used time first), and if it is still
// exceeded, facts are deleted oldest last-used time (file mtime) first. Facts used within the interval and the store's used marker are not deleted.

/// Places one fact of size bytes in the fact store and sets its last-used time to ago seconds before.
fn stale_object(cache: &Path, name: &str, bytes: usize, ago: u64) -> PathBuf {
    let path = cache.join("objects").join("hy-index.bin").join("ab").join(name);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    stamped(&path, &"x".repeat(bytes), ago);
    path
}

#[test]
fn over_the_cap_the_least_recently_used_facts_go_first_until_within_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let live = repo();
    let oldest = stale_object(cache.path(), "00000000000000000000000000a1", 10_000, 30 * 86400);
    let older = stale_object(cache.path(), "00000000000000000000000000a2", 10_000, 20 * 86400);
    let recent = stale_object(cache.path(), "00000000000000000000000000a3", 10_000, 10 * 86400);
    run_with_cap(live.path(), cache.path(), 15_000);
    assert!(!oldest.exists(), "when the cap is exceeded, deletion starts from the fact with the oldest last-used time");
    assert!(!older.exists(), "the next oldest fact is also deleted until back within the cap");
    assert!(recent.is_file(), "once back within the cap, newer facts remain");
    assert!(cache.path().join("objects").join("used").is_file(), "the store's used marker is not deleted (a marker that keeps old versions of the linter from deleting the whole store)");
}

#[test]
fn a_root_dir_goes_before_any_fact() {
    let cache = tempfile::TempDir::new().unwrap();
    let kept_root = repo();
    let live = repo();
    // Root dirs (holding facts of the previous form) are deleted first in oldest-first order, and if that brings it back within the cap, the facts remain.
    let old_root = stale_root_dir(cache.path(), "00000000000000a1", kept_root.path(), 20_000, 30 * 86400);
    let fact = stale_object(cache.path(), "00000000000000000000000000b1", 10_000, 25 * 86400);
    run_with_cap(live.path(), cache.path(), 15_000);
    assert!(!old_root.exists(), "the root dir is deleted first");
    assert!(fact.is_file(), "if deleting the root dir brings it back within the cap, the fact remains");
}

#[test]
fn a_fact_used_within_ten_minutes_stays_even_over_the_cap() {
    let cache = tempfile::TempDir::new().unwrap();
    let live = repo();
    let busy = stale_object(cache.path(), "00000000000000000000000000c1", 10_000, 60);
    let idle = stale_object(cache.path(), "00000000000000000000000000c2", 10_000, 20 * 60);
    run_with_cap(live.path(), cache.path(), 1000);
    assert!(busy.is_file(), "a fact used within 10 minutes remains even if the cap is exceeded (a concurrent run is reading it)");
    assert!(!idle.exists(), "a fact used more than 10 minutes ago is deleted if the cap is exceeded");
}

#[test]
fn within_the_cap_no_fact_is_cleared() {
    let cache = tempfile::TempDir::new().unwrap();
    let live = repo();
    let old = stale_object(cache.path(), "00000000000000000000000000d1", 100, 30 * 86400);
    run_with_cap(live.path(), cache.path(), 1_000_000);
    assert!(old.is_file(), "within the cap, a fact that is merely old is not deleted (nothing is deleted for age alone)");
}

#[test]
fn a_temporary_fact_left_by_a_cut_off_run_is_cleared_once_stale() {
    let cache = tempfile::TempDir::new().unwrap();
    let live = repo();
    let dir = cache.path().join("objects").join("hy-index.bin").join("ab");
    std::fs::create_dir_all(&dir).unwrap();
    let stale = dir.join(".00000000000000000000000000e1.123.tmp");
    let fresh = dir.join(".00000000000000000000000000e2.124.tmp");
    stamped(&stale, "half", 2 * 3600);
    stamped(&fresh, "half", 60);
    run_with_cap(live.path(), cache.path(), 1_000_000);
    assert!(!stale.exists(), "a temporary file older than 1 hour (left by a cut-off run) is deleted by cleanup");
    assert!(fresh.is_file(), "a new temporary file that may be being written is not deleted");
}

// -- Cleanup that counts the fact store runs in a process outside the run ------------------------
// Cleanup that counts the fact store (about 400,000 facts on node-h) took 3-19 seconds and exceeded the deadline (4 seconds) of the hook right after a write. By default it hands the cleanup, still holding the in-progress
// marker, to another process, and the run does not wait. The process it was handed to finishes the cleanup, records the finish time, and removes the marker.

/// Runs the binary with root as the root, the store cache, and the overall cap (in bytes), with the default of handing off the cleanup (returns the instrument's stderr output).
fn run_handing_the_sweep(root: &Path, cache: &Path, cap: u64) -> String {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .env_remove("DOEFF_LINTER_SWEEP_INLINE")
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache)
        .env("DOEFF_LINTER_CACHE_MAX_BYTES", cap.to_string())
        .env("DOEFF_LINTER_TIMING", "1")
        .output()
        .unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn the_sweep_over_the_facts_is_handed_to_a_process_that_finishes_it_after_the_run() {
    let cache = tempfile::TempDir::new().unwrap();
    let live = repo();
    let idle = stale_object(cache.path(), "00000000000000000000000000f1", 10_000, 20 * 60);
    let stderr = run_handing_the_sweep(live.path(), cache.path(), 1000);
    assert!(stderr.contains("facts-cache.sweep handed=1"), "the run hands the cleanup to another process (it does not count by itself): {stderr}");
    // The process it was handed to runs outside the run, so wait until the finish time is recorded and the in-progress marker is removed (up to 60 seconds).
    let done = |cache: &Path| cache.join(".swept-cap").is_file() && !cache.join(".swept-cap.lock").exists();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    while !done(cache.path()) && std::time::Instant::now() < deadline {
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(done(cache.path()), "the process it was handed to records the time the cleanup finished and removes the in-progress marker");
    assert!(!idle.exists(), "the process it was handed to deletes the old facts that exceed the cap");
    assert!(root_dir_of(cache.path(), live.path()).is_some(), "this run's root dir remains");
}

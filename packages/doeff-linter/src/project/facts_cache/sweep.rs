//! Lifetime of the store (M-sweep) — cap, oldest first, vanished roots, temporary files. It does not know the shape of the store's contents (facts and tables).
//!
//! Knowledge it holds: the cap (default 4 GiB), the 10 minutes during which a root counts as in use and is protected, the interval between turns and the record file, the order of deleting root dirs first and then, if still over, deleting facts oldest first, and the
//! `objects` used-marker. It does not know the layout of facts; for facts it only deletes what `store::list` returns with `store::remove` (it cannot build a fact's path).
//! Deleting does not change answers (unreadable chunks and facts are rebuilt — see the parent's header note).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use super::store::{self, OBJECTS_DIR};

/// When the total of facts newly placed by one run exceeds this many bytes, check the cap regardless of the turn interval (the first
/// run after the linter is rebuilt re-places all facts — about 200MB).
const GROWTH_CHECK_BYTES: u64 = 32 * 1024 * 1024;

/// A record, inside the root dir, of the path of the root that created that dir.
const ROOT_RECORD: &str = "root.path";
/// A record, in the store, of the time (UNIX seconds) of the previous sweep scan.
const SWEEP_RECORD: &str = ".swept";
/// Interval between sweep scans (seconds) — the linter runs from a hook on every file write, so it does not scan every time.
const SWEEP_INTERVAL_SECONDS: u64 = 3600;

/// The store root and root dir of this run (needed when the cap sweep is called later — `note_object_bytes`).
static TENDED_AT: std::sync::OnceLock<(PathBuf, PathBuf)> = std::sync::OnceLock::new();

/// A root dir is decided by a hash of the root's path, so root dirs of vanished roots, such as worktrees and temporary copies for quality checks, stayed behind with nobody reading them
/// (4136 dirs and 21.6G on zeus). At a single point on the writing side, the root's path is recorded in the root dir, the store is scanned at spaced intervals,
/// and dirs whose recorded root no longer exists are deleted. Dirs with no record (created before this mechanism) are not deleted — whether they may be deleted is decided separately (#1472).
/// Nothing is deleted for time alone. Deleting does not change answers (unreadable chunks are rebuilt — see the header note). Done only once per run.
pub(super) fn tend_roots(base: &Path, root_dir: &Path, root: &Path) {
    static TENDED: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    TENDED.get_or_init(|| {
        // Whether this run newly creates a root dir (when the whole store grows — the root table is about 2MB. In the earlier form, one worktree was about 176MB). When it grows,
        // check the root-dir cap on the spot regardless of the turn interval of the cap sweep (sweep_over_cap_when_due).
        let grows = !root_dir.join(ROOT_RECORD).exists();
        record_root(root_dir, root);
        mark_used(root_dir);
        // Mark the facts store as used, the same as the root dir (an older linter that does not know the facts store deletes every dir directly under the store root as a root dir,
        // oldest last-used time first, so if the marker is old the whole facts store gets deleted).
        mark_used(&base.join(OBJECTS_DIR));
        let _ = TENDED_AT.set((base.to_path_buf(), root_dir.to_path_buf()));
        // On machines whose clock points before 1970 the interval cannot be measured, so do not scan
        if let Ok(now) = std::time::SystemTime::now().duration_since(UNIX_EPOCH) {
            sweep_vanished_roots(base, now.as_secs());
            let growth = if grows { Growth::Roots } else { Growth::None };
            sweep_over_cap_when_due(base, root_dir, cache_cap_bytes(), now.as_secs(), growth);
        }
    });
}

/// Counts the bytes of facts newly placed by this run, and once, when the total exceeds GROWTH_CHECK_BYTES, checks the cap including the facts store.
pub(super) fn note_object_bytes(written: u64) {
    static TOTAL: AtomicU64 = AtomicU64::new(0);
    static CHECKED: AtomicBool = AtomicBool::new(false);
    if written == 0 || TOTAL.fetch_add(written, Ordering::Relaxed) + written < GROWTH_CHECK_BYTES || CHECKED.swap(true, Ordering::Relaxed) {
        return;
    }
    let (Some((base, root_dir)), Ok(now)) = (TENDED_AT.get(), SystemTime::now().duration_since(UNIX_EPOCH)) else { return };
    sweep_over_cap_when_due(base, root_dir, cache_cap_bytes(), now.as_secs(), Growth::Objects);
}

/// The marker of the last-used time of a root dir (empty contents — the file's modification time is the marker). It decides the oldest-first order when the cap is exceeded.
const USED_RECORD: &str = "used";
/// Environment variable that changes the cap on the whole store (bytes).
const CAP_ENV: &str = "DOEFF_LINTER_CACHE_MAX_BYTES";
/// Default cap on the whole store (4 GiB) — in the earlier form one worktree's root dir was about 176MB (measured on zeus 2026-10-10 — most of it the repo-wide Hy index
/// hy-index.bin.d at 156MB), allowing about 23 roots. With the earlier default of 10 GiB (estimating 65MB per root), 7.9G piled up on zeus and the worker's
/// disk fell below the lower bound for preparing a new version. Deleted roots are rebuilt on the next run (answers do not change).
/// To revert = revert this value (it can also be changed per machine with the environment variable CAP_ENV).
const DEFAULT_CAP_BYTES: u64 = 4 * 1024 * 1024 * 1024;
/// Length (seconds) of the "roots in use" that are not deleted even above the cap — dirs used within these seconds (roots used by concurrently running runs). Before, this was the same 1 hour
/// as the turn interval, and when work was heavy nearly every root was protected and the store could not be brought back under the cap.
const IN_USE_SECONDS: u64 = 600;

/// Marks that this run used the root dir (once per run). The result dir of the commit hook's HEAD tree also marks with this on every
/// read and write.
pub fn mark_used(root_dir: &Path) {
    if std::fs::create_dir_all(root_dir).is_ok() {
        let _ = std::fs::write(root_dir.join(USED_RECORD), b"");
    }
}

/// The cap on the whole store (the default if the environment variable has an unreadable value).
fn cache_cap_bytes() -> u64 {
    std::env::var(CAP_ENV).ok().and_then(|text| text.trim().parse::<u64>().ok()).unwrap_or(DEFAULT_CAP_BYTES)
}

/// Total size (bytes) of the files in a root dir (directly under the root dir and the `<kind>.<shape>.d/` chunks).
fn dir_bytes(dir: &Path) -> u64 {
    let Ok(entries) = std::fs::read_dir(dir) else { return 0 };
    entries
        .flatten()
        .map(|entry| match entry.metadata() {
            Ok(meta) if meta.is_dir() => dir_bytes(&entry.path()),
            Ok(meta) => meta.len(),
            Err(_) => 0,
        })
        .sum()
}

/// The last-used time (UNIX seconds) of a root dir — the modification time of the `used` marker. For a dir without a marker (created before this mechanism), the dir's modification time.
fn last_used(dir: &Path) -> u64 {
    std::fs::metadata(dir.join(USED_RECORD))
        .or_else(|_| std::fs::metadata(dir))
        .and_then(|meta| meta.modified())
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs())
}

/// A record of the time (UNIX seconds) the cap sweep last finished. Kept separate from `.swept`, the record of the sweep of dirs whose root vanished — `.swept` is also written by
/// older linters that lack the cap sweep, so if they shared a turn, the cap sweep would not run while an older version held the turn (on zeus's 2026-10-02 12:50
/// scan it stayed at 63G with 0 deleted).
const CAP_SWEEP_RECORD: &str = ".swept-cap";
/// A marker (empty contents) while a cap sweep is in progress. It keeps concurrently running runs from overlapping the same sweep. A marker left by an aborted run is treated as absent once these seconds pass.
const CAP_SWEEP_LOCK: &str = ".swept-cap.lock";
const CAP_SWEEP_LOCK_STALE_SECONDS: u64 = 600;

/// How the growth of the store is signaled (sweep_over_cap_when_due).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Growth {
    /// Not grown — sweep only when the turn interval has passed.
    None,
    /// This run newly created a root dir — check the cap with the root dirs alone, regardless of the turn interval (the facts store is not walked — counting facts one by one
    /// every time a worktree is added is heavy).
    Roots,
    /// This run placed many new facts (after the linter is rebuilt) — check including the facts store, regardless of the turn interval.
    Objects,
}

/// Runs the cap sweep if the interval has passed since the last cap sweep finished, or if the whole store has grown (growth — see the header note).
/// The finish time is written after the sweep including the facts store has finished — a run aborted by, e.g., the commit hook's seconds limit writes no record, so
/// the next run redoes it (this avoids a shape that only takes the turn and ends). If we did not check on growth, the store would stay over the cap by however much the worktrees grew within the turn interval
/// (zeus's 7.9G). While a concurrently running run is sweeping, do not overlap even on growth.
fn sweep_over_cap_when_due(base: &Path, root_dir: &Path, cap: u64, now: u64, growth: Growth) {
    let stamp = base.join(CAP_SWEEP_RECORD);
    let last = std::fs::read_to_string(&stamp).ok().and_then(|text| text.trim().parse::<u64>().ok());
    let due = !last.is_some_and(|last| now.saturating_sub(last) < SWEEP_INTERVAL_SECONDS);
    if !due && growth == Growth::None {
        return;
    }
    let lock = base.join(CAP_SWEEP_LOCK);
    let held_by_other = std::fs::metadata(&lock)
        .and_then(|meta| meta.modified())
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .is_some_and(|at| now.saturating_sub(at.as_secs()) < CAP_SWEEP_LOCK_STALE_SECONDS);
    if held_by_other || std::fs::create_dir_all(base).is_err() || std::fs::write(&lock, b"").is_err() {
        return;
    }
    let with_objects = due || growth == Growth::Objects;
    // A sweep that counts the facts store (on zeus about 400k facts, 3 to 19 seconds) does not wait within the deadline right after a write (4 seconds) of the hook — hand it to another process
    // while it holds the in-progress marker, and the process it was handed to removes the marker at the end. If it cannot be handed over, sweep in this run.
    if with_objects && std::env::var_os(SWEEP_INLINE_ENV).is_none() && hand_off(base, root_dir, cap, now) {
        crate::timing::count("facts-cache.sweep", "handed", 1);
        return;
    }
    finish_sweep(base, root_dir, cap, now, with_objects);
}

/// Runs the cap sweep and, if it counted up to the facts store, records the finish time and removes the in-progress marker (the same steps within this run and in a handed-over process).
fn finish_sweep(base: &Path, root_dir: &Path, cap: u64, now: u64, with_objects: bool) {
    sweep_over_cap(base, root_dir, cap, now, with_objects);
    if with_objects {
        let _ = std::fs::write(base.join(CAP_SWEEP_RECORD), now.to_string());
    }
    let _ = std::fs::remove_file(base.join(CAP_SWEEP_LOCK));
}

/// Environment variable (if present) that makes the sweep run inside the run — so that a test can see the sweep's result right after the run. The default hands it to another process and does not wait.
pub const SWEEP_INLINE_ENV: &str = "DOEFF_LINTER_SWEEP_INLINE";
/// The command entry of the linter itself that runs a handed-over sweep (`doeff-linter sweep-facts-cache <store> <root dir> <cap> <time>` — main recognizes it).
pub const SWEEP_COMMAND: &str = "sweep-facts-cache";

/// Hands a sweep that counts up to the facts store to a separate process that does not wait (the linter's own `SWEEP_COMMAND`). The handed-over process is in a new session and
/// has no standard input or output — it does not stop when the hook cuts off its own process group, and it does not make the hook wait for the end of its output. Returns true if handed over.
fn hand_off(base: &Path, root_dir: &Path, cap: u64, now: u64) -> bool {
    let Ok(exe) = std::env::current_exe() else { return false };
    let mut command = std::process::Command::new(exe);
    command
        .arg(SWEEP_COMMAND)
        .arg(base)
        .arg(root_dir)
        .arg(cap.to_string())
        .arg(now.to_string())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: the only thing called in the child after fork and before exec is setsid (async-signal-safe, touches no memory).
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
    }
    command.spawn().is_ok()
}

/// The body of `SWEEP_COMMAND` — finishes at low priority the sweep that the handing-over run passed while holding the in-progress marker, and removes the marker. Returns 2 if the arguments cannot be read.
pub fn run_handed_sweep(args: &[String]) -> std::process::ExitCode {
    let [base, root_dir, cap, now] = args else { return std::process::ExitCode::from(2) };
    let (Ok(cap), Ok(now)) = (cap.parse::<u64>(), now.parse::<u64>()) else { return std::process::ExitCode::from(2) };
    // SAFETY: nice only changes the priority of its own process (touches no memory). The sweep is the same even if it cannot be lowered.
    #[cfg(unix)]
    unsafe {
        libc::nice(10);
    }
    finish_sweep(Path::new(base), Path::new(root_dir), cap, now, true);
    std::process::ExitCode::SUCCESS
}

/// If the whole store exceeds the cap `cap`, deletes from the dirs with the oldest last-used time to bring it back under the cap, and if still over, deletes facts
/// oldest last-used first. Worktree dirs whose root keeps existing were not deleted by sweep_vanished_roots and kept growing
/// as many as there were roots (62G on zeus, about 4G per hour). The root dir of this run, and dirs and facts used within IN_USE_SECONDS (roots used by
/// concurrently running runs) are not deleted — even if the cap is briefly exceeded, roots in use are not deleted and forced to be rebuilt. Deleting does not change answers (unreadable chunks and facts are rebuilt —
/// see the header note). If `with_objects` is false, the facts store is not counted (checked with root dirs alone — the store's facts are numerous and heavy to walk).
fn sweep_over_cap(base: &Path, root_dir: &Path, cap: u64, now: u64, with_objects: bool) {
    let Ok(entries) = std::fs::read_dir(base) else { return };
    let mut dirs: Vec<(u64, u64, PathBuf)> = entries
        .flatten()
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()) && entry.file_name() != OBJECTS_DIR)
        .map(|entry| {
            let dir = entry.path();
            (last_used(&dir), dir_bytes(&dir), dir)
        })
        .collect();
    let mut objects = if with_objects { store::list(&base.join(OBJECTS_DIR), now) } else { Vec::new() };
    let mut total: u64 = dirs.iter().map(|(_, bytes, _)| bytes).sum::<u64>() + objects.iter().map(|object| object.bytes).sum::<u64>();
    if total <= cap {
        return;
    }
    dirs.sort_by_key(|(used, _, _)| *used);
    for (used, bytes, dir) in dirs {
        if total <= cap {
            break;
        }
        if dir == root_dir || now.saturating_sub(used) < IN_USE_SECONDS {
            continue;
        }
        // If it cannot be deleted, try again on the next scan (it does not affect answers)
        if std::fs::remove_dir_all(&dir).is_ok() {
            total = total.saturating_sub(bytes);
        }
    }
    // Facts in oldest last-used order. Facts within IN_USE_SECONDS of being placed (or of being read and refreshed) are protected. The modification time of a read fact is
    // refreshed only at the precision of store's OBJECT_TOUCH_SECONDS (1 day), so it does not protect "facts being read" (if unreadable, they are rebuilt).
    objects.sort_by_key(|object| object.used);
    for object in objects {
        if total <= cap {
            break;
        }
        if now.saturating_sub(object.used) < IN_USE_SECONDS {
            continue;
        }
        let bytes = object.bytes;
        if store::remove(object) {
            total = total.saturating_sub(bytes);
        }
    }
}

/// Records the absolute path of the root in the root dir (does not write if the same record already exists).
fn record_root(root_dir: &Path, root: &Path) {
    let Ok(absolute) = std::fs::canonicalize(root) else { return };
    let Some(text) = absolute.to_str() else { return };
    let record = root_dir.join(ROOT_RECORD);
    if std::fs::read_to_string(&record).is_ok_and(|known| known == text) {
        return;
    }
    if std::fs::create_dir_all(root_dir).is_err() {
        return;
    }
    let tmp = root_dir.join(format!(".{ROOT_RECORD}.{}.tmp", std::process::id()));
    if std::fs::write(&tmp, text).is_ok() && std::fs::rename(&tmp, &record).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// If the interval has passed since the previous scan, deletes the store's root dirs whose recorded root path is absent (NotFound). Dirs with no record, dirs whose record
/// cannot be read, and dirs whose root's existence cannot be checked are kept. The time record is rewritten first, so concurrently running runs do not scan again within the same interval.
/// The cap sweep counts its turn with a separate record (sweep_over_cap_when_due).
fn sweep_vanished_roots(base: &Path, now: u64) {
    let stamp = base.join(SWEEP_RECORD);
    let last = std::fs::read_to_string(&stamp).ok().and_then(|text| text.trim().parse::<u64>().ok());
    if last.is_some_and(|last| now.saturating_sub(last) < SWEEP_INTERVAL_SECONDS) {
        return;
    }
    if std::fs::create_dir_all(base).is_err() || std::fs::write(&stamp, now.to_string()).is_err() {
        return;
    }
    let Ok(entries) = std::fs::read_dir(base) else { return };
    for entry in entries.flatten() {
        let dir = entry.path();
        let Ok(recorded) = std::fs::read_to_string(dir.join(ROOT_RECORD)) else { continue };
        let vanished = matches!(std::fs::symlink_metadata(&recorded), Err(e) if e.kind() == std::io::ErrorKind::NotFound);
        if vanished {
            // If it cannot be deleted, try again on the next scan (it does not affect answers)
            let _ = std::fs::remove_dir_all(&dir);
        }
    }
}

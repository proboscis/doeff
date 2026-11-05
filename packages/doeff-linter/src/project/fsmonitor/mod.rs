//! A resident process remembers, via inotify, the worktree's "files that differ from git's index" and the repo-walk listing, and answers queries (the shape of git's fsmonitor).
//!
//! A single-file run (hook, the editor's `--stdin`) is a separate process each time, so even when warm it redid, every time, the stat of all the repo's Hy files (about 4600) and the reads of about 2000 dirs
//! (where `GitIndex::blob_of` checks whether a file equals the index, and the walk of `hy_files`). One resident process per machine
//! (`doeff-linter fsmonitor-serve`) does the walk and stat once per worktree, and afterwards updates only from inotify notifications. A single-file run
//! reads the index itself (once) and asks the resident process for "index rows that differ from the current file, entries the walk finds that are not in the index, and entries in the index that the walk does not find".
//!
//! Safeguards (each only falls back to the same answer as when "there is no resident process" — the answer does not change, it only gets slower):
//! - Do not use it when the resident process is absent, the answer does not arrive within 300 milliseconds, the index it answered with differs from the one we read (the identity = modification time, size or inode differs),
//!   the worktree cannot be walked (unreadable dir, watch limit, a non-UTF-8 name), or on an fs where inotify does not work, such as NFS.
//! - Before answering a query, read the kernel's notification queue until it is empty. inotify notifications are queued inside the write system call, so writes finished before the hook
//!   are always in the answer (unlike a remote fs that needs a watchman cookie, on an fs of the same machine there is nothing to wait for).
//! - When the notification queue overflows, the worktree root moves, or a dir is added or removed, rebuild the worktree (for a dir added or removed, only under that dir).
//!
//! The user side receives the listing with `session(root)`, gets a file's blob id (without stat) with `Session::clean_oid`, and gets the walk result with `Session::listing`.
//! It is enabled only by a run that calls `enable()` (a single-file run with `--stdin`). It is not used if `DOEFF_LINTER_NO_FSMONITOR` is set.
//! The resident process watches the worktree with inotify (a Linux-only interface), so outside Linux (macOS machines) it is not built and `enable()` has no effect —
//! a single-file run stats and walks by itself, as when there is no resident process (the answer is the same).

#[cfg(target_os = "linux")]
pub mod daemon;
#[cfg(not(target_os = "linux"))]
#[path = "unsupported.rs"]
pub mod daemon;

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, SystemTime};

use serde::{Deserialize, Serialize};

use super::git_index::{GitIndex, Ident};
use super::hy_files::{walks_to, WalkRules};

/// Name of the subcommand that starts the resident process.
pub const SERVE_COMMAND: &str = "fsmonitor-serve";
/// Environment variable for not using it.
const OFF_ENV: &str = "DOEFF_LINTER_NO_FSMONITOR";
/// Environment variable that changes the resident process's location (socket, lock) (for tests).
pub const DIR_ENV: &str = "DOEFF_LINTER_FSMONITOR_DIR";
/// Version of the answer's shape. Raise it when the meaning (what is regarded as "the same") changes — an old resident process keeps answering on the old version's socket, and a new run asks the new version's socket.
pub const PROTOCOL: u32 = 1;
/// Wait for a query. If exceeded, walk by ourselves without using the resident process (the first query of a worktree waits while the resident process walks the worktree to build it).
const QUERY_TIMEOUT: Duration = Duration::from_millis(300);
/// Lower bound of the interval for trying to start the resident process (on a machine where it cannot be started, do not try to start a process every time).
const RESPAWN_EVERY: Duration = Duration::from_secs(60);

/// One query.
#[derive(Debug, Serialize, Deserialize)]
pub struct Request {
    pub v: u32,
    pub root: String,
}

/// The answer to a query. If `ok` is false the other fields are empty, and the caller walks by itself.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Answer {
    pub ok: bool,
    /// Identity of the index file the resident process is reading.
    pub ident: Ident,
    /// Among the index's regular-file rows, those for which the current file cannot be said to equal the row (stat differs, missing, racy, or under a dir that cannot be watched).
    pub dirty: Vec<String>,
    /// Files the walk picks up (regular files with a Hy or Python extension) but that have no regular-file row in the index.
    pub extras: Vec<String>,
    /// Files that have a regular-file row in the index and a Hy or Python extension but that the walk does not pick up (deleted, turned into a symlink, under a symlinked dir).
    pub missing: Vec<String>,
}

static ENABLED: AtomicBool = AtomicBool::new(false);

/// Allows this run to query the resident process (called only by a single-file run with `--stdin`).
pub fn enable() {
    if cfg!(target_os = "linux") && std::env::var_os(OFF_ENV).is_none() {
        ENABLED.store(true, Ordering::Relaxed);
    }
}

/// Location of the resident process. One per user of the machine (do not query another user's socket).
pub fn state_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os(DIR_ENV) {
        return PathBuf::from(dir);
    }
    // SAFETY: getuid takes no arguments and touches no memory.
    let uid = unsafe { libc::getuid() };
    PathBuf::from(format!("/tmp/doeff-linter-fsmonitor-{uid}"))
}

pub fn socket_path(dir: &Path) -> PathBuf {
    dir.join(format!("v{PROTOCOL}.sock"))
}

pub fn lock_path(dir: &Path) -> PathBuf {
    dir.join(format!("v{PROTOCOL}.lock"))
}

/// Asks the resident process for the listing of `root`. None if it is absent, does not answer, or its answer cannot be read. If absent, starts it (after an interval).
pub fn ask(dir: &Path, root: &Path) -> Option<Answer> {
    let stream = match UnixStream::connect(socket_path(dir)) {
        Ok(stream) => stream,
        Err(_) => {
            start_daemon(dir);
            return None;
        }
    };
    stream.set_read_timeout(Some(QUERY_TIMEOUT)).ok()?;
    stream.set_write_timeout(Some(QUERY_TIMEOUT)).ok()?;
    let request = serde_json::to_string(&Request { v: PROTOCOL, root: root.to_str()?.to_string() }).ok()?;
    (&stream).write_all(format!("{request}\n").as_bytes()).ok()?;
    let mut line = String::new();
    BufReader::new(&stream).read_line(&mut line).ok()?;
    serde_json::from_str(&line).ok()
}

/// Starts the resident process as a separate process that is not waited for (a new session, no standard I/O — it does not stop even if the hook kills its own process group, and
/// does not make the hook's output wait for its end). If someone holds the lock (just started, or running but without a socket yet), it is not started.
fn start_daemon(dir: &Path) {
    use std::os::unix::fs::DirBuilderExt;
    use std::os::unix::process::CommandExt;
    if std::fs::DirBuilder::new().mode(0o700).recursive(true).create(dir).is_err() {
        return;
    }
    let tried = dir.join(format!("v{PROTOCOL}.spawned"));
    let recent = std::fs::metadata(&tried).and_then(|meta| meta.modified()).ok().and_then(|at| SystemTime::now().duration_since(at).ok()).is_some_and(|age| age < RESPAWN_EVERY);
    if recent || std::fs::write(&tried, b"").is_err() || daemon::lock_is_held(&lock_path(dir)) {
        return;
    }
    let Ok(exe) = std::env::current_exe() else { return };
    let mut command = std::process::Command::new(exe);
    command.arg(SERVE_COMMAND).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    // SAFETY: only setsid is called in the child after fork and before exec (async-signal-safe, touches no memory).
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let _ = command.spawn();
}

/// The listing of one worktree, as answered by the resident process.
pub struct Session {
    root: PathBuf,
    index: Arc<GitIndex>,
    dirty: HashSet<String>,
    extras: Vec<String>,
    missing: HashSet<String>,
}

type Sessions = Mutex<HashMap<PathBuf, Option<Arc<Session>>>>;
static SESSIONS: OnceLock<Sessions> = OnceLock::new();

/// The listing of `root` (asked only once in this run). None if not enabled, the resident process is unusable, or the index does not match (the caller stats and walks by itself).
pub fn session(root: &Path) -> Option<Arc<Session>> {
    if !ENABLED.load(Ordering::Relaxed) {
        return None;
    }
    let memo = SESSIONS.get_or_init(Default::default);
    let mut memo = memo.lock().unwrap_or_else(PoisonError::into_inner);
    memo.entry(root.to_path_buf()).or_insert_with(|| crate::timing::timed("fsmonitor.query", || open(&state_dir(), root))).clone()
}

/// Asks the resident process and builds the listing (the body of `session`). Only when the index we read and the index the resident process sees are the same.
pub fn open(dir: &Path, root: &Path) -> Option<Arc<Session>> {
    let index = GitIndex::of(root)?;
    let answer = ask(dir, root)?;
    if !answer.ok || answer.ident != index.ident() {
        return None;
    }
    Some(Arc::new(Session {
        root: root.to_path_buf(),
        index,
        dirty: answer.dirty.into_iter().collect(),
        extras: answer.extras,
        missing: answer.missing.into_iter().collect(),
    }))
}

impl Session {
    /// If the file at `rel` (a path from the root) equals the index's regular-file row, its blob id — without stat.
    pub fn clean_oid(&self, rel: &str) -> Option<[u8; 20]> {
        if self.dirty.contains(rel) {
            return None;
        }
        self.index.regular_oid(rel)
    }

    /// Paths of files that the walk of `rules` picks up (the same set and order as `hy_files::walk_reading_with` walks and lines up — ordered per path component).
    pub fn listing(&self, rules: &WalkRules) -> Vec<PathBuf> {
        let tracked = self.index.files().map(|(rel, _)| rel).filter(|rel| walks_to(rules, rel) && !self.missing.contains(*rel));
        let extras = self.extras.iter().map(String::as_str).filter(|rel| walks_to(rules, rel));
        let mut found: Vec<PathBuf> = tracked.chain(extras).map(|rel| self.root.join(rel)).collect();
        found.sort();
        found
    }
}

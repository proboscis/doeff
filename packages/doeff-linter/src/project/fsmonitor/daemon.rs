//! The body of the resident process (`doeff-linter fsmonitor-serve`) — per working tree, it remembers the walk and the "files that differ from the index", updates them on inotify notifications, and answers queries.
//!
//! What is remembered for one working tree (`Root`):
//! - `dirs`: the dirs being watched (paths from the root; only dirs that are descended into — dirs named in `hy_index::SKIPPED_DIRS` are not descended into) and their watches.
//! - `candidates`: regular files with the extensions the walk picks up (Hy, Python) (symlinks are not picked up — same as the walk).
//! - `dirty`: among the index's regular-file rows, those for which the current file cannot be said to be the same as the row (`GitIndex::blob_of`). Rows under dirs that cannot be watched are always dirty.
//! - `index`: the index that was read. It watches `.git` (the gitdir for a worktree); when `index` is replaced, it re-reads it and redoes all of `dirty`.
//!
//! Handling notifications: a file notification re-examines only that path. When a dir is added or removed, only the part under that dir is rebuilt. On queue overflow, the root moving, or a dir that cannot be watched,
//! the working tree is rebuilt (while a dir that cannot be watched exists, queries are answered "unavailable" — the caller walks by itself). Before answering a query, the notification queue is read until it is empty.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::ffi::CString;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use doeff_indexer::hy_index;

use super::{lock_path, socket_path, state_dir, Answer, Request, PROTOCOL};
use crate::project::git_index::{self, GitIndex};

/// Watches of a working tree's dirs.
const DIR_MASK: u32 = libc::IN_CREATE
    | libc::IN_DELETE
    | libc::IN_MODIFY
    | libc::IN_ATTRIB
    | libc::IN_CLOSE_WRITE
    | libc::IN_MOVED_FROM
    | libc::IN_MOVED_TO
    | libc::IN_DELETE_SELF
    | libc::IN_MOVE_SELF
    | libc::IN_ONLYDIR;
/// Watch of the gitdir (only watches for `index` being replaced).
const GIT_MASK: u32 = libc::IN_CREATE | libc::IN_MOVED_TO | libc::IN_CLOSE_WRITE | libc::IN_DELETE | libc::IN_DELETE_SELF | libc::IN_MOVE_SELF | libc::IN_ONLYDIR;
/// Time until a working tree that never receives a query is released.
const ROOT_IDLE: Duration = Duration::from_secs(20 * 60);
/// Time until a resident process that never receives a query exits.
const DAEMON_IDLE: Duration = Duration::from_secs(60 * 60);
/// Interval before rebuilding a working tree that could not be built (cannot be watched, or the index cannot be trusted).
const REBUILD_AFTER: Duration = Duration::from_secs(5);
/// The `statfs` types of filesystems where inotify does not work (NFS, SMB, CIFS, FUSE, 9p).
const UNWATCHABLE_FS: [i64; 5] = [0x6969, 0x517B, 0xFF53_4D42, 0x6573_5546, 0x0102_1997];

#[derive(Clone, PartialEq, Eq)]
enum Owner {
    Dir { root: PathBuf, rel: String },
    Git { root: PathBuf },
}

/// The inotify handle and the owner of each watch (two working trees can watch the same dir — when one working tree is inside another).
struct Watcher {
    fd: i32,
    owners: HashMap<i32, Vec<Owner>>,
}

impl Watcher {
    fn new() -> Option<Watcher> {
        // SAFETY: inotify_init1 takes only flags and returns an fd.
        let fd = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        (fd >= 0).then_some(Watcher { fd, owners: HashMap::new() })
    }

    fn add(&mut self, path: &Path, mask: u32, owner: Owner) -> Option<i32> {
        let c_path = CString::new(path.as_os_str().as_bytes()).ok()?;
        // SAFETY: c_path is a valid NUL-terminated string, used only for the duration of the call.
        let wd = unsafe { libc::inotify_add_watch(self.fd, c_path.as_ptr(), mask) };
        if wd < 0 {
            return None;
        }
        let owners = self.owners.entry(wd).or_default();
        if !owners.contains(&owner) {
            owners.push(owner);
        }
        Some(wd)
    }

    /// Release the watch `wd` held by `owner`. If no owner remains, remove the watch.
    fn release(&mut self, wd: i32, owner: &Owner) {
        let Some(owners) = self.owners.get_mut(&wd) else { return };
        owners.retain(|o| o != owner);
        if owners.is_empty() {
            self.owners.remove(&wd);
            // SAFETY: fd and wd are taken as plain numbers (a detached wd just returns EINVAL).
            unsafe { libc::inotify_rm_watch(self.fd, wd) };
        }
    }

    /// Release all watches held by working tree `root`.
    fn release_root(&mut self, root: &Path) {
        let mine = |owner: &Owner| match owner {
            Owner::Dir { root: r, .. } | Owner::Git { root: r } => r == root,
        };
        let wds: Vec<i32> = self.owners.iter().filter(|(_, owners)| owners.iter().any(mine)).map(|(wd, _)| *wd).collect();
        for wd in wds {
            if let Some(owners) = self.owners.get_mut(&wd) {
                owners.retain(|o| !mine(o));
                if owners.is_empty() {
                    self.owners.remove(&wd);
                    // SAFETY: same as `release`.
                    unsafe { libc::inotify_rm_watch(self.fd, wd) };
                }
            }
        }
    }
}

/// What is remembered for one working tree.
struct Root {
    path: PathBuf,
    index: GitIndex,
    dirs: HashMap<String, i32>,
    candidates: BTreeSet<String>,
    dirty: HashSet<String>,
    git_wd: i32,
    degraded: bool,
    built_at: Instant,
    last_query: Instant,
}

fn is_candidate_name(name: &str) -> bool {
    Path::new(name).extension().and_then(|e| e.to_str()).is_some_and(|e| hy_index::HY_EXTENSIONS.contains(&e) || e == "py")
}

fn parent_of(rel: &str) -> &str {
    rel.rsplit_once('/').map_or("", |(dir, _)| dir)
}

fn join(rel: &str, name: &str) -> String {
    if rel.is_empty() {
        name.to_string()
    } else {
        format!("{rel}/{name}")
    }
}

fn descends(name: &str) -> bool {
    !hy_index::SKIPPED_DIRS.contains(&name)
}

/// Whether the walk could pick up the file at path `rel` from the root (the extension is Hy or Python, and all intermediate dirs are ones that are descended into).
fn reachable(rel: &str) -> bool {
    let (dirs, name) = rel.rsplit_once('/').unwrap_or(("", rel));
    is_candidate_name(name) && dirs.split('/').filter(|d| !d.is_empty()).all(descends)
}

fn fs_watchable(path: &Path) -> bool {
    let Ok(c_path) = CString::new(path.as_os_str().as_bytes()) else { return false };
    let mut stat = std::mem::MaybeUninit::<libc::statfs>::uninit();
    // SAFETY: c_path is a valid C string, and stat is a write destination of one statfs. It is read only on success.
    let done = unsafe { libc::statfs(c_path.as_ptr(), stat.as_mut_ptr()) } == 0;
    // SAFETY: when statfs returns 0, stat is filled in.
    !done || !UNWATCHABLE_FS.contains(&(unsafe { stat.assume_init() }.f_type as i64))
}

/// Whether the file `rel` can be said to be the same as the index's regular-file row (within a watchable dir, a regular file, and stat equals the row).
fn is_clean(root: &Root, rel: &str) -> bool {
    root.dirs.contains_key(parent_of(rel))
        && std::fs::symlink_metadata(root.path.join(rel)).is_ok_and(|meta| meta.file_type().is_file() && root.index.blob_of(rel, &meta).is_some())
}

fn judge_all(root: &mut Root) {
    let dirty: HashSet<String> = root.index.files().map(|(rel, _)| rel).filter(|rel| !is_clean(root, rel)).map(str::to_string).collect();
    root.dirty = dirty;
}

/// Walk under dir `rel`, watch the dirs, and remember the files to pick up.
fn scan_subtree(watcher: &mut Watcher, root: &mut Root, rel: &str) {
    let mut stack = vec![rel.to_string()];
    while let Some(rel) = stack.pop() {
        let dir = if rel.is_empty() { root.path.clone() } else { root.path.join(&rel) };
        let owner = Owner::Dir { root: root.path.clone(), rel: rel.clone() };
        let Some(wd) = watcher.add(&dir, DIR_MASK, owner) else {
            // A dir that vanished during the walk is handled by the removal notification. If a dir that cannot be watched (permissions, limits) exists, the walk's listing is not trusted.
            root.degraded |= dir.is_dir();
            continue;
        };
        root.dirs.insert(rel.clone(), wd);
        let Ok(read) = std::fs::read_dir(&dir) else {
            root.degraded = true;
            continue;
        };
        for entry in read.filter_map(Result::ok) {
            let Ok(kind) = entry.file_type() else { continue };
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                root.degraded = true;
                continue;
            };
            if kind.is_dir() {
                if descends(name) {
                    stack.push(join(&rel, name));
                }
            } else if kind.is_file() && is_candidate_name(name) {
                root.candidates.insert(join(&rel, name));
            }
        }
    }
}

/// Build the memory for working tree `path`. None if it cannot be built (not a git working tree, the index cannot be trusted, or a filesystem where inotify does not work).
fn build(watcher: &mut Watcher, path: &Path) -> Option<Root> {
    if !path.is_dir() || !fs_watchable(path) {
        return None;
    }
    // Watch the gitdir first and then read the index (a replacement during the read is picked up by a notification).
    let gitdir = git_index::index_path(path)?.parent()?.to_path_buf();
    let git_wd = watcher.add(&gitdir, GIT_MASK, Owner::Git { root: path.to_path_buf() })?;
    let Some(index) = GitIndex::read(path) else {
        watcher.release_root(path);
        return None;
    };
    let mut root = Root {
        path: path.to_path_buf(),
        index,
        dirs: HashMap::new(),
        candidates: BTreeSet::new(),
        dirty: HashSet::new(),
        git_wd,
        degraded: false,
        built_at: Instant::now(),
        last_query: Instant::now(),
    };
    scan_subtree(watcher, &mut root, "");
    judge_all(&mut root);
    Some(root)
}

/// The work for one working tree, collected from notifications.
#[derive(Default)]
struct Pending {
    files: HashSet<String>,
    added: BTreeSet<String>,
    removed: BTreeSet<String>,
    reload: bool,
    rebuild: bool,
}

/// Release the memory under dir `rel` (watches, picked-up files, and the index rows' "same").
fn remove_subtree(watcher: &mut Watcher, root: &mut Root, rel: &str) {
    let prefix = format!("{rel}/");
    let doomed: Vec<String> = root.dirs.keys().filter(|dir| dir.as_str() == rel || dir.starts_with(&prefix)).cloned().collect();
    for dir in doomed {
        if let Some(wd) = root.dirs.remove(&dir) {
            watcher.release(wd, &Owner::Dir { root: root.path.clone(), rel: dir });
        }
    }
    let gone: Vec<String> = root.candidates.range(prefix.clone()..).take_while(|c| c.starts_with(&prefix)).cloned().collect();
    for path in gone {
        root.candidates.remove(&path);
    }
    let tracked: Vec<String> = root.index.paths_under(&prefix).map(str::to_string).collect();
    root.dirty.extend(tracked);
}

/// Walk again under the added dir `rel` and re-examine the index rows' "same" under it.
fn add_subtree(watcher: &mut Watcher, root: &mut Root, rel: &str) {
    let name = rel.rsplit_once('/').map_or(rel, |(_, name)| name);
    let present = std::fs::symlink_metadata(root.path.join(rel)).is_ok_and(|meta| meta.file_type().is_dir());
    if !present || !descends(name) || !root.dirs.contains_key(parent_of(rel)) {
        return;
    }
    scan_subtree(watcher, root, rel);
    let prefix = format!("{rel}/");
    let tracked: Vec<String> = root.index.paths_under(&prefix).map(str::to_string).collect();
    for path in tracked {
        if is_clean(root, &path) {
            root.dirty.remove(&path);
        } else {
            root.dirty.insert(path);
        }
    }
}

/// A notification for one file — re-examine the memory of picked-up files and the index row's "same".
fn touch(root: &mut Root, rel: &str) {
    let name = rel.rsplit_once('/').map_or(rel, |(_, name)| name);
    if !root.dirs.contains_key(parent_of(rel)) {
        return;
    }
    let regular = std::fs::symlink_metadata(root.path.join(rel)).is_ok_and(|meta| meta.file_type().is_file());
    if regular && is_candidate_name(name) {
        root.candidates.insert(rel.to_string());
    } else {
        root.candidates.remove(rel);
    }
    if root.index.regular_oid(rel).is_some() {
        if is_clean(root, rel) {
            root.dirty.remove(rel);
        } else {
            root.dirty.insert(rel.to_string());
        }
    }
}

/// Apply the notification work to the memory. When a rebuild is needed (false), the caller rebuilds.
fn apply(watcher: &mut Watcher, root: &mut Root, pending: Pending) -> bool {
    if pending.rebuild {
        return false;
    }
    for rel in &pending.removed {
        remove_subtree(watcher, root, rel);
    }
    for rel in &pending.added {
        add_subtree(watcher, root, rel);
    }
    if pending.reload {
        let Some(index) = GitIndex::read(&root.path) else { return false };
        root.index = index;
        judge_all(root);
    }
    for rel in &pending.files {
        touch(root, rel);
    }
    true
}

struct Daemon {
    watcher: Watcher,
    roots: HashMap<PathBuf, Root>,
    /// Working trees that could not be built, and the time (for a while, queries are answered "unavailable").
    refused: HashMap<PathBuf, Instant>,
    active: Instant,
}

impl Daemon {
    /// Read the kernel's notification queue until empty, collect it into per-working-tree work, and apply it.
    fn drain(&mut self) {
        let mut pending: HashMap<PathBuf, Pending> = HashMap::new();
        let mut overflow = false;
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            // SAFETY: buf is a writable 64KiB region, and read writes only up to that size.
            let n = unsafe { libc::read(self.watcher.fd, buf.as_mut_ptr().cast(), buf.len()) };
            if n <= 0 {
                break;
            }
            let mut at = 0usize;
            let end = n as usize;
            while at + 16 <= end {
                let wd = i32::from_ne_bytes(buf[at..at + 4].try_into().unwrap_or_default());
                let mask = u32::from_ne_bytes(buf[at + 4..at + 8].try_into().unwrap_or_default());
                let len = u32::from_ne_bytes(buf[at + 12..at + 16].try_into().unwrap_or_default()) as usize;
                let name_bytes = buf.get(at + 16..(at + 16 + len).min(end)).unwrap_or_default();
                let name_bytes = &name_bytes[..name_bytes.iter().position(|b| *b == 0).unwrap_or(name_bytes.len())];
                at += 16 + len;
                if mask & libc::IN_Q_OVERFLOW != 0 {
                    overflow = true;
                    continue;
                }
                if mask & libc::IN_IGNORED != 0 {
                    self.watcher.owners.remove(&wd);
                    continue;
                }
                let Some(owners) = self.watcher.owners.get(&wd).cloned() else { continue };
                for owner in owners {
                    note(&mut pending, &owner, mask, name_bytes);
                }
            }
        }
        if overflow {
            for root in self.roots.keys() {
                pending.entry(root.clone()).or_default().rebuild = true;
            }
        }
        for (path, work) in pending {
            let Some(mut root) = self.roots.remove(&path) else { continue };
            if apply(&mut self.watcher, &mut root, work) {
                self.roots.insert(path, root);
            } else {
                self.watcher.release_root(&path);
                self.rebuild(&path, root.last_query);
            }
        }
    }

    fn rebuild(&mut self, path: &Path, last_query: Instant) {
        match build(&mut self.watcher, path) {
            Some(mut root) => {
                root.last_query = last_query;
                self.roots.insert(path.to_path_buf(), root);
            }
            None => {
                self.refused.insert(path.to_path_buf(), Instant::now());
            }
        }
    }

    /// Answer a query for `root` (after applying all notifications).
    fn answer(&mut self, root: &Path) -> Answer {
        self.active = Instant::now();
        self.drain();
        let stale = self.roots.get(root).is_some_and(|state| state.degraded && state.built_at.elapsed() >= REBUILD_AFTER);
        if stale {
            self.watcher.release_root(root);
            self.roots.remove(root);
        }
        if !self.roots.contains_key(root) {
            if self.refused.get(root).is_some_and(|at| at.elapsed() < REBUILD_AFTER) {
                return Answer::default();
            }
            self.refused.remove(root);
            self.rebuild(root, Instant::now());
        }
        let Some(state) = self.roots.get_mut(root) else { return Answer::default() };
        state.last_query = Instant::now();
        if state.degraded {
            return Answer::default();
        }
        let mut dirty: Vec<String> = state.dirty.iter().cloned().collect();
        dirty.sort();
        let extras = state.candidates.iter().filter(|rel| state.index.regular_oid(rel).is_none()).cloned().collect();
        let missing = state.index.files().map(|(rel, _)| rel).filter(|rel| reachable(rel) && !state.candidates.contains(*rel)).map(str::to_string).collect();
        Answer { ok: true, ident: state.index.ident(), dirty, extras, missing }
    }

    fn client(&mut self, mut stream: UnixStream) {
        let _ = stream.set_nonblocking(false);
        let _ = stream.set_read_timeout(Some(Duration::from_millis(200)));
        let _ = stream.set_write_timeout(Some(Duration::from_millis(200)));
        let mut line = String::new();
        if BufReader::new((&stream).take(8192)).read_line(&mut line).is_err() {
            return;
        }
        let Ok(request) = serde_json::from_str::<Request>(&line) else { return };
        let answer = if request.v == PROTOCOL && request.root.starts_with('/') { self.answer(Path::new(&request.root)) } else { Answer::default() };
        if let Ok(text) = serde_json::to_string(&answer) {
            let _ = stream.write_all(format!("{text}\n").as_bytes());
        }
    }

    /// Release working trees that receive no queries.
    fn forget_idle(&mut self) {
        let idle: Vec<PathBuf> = self.roots.iter().filter(|(_, root)| root.last_query.elapsed() >= ROOT_IDLE).map(|(path, _)| path.clone()).collect();
        for path in idle {
            self.watcher.release_root(&path);
            self.roots.remove(&path);
        }
        self.refused.retain(|_, at| at.elapsed() < ROOT_IDLE);
    }
}

/// Write one notification into per-working-tree work.
fn note(pending: &mut HashMap<PathBuf, Pending>, owner: &Owner, mask: u32, name: &[u8]) {
    match owner {
        Owner::Git { root } => {
            let work = pending.entry(root.clone()).or_default();
            if mask & (libc::IN_DELETE_SELF | libc::IN_MOVE_SELF) != 0 {
                work.rebuild = true;
            } else if name == b"index" {
                work.reload = true;
            }
        }
        Owner::Dir { root, rel } => {
            let work = pending.entry(root.clone()).or_default();
            if mask & (libc::IN_DELETE_SELF | libc::IN_MOVE_SELF) != 0 {
                // Rebuild only when the root dir vanished or moved (dirs below are handled by their parent dir's notification).
                work.rebuild |= rel.is_empty();
                return;
            }
            let Ok(name) = std::str::from_utf8(name) else {
                work.rebuild = true;
                return;
            };
            let child = join(rel, name);
            if mask & libc::IN_ISDIR != 0 {
                if mask & (libc::IN_CREATE | libc::IN_MOVED_TO) != 0 {
                    work.added.insert(child);
                } else if mask & (libc::IN_DELETE | libc::IN_MOVED_FROM) != 0 {
                    work.removed.insert(child);
                }
            } else {
                work.files.insert(child);
            }
        }
    }
}

/// Whether someone holds the lock file (if held, a resident process exists or is starting up).
pub fn lock_is_held(path: &Path) -> bool {
    let Ok(file) = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(path) else { return false };
    // SAFETY: fd belongs to an open file, and flock takes only flags. Closing the file releases the lock.
    unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) != 0 }
}

/// The body of `fsmonitor-serve` — take the storage lock and keep receiving queries on the socket. If the lock cannot be taken (another resident process exists), exit immediately.
pub fn serve() -> ExitCode {
    let stop = AtomicBool::new(false);
    run(&state_dir(), Duration::from_secs(1), &stop, DAEMON_IDLE)
}

/// The body of `serve`. At each `tick`, check `stop` and the wait time (tests shorten it).
pub fn run(dir: &Path, tick: Duration, stop: &AtomicBool, idle_exit: Duration) -> ExitCode {
    use std::os::unix::fs::DirBuilderExt;
    if std::fs::DirBuilder::new().mode(0o700).recursive(true).create(dir).is_err() {
        return ExitCode::from(2);
    }
    let Ok(lock) = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(lock_path(dir)) else { return ExitCode::from(2) };
    // SAFETY: fd belongs to an open file. The lock is held for the life of this process (while the lock is held).
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return ExitCode::SUCCESS;
    }
    let socket = socket_path(dir);
    let _ = std::fs::remove_file(&socket);
    let Ok(listener) = UnixListener::bind(&socket) else { return ExitCode::from(2) };
    let Some(watcher) = Watcher::new() else { return ExitCode::from(2) };
    if listener.set_nonblocking(true).is_err() {
        return ExitCode::from(2);
    }
    let mut daemon = Daemon { watcher, roots: HashMap::new(), refused: HashMap::new(), active: Instant::now() };
    let mut fds = [libc::pollfd { fd: listener.as_raw_fd(), events: libc::POLLIN, revents: 0 }, libc::pollfd { fd: daemon.watcher.fd, events: libc::POLLIN, revents: 0 }];
    while !stop.load(Ordering::Relaxed) {
        // SAFETY: fds is an array of two valid pollfds, and poll writes only up to that length.
        let ready = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, tick.as_millis() as i32) };
        if ready > 0 && fds[1].revents != 0 {
            daemon.drain();
        }
        if ready > 0 && fds[0].revents != 0 {
            while let Ok((stream, _)) = listener.accept() {
                daemon.client(stream);
            }
        }
        daemon.forget_idle();
        if daemon.roots.is_empty() && daemon.active.elapsed() >= idle_exit {
            break;
        }
    }
    let _ = std::fs::remove_file(&socket);
    ExitCode::SUCCESS
}

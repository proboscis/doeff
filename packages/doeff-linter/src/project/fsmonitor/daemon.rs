//! 常駐の process の本体(`doeff-linter fsmonitor-serve`)— 作業木ごとに、歩きと「index と同じでない file」を覚え、inotify の知らせで更新して、問いに答える。
//!
//! 作業木 1 つの覚え(`Root`):
//! - `dirs`: 見張っている dir(根からの path・降りる dir だけ — `hy_index::SKIPPED_DIRS` の名の dir は降りない)と、その watch。
//! - `candidates`: 歩きが拾う拡張子(Hy・Python)の通常の file(symlink は拾わない — 歩きと同じ)。
//! - `dirty`: index の通常の file の行のうち、今の file が行と同じ(`GitIndex::blob_of`)と言えない物。見張れない dir の下の行は常に dirty。
//! - `index`: 読んだ index。`.git`(作業木なら gitdir)を見張り、`index` が置き替わったら読み直して `dirty` を全部やり直す。
//!
//! 知らせの扱い: file の知らせはその path だけを調べ直す。dir が増えた・減った時は、その dir の下だけ作り直す。知らせの列の溢れ・根が動いた・見張れない dir が在る時は、
//! 作業木を作り直す(見張れない dir が在る間は、問いに「使えない」と答える — 呼び手は自分で歩く)。問いの答えの前には、知らせの列を空になるまで読む。

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

/// 作業木の dir の見張り。
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
/// gitdir の見張り(`index` の置き替えだけを見る)。
const GIT_MASK: u32 = libc::IN_CREATE | libc::IN_MOVED_TO | libc::IN_CLOSE_WRITE | libc::IN_DELETE | libc::IN_DELETE_SELF | libc::IN_MOVE_SELF | libc::IN_ONLYDIR;
/// 問いが 1 度も来ない作業木を手放すまでの時間。
const ROOT_IDLE: Duration = Duration::from_secs(20 * 60);
/// 問いが 1 度も来ない常駐の process が終わるまでの時間。
const DAEMON_IDLE: Duration = Duration::from_secs(60 * 60);
/// 作れなかった作業木(見張れない・index が信じられない)を作り直すまでの間。
const REBUILD_AFTER: Duration = Duration::from_secs(5);
/// inotify が効かない fs(NFS・SMB・CIFS・FUSE・9p)の `statfs` の種類。
const UNWATCHABLE_FS: [i64; 5] = [0x6969, 0x517B, 0xFF53_4D42, 0x6573_5546, 0x0102_1997];

#[derive(Clone, PartialEq, Eq)]
enum Owner {
    Dir { root: PathBuf, rel: String },
    Git { root: PathBuf },
}

/// inotify の口と、watch ごとの持ち主(同じ dir を 2 つの作業木が見張ることも在る — 作業木の中に別の作業木が在る時)。
struct Watcher {
    fd: i32,
    owners: HashMap<i32, Vec<Owner>>,
}

impl Watcher {
    fn new() -> Option<Watcher> {
        // SAFETY: inotify_init1 は旗だけを取り、fd を返す。
        let fd = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        (fd >= 0).then_some(Watcher { fd, owners: HashMap::new() })
    }

    fn add(&mut self, path: &Path, mask: u32, owner: Owner) -> Option<i32> {
        let c_path = CString::new(path.as_os_str().as_bytes()).ok()?;
        // SAFETY: c_path は NUL で終わる有効な文字列で、呼びの間だけ使う。
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

    /// `owner` の持つ watch `wd` を手放す。持ち主が居なくなれば watch を外す。
    fn release(&mut self, wd: i32, owner: &Owner) {
        let Some(owners) = self.owners.get_mut(&wd) else { return };
        owners.retain(|o| o != owner);
        if owners.is_empty() {
            self.owners.remove(&wd);
            // SAFETY: fd と wd は数だけを取る(外れている wd は EINVAL で戻るだけ)。
            unsafe { libc::inotify_rm_watch(self.fd, wd) };
        }
    }

    /// 作業木 `root` の持つ watch を全部手放す。
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
                    // SAFETY: `release` と同じ。
                    unsafe { libc::inotify_rm_watch(self.fd, wd) };
                }
            }
        }
    }
}

/// 作業木 1 つの覚え。
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

/// 根からの path `rel` の file を、歩きが拾い得るか(拡張子が Hy・Python で、途中の dir が全部降りる名)。
fn reachable(rel: &str) -> bool {
    let (dirs, name) = rel.rsplit_once('/').unwrap_or(("", rel));
    is_candidate_name(name) && dirs.split('/').filter(|d| !d.is_empty()).all(descends)
}

fn fs_watchable(path: &Path) -> bool {
    let Ok(c_path) = CString::new(path.as_os_str().as_bytes()) else { return false };
    let mut stat = std::mem::MaybeUninit::<libc::statfs>::uninit();
    // SAFETY: c_path は有効な C の文字列で、stat は statfs 1 つ分の書き込み先。成功した時だけ読む。
    let done = unsafe { libc::statfs(c_path.as_ptr(), stat.as_mut_ptr()) } == 0;
    // SAFETY: statfs が 0 を返した時、stat は埋まっている。
    !done || !UNWATCHABLE_FS.contains(&(unsafe { stat.assume_init() }.f_type as i64))
}

/// `rel` の file が index の通常の file の行と同じと言えるか(見張れる dir の中で、通常の file で、stat が行と同じ)。
fn is_clean(root: &Root, rel: &str) -> bool {
    root.dirs.contains_key(parent_of(rel))
        && std::fs::symlink_metadata(root.path.join(rel)).is_ok_and(|meta| meta.file_type().is_file() && root.index.blob_of(rel, &meta).is_some())
}

fn judge_all(root: &mut Root) {
    let dirty: HashSet<String> = root.index.files().map(|(rel, _)| rel).filter(|rel| !is_clean(root, rel)).map(str::to_string).collect();
    root.dirty = dirty;
}

/// dir `rel` の下を歩いて、dir を見張り、拾う file を覚える。
fn scan_subtree(watcher: &mut Watcher, root: &mut Root, rel: &str) {
    let mut stack = vec![rel.to_string()];
    while let Some(rel) = stack.pop() {
        let dir = if rel.is_empty() { root.path.clone() } else { root.path.join(&rel) };
        let owner = Owner::Dir { root: root.path.clone(), rel: rel.clone() };
        let Some(wd) = watcher.add(&dir, DIR_MASK, owner) else {
            // 歩く間に消えた dir は、消した知らせが整える。見張れない dir(権限・上限)が在れば、歩きの一覧を信じない。
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

/// 作業木 `path` の覚えを作る。作れなければ None(git の作業木でない・index が信じられない・inotify が効かない fs)。
fn build(watcher: &mut Watcher, path: &Path) -> Option<Root> {
    if !path.is_dir() || !fs_watchable(path) {
        return None;
    }
    // gitdir を先に見張ってから index を読む(読む間の置き替えを知らせで拾う)。
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

/// 知らせから集めた、作業木 1 つの仕事。
#[derive(Default)]
struct Pending {
    files: HashSet<String>,
    added: BTreeSet<String>,
    removed: BTreeSet<String>,
    reload: bool,
    rebuild: bool,
}

/// dir `rel` の下の覚えを手放す(見張り・拾った file・index の行の「同じ」)。
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

/// 増えた dir `rel` の下を歩き直し、その下の index の行の「同じ」を調べ直す。
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

/// file 1 つの知らせ — 拾う file の覚えと、index の行の「同じ」を調べ直す。
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

/// 知らせの仕事を覚えへ当てる。作り直しが要る(false)時は呼び手が作り直す。
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
    /// 作れなかった作業木と、その刻(しばらく問いに「使えない」と答える)。
    refused: HashMap<PathBuf, Instant>,
    active: Instant,
}

impl Daemon {
    /// kernel の知らせの列を空になるまで読み、作業木ごとの仕事に集めて当てる。
    fn drain(&mut self) {
        let mut pending: HashMap<PathBuf, Pending> = HashMap::new();
        let mut overflow = false;
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            // SAFETY: buf は書き込める 64KiB の領域で、read はその大きさまでしか書かない。
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

    /// `root` の問いに答える(知らせを全部当ててから)。
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

    /// 問いの来ない作業木を手放す。
    fn forget_idle(&mut self) {
        let idle: Vec<PathBuf> = self.roots.iter().filter(|(_, root)| root.last_query.elapsed() >= ROOT_IDLE).map(|(path, _)| path.clone()).collect();
        for path in idle {
            self.watcher.release_root(&path);
            self.roots.remove(&path);
        }
        self.refused.retain(|_, at| at.elapsed() < ROOT_IDLE);
    }
}

/// 1 つの知らせを、作業木ごとの仕事へ書く。
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
                // 根の dir が消えた・動いた時だけ作り直す(下の dir は、親の dir の知らせが整える)。
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

/// 錠の file を誰かが持っているか(持っていれば常駐の process が居るか、起きる途中)。
pub fn lock_is_held(path: &Path) -> bool {
    let Ok(file) = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(path) else { return false };
    // SAFETY: fd は開いた file の物で、flock は旗だけを取る。file を閉じれば錠も外れる。
    unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) != 0 }
}

/// `fsmonitor-serve` の本体 — 置き場の錠を取り、socket で問いを受け続ける。錠が取れなければ(別の常駐の process が居る)すぐ終わる。
pub fn serve() -> ExitCode {
    let stop = AtomicBool::new(false);
    run(&state_dir(), Duration::from_secs(1), &stop, DAEMON_IDLE)
}

/// `serve` の本体。`tick` ごとに `stop` と待ちの時間を見る(試験は短くする)。
pub fn run(dir: &Path, tick: Duration, stop: &AtomicBool, idle_exit: Duration) -> ExitCode {
    use std::os::unix::fs::DirBuilderExt;
    if std::fs::DirBuilder::new().mode(0o700).recursive(true).create(dir).is_err() {
        return ExitCode::from(2);
    }
    let Ok(lock) = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(lock_path(dir)) else { return ExitCode::from(2) };
    // SAFETY: fd は開いた file の物。錠はこの process の間(lock を持つ間)持つ。
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
        // SAFETY: fds は 2 つの有効な pollfd の列で、poll はその長さまでしか書かない。
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

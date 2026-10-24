//! 作業木の「git の index と同じでない file」と repo の歩きの一覧を、常駐の process が inotify で覚えて答える(git の fsmonitor の形・card ki-9e7e85407b71)。
//!
//! 1 file の実行(hook・エディタの `--stdin`)は、1 回ごとに別の process なので、温まっても repo の Hy の全部(約 4600)の stat と約 2000 の dir の
//! 読みを毎回やり直していた(`GitIndex::blob_of` で file が index と同じかを確かめる所・`hy_files` の歩き)。機体ごとに 1 つの常駐の process
//! (`doeff-linter fsmonitor-serve`)が、作業木ごとに歩きと stat を 1 度だけ済ませ、以後は inotify の知らせだけで更新する。1 file の実行は
//! 自分で index を読み(1 度)、常駐の process へ「index の行のうち今の file と違う物・歩きに在って index に無い物・index に在って歩きに無い物」を問う。
//!
//! 守り(どれも「常駐の process が無い」時と同じ答えへ倒れるだけ — 答えは変わらず、遅くなるだけ):
//! - 常駐の process が居ない・答えが 300 ミリ秒以内に来ない・答えた index が自分の読んだ index と違う(名乗り = 更新時刻・大きさ・inode が違う)・
//!   作業木が歩けない(読めない dir・watch の上限・UTF-8 でない名)・NFS などの inotify が効かない fs は、使わない。
//! - 問いに答える前に、kernel の知らせの列を空になるまで読む。inotify の知らせは書き込みの system call の中で積まれるので、hook より前に済んだ書き込みは
//!   必ず答えに入る(watchman の cookie が要る遠い fs と違い、同じ機体の fs では待ちが要らない)。
//! - 知らせの列が溢れた・作業木の根が動いた・dir が増減した時は、作業木を作り直す(dir の増減は、その dir の下だけ)。
//!
//! 使う側は `session(root)` で一覧を受け、`Session::clean_oid` で file の blob の id を(stat せずに)引き、`Session::listing` で歩きの結果を引く。
//! 有効にするのは `enable()` を呼んだ実行だけ(`--stdin` の 1 file の実行)。`DOEFF_LINTER_NO_FSMONITOR` が在れば使わない。
//! 常駐の process は inotify(Linux だけの口)で作業木を見張るので、Linux の外(macOS の機体)では組まず、`enable()` も効かない —
//! 1 file の実行は常駐の process が居ない時と同じく自分で stat して歩く(答えは同じ・card ki-1bcd5b34cbb3)。

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

/// 常駐の process を起こす副命令の名。
pub const SERVE_COMMAND: &str = "fsmonitor-serve";
/// 使わない印の環境変数。
const OFF_ENV: &str = "DOEFF_LINTER_NO_FSMONITOR";
/// 常駐の process の置き場(socket・錠)を変える環境変数(試験)。
pub const DIR_ENV: &str = "DOEFF_LINTER_FSMONITOR_DIR";
/// 答えの形の版。意味(何を「同じ」と見るか)を変えたら上げる — 旧い常駐の process は旧い版の socket で答え続け、新しい実行は新しい版の socket へ問う。
pub const PROTOCOL: u32 = 1;
/// 問いの待ち。越えたら常駐の process を使わずに自分で歩く(作業木の最初の問いは、常駐の process が作業木を歩いて作る間 待たされる)。
const QUERY_TIMEOUT: Duration = Duration::from_millis(300);
/// 常駐の process を起こすのを試す間隔の下限(起こせない機体で、毎回 process を起こそうとしない)。
const RESPAWN_EVERY: Duration = Duration::from_secs(60);

/// 1 回の問い。
#[derive(Debug, Serialize, Deserialize)]
pub struct Request {
    pub v: u32,
    pub root: String,
}

/// 問いの答え。`ok` が偽なら他の欄は空で、呼び手は自分で歩く。
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Answer {
    pub ok: bool,
    /// 常駐の process が読んでいる index file の名乗り。
    pub ident: Ident,
    /// index の通常の file の行のうち、今の file が行と同じと言えない物(stat が違う・無い・racy・見張れない dir の下)。
    pub dirty: Vec<String>,
    /// 歩きが拾う(Hy・Python の拡張子の通常の file)のに、index に通常の file の行が無い物。
    pub extras: Vec<String>,
    /// index に通常の file の行が在り Hy・Python の拡張子なのに、歩きが拾わない物(消した・symlink になった・symlink の dir の下)。
    pub missing: Vec<String>,
}

static ENABLED: AtomicBool = AtomicBool::new(false);

/// この実行で常駐の process に問うことを許す(`--stdin` の 1 file の実行だけが呼ぶ)。
pub fn enable() {
    if cfg!(target_os = "linux") && std::env::var_os(OFF_ENV).is_none() {
        ENABLED.store(true, Ordering::Relaxed);
    }
}

/// 常駐の process の置き場。機体の利用者ごとに 1 つ(他の利用者の socket へ問わない)。
pub fn state_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os(DIR_ENV) {
        return PathBuf::from(dir);
    }
    // SAFETY: getuid は引数も memory も取らない。
    let uid = unsafe { libc::getuid() };
    PathBuf::from(format!("/tmp/doeff-linter-fsmonitor-{uid}"))
}

pub fn socket_path(dir: &Path) -> PathBuf {
    dir.join(format!("v{PROTOCOL}.sock"))
}

pub fn lock_path(dir: &Path) -> PathBuf {
    dir.join(format!("v{PROTOCOL}.lock"))
}

/// 常駐の process に `root` の一覧を問う。居ない・答えない・答えが読めない時は None。居なければ(間隔を置いて)起こす。
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

/// 常駐の process を、待たない別の process として起こす(新しい session・標準の入出力なし — hook が自分の process の組を打ち切っても止まらず、
/// hook の出力の終わりを待たせない)。錠を誰かが持っていれば(起こした直後か、動いているが socket がまだ無い)起こさない。
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
    // SAFETY: fork の後・exec の前の子で呼ぶのは setsid だけ(async-signal-safe で、memory を触らない)。
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let _ = command.spawn();
}

/// 常駐の process が答えた、1 つの作業木の一覧。
pub struct Session {
    root: PathBuf,
    index: Arc<GitIndex>,
    dirty: HashSet<String>,
    extras: Vec<String>,
    missing: HashSet<String>,
}

type Sessions = Mutex<HashMap<PathBuf, Option<Arc<Session>>>>;
static SESSIONS: OnceLock<Sessions> = OnceLock::new();

/// `root` の一覧(この実行で 1 度だけ問う)。有効でない・常駐の process が使えない・index が合わない時は None(呼び手は自分で stat して歩く)。
pub fn session(root: &Path) -> Option<Arc<Session>> {
    if !ENABLED.load(Ordering::Relaxed) {
        return None;
    }
    let memo = SESSIONS.get_or_init(Default::default);
    let mut memo = memo.lock().unwrap_or_else(PoisonError::into_inner);
    memo.entry(root.to_path_buf()).or_insert_with(|| crate::timing::timed("fsmonitor.query", || open(&state_dir(), root))).clone()
}

/// 常駐の process に問って一覧を作る(`session` の本体)。自分が読んだ index と常駐の process が見ている index が同じ時だけ。
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
    /// `rel`(根からの path)の file が index の通常の file の行と同じなら、その blob の id — stat しない。
    pub fn clean_oid(&self, rel: &str) -> Option<[u8; 20]> {
        if self.dirty.contains(rel) {
            return None;
        }
        self.index.regular_oid(rel)
    }

    /// `rules` の歩きが拾う file の path(`hy_files::walk_reading_with` が歩いて並べる物と同じ集合と順 — path の成分ごとの順)。
    pub fn listing(&self, rules: &WalkRules) -> Vec<PathBuf> {
        let tracked = self.index.files().map(|(rel, _)| rel).filter(|rel| walks_to(rules, rel) && !self.missing.contains(*rel));
        let extras = self.extras.iter().map(String::as_str).filter(|rel| walks_to(rules, rel));
        let mut found: Vec<PathBuf> = tracked.chain(extras).map(|rel| self.root.join(rel)).collect();
        found.sort();
        found
    }
}

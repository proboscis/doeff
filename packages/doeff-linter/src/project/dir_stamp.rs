//! dir の中の file の読みを、file の指紋で覚える(card ki-58bfad2c13a1 の残り・ki-40c8ca380713)。
//!
//! 登録簿と判定の一覧は 1 鍵 1 file の dir で、agora-controllers では 500 file ほどある。1 file の hook は何度も撃たれ、その度に全部を open すると、
//! page cache が数分で追い出される Pod では毎回 disk を待つ(IO の圧迫 55〜66% の日、冷えた作業木の 1 回の open 596 件のうち 494 件がこの dir だった)。
//! 一方、冷えた状態でも stat は 449 回で 0.02 秒(inode は 1 block に 16 個載る)。指紋が前の実行と同じなら、中身を読まずに前の読みの結果を使う。
//!
//! 指紋は 2 通り:
//! - 全部の file が git の index の行と同じ stat なら(`GitIndex::blob_of` — 中身は index の blob と同じ)、(根からの path・blob の id)で作る。中身だけで
//!   決まるので、同じ commit から作った別の作業木どうしが結果を分け合う(冷えた新しい作業木は 449 file を読まず、覚えも書かない — card ki-40c8ca380713。
//!   読みの待ちが 0.8〜4.8 秒・覚えの書きが 1.8〜3.0 秒止まる回があった)。
//! - 1 つでも外れれば(index に無い・書き替えた・git の作業木でない)、(path・大きさ・更新時刻・inode)で作る。path を含むので作業木ごと。
//!
//! 後者で、指紋が同じなのに中身が違う事は、(1) 更新時刻を戻して書く道具(`touch -r`・`cp -p`)で大きさも同じに保った時と、(2) 更新時刻の刻みより
//! 短い間に同じ大きさで書き替えた時に起きうる。(2) は git の racy と同じ守りで外す — 更新から `SETTLE` 経っていない file が 1 つでも在れば覚えない
//! (その実行は毎回読む)。(1) は覚えない手立てが無いが、登録簿の file を更新時刻まで保って同じ大きさに書き替える運用は無い。
//! 前者の守りは index の側に在る(行の更新時刻が index を書いた時刻以後の行は信じない — `git_index` の頭の註)。

use std::fs::Metadata;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::de::DeserializeOwned;
use serde::Serialize;

use super::git_index::{self, GitIndex};
use super::{bulk_read, facts_cache, paths};

/// 更新からこの時間経っていない file は、同じ瞬間の別の書き込みと更新時刻で区別できないので覚えない。
const SETTLE_NS: u128 = 2_000_000_000;
/// blob の id の指紋で、index の行と stat が外れて中身を読んで id を作る file の数の上限(dir の file の 1/8 と大きい方)。
const UNTRUSTED_AT_MOST: usize = 16;

/// `files` の読みの結果 `T` を、`files` の指紋で覚える。指紋が前と同じなら `read` を呼ばずに前の結果を返す。
/// `read` は結果と「覚えてよいか」を返す(根の絶対 path を持つ結果は置き場が断るので、呼び手が先に外す)。
/// 指紋を作れない(更新の直後の file・読めない file・file が無い)時は覚えず、毎回 `read` を呼ぶ。
pub fn remembered<T, F>(root: &Path, kind: &str, files: &[PathBuf], read: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> (T, bool),
{
    match fingerprint(root, files) {
        Some(key) => facts_cache::keyed_value_if(root, kind, key, read),
        None => read().0,
    }
}

/// 指紋の hash(FNV-1a — 実行と build をまたいで同じ答え)。
struct Fnv(u64);

impl Fnv {
    fn new() -> Fnv {
        Fnv(0xcbf2_9ce4_8422_2325)
    }

    fn mix(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 ^= u64::from(*byte);
            self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
}

/// `files` 全部の指紋(頭の註の 2 通り — blob の id で作れればそれ、作れなければ stat で)。覚えられない時は None。
fn fingerprint(root: &Path, files: &[PathBuf]) -> Option<u64> {
    if files.is_empty() {
        return None;
    }
    // 冷えた stat も待ちなので重ねる(冷えた 449 回で 0.02 秒でも、IO の圧迫が重い時は 1 回ずつ待つ)。
    let metas = bulk_read::fan_out(files, |file| std::fs::metadata(file).ok());
    by_blobs(root, files, &metas).or_else(|| by_stamps(files, &metas))
}

/// (根からの path・中身の blob の id)の指紋 — 作業木をまたいで同じ値になる。stat が git の index の行と同じ file は index の id を使い(中身を読まない)、
/// 外れた file(index に無い・書き替えた・index を書いたのと同じ刻に書かれた racy の行 — 作業木を作った直後は、最後に書かれた数 file がそうなる)は
/// 中身を読んで id を作る。外れた file が多い(`UNTRUSTED_AT_MOST` を越える)・git の作業木でない時は None(stat の指紋に任せる — 毎回全部を読むより、
/// 作業木ごとに覚えるほうが軽い)。外れた file に更新から `SETTLE_NS` 経っていない物が在れば None(読む間に書かれている file の id を指紋にしない)。
/// stat の指紋と同じ値にならないよう、頭に種類の印を混ぜる。
fn by_blobs(root: &Path, files: &[PathBuf], metas: &[Option<Metadata>]) -> Option<u64> {
    let index = GitIndex::of(root)?;
    let rels: Vec<String> = files.iter().map(|file| paths::relative_path(root, file)).collect::<Option<_>>()?;
    let known: Vec<Option<[u8; 20]>> = rels.iter().zip(metas).map(|(rel, meta)| index.blob_of(rel, meta.as_ref()?)).collect();
    let untrusted: Vec<usize> = known.iter().enumerate().filter(|(_, oid)| oid.is_none()).map(|(at, _)| at).collect();
    if untrusted.len() > UNTRUSTED_AT_MOST.max(files.len() / 8) {
        return None;
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_nanos();
    let settled = |at: &usize| metas[*at].as_ref().and_then(stamp).is_some_and(|(_, mtime_ns, _)| mtime_ns.saturating_add(SETTLE_NS) <= now);
    if !untrusted.iter().all(settled) {
        return None;
    }
    let read: Vec<Option<[u8; 20]>> = bulk_read::fan_out(&untrusted, |at| std::fs::read(&files[*at]).ok().map(|bytes| git_index::blob_id(&bytes)));
    let mut oids = known;
    for (at, oid) in untrusted.iter().zip(read) {
        oids[*at] = Some(oid?);
    }
    let mut hash = Fnv::new();
    hash.mix(b"git-blob\0");
    hash.mix(&(files.len() as u64).to_le_bytes());
    for (rel, oid) in rels.iter().zip(oids) {
        hash.mix(rel.as_bytes());
        hash.mix(&[0]);
        hash.mix(&oid?);
    }
    Some(hash.0)
}

/// (path・大きさ・更新時刻・inode)の指紋。更新から `SETTLE_NS` 経っていない file・読めない file が在れば None。
fn by_stamps(files: &[PathBuf], metas: &[Option<Metadata>]) -> Option<u64> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_nanos();
    let mut hash = Fnv::new();
    hash.mix(&(files.len() as u64).to_le_bytes());
    for (file, meta) in files.iter().zip(metas) {
        let (len, mtime_ns, inode) = stamp(meta.as_ref()?)?;
        if mtime_ns.saturating_add(SETTLE_NS) > now {
            return None;
        }
        hash.mix(file.to_string_lossy().as_bytes());
        hash.mix(&[0]);
        hash.mix(&len.to_le_bytes());
        hash.mix(&mtime_ns.to_le_bytes());
        hash.mix(&inode.to_le_bytes());
    }
    Some(hash.0)
}

/// file 1 つの印(大きさ・更新時刻 ns・inode)。
fn stamp(meta: &Metadata) -> Option<(u64, u128, u64)> {
    let mtime_ns = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some((meta.len(), mtime_ns, inode(meta)))
}

#[cfg(unix)]
fn inode(meta: &Metadata) -> u64 {
    std::os::unix::fs::MetadataExt::ino(meta)
}

#[cfg(not(unix))]
fn inode(_meta: &Metadata) -> u64 {
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn aged(dir: &Path, name: &str, text: &str) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, text).unwrap();
        set_age(&path, 3600);
        path
    }

    fn set_age(path: &Path, seconds: u64) {
        std::fs::File::options().write(true).open(path).unwrap().set_modified(SystemTime::now() - Duration::from_secs(seconds)).unwrap();
    }

    #[test]
    fn the_same_files_give_the_same_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let files = vec![aged(dir.path(), "a.txt", "one"), aged(dir.path(), "b.txt", "two")];
        let first = fingerprint(dir.path(), &files);
        assert!(first.is_some());
        assert_eq!(first, fingerprint(dir.path(), &files));
    }

    #[test]
    fn a_change_of_size_time_name_or_set_of_files_changes_the_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let a = aged(dir.path(), "a.txt", "one");
        let b = aged(dir.path(), "b.txt", "two");
        let base = fingerprint(dir.path(), &[a.clone(), b.clone()]).unwrap();
        // 大きさ
        std::fs::write(&b, "three").unwrap();
        set_age(&b, 3600);
        let resized = fingerprint(dir.path(), &[a.clone(), b.clone()]).unwrap();
        assert_ne!(resized, base);
        // 更新時刻だけ(大きさは同じ)
        set_age(&b, 7200);
        let retimed = fingerprint(dir.path(), &[a.clone(), b.clone()]).unwrap();
        assert_ne!(retimed, resized);
        // file の集まり(足す・減らす)
        let c = aged(dir.path(), "c.txt", "x");
        assert_ne!(fingerprint(dir.path(), &[a.clone(), b.clone(), c]).unwrap(), retimed);
        assert_ne!(fingerprint(dir.path(), std::slice::from_ref(&a)).unwrap(), retimed);
    }

    #[test]
    fn the_same_names_and_stamps_in_another_dir_are_another_fingerprint() {
        let first = tempfile::tempdir().unwrap();
        let second = tempfile::tempdir().unwrap();
        let a = aged(first.path(), "a.txt", "one");
        let b = aged(second.path(), "a.txt", "one");
        // 更新時刻も同じにする(inode と path だけが違う)。
        let at = std::fs::metadata(&a).unwrap().modified().unwrap();
        std::fs::File::options().write(true).open(&b).unwrap().set_modified(at).unwrap();
        assert_ne!(fingerprint(first.path(), &[a]), fingerprint(second.path(), &[b]));
    }

    fn git(dir: &Path, args: &[&str]) -> bool {
        std::process::Command::new("git")
            .args(["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"])
            .args(args)
            .current_dir(dir)
            .env_remove("GIT_DIR")
            .env_remove("GIT_INDEX_FILE")
            .status()
            .is_ok_and(|status| status.success())
    }

    /// 同じ commit から作った 2 つの作業木の同じ file は、path も inode も更新時刻も違うが、blob の id の指紋は同じ(作業木をまたいで分け合う)。
    /// 片方の file を書き替えれば(index の blob と違う中身)、その作業木は stat の指紋に倒れて違う値になる。git が無い機体では確かめない。
    #[test]
    fn two_worktrees_of_one_commit_share_the_blob_fingerprint() {
        let main = tempfile::tempdir().unwrap();
        let files = |root: &Path| vec![root.join("reg/a.txt"), root.join("reg/b.txt")];
        std::fs::create_dir_all(main.path().join("reg")).unwrap();
        for file in files(main.path()) {
            std::fs::write(&file, "key\n").unwrap();
            set_age(&file, 3600);
        }
        if !(git(main.path(), &["init", "-q", "-b", "main"]) && git(main.path(), &["add", "-A"]) && git(main.path(), &["commit", "-q", "-m", "base"])) {
            return;
        }
        let parent = tempfile::tempdir().unwrap();
        let other = parent.path().join("other");
        assert!(git(main.path(), &["worktree", "add", "-q", "--detach", other.to_str().unwrap()]));
        let shared = fingerprint(main.path(), &files(main.path()));
        assert!(shared.is_some());
        // 作ったばかりの作業木の file は、更新から 2 秒の内(index と同じ刻の racy の行でもありうる)— 読む間に書かれうるので覚えない。
        assert_eq!(fingerprint(&other, &files(&other)), None, "書かれた直後の file の指紋を作った");
        // 更新時刻が index の行と外れた file は、中身を読んで blob の id を作る — 中身が同じなら別の作業木と同じ指紋。
        for file in files(&other) {
            set_age(&file, 3600);
        }
        assert_eq!(fingerprint(&other, &files(&other)), shared, "同じ中身の作業木どうしで指紋が違う");
        std::fs::write(other.join("reg/b.txt"), "changed\n").unwrap();
        set_age(&other.join("reg/b.txt"), 3600);
        let changed = fingerprint(&other, &files(&other));
        assert!(changed.is_some() && changed != shared, "書き替えた file を index の blob の指紋で数えた");
    }

    /// index の行と外れた file が多い dir は、毎回全部を読んで id を作るより作業木ごとに stat で覚えるほうが軽い — stat の指紋に倒す(作業木ごとに違う値)。
    #[test]
    fn many_files_off_the_index_fall_back_to_the_stat_fingerprint() {
        let main = tempfile::tempdir().unwrap();
        let files: Vec<PathBuf> = (0..40).map(|n| main.path().join(format!("reg/k{n:02}.txt"))).collect();
        std::fs::create_dir_all(main.path().join("reg")).unwrap();
        for file in &files {
            std::fs::write(file, "key\n").unwrap();
            set_age(file, 3600);
        }
        if !(git(main.path(), &["init", "-q", "-b", "main"]) && git(main.path(), &["add", "-A"]) && git(main.path(), &["commit", "-q", "-m", "base"])) {
            return;
        }
        let trusted = fingerprint(main.path(), &files);
        for file in &files {
            set_age(file, 7200);
        }
        let metas: Vec<Option<Metadata>> = files.iter().map(|file| std::fs::metadata(file).ok()).collect();
        assert_eq!(by_blobs(main.path(), &files, &metas), None, "40 file のうち 40 が index と外れても中身を読んで指紋を作った");
        assert_eq!(fingerprint(main.path(), &files), by_stamps(&files, &metas));
        assert_ne!(fingerprint(main.path(), &files), trusted);
    }

    #[test]
    fn a_file_written_a_moment_ago_or_missing_or_no_file_is_not_remembered() {
        let dir = tempfile::tempdir().unwrap();
        let old = aged(dir.path(), "old.txt", "one");
        let fresh = dir.path().join("fresh.txt");
        std::fs::write(&fresh, "now").unwrap();
        assert!(fingerprint(dir.path(), &[old.clone(), fresh]).is_none(), "更新の直後は覚えない");
        assert!(fingerprint(dir.path(), &[old.clone(), dir.path().join("missing.txt")]).is_none(), "読めない file があれば覚えない");
        assert!(fingerprint(dir.path(), &[]).is_none(), "file が無ければ覚えない");
        // 未来の更新時刻(時計のずれ)も、今と区別できないので覚えない。
        let future = dir.path().join("future.txt");
        std::fs::write(&future, "later").unwrap();
        std::fs::File::options().write(true).open(&future).unwrap().set_modified(SystemTime::now() + Duration::from_secs(3600)).unwrap();
        assert!(fingerprint(dir.path(), &[old, future]).is_none());
    }

    #[test]
    fn without_a_fingerprint_the_read_runs_each_time_and_its_answer_is_returned() {
        let dir = tempfile::tempdir().unwrap();
        let fresh = dir.path().join("fresh.txt");
        std::fs::write(&fresh, "now").unwrap();
        let calls = std::sync::atomic::AtomicUsize::new(0);
        for _ in 0..2 {
            let value: String = remembered(dir.path(), "dir-stamp-test", std::slice::from_ref(&fresh), || {
                calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                ("read".to_string(), true)
            });
            assert_eq!(value, "read");
        }
        assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 2);
    }
}

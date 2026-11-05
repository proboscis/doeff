//! Remembers the reading of the files in a dir by the files' fingerprint.
//!
//! The registry and the list of judgments are dirs with one file per key, about 500 files in agora-controllers. A one-file hook fires many times, and opening everything each time
//! means waiting on the disk every time in a Pod where the page cache is evicted within minutes (on days with 55-66% IO pressure, 494 of the 596 opens in one cold working-tree run were this dir).
//! On the other hand, even cold, 449 stats take 0.02 seconds (16 inodes fit in one block). If the fingerprint equals the previous run's, the previous reading's result is used without reading the contents.
//!
//! There are two kinds of fingerprint:
//! - If every file has the same stat as its git index row (`GitIndex::blob_of`: the contents equal the index's blob), it is built from (path from the root, blob id). Since it is
//!   decided by contents alone, different working trees made from the same commit share results (a new cold working tree neither reads the 449 files nor writes a memo.
//!   There were runs stalled 0.8-4.8 seconds waiting on reads and 1.8-3.0 seconds writing memos).
//! - If even one file deviates (not in the index, rewritten, not a git working tree), it is built from (path, size, mtime, inode). It includes the path, so it is per working tree.
//!
//! With the latter, contents can differ even though the fingerprint is the same in two cases: (1) a tool that writes while restoring the mtime (`touch -r`, `cp -p`) keeps the size the same too, and (2) a rewrite
//! with the same size within a shorter span than the mtime granularity. (2) is excluded with the same guard as git's racy case: if even one file was updated less than `SETTLE` ago, nothing is remembered
//! (that run reads every time). For (1) there is no way to avoid remembering, but there is no workflow that rewrites a registry file to the same size while preserving its mtime.
//! The guard for the former is on the index side (rows whose mtime is at or after the time the index was written are not trusted; see the header comment of `git_index`).

use std::fs::Metadata;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::de::DeserializeOwned;
use serde::Serialize;

use super::fsmonitor;
use super::git_index::{self, GitIndex};
use super::{bulk_read, facts_cache, paths};

/// A file updated less than this long ago cannot be told apart by mtime from another write at the same instant, so it is not remembered.
const SETTLE_NS: u128 = 2_000_000_000;
/// Upper limit on the number of files, under the blob-id fingerprint, whose stat deviates from the index row so that the contents are read to build the id (1/8 of the dir's files, or this, whichever is larger).
const UNTRUSTED_AT_MOST: usize = 16;

/// Remembers the reading result `T` of `files` by the fingerprint of `files`. If the fingerprint equals the previous one, returns the previous result without calling `read`.
/// `read` returns the result and "whether it may be remembered" (the store refuses a result that holds the root's absolute path, so the caller removes it first).
/// When no fingerprint can be built (a just-updated file, an unreadable file, no files), nothing is remembered and `read` is called every time.
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

/// Hash of the fingerprint (FNV-1a: the same answer across runs and builds).
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

/// Fingerprint of all of `files` (the two kinds in the header comment: by blob id if it can be built, otherwise by stat). None if it cannot be remembered.
fn fingerprint(root: &Path, files: &[PathBuf]) -> Option<u64> {
    if files.is_empty() {
        return None;
    }
    // If the resident process (`fsmonitor`) has answered that all files are "the same as the index rows", build the blob-id fingerprint without stat.
    if let Some(key) = by_vouched(root, files) {
        return Some(key);
    }
    // A cold stat is also a wait, so overlap them (even if 449 take 0.02 seconds, under heavy IO pressure each waits one at a time).
    let metas = bulk_read::fan_out(files, |file| std::fs::metadata(file).ok());
    by_blobs(root, files, &metas).or_else(|| by_stamps(files, &metas))
}

/// Fingerprint of (path from the root, blob id of the contents): the same value across working trees. For a file whose stat equals the git index row it uses the index's id (without reading contents),
/// and for a deviating file (not in the index, rewritten, or a racy row written at the same instant the index was written: right after a working tree is made, the last few files written are like this)
/// it reads the contents to build the id. None if many files deviate (more than `UNTRUSTED_AT_MOST`) or it is not a git working tree (leave it to the stat fingerprint: remembering per working tree
/// is lighter than reading everything every time). None if a deviating file was updated less than `SETTLE_NS` ago (the id of a file that may be written while being read is not used as a fingerprint).
/// A kind marker is mixed into the head so that it never equals the value of a stat fingerprint.
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
    blob_fingerprint(&rels, oids)
}

/// Hash of the `by_blobs` fingerprint (the sequence of paths and blob ids).
fn blob_fingerprint(rels: &[String], oids: Vec<Option<[u8; 20]>>) -> Option<u64> {
    let mut hash = Fnv::new();
    hash.mix(b"git-blob\0");
    hash.mix(&(rels.len() as u64).to_le_bytes());
    for (rel, oid) in rels.iter().zip(oids) {
        hash.mix(rel.as_bytes());
        hash.mix(&[0]);
        hash.mix(&oid?);
    }
    Some(hash.0)
}

/// Blob-id fingerprint for when the resident process answered that all files are "the same as the index rows" (the same value as `by_blobs`; no stat and no read). None if even one differs.
fn by_vouched(root: &Path, files: &[PathBuf]) -> Option<u64> {
    let session = fsmonitor::session(root)?;
    let rels: Vec<String> = files.iter().map(|file| paths::relative_path(root, file)).collect::<Option<_>>()?;
    let oids: Vec<Option<[u8; 20]>> = rels.iter().map(|rel| session.clean_oid(rel)).collect();
    oids.iter().all(Option::is_some).then(|| blob_fingerprint(&rels, oids)).flatten()
}

/// Fingerprint of (path, size, mtime, inode). None if there is a file updated less than `SETTLE_NS` ago or an unreadable file.
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

/// The marker of one file (size, mtime ns, inode).
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
        // size
        std::fs::write(&b, "three").unwrap();
        set_age(&b, 3600);
        let resized = fingerprint(dir.path(), &[a.clone(), b.clone()]).unwrap();
        assert_ne!(resized, base);
        // mtime only (size is the same)
        set_age(&b, 7200);
        let retimed = fingerprint(dir.path(), &[a.clone(), b.clone()]).unwrap();
        assert_ne!(retimed, resized);
        // set of files (added, removed)
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
        // make the mtime the same too (only inode and path differ).
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

    /// The same file in two working trees made from the same commit differs in path, inode, and mtime, but the blob-id fingerprint is the same (shared across working trees).
    /// If one file is rewritten (contents different from the index's blob), that working tree falls back to the stat fingerprint and gets a different value. Not checked on machines without git.
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
        // A file in a just-made working tree is within 2 seconds of its update (it may also be a racy row at the same instant as the index), and may be written while being read, so it is not remembered.
        assert_eq!(fingerprint(&other, &files(&other)), None, "built a fingerprint for a just-written file");
        // A file whose mtime deviates from the index row has its contents read to build the blob id; with the same contents it gets the same fingerprint as another working tree.
        for file in files(&other) {
            set_age(&file, 3600);
        }
        assert_eq!(fingerprint(&other, &files(&other)), shared, "working trees with the same contents have different fingerprints");
        std::fs::write(other.join("reg/b.txt"), "changed\n").unwrap();
        set_age(&other.join("reg/b.txt"), 3600);
        let changed = fingerprint(&other, &files(&other));
        assert!(changed.is_some() && changed != shared, "counted a rewritten file by the index blob's fingerprint");
    }

    /// A dir where many files deviate from the index rows is lighter to remember per working tree by stat than to read everything every time to build ids; fall back to the stat fingerprint (a different value per working tree).
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
        assert_eq!(by_blobs(main.path(), &files, &metas), None, "built a fingerprint by reading contents even though 40 of 40 files deviate from the index");
        assert_eq!(fingerprint(main.path(), &files), by_stamps(&files, &metas));
        assert_ne!(fingerprint(main.path(), &files), trusted);
    }

    #[test]
    fn a_file_written_a_moment_ago_or_missing_or_no_file_is_not_remembered() {
        let dir = tempfile::tempdir().unwrap();
        let old = aged(dir.path(), "old.txt", "one");
        let fresh = dir.path().join("fresh.txt");
        std::fs::write(&fresh, "now").unwrap();
        assert!(fingerprint(dir.path(), &[old.clone(), fresh]).is_none(), "nothing is remembered right after an update");
        assert!(fingerprint(dir.path(), &[old.clone(), dir.path().join("missing.txt")]).is_none(), "nothing is remembered if there is an unreadable file");
        assert!(fingerprint(dir.path(), &[]).is_none(), "nothing is remembered if there are no files");
        // A future mtime (clock skew) cannot be told apart from now either, so nothing is remembered.
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

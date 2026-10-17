//! dir の中の file の読みを、(path・大きさ・更新時刻・inode)の指紋で覚える(card ki-58bfad2c13a1 の残り)。
//!
//! 登録簿と判定の一覧は 1 鍵 1 file の dir で、agora-controllers では 500 file ほどある。1 file の hook は何度も撃たれ、その度に全部を open すると、
//! page cache が数分で追い出される Pod では毎回 disk を待つ(IO の圧迫 55〜66% の日、冷えた作業木の 1 回の open 596 件のうち 494 件がこの dir だった)。
//! 一方、冷えた状態でも stat は 449 回で 0.02 秒(inode は 1 block に 16 個載る)。指紋が前の実行と同じなら、中身を読まずに前の読みの結果を使う。
//!
//! 指紋が同じなのに中身が違う事は、(1) 更新時刻を戻して書く道具(`touch -r`・`cp -p`)で大きさも同じに保った時と、(2) 更新時刻の刻みより
//! 短い間に同じ大きさで書き替えた時に起きうる。(2) は git の racy と同じ守りで外す — 更新から `SETTLE` 経っていない file が 1 つでも在れば覚えない
//! (その実行は毎回読む)。(1) は覚えない手立てが無いが、登録簿の file を更新時刻まで保って同じ大きさに書き替える運用は無い。
//! 指紋に path を含めるので、作業木が違えば別の指紋(作業木をまたいで分け合わない — 更新時刻が作業木ごとに違う)。

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::de::DeserializeOwned;
use serde::Serialize;

use super::{bulk_read, facts_cache};

/// 更新からこの時間経っていない file は、同じ瞬間の別の書き込みと更新時刻で区別できないので覚えない。
const SETTLE_NS: u128 = 2_000_000_000;

/// `files` の読みの結果 `T` を、`files` の指紋で覚える。指紋が前と同じなら `read` を呼ばずに前の結果を返す。
/// `read` は結果と「覚えてよいか」を返す(根の絶対 path を持つ結果は置き場が断るので、呼び手が先に外す)。
/// 指紋を作れない(更新の直後の file・読めない file・file が無い)時は覚えず、毎回 `read` を呼ぶ。
pub fn remembered<T, F>(root: &Path, kind: &str, files: &[PathBuf], read: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> (T, bool),
{
    match fingerprint(files) {
        Some(key) => facts_cache::keyed_value_if(root, kind, key, read),
        None => read().0,
    }
}

/// file 1 つの印(大きさ・更新時刻 ns・inode)。
fn stamp(path: &Path) -> Option<(u64, u128, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime_ns = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some((meta.len(), mtime_ns, inode(&meta)))
}

#[cfg(unix)]
fn inode(meta: &std::fs::Metadata) -> u64 {
    std::os::unix::fs::MetadataExt::ino(meta)
}

#[cfg(not(unix))]
fn inode(_meta: &std::fs::Metadata) -> u64 {
    0
}

/// `files` 全部の指紋(FNV-1a — 実行と build をまたいで同じ答え)。覚えられない時は None。
fn fingerprint(files: &[PathBuf]) -> Option<u64> {
    if files.is_empty() {
        return None;
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_nanos();
    // 冷えた stat も待ちなので重ねる(冷えた 449 回で 0.02 秒でも、IO の圧迫が重い時は 1 回ずつ待つ)。
    let stamps = bulk_read::fan_out(files, |file| stamp(file));
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    let mut mix = |bytes: &[u8]| {
        for byte in bytes {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
        }
    };
    mix(&(files.len() as u64).to_le_bytes());
    for (file, stamp) in files.iter().zip(stamps) {
        let (len, mtime_ns, inode) = stamp?;
        if mtime_ns.saturating_add(SETTLE_NS) > now {
            return None;
        }
        mix(file.to_string_lossy().as_bytes());
        mix(&[0]);
        mix(&len.to_le_bytes());
        mix(&mtime_ns.to_le_bytes());
        mix(&inode.to_le_bytes());
    }
    Some(hash)
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
        let first = fingerprint(&files);
        assert!(first.is_some());
        assert_eq!(first, fingerprint(&files));
    }

    #[test]
    fn a_change_of_size_time_name_or_set_of_files_changes_the_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let a = aged(dir.path(), "a.txt", "one");
        let b = aged(dir.path(), "b.txt", "two");
        let base = fingerprint(&[a.clone(), b.clone()]).unwrap();
        // 大きさ
        std::fs::write(&b, "three").unwrap();
        set_age(&b, 3600);
        let resized = fingerprint(&[a.clone(), b.clone()]).unwrap();
        assert_ne!(resized, base);
        // 更新時刻だけ(大きさは同じ)
        set_age(&b, 7200);
        let retimed = fingerprint(&[a.clone(), b.clone()]).unwrap();
        assert_ne!(retimed, resized);
        // file の集まり(足す・減らす)
        let c = aged(dir.path(), "c.txt", "x");
        assert_ne!(fingerprint(&[a.clone(), b.clone(), c]).unwrap(), retimed);
        assert_ne!(fingerprint(std::slice::from_ref(&a)).unwrap(), retimed);
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
        assert_ne!(fingerprint(&[a]), fingerprint(&[b]));
    }

    #[test]
    fn a_file_written_a_moment_ago_or_missing_or_no_file_is_not_remembered() {
        let dir = tempfile::tempdir().unwrap();
        let old = aged(dir.path(), "old.txt", "one");
        let fresh = dir.path().join("fresh.txt");
        std::fs::write(&fresh, "now").unwrap();
        assert!(fingerprint(&[old.clone(), fresh]).is_none(), "更新の直後は覚えない");
        assert!(fingerprint(&[old.clone(), dir.path().join("missing.txt")]).is_none(), "読めない file があれば覚えない");
        assert!(fingerprint(&[]).is_none(), "file が無ければ覚えない");
        // 未来の更新時刻(時計のずれ)も、今と区別できないので覚えない。
        let future = dir.path().join("future.txt");
        std::fs::write(&future, "later").unwrap();
        std::fs::File::options().write(true).open(&future).unwrap().set_modified(SystemTime::now() + Duration::from_secs(3600)).unwrap();
        assert!(fingerprint(&[old, future]).is_none());
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

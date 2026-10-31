//! 作業木の git の index が持つ blob の id で、file の中身を読まずに「その中身」を名指す。
//!
//! 冷えた作業木の最初の 1 回は、repo の Hy の約 2900 file を 1 つずつ開いて読む(作業木ごとに別の inode なので、1 file ごとに disk を待つ)。
//! 別の作業木が同じ中身を読んで結果を置いていても、(path・更新時刻)は作業木ごとに違うので引けない。git の index は、checkout の時に各 file の
//! (大きさ・更新時刻・inode)と blob の id(中身の SHA-1)を 1 file にまとめて持っている。今の file の stat が index の行と同じなら、中身は index の
//! blob と同じ(git 自身が `git status` で中身を読まずに使う判断と同じ)ので、blob の id が中身の鍵になり、作業木をまたいで結果を分け合える。
//!
//! 信じない時(全部「中身を読む」へ倒れる — 答えは変わらず遅くなるだけ):
//! - index が読めない・版が 2〜3 でない(版 4 は path を前の行から縮める)・分割 index(`link`)・root が git の作業木の頂上でない。
//! - 行の stat が今の file と違う(大きさ・更新時刻の秒と ns・inode の下位 32 bit)。
//! - 行の更新時刻が index file の更新時刻以後(git の racy と同じ — index を書いた瞬間と同じ刻の書き込みは、stat が同じままでも中身が違いうる)。
//! - 衝突の行(stage が 0 でない)。
//!
//! 置く時は、読んだ中身から git と同じ綴り(`blob <長さ>\0<中身>`)で SHA-1 を作り、index の id と同じ時だけ置く(`blob_id`)。clean の filter や改行の変換が
//! 在る作業木は、index の id が作業木の中身の hash と一致しないので、置かれない(引かれることも無い)。

use std::collections::HashMap;
use std::fs::Metadata;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

/// 根ごとに 1 度だけ読んだ index(読めなかった根は None を覚える)— 1 回の実行の中で、登録簿の読みと repo の Hy の読みが同じ index を 2 度読まない。
static READ: OnceLock<Mutex<HashMap<PathBuf, Option<Arc<GitIndex>>>>> = OnceLock::new();

/// index の行 1 つ(stage 0)。
struct Entry {
    mtime_sec: u32,
    mtime_nsec: u32,
    ino: u32,
    mode: u32,
    size: u32,
    oid: [u8; 20],
}

/// 作業木の index を読んだ物 — (path〔root からの綴り〕・行)を index の並び(path の byte の順)で。
pub struct GitIndex {
    entries: Vec<(String, Entry)>,
    /// index file 自身の更新時刻(秒・ns)。
    stamp: (u64, u32),
    /// index file の名乗り(更新時刻の秒・ns・大きさ・inode)— 読む前の stat から取る。常駐の process(`fsmonitor`)が読んだ index と今の index が同じ物かを比べる。
    ident: Ident,
}

/// index file の名乗り(更新時刻の秒・ns・大きさ・inode)。git は index を書き直す時に新しい file へ rename するので、書き替わると inode と更新時刻が変わる。
pub type Ident = (u64, u32, u64, u64);

impl GitIndex {
    /// `root`(git の作業木の頂上)の index を読む。信じられなければ None。
    pub fn read(root: &Path) -> Option<GitIndex> {
        let path = index_path(root)?;
        let meta = std::fs::metadata(&path).ok()?;
        let bytes = std::fs::read(&path).ok()?;
        let stamp = mtime_of(&meta)?;
        let ident = ident_of(&meta)?;
        Some(GitIndex { entries: parse(&bytes)?, stamp, ident })
    }

    /// 読んだ index file の名乗り。
    pub fn ident(&self) -> Ident {
        self.ident
    }

    /// path が `prefix`(`/` で終わる dir の綴り)で始まる通常の file の行の path — index の並びで。
    pub fn paths_under<'a>(&'a self, prefix: &'a str) -> impl Iterator<Item = &'a str> + 'a {
        let from = self.entries.partition_point(|(path, _)| path.as_str() < prefix);
        self.entries[from..]
            .iter()
            .take_while(move |(path, _)| path.starts_with(prefix))
            .filter(|(_, entry)| entry.mode & 0o170_000 == 0o100_000)
            .map(|(path, _)| path.as_str())
    }

    /// 通常の file の行(mode 100644・100755)の blob の id — file の今の stat は見ない(今の file がその行と同じかは、呼び手が別に知っている)。
    pub fn regular_oid(&self, rel: &str) -> Option<[u8; 20]> {
        let at = self.entries.binary_search_by(|(path, _)| path.as_str().cmp(rel)).ok()?;
        let entry = &self.entries[at].1;
        (entry.mode & 0o170_000 == 0o100_000).then_some(entry.oid)
    }

    /// `root` の index — 1 回の実行の中で根ごとに 1 度だけ読む(前提は `hy_files` と同じ: 実行の間に index は書き替わらないと見る。書き替わっていても、
    /// 行の stat と今の file の比べが外れて「中身を読む」へ倒れるだけ)。並ぶ thread が同時に引いても、読むのは 1 度。
    pub fn of(root: &Path) -> Option<Arc<GitIndex>> {
        let memo = READ.get_or_init(Default::default);
        let mut memo = memo.lock().unwrap_or_else(PoisonError::into_inner);
        memo.entry(root.to_path_buf()).or_insert_with(|| crate::timing::timed("git-index.read", || GitIndex::read(root)).map(Arc::new)).clone()
    }

    /// `rel` の file の stat(`meta`)が index の行と同じなら、その blob の id。
    pub fn blob_of(&self, rel: &str, meta: &Metadata) -> Option<[u8; 20]> {
        // index の行は path の byte の順に並ぶ(git の決まり)。並んでいない壊れた index では引けないだけ — 「中身を読む」へ倒れる。
        let at = self.entries.binary_search_by(|(path, _)| path.as_str().cmp(rel)).ok()?;
        let entry = &self.entries[at].1;
        let (sec, nsec) = mtime_of(meta)?;
        let racy = (u64::from(entry.mtime_sec), entry.mtime_nsec) >= self.stamp;
        let same = u64::from(entry.mtime_sec) == sec && entry.mtime_nsec == nsec && u64::from(entry.size) == (meta.len() & 0xffff_ffff) && entry.ino == inode_of(meta);
        (same && !racy).then_some(entry.oid)
    }

    /// 通常の file(mode 100644・100755)の行の(path・blob の id)を、index の並びで。symlink(120000)と submodule(160000)は含めない
    /// (repo を歩く所は symlink を辿らず、通常の file だけを拾う)。
    pub fn files(&self) -> impl Iterator<Item = (&str, [u8; 20])> {
        self.entries.iter().filter(|(_, entry)| entry.mode & 0o170_000 == 0o100_000).map(|(path, entry)| (path.as_str(), entry.oid))
    }
}

/// index の path — root/.git が dir ならその中、file(`gitdir: <path>`・作業木)ならその指す dir の中。
pub(crate) fn index_path(root: &Path) -> Option<PathBuf> {
    let dot_git = root.join(".git");
    let meta = std::fs::metadata(&dot_git).ok()?;
    if meta.is_dir() {
        return Some(dot_git.join("index"));
    }
    let text = std::fs::read_to_string(&dot_git).ok()?;
    let target = text.lines().find_map(|line| line.strip_prefix("gitdir:"))?.trim();
    let target = Path::new(target);
    Some(if target.is_absolute() { target.join("index") } else { root.join(target).join("index") })
}

/// 名乗り(更新時刻の秒・ns・大きさ・inode)。
#[cfg(unix)]
pub fn ident_of(meta: &Metadata) -> Option<Ident> {
    use std::os::unix::fs::MetadataExt;
    let (sec, nsec) = mtime_of(meta)?;
    Some((sec, nsec, meta.len(), meta.ino()))
}

#[cfg(not(unix))]
pub fn ident_of(_meta: &Metadata) -> Option<Ident> {
    None
}

#[cfg(unix)]
fn mtime_of(meta: &Metadata) -> Option<(u64, u32)> {
    use std::os::unix::fs::MetadataExt;
    Some((u64::try_from(meta.mtime()).ok()?, u32::try_from(meta.mtime_nsec()).ok()?))
}

#[cfg(not(unix))]
fn mtime_of(_meta: &Metadata) -> Option<(u64, u32)> {
    None
}

#[cfg(unix)]
fn inode_of(meta: &Metadata) -> u32 {
    use std::os::unix::fs::MetadataExt;
    (meta.ino() & 0xffff_ffff) as u32
}

#[cfg(not(unix))]
fn inode_of(_meta: &Metadata) -> u32 {
    0
}

fn be32(bytes: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_be_bytes(bytes.get(at..at + 4)?.try_into().ok()?))
}

/// index file(版 2・3)の行を読む(index の並びのまま)。版 4・分割 index は None。
fn parse(bytes: &[u8]) -> Option<Vec<(String, Entry)>> {
    if bytes.get(..4)? != b"DIRC" {
        return None;
    }
    let version = be32(bytes, 4)?;
    if version != 2 && version != 3 {
        return None;
    }
    let count = be32(bytes, 8)? as usize;
    let mut entries = Vec::with_capacity(count);
    let mut at = 12;
    for _ in 0..count {
        let start = at;
        let mtime_sec = be32(bytes, start + 8)?;
        let mtime_nsec = be32(bytes, start + 12)?;
        let ino = be32(bytes, start + 20)?;
        let mode = be32(bytes, start + 24)?;
        let size = be32(bytes, start + 36)?;
        let oid: [u8; 20] = bytes.get(start + 40..start + 60)?.try_into().ok()?;
        let flags = u16::from_be_bytes(bytes.get(start + 60..start + 62)?.try_into().ok()?);
        let mut path_at = start + 62;
        if version == 3 && flags & 0x4000 != 0 {
            path_at += 2;
        }
        let length = bytes.get(path_at..)?.iter().position(|byte| *byte == 0)?;
        let path = std::str::from_utf8(bytes.get(path_at..path_at + length)?).ok()?;
        // 行は 8 byte の倍数に、path の後ろの NUL を 1〜8 個で揃える。
        at = start + ((path_at - start + length + 8) & !7);
        if (flags >> 12) & 3 == 0 {
            entries.push((path.to_string(), Entry { mtime_sec, mtime_nsec, ino, mode, size, oid }));
        }
    }
    // 行の後ろの拡張。分割 index(`link`)は行が別の file にあるので信じない。
    while at + 8 <= bytes.len().saturating_sub(20) {
        let size = be32(bytes, at + 4)? as usize;
        if bytes.get(at..at + 4)? == b"link" {
            return None;
        }
        at += 8 + size;
    }
    Some(entries)
}

/// git の blob の id(`blob <長さ>\0<中身>` の SHA-1)。
pub fn blob_id(content: &[u8]) -> [u8; 20] {
    let mut sha = Sha1::new();
    sha.update(format!("blob {}\0", content.len()).as_bytes());
    sha.update(content);
    sha.finish()
}

/// id の 16 進の綴り。
pub fn hex(oid: &[u8; 20]) -> String {
    oid.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// SHA-1(git の blob の id を作る所でだけ使う — 暗号の強さを頼む使い方ではない。依存を足さないために自前で持つ)。
struct Sha1 {
    state: [u32; 5],
    block: [u8; 64],
    filled: usize,
    length: u64,
}

impl Sha1 {
    fn new() -> Sha1 {
        Sha1 { state: [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0], block: [0; 64], filled: 0, length: 0 }
    }

    fn update(&mut self, mut data: &[u8]) {
        self.length += data.len() as u64;
        while !data.is_empty() {
            let take = (64 - self.filled).min(data.len());
            self.block[self.filled..self.filled + take].copy_from_slice(&data[..take]);
            self.filled += take;
            data = &data[take..];
            if self.filled == 64 {
                let block = self.block;
                self.compress(&block);
                self.filled = 0;
            }
        }
    }

    fn compress(&mut self, block: &[u8; 64]) {
        let mut w = [0u32; 80];
        for (i, chunk) in block.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = self.state;
        for (i, word) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let next = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(*word);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = next;
        }
        for (state, add) in self.state.iter_mut().zip([a, b, c, d, e]) {
            *state = state.wrapping_add(add);
        }
    }

    fn finish(mut self) -> [u8; 20] {
        let bits = self.length * 8;
        self.update(&[0x80]);
        while self.filled != 56 {
            self.update(&[0]);
        }
        self.update(&bits.to_be_bytes());
        let mut out = [0u8; 20];
        for (chunk, word) in out.chunks_exact_mut(4).zip(self.state) {
            chunk.copy_from_slice(&word.to_be_bytes());
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_blob_id_is_the_one_git_makes() {
        // `printf 'hello\n' | git hash-object --stdin` と `git hash-object` の空の file。
        assert_eq!(hex(&blob_id(b"hello\n")), "ce013625030ba8dba906f756967f9e9ca394464a");
        assert_eq!(hex(&blob_id(b"")), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391");
    }

    #[test]
    fn a_content_longer_than_one_block_gets_the_same_id_as_git() {
        // `python3 -c "print('x'*200)" | git hash-object --stdin` を、1 block(64 byte)を何度もまたぐ長さで確かめた値。
        let text = format!("{}\n", "x".repeat(200));
        let expected = std::process::Command::new("git").args(["hash-object", "--stdin"]).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).spawn();
        let Ok(mut child) = expected else { return };
        use std::io::Write;
        child.stdin.take().unwrap().write_all(text.as_bytes()).unwrap();
        let output = child.wait_with_output().unwrap();
        assert_eq!(hex(&blob_id(text.as_bytes())), String::from_utf8_lossy(&output.stdout).trim());
    }

    #[test]
    fn an_index_of_another_version_or_a_broken_one_is_not_trusted() {
        assert!(parse(b"DIRC\0\0\0\x04\0\0\0\0").is_none());
        assert!(parse(b"nope").is_none());
        assert!(parse(b"DIRC\0\0\0\x02\0\0\0\x01").is_none());
    }
}

//! Names "that content" without reading the file's contents, using the blob id held by the working tree's git index.
//!
//! The first run on a cold working tree opens and reads about 2900 Hy files of the repo one by one (each working tree has different inodes, so every file waits on the disk).
//! Even if another working tree has read the same contents and stored the result, (path, mtime) differs per working tree, so it cannot be looked up. The git index holds, for each file at checkout time,
//! (size, mtime, inode) and the blob id (SHA-1 of the contents) in one file. If the current file's stat equals the index row, the contents are the same as the index's
//! blob (the same judgment that git itself uses in `git status` without reading contents), so the blob id becomes the key of the contents and results can be shared across working trees.
//!
//! When it does not trust the index (everything falls back to "read the contents": the answer does not change, it is only slower):
//! - The index cannot be read, the version is not 2-3 (version 4 compresses paths against the previous row), it is a split index (`link`), or the root is not the top of a git working tree.
//! - The row's stat differs from the current file (size, mtime seconds and ns, low 32 bits of the inode).
//! - The row's mtime is at or after the index file's mtime (the same as git's racy case: a write at the same instant the index was written can have different contents even with an identical stat).
//! - A conflict row (stage is not 0).
//!
//! When storing, it builds a SHA-1 from the contents it read, in git's own form (`blob <length>\0<contents>`), and stores only if it equals the index's id (`blob_id`). A working tree with a clean filter or newline conversion
//! does not store, since the index's id does not match the hash of the working tree's contents (and nothing is looked up either).

use std::collections::HashMap;
use std::fs::Metadata;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

/// An index read once per root (a root that could not be read is remembered as None), so that within one run the registry read and the repo Hy read do not read the same index twice.
static READ: OnceLock<Mutex<HashMap<PathBuf, Option<Arc<GitIndex>>>>> = OnceLock::new();

/// One index row (stage 0).
struct Entry {
    mtime_sec: u32,
    mtime_nsec: u32,
    ino: u32,
    mode: u32,
    size: u32,
    oid: [u8; 20],
}

/// The working tree's index as read: (path [spelling from root], row) in index order (byte order of path).
pub struct GitIndex {
    entries: Vec<(String, Entry)>,
    /// Mtime of the index file itself (seconds, ns).
    stamp: (u64, u32),
    /// Identity of the index file (mtime seconds, ns, size, inode), taken from the stat before reading. Used to compare whether the index read by a resident process (`fsmonitor`) is the same as the current index.
    ident: Ident,
}

/// Identity of the index file (mtime seconds, ns, size, inode). git renames a new file into place when it rewrites the index, so a rewrite changes the inode and the mtime.
pub type Ident = (u64, u32, u64, u64);

impl GitIndex {
    /// Reads the index of `root` (the top of the git working tree). None if it cannot be trusted.
    pub fn read(root: &Path) -> Option<GitIndex> {
        let path = index_path(root)?;
        let meta = std::fs::metadata(&path).ok()?;
        let bytes = std::fs::read(&path).ok()?;
        let stamp = mtime_of(&meta)?;
        let ident = ident_of(&meta)?;
        Some(GitIndex { entries: parse(&bytes)?, stamp, ident })
    }

    /// Identity of the index file that was read.
    pub fn ident(&self) -> Ident {
        self.ident
    }

    /// Paths of the regular-file rows whose path starts with `prefix` (a dir spelling ending in `/`), in index order.
    pub fn paths_under<'a>(&'a self, prefix: &'a str) -> impl Iterator<Item = &'a str> + 'a {
        let from = self.entries.partition_point(|(path, _)| path.as_str() < prefix);
        self.entries[from..]
            .iter()
            .take_while(move |(path, _)| path.starts_with(prefix))
            .filter(|(_, entry)| entry.mode & 0o170_000 == 0o100_000)
            .map(|(path, _)| path.as_str())
    }

    /// The blob id of a regular-file row (mode 100644, 100755); it does not look at the file's current stat (whether the current file equals that row is known separately by the caller).
    pub fn regular_oid(&self, rel: &str) -> Option<[u8; 20]> {
        let at = self.entries.binary_search_by(|(path, _)| path.as_str().cmp(rel)).ok()?;
        let entry = &self.entries[at].1;
        (entry.mode & 0o170_000 == 0o100_000).then_some(entry.oid)
    }

    /// The index of `root`: read once per root within one run (the premise is the same as `hy_files`: the index is assumed not to be rewritten during the run. Even if it is rewritten,
    /// the comparison of the row's stat with the current file just misses and falls back to "read the contents"). Even if parallel threads look it up at once, it is read only once.
    pub fn of(root: &Path) -> Option<Arc<GitIndex>> {
        let memo = READ.get_or_init(Default::default);
        let mut memo = memo.lock().unwrap_or_else(PoisonError::into_inner);
        memo.entry(root.to_path_buf()).or_insert_with(|| crate::timing::timed("git-index.read", || GitIndex::read(root)).map(Arc::new)).clone()
    }

    /// The blob id if the stat (`meta`) of the file `rel` equals the index row.
    pub fn blob_of(&self, rel: &str, meta: &Metadata) -> Option<[u8; 20]> {
        // Index rows are in byte order of path (git's rule). A broken index that is not in order just cannot be looked up, falling back to "read the contents".
        let at = self.entries.binary_search_by(|(path, _)| path.as_str().cmp(rel)).ok()?;
        let entry = &self.entries[at].1;
        let (sec, nsec) = mtime_of(meta)?;
        let racy = (u64::from(entry.mtime_sec), entry.mtime_nsec) >= self.stamp;
        let same = u64::from(entry.mtime_sec) == sec && entry.mtime_nsec == nsec && u64::from(entry.size) == (meta.len() & 0xffff_ffff) && entry.ino == inode_of(meta);
        (same && !racy).then_some(entry.oid)
    }

    /// The (path, blob id) of the rows of regular files (mode 100644, 100755), in index order. Symlinks (120000) and submodules (160000) are not included
    /// (the place that walks the repo does not follow symlinks and picks up only regular files).
    pub fn files(&self) -> impl Iterator<Item = (&str, [u8; 20])> {
        self.entries.iter().filter(|(_, entry)| entry.mode & 0o170_000 == 0o100_000).map(|(path, entry)| (path.as_str(), entry.oid))
    }
}

/// Path of the index: inside root/.git if it is a dir; if it is a file (`gitdir: <path>`, a working tree), inside the dir it points to.
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

/// Identity (mtime seconds, ns, size, inode).
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

/// Reads the rows of an index file (versions 2 and 3), in index order as they are. None for version 4 or a split index.
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
        // Rows are padded to a multiple of 8 bytes with 1-8 NULs after the path.
        at = start + ((path_at - start + length + 8) & !7);
        if (flags >> 12) & 3 == 0 {
            entries.push((path.to_string(), Entry { mtime_sec, mtime_nsec, ino, mode, size, oid }));
        }
    }
    // Extensions after the rows. A split index (`link`) is not trusted because its rows are in another file.
    while at + 8 <= bytes.len().saturating_sub(20) {
        let size = be32(bytes, at + 4)? as usize;
        if bytes.get(at..at + 4)? == b"link" {
            return None;
        }
        at += 8 + size;
    }
    Some(entries)
}

/// The git blob id (SHA-1 of `blob <length>\0<contents>`).
pub fn blob_id(content: &[u8]) -> [u8; 20] {
    let mut sha = Sha1::new();
    sha.update(format!("blob {}\0", content.len()).as_bytes());
    sha.update(content);
    sha.finish()
}

/// Hexadecimal spelling of the id.
pub fn hex(oid: &[u8; 20]) -> String {
    oid.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// SHA-1 (used only where the git blob id is built; not meant for cryptographic strength. Kept in-house to avoid adding a dependency).
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
        // `printf 'hello\n' | git hash-object --stdin` and the empty file of `git hash-object`.
        assert_eq!(hex(&blob_id(b"hello\n")), "ce013625030ba8dba906f756967f9e9ca394464a");
        assert_eq!(hex(&blob_id(b"")), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391");
    }

    #[test]
    fn a_content_longer_than_one_block_gets_the_same_id_as_git() {
        // A value checked with `python3 -c "print('x'*200)" | git hash-object --stdin`, at a length that spans one block (64 bytes) many times.
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

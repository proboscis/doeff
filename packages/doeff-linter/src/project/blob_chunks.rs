//! Store and look up per-file facts, keyed by the git index's blob id, as one table per chunk of the index order split into chunks.
//!
//! Where a one-file run looks at all files of the repo (callers of a Program passed as an argument, declarations of failure types, class frozen-ness), the facts of unchanged files are
//! looked up by blob id (without reading the contents). If facts are stored one blob per file, a cold fresh working tree opens as many store files
//! as there are Hy files in the repo (about 4600) just to look them up, waiting on the disk for each (store files, like working-tree files, are evicted from the page cache within minutes).
//! So the index rows (in path byte order) are split into chunks, and each chunk stores a table of (blob id, fact) in one file (about 70 files for agora-controllers' Hy).
//!
//! Chunk boundaries are decided by path alone (`starts_chunk` — cut at a row whose path hash is divisible by `CHUNK_EVERY`, and always cut at `CHUNK_MOST` rows); a chunk's store name is
//! the path at the head of the chunk (`slot`). Changing a file does not change chunk names, and only adds new blob rows to the table, so working trees made from different commits
//! look up the same store, and rows for unchanged files hit as they are. A table holds rows for the current version and for recent past versions (`SLOT_KEEPS` — working trees of old commits hit too).
//!
//! For blobs not in the table, the caller reads the contents, the fact it builds is remembered (`learn_read`), and at the end of the run (`finish`) it is merged with the store's current table and written back. If concurrent runs
//! rewrite the same table at the same time, the later one wins and rows added by the earlier one are dropped (the next run to look them up just re-reads and adds them). Every row is the same
//! fact decided by the blob's contents alone (the caller guarantees this — facts are decided by contents only, reading neither path nor root), so the answer is the same as when reading the contents, whichever version's table is used.

use std::collections::HashMap;
use std::fs::Metadata;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use serde::de::DeserializeOwned;
use serde::Serialize;

use super::facts_cache::BlobFacts;
use super::fsmonitor::{self, Session};
use super::git_index::{self, GitIndex};

/// Target chunk size (number of rows) — cut at a row whose path hash is divisible by this. Number of chunks is file count / this.
const CHUNK_EVERY: u64 = 64;
/// Upper limit on rows in a chunk — cut here even when hash boundaries do not come in succession (so that a chunk to read does not get too large).
const CHUNK_MOST: usize = 256;
/// Upper limit on rows a chunk table holds (a multiple of the chunk's current row count — rows for the current version plus the same number of rows for past versions). The lower limit is `SLOT_KEEPS_AT_LEAST`.
const SLOT_KEEPS: usize = 3;
const SLOT_KEEPS_AT_LEAST: usize = 64;

/// Whether to start a new chunk at row `path` (decided by path alone — FNV-1a; the same answer across runs and builds).
fn starts_chunk(path: &str) -> bool {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in path.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash % CHUNK_EVERY == 0
}

/// Split a sequence of (path, blob id) rows (in index order) into chunks.
fn chunks_of<'a>(files: impl Iterator<Item = (&'a str, [u8; 20])>) -> Vec<Vec<(String, [u8; 20])>> {
    let mut chunks: Vec<Vec<(String, [u8; 20])>> = Vec::new();
    for (path, oid) in files {
        let fresh = match chunks.last() {
            None => true,
            Some(last) => starts_chunk(path) || last.len() >= CHUNK_MOST,
        };
        if fresh {
            chunks.push(Vec::new());
        }
        if let Some(last) = chunks.last_mut() {
            last.push((path.to_string(), oid));
        }
    }
    chunks
}

/// Rows when rewriting a chunk table: in the order rows added in this run, rows of the current chunk, and everything else (past versions), up to `keep` rows (one row per blob).
fn merged<T: Clone>(added: &[([u8; 20], T)], stored: Vec<([u8; 20], T)>, members: &[(String, [u8; 20])], keep: usize) -> Vec<([u8; 20], T)> {
    let current: std::collections::HashSet<[u8; 20]> = members.iter().map(|(_, oid)| *oid).collect();
    let (now, past): (Vec<([u8; 20], T)>, Vec<([u8; 20], T)>) = stored.into_iter().partition(|(oid, _)| current.contains(oid));
    let mut seen = std::collections::HashSet::new();
    added.iter().cloned().chain(now).chain(past).filter(|(oid, _)| seen.insert(*oid)).take(keep).collect()
}

/// Result of looking up one file.
pub enum Lookup<T> {
    /// The fact was found (contents not read).
    Known(T),
    /// The contents match the index's blob, but that blob's fact does not exist yet — the caller reads the contents, builds it, and has it remembered with `learn_read`.
    Unknown([u8; 20]),
    /// A file that diverges from the index (rewritten, not in the index, or a racy row written at the same moment as the index) — the caller reads the contents and builds it (not remembered).
    Untrusted,
}

/// Per-blob facts of one kind, stored per chunk (held only during one run).
pub struct ChunkedFacts<T> {
    facts: BlobFacts,
    index: Arc<GitIndex>,
    /// Answer from the resident process (`fsmonitor`) — looks up whether a file is the same as the index row without stat (otherwise stat per file).
    session: Option<Arc<Session>>,
    chunks: Vec<Vec<(String, [u8; 20])>>,
    /// path -> chunk.
    at: HashMap<String, usize>,
    /// Per-chunk table read from the store (read when first looked up — empty if not in the store).
    loaded: Vec<OnceLock<HashMap<[u8; 20], T>>>,
    /// Facts learned from contents in this run (chunk -> (blob id, fact) of that chunk's files) — added to the table at the end.
    learned: Mutex<HashMap<usize, Vec<([u8; 20], T)>>>,
    /// Number of chunks read from the store and number of blobs not in the table (for instrumentation and tests).
    chunk_reads: AtomicUsize,
    missed: AtomicUsize,
}

impl<T: Clone + Serialize + DeserializeOwned> ChunkedFacts<T> {
    /// Among the regular files in `root`'s index, split those that `select` (path from the root) picks into chunks and open the store of kind `kind`. None when the index cannot be read or
    /// the cache is not used (the caller reads the contents of all files). `select` must pick the same set of files the caller walks and looks at (files not picked do not
    /// enter any chunk, and `lookup` answers that they are not in the table so the caller just reads them every time — the answer does not change).
    pub fn open(root: &Path, kind: &str, select: impl Fn(&str) -> bool) -> Option<Self> {
        let facts = BlobFacts::open(root, kind)?;
        let index = GitIndex::of(root)?;
        let chunks = chunks_of(index.files().filter(|(path, _)| select(path)));
        let at = chunks.iter().enumerate().flat_map(|(c, members)| members.iter().map(move |(path, _)| (path.clone(), c))).collect();
        let loaded = chunks.iter().map(|_| OnceLock::new()).collect();
        Some(ChunkedFacts {
            facts,
            index,
            session: fsmonitor::session(root),
            chunks,
            at,
            loaded,
            learned: Mutex::new(HashMap::new()),
            chunk_reads: AtomicUsize::new(0),
            missed: AtomicUsize::new(0),
        })
    }

    /// Store name of chunk `c` (the path at the head of the chunk).
    fn slot(&self, c: usize) -> &str {
        self.chunks[c].first().map_or("", |(path, _)| path.as_str())
    }

    /// Look up the fact for one file (path from the root `rel`, location `path`). A file the resident process answered "same as the index row" is not stat-ed; otherwise it is stat-ed and
    /// looked up. When stat-ed, its `Metadata` is also returned (`learn_read` uses it to confirm the file did not change while reading).
    pub fn lookup_path(&self, rel: &str, path: &Path) -> Option<(Lookup<T>, Option<Metadata>)> {
        if let Some(oid) = self.session.as_ref().and_then(|session| session.clean_oid(rel)) {
            return Some((self.lookup_oid(rel, oid), None));
        }
        let meta = std::fs::metadata(path).ok()?;
        Some((self.lookup(rel, &meta), Some(meta)))
    }

    /// Look up the fact for one file (path from the root `rel`, current stat `meta`).
    pub fn lookup(&self, rel: &str, meta: &Metadata) -> Lookup<T> {
        let Some(oid) = self.index.blob_of(rel, meta) else { return Lookup::Untrusted };
        self.lookup_oid(rel, oid)
    }

    /// Look up the fact for a file whose contents are known to be the same as blob `oid`.
    fn lookup_oid(&self, rel: &str, oid: [u8; 20]) -> Lookup<T> {
        let known = self.at.get(rel).and_then(|&c| {
            let table = self.loaded[c].get_or_init(|| {
                self.chunk_reads.fetch_add(1, Ordering::Relaxed);
                self.facts.get_slot::<T>(self.slot(c)).map(|rows| rows.into_iter().collect()).unwrap_or_default()
            });
            table.get(&oid).cloned()
        });
        match known {
            Some(value) => Lookup::Known(value),
            None => {
                self.missed.fetch_add(1, Ordering::Relaxed);
                Lookup::Unknown(oid)
            }
        }
    }

    /// Number of chunks read from the store and number of blobs not in the table.
    pub fn reads(&self) -> (usize, usize) {
        (self.chunk_reads.load(Ordering::Relaxed), self.missed.load(Ordering::Relaxed))
    }

    /// For a file `lookup` answered `Unknown(oid)` (path from the root `rel`, `path`, stat at lookup `meta`), remember the fact built from its contents `source` after confirming that the file did not
    /// change while reading and that the contents are the same as the index's blob (they differ in working trees with clean filters or newline conversion). If this cannot be confirmed,
    /// it is not remembered (the answer does not change).
    /// When `meta` is None (a file the resident process answered as the same as the index row), the stat comparison is skipped — the comparison below confirms the contents are the same as the index's blob.
    pub fn learn_read(&self, rel: &str, oid: [u8; 20], path: &Path, meta: Option<&Metadata>, source: &[u8], value: T) {
        let Some(&c) = self.at.get(rel) else { return };
        let unchanged = meta.map_or(true, |meta| std::fs::metadata(path).is_ok_and(|now| now.len() == meta.len() && now.modified().ok() == meta.modified().ok()));
        if unchanged && git_index::blob_id(source) == oid {
            self.learned.lock().unwrap_or_else(PoisonError::into_inner).entry(c).or_default().push((oid, value));
        }
    }

    /// Write back the facts remembered in this run, per chunk, merged with the store's current table (re-read, including rows added by concurrent runs).
    pub fn finish(&self) {
        let learned = std::mem::take(&mut *self.learned.lock().unwrap_or_else(PoisonError::into_inner));
        for (c, added) in learned {
            let members = &self.chunks[c];
            let stored = self.facts.get_slot::<T>(self.slot(c)).unwrap_or_default();
            let keep = (members.len() * SLOT_KEEPS).max(SLOT_KEEPS_AT_LEAST);
            self.facts.put_slot(self.slot(c), &merged(&added, stored, members, keep));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn oid(n: u8) -> [u8; 20] {
        [n; 20]
    }

    /// Chunk boundaries are decided by path alone: adding or removing one row leaves chunks other than that row's chunk with their contents (path and id) unchanged — they can be shared with working trees of other commits.
    #[test]
    fn adding_or_removing_a_file_changes_only_its_own_chunk() {
        let paths: Vec<String> = (0..2000).map(|n| format!("app/m{n:04}.hy")).collect();
        let base = chunks_of(paths.iter().map(|p| (p.as_str(), oid(1))));
        assert!(base.len() >= 10, "not enough test material (chunks {})", base.len());
        assert!(base.iter().all(|members| members.len() <= CHUNK_MOST));
        let added: Vec<String> = paths.iter().cloned().chain(["app/m1000a.hy".to_string()]).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let with = chunks_of(added.iter().map(|p| (p.as_str(), oid(1))));
        let unchanged = with.iter().filter(|members| base.contains(members)).count();
        assert!(unchanged + 1 >= base.len(), "adding one row leaves only {} of {} chunks", unchanged, base.len());
        let removed: Vec<&String> = paths.iter().filter(|p| p.as_str() != "app/m0500.hy").collect();
        let without = chunks_of(removed.iter().map(|p| (p.as_str(), oid(1))));
        assert!(without.iter().filter(|members| base.contains(members)).count() + 1 >= base.len());
    }

    /// When rewriting a table, keep rows in the order added rows, current chunk rows, past-version rows, and drop from the oldest versions at the limit (one row per blob).
    #[test]
    fn a_rewritten_table_keeps_the_added_and_current_rows_before_past_versions() {
        let members = vec![("a.hy".to_string(), oid(1)), ("b.hy".to_string(), oid(2))];
        let stored = vec![(oid(9), "past-9"), (oid(1), "now-1"), (oid(8), "past-8"), (oid(2), "now-2")];
        let added = [(oid(3), "added-3"), (oid(1), "now-1")];
        assert_eq!(merged(&added, stored.clone(), &members, 10), vec![(oid(3), "added-3"), (oid(1), "now-1"), (oid(2), "now-2"), (oid(9), "past-9"), (oid(8), "past-8")]);
        assert_eq!(merged(&added, stored, &members, 4), vec![(oid(3), "added-3"), (oid(1), "now-1"), (oid(2), "now-2"), (oid(9), "past-9")]);
    }

    /// The same sequence of rows splits into the same chunks (same order too) — every row enters exactly one chunk exactly once.
    #[test]
    fn every_file_is_in_exactly_one_chunk_in_index_order() {
        let paths: Vec<String> = (0..700).map(|n| format!("d{}/f{n}.hy", n % 13)).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let chunks = chunks_of(paths.iter().map(|p| (p.as_str(), oid(2))));
        let flat: Vec<&str> = chunks.iter().flatten().map(|(p, _)| p.as_str()).collect();
        assert_eq!(flat, paths.iter().map(String::as_str).collect::<Vec<_>>());
        assert_eq!(chunks, chunks_of(paths.iter().map(|p| (p.as_str(), oid(2)))));
        assert!(chunks_of(std::iter::empty()).is_empty());
    }
}

//! Per-file facts cache.
//!
//! Even in a one-file run (the `--stdin --path` hook right after a write), the rules that need facts about the whole repo (DOEFF126's defk names and
//! DOEFF127 / 129 / 130's effect-inference world) read and re-parsed every Hy file in the repo, which was most of the CPU of one run
//! (measured by the #1022 instrument). For facts determined by one file's contents alone, the facts are placed on disk and
//! only changed files are re-parsed. The answer does not change (the same file contents give the same facts).
//!
//! There are two stores (each working tree used to rebuild everything and write about 3.4GB per hour to zeus's USB SSD):
//! - The facts themselves (`objects/<kind>.<form>/<first 2 chars of hash>/<rest>`): one fact per file, placed under a key determined by contents. The key is
//!   the linter's marker, the file's path from the root (`rel`), and the SHA-256 hash of the contents. Files with the same contents read the same fact even across working trees
//!   (no rebuild even when a checkout makes the mtime newer). Once placed, it is never rewritten (renamed from a temporary file).
//! - The per-root table (`<root dir>/<kind>.<form>.d/<chunk>`): file path -> size and mtime, and the fact key at that time. If the size and mtime are the same as before,
//!   the fact is looked up by the previous key without reading the contents (the quick check). If they differ, the key is rebuilt from the hash of the contents, and if the store has it, it is read.
//!
//! How the files are divided (by responsibility): this file = the public entry, the procedure for looking up one file, and the per-root table /
//! `store.rs` = the fact store (only here can the layout, keys, encoding, mtimes, and fact paths be made) / `sweep.rs` = the store's lifetime (cap, oldest first, vanished roots).
//! Only `sweep.rs` can delete facts (the lookup procedure does not delete: facts are shared across roots, so one root's table cannot decide that one is "not needed").
//!
//! Nothing may move a fact outside its key (the caller upholds this): a fact is determined only by the file's contents, `rel`, and the key the caller passes (`per_file_keyed`),
//! and does not read the root's absolute path or settings. For kinds whose values hold the root's absolute path (the Hy index, signature facts), the stored value does not contain a path (`rel`
//! equivalent or empty), and after reading, the caller attaches the current root's path with `bind`; if a working tree's path stayed in the store, a fact read by another working tree would
//! point at another working tree's files. This promise does not rely on the caller's memory; the place that stores it watches (`store::LeakGuard`: if the current root's absolute path is in the value,
//! it does not store it but rebuilds it, and tells stderr just once).
//!
//! The overall marker: the linter's own binary size and mtime, the version, and the kind of fact. If the linter is rebuilt, the key changes and everything is rebuilt
//! (since how facts are collected may have changed). Facts under an old marker are never read and are deleted by the cap cleanup.
//!
//! The store's root is `$DOEFF_LINTER_CACHE_DIR`, or if absent `$XDG_CACHE_HOME/doeff-linter`, or if absent `~/.cache/doeff-linter`. Under the root,
//! a dir named by the hash of the root's path holds the table, split into SHARDS chunks (`00` onward) decided by the file's path.
//! The root's dir records the root's absolute path (`root.path`), and every hour the store is scanned and dirs whose recorded root no longer exists are deleted
//! (`tend_roots`; dirs with no record are not deleted).
//! Only changed chunks are written for the table (chunks whose rows changed, or chunks containing a vanished file). A run right after changing one file used to rewrite the whole repo's facts
//! (about 0.35 seconds on zeus). Reading reads all chunks together. The previous forms (facts inside chunks, and
//! the one-file form) are not read: an unreadable chunk is treated as absent and is only rebuilt, so the answer does not change.
//! Writes rename from a temporary file (so concurrent hooks do not corrupt each other: the later writer just wins, and both are correct facts).
//! If `DOEFF_LINTER_NO_CACHE` is set, nothing is read or written. Unreadable or broken chunks and facts are treated as absent (that file's facts are re-parsed;
//! the answer does not change).
//!
//! There are two forms of facts: the default is JSON (`<kind>.json`). Large kinds, such as the whole repo's Hy index, are placed in binary without field names (bincode,
//! `<kind>.bin`; `per_file_compact`; JSON was 147MB on agora's main line and took 1.2 seconds to read).
//! The binary form's spelling is just the sequence of the value's fields, so the caller upholds that the value type's serde form never emits or omits a field depending on the value
//! (that is, it has no `skip_serializing_if`). The table is binary regardless of form.

use rayon::prelude::*;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

mod store;
mod sweep;

use store::{object_key, read_object, write_object, Format, LeakGuard, ObjectKey, OBJECTS_DIR};
use sweep::{note_object_bytes, tend_roots};

pub use sweep::{mark_used, run_handed_sweep, SWEEP_COMMAND, SWEEP_INLINE_ENV};

const NO_CACHE_ENV: &str = "DOEFF_LINTER_NO_CACHE";
const DIR_ENV: &str = "DOEFF_LINTER_CACHE_DIR";
/// Number of table chunks per kind (header comment): rewriting after one file's change touches about 1/SHARDS, and a read opens SHARDS per kind.
const SHARDS: usize = 16;

/// The chunk that holds a file's path from the root (FNV-1a: the same answer across runs and builds).
fn shard_of(rel: &str) -> usize {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in rel.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    (hash % SHARDS as u64) as usize
}

/// From the kind's table store file (the one-file path of the previous form), the path of chunk n (`<store>.d/<n>`).
fn shard_path(file: &Path, n: usize) -> PathBuf {
    let name = file.file_name().and_then(|n| n.to_str()).unwrap_or("facts");
    file.with_file_name(format!("{name}.d")).join(format!("{n:02}"))
}

/// The quick-check key of one file (size, mtime ns).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
struct Stamp {
    len: u64,
    mtime_ns: u128,
}

fn stamp_of(path: &Path) -> Option<Stamp> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime_ns = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some(Stamp { len: meta.len(), mtime_ns })
}

/// One row of the per-root table.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Row {
    rel: String,
    stamp: Stamp,
    /// None = a file with nothing to collect (e.g. it has no definitions at all); the absence is remembered too and not re-read.
    object: Option<ObjectKey>,
}

#[derive(Serialize, Deserialize)]
struct Index {
    identity: String,
    rows: Vec<Row>,
}

/// The shape of `Index` when writing (borrows rows without copying them; the spelling is the same as `Index`).
#[derive(Serialize)]
struct IndexRef<'a> {
    identity: &'a str,
    rows: &'a [&'a Row],
}

/// The linter's own marker (binary size and mtime, version) and the kind of fact.
fn identity(kind: &str) -> String {
    let exe = std::env::current_exe().ok().and_then(|p| stamp_of(&p));
    let exe = exe.map(|s| format!("{}-{}", s.len, s.mtime_ns)).unwrap_or_else(|| "unknown".to_string());
    format!("{kind}/{}/{exe}", env!("CARGO_PKG_VERSION"))
}

/// The store of one kind: the per-root table (the one-file path of the previous form; chunks are `shard_path`) and the dir of facts shared across roots.
struct Place {
    index: PathBuf,
    objects: PathBuf,
    /// A watch that no value holding the root's absolute path is stored (`store::LeakGuard`).
    guard: LeakGuard,
}

/// The store's root (header comment: `$DOEFF_LINTER_CACHE_DIR`, `$XDG_CACHE_HOME/doeff-linter`, `~/.cache/doeff-linter`, in that order).
/// None if `DOEFF_LINTER_NO_CACHE` is set. Every dir directly under the root is subject to the cap cleanup (sweep_over_cap); a store outside the root's dirs
/// (the commit hook's result for the HEAD tree) also falls under the same cap if it places a dir directly under the root and sets the `used` marker (mark_used).
/// The fact store `objects` is not deleted per dir; facts are deleted one by one, oldest last-used time first.
pub fn cache_base() -> Option<PathBuf> {
    if std::env::var_os(NO_CACHE_ENV).is_some() {
        return None;
    }
    std::env::var_os(DIR_ENV)
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("XDG_CACHE_HOME").map(|d| PathBuf::from(d).join("doeff-linter")))
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache").join("doeff-linter")))
}

fn place(root: &Path, kind: &str, format: Format) -> Option<Place> {
    if cfg!(test) {
        return None;
    }
    let base = cache_base()?;
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    root.hash(&mut hasher);
    let root_dir = base.join(format!("{:016x}", hasher.finish()));
    crate::timing::timed("facts-cache.roots", || tend_roots(&base, &root_dir, root));
    let name = format!("{kind}.{}", format.extension());
    Some(Place { index: root_dir.join(&name), objects: base.join(OBJECTS_DIR).join(name), guard: LeakGuard::of(root) })
}

fn read_index_shard(file: &Path, identity: &str) -> Vec<Row> {
    let Ok(bytes) = std::fs::read(file) else { return Vec::new() };
    match bincode::deserialize::<Index>(&bytes) {
        Ok(index) if index.identity == identity => index.rows,
        _ => Vec::new(),
    }
}

/// Reads all chunks together into one table (unreadable chunks and chunks with a different marker are treated as absent: that chunk's files are looked up again).
fn load_index(file: &Path, identity: &str) -> HashMap<String, Row> {
    let shards: Vec<Vec<Row>> = (0..SHARDS).into_par_iter().map(|n| read_index_shard(&shard_path(file, n), identity)).collect();
    shards.into_iter().flatten().map(|row| (row.rel.clone(), row)).collect()
}

/// Writes one table chunk. If there are no rows, deletes the chunk's file (so no chunk of the previous form remains).
fn save_index_shard(file: &Path, identity: &str, rows: &[&Row]) {
    let Some(dir) = file.parent() else { return };
    if rows.is_empty() {
        let _ = std::fs::remove_file(file);
        return;
    }
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    let Ok(bytes) = bincode::serialize(&IndexRef { identity, rows }) else { return };
    let tmp = dir.join(format!(".{}.{}.tmp", file.file_name().and_then(|n| n.to_str()).unwrap_or("facts"), std::process::id()));
    if std::fs::write(&tmp, bytes).is_ok() && std::fs::rename(&tmp, file).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// Rebuilds, with `compute`, the facts of `files` (path from the root and absolute path) for only the changed files, and returns them (in `files` order;
/// files with nothing to collect are not included). `compute` must determine the fact from that file's contents and `rel` alone (it reads no other file and not the root's absolute path).
pub fn per_file<T, F>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_keyed(root, kind, "", files, compute)
}

/// Same as `per_file`, but for when the fact depends on `key` as well as the file's contents (if `key` changes, everything is rebuilt).
pub fn per_file_keyed<T, F>(root: &Path, kind: &str, key: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_at(place(root, kind, Format::Json).as_ref(), &format!("{}/{key}", identity(kind)), files, compute, no_bind, Format::Json)
}

/// Same as `per_file`, but the cache is placed in binary form without field names (header comment; for large kinds).
pub fn per_file_compact<T, F>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_compact_bound(root, kind, files, compute, no_bind)
}

/// Same as `per_file_compact`, but for kinds whose facts hold the root's absolute path. `compute` returns a fact without a path (a form with no root absolute path), and
/// before returning (both when read from the store and when built), attaches the current root's path with `bind(fact, rel, absolute path)`. The stored value is the form before `bind`
/// (header comment: if a working tree's path stayed in the store, another working tree would point at another working tree's files).
pub fn per_file_compact_bound<T, F, B>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F, bind: B) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    per_file_at(place(root, kind, Format::Compact).as_ref(), &format!("{}/", identity(kind)), files, compute, bind, Format::Compact)
}

fn no_bind<T>(_value: &mut T, _rel: &str, _path: &Path) {}

/// The range over which facts are looked up: look up the whole repo's files through the table (a whole run), or look up a few files by the hash of their contents (a one-file run, `per_file_named`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reach {
    Whole,
    Named,
}

/// Depending on `reach`, look up with `per_file_keyed` (whole) or `per_file_named` (named), so that the caller holds only one way of building facts.
pub fn per_file_in<T, F>(reach: Reach, root: &Path, kind: &str, key: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    match reach {
        Reach::Whole => per_file_keyed(root, kind, key, files, compute),
        Reach::Named => per_file_named(root, kind, key, files, compute),
    }
}

/// Looks up the same facts as `per_file_keyed`, for **only a few files** and without going through the table (a one-file run reads only the files the written file names).
/// It neither reads nor writes the whole repo's table (16 chunks per root, mtime comparison per file), and looks up facts by the hash of the file's contents (the store's facts use
/// the same keys as `per_file_keyed`: this reads facts a whole run stored, and a whole run reads facts stored here). It adds no rows to the table, so the next whole
/// run puts this file in the table again (the answer does not change). Returns in `files` order, excluding files with nothing to collect.
pub fn per_file_named<T, F>(root: &Path, kind: &str, key: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_named_at(place(root, kind, Format::Json).as_ref(), &format!("{}/{key}", identity(kind)), files, compute, no_bind, Format::Json)
}

/// The binary form of `per_file_named`, for kinds that hold the root's absolute path (the same promise as `per_file_compact_bound`: `compute` returns a fact without a path, and
/// before returning, `bind` attaches the current root's path).
pub fn per_file_compact_named_bound<T, F, B>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F, bind: B) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    per_file_named_at(place(root, kind, Format::Compact).as_ref(), &format!("{}/", identity(kind)), files, compute, bind, Format::Compact)
}

/// Looks up one file's fact by the hash of its contents (the half of `resolve` that does not use table rows). If the store has it, reads it; otherwise builds it with `compute` and stores it.
/// If the file changed between reading the contents and building, the key and the fact would disagree, so it is not stored (the answer is the file as of that time).
fn resolve_by_content<T, F>(place: &Place, identity: &str, rel: &str, path: &Path, compute: &F, format: Format) -> (Option<T>, u64)
where
    T: Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T>,
{
    let Some(stamp) = stamp_of(path) else { return (None, 0) };
    let Ok(bytes) = std::fs::read(path) else { return (compute(rel, path), 0) };
    let digest = Sha256::digest(&bytes);
    drop(bytes);
    let key = object_key(&[identity.as_bytes(), rel.as_bytes(), digest.as_slice()]);
    if let Some(value) = read_object::<T>(&place.objects, &key, format) {
        return (Some(value), 0);
    }
    let value = compute(rel, path);
    if stamp_of(path) != Some(stamp) {
        return (value, 0);
    }
    let written = value.as_ref().map_or(0, |value| write_object(&place.objects, &key, value, format, &place.guard));
    (value, written)
}

/// The body of `per_file_named`: takes the cache's store, marker, and form (store None = do not use the cache).
fn per_file_named_at<T, F, B>(place: Option<&Place>, identity: &str, files: &[(String, PathBuf)], compute: F, bind: B, format: Format) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    let bound = |rel: &str, path: &Path, value: Option<T>| {
        value.map(|mut value| {
            bind(&mut value, rel, path);
            value
        })
    };
    let Some(place) = place else {
        return files.par_iter().filter_map(|(rel, path)| bound(rel, path, compute(rel, path))).collect();
    };
    let resolved: Vec<(Option<T>, u64)> =
        crate::timing::timed("facts-cache.named", || files.par_iter().map(|(rel, path)| resolve_by_content(place, identity, rel, path, &compute, format)).collect());
    note_object_bytes(resolved.iter().map(|(_, written)| *written).sum());
    files.iter().zip(resolved).filter_map(|((rel, path), (value, _))| bound(rel, path, value)).collect()
}

/// The form of `per_file_named` for **files whose contents have already been read**: it looks up the contents that a one-file run read while walking the repo's Hy, without re-reading from disk
/// (re-reading would add one more open and read per file). The store's key is the same as `per_file_keyed` (marker, `key`, `rel`, SHA-256 of contents), so a whole run reads
/// facts stored here and this reads facts a whole run stored. `contents` is a list of (path from the root, contents). The result is in `contents` order, and a file for which `compute` returned None is None
/// (not stored: since it does not go through the table, there is no row to remember a file with nothing to collect. When the caller wants it remembered with an empty fact, return `Some(empty)`).
pub fn per_file_content<T, F>(root: &Path, kind: &str, key: &str, contents: &[(&str, &str)], compute: F) -> Vec<Option<T>>
where
    T: Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &str) -> Option<T> + Sync,
{
    let place = place(root, kind, Format::Json);
    let identity = format!("{}/{key}", identity(kind));
    let resolved: Vec<(Option<T>, u64)> = contents
        .par_iter()
        .map(|(rel, source)| {
            let Some(place) = place.as_ref() else { return (compute(rel, source), 0) };
            let digest = Sha256::digest(source.as_bytes());
            let object = object_key(&[identity.as_bytes(), rel.as_bytes(), digest.as_slice()]);
            if let Some(value) = read_object::<T>(&place.objects, &object, Format::Json) {
                return (Some(value), 0);
            }
            let value = compute(rel, source);
            let written = value.as_ref().map_or(0, |value| write_object(&place.objects, &object, value, Format::Json, &place.guard));
            (value, written)
        })
        .collect();
    note_object_bytes(resolved.iter().map(|(_, written)| *written).sum());
    resolved.into_iter().map(|(value, _)| value).collect()
}

/// A fact store whose contents are decided by the git blob id: looks up facts that another working tree stored, without reading the contents.
/// Facts are stored as tables of (blob id, fact) per chunk (`blob_chunks`). The key is the linter's marker, the kind, and the chunk's name. The caller upholds that facts are determined by the blob's contents alone (neither `rel` nor the root is
/// read). The store and the cap cleanup are the same as for other facts.
pub struct BlobFacts {
    place: Place,
    identity: String,
}

impl BlobFacts {
    /// Opens the store of `kind` for `root`. None when the cache is not used (`DOEFF_LINTER_NO_CACHE`, tests).
    pub fn open(root: &Path, kind: &str) -> Option<BlobFacts> {
        Some(BlobFacts { place: place(root, kind, Format::Compact)?, identity: format!("{}/", identity(kind)) })
    }

    /// The key of a chunk's store (name `slot`: the caller decides it by the delimiters of the index order).
    fn slot_key(&self, slot: &str) -> ObjectKey {
        object_key(&[self.identity.as_bytes(), b"slot", slot.as_bytes()])
    }

    /// Reads the (blob id, fact) table of a chunk's store (None if absent or broken). Storing one fact per file would open as many store files as the repo's Hy count (about 4600) on a cold new working tree
    /// and wait on the disk, so facts are gathered into one file per chunk of the index order and looked up that way (`blob_chunks`).
    pub fn get_slot<T: DeserializeOwned>(&self, slot: &str) -> Option<Vec<([u8; 20], T)>> {
        read_object::<Vec<([u8; 20], T)>>(&self.place.objects, &self.slot_key(slot), Format::Compact)
    }

    /// Stores the table of a chunk's store again. Facts are determined by the blob's contents alone, so any row of the table, whenever it was stored, holds the same fact for the same blob. Within the store's facts,
    /// this is the only kind where the contents under the same key change (rows are added and rows of old versions drop out) (it renames from a temporary file, so a reader reads the whole previous or the whole next table).
    pub fn put_slot<T: Serialize>(&self, slot: &str, entries: &[([u8; 20], T)]) {
        note_object_bytes(write_object(&self.place.objects, &self.slot_key(slot), &entries, Format::Compact, &self.place.guard));
    }
}

/// Remembers one value under a key `key` that the caller built from its inputs (not per file: a value derived from the whole repo's facts, e.g. the fixed point of effect inference).
/// If the stored value's key equals `key`, reads it; otherwise (absent, broken, or the linter was rebuilt) builds it with `compute` and stores it. The answer does not change:
/// the caller upholds that the key covers all the inputs. Values are shared across roots (only values that do not hold the root's absolute path). The form is binary (bincode).
pub fn keyed_value<T, F>(root: &Path, kind: &str, key: u64, compute: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> T,
{
    keyed_value_if(root, kind, key, || (compute(), true))
}

/// The form of `keyed_value` that stores only when allowed: `compute` returns the value and "whether it may be stored" (the caller drops, beforehand, values the store would refuse, such as a value
/// that holds the root's absolute path; when the store refuses, it tells stderr once, so a value known to be refused is not tried). A value not stored is also returned as the answer.
pub fn keyed_value_if<T, F>(root: &Path, kind: &str, key: u64, compute: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> (T, bool),
{
    let Some(place) = place(root, kind, Format::Compact) else { return compute().0 };
    let identity = format!("{}/", identity(kind));
    let object = object_key(&[identity.as_bytes(), b"keyed-value", &key.to_le_bytes()]);
    let cached = crate::timing::timed("facts-cache.load", || read_object::<T>(&place.objects, &object, Format::Compact));
    if let Some(value) = cached {
        return value;
    }
    let (value, keep) = compute();
    if keep {
        let written = crate::timing::timed("facts-cache.save", || write_object(&place.objects, &object, &value, Format::Compact, &place.guard));
        note_object_bytes(written);
    }
    value
}

/// The result of looking up one file: the fact, the table row, whether to rewrite the table chunk, and the bytes of any newly stored fact.
struct Resolved<T> {
    value: Option<T>,
    /// None = not remembered (an unreadable file, or a file that changed while being read).
    row: Option<Row>,
    changed: bool,
    written: u64,
}

/// Looks up one file's fact. If the table row's size and mtime equal the current ones, reads the fact by the row's key without reading the contents. Otherwise builds a key from the hash of the contents,
/// reads it if the store has it (a fact another working tree stored), and otherwise builds it with `compute` and stores it.
fn resolve<T, F>(place: &Place, identity: &str, rel: &str, path: &Path, cached: Option<&Row>, compute: &F, format: Format) -> Resolved<T>
where
    T: Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T>,
{
    let skipped = || Resolved { value: None, row: None, changed: cached.is_some(), written: 0 };
    let Some(stamp) = stamp_of(path) else { return skipped() };
    if let Some(row) = cached.filter(|row| row.stamp == stamp) {
        match &row.object {
            None => return Resolved { value: None, row: Some(row.clone()), changed: false, written: 0 },
            Some(key) => {
                if let Some(value) = read_object::<T>(&place.objects, key, format) {
                    return Resolved { value: Some(value), row: Some(row.clone()), changed: false, written: 0 };
                }
                // The fact is absent or broken (deleted by the cap cleanup): look it up again from the contents
            }
        }
    }
    let Ok(bytes) = std::fs::read(path) else {
        // An unreadable file is not remembered; only `compute`'s answer is returned (`compute` decides the answer for an unreadable file)
        return Resolved { value: compute(rel, path), row: None, changed: cached.is_some(), written: 0 };
    };
    let digest = Sha256::digest(&bytes);
    drop(bytes);
    let key = object_key(&[identity.as_bytes(), rel.as_bytes(), digest.as_slice()]);
    let (value, written) = match read_object::<T>(&place.objects, &key, format) {
        Some(value) => (Some(value), 0),
        None => {
            let value = compute(rel, path);
            // If the file changed between reading the contents and building, the key and the fact would disagree, so it is not remembered (the answer is the file as of that time).
            if stamp_of(path) != Some(stamp) {
                return Resolved { value, row: None, changed: cached.is_some(), written: 0 };
            }
            let written = value.as_ref().map_or(0, |value| write_object(&place.objects, &key, value, format, &place.guard));
            (value, written)
        }
    };
    let row = Row { rel: rel.to_string(), stamp, object: value.is_some().then_some(key) };
    let changed = cached != Some(&row);
    Resolved { value, row: Some(row), changed, written }
}

/// The body of `per_file`: takes the cache's store, marker, and form (store None = do not use the cache).
fn per_file_at<T, F, B>(place: Option<&Place>, identity: &str, files: &[(String, PathBuf)], compute: F, bind: B, format: Format) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    let bound = |rel: &str, path: &Path, value: Option<T>| {
        value.map(|mut value| {
            bind(&mut value, rel, path);
            value
        })
    };
    let Some(place) = place else {
        return files.par_iter().filter_map(|(rel, path)| bound(rel, path, compute(rel, path))).collect();
    };
    let known: HashMap<String, Row> = crate::timing::timed("facts-cache.load", || load_index(&place.index, identity));
    let resolved: Vec<Resolved<T>> = crate::timing::timed("facts-cache.resolve", || {
        files.par_iter().map(|(rel, path)| resolve(place, identity, rel, path, known.get(rel), &compute, format)).collect()
    });
    note_object_bytes(resolved.iter().map(|r| r.written).sum());
    // Chunks to rewrite = the chunks of files whose rows changed, plus the chunks of files that were in the table but are now absent (vanished or unreadable).
    let mut dirty = [false; SHARDS];
    for ((rel, _), one) in files.iter().zip(&resolved) {
        if one.changed {
            dirty[shard_of(rel)] = true;
        }
    }
    let present: HashSet<&str> = resolved.iter().filter_map(|one| one.row.as_ref().map(|row| row.rel.as_str())).collect();
    for rel in known.keys().filter(|rel| !present.contains(rel.as_str())) {
        dirty[shard_of(rel)] = true;
    }
    if dirty.iter().any(|d| *d) {
        crate::timing::timed("facts-cache.save", || {
            let mut by_shard: Vec<Vec<&Row>> = (0..SHARDS).map(|_| Vec::new()).collect();
            for row in resolved.iter().filter_map(|one| one.row.as_ref()) {
                by_shard[shard_of(&row.rel)].push(row);
            }
            // The file of a chunk with no rows is deleted even if it is not a chunk to rewrite (so a chunk of the previous form does not remain without holding facts).
            by_shard
                .par_iter()
                .enumerate()
                .filter(|(n, rows)| dirty[*n] || rows.is_empty())
                .for_each(|(n, rows)| save_index_shard(&shard_path(&place.index, n), identity, rows));
            // The one-file form of the previous form is not read, so it is deleted (header comment: the store is only the cache the linter owns).
            let _ = std::fs::remove_file(&place.index);
        });
    }
    files.par_iter().zip(resolved.into_par_iter()).filter_map(|((rel, path), one)| bound(rel, path, one.value)).collect()
}



#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::SystemTime;

    /// The table of root `root` and the fact store shared across roots (`dir/objects`; the kind is `kind`).
    fn place_of(dir: &Path, root: &str, format: Format) -> Place {
        let name = format!("kind.{}", format.extension());
        Place { index: dir.join(root).join(&name), objects: dir.join(OBJECTS_DIR).join(name), guard: LeakGuard::of(dir) }
    }

    fn run(place: &Place, identity: &str, files: &[(String, PathBuf)], calls: &AtomicUsize, format: Format) -> Vec<String> {
        per_file_at(
            Some(place),
            identity,
            files,
            |_rel, path| {
                calls.fetch_add(1, Ordering::SeqCst);
                let text = std::fs::read_to_string(path).ok()?;
                (!text.is_empty()).then_some(text)
            },
            no_bind,
            format,
        )
    }

    /// Total size of the files under dir (including temporary files; counts the bytes stored).
    fn bytes_under(dir: &Path) -> u64 {
        walkdir::WalkDir::new(dir).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()).map(|e| e.metadata().unwrap().len()).sum()
    }

    fn set_mtime(path: &Path, at: SystemTime) {
        std::fs::File::options().write(true).open(path).unwrap().set_modified(at).unwrap();
    }

    /// Only changed files are re-read and the answer does not change; the same scenario regardless of form (JSON or binary).
    fn only_changed_files_are_read_again(format: Format) {
        let dir = tempfile::tempdir().unwrap();
        let (a, b, empty) = (dir.path().join("a.hy"), dir.path().join("b.hy"), dir.path().join("c.hy"));
        std::fs::write(&a, "(defk a [])").unwrap();
        std::fs::write(&b, "(defk b [])").unwrap();
        std::fs::write(&empty, "").unwrap();
        let files = vec![("a.hy".to_string(), a.clone()), ("b.hy".to_string(), b.clone()), ("c.hy".to_string(), empty)];
        let cache = place_of(dir.path(), "cache", format);
        let calls = AtomicUsize::new(0);
        let first = run(&cache, "v1", &files, &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // If nothing changed, nothing is re-read and the answer is the same (files with nothing to collect are remembered too).
        assert_eq!(run(&cache, "v1", &files, &calls, format), first);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
        // If one file's contents change, only that file is re-read and the new fact is returned.
        std::fs::write(&b, "(defk b-changed [])").unwrap();
        assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string(), "(defk b-changed [])".to_string()]);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // If the linter's marker changes, everything is re-read.
        run(&cache, "v2", &files, &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // A vanished file remains in neither the answer nor the cache.
        std::fs::remove_file(&a).unwrap();
        assert_eq!(run(&cache, "v2", &files, &calls, format), vec!["(defk b-changed [])".to_string()]);
    }

    #[test]
    fn only_changed_files_are_read_again_and_the_answer_is_the_same() {
        only_changed_files_are_read_again(Format::Json);
    }

    #[test]
    fn the_compact_form_reads_only_changed_files_again_too() {
        only_changed_files_are_read_again(Format::Compact);
    }

    #[test]
    fn a_broken_index_or_fact_is_read_as_absent() {
        for (format, broken) in [(Format::Json, &b"{ not json"[..]), (Format::Compact, &b"\x01\x02"[..])] {
            let dir = tempfile::tempdir().unwrap();
            let a = dir.path().join("a.hy");
            std::fs::write(&a, "(defk a [])").unwrap();
            let files = vec![("a.hy".to_string(), a)];
            let cache = place_of(dir.path(), "cache", format);
            // A broken table chunk
            let shard = shard_path(&cache.index, shard_of("a.hy"));
            std::fs::create_dir_all(shard.parent().unwrap()).unwrap();
            std::fs::write(&shard, broken).unwrap();
            let calls = AtomicUsize::new(0);
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
            // A broken fact (the table is correct): rebuild only that file and store it again (the next run does not re-read it)
            let fact = walkdir::WalkDir::new(&cache.objects).into_iter().filter_map(Result::ok).find(|e| e.file_type().is_file()).unwrap();
            std::fs::write(fact.path(), broken).unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
        }
    }

    /// When one file changes, the table rewritten is only that file's chunk; other chunks' files are not written (their mtime does not change).
    /// The chunk of a vanished file is rewritten too, and the one-file form of the previous form is deleted.
    #[test]
    fn only_the_shard_of_a_changed_file_is_written_again() {
        for format in [Format::Json, Format::Compact] {
            let dir = tempfile::tempdir().unwrap();
            // Pick two files in different chunks.
            let names: Vec<String> = (0..64).map(|i| format!("f{i}.hy")).collect();
            let first = names[0].clone();
            let other = names.iter().find(|n| shard_of(n) != shard_of(&first)).unwrap().clone();
            let files: Vec<(String, PathBuf)> = [&first, &other]
                .iter()
                .map(|n| {
                    let path = dir.path().join(n.as_str());
                    std::fs::write(&path, format!("(defk {n} [])")).unwrap();
                    (n.to_string(), path)
                })
                .collect();
            let cache = place_of(dir.path(), "cache", format);
            std::fs::create_dir_all(cache.index.parent().unwrap()).unwrap();
            std::fs::write(&cache.index, b"old single file").unwrap();
            let calls = AtomicUsize::new(0);
            run(&cache, "v1", &files, &calls, format);
            assert!(!cache.index.exists(), "the one-file form of the previous form remained");
            let modified = |n: &str| std::fs::metadata(shard_path(&cache.index, shard_of(n))).unwrap().modified().unwrap();
            let (first_at, other_at) = (modified(&first), modified(&other));
            std::thread::sleep(std::time::Duration::from_millis(20));
            std::fs::write(&files[0].1, "(defk changed [])").unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk changed [])".to_string(), format!("(defk {other} [])")]);
            assert_ne!(modified(&first), first_at, "the changed file's chunk was not rewritten");
            assert_eq!(modified(&other), other_at, "even the chunk of an unchanged file was rewritten");
            // The chunk of a vanished file is rewritten (it is still gone when read back).
            std::thread::sleep(std::time::Duration::from_millis(20));
            std::fs::remove_file(&files[1].1).unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk changed [])".to_string()]);
            assert!(!shard_path(&cache.index, shard_of(&other)).exists(), "the chunk file with no remaining rows remained");
        }
    }

    /// A file with the same contents and the same `rel` does not rebuild its fact even in another root (another working tree with a newer mtime); what is newly written is
    /// only the per-root table (no facts at all).
    #[test]
    fn a_second_root_with_the_same_contents_reads_the_facts_the_first_wrote() {
        for format in [Format::Json, Format::Compact] {
            let dir = tempfile::tempdir().unwrap();
            let cache_dir = dir.path().join("cache");
            let tree = |name: &str| -> Vec<(String, PathBuf)> {
                (0..24)
                    .map(|i| {
                        let path = dir.path().join(name).join(format!("m{i}.hy"));
                        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                        std::fs::write(&path, format!("(defk m{i} [] {})", "(body x)".repeat(500))).unwrap();
                        (format!("m{i}.hy"), path)
                    })
                    .collect()
            };
            let (one, two) = (tree("one"), tree("two"));
            // The second tree has the same contents and only a different mtime (a working tree that was checked out again).
            for (_, path) in &two {
                set_mtime(path, SystemTime::now() + std::time::Duration::from_secs(5));
            }
            let calls = AtomicUsize::new(0);
            let first_place = place_of(&cache_dir, "root-one", format);
            let answer = run(&first_place, "v1", &one, &calls, format);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 24);
            let written_first = bytes_under(&cache_dir);
            let objects_before = bytes_under(&first_place.objects);
            let second_place = place_of(&cache_dir, "root-two", format);
            assert_eq!(run(&second_place, "v1", &two, &calls, format), answer);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 0, "rebuilt the fact of a file with the same contents");
            assert_eq!(bytes_under(&second_place.objects), objects_before, "stored a fact of the same contents again");
            let written_second = bytes_under(&cache_dir) - written_first;
            assert!(written_second * 10 < written_first, "the second root newly wrote {written_second} bytes, which is 10% or more of the first's {written_first}");
        }
    }

    /// A fact's key is the marker, `rel`, and the contents: if any differs, nothing is shared.
    #[test]
    fn facts_are_shared_only_for_the_same_name_contents_and_identity() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("x.hy");
        std::fs::write(&path, "(defk x [])").unwrap();
        let calls = AtomicUsize::new(0);
        let format = Format::Json;
        run(&place_of(dir.path(), "r1", format), "v1", &[("x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // rel differs (the module name changes; a fact is determined by rel too)
        run(&place_of(dir.path(), "r2", format), "v1", &[("other/x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // the marker differs (linter rebuilt, key)
        run(&place_of(dir.path(), "r3", format), "v2", &[("x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // all the same: not re-read even from another root's table
        run(&place_of(dir.path(), "r4", format), "v1", &[("x.hy".to_string(), path)], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
    }

    /// A fact that holds the root's absolute path stores a value without a path and attaches the current root's path after reading: no working tree's path remains in the store,
    /// and when another working tree reads it, it points at its own files.
    #[test]
    fn a_bound_path_is_attached_after_reading_and_never_stored() {
        #[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
        struct Fact {
            path: String,
            text: String,
        }
        let dir = tempfile::tempdir().unwrap();
        let cache_dir = dir.path().join("cache");
        let format = Format::Compact;
        let tree = |name: &str| -> Vec<(String, PathBuf)> {
            let path = dir.path().join(name).join("a.hy");
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, "(defk a [])").unwrap();
            vec![("a.hy".to_string(), path)]
        };
        let read = |name: &str, files: &[(String, PathBuf)]| {
            per_file_at(
                Some(&place_of(&cache_dir, name, format)),
                "v1",
                files,
                |_rel, path| Some(Fact { path: String::new(), text: std::fs::read_to_string(path).ok()? }),
                |fact, _rel, path| fact.path = path.to_string_lossy().into_owned(),
                format,
            )
        };
        let (one, two) = (tree("one"), tree("two"));
        let first = read("root-one", &one);
        assert_eq!(first[0].path, one[0].1.to_string_lossy());
        // The current root's path is attached to a fact read from the store (not rebuilt) too.
        let second = read("root-two", &two);
        assert_eq!(second[0].path, two[0].1.to_string_lossy());
        assert_eq!(second[0].text, "(defk a [])");
        let stored: Vec<u8> = walkdir::WalkDir::new(&cache_dir)
            .into_iter()
            .filter_map(Result::ok)
            .filter(|e| e.file_type().is_file())
            .flat_map(|e| std::fs::read(e.path()).unwrap())
            .collect();
        let dir_text = dir.path().to_string_lossy().into_owned();
        assert!(!stored.windows(dir_text.len()).any(|w| w == dir_text.as_bytes()), "the root's absolute path remained in the store");
    }

    /// Even if a new kind forgets bind and returns a value holding the root's absolute path as is, it is not stored (no working tree's path remains in the store).
    /// The answer stays the rebuilt value, and it is rebuilt every time. It does not rely only on kinds whose caller tests pass; the place that stores it watches (a candidate from blind B of the design verification).
    #[test]
    fn a_kind_that_forgot_to_unbind_the_root_path_is_computed_each_time_and_never_stored() {
        let dir = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(dir.path()).unwrap();
        let path = root.join("a.hy");
        std::fs::write(&path, "(defk a [])").unwrap();
        let files = vec![("a.hy".to_string(), path.clone())];
        let mut cache = place_of(&root.join("cache"), "root-one", Format::Compact);
        cache.guard = LeakGuard::of(&root);
        let calls = AtomicUsize::new(0);
        let leaking = |_rel: &str, path: &Path| {
            calls.fetch_add(1, Ordering::SeqCst);
            Some(path.to_string_lossy().into_owned())
        };
        let before = store::LEAKED.load(Ordering::Relaxed);
        for _ in 0..2 {
            let found = per_file_at(Some(&cache), "v1", &files, leaking, no_bind, Format::Compact);
            assert_eq!(found, vec![path.to_string_lossy().into_owned()], "the answer differs from the rebuilt value");
        }
        assert_eq!(calls.load(Ordering::SeqCst), 2, "stored a value holding the root's path, and did not rebuild on the second run");
        assert!(store::LEAKED.load(Ordering::Relaxed) >= before + 2);
        assert_eq!(bytes_under(&cache.objects), 0, "a value holding the root's path was stored in the store");
    }

    /// If the file changes between reading the contents and building the fact, the fact is neither stored nor remembered in the table (the next run looks it up again).
    #[test]
    fn a_file_changed_while_its_fact_is_made_is_not_remembered() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.hy");
        std::fs::write(&path, "(defk before [])").unwrap();
        let files = vec![("a.hy".to_string(), path.clone())];
        let cache = place_of(dir.path(), "cache", Format::Json);
        let racing = per_file_at(
            Some(&cache),
            "v1",
            &files,
            |_rel, path| {
                let seen = std::fs::read_to_string(path).ok();
                std::fs::write(path, "(defk after [])").unwrap();
                set_mtime(path, SystemTime::now() + std::time::Duration::from_secs(5));
                seen
            },
            no_bind,
            Format::Json,
        );
        assert_eq!(racing, vec!["(defk before [])".to_string()]);
        assert!(!cache.objects.exists(), "stored a disagreeing fact");
        let calls = AtomicUsize::new(0);
        assert_eq!(run(&cache, "v1", &files, &calls, Format::Json), vec!["(defk after [])".to_string()]);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
    }

    /// Reading an old fact makes its last-used time now (so the cap cleanup does not put a frequently read fact at the front of the oldest-first order).
    #[test]
    fn reading_an_old_fact_refreshes_its_last_used_time() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.hy");
        std::fs::write(&path, "(defk a [])").unwrap();
        let files = vec![("a.hy".to_string(), path)];
        let cache = place_of(dir.path(), "cache", Format::Json);
        let calls = AtomicUsize::new(0);
        run(&cache, "v1", &files, &calls, Format::Json);
        let fact = walkdir::WalkDir::new(&cache.objects).into_iter().filter_map(Result::ok).find(|e| e.file_type().is_file()).unwrap().into_path();
        let old = SystemTime::now() - std::time::Duration::from_secs(2 * store::OBJECT_TOUCH_SECONDS);
        set_mtime(&fact, old);
        run(&cache, "v1", &files, &calls, Format::Json);
        let age = SystemTime::now().duration_since(std::fs::metadata(&fact).unwrap().modified().unwrap()).unwrap();
        assert!(age.as_secs() < store::OBJECT_TOUCH_SECONDS, "the read fact's last-used time stayed old: {age:?}");
        assert_eq!(calls.load(Ordering::SeqCst), 1, "rebuilt although it was only read");
    }

    /// From the body of the file (before `#[cfg(test)]`), extracts only the statements without comments and end-of-line notes, so the name scan is not thrown off by names in explanatory text.
    fn code_of(source: &str) -> String {
        let body = source.split("#[cfg(test)]").next().unwrap_or("");
        body.lines().map(|line| line.split("//").next().unwrap_or("")).collect::<Vec<_>>().join("\n")
    }

    /// Names that code uses after `store::` (both `store::a` and `use store::{a, b as c}`).
    fn names_taken_from_store(code: &str) -> Vec<String> {
        let mut names = Vec::new();
        for (at, _) in code.match_indices("store::") {
            let rest = &code[at + "store::".len()..];
            if let Some(group) = rest.strip_prefix('{') {
                let inside = group.split('}').next().unwrap_or("");
                names.extend(inside.split(',').filter_map(|item| item.split_whitespace().next()).map(str::to_string));
            } else {
                names.push(rest.chars().take_while(|c| c.is_alphanumeric() || *c == '_').collect());
            }
        }
        names
    }

    /// Protects the knowledge owners with a name scan (a diff where the lookup procedure deletes shared facts passed all the checks it was shown).
    /// rustc cannot limit visibility among sibling modules (`pub(super)` is visible to both the parent and siblings), so this checks that files that are not the owner do not use the names.
    /// - What the lookup procedure (this file) uses from store is only the entrances that store and read facts by key. If it had the entrances that list and delete facts (`list`, `remove`), lifetime decisions would be in two places.
    /// - What cleanup (sweep) uses from store is only the entrances that list and delete facts, and the store's names. If it had the entrances that store, read, or make keys for facts, knowledge of the facts' contents would leak.
    /// - store holds no lifetime rules (cap, seconds a root counts as in use, interval between turns).
    #[test]
    fn each_file_uses_only_the_names_of_the_knowledge_it_owns() {
        let resolve = names_taken_from_store(&code_of(include_str!("facts_cache.rs")));
        let allowed_for_resolve = ["object_key", "read_object", "write_object", "Format", "LeakGuard", "ObjectKey", "OBJECTS_DIR"];
        assert!(!resolve.is_empty(), "the lookup procedure uses no name from store; the scan sees nothing");
        for name in &resolve {
            assert!(allowed_for_resolve.contains(&name.as_str()), "the lookup procedure uses store's {name}; the entrance that lists and deletes facts belongs to cleanup alone");
        }

        let sweep = names_taken_from_store(&code_of(include_str!("facts_cache/sweep.rs")));
        let allowed_for_sweep = ["self", "list", "remove", "OBJECTS_DIR"];
        assert!(sweep.iter().any(|name| name == "list") && sweep.iter().any(|name| name == "remove"), "cleanup does not list and delete facts; the scan sees nothing");
        for name in &sweep {
            assert!(allowed_for_sweep.contains(&name.as_str()), "cleanup uses store's {name}; the entrances that store, read, and make keys for facts belong to store and the lookup procedure");
        }

        let store = code_of(include_str!("facts_cache/store.rs"));
        for name in ["IN_USE_SECONDS", "DEFAULT_CAP_BYTES", "CAP_ENV", "cache_cap_bytes", "SWEEP_INTERVAL_SECONDS"] {
            assert!(!store.contains(name), "store holds the lifetime rule {name}; the cap and oldest-first order are decided by cleanup");
        }
    }
}

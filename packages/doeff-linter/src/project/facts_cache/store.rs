//! The facts store (M-store) — puts, reads, lists, and removes, one file each, the facts shared across worktrees.
//!
//! Knowledge held by this file: the store layout (dir levels, file names, temporary file names), how keys are made, the encoding, and the conventions for modification times.
//! No other file knows these — only this file can build a fact's path (`object_path` is private); other files read and put by key, and cleanup
//! `remove`s only what `list` returned. Even if the layout changes (more levels, a different encoding), only this file needs fixing.
//!
//! Conventions:
//! - A fact is never rewritten once put (rename from a temporary file). The same key means the same contents. Only cleanup (sweep) deletes; nothing outside cleanup (the lookup path) deletes —
//!   facts are shared across roots, so one root's table alone cannot decide "no longer needed" (blind review B).
//! - A file's modification time = a guide to when it was last used. It is set when put, and on read it is set to now if older than OBJECT_TOUCH_SECONDS. Its precision is
//!   OBJECT_TOUCH_SECONDS (1 day), and it does not protect "facts being read" from cleanup — if one becomes unreadable the caller rebuilds it (the answer does not change).
//!   What is protected is only "facts within IN_USE_SECONDS since put (or since last read and refreshed)" (sweep judges this).
//! - An unreadable, broken, or wrongly shaped fact is None (the caller rebuilds it).

use rayon::prelude::*;
use serde::de::DeserializeOwned;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// The shape of a fact (the header comment).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Format {
    Json,
    Compact,
}

impl Format {
    pub(super) fn extension(self) -> &'static str {
        match self {
            Format::Json => "json",
            Format::Compact => "bin",
        }
    }
}

/// The name of the dir of the store of facts shared across roots (directly under the store root).
pub(super) const OBJECTS_DIR: &str = "objects";
/// When a fact is read, if its last-used time (the file's modification time) is older than this many seconds it is updated to now — so that, when the limit is exceeded, frequently read facts
/// (files of main that every worktree uses) are not at the front of the oldest-first order. It is set to 1 day because each re-stamping of the modification time makes the file count as "updated"
/// (it is counted), so the interval is sized to let only 1/(1 day) of the facts mix into the hourly writes.
pub(super) const OBJECT_TOUCH_SECONDS: u64 = 24 * 3600;
/// A half-written temporary file older than this many seconds is deleted when listing, as the leftover of an aborted run.
const OBJECT_TMP_STALE_SECONDS: u64 = 3600;
/// The lower bound of one fact's size when counting toward the limit (bytes) — even a nearly empty fact uses 1 block on disk.
const OBJECT_MIN_BYTES: u64 = 4096;

/// The store key of one fact (the first 16 bytes of a hash determined by the contents).
pub(super) type ObjectKey = [u8; 16];

/// Builds a fact's store key from key material (each part is followed by its length — if the part boundaries differ, so does the key).
pub(super) fn object_key(parts: &[&[u8]]) -> ObjectKey {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update((part.len() as u64).to_le_bytes());
        hasher.update(part);
    }
    let digest = hasher.finalize();
    let mut key = [0u8; 16];
    key.copy_from_slice(&digest[..16]);
    key
}

/// The path of one fact's file (`<store>/<first 2 chars of the hash>/<rest>` — so that not too many files gather in one dir). Private — only this file holds the knowledge of the layout.
fn object_path(objects: &Path, key: &ObjectKey) -> PathBuf {
    let name: String = key.iter().map(|byte| format!("{byte:02x}")).collect();
    objects.join(&name[..2]).join(&name[2..])
}

/// The path of the temporary file while putting a fact (same dir as the fact's file, `.<name>.<pid>.tmp`).
fn temporary_path(object: &Path) -> PathBuf {
    let name = object.file_name().and_then(|n| n.to_str()).unwrap_or("object");
    object.with_file_name(format!(".{name}.{}.tmp", std::process::id()))
}

/// Whether it is a temporary file's name (the name `temporary_path` makes — told apart when listing facts).
fn is_temporary(name: &str) -> bool {
    name.starts_with('.') && name.ends_with(".tmp")
}

fn encode_value<T: Serialize>(value: &T, format: Format) -> Option<Vec<u8>> {
    match format {
        Format::Json => serde_json::to_vec(value).ok(),
        Format::Compact => bincode::serialize(value).ok(),
    }
}

fn decode_value<T: DeserializeOwned>(bytes: &[u8], format: Format) -> Option<T> {
    match format {
        Format::Json => serde_json::from_slice(bytes).ok(),
        Format::Compact => bincode::deserialize(bytes).ok(),
    }
}

/// A watch for whether the current root's absolute path is in a value (checks the fact's spelling before putting). A value that holds the root's absolute path spells differently per worktree, so if it were put
/// in the store, a fact read by another worktree would point at a file of a different worktree — the contract that callers put it in a path-free form and attach the path with bind after reading (the header comment of facts_cache)
/// is checked at the point of putting rather than relying on the caller's memory (blind review B of the design verification — even if a new kind forgets the contract, it just rebuilds without putting, and the answer is still correct).
pub(super) struct LeakGuard {
    needles: Vec<Vec<u8>>,
}

/// The number of times the watch was breached (an attempt to put a value holding the root's absolute path). Read by tests.
pub(super) static LEAKED: AtomicUsize = AtomicUsize::new(0);

impl LeakGuard {
    /// Turns the root's absolute path (the given spelling and the normalized spelling) into spellings to search for. Spellings too short (such as `/`) would match any value, so they are not used.
    pub(super) fn of(root: &Path) -> LeakGuard {
        let mut needles: Vec<Vec<u8>> = Vec::new();
        for candidate in [Some(root.to_path_buf()), std::fs::canonicalize(root).ok()].into_iter().flatten() {
            let text = candidate.to_string_lossy().into_owned().into_bytes();
            if candidate.is_absolute() && text.len() >= 4 && !needles.contains(&text) {
                needles.push(text);
            }
        }
        LeakGuard { needles }
    }

    fn finds_in(&self, bytes: &[u8]) -> bool {
        self.needles.iter().any(|needle| memchr::memmem::find(bytes, needle).is_some())
    }
}

/// Reads a fact (a missing, broken, or wrongly shaped fact is None — the caller rebuilds it). If the last-used time is older than OBJECT_TOUCH_SECONDS, sets it to now.
pub(super) fn read_object<T: DeserializeOwned>(objects: &Path, key: &ObjectKey, format: Format) -> Option<T> {
    let path = object_path(objects, key);
    let mut file = std::fs::File::open(&path).ok()?;
    let meta = file.metadata().ok()?;
    let mut bytes = Vec::with_capacity(usize::try_from(meta.len()).unwrap_or(0));
    file.read_to_end(&mut bytes).ok()?;
    let value = decode_value(&bytes, format)?;
    let stale = meta
        .modified()
        .ok()
        .and_then(|at| SystemTime::now().duration_since(at).ok())
        .is_some_and(|age| age.as_secs() >= OBJECT_TOUCH_SECONDS);
    if stale {
        // Being unable to stamp it does not affect the answer (the oldest-first order is only slightly off)
        let _ = file.set_modified(SystemTime::now()).or_else(|_| {
            std::fs::File::options().write(true).open(&path).and_then(|writable| writable.set_modified(SystemTime::now()))
        });
    }
    Some(value)
}

/// Puts a fact (rename from a temporary file). Returns the bytes put (0 if it could not be put). Does not put if the value holds the root's absolute path (the watch — `LeakGuard`).
pub(super) fn write_object<T: Serialize>(objects: &Path, key: &ObjectKey, value: &T, format: Format, guard: &LeakGuard) -> u64 {
    let path = object_path(objects, key);
    let Some(dir) = path.parent() else { return 0 };
    if std::fs::create_dir_all(dir).is_err() {
        return 0;
    }
    let Some(bytes) = encode_value(value, format) else { return 0 };
    if guard.finds_in(&bytes) {
        // Notify only once per run (thousands of facts per kind line up with the same mistake). The answer stays correct as the rebuilt value.
        if LEAKED.fetch_add(1, Ordering::Relaxed) == 0 {
            eprintln!("doeff-linter: facts cache: values holding the root's absolute path are not put in the store (the contract is that callers put them in a path-free form and attach the path with bind — the header comment of facts_cache)");
        }
        return 0;
    }
    let tmp = temporary_path(&path);
    if std::fs::write(&tmp, &bytes).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return 0;
    }
    if std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return 0;
    }
    bytes.len() as u64
}

/// One fact in the store (cleanup counts them and `remove`s them oldest first). The path is only held, not exposed.
pub(super) struct StoredObject {
    /// Last-used time (UNIX seconds — the file's modification time).
    pub(super) used: u64,
    /// Size counted toward the limit (bytes — with OBJECT_MIN_BYTES as the lower bound).
    pub(super) bytes: u64,
    path: PathBuf,
}

/// Lists all facts in the store (independent of the depth of the layout — a file directly under the store root [the used marker] is not a fact). Old
/// temporary files left by aborted runs are deleted here.
pub(super) fn list(objects: &Path, now: u64) -> Vec<StoredObject> {
    walkdir::WalkDir::new(objects)
        .min_depth(2)
        .into_iter()
        .par_bridge()
        .filter_map(|entry| {
            let entry = entry.ok().filter(|entry| entry.file_type().is_file())?;
            let meta = entry.metadata().ok()?;
            let used = meta.modified().ok().and_then(|time| time.duration_since(UNIX_EPOCH).ok()).map_or(0, |d| d.as_secs());
            if entry.file_name().to_str().is_some_and(is_temporary) {
                if now.saturating_sub(used) >= OBJECT_TMP_STALE_SECONDS {
                    let _ = std::fs::remove_file(entry.path());
                }
                return None;
            }
            Some(StoredObject { used, bytes: meta.len().max(OBJECT_MIN_BYTES), path: entry.into_path() })
        })
        .collect()
}

/// Removes a fact (whether it was removed). A removed fact is rebuilt the next time it is needed (the answer does not change).
pub(super) fn remove(object: StoredObject) -> bool {
    std::fs::remove_file(object.path).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set_mtime(path: &Path, at: SystemTime) {
        std::fs::File::options().write(true).open(path).unwrap().set_modified(at).unwrap();
    }

    fn no_guard() -> LeakGuard {
        LeakGuard { needles: Vec::new() }
    }

    /// All facts that were put are listed — even if the layout changes (levels get deeper) they are not missed by cleanup's count (blind review A of the design verification: one line of layout change
    /// left all tests passing while the facts limit stopped working). The used marker directly under the store root is not counted as a fact.
    #[test]
    fn every_object_written_is_listed_and_a_deeper_layout_is_still_counted() {
        let dir = tempfile::tempdir().unwrap();
        let objects = dir.path().join("objects").join("kind.bin");
        let keys: Vec<ObjectKey> = (0..40u32).map(|i| object_key(&[&i.to_le_bytes()])).collect();
        for key in &keys {
            assert!(write_object(&objects, key, &"x".repeat(100), Format::Json, &no_guard()) > 0);
        }
        std::fs::write(dir.path().join("objects").join("used"), b"").unwrap();
        assert_eq!(list(&dir.path().join("objects"), 0).len(), keys.len());
        // Facts are counted even if the layout changed and they are in a deeper place (independent of layout depth).
        let deeper = objects.join("aa").join("bb").join("cc");
        std::fs::create_dir_all(&deeper).unwrap();
        std::fs::write(deeper.join("fact"), b"y").unwrap();
        assert_eq!(list(&dir.path().join("objects"), 0).len(), keys.len() + 1);
    }

    /// A temporary file's name follows a convention shared by the putting side (temporary_path) and the listing side (is_temporary) — even if the putting side's name changes, cleanup's recognition
    /// does not drift. Only old ones are deleted; new ones (possibly being written) remain.
    #[test]
    fn a_temporary_made_by_the_writer_is_recognised_by_the_lister() {
        let dir = tempfile::tempdir().unwrap();
        let objects = dir.path().join("objects").join("kind.bin");
        let key = object_key(&[b"a"]);
        let object = object_path(&objects, &key);
        std::fs::create_dir_all(object.parent().unwrap()).unwrap();
        let stale = temporary_path(&object);
        let fresh = temporary_path(&object_path(&objects, &object_key(&[b"b"])));
        std::fs::create_dir_all(fresh.parent().unwrap()).unwrap();
        std::fs::write(&stale, b"half").unwrap();
        std::fs::write(&fresh, b"half").unwrap();
        set_mtime(&stale, SystemTime::now() - std::time::Duration::from_secs(2 * OBJECT_TMP_STALE_SECONDS));
        assert!(list(&dir.path().join("objects"), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()).is_empty(), "temporary files were counted as facts");
        assert!(!stale.exists(), "the old temporary file remained");
        assert!(fresh.exists(), "even the new temporary file was deleted");
    }

    /// A value holding the root's absolute path is not put (the watch) — a value without a path is put.
    #[test]
    fn a_value_holding_the_root_path_is_not_stored() {
        let dir = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(dir.path()).unwrap();
        let objects = dir.path().join("objects").join("kind.bin");
        let guard = LeakGuard::of(&root);
        let leaking = format!("{}/app/a.hy", root.display());
        let before = LEAKED.load(Ordering::Relaxed);
        assert_eq!(write_object(&objects, &object_key(&[b"leak"]), &leaking, Format::Json, &guard), 0, "a value holding the root's path was put");
        assert_eq!(write_object(&objects, &object_key(&[b"leak"]), &leaking, Format::Compact, &guard), 0, "it was put even in the binary form");
        assert!(LEAKED.load(Ordering::Relaxed) >= before + 2);
        assert!(list(&dir.path().join("objects"), 0).is_empty(), "a value that was not to be put is in the store");
        assert!(write_object(&objects, &object_key(&[b"ok"]), &"app/a.hy".to_string(), Format::Json, &guard) > 0, "a value without a path could not be put");
    }

    /// The watch never matches every value by searching for a short spelling of the root.
    #[test]
    fn a_too_short_root_makes_no_needle() {
        assert!(LeakGuard::of(Path::new("/")).needles.is_empty());
        assert!(LeakGuard::of(Path::new(".")).needles.iter().all(|needle| needle.len() >= 4));
    }
}

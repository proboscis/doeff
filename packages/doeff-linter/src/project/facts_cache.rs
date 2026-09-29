//! file ごとの事実の cache(agora-redesign #1025)。
//!
//! 1 file の実行(書き込み直後の hook の `--stdin --path`)でも、repo 全体の事実が要る規則(DOEFF126 の defk の名・
//! DOEFF127 / 129 / 130 の effect の推論の世界)は、repo の Hy の file を全部読んで解析し直していた — 1 回の CPU の
//! 大半(#1022 の計器の実測)。file ごとの事実がその file の中身だけで決まる物について、事実を disk に置き、
//! 変わった file だけ解析し直す。答えは変えない(同じ file の中身からは同じ事実)。
//!
//! 鍵:
//! - file ごと: 大きさと更新時刻(ns)。どちらかが変われば解析し直す。
//! - 全体の印: linter 自身の binary の大きさと更新時刻・版・事実の種類。linter が組み直されれば全部を作り直す
//!   (事実の集め方が変わりうるため)。
//!
//! 置き場は `$DOEFF_LINTER_CACHE_DIR`・無ければ `$XDG_CACHE_HOME/doeff-linter`・無ければ `~/.cache/doeff-linter` の下の
//! 根の path の hash の dir に、種類ごとの 1 file。書くのは一時 file から rename する(並走する hook どうしで壊さない —
//! 後に書いた方が勝つだけで、どちらも正しい事実)。`DOEFF_LINTER_NO_CACHE` が在れば読みも書きもしない。
//! 読めない・壊れた cache は無い物として扱う(全部を解析し直す — 答えは変わらない)。
//!
//! 形は 2 つ: 既定は JSON(`<種類>.json`)。repo 全体の Hy の索引のように大きい種類は、欄の名前を持たない binary(bincode・
//! `<種類>.bin`)で置く(`per_file_compact` — JSON では agora の本線で 147MB・読みに 1.2 秒かかった・agora-redesign #1364)。
//! binary の形は値の欄の並びがそのまま綴りなので、値の型の serde の形が値によって欄を出したり出さなかったりしないこと
//! (`skip_serializing_if` を持たないこと)を呼び手が保つ。

use rayon::prelude::*;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

const NO_CACHE_ENV: &str = "DOEFF_LINTER_NO_CACHE";
const DIR_ENV: &str = "DOEFF_LINTER_CACHE_DIR";

/// file 1 つの鍵(大きさ・更新時刻の ns)。
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

#[derive(Serialize, Deserialize)]
struct Entry<T> {
    stamp: Stamp,
    /// None = 集める物が無い file(例: 定義を 1 つも持たない)— 無いことも覚えて読み直さない。
    value: Option<T>,
}

#[derive(Serialize, Deserialize)]
struct Stored<T> {
    identity: String,
    entries: HashMap<String, Entry<T>>,
}

/// linter 自身の印(binary の大きさと更新時刻・版)と事実の種類。
fn identity(kind: &str) -> String {
    let exe = std::env::current_exe().ok().and_then(|p| stamp_of(&p));
    let exe = exe.map(|s| format!("{}-{}", s.len, s.mtime_ns)).unwrap_or_else(|| "unknown".to_string());
    format!("{kind}/{}/{exe}", env!("CARGO_PKG_VERSION"))
}

/// cache の file の形(頭の註)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Format {
    Json,
    Compact,
}

impl Format {
    fn extension(self) -> &'static str {
        match self {
            Format::Json => "json",
            Format::Compact => "bin",
        }
    }
}

/// binary の形の file 1 つの値 — 値は bincode の塊のまま運び、読む時に file ごとに並べて decode する(60MB の索引を 1 本の流れで
/// decode すると 0.3 秒かかった — agora-redesign #1364)。
#[derive(Serialize, Deserialize)]
struct CompactEntry {
    rel: String,
    stamp: Stamp,
    value: Option<Vec<u8>>,
}

#[derive(Serialize, Deserialize)]
struct CompactStored {
    identity: String,
    entries: Vec<CompactEntry>,
}

fn cache_file(root: &Path, kind: &str, format: Format) -> Option<PathBuf> {
    if cfg!(test) || std::env::var_os(NO_CACHE_ENV).is_some() {
        return None;
    }
    let base = std::env::var_os(DIR_ENV)
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("XDG_CACHE_HOME").map(|d| PathBuf::from(d).join("doeff-linter")))
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache").join("doeff-linter")))?;
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    root.hash(&mut hasher);
    Some(base.join(format!("{:016x}", hasher.finish())).join(format!("{kind}.{}", format.extension())))
}

fn load<T: DeserializeOwned + Send>(file: &Path, identity: &str, format: Format) -> HashMap<String, Entry<T>> {
    let Ok(bytes) = std::fs::read(file) else { return HashMap::new() };
    match format {
        Format::Json => match serde_json::from_slice::<Stored<T>>(&bytes) {
            Ok(stored) if stored.identity == identity => stored.entries,
            _ => HashMap::new(),
        },
        Format::Compact => {
            let Ok(stored) = bincode::deserialize::<CompactStored>(&bytes) else { return HashMap::new() };
            if stored.identity != identity {
                return HashMap::new();
            }
            // 値が 1 つでも読めなければ、cache 全体を無い物として扱う(全部を解析し直す — 答えは変わらない)。
            let decoded: Option<Vec<(String, Entry<T>)>> = stored
                .entries
                .into_par_iter()
                .map(|CompactEntry { rel, stamp, value }| {
                    let value = match value {
                        Some(blob) => Some(bincode::deserialize::<T>(&blob).ok()?),
                        None => None,
                    };
                    Some((rel, Entry { stamp, value }))
                })
                .collect();
            decoded.map(|entries| entries.into_iter().collect()).unwrap_or_default()
        }
    }
}

/// 書く時の `Stored` の形(値を複製せずに借りて綴る — 綴りは `Stored` と同じ)。
#[derive(Serialize)]
struct StoredRef<'a, T> {
    identity: &'a str,
    entries: HashMap<&'a str, &'a Entry<T>>,
}

fn encode<T: Serialize + Sync>(identity: &str, entries: &[(String, Entry<T>, bool)], format: Format) -> Option<Vec<u8>> {
    match format {
        Format::Json => {
            let stored = StoredRef { identity, entries: entries.iter().map(|(rel, entry, _)| (rel.as_str(), entry)).collect() };
            serde_json::to_vec(&stored).ok()
        }
        Format::Compact => {
            let entries: Option<Vec<CompactEntry>> = entries
                .par_iter()
                .map(|(rel, entry, _)| {
                    let value = match &entry.value {
                        Some(value) => Some(bincode::serialize(value).ok()?),
                        None => None,
                    };
                    Some(CompactEntry { rel: rel.clone(), stamp: entry.stamp, value })
                })
                .collect();
            bincode::serialize(&CompactStored { identity: identity.to_string(), entries: entries? }).ok()
        }
    }
}

fn save<T: Serialize + Sync>(file: &Path, identity: &str, entries: &[(String, Entry<T>, bool)], format: Format) {
    let Some(dir) = file.parent() else { return };
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    let Some(bytes) = encode(identity, entries, format) else { return };
    let tmp = dir.join(format!(".{}.{}.tmp", file.file_name().and_then(|n| n.to_str()).unwrap_or("facts"), std::process::id()));
    if std::fs::write(&tmp, bytes).is_ok() && std::fs::rename(&tmp, file).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// `files`(根からの path と絶対 path)の事実を、変わった file だけ `compute` で作り直して返す(順は `files` の順・
/// 集める物が無い file は含めない)。`compute` はその file の中身だけで事実を決めること(他の file を読まない)。
pub fn per_file<T, F>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_keyed(root, kind, "", files, compute)
}

/// `per_file` と同じ — ただし事実が file の中身のほかに `key` にも依る時に使う(`key` が変われば全部を作り直す)。
pub fn per_file_keyed<T, F>(root: &Path, kind: &str, key: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_at(cache_file(root, kind, Format::Json).as_deref(), &format!("{}/{key}", identity(kind)), files, compute, Format::Json)
}

/// `per_file` と同じ — ただし cache を欄の名前を持たない binary の形で置く(頭の註 — 大きい種類のため)。
pub fn per_file_compact<T, F>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_at(cache_file(root, kind, Format::Compact).as_deref(), &format!("{}/", identity(kind)), files, compute, Format::Compact)
}

/// `per_file` の本体 — cache の file の置き場と印と形を受ける(置き場が None = cache を使わない)。
fn per_file_at<T, F>(file: Option<&Path>, identity: &str, files: &[(String, PathBuf)], compute: F, format: Format) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    let Some(file) = file else {
        return files.par_iter().filter_map(|(rel, path)| compute(rel, path)).collect();
    };
    let mut known: HashMap<String, Entry<T>> = crate::timing::timed("facts-cache.load", || load(file, identity, format));
    let before = known.len();
    // cache の値は複製せずに移す(repo 全体の Hy の索引では、値の複製が cache の読みの大半を占めた — agora-redesign #1364)。
    let taken: Vec<Option<Entry<T>>> = files.iter().map(|(rel, _)| known.remove(rel)).collect();
    let fresh: Vec<(String, Entry<T>, bool)> = files
        .par_iter()
        .zip(taken.into_par_iter())
        .filter_map(|((rel, path), cached)| {
            let stamp = stamp_of(path)?;
            match cached {
                Some(entry) if entry.stamp == stamp => Some((rel.clone(), entry, false)),
                _ => Some((rel.clone(), Entry { stamp, value: compute(rel, path) }, true)),
            }
        })
        .collect();
    let changed = fresh.iter().any(|(_, _, recomputed)| *recomputed) || fresh.len() != before;
    if changed {
        save(file, identity, &fresh, format);
    }
    fresh.into_iter().filter_map(|(_, entry, _)| entry.value).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn run(cache: &Path, identity: &str, files: &[(String, PathBuf)], calls: &AtomicUsize, format: Format) -> Vec<String> {
        per_file_at(
            Some(cache),
            identity,
            files,
            |_rel, path| {
                calls.fetch_add(1, Ordering::SeqCst);
                let text = std::fs::read_to_string(path).ok()?;
                (!text.is_empty()).then_some(text)
            },
            format,
        )
    }

    /// 変わった file だけを読み直し、答えは変わらない — 形(JSON・binary)を問わず同じ筋書き。
    fn only_changed_files_are_read_again(format: Format) {
        let dir = tempfile::tempdir().unwrap();
        let (a, b, empty) = (dir.path().join("a.hy"), dir.path().join("b.hy"), dir.path().join("c.hy"));
        std::fs::write(&a, "(defk a [])").unwrap();
        std::fs::write(&b, "(defk b [])").unwrap();
        std::fs::write(&empty, "").unwrap();
        let files = vec![("a.hy".to_string(), a.clone()), ("b.hy".to_string(), b.clone()), ("c.hy".to_string(), empty)];
        let cache = dir.path().join("cache").join(format!("kind.{}", format.extension()));
        let calls = AtomicUsize::new(0);
        let first = run(&cache, "v1", &files, &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // 変わっていなければ 1 つも読み直さず、同じ答え(集める物が無い file も覚えている)。
        assert_eq!(run(&cache, "v1", &files, &calls, format), first);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
        // 1 file の中身が変われば、その file だけ読み直して新しい事実を返す。
        std::fs::write(&b, "(defk b-changed [])").unwrap();
        assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string(), "(defk b-changed [])".to_string()]);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // linter の印が変われば全部を読み直す。
        run(&cache, "v2", &files, &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // 消えた file は答えにも cache にも残らない。
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
    fn a_broken_cache_is_read_as_absent() {
        for (format, broken) in [(Format::Json, &b"{ not json"[..]), (Format::Compact, &b"\x01\x02"[..])] {
            let dir = tempfile::tempdir().unwrap();
            let a = dir.path().join("a.hy");
            std::fs::write(&a, "(defk a [])").unwrap();
            let cache = dir.path().join(format!("kind.{}", format.extension()));
            std::fs::write(&cache, broken).unwrap();
            let calls = AtomicUsize::new(0);
            assert_eq!(run(&cache, "v1", &[("a.hy".to_string(), a)], &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.load(Ordering::SeqCst), 1);
        }
    }
}

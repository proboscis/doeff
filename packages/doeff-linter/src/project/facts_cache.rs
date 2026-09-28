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

fn cache_file(root: &Path, kind: &str) -> Option<PathBuf> {
    if cfg!(test) || std::env::var_os(NO_CACHE_ENV).is_some() {
        return None;
    }
    let base = std::env::var_os(DIR_ENV)
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("XDG_CACHE_HOME").map(|d| PathBuf::from(d).join("doeff-linter")))
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache").join("doeff-linter")))?;
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    root.hash(&mut hasher);
    Some(base.join(format!("{:016x}", hasher.finish())).join(format!("{kind}.json")))
}

fn load<T: DeserializeOwned>(file: &Path, identity: &str) -> HashMap<String, Entry<T>> {
    let Ok(text) = std::fs::read(file) else { return HashMap::new() };
    match serde_json::from_slice::<Stored<T>>(&text) {
        Ok(stored) if stored.identity == identity => stored.entries,
        _ => HashMap::new(),
    }
}

fn save<T: Serialize>(file: &Path, identity: &str, entries: HashMap<String, Entry<T>>) {
    let Some(dir) = file.parent() else { return };
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    let stored = Stored { identity: identity.to_string(), entries };
    let Ok(bytes) = serde_json::to_vec(&stored) else { return };
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
    per_file_at(cache_file(root, kind).as_deref(), &format!("{}/{key}", identity(kind)), files, compute)
}

/// `per_file` の本体 — cache の file の置き場と印を受ける(None = cache を使わない)。
fn per_file_at<T, F>(file: Option<&Path>, identity: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    let Some(file) = file else {
        return files.par_iter().filter_map(|(rel, path)| compute(rel, path)).collect();
    };
    let known: HashMap<String, Entry<T>> = load(file, identity);
    let fresh: Vec<(String, Entry<T>, bool)> = files
        .par_iter()
        .filter_map(|(rel, path)| {
            let stamp = stamp_of(path)?;
            match known.get(rel) {
                Some(entry) if entry.stamp == stamp => Some((rel.clone(), Entry { stamp, value: entry.value.clone() }, false)),
                _ => Some((rel.clone(), Entry { stamp, value: compute(rel, path) }, true)),
            }
        })
        .collect();
    let changed = fresh.iter().any(|(_, _, recomputed)| *recomputed) || fresh.len() != known.len();
    let values: Vec<T> = fresh.iter().filter_map(|(_, entry, _)| entry.value.clone()).collect();
    if changed {
        save(file, identity, fresh.into_iter().map(|(rel, entry, _)| (rel, entry)).collect());
    }
    values
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn run(cache: &Path, identity: &str, files: &[(String, PathBuf)], calls: &AtomicUsize) -> Vec<String> {
        per_file_at(Some(cache), identity, files, |_rel, path| {
            calls.fetch_add(1, Ordering::SeqCst);
            let text = std::fs::read_to_string(path).ok()?;
            (!text.is_empty()).then_some(text)
        })
    }

    #[test]
    fn only_changed_files_are_read_again_and_the_answer_is_the_same() {
        let dir = tempfile::tempdir().unwrap();
        let (a, b, empty) = (dir.path().join("a.hy"), dir.path().join("b.hy"), dir.path().join("c.hy"));
        std::fs::write(&a, "(defk a [])").unwrap();
        std::fs::write(&b, "(defk b [])").unwrap();
        std::fs::write(&empty, "").unwrap();
        let files = vec![("a.hy".to_string(), a.clone()), ("b.hy".to_string(), b.clone()), ("c.hy".to_string(), empty)];
        let cache = dir.path().join("cache").join("kind.json");
        let calls = AtomicUsize::new(0);
        let first = run(&cache, "v1", &files, &calls);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // 変わっていなければ 1 つも読み直さず、同じ答え(集める物が無い file も覚えている)。
        assert_eq!(run(&cache, "v1", &files, &calls), first);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
        // 1 file の中身が変われば、その file だけ読み直して新しい事実を返す。
        std::fs::write(&b, "(defk b-changed [])").unwrap();
        assert_eq!(run(&cache, "v1", &files, &calls), vec!["(defk a [])".to_string(), "(defk b-changed [])".to_string()]);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // linter の印が変われば全部を読み直す。
        run(&cache, "v2", &files, &calls);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 3);
        // 消えた file は答えにも cache にも残らない。
        std::fs::remove_file(&a).unwrap();
        assert_eq!(run(&cache, "v2", &files, &calls), vec!["(defk b-changed [])".to_string()]);
    }

    #[test]
    fn a_broken_cache_is_read_as_absent() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.hy");
        std::fs::write(&a, "(defk a [])").unwrap();
        let cache = dir.path().join("kind.json");
        std::fs::write(&cache, "{ not json").unwrap();
        let calls = AtomicUsize::new(0);
        assert_eq!(run(&cache, "v1", &[("a.hy".to_string(), a)], &calls), vec!["(defk a [])".to_string()]);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}

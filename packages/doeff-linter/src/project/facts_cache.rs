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
//! 根の path の hash の dir に、種類ごとの dir(`<種類>.<形>.d/`)を置き、file の path で決まる SHARDS 個の塊(`00`〜)に分けて置く。
//! 根の dir には根の絶対 path(`root.path`)を記録し、1 時間ごとに置き場を走査して、記録した根が無くなった dir を消す
//! (`tend_roots` — agora-redesign #1903。記録の無い dir は消さない)。
//! 書くのは変わった塊だけ(再計算した file か、消えた file を含む塊)— 1 file を変えた直後の実行が、repo 全体の事実(Hy の索引で
//! 65MB)を丸ごと書き直していた(zeus で約 0.35 秒・agora-redesign #1523)。読むのは全部の塊を並べて読む。前の形の 1 file
//! (`<種類>.<形>`)は、新しい形で初めて書く時に消す(読まない — 塊が無ければ作り直すだけで答えは変わらない)。
//! 書くのは一時 file から rename する(並走する hook どうしで壊さない — 後に書いた方が勝つだけで、どちらも正しい事実)。
//! `DOEFF_LINTER_NO_CACHE` が在れば読みも書きもしない。読めない・壊れた塊は無い物として扱う(その塊の file を解析し直す —
//! 答えは変わらない)。
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
/// 種類ごとの塊の数(頭の註)— 1 file の変更で書き直すのは約 1/SHARDS、読みで開くのは種類ごとに SHARDS 個。
const SHARDS: usize = 16;

/// file の根からの path が載る塊(FNV-1a — 実行と build をまたいで同じ答え)。
fn shard_of(rel: &str) -> usize {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in rel.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    (hash % SHARDS as u64) as usize
}

/// 種類の置き場 file(前の形の 1 file の path)から、塊 n の path(`<置き場>.d/<n>`)。
fn shard_path(file: &Path, n: usize) -> PathBuf {
    let name = file.file_name().and_then(|n| n.to_str()).unwrap_or("facts");
    file.with_file_name(format!("{name}.d")).join(format!("{n:02}"))
}

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

/// 置き場の根(頭の註 — `$DOEFF_LINTER_CACHE_DIR`・`$XDG_CACHE_HOME/doeff-linter`・`~/.cache/doeff-linter` の順)。
/// `DOEFF_LINTER_NO_CACHE` が在れば None。根の直下の dir は全部、上限の片づけ(sweep_over_cap)の対象 — 根の dir の外の置き場
/// (commit の hook の HEAD の木の結果・agora-redesign #2723)も、根の直下に dir を置いて印 `used`(mark_used)を付ければ同じ上限に入る。
pub fn cache_base() -> Option<PathBuf> {
    if std::env::var_os(NO_CACHE_ENV).is_some() {
        return None;
    }
    std::env::var_os(DIR_ENV)
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("XDG_CACHE_HOME").map(|d| PathBuf::from(d).join("doeff-linter")))
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache").join("doeff-linter")))
}

fn cache_file(root: &Path, kind: &str, format: Format) -> Option<PathBuf> {
    if cfg!(test) {
        return None;
    }
    let base = cache_base()?;
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    root.hash(&mut hasher);
    let root_dir = base.join(format!("{:016x}", hasher.finish()));
    crate::timing::timed("facts-cache.roots", || tend_roots(&base, &root_dir, root));
    Some(root_dir.join(format!("{kind}.{}", format.extension())))
}

/// 根の dir の中の、その dir を作った根の path の記録(agora-redesign #1903)。
const ROOT_RECORD: &str = "root.path";
/// 置き場の、前に片づけの走査をした時刻(UNIX 秒)の記録。
const SWEEP_RECORD: &str = ".swept";
/// 片づけの走査の間隔(秒)— linter は file を書くたびに hook から走るので、毎回は走査しない。
const SWEEP_INTERVAL_SECONDS: u64 = 3600;

/// 根の dir は根の path の hash で決まるので、作業木や品質検査の一時の写しのように消えた根の dir は、誰も読まないまま残り続けた
/// (zeus で 4136 dir・21.6G — agora-redesign #1903)。書く側の 1 点で、根の dir に根の path を記録し、間隔を空けて置き場を走査して、
/// 記録した根がもう無い dir を消す。記録の無い dir(この仕組みより前に作られた dir)は消さない — 消してよいかは別に決める(#1472)。
/// 時間だけを理由には消さない。消しても答えは変わらない(読めない塊は作り直す — 頭の註)。1 つの実行で 1 度だけ行う。
fn tend_roots(base: &Path, root_dir: &Path, root: &Path) {
    static TENDED: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    TENDED.get_or_init(|| {
        record_root(root_dir, root);
        mark_used(root_dir);
        // 時計が 1970 年より前を指す機体では間隔を測れないので走査しない
        if let Ok(now) = std::time::SystemTime::now().duration_since(UNIX_EPOCH) {
            if sweep_vanished_roots(base, now.as_secs()) {
                sweep_over_cap(base, root_dir, cache_cap_bytes(), now.as_secs());
            }
        }
    });
}

/// 根の dir の、最後に使われた時刻の印(中身は空 — file の更新時刻が印)。上限を超えた時に古い順を決める(agora-redesign #2725)。
const USED_RECORD: &str = "used";
/// 置き場の全体の上限を変える環境変数(byte)。
const CAP_ENV: &str = "DOEFF_LINTER_CACHE_MAX_BYTES";
/// 置き場の全体の上限の既定(10 GiB)— 作業木 1 つの根の dir は約 65MB なので、約 150 の根を持てる。
const DEFAULT_CAP_BYTES: u64 = 10 * 1024 * 1024 * 1024;

/// 置き場の全体の上限(環境変数が読めない値なら既定)。
fn cache_cap_bytes() -> u64 {
    std::env::var(CAP_ENV).ok().and_then(|text| text.trim().parse::<u64>().ok()).unwrap_or(DEFAULT_CAP_BYTES)
}

/// この実行が根の dir を使った印を付ける(1 つの実行で 1 度)。commit の hook の HEAD の木の結果の dir も、読み書きのたびにこれで
/// 印を付ける(agora-redesign #2723)。
pub fn mark_used(root_dir: &Path) {
    if std::fs::create_dir_all(root_dir).is_ok() {
        let _ = std::fs::write(root_dir.join(USED_RECORD), b"");
    }
}

/// 根の dir の中の file の大きさの合計(byte — 根の dir の直下と `<種類>.<形>.d/` の塊)。
fn dir_bytes(dir: &Path) -> u64 {
    let Ok(entries) = std::fs::read_dir(dir) else { return 0 };
    entries
        .flatten()
        .map(|entry| match entry.metadata() {
            Ok(meta) if meta.is_dir() => dir_bytes(&entry.path()),
            Ok(meta) => meta.len(),
            Err(_) => 0,
        })
        .sum()
}

/// 根の dir の、最後に使われた時刻(UNIX 秒)— 印 `used` の更新時刻。印の無い dir(この仕組みより前に作られた dir)は dir の更新時刻。
fn last_used(dir: &Path) -> u64 {
    std::fs::metadata(dir.join(USED_RECORD))
        .or_else(|_| std::fs::metadata(dir))
        .and_then(|meta| meta.modified())
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs())
}

/// 置き場の全体が上限 cap を超えていれば、最後に使われた時刻の古い dir から消して上限の内へ戻す(agora-redesign #2725)。根が在り続ける
/// 作業木の dir は sweep_vanished_roots では消えず、根の数だけ増え続けた(zeus で 62G・1 時間に約 4G)。この実行の根の dir と、前の走査の
/// 間隔の内に使われた dir(並走する実行が使っている根)は消さない — 上限を一時に超えても、使っている根を消して作り直させない。消しても
/// 答えは変わらない(読めない塊は作り直す — 頭の註)。
fn sweep_over_cap(base: &Path, root_dir: &Path, cap: u64, now: u64) {
    let Ok(entries) = std::fs::read_dir(base) else { return };
    let mut dirs: Vec<(u64, u64, PathBuf)> = entries
        .flatten()
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
        .map(|entry| {
            let dir = entry.path();
            (last_used(&dir), dir_bytes(&dir), dir)
        })
        .collect();
    let mut total: u64 = dirs.iter().map(|(_, bytes, _)| bytes).sum();
    if total <= cap {
        return;
    }
    dirs.sort_by_key(|(used, _, _)| *used);
    for (used, bytes, dir) in dirs {
        if total <= cap {
            break;
        }
        if dir == root_dir || now.saturating_sub(used) < SWEEP_INTERVAL_SECONDS {
            continue;
        }
        // 消せなければ次の走査でもう一度試す(答えには関わらない)
        if std::fs::remove_dir_all(&dir).is_ok() {
            total = total.saturating_sub(bytes);
        }
    }
}

/// 根の dir に根の絶対 path を記録する(同じ記録が既に在れば書かない)。
fn record_root(root_dir: &Path, root: &Path) {
    let Ok(absolute) = std::fs::canonicalize(root) else { return };
    let Some(text) = absolute.to_str() else { return };
    let record = root_dir.join(ROOT_RECORD);
    if std::fs::read_to_string(&record).is_ok_and(|known| known == text) {
        return;
    }
    if std::fs::create_dir_all(root_dir).is_err() {
        return;
    }
    let tmp = root_dir.join(format!(".{ROOT_RECORD}.{}.tmp", std::process::id()));
    if std::fs::write(&tmp, text).is_ok() && std::fs::rename(&tmp, &record).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// 前の走査から間隔が過ぎていれば、置き場の根の dir のうち、記録した根の path が無い(NotFound)dir を消す。記録の無い dir・記録を
/// 読めない dir・根の有無を確かめられない dir は残す。時刻の記録を先に書き換えるので、並走する実行は同じ間隔の中で重ねて走査しない。
/// 走査した時に真を返す(間隔の内・時刻の記録を書けない時は偽 — 上限の片づけ sweep_over_cap も同じ間隔で走らせるため)。
fn sweep_vanished_roots(base: &Path, now: u64) -> bool {
    let stamp = base.join(SWEEP_RECORD);
    let last = std::fs::read_to_string(&stamp).ok().and_then(|text| text.trim().parse::<u64>().ok());
    if last.is_some_and(|last| now.saturating_sub(last) < SWEEP_INTERVAL_SECONDS) {
        return false;
    }
    if std::fs::create_dir_all(base).is_err() || std::fs::write(&stamp, now.to_string()).is_err() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(base) else { return true };
    for entry in entries.flatten() {
        let dir = entry.path();
        let Ok(recorded) = std::fs::read_to_string(dir.join(ROOT_RECORD)) else { continue };
        let vanished = matches!(std::fs::symlink_metadata(&recorded), Err(e) if e.kind() == std::io::ErrorKind::NotFound);
        if vanished {
            // 消せなければ次の走査でもう一度試す(答えには関わらない)
            let _ = std::fs::remove_dir_all(&dir);
        }
    }
    true
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

fn encode<T: Serialize + Sync>(identity: &str, entries: &[(&str, &Entry<T>)], format: Format) -> Option<Vec<u8>> {
    match format {
        Format::Json => {
            let stored = StoredRef { identity, entries: entries.iter().map(|(rel, entry)| (*rel, *entry)).collect() };
            serde_json::to_vec(&stored).ok()
        }
        Format::Compact => {
            let entries: Option<Vec<CompactEntry>> = entries
                .par_iter()
                .map(|(rel, entry)| {
                    let value = match &entry.value {
                        Some(value) => Some(bincode::serialize(value).ok()?),
                        None => None,
                    };
                    Some(CompactEntry { rel: rel.to_string(), stamp: entry.stamp, value })
                })
                .collect();
            bincode::serialize(&CompactStored { identity: identity.to_string(), entries: entries? }).ok()
        }
    }
}

/// 全部の塊を並べて読み、1 つの表にする(読めない・印の違う塊は無い物 — その塊の file は解析し直される)。
fn load_shards<T: DeserializeOwned + Send>(file: &Path, identity: &str, format: Format) -> HashMap<String, Entry<T>> {
    let shards: Vec<HashMap<String, Entry<T>>> = (0..SHARDS).into_par_iter().map(|n| load(&shard_path(file, n), identity, format)).collect();
    shards.into_iter().flatten().collect()
}

fn save<T: Serialize + Sync>(file: &Path, identity: &str, entries: &[(&str, &Entry<T>)], format: Format) {
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

/// 1 つの値を、呼び手が材料から作った鍵 `key` で覚える(file ごとではなく、repo 全体の事実から導いた値 — 例: effect の推論の不動点)。
/// 置いた値の鍵が `key` と同じなら読み、違えば(無い・壊れた・linter が組み直された時も)`compute` で作って置き直す。答えは変えない —
/// 鍵が材料を漏れなく覆うことは呼び手が保つ。形は binary(bincode)。
pub fn keyed_value<T, F>(root: &Path, kind: &str, key: u64, compute: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> T,
{
    #[derive(Serialize, Deserialize)]
    struct Keyed<V> {
        identity: String,
        key: u64,
        value: V,
    }
    /// 書く時の形(値を複製せずに借りて綴る — 綴りは `Keyed` と同じ)。
    #[derive(Serialize)]
    struct KeyedRef<'a, V> {
        identity: &'a str,
        key: u64,
        value: &'a V,
    }
    let Some(file) = cache_file(root, kind, Format::Compact) else { return compute() };
    let identity = format!("{}/", identity(kind));
    let cached = crate::timing::timed("facts-cache.load", || {
        std::fs::read(&file).ok().and_then(|bytes| bincode::deserialize::<Keyed<T>>(&bytes).ok())
    });
    if let Some(keyed) = cached.filter(|k| k.identity == identity && k.key == key) {
        return keyed.value;
    }
    let value = compute();
    crate::timing::timed("facts-cache.save", || {
        let Some(dir) = file.parent() else { return };
        if std::fs::create_dir_all(dir).is_err() {
            return;
        }
        let Ok(bytes) = bincode::serialize(&KeyedRef { identity: identity.as_str(), key, value: &value }) else { return };
        let tmp = dir.join(format!(".{kind}.{}.tmp", std::process::id()));
        if std::fs::write(&tmp, bytes).is_ok() && std::fs::rename(&tmp, &file).is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
    });
    value
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
    let mut known: HashMap<String, Entry<T>> = crate::timing::timed("facts-cache.load", || load_shards(file, identity, format));
    let loaded: Vec<String> = known.keys().cloned().collect();
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
    // 書き直す塊 = 再計算した file の塊と、cache に在ったのに今は無い(消えた・読めない)file の塊。
    let mut dirty = [false; SHARDS];
    for (rel, _, recomputed) in &fresh {
        if *recomputed {
            dirty[shard_of(rel)] = true;
        }
    }
    let present: std::collections::HashSet<&str> = fresh.iter().map(|(rel, _, _)| rel.as_str()).collect();
    for rel in loaded.iter().filter(|rel| !present.contains(rel.as_str())) {
        dirty[shard_of(rel)] = true;
    }
    if dirty.iter().any(|d| *d) {
        crate::timing::timed("facts-cache.save", || {
            let mut by_shard: Vec<Vec<(&str, &Entry<T>)>> = (0..SHARDS).map(|_| Vec::new()).collect();
            for (rel, entry, _) in &fresh {
                by_shard[shard_of(rel)].push((rel.as_str(), entry));
            }
            by_shard
                .par_iter()
                .enumerate()
                .filter(|(n, _)| dirty[*n])
                .for_each(|(n, entries)| save(&shard_path(file, n), identity, entries, format));
            // 前の形の 1 file は読まないので消す(頭の註 — 置き場は linter が持つ cache だけ)。
            let _ = std::fs::remove_file(file);
        });
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
            let shard = shard_path(&cache, shard_of("a.hy"));
            std::fs::create_dir_all(shard.parent().unwrap()).unwrap();
            std::fs::write(&shard, broken).unwrap();
            let calls = AtomicUsize::new(0);
            assert_eq!(run(&cache, "v1", &[("a.hy".to_string(), a)], &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.load(Ordering::SeqCst), 1);
        }
    }

    /// agora-redesign #1523: 1 file を変えた時に書き直すのはその file の塊だけ — 他の塊の file は書かない(更新時刻が変わらない)。
    /// 消えた file の塊も書き直し、前の形の 1 file は消す。
    #[test]
    fn only_the_shard_of_a_changed_file_is_written_again() {
        for format in [Format::Json, Format::Compact] {
            let dir = tempfile::tempdir().unwrap();
            // 塊の違う 2 つの file を選ぶ。
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
            let cache = dir.path().join("cache").join(format!("kind.{}", format.extension()));
            std::fs::create_dir_all(cache.parent().unwrap()).unwrap();
            std::fs::write(&cache, b"old single file").unwrap();
            let calls = AtomicUsize::new(0);
            run(&cache, "v1", &files, &calls, format);
            assert!(!cache.exists(), "前の形の 1 file が残った");
            let modified = |n: &str| std::fs::metadata(shard_path(&cache, shard_of(n))).unwrap().modified().unwrap();
            let (first_at, other_at) = (modified(&first), modified(&other));
            std::thread::sleep(std::time::Duration::from_millis(20));
            std::fs::write(&files[0].1, "(defk changed [])").unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk changed [])".to_string(), format!("(defk {other} [])")]);
            assert_ne!(modified(&first), first_at, "変えた file の塊を書き直していない");
            assert_eq!(modified(&other), other_at, "変えていない file の塊まで書き直した");
            // 消えた file の塊は書き直す(読み戻しても消えている)。
            std::thread::sleep(std::time::Duration::from_millis(20));
            std::fs::remove_file(&files[1].1).unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk changed [])".to_string()]);
            assert_ne!(modified(&other), other_at, "消えた file の塊を書き直していない");
        }
    }
}

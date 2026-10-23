//! file ごとの事実の cache(agora-redesign #1025)。
//!
//! 1 file の実行(書き込み直後の hook の `--stdin --path`)でも、repo 全体の事実が要る規則(DOEFF126 の defk の名・
//! DOEFF127 / 129 / 130 の effect の推論の世界)は、repo の Hy の file を全部読んで解析し直していた — 1 回の CPU の
//! 大半(#1022 の計器の実測)。file ごとの事実がその file の中身だけで決まる物について、事実を disk に置き、
//! 変わった file だけ解析し直す。答えは変えない(同じ file の中身からは同じ事実)。
//!
//! 置き場は 2 つ(作業木ごとに全部を作り直して 1 時間に約 3.4GB を zeus の USB の SSD へ書いていた — card ki-9743c30d49bc):
//! - 事実そのもの(`objects/<種類>.<形>/<hash の頭 2 字>/<残り>`): 1 file の事実を 1 つずつ、中身から決まる鍵で置く。鍵は
//!   linter の印・file の根からの path(`rel`)・中身の SHA-256 の hash。同じ中身の file は、作業木が違っても同じ事実を読む
//!   (checkout で更新時刻が新しくなっても作り直さない)。置いたら書き換えない(一時 file から rename)。
//! - 根ごとの表(`<根の dir>/<種類>.<形>.d/<塊>`): file の path → 大きさ・更新時刻と、その時の事実の鍵。大きさと更新時刻が前と
//!   同じなら中身を読まずに前の鍵で事実を引く(早い判じ)。違えば中身の hash から鍵を作り直し、置き場に在ればそれを読む。
//!
//! file の分け方(責務ごと — 設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW): この file = 公開の入口・file 1 つを引く手順・根ごとの表 /
//! `store.rs` = 事実の置き場(配置・鍵・符号化・更新時刻・事実の path を作れるのはここだけ)/ `sweep.rs` = 置き場の寿命(上限・古い順・消えた根)。
//! 事実を消せるのは `sweep.rs` だけ(引く手順は消さない — 事実は根をまたいで分け合うので、1 つの根の表からは「要らない」と決められない)。
//!
//! 鍵の外に事実を動かす物は無いこと(呼び手が保つ): 事実は file の中身・`rel`・呼び手が渡す鍵(`per_file_keyed`)だけで決まり、
//! 根の絶対 path や設定を読まない。根の絶対 path を値に持つ種類(Hy の索引・signature の事実)は、置く値に path を入れず(`rel`
//! 相当か空)、読んだ後で呼び手が `bind` で今の根の path を付ける — 置き場に作業木の path が残ると、別の作業木が読んだ事実が
//! 他の作業木の file を指す。この約束は呼び手の記憶に頼らず、置く所が見張る(`store::LeakGuard` — 値に今の根の絶対 path が入っていたら
//! 置かずに作り直す・1 度だけ stderr へ知らせる)。
//!
//! 全体の印: linter 自身の binary の大きさと更新時刻・版・事実の種類。linter が組み直されれば鍵が変わり、全部を作り直す
//! (事実の集め方が変わりうるため)。古い印の事実は読まれないまま上限の片づけで消える。
//!
//! 置き場の根は `$DOEFF_LINTER_CACHE_DIR`・無ければ `$XDG_CACHE_HOME/doeff-linter`・無ければ `~/.cache/doeff-linter`。根の下の
//! 根の path の hash の dir に表を、file の path で決まる SHARDS 個の塊(`00`〜)に分けて置く。
//! 根の dir には根の絶対 path(`root.path`)を記録し、1 時間ごとに置き場を走査して、記録した根が無くなった dir を消す
//! (`tend_roots` — agora-redesign #1903。記録の無い dir は消さない)。
//! 表を書くのは変わった塊だけ(行が変わった塊か、消えた file を含む塊)— 1 file を変えた直後の実行が、repo 全体の事実を
//! 丸ごと書き直していた(zeus で約 0.35 秒・agora-redesign #1523)。読むのは全部の塊を並べて読む。前の形(事実を塊に入れた形・
//! 1 file の形)は読まない — 読めない塊は無い物として扱い、作り直すだけで答えは変わらない。
//! 書くのは一時 file から rename する(並走する hook どうしで壊さない — 後に書いた方が勝つだけで、どちらも正しい事実)。
//! `DOEFF_LINTER_NO_CACHE` が在れば読みも書きもしない。読めない・壊れた塊や事実は無い物として扱う(その file の事実を解析し直す —
//! 答えは変わらない)。
//!
//! 事実の形は 2 つ: 既定は JSON(`<種類>.json`)。repo 全体の Hy の索引のように大きい種類は、欄の名前を持たない binary(bincode・
//! `<種類>.bin`)で置く(`per_file_compact` — JSON では agora の本線で 147MB・読みに 1.2 秒かかった・agora-redesign #1364)。
//! binary の形は値の欄の並びがそのまま綴りなので、値の型の serde の形が値によって欄を出したり出さなかったりすること
//! (`skip_serializing_if` を持つこと)が無いよう呼び手が保つ。表は形によらず binary。

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

pub use sweep::mark_used;

const NO_CACHE_ENV: &str = "DOEFF_LINTER_NO_CACHE";
const DIR_ENV: &str = "DOEFF_LINTER_CACHE_DIR";
/// 種類ごとの表の塊の数(頭の註)— 1 file の変更で書き直すのは約 1/SHARDS、読みで開くのは種類ごとに SHARDS 個。
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

/// 種類の表の置き場 file(前の形の 1 file の path)から、塊 n の path(`<置き場>.d/<n>`)。
fn shard_path(file: &Path, n: usize) -> PathBuf {
    let name = file.file_name().and_then(|n| n.to_str()).unwrap_or("facts");
    file.with_file_name(format!("{name}.d")).join(format!("{n:02}"))
}

/// file 1 つの早い判じの鍵(大きさ・更新時刻の ns)。
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

/// 根ごとの表の 1 行。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Row {
    rel: String,
    stamp: Stamp,
    /// None = 集める物が無い file(例: 定義を 1 つも持たない)— 無いことも覚えて読み直さない。
    object: Option<ObjectKey>,
}

#[derive(Serialize, Deserialize)]
struct Index {
    identity: String,
    rows: Vec<Row>,
}

/// 書く時の `Index` の形(行を複製せずに借りて綴る — 綴りは `Index` と同じ)。
#[derive(Serialize)]
struct IndexRef<'a> {
    identity: &'a str,
    rows: &'a [&'a Row],
}

/// linter 自身の印(binary の大きさと更新時刻・版)と事実の種類。
fn identity(kind: &str) -> String {
    let exe = std::env::current_exe().ok().and_then(|p| stamp_of(&p));
    let exe = exe.map(|s| format!("{}-{}", s.len, s.mtime_ns)).unwrap_or_else(|| "unknown".to_string());
    format!("{kind}/{}/{exe}", env!("CARGO_PKG_VERSION"))
}

/// 種類 1 つの置き場 — 根ごとの表(前の形の 1 file の path。塊は `shard_path`)と、根をまたいで分け合う事実の dir。
struct Place {
    index: PathBuf,
    objects: PathBuf,
    /// 根の絶対 path を持つ値を置かない見張り(`store::LeakGuard`)。
    guard: LeakGuard,
}

/// 置き場の根(頭の註 — `$DOEFF_LINTER_CACHE_DIR`・`$XDG_CACHE_HOME/doeff-linter`・`~/.cache/doeff-linter` の順)。
/// `DOEFF_LINTER_NO_CACHE` が在れば None。根の直下の dir は全部、上限の片づけ(sweep_over_cap)の対象 — 根の dir の外の置き場
/// (commit の hook の HEAD の木の結果・agora-redesign #2723)も、根の直下に dir を置いて印 `used`(mark_used)を付ければ同じ上限に入る。
/// 事実の置き場 `objects` は dir ごとには消されず、事実 1 つずつ、最後に使われた時刻の古い順に消される。
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

/// 全部の塊を並べて読み、1 つの表にする(読めない・印の違う塊は無い物 — その塊の file は引き直される)。
fn load_index(file: &Path, identity: &str) -> HashMap<String, Row> {
    let shards: Vec<Vec<Row>> = (0..SHARDS).into_par_iter().map(|n| read_index_shard(&shard_path(file, n), identity)).collect();
    shards.into_iter().flatten().map(|row| (row.rel.clone(), row)).collect()
}

/// 表の塊 1 つを書く。行が無ければ塊の file を消す(前の形の塊が残らない)。
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

/// `files`(根からの path と絶対 path)の事実を、変わった file だけ `compute` で作り直して返す(順は `files` の順・
/// 集める物が無い file は含めない)。`compute` はその file の中身と `rel` だけで事実を決めること(他の file も根の絶対 path も読まない)。
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
    per_file_at(place(root, kind, Format::Json).as_ref(), &format!("{}/{key}", identity(kind)), files, compute, no_bind, Format::Json)
}

/// `per_file` と同じ — ただし cache を欄の名前を持たない binary の形で置く(頭の註 — 大きい種類のため)。
pub fn per_file_compact<T, F>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_compact_bound(root, kind, files, compute, no_bind)
}

/// `per_file_compact` と同じ — ただし事実が根の絶対 path を持つ種類のため。`compute` は path を持たない事実(根の絶対 path を入れない形)を返し、
/// 返す前(置き場から読んだ時も作った時も)に `bind(事実, rel, 絶対 path)` で今の根の path を付ける。置く値には `bind` の前の形を置く
/// (頭の註 — 作業木の path が置き場に残ると、別の作業木が他の作業木の file を指す)。
pub fn per_file_compact_bound<T, F, B>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F, bind: B) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    per_file_at(place(root, kind, Format::Compact).as_ref(), &format!("{}/", identity(kind)), files, compute, bind, Format::Compact)
}

fn no_bind<T>(_value: &mut T, _rel: &str, _path: &Path) {}

/// 事実を引く範囲 — repo 全体の file を表で引く(全体の実行)か、少数の file を中身の hash で引く(1 file の実行 — `per_file_named`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reach {
    Whole,
    Named,
}

/// `reach` に応じて `per_file_keyed`(全体)か `per_file_named`(名指し)で引く — 呼び手が事実の作り方を 1 つだけ持つため。
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

/// `per_file_keyed` と同じ事実を、**少数の file だけ**表を通らずに引く(1 file の実行が、書いた file の名指す file だけを読む形)。
/// repo 全体の表(根ごとの塊 16 個・file ごとの更新時刻の比べ)は読まず書かず、file の中身の hash で事実を引く(置き場の事実は
/// `per_file_keyed` と同じ鍵 — 全体の実行が置いた事実を読み、ここが置いた事実を全体の実行が読む)。表に行を足さないので、次の全体の
/// 実行はこの file を表に載せ直す(答えは変わらない)。`files` の順に、集める物が無い file を除いて返す。
pub fn per_file_named<T, F>(root: &Path, kind: &str, key: &str, files: &[(String, PathBuf)], compute: F) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
{
    per_file_named_at(place(root, kind, Format::Json).as_ref(), &format!("{}/{key}", identity(kind)), files, compute, no_bind, Format::Json)
}

/// `per_file_named` の binary の形で、根の絶対 path を持つ種類のため(`per_file_compact_bound` と同じ約束 — `compute` は path を持たない事実を返し、
/// 返す前に `bind` で今の根の path を付ける)。
pub fn per_file_compact_named_bound<T, F, B>(root: &Path, kind: &str, files: &[(String, PathBuf)], compute: F, bind: B) -> Vec<T>
where
    T: Clone + Send + Sync + Serialize + DeserializeOwned,
    F: Fn(&str, &Path) -> Option<T> + Sync,
    B: Fn(&mut T, &str, &Path) + Sync,
{
    per_file_named_at(place(root, kind, Format::Compact).as_ref(), &format!("{}/", identity(kind)), files, compute, bind, Format::Compact)
}

/// file 1 つの事実を中身の hash から引く(`resolve` の、表の行を使わない半分)。置き場に在ればそれを読み、無ければ `compute` で作って置く。
/// 中身を読んでから作るまでの間に file が変わっていたら、鍵と事実が食い違うので置かない(答えはその時の file のまま)。
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

/// `per_file_named` の本体 — cache の置き場と印と形を受ける(置き場が None = cache を使わない)。
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

/// `per_file_named` の、**中身を既に読んだ file** のための形 — 1 file の実行が repo の Hy を歩きながら読んだ中身を、disk から読み直さずに引く
/// (読み直すと、file ごとに open と read がもう 1 回ずつ増える)。置き場の鍵は `per_file_keyed` と同じ(印・`key`・`rel`・中身の SHA-256)なので、全体の実行が
/// 置いた事実を読み、ここが置いた事実を全体の実行が読む。`contents` は(根からの path・中身)の列。結果は `contents` の順で、`compute` が None を返した file は None
/// (置かない — 表を通らないので、集める物が無い file を覚える行は無い。呼び手が空の事実で覚えさせたい時は `Some(空)` を返す)。
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

/// 中身が git の blob の id で決まる事実の置き場(card ki-d56ba76422ba・ki-40c8ca380713)— 中身を読まずに、別の作業木が置いた事実を引く。
/// 事実は塊ごとの(blob の id・事実)の表で置く(`blob_chunks`)。鍵は linter の印・種類・塊の名。事実は blob の中身だけで決まること(`rel` も根も
/// 読まない)を呼び手が保つ。置き場と上限の片づけは他の事実と同じ。
pub struct BlobFacts {
    place: Place,
    identity: String,
}

impl BlobFacts {
    /// `root` の `kind` の置き場を開く。cache を使わない時(`DOEFF_LINTER_NO_CACHE`・試験)は None。
    pub fn open(root: &Path, kind: &str) -> Option<BlobFacts> {
        Some(BlobFacts { place: place(root, kind, Format::Compact)?, identity: format!("{}/", identity(kind)) })
    }

    /// 塊の置き場(名 `slot` — 呼び手が index の並びの区切りで決める)の鍵。
    fn slot_key(&self, slot: &str) -> ObjectKey {
        object_key(&[self.identity.as_bytes(), b"slot", slot.as_bytes()])
    }

    /// 塊の置き場の(blob の id・事実)の表を読む(無い・壊れた物は None)。1 file の事実を 1 つずつ置くと、冷えた新しい作業木で repo の Hy の数だけ
    /// (約 4600)置き場の file を開いて disk を待つので、index の並びの塊ごとに 1 file にまとめて引く(card ki-40c8ca380713 — `blob_chunks`)。
    pub fn get_slot<T: DeserializeOwned>(&self, slot: &str) -> Option<Vec<([u8; 20], T)>> {
        read_object::<Vec<([u8; 20], T)>>(&self.place.objects, &self.slot_key(slot), Format::Compact)
    }

    /// 塊の置き場の表を置き直す。事実は blob の中身だけで決まるので、表のどの行も、いつ置いた物でも同じ blob には同じ事実 — 置き場の事実の中で、
    /// 同じ鍵の中身が替わる(行が増える・古い版の行が落ちる)のはこの種類だけ(一時 file からの rename なので、読む側は前か後の表を丸ごと読む)。
    pub fn put_slot<T: Serialize>(&self, slot: &str, entries: &[([u8; 20], T)]) {
        note_object_bytes(write_object(&self.place.objects, &self.slot_key(slot), &entries, Format::Compact, &self.place.guard));
    }
}

/// 1 つの値を、呼び手が材料から作った鍵 `key` で覚える(file ごとではなく、repo 全体の事実から導いた値 — 例: effect の推論の不動点)。
/// 置いた値の鍵が `key` と同じなら読み、違えば(無い・壊れた・linter が組み直された時も)`compute` で作って置く。答えは変えない —
/// 鍵が材料を漏れなく覆うことは呼び手が保つ。値は根をまたいで分け合う(根の絶対 path を持たない値だけ)。形は binary(bincode)。
pub fn keyed_value<T, F>(root: &Path, kind: &str, key: u64, compute: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> T,
{
    keyed_value_if(root, kind, key, || (compute(), true))
}

/// `keyed_value` の、置いてよい時だけ置く形 — `compute` は値と「置いてよいか」を返す(値が根の絶対 path を持つ時など、置き場が断る値を呼び手が
/// 先に外す。置き場が断ると 1 度 stderr へ知らせるので、断られるとわかっている値は置こうとしない)。置かなかった値も答えとして返す。
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

/// file 1 つの引いた結果 — 事実・表の行・表の塊を書き直すか・新しく置いた事実の byte。
struct Resolved<T> {
    value: Option<T>,
    /// None = 覚えない(読めない file・読む間に変わった file)。
    row: Option<Row>,
    changed: bool,
    written: u64,
}

/// file 1 つの事実を引く。表の行の大きさ・更新時刻が今と同じなら中身を読まず、行の鍵で事実を読む。違えば中身の hash から鍵を作り、
/// 置き場に在ればそれを読み(別の作業木が置いた事実)、無ければ `compute` で作って置く。
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
                // 事実が無い・壊れた(上限の片づけで消えた)— 中身から引き直す
            }
        }
    }
    let Ok(bytes) = std::fs::read(path) else {
        // 読めない file は覚えずに、`compute` の答えだけ返す(読めない時の答えは `compute` が決める)
        return Resolved { value: compute(rel, path), row: None, changed: cached.is_some(), written: 0 };
    };
    let digest = Sha256::digest(&bytes);
    drop(bytes);
    let key = object_key(&[identity.as_bytes(), rel.as_bytes(), digest.as_slice()]);
    let (value, written) = match read_object::<T>(&place.objects, &key, format) {
        Some(value) => (Some(value), 0),
        None => {
            let value = compute(rel, path);
            // 中身を読んでから作るまでの間に file が変わっていたら、鍵と事実が食い違うので覚えない(答えはその時の file のまま)。
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

/// `per_file` の本体 — cache の置き場と印と形を受ける(置き場が None = cache を使わない)。
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
    // 書き直す塊 = 行が変わった file の塊と、表に在ったのに今は無い(消えた・読めない)file の塊。
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
            // 行の無い塊の file は、書き直す塊でなくても消す(前の形の塊が、事実を持たないまま残らない)。
            by_shard
                .par_iter()
                .enumerate()
                .filter(|(n, rows)| dirty[*n] || rows.is_empty())
                .for_each(|(n, rows)| save_index_shard(&shard_path(&place.index, n), identity, rows));
            // 前の形の 1 file は読まないので消す(頭の註 — 置き場は linter が持つ cache だけ)。
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

    /// 根 `root` の表と、根をまたいで分け合う事実の置き場(`dir/objects` — 種類は `kind`)。
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

    /// dir の下の file の大きさの合計(一時 file も含む — 置いた byte を数える)。
    fn bytes_under(dir: &Path) -> u64 {
        walkdir::WalkDir::new(dir).into_iter().filter_map(Result::ok).filter(|e| e.file_type().is_file()).map(|e| e.metadata().unwrap().len()).sum()
    }

    fn set_mtime(path: &Path, at: SystemTime) {
        std::fs::File::options().write(true).open(path).unwrap().set_modified(at).unwrap();
    }

    /// 変わった file だけを読み直し、答えは変わらない — 形(JSON・binary)を問わず同じ筋書き。
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
    fn a_broken_index_or_fact_is_read_as_absent() {
        for (format, broken) in [(Format::Json, &b"{ not json"[..]), (Format::Compact, &b"\x01\x02"[..])] {
            let dir = tempfile::tempdir().unwrap();
            let a = dir.path().join("a.hy");
            std::fs::write(&a, "(defk a [])").unwrap();
            let files = vec![("a.hy".to_string(), a)];
            let cache = place_of(dir.path(), "cache", format);
            // 壊れた表の塊
            let shard = shard_path(&cache.index, shard_of("a.hy"));
            std::fs::create_dir_all(shard.parent().unwrap()).unwrap();
            std::fs::write(&shard, broken).unwrap();
            let calls = AtomicUsize::new(0);
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
            // 壊れた事実(表は正しい)— その file だけ作り直し、置き直す(次は読み直さない)
            let fact = walkdir::WalkDir::new(&cache.objects).into_iter().filter_map(Result::ok).find(|e| e.file_type().is_file()).unwrap();
            std::fs::write(fact.path(), broken).unwrap();
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
            assert_eq!(run(&cache, "v1", &files, &calls, format), vec!["(defk a [])".to_string()]);
            assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
        }
    }

    /// agora-redesign #1523: 1 file を変えた時に書き直す表はその file の塊だけ — 他の塊の file は書かない(更新時刻が変わらない)。
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
            let cache = place_of(dir.path(), "cache", format);
            std::fs::create_dir_all(cache.index.parent().unwrap()).unwrap();
            std::fs::write(&cache.index, b"old single file").unwrap();
            let calls = AtomicUsize::new(0);
            run(&cache, "v1", &files, &calls, format);
            assert!(!cache.index.exists(), "前の形の 1 file が残った");
            let modified = |n: &str| std::fs::metadata(shard_path(&cache.index, shard_of(n))).unwrap().modified().unwrap();
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
            assert!(!shard_path(&cache.index, shard_of(&other)).exists(), "行の無くなった塊の file が残った");
        }
    }

    /// card ki-9743c30d49bc: 同じ中身・同じ `rel` の file は、別の根(更新時刻が新しい別の作業木)でも事実を作り直さず、新しく書くのは
    /// 根ごとの表だけ(事実は 1 つも書かない)。
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
            // 2 本目の木は、中身が同じで更新時刻だけ違う(checkout し直した作業木)。
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
            assert_eq!(calls.swap(0, Ordering::SeqCst), 0, "同じ中身の file の事実を作り直した");
            assert_eq!(bytes_under(&second_place.objects), objects_before, "同じ中身の事実を置き直した");
            let written_second = bytes_under(&cache_dir) - written_first;
            assert!(written_second * 10 < written_first, "2 本目の根が新しく書いた byte {written_second} が 1 本目 {written_first} の 10% 以上");
        }
    }

    /// 事実の鍵は印・`rel`・中身 — どれかが違えば分け合わない。
    #[test]
    fn facts_are_shared_only_for_the_same_name_contents_and_identity() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("x.hy");
        std::fs::write(&path, "(defk x [])").unwrap();
        let calls = AtomicUsize::new(0);
        let format = Format::Json;
        run(&place_of(dir.path(), "r1", format), "v1", &[("x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // rel が違う(module の名が変わる — 事実は rel でも決まる)
        run(&place_of(dir.path(), "r2", format), "v1", &[("other/x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // 印が違う(linter の組み直し・鍵)
        run(&place_of(dir.path(), "r3", format), "v2", &[("x.hy".to_string(), path.clone())], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
        // 全部同じ — 別の根の表でも読み直さない
        run(&place_of(dir.path(), "r4", format), "v1", &[("x.hy".to_string(), path)], &calls, format);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 0);
    }

    /// 根の絶対 path を持つ事実は、置く値に path を入れず、読んだ後で今の根の path を付ける — 置き場に作業木の path が残らず、
    /// 別の作業木が読んでも自分の file を指す。
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
        // 置き場から読んだ(作り直していない)事実にも、今の根の path が付く。
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
        assert!(!stored.windows(dir_text.len()).any(|w| w == dir_text.as_bytes()), "置き場に根の絶対 path が残った");
    }

    /// 新しい種類が bind を忘れて、根の絶対 path を持つ値をそのまま返しても、置き場には置かれない(置き場に作業木の path が残らない)。
    /// 答えは作り直した値のままで、毎回作り直す。呼び手の検が通る種類にだけ頼らず、置く所が見張る(設計検証の盲検 B の候補)。
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
            assert_eq!(found, vec![path.to_string_lossy().into_owned()], "答えが作り直した値と違う");
        }
        assert_eq!(calls.load(Ordering::SeqCst), 2, "根の path を持つ値を置いて、2 回目に作り直さなかった");
        assert!(store::LEAKED.load(Ordering::Relaxed) >= before + 2);
        assert_eq!(bytes_under(&cache.objects), 0, "根の path を持つ値が置き場に置かれた");
    }

    /// 中身を読んでから事実を作るまでの間に file が変わったら、事実を置かず表にも覚えない(次の実行が引き直す)。
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
        assert!(!cache.objects.exists(), "食い違った事実を置いた");
        let calls = AtomicUsize::new(0);
        assert_eq!(run(&cache, "v1", &files, &calls, Format::Json), vec!["(defk after [])".to_string()]);
        assert_eq!(calls.swap(0, Ordering::SeqCst), 1);
    }

    /// 古い事実を読んだら最後に使われた時刻が今になる(上限の片づけで、よく読まれる事実を古い順の先頭にしない)。
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
        assert!(age.as_secs() < store::OBJECT_TOUCH_SECONDS, "読んだ事実の最後に使われた時刻が古いまま: {age:?}");
        assert_eq!(calls.load(Ordering::SeqCst), 1, "読んだだけで作り直した");
    }

    /// file の本体(`#[cfg(test)]` より前)から、コメントと行末の註を除いた文だけを取り出す — 名の走査が、説明の文に出る名で外れない。
    fn code_of(source: &str) -> String {
        let body = source.split("#[cfg(test)]").next().unwrap_or("");
        body.lines().map(|line| line.split("//").next().unwrap_or("")).collect::<Vec<_>>().join("\n")
    }

    /// `store::` に続けて code が使っている名(`store::a` と `use store::{a, b as c}` の両方)。
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

    /// 知識の持ち主を名の走査で守る(設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW の盲検 B — 引く手順が共有の事実を消す差分は、提示された検が全部通った)。
    /// rustc は兄弟の module どうしの見え方を限れない(`pub(super)` は親にも兄弟にも見える)ので、持ち主でない file が使わない名を、ここで確かめる。
    /// - 引く手順(このfile)が store から使うのは、事実を鍵で置く・読む口だけ。事実を並べて消す口(`list`・`remove`)を持てば、寿命の判断が 2 か所になる。
    /// - 片づけ(sweep)が store から使うのは、事実を並べて消す口と置き場の名だけ。事実を置く・読む・鍵を作る口を持てば、事実の中身の知識が漏れる。
    /// - store は寿命の決まり(上限・使っている根の秒・番の間隔)を持たない。
    #[test]
    fn each_file_uses_only_the_names_of_the_knowledge_it_owns() {
        let resolve = names_taken_from_store(&code_of(include_str!("facts_cache.rs")));
        let allowed_for_resolve = ["object_key", "read_object", "write_object", "Format", "LeakGuard", "ObjectKey", "OBJECTS_DIR"];
        assert!(!resolve.is_empty(), "引く手順が store の名を 1 つも使っていない — 走査が何も見ていない");
        for name in &resolve {
            assert!(allowed_for_resolve.contains(&name.as_str()), "引く手順が store の {name} を使っている — 事実を並べて消す口は片づけだけが持つ");
        }

        let sweep = names_taken_from_store(&code_of(include_str!("facts_cache/sweep.rs")));
        let allowed_for_sweep = ["self", "list", "remove", "OBJECTS_DIR"];
        assert!(sweep.iter().any(|name| name == "list") && sweep.iter().any(|name| name == "remove"), "片づけが事実を並べて消していない — 走査が何も見ていない");
        for name in &sweep {
            assert!(allowed_for_sweep.contains(&name.as_str()), "片づけが store の {name} を使っている — 事実を置く・読む・鍵を作る口は store と引く手順のもの");
        }

        let store = code_of(include_str!("facts_cache/store.rs"));
        for name in ["IN_USE_SECONDS", "DEFAULT_CAP_BYTES", "CAP_ENV", "cache_cap_bytes", "SWEEP_INTERVAL_SECONDS"] {
            assert!(!store.contains(name), "store が寿命の決まり {name} を持っている — 上限と古い順は片づけが決める");
        }
    }
}

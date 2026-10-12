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
//! 鍵の外に事実を動かす物は無いこと(呼び手が保つ): 事実は file の中身・`rel`・呼び手が渡す鍵(`per_file_keyed`)だけで決まり、
//! 根の絶対 path や設定を読まない。根の絶対 path を値に持つ種類(Hy の索引・signature の事実)は、置く値に path を入れず(`rel`
//! 相当か空)、読んだ後で呼び手が `bind` で今の根の path を付ける — 置き場に作業木の path が残ると、別の作業木が読んだ事実が
//! 他の作業木の file を指す。
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
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const NO_CACHE_ENV: &str = "DOEFF_LINTER_NO_CACHE";
const DIR_ENV: &str = "DOEFF_LINTER_CACHE_DIR";
/// 種類ごとの表の塊の数(頭の註)— 1 file の変更で書き直すのは約 1/SHARDS、読みで開くのは種類ごとに SHARDS 個。
const SHARDS: usize = 16;

/// 根をまたいで分け合う事実の置き場の dir の名(置き場の根の直下)。
const OBJECTS_DIR: &str = "objects";
/// 事実を読んだ時、最後に使われた時刻(file の更新時刻)がこの秒より古ければ今に更新する — 上限を超えた時に、よく読まれる事実
/// (どの作業木も使う main の file)を古い順の先頭にしないため。事実の読みのたびには書かない。
const OBJECT_TOUCH_SECONDS: u64 = 6 * 3600;
/// 書きかけの一時 file がこの秒より古ければ、打ち切られた実行の残りとして片づけで消す。
const OBJECT_TMP_STALE_SECONDS: u64 = 3600;
/// 上限を数える時、事実 1 つの大きさの下限(byte)— 空に近い事実も disk では 1 block を使う。
const OBJECT_MIN_BYTES: u64 = 4096;
/// 1 つの実行が新しく置いた事実の合計がこの byte を越えたら、番の間隔に依らず上限を確かめる(linter の組み直しの後の最初の
/// 実行は、事実を全部置き直す — 約 200MB)。
const GROWTH_CHECK_BYTES: u64 = 32 * 1024 * 1024;

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

/// 事実 1 つの置き場の鍵(中身から決まる hash の頭 16 byte)。
type ObjectKey = [u8; 16];

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

/// 事実の形(頭の註)。
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

/// 種類 1 つの置き場 — 根ごとの表(前の形の 1 file の path。塊は `shard_path`)と、根をまたいで分け合う事実の dir。
struct Place {
    index: PathBuf,
    objects: PathBuf,
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
    Some(Place { index: root_dir.join(&name), objects: base.join(OBJECTS_DIR).join(name) })
}

/// 根の dir の中の、その dir を作った根の path の記録(agora-redesign #1903)。
const ROOT_RECORD: &str = "root.path";
/// 置き場の、前に片づけの走査をした時刻(UNIX 秒)の記録。
const SWEEP_RECORD: &str = ".swept";
/// 片づけの走査の間隔(秒)— linter は file を書くたびに hook から走るので、毎回は走査しない。
const SWEEP_INTERVAL_SECONDS: u64 = 3600;

/// この実行の置き場の根と根の dir(上限の片づけを後から呼ぶ時に要る — `note_object_bytes`)。
static TENDED_AT: std::sync::OnceLock<(PathBuf, PathBuf)> = std::sync::OnceLock::new();

/// 根の dir は根の path の hash で決まるので、作業木や品質検査の一時の写しのように消えた根の dir は、誰も読まないまま残り続けた
/// (zeus で 4136 dir・21.6G — agora-redesign #1903)。書く側の 1 点で、根の dir に根の path を記録し、間隔を空けて置き場を走査して、
/// 記録した根がもう無い dir を消す。記録の無い dir(この仕組みより前に作られた dir)は消さない — 消してよいかは別に決める(#1472)。
/// 時間だけを理由には消さない。消しても答えは変わらない(読めない塊は作り直す — 頭の註)。1 つの実行で 1 度だけ行う。
fn tend_roots(base: &Path, root_dir: &Path, root: &Path) {
    static TENDED: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    TENDED.get_or_init(|| {
        // この実行が根の dir を新しく作るか(置き場の全体が増える時 — 根の表の約 2MB。前の形では作業木 1 つで約 176MB)。増える時は
        // 上限の片づけの番の間隔に依らず、その場で根の dir の上限を確かめる(sweep_over_cap_when_due)。
        let grows = !root_dir.join(ROOT_RECORD).exists();
        record_root(root_dir, root);
        mark_used(root_dir);
        // 事実の置き場も、根の dir と同じに使った印を付ける(置き場を知らない古い版の linter は、置き場の根の直下の dir を全部、根の dir
        // として最後に使われた時刻の古い順に消すので、印が古いと事実の置き場ごと消される)。
        mark_used(&base.join(OBJECTS_DIR));
        let _ = TENDED_AT.set((base.to_path_buf(), root_dir.to_path_buf()));
        // 時計が 1970 年より前を指す機体では間隔を測れないので走査しない
        if let Ok(now) = std::time::SystemTime::now().duration_since(UNIX_EPOCH) {
            sweep_vanished_roots(base, now.as_secs());
            let growth = if grows { Growth::Roots } else { Growth::None };
            sweep_over_cap_when_due(base, root_dir, cache_cap_bytes(), now.as_secs(), growth);
        }
    });
}

/// この実行が新しく置いた事実の byte を数え、合計が GROWTH_CHECK_BYTES を越えた時に 1 度だけ、事実の置き場を含めて上限を確かめる。
fn note_object_bytes(written: u64) {
    static TOTAL: AtomicU64 = AtomicU64::new(0);
    static CHECKED: AtomicBool = AtomicBool::new(false);
    if written == 0 || TOTAL.fetch_add(written, Ordering::Relaxed) + written < GROWTH_CHECK_BYTES || CHECKED.swap(true, Ordering::Relaxed) {
        return;
    }
    let (Some((base, root_dir)), Ok(now)) = (TENDED_AT.get(), SystemTime::now().duration_since(UNIX_EPOCH)) else { return };
    sweep_over_cap_when_due(base, root_dir, cache_cap_bytes(), now.as_secs(), Growth::Objects);
}

/// 根の dir の、最後に使われた時刻の印(中身は空 — file の更新時刻が印)。上限を超えた時に古い順を決める(agora-redesign #2725)。
const USED_RECORD: &str = "used";
/// 置き場の全体の上限を変える環境変数(byte)。
const CAP_ENV: &str = "DOEFF_LINTER_CACHE_MAX_BYTES";
/// 置き場の全体の上限の既定(4 GiB)— 前の形では作業木 1 つの根の dir が約 176MB(2026-10-10 の zeus の実測 — 大半は repo 全体の Hy の索引
/// hy-index.bin.d の 156MB)で、約 23 の根を持てた。前の既定 10 GiB(根 1 つ 65MB の見積もり)では zeus に 7.9G 溜まり、worker の
/// disk が新しい版の準備の下限を割った(agora の card acp:kanban-issue:ki-f3bbb3ca4667)。消した根は次の実行で作り直す(答えは変わらない)。
/// 戻し方 = この値を戻す(環境変数 CAP_ENV でも機体ごとに変えられる)。
const DEFAULT_CAP_BYTES: u64 = 4 * 1024 * 1024 * 1024;
/// 上限を越えても消さない「使っている根」の長さ(秒)— この秒の内に使われた dir(並走する実行が使っている根)。前は番の間隔と同じ
/// 1 時間で、作業の多い時はほぼ全部の根が守られて上限の内へ戻せなかった(ki-f3bbb3ca4667)。
const IN_USE_SECONDS: u64 = 600;

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

/// 上限の片づけの、前に終えた時刻(UNIX 秒)の記録。根が消えた dir の片づけの記録 `.swept` とは分ける — `.swept` は上限の片づけを
/// 持たない古い版の linter も書くので、それと番を分け合うと、古い版が番を取った間は上限の片づけが走らない(zeus の 2026-10-02 12:50 の
/// 走査で 63G のまま 0 件 — agora-redesign #2725)。
const CAP_SWEEP_RECORD: &str = ".swept-cap";
/// 上限の片づけの最中の印(中身は空)。並走する実行が同じ片づけを重ねないため。打ち切られた実行が残した印は、この秒を過ぎたら無いものとする。
const CAP_SWEEP_LOCK: &str = ".swept-cap.lock";
const CAP_SWEEP_LOCK_STALE_SECONDS: u64 = 600;

/// 置き場が増えた事の知らせ方(sweep_over_cap_when_due)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Growth {
    /// 増えていない — 番の間隔が過ぎた時だけ片づける。
    None,
    /// この実行が根の dir を新しく作った — 番の間隔に依らず、根の dir だけで上限を確かめる(事実の置き場は歩かない — 作業木が
    /// 増えるたびに事実を 1 つずつ数えるのは重い)。
    Roots,
    /// この実行が新しい事実を多く置いた(linter の組み直しの後) — 番の間隔に依らず、事実の置き場も含めて確かめる。
    Objects,
}

/// 前に上限の片づけを終えてから間隔が過ぎているか、置き場の全体が増えた(growth — 頭の註)なら、上限の片づけをする。
/// 終えた時刻は、事実の置き場も含めた片づけが終わってから書く — commit の hook の秒の上限などで打ち切られた実行は記録を書かないので、
/// 次の実行がやり直す(番だけ取って終わる形を作らない)。増える時に確かめないと、番の間隔の内に作業木が増えた分だけ上限を越えたままに
/// なる(zeus の 7.9G — ki-f3bbb3ca4667)。並走する実行の片づけの最中は、増える時も重ねない。
fn sweep_over_cap_when_due(base: &Path, root_dir: &Path, cap: u64, now: u64, growth: Growth) {
    let stamp = base.join(CAP_SWEEP_RECORD);
    let last = std::fs::read_to_string(&stamp).ok().and_then(|text| text.trim().parse::<u64>().ok());
    let due = !last.is_some_and(|last| now.saturating_sub(last) < SWEEP_INTERVAL_SECONDS);
    if !due && growth == Growth::None {
        return;
    }
    let lock = base.join(CAP_SWEEP_LOCK);
    let held_by_other = std::fs::metadata(&lock)
        .and_then(|meta| meta.modified())
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .is_some_and(|at| now.saturating_sub(at.as_secs()) < CAP_SWEEP_LOCK_STALE_SECONDS);
    if held_by_other || std::fs::create_dir_all(base).is_err() || std::fs::write(&lock, b"").is_err() {
        return;
    }
    let with_objects = due || growth == Growth::Objects;
    sweep_over_cap(base, root_dir, cap, now, with_objects);
    if with_objects {
        let _ = std::fs::write(&stamp, now.to_string());
    }
    let _ = std::fs::remove_file(&lock);
}

/// 事実の置き場の file(事実 1 つ = 1 file)を、(最後に使われた時刻・数える大きさ・path)で並べる。打ち切られた実行が残した古い
/// 一時 file はここで消す。
fn object_files(objects: &Path, now: u64) -> Vec<(u64, u64, PathBuf)> {
    let fans: Vec<PathBuf> = std::fs::read_dir(objects)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|kind| kind.file_type().is_ok_and(|t| t.is_dir()))
        .flat_map(|kind| std::fs::read_dir(kind.path()).into_iter().flatten().flatten().collect::<Vec<_>>())
        .filter(|fan| fan.file_type().is_ok_and(|t| t.is_dir()))
        .map(|fan| fan.path())
        .collect();
    fans.par_iter()
        .flat_map_iter(|fan| {
            std::fs::read_dir(fan).into_iter().flatten().flatten().filter_map(move |entry| {
                let meta = entry.metadata().ok().filter(|meta| meta.is_file())?;
                let modified = meta.modified().ok().and_then(|time| time.duration_since(UNIX_EPOCH).ok()).map_or(0, |d| d.as_secs());
                let path = entry.path();
                let temporary = entry.file_name().to_str().is_some_and(|name| name.starts_with('.') && name.ends_with(".tmp"));
                if temporary {
                    if now.saturating_sub(modified) >= OBJECT_TMP_STALE_SECONDS {
                        let _ = std::fs::remove_file(&path);
                    }
                    return None;
                }
                Some((modified, meta.len().max(OBJECT_MIN_BYTES), path))
            })
        })
        .collect()
}

/// 置き場の全体が上限 cap を超えていれば、最後に使われた時刻の古い dir から消して上限の内へ戻し、それでも超えていれば事実を
/// 最後に使われた時刻の古い順に消す(agora-redesign #2725)。根が在り続ける作業木の dir は sweep_vanished_roots では消えず、根の数だけ
/// 増え続けた(zeus で 62G・1 時間に約 4G)。この実行の根の dir と、IN_USE_SECONDS の内に使われた dir・事実(並走する実行が使っている
/// 根)は消さない — 上限を一時に超えても、使っている根を消して作り直させない。消しても答えは変わらない(読めない塊や事実は作り直す —
/// 頭の註)。`with_objects` が偽なら事実の置き場は数えない(根の dir だけで確かめる — 置き場の事実は数が多く、歩くのが重い)。
fn sweep_over_cap(base: &Path, root_dir: &Path, cap: u64, now: u64, with_objects: bool) {
    let Ok(entries) = std::fs::read_dir(base) else { return };
    let mut dirs: Vec<(u64, u64, PathBuf)> = entries
        .flatten()
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()) && entry.file_name() != OBJECTS_DIR)
        .map(|entry| {
            let dir = entry.path();
            (last_used(&dir), dir_bytes(&dir), dir)
        })
        .collect();
    let mut objects = if with_objects { object_files(&base.join(OBJECTS_DIR), now) } else { Vec::new() };
    let mut total: u64 = dirs.iter().map(|(_, bytes, _)| bytes).sum::<u64>() + objects.iter().map(|(_, bytes, _)| bytes).sum::<u64>();
    if total <= cap {
        return;
    }
    dirs.sort_by_key(|(used, _, _)| *used);
    for (used, bytes, dir) in dirs {
        if total <= cap {
            break;
        }
        if dir == root_dir || now.saturating_sub(used) < IN_USE_SECONDS {
            continue;
        }
        // 消せなければ次の走査でもう一度試す(答えには関わらない)
        if std::fs::remove_dir_all(&dir).is_ok() {
            total = total.saturating_sub(bytes);
        }
    }
    objects.sort_by_key(|(used, _, _)| *used);
    for (used, bytes, path) in objects {
        if total <= cap {
            break;
        }
        if now.saturating_sub(used) < IN_USE_SECONDS {
            continue;
        }
        if std::fs::remove_file(&path).is_ok() {
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
/// 上限の片づけは別の記録で番を数える(sweep_over_cap_when_due)。
fn sweep_vanished_roots(base: &Path, now: u64) {
    let stamp = base.join(SWEEP_RECORD);
    let last = std::fs::read_to_string(&stamp).ok().and_then(|text| text.trim().parse::<u64>().ok());
    if last.is_some_and(|last| now.saturating_sub(last) < SWEEP_INTERVAL_SECONDS) {
        return;
    }
    if std::fs::create_dir_all(base).is_err() || std::fs::write(&stamp, now.to_string()).is_err() {
        return;
    }
    let Ok(entries) = std::fs::read_dir(base) else { return };
    for entry in entries.flatten() {
        let dir = entry.path();
        let Ok(recorded) = std::fs::read_to_string(dir.join(ROOT_RECORD)) else { continue };
        let vanished = matches!(std::fs::symlink_metadata(&recorded), Err(e) if e.kind() == std::io::ErrorKind::NotFound);
        if vanished {
            // 消せなければ次の走査でもう一度試す(答えには関わらない)
            let _ = std::fs::remove_dir_all(&dir);
        }
    }
}

/// 鍵の材料(それぞれの長さを前に付けて続ける — 材料の切れ目が違えば鍵も違う)から、事実の置き場の鍵を作る。
fn object_key(parts: &[&[u8]]) -> ObjectKey {
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

/// 事実 1 つの file の path(`<置き場>/<hash の頭 2 字>/<残り>` — 1 つの dir に file を集めすぎない)。
fn object_path(objects: &Path, key: &ObjectKey) -> PathBuf {
    let name: String = key.iter().map(|byte| format!("{byte:02x}")).collect();
    objects.join(&name[..2]).join(&name[2..])
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

/// 事実を読む(無い・壊れた・形の違う事実は None — 呼び手が作り直す)。最後に使われた時刻が OBJECT_TOUCH_SECONDS より古ければ今にする。
fn read_object<T: DeserializeOwned>(objects: &Path, key: &ObjectKey, format: Format) -> Option<T> {
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
        // 印を付けられなくても答えには関わらない(古い順が少しずれるだけ)
        let _ = file.set_modified(SystemTime::now()).or_else(|_| {
            std::fs::File::options().write(true).open(&path).and_then(|writable| writable.set_modified(SystemTime::now()))
        });
    }
    Some(value)
}

/// 事実を置く(一時 file から rename)。置いた byte を返す(置けなければ 0)。
fn write_object<T: Serialize>(objects: &Path, key: &ObjectKey, value: &T, format: Format) -> u64 {
    let path = object_path(objects, key);
    let Some(dir) = path.parent() else { return 0 };
    if std::fs::create_dir_all(dir).is_err() {
        return 0;
    }
    let Some(bytes) = encode_value(value, format) else { return 0 };
    let tmp = dir.join(format!(".{}.{}.tmp", path.file_name().and_then(|n| n.to_str()).unwrap_or("object"), std::process::id()));
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

/// 1 つの値を、呼び手が材料から作った鍵 `key` で覚える(file ごとではなく、repo 全体の事実から導いた値 — 例: effect の推論の不動点)。
/// 置いた値の鍵が `key` と同じなら読み、違えば(無い・壊れた・linter が組み直された時も)`compute` で作って置く。答えは変えない —
/// 鍵が材料を漏れなく覆うことは呼び手が保つ。値は根をまたいで分け合う(根の絶対 path を持たない値だけ)。形は binary(bincode)。
pub fn keyed_value<T, F>(root: &Path, kind: &str, key: u64, compute: F) -> T
where
    T: Serialize + DeserializeOwned,
    F: FnOnce() -> T,
{
    let Some(place) = place(root, kind, Format::Compact) else { return compute() };
    let identity = format!("{}/", identity(kind));
    let object = object_key(&[identity.as_bytes(), b"keyed-value", &key.to_le_bytes()]);
    let cached = crate::timing::timed("facts-cache.load", || read_object::<T>(&place.objects, &object, Format::Compact));
    if let Some(value) = cached {
        return value;
    }
    let value = compute();
    let written = crate::timing::timed("facts-cache.save", || write_object(&place.objects, &object, &value, Format::Compact));
    note_object_bytes(written);
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
            let written = value.as_ref().map_or(0, |value| write_object(&place.objects, &key, value, format));
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

    /// 根 `root` の表と、根をまたいで分け合う事実の置き場(`dir/objects` — 種類は `kind`)。
    fn place_of(dir: &Path, root: &str, format: Format) -> Place {
        let name = format!("kind.{}", format.extension());
        Place { index: dir.join(root).join(&name), objects: dir.join(OBJECTS_DIR).join(name) }
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
        let old = SystemTime::now() - std::time::Duration::from_secs(2 * OBJECT_TOUCH_SECONDS);
        set_mtime(&fact, old);
        run(&cache, "v1", &files, &calls, Format::Json);
        let age = SystemTime::now().duration_since(std::fs::metadata(&fact).unwrap().modified().unwrap()).unwrap();
        assert!(age.as_secs() < OBJECT_TOUCH_SECONDS, "読んだ事実の最後に使われた時刻が古いまま: {age:?}");
        assert_eq!(calls.load(Ordering::SeqCst), 1, "読んだだけで作り直した");
    }
}

//! 置き場の寿命(M-sweep)— 上限・古い順・消えた根・一時 file。置き場の中身(事実・表)の形は知らない。
//!
//! 持つ知識: 上限(既定 4 GiB)・使っている根として守る 10 分・番の間隔と記録の file・根の dir を先に消し、それでも超えていれば事実を古い順に
//! 消す順・`objects` の使った印。事実の配置は知らず、事実は `store::list` が返した物を `store::remove` で消すだけ(事実の path を作れない)。
//! 消しても答えは変わらない(読めない塊や事実は作り直す — 親の頭の註)。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use super::store::{self, OBJECTS_DIR};

/// 1 つの実行が新しく置いた事実の合計がこの byte を越えたら、番の間隔に依らず上限を確かめる(linter の組み直しの後の最初の
/// 実行は、事実を全部置き直す — 約 200MB)。
const GROWTH_CHECK_BYTES: u64 = 32 * 1024 * 1024;

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
pub(super) fn tend_roots(base: &Path, root_dir: &Path, root: &Path) {
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
pub(super) fn note_object_bytes(written: u64) {
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

/// この実行が根の dir を使った印を付ける(1 つの実行で 1 度)。commit の hook の HEAD の木の結果の dir も、読み書きのたびにこれで
/// 印を付ける(agora-redesign #2723)。
pub fn mark_used(root_dir: &Path) {
    if std::fs::create_dir_all(root_dir).is_ok() {
        let _ = std::fs::write(root_dir.join(USED_RECORD), b"");
    }
}

/// 置き場の全体の上限(環境変数が読めない値なら既定)。
fn cache_cap_bytes() -> u64 {
    std::env::var(CAP_ENV).ok().and_then(|text| text.trim().parse::<u64>().ok()).unwrap_or(DEFAULT_CAP_BYTES)
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
    // 事実の置き場を数える片づけ(zeus で約 40 万の事実・3〜19 秒)は、書き込み直後の hook の期限(4 秒)の内で待たない — 最中の印を
    // 持ったまま別の process へ渡し、渡した process が終わりに印を外す(card ki-40c8ca380713)。渡せなければこの実行で片づける。
    if with_objects && std::env::var_os(SWEEP_INLINE_ENV).is_none() && hand_off(base, root_dir, cap, now) {
        crate::timing::count("facts-cache.sweep", "handed", 1);
        return;
    }
    finish_sweep(base, root_dir, cap, now, with_objects);
}

/// 上限の片づけをして、事実の置き場まで数えたなら終えた時刻を記録し、最中の印を外す(この実行の中でも、渡された process でも同じ手順)。
fn finish_sweep(base: &Path, root_dir: &Path, cap: u64, now: u64, with_objects: bool) {
    sweep_over_cap(base, root_dir, cap, now, with_objects);
    if with_objects {
        let _ = std::fs::write(base.join(CAP_SWEEP_RECORD), now.to_string());
    }
    let _ = std::fs::remove_file(base.join(CAP_SWEEP_LOCK));
}

/// 片づけを実行の中で行わせる環境変数(在れば)— 検が、実行の直後に置き場の片づけの結果を見るため。既定は別の process へ渡して待たない。
pub const SWEEP_INLINE_ENV: &str = "DOEFF_LINTER_SWEEP_INLINE";
/// 渡された片づけを走らせる、linter 自身の命令の口(`doeff-linter sweep-facts-cache <置き場> <根の dir> <上限> <時刻>` — main が見分ける)。
pub const SWEEP_COMMAND: &str = "sweep-facts-cache";

/// 事実の置き場まで数える片づけを、待たない別の process(linter 自身の `SWEEP_COMMAND`)へ渡す。渡した process は新しい session で、
/// 標準の入出力を持たない — hook が自分の process の組を打ち切っても止まらず、hook の出力の終わりを待たせない。渡せたら真。
fn hand_off(base: &Path, root_dir: &Path, cap: u64, now: u64) -> bool {
    let Ok(exe) = std::env::current_exe() else { return false };
    let mut command = std::process::Command::new(exe);
    command
        .arg(SWEEP_COMMAND)
        .arg(base)
        .arg(root_dir)
        .arg(cap.to_string())
        .arg(now.to_string())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: fork の後・exec の前の子で呼ぶのは setsid だけ(async-signal-safe で、memory を触らない)。
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
    }
    command.spawn().is_ok()
}

/// `SWEEP_COMMAND` の本体 — 渡した実行が最中の印を持ったまま渡した片づけを、低い優先度で済ませて印を外す。引数が読めなければ 2。
pub fn run_handed_sweep(args: &[String]) -> std::process::ExitCode {
    let [base, root_dir, cap, now] = args else { return std::process::ExitCode::from(2) };
    let (Ok(cap), Ok(now)) = (cap.parse::<u64>(), now.parse::<u64>()) else { return std::process::ExitCode::from(2) };
    // SAFETY: nice は自分の process の優先度を変えるだけ(memory を触らない)。下げられなくても片づけは同じ。
    #[cfg(unix)]
    unsafe {
        libc::nice(10);
    }
    finish_sweep(Path::new(base), Path::new(root_dir), cap, now, true);
    std::process::ExitCode::SUCCESS
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
    let mut objects = if with_objects { store::list(&base.join(OBJECTS_DIR), now) } else { Vec::new() };
    let mut total: u64 = dirs.iter().map(|(_, bytes, _)| bytes).sum::<u64>() + objects.iter().map(|object| object.bytes).sum::<u64>();
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
    // 事実は最後に使われた時刻の古い順。置いてから(か読んで更新してから)IN_USE_SECONDS の内の事実は守る。読んだ事実の更新時刻は
    // store の OBJECT_TOUCH_SECONDS(1 日)の精度でしか付け直されないので、「読まれている事実」を守る物ではない(読めなければ作り直す)。
    objects.sort_by_key(|object| object.used);
    for object in objects {
        if total <= cap {
            break;
        }
        if now.saturating_sub(object.used) < IN_USE_SECONDS {
            continue;
        }
        let bytes = object.bytes;
        if store::remove(object) {
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

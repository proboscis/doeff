//! 事実の置き場(M-store)— 作業木をまたいで分け合う事実を、1 つずつ file で置く・読む・並べる・消す。
//!
//! この file が持つ知識: 置き場の配置(dir の段・file の名・一時 file の名)・鍵の作り方・符号化・更新時刻の取り決め。
//! 他の file はこれを知らない — 事実の path を作れるのはここだけ(`object_path` は非公開)で、他の file は鍵で読み・置き、片づけは
//! `list` が返した物だけを `remove` する。配置を替えても(段を増やす・符号化を替える)直すのはこの file だけ。
//!
//! 取り決め:
//! - 事実は置いたら書き換えない(一時 file から rename)。同じ鍵なら同じ内容。消すのは片づけ(sweep)だけで、片づけの外(引く手順)は消さない —
//!   事実は根をまたいで分け合うので、1 つの根の表だけからは「もう要らない」と決められない(card ki-9743c30d49bc の設計検証・盲検 B)。
//! - file の更新時刻 = 最後に使われた時刻の目安。置いた時に付き、読んだ時は OBJECT_TOUCH_SECONDS より古ければ今にする。精度は
//!   OBJECT_TOUCH_SECONDS(1 日)で、「読まれている事実」を片づけから守る物ではない — 読めなくなれば呼び手が作り直す(答えは変わらない)。
//!   守るのは「置いてから(か読んで更新してから)IN_USE_SECONDS の内の事実」だけ(sweep が判じる)。
//! - 読めない・壊れた・形の違う事実は None(呼び手が作り直す)。

use rayon::prelude::*;
use serde::de::DeserializeOwned;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// 事実の形(頭の註)。
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

/// 根をまたいで分け合う事実の置き場の dir の名(置き場の根の直下)。
pub(super) const OBJECTS_DIR: &str = "objects";
/// 事実を読んだ時、最後に使われた時刻(file の更新時刻)がこの秒より古ければ今に更新する — 上限を超えた時に、よく読まれる事実
/// (どの作業木も使う main の file)を古い順の先頭にしないため。1 日にしてあるのは、更新時刻を付け直すたびに file が「更新された」と数えられ
/// (card ki-fe848da94d24 の測りは更新時刻で数える)、事実の 1 日分の 1 だけが毎時間の書きに混ざるのを許す大きさにするため。
pub(super) const OBJECT_TOUCH_SECONDS: u64 = 24 * 3600;
/// 書きかけの一時 file がこの秒より古ければ、打ち切られた実行の残りとして並べる時に消す。
const OBJECT_TMP_STALE_SECONDS: u64 = 3600;
/// 上限を数える時、事実 1 つの大きさの下限(byte)— 空に近い事実も disk では 1 block を使う。
const OBJECT_MIN_BYTES: u64 = 4096;

/// 事実 1 つの置き場の鍵(中身から決まる hash の頭 16 byte)。
pub(super) type ObjectKey = [u8; 16];

/// 鍵の材料(それぞれの長さを前に付けて続ける — 材料の切れ目が違えば鍵も違う)から、事実の置き場の鍵を作る。
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

/// 事実 1 つの file の path(`<置き場>/<hash の頭 2 字>/<残り>` — 1 つの dir に file を集めすぎない)。非公開 — 配置の知識はこの file だけが持つ。
fn object_path(objects: &Path, key: &ObjectKey) -> PathBuf {
    let name: String = key.iter().map(|byte| format!("{byte:02x}")).collect();
    objects.join(&name[..2]).join(&name[2..])
}

/// 事実を置く途中の一時 file の path(事実の file と同じ dir・`.<名>.<pid>.tmp`)。
fn temporary_path(object: &Path) -> PathBuf {
    let name = object.file_name().and_then(|n| n.to_str()).unwrap_or("object");
    object.with_file_name(format!(".{name}.{}.tmp", std::process::id()))
}

/// 一時 file の名か(`temporary_path` が作る名 — 事実を並べる時に見分ける)。
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

/// 値に今の根の絶対 path が入っていないかの見張り(置く前に事実の綴りを調べる)。根の絶対 path を持つ値は作業木ごとに綴りが違うので、置き場に
/// 置くと別の作業木が読んだ事実が他の作業木の file を指す — 呼び手が path を持たない形で置き、読んだ後に bind で付ける約束(facts_cache の
/// 頭の註)を、呼び手の記憶ではなく置く所で確かめる(設計検証の盲検 B — 新しい種類が約束を忘れても、置かずに作り直すだけで答えは正しい)。
pub(super) struct LeakGuard {
    needles: Vec<Vec<u8>>,
}

/// 見張りが破られた(根の絶対 path を持つ値を置こうとした)回数。検が読む。
pub(super) static LEAKED: AtomicUsize = AtomicUsize::new(0);

impl LeakGuard {
    /// 根の絶対 path(与えられた綴りと正規化した綴り)を探す綴りにする。短すぎる綴り(`/` など)は、どの値にも当たってしまうので使わない。
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

/// 事実を読む(無い・壊れた・形の違う事実は None — 呼び手が作り直す)。最後に使われた時刻が OBJECT_TOUCH_SECONDS より古ければ今にする。
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
        // 印を付けられなくても答えには関わらない(古い順が少しずれるだけ)
        let _ = file.set_modified(SystemTime::now()).or_else(|_| {
            std::fs::File::options().write(true).open(&path).and_then(|writable| writable.set_modified(SystemTime::now()))
        });
    }
    Some(value)
}

/// 事実を置く(一時 file から rename)。置いた byte を返す(置けなければ 0)。値に根の絶対 path が入っていれば置かない(見張り — `LeakGuard`)。
pub(super) fn write_object<T: Serialize>(objects: &Path, key: &ObjectKey, value: &T, format: Format, guard: &LeakGuard) -> u64 {
    let path = object_path(objects, key);
    let Some(dir) = path.parent() else { return 0 };
    if std::fs::create_dir_all(dir).is_err() {
        return 0;
    }
    let Some(bytes) = encode_value(value, format) else { return 0 };
    if guard.finds_in(&bytes) {
        // 1 つの実行で 1 度だけ知らせる(種類ごとに数千の事実が同じ誤りで並ぶ)。答えは作り直した値のまま正しい。
        if LEAKED.fetch_add(1, Ordering::Relaxed) == 0 {
            eprintln!("doeff-linter: 事実の cache: 根の絶対 path を持つ値は置き場に置かない(呼び手が path を持たない形で置き、bind で付ける約束 — facts_cache の頭の註)");
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

/// 置き場にある事実 1 つ(片づけが数え、古い順に `remove` する)。path は持たせるだけで、外へは出さない。
pub(super) struct StoredObject {
    /// 最後に使われた時刻(UNIX 秒 — file の更新時刻)。
    pub(super) used: u64,
    /// 上限へ数える大きさ(byte — OBJECT_MIN_BYTES を下限にする)。
    pub(super) bytes: u64,
    path: PathBuf,
}

/// 置き場にある事実を全部並べる(配置の深さに依らない — 置き場の根の直下の file〔使った印〕は事実ではない)。打ち切られた実行が残した古い
/// 一時 file はここで消す。
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

/// 事実を消す(消せたか)。消した事実は次に要る時に作り直される(答えは変わらない)。
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

    /// 置いた事実は全部並ぶ — 配置を替えても(段が深くなっても)片づけの数えから漏れない(設計検証の盲検 A: 1 行の配置の変更で、
    /// 検が全部通ったまま事実の上限が効かなくなった)。置き場の根の直下の使った印は事実に数えない。
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
        // 配置が変わって事実が深い所に在っても数える(配置の深さに依らない)。
        let deeper = objects.join("aa").join("bb").join("cc");
        std::fs::create_dir_all(&deeper).unwrap();
        std::fs::write(deeper.join("fact"), b"y").unwrap();
        assert_eq!(list(&dir.path().join("objects"), 0).len(), keys.len() + 1);
    }

    /// 一時 file の名は、置く側(temporary_path)と並べる側(is_temporary)が同じ決まりを共有する — 置く側の名を替えても片づけの見分けが
    /// 外れない。古い物だけ消え、新しい物(書いている最中かもしれない)は残る。
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
        assert!(list(&dir.path().join("objects"), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()).is_empty(), "一時 file を事実に数えた");
        assert!(!stale.exists(), "古い一時 file が残った");
        assert!(fresh.exists(), "新しい一時 file まで消した");
    }

    /// 根の絶対 path を持つ値は置かない(見張り)— path を持たない値は置く。
    #[test]
    fn a_value_holding_the_root_path_is_not_stored() {
        let dir = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(dir.path()).unwrap();
        let objects = dir.path().join("objects").join("kind.bin");
        let guard = LeakGuard::of(&root);
        let leaking = format!("{}/app/a.hy", root.display());
        let before = LEAKED.load(Ordering::Relaxed);
        assert_eq!(write_object(&objects, &object_key(&[b"leak"]), &leaking, Format::Json, &guard), 0, "根の path を持つ値を置いた");
        assert_eq!(write_object(&objects, &object_key(&[b"leak"]), &leaking, Format::Compact, &guard), 0, "binary の形でも置いた");
        assert!(LEAKED.load(Ordering::Relaxed) >= before + 2);
        assert!(list(&dir.path().join("objects"), 0).is_empty(), "置かなかった値が置き場に在る");
        assert!(write_object(&objects, &object_key(&[b"ok"]), &"app/a.hy".to_string(), Format::Json, &guard) > 0, "path を持たない値を置けない");
    }

    /// 見張りが根の綴りを短い物で探して、どの値にも当たる事は無い。
    #[test]
    fn a_too_short_root_makes_no_needle() {
        assert!(LeakGuard::of(Path::new("/")).needles.is_empty());
        assert!(LeakGuard::of(Path::new(".")).needles.iter().all(|needle| needle.len() >= 4));
    }
}

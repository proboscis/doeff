//! commit の hook の、HEAD の木の repo 全体の比べの結果の置き場(agora-redesign #2723)。
//!
//! 宣言の file を変えた commit は、規則の組を HEAD の木と先端の木の 2 つに当てて比べる(commit_hook)。HEAD の木の 1 回は、毎回新しい
//! 一時の dir に書き出した木を測るので事実の cache(facts_cache)が効かず、先端の 1 回より遅い(agora-controllers で 8.4 秒 対 4.9 秒・
//! 2026-10-02 zeus)。2 回の合計が子 1 回ごとの上限(20 秒)の際にあり、負荷が高いと上限を越えて「測れなかった」まま通っていた。
//! HEAD の木の結果は HEAD の commit・子の linter・規則の組・木の中の設定で決まるので、ここに置き、同じ鍵の次の hook(止められた
//! commit のやり直し・同じ origin/main から切った作業木の最初の commit)は先端の木だけを測る。上限の秒は上げない。
//!
//! 置き場 = facts_cache の根(`~/.cache/doeff-linter` ほか — `facts_cache::cache_base`)の直下の `commit-hook-head-<鍵の hash>/`。
//! 読み書きのたびに dir に印 `used` を付け(`facts_cache::mark_used`)、根の全体の上限の片づけ(agora-redesign #2725 — 既定 10 GiB・
//! 最後に使われた時刻の古い順・1 時間の内に使われた dir は残す)の対象に入れる。根の path の記録(root.path)は置かないので、根が
//! 消えた dir の片づけ(#1903)には掛からない。`DOEFF_LINTER_NO_CACHE` が在れば置き場を使わない。
//!
//! 中身 = `report.json`(鍵の全文・測った木の path・子の linter の editor-json の出力)。鍵の全文が合わない(hash の衝突)・読めない・
//! 壊れた file は無い物として測り直す(答えは変わらない)。書くのは一時の file からの rename(並走する hook どうしで壊さない)。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

/// 置き場の dir の名の頭(facts_cache の根の dir の 16 桁の hash と並ぶ)。
pub const DIR_PREFIX: &str = "commit-hook-head-";
/// dir の中の結果の file。
const REPORT_FILE: &str = "report.json";

/// HEAD の木の結果の鍵 — この 4 つが同じなら、HEAD の木を同じ規則で測った答えは同じ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HeadKey {
    /// HEAD の commit(40 桁)。
    pub head: String,
    /// 子の linter の版(`linter_identity` — binary の path・大きさ・更新時刻の ns)。
    pub linter: String,
    /// 当てた規則(enable の順)。
    pub rules: Vec<String>,
    /// 子に渡す設定 file の、HEAD の木の中の相対 path(設定 file が無ければ None)。
    pub config: Option<String>,
}

impl HeadKey {
    /// 置き場の dir の名(鍵の全文の FNV-1a — 実行と build をまたいで同じ答え)。
    pub fn dir_name(&self) -> String {
        let text = serde_json::to_string(self).unwrap_or_default();
        let hash = text.as_bytes().iter().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3));
        format!("{}{:016x}", DIR_PREFIX, hash)
    }
}

/// 置き場に置いた HEAD の木の結果。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StoredHead {
    /// 鍵の全文(読む時に照らし、合わなければ無い物とする)。
    pub key: HeadKey,
    /// 子を撃った木の path(読む側が違反の path を先端の根へ付け替える起点)。
    pub tree: String,
    /// 子の linter の editor-json の出力(手を加えない)。
    pub report: Value,
}

/// 子の linter の版 — binary の path(正規化)・大きさ・更新時刻の ns。組み直せば変わる。読めなければ None(置き場を使わない)。
pub fn linter_identity(linter: &Path) -> Option<String> {
    let path = linter.canonicalize().ok()?;
    let meta = std::fs::metadata(&path).ok()?;
    let mtime_ns = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some(format!("{}:{}:{}", path.display(), meta.len(), mtime_ns))
}

fn dir_of(base: &Path, key: &HeadKey) -> PathBuf {
    base.join(key.dir_name())
}

/// 置き場から鍵の結果を読む(無い・読めない・鍵の全文が合わなければ None)。読めたら dir に使った印を付ける。
pub fn load(base: &Path, key: &HeadKey) -> Option<StoredHead> {
    let dir = dir_of(base, key);
    let bytes = std::fs::read(dir.join(REPORT_FILE)).ok()?;
    let stored = serde_json::from_slice::<StoredHead>(&bytes).ok().filter(|stored| &stored.key == key)?;
    crate::project::facts_cache::mark_used(&dir);
    Some(stored)
}

/// 鍵の結果を置き場に置き、dir に使った印を付ける。書けなければ黙って諦める(次の hook が測り直すだけで、答えは変わらない)。
pub fn store(base: &Path, key: &HeadKey, tree: &Path, report: &Value) {
    let dir = dir_of(base, key);
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let stored = StoredHead { key: key.clone(), tree: tree.to_string_lossy().into_owned(), report: report.clone() };
    let Ok(text) = serde_json::to_vec(&stored) else { return };
    let tmp = dir.join(format!(".{}.{}.tmp", REPORT_FILE, std::process::id()));
    if std::fs::write(&tmp, text).is_err() || std::fs::rename(&tmp, dir.join(REPORT_FILE)).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return;
    }
    crate::project::facts_cache::mark_used(&dir);
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn key(rules: &[&str]) -> HeadKey {
        HeadKey {
            head: "a".repeat(40),
            linter: "/bin/doeff-linter:1:2".to_string(),
            rules: rules.iter().map(|r| r.to_string()).collect(),
            config: Some("pyproject.toml".to_string()),
        }
    }

    #[test]
    fn head_report_cache_round_trips_and_marks_the_dir_used() {
        let base = tempfile::TempDir::new().unwrap();
        let wanted = key(&["DOEFF163", "DOEFF167"]);
        assert_eq!(load(base.path(), &wanted), None);
        let report = json!({ "root": "/tmp/t/tree", "violations": [{ "key": "architecture.hy::DOEFF167::queue::Q1" }] });
        store(base.path(), &wanted, Path::new("/tmp/t/tree"), &report);
        let dir = base.path().join(wanted.dir_name());
        assert!(dir.join("used").is_file(), "使った印が無い");
        assert!(wanted.dir_name().starts_with(DIR_PREFIX));
        let got = load(base.path(), &wanted).unwrap();
        assert_eq!((got.tree.as_str(), &got.report), ("/tmp/t/tree", &report));
        // 規則の組が違えば別の鍵(別の dir)— 読めない。
        let other = key(&["DOEFF163"]);
        assert_ne!(other.dir_name(), wanted.dir_name());
        assert_eq!(load(base.path(), &other), None);
    }

    #[test]
    fn head_report_cache_ignores_a_record_whose_key_text_differs() {
        let base = tempfile::TempDir::new().unwrap();
        let wanted = key(&["DOEFF167"]);
        // 同じ dir の名に別の鍵の全文が置かれた(hash の衝突の代役)— 無い物として測り直させる。
        let mut stranger = wanted.clone();
        stranger.head = "b".repeat(40);
        let dir = base.path().join(wanted.dir_name());
        std::fs::create_dir_all(&dir).unwrap();
        let record = StoredHead { key: stranger, tree: "/t".to_string(), report: json!({ "violations": [] }) };
        std::fs::write(dir.join(REPORT_FILE), serde_json::to_vec(&record).unwrap()).unwrap();
        assert_eq!(load(base.path(), &wanted), None);
        std::fs::write(dir.join(REPORT_FILE), b"{ broken").unwrap();
        assert_eq!(load(base.path(), &wanted), None);
    }
}

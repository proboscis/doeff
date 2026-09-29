//! repo の Hy の file の一覧を、1 回の実行につき根ごとに 1 度だけ歩いて作る(agora-redesign #1418)。
//!
//! 規則の群(定義・臭い・素の呼び・索引・見出しと束縛・defk への書き換えの候補)がそれぞれ `collect_hy_files` で repo を歩いていた —
//! 1 file の commit の hook でも 1 回の実行で約 8 回、agora-controllers(約 1200 の dir)で 1 回 0.05 秒(zeus)。
//! 前提 = この crate を使うのは 1 回の実行で終わる binary だけで(常駐の使い手は無い)、実行の間に file は増えも減りもしない。
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use doeff_indexer::hy_index;

static WALKED: OnceLock<Mutex<HashMap<PathBuf, Arc<Vec<PathBuf>>>>> = OnceLock::new();

/// root 以下の Hy の file(`hy_index::collect_hy_files` と同じ集合と順)— 同じ root は 2 度目から歩かない。
pub fn collect(root: &Path) -> Vec<PathBuf> {
    let memo = WALKED.get_or_init(Default::default);
    if let Some(found) = memo.lock().expect("一覧の memo").get(root) {
        return found.as_ref().clone();
    }
    let found = Arc::new(hy_index::collect_hy_files(root));
    memo.lock().expect("一覧の memo").insert(root.to_path_buf(), Arc::clone(&found));
    found.as_ref().clone()
}

//! repo の Hy の file の一覧を、1 回の実行につき根ごとに 1 度だけ歩いて作る(agora-redesign #1418)。
//!
//! 規則の群(定義・臭い・素の呼び・索引・見出しと束縛・defk への書き換えの候補)がそれぞれ `collect_hy_files` で repo を歩いていた —
//! 1 file の commit の hook でも 1 回の実行で約 8 回、agora-controllers(約 1200 の dir)で 1 回 0.05 秒(zeus)。
//! 前提 = この crate を使うのは 1 回の実行で終わる binary だけで(常駐の使い手は無い)、実行の間に file は増えも減りもしない。
use std::collections::{BTreeSet, HashMap};
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

/// 命令の行で名指された path(file か dir)のうち、linter が歩く範囲(root の下の Hy の file — `collect`)の外の Hy の file を返す
/// (agora-redesign #2821)。層の規則は歩いた file だけを判じるので、範囲の外の file は名指しても何も判じられず、黙ると「測って通った」と
/// 読める — 呼び手はこの一覧を名指して緑と分ける。dir はその下の Hy の file に開く(歩き方は `collect` と同じ)。Hy でない file・在らない
/// path は返さない(Python の規則は名指しの path から file を集めて当てるので、範囲の外でも測る)。`declaration`(読んだ service と層の
/// 宣言の file — 例 package の根の外の architecture.hy)は宣言の規則が判じるので返さない。返す path は名指しの綴りの下の形。
pub fn outside_walk(root: &Path, named: &[PathBuf], declaration: Option<&Path>) -> Vec<PathBuf> {
    let walked: BTreeSet<PathBuf> =
        collect(root).iter().chain(declaration.map(Path::to_path_buf).iter()).filter_map(|path| path.canonicalize().ok()).collect();
    let outside: BTreeSet<PathBuf> = named
        .iter()
        .flat_map(|path| hy_index::collect_hy_files(path))
        .filter(|file| !file.canonicalize().is_ok_and(|real| walked.contains(&real)))
        .collect();
    outside.into_iter().collect()
}

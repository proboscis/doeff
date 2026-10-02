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

/// 歩く範囲の Hy の file と名乗り(根からの相対)— 根の下(`collect(root)`・名乗りは根からの相対)と、設定の `include` の dir の下
/// (名乗りは根から `..` を含む相対 = `paths::declared_rel` — 宣言の file の鍵と同じ形)。歩く範囲の 1 か所の定義で、定義の書き方の
/// 規則の母集団と「対象の外」の判定が読む(agora-redesign #2821 の案 A-1)。同じ file が両方に入れば根の側だけ。
pub fn walked(root: &Path, include: &[PathBuf]) -> Vec<(String, PathBuf)> {
    let under_root: Vec<(String, PathBuf)> =
        collect(root).into_iter().filter_map(|path| super::paths::relative_path(root, &path).map(|rel| (rel, path))).collect();
    let seen: BTreeSet<String> = under_root.iter().map(|(rel, _)| rel.clone()).collect();
    let extra: Vec<(String, PathBuf)> = include
        .iter()
        .flat_map(|dir| collect(dir))
        .map(|path| (super::paths::declared_rel(root, &path), path))
        .filter(|(rel, _)| !seen.contains(rel))
        .collect();
    under_root.into_iter().chain(extra).collect()
}

/// 1 file の名乗り(歩く範囲の中なら Some — 根の下は根からの相対、`include` の dir の下は `..` を含む相対)。範囲の外は None。
pub fn walked_rel(root: &Path, include: &[PathBuf], path: &Path) -> Option<String> {
    let real = |p: &Path| p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    super::paths::relative_path(root, path)
        .or_else(|| include.iter().any(|dir| real(path).starts_with(real(dir))).then(|| super::paths::declared_rel(root, path)))
}

/// 命令の行で名指された path(file か dir)のうち、linter が歩く範囲(`walked` — 根の下と設定の `include`)の外の Hy の file を返す
/// (agora-redesign #2821)。規則は歩いた file だけを判じるので、範囲の外の file は名指しても何も判じられず、黙ると「測って通った」と
/// 読める — 呼び手はこの一覧を名指して緑と分ける。dir はその下の Hy の file に開く(歩き方は `collect` と同じ)。Hy でない file・在らない
/// path は返さない(Python の規則は名指しの path から file を集めて当てるので、範囲の外でも測る)。`declaration`(読んだ service と層の
/// 宣言の file — 例 package の根の外の architecture.hy)は宣言の規則が判じるので返さない。返す path は名指しの path の下の形(呼び手は
/// 正規化した絶対の path を渡す — 違反の path と同じ形)。
pub fn outside_walk(root: &Path, include: &[PathBuf], named: &[PathBuf], declaration: Option<&Path>) -> Vec<PathBuf> {
    let walked: BTreeSet<PathBuf> = walked(root, include)
        .into_iter()
        .map(|(_, path)| path)
        .chain(declaration.map(Path::to_path_buf))
        .filter_map(|path| path.canonicalize().ok())
        .collect();
    let outside: BTreeSet<PathBuf> = named
        .iter()
        .flat_map(|path| hy_index::collect_hy_files(path))
        .filter(|file| !file.canonicalize().is_ok_and(|real| walked.contains(&real)))
        .collect();
    outside.into_iter().collect()
}

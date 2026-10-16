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
static BY_MODULE: OnceLock<Mutex<HashMap<PathBuf, Arc<ModuleFiles>>>> = OnceLock::new();

/// module の綴り(`a.b.c`)→ その module の Hy の file(根からの path と絶対 path)。同じ module に当たる file(`a/b.hy` と `a/b.hyk`)は全部。
pub use super::module_probe::ModuleProbe as ModuleFiles;

/// root 以下の Hy の file(`hy_index::collect_hy_files` と同じ集合と順)— 同じ root は 2 度目から歩かない。
pub fn collect(root: &Path) -> Vec<PathBuf> {
    let memo = WALKED.get_or_init(Default::default);
    if let Some(found) = memo.lock().expect("一覧の memo").get(root) {
        return found.as_ref().clone();
    }
    let found = Arc::new(walk_overlapped(root));
    memo.lock().expect("一覧の memo").insert(root.to_path_buf(), Arc::clone(&found));
    found.as_ref().clone()
}

/// `hy_index::collect_hy_files` と同じ集合と順の一覧を、dir を 1 層ずつ並べて開いて作る。
///
/// 順に歩くと dir ごとに disk を 1 回待つ(agora-controllers で約 1200 の dir — 混んだ Pod の冷えた作業木で 9〜15 秒・CPU は 0.1 秒)。
/// 1 つの dir の中身は他の dir の中身に依らないので、同じ層の dir は待ちを重ねて開く(`bulk_read::fan_out_wide`)。降りない dir の名・symlink を辿らない事・
/// 読めない dir を黙って飛ばす事・最後に path で並べる事は、歩く物と同じ。根が dir として開けない時(file・無い path)は歩く物に任せる。
fn walk_overlapped(root: &Path) -> Vec<PathBuf> {
    let Some(first) = list_dir(root) else { return hy_index::collect_hy_files(root) };
    let mut files = first.files;
    let mut level = first.dirs;
    while !level.is_empty() {
        let listed = super::bulk_read::fan_out_wide(&level, |dir| list_dir(dir));
        level = Vec::new();
        for listing in listed.into_iter().flatten() {
            level.extend(listing.dirs);
            files.extend(listing.files);
        }
    }
    files.sort();
    files
}

/// dir 1 つの中身 — 降りる dir と Hy の file。
struct Listing {
    dirs: Vec<PathBuf>,
    files: Vec<PathBuf>,
}

/// dir を開いて中身を分ける。開けなければ None。
fn list_dir(dir: &Path) -> Option<Listing> {
    let mut listing = Listing { dirs: Vec::new(), files: Vec::new() };
    for entry in std::fs::read_dir(dir).ok()?.filter_map(Result::ok) {
        let Ok(kind) = entry.file_type() else { continue };
        let name = entry.file_name();
        if kind.is_dir() {
            if !hy_index::SKIPPED_DIRS.contains(&name.to_string_lossy().as_ref()) {
                listing.dirs.push(entry.path());
            }
        } else if kind.is_file() && Path::new(&name).extension().and_then(|extension| extension.to_str()).is_some_and(|extension| hy_index::HY_EXTENSIONS.contains(&extension)) {
            listing.files.push(entry.path());
        }
    }
    Some(listing)
}

/// root 以下の Hy の file を module の綴りで引く索引(`names::module_of` の逆)。tree を歩かず、引かれた module の path だけを探る(`ModuleProbe`)—
/// 探す範囲は `collect(root)` と同じ(根の下の通常の Hy の file・`collect_hy_files` が降りない dir の下は探さない)。1 file の実行が「書いた file の名指す
/// module の file だけ」を引くための索引で、同じ root は同じ索引を使う(探した module は覚える)。
pub fn by_module(root: &Path) -> Arc<ModuleFiles> {
    let memo = BY_MODULE.get_or_init(Default::default);
    let mut memo = memo.lock().expect("module の索引の memo");
    Arc::clone(
        memo.entry(root.to_path_buf())
            .or_insert_with(|| Arc::new(ModuleFiles::new(root, hy_index::HY_EXTENSIONS, hy_index::SKIPPED_DIRS))),
    )
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

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(files: &[&str]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for rel in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        dir
    }

    /// 並べて開いて作る一覧は、歩く物(`hy_index::collect_hy_files`)と同じ集合・同じ順 — 降りない dir・symlink・Hy でない file・dir の名が `.hy` で終わる物も。
    #[test]
    fn the_overlapped_walk_lists_what_the_plain_walk_lists_in_the_same_order() {
        let dir = tree(&[
            "top.hy",
            "z_last.hyk",
            "a/b/c/deep.hyp",
            "a/b/other.hy",
            "a/notes.txt",
            "app/v1.2/x.hy",
            "app/.venv/lib/y.hy",
            "app/node_modules/n.hy",
            "app/target/z.hy",
            ".git/hooks/h.hy",
            "pkg/__pycache__/c.hy",
            "pkg/real.hy",
            "many/d00/f.hy",
            "many/d01/f.hy",
            "many/d02/f.hy",
            "many/d03/f.hy",
            "many/d04/f.hy",
            "many/d05/f.hy",
            "target",
        ]);
        std::fs::create_dir_all(dir.path().join("folder.hy/inside")).unwrap();
        std::fs::write(dir.path().join("folder.hy/inside/in.hy"), "").unwrap();
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(dir.path().join("a"), dir.path().join("linked")).unwrap();
            std::os::unix::fs::symlink(dir.path().join("top.hy"), dir.path().join("alias.hy")).unwrap();
        }
        let walked = hy_index::collect_hy_files(dir.path());
        assert!(walked.len() >= 10, "検の材料が足りない: {walked:?}");
        assert_eq!(walk_overlapped(dir.path()), walked);
    }

    /// 根が降りない名でも、根自身は降りる(歩く物と同じ)。開けない dir は黙って飛ばす。
    #[test]
    fn the_root_is_entered_even_when_its_name_is_skipped_and_unreadable_dirs_are_left_out() {
        let parent = tree(&["target/inner/x.hy", "target/y.hy"]);
        let root = parent.path().join("target");
        assert_eq!(walk_overlapped(&root), hy_index::collect_hy_files(&root));
        assert_eq!(walk_overlapped(&root).len(), 2);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let locked = root.join("inner");
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
            assert_eq!(walk_overlapped(&root), hy_index::collect_hy_files(&root));
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
    }

    /// 根が dir でない(file・無い path)なら、歩く物の答えのまま。
    #[test]
    fn a_root_that_is_not_a_directory_gives_what_the_plain_walk_gives() {
        let dir = tree(&["one.hy", "note.txt"]);
        for root in [dir.path().join("one.hy"), dir.path().join("note.txt"), dir.path().join("missing")] {
            assert_eq!(walk_overlapped(&root), hy_index::collect_hy_files(&root), "{root:?}");
        }
    }
}

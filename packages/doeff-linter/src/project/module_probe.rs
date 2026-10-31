//! module の綴りから file を、tree を歩かずに探る(1 file の実行)。
//!
//! 1 file の実行が引く module は、書いた file が名指す数十個だけ。その file を探すのに repo の tree を全部歩くと(agora-controllers で約 1200 の dir)、
//! 冷えた作業木では dir ごとに 1 回ずつ disk を読む(混んだ Pod で `iowait` が 12〜25% の時、1 回の歩きで 5〜11 秒かかった)。module の綴り `a.b.c` は
//! 根からの path `a/b/c.<拡張子>` で、path の各段は dir か file の 1 つだけなので、根から段ごとに探れば足りる(1 module 数回の stat)。
//!
//! 探す範囲は、歩く物(`hy_index::collect_hy_files`・`walk_files`)と同じ — 根の下の通常の file(symlink は file でも dir でも辿らない)で、
//! 歩く物が降りない dir の名(`skipped_dirs`)の下は探さない。module の綴りは `names::module_of`(最後の拡張子を外して `/` を `.` にする)の逆で、
//! 段の名に `.` が入る path(`v1.2/x.hy` → `v1.2.x`)も、`.` で切る位置を全部試して見つける。

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// module の綴り → その module の file(根からの path と絶対 path — path の順)。同じ綴りに当たる file(`a/b.hy` と `a/b.hyk`)は全部。
pub struct ModuleProbe {
    root: PathBuf,
    extensions: &'static [&'static str],
    skipped_dirs: &'static [&'static str],
    memo: Mutex<HashMap<String, Arc<Vec<(String, PathBuf)>>>>,
}

impl ModuleProbe {
    pub fn new(root: &Path, extensions: &'static [&'static str], skipped_dirs: &'static [&'static str]) -> ModuleProbe {
        ModuleProbe { root: root.to_path_buf(), extensions, skipped_dirs, memo: Mutex::new(HashMap::new()) }
    }

    /// module の file(同じ module は 2 度探さない)。無ければ空。
    pub fn files(&self, module: &str) -> Arc<Vec<(String, PathBuf)>> {
        if let Some(found) = self.memo.lock().expect("module の探りの memo").get(module) {
            return Arc::clone(found);
        }
        let found = Arc::new(self.probe(module));
        self.memo.lock().expect("module の探りの memo").insert(module.to_string(), Arc::clone(&found));
        found
    }

    /// module に当たる file が在るか。
    pub fn contains(&self, module: &str) -> bool {
        !self.files(module).is_empty()
    }

    fn probe(&self, module: &str) -> Vec<(String, PathBuf)> {
        let parts: Vec<&str> = module.split('.').collect();
        // 綴りは file の中の記号なので、path を外へ逃がす形(空の段・区切り・NUL)は探さない。
        if parts.iter().any(|part| part.is_empty() || part.contains(['/', '\\', '\0'])) {
            return Vec::new();
        }
        let mut found = Vec::new();
        self.descend(&self.root, "", &parts, &mut found);
        found.sort();
        found.dedup();
        found
    }

    /// `dir`(根からの綴り `rel`)の下で、残りの段 `parts` に当たる file を探す。`.` で切る位置は全部試す(段の名に `.` が入る path のため)。
    fn descend(&self, dir: &Path, rel: &str, parts: &[&str], out: &mut Vec<(String, PathBuf)>) {
        for take in 1..=parts.len() {
            let name = parts[..take].join(".");
            if take == parts.len() {
                for extension in self.extensions {
                    let file = format!("{name}.{extension}");
                    let path = dir.join(&file);
                    if std::fs::symlink_metadata(&path).is_ok_and(|meta| meta.is_file()) {
                        out.push((format!("{rel}{file}"), path));
                    }
                }
            } else if !self.skipped_dirs.contains(&name.as_str()) {
                let path = dir.join(&name);
                if std::fs::symlink_metadata(&path).is_ok_and(|meta| meta.is_dir()) {
                    self.descend(&path, &format!("{rel}{name}/"), &parts[take..], out);
                }
            }
        }
    }
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

    fn rels(probe: &ModuleProbe, module: &str) -> Vec<String> {
        probe.files(module).iter().map(|(rel, _)| rel.clone()).collect()
    }

    /// 歩く物(`hy_index::collect_hy_files` が module_of した綴りの表)と同じ答え。
    #[test]
    fn it_finds_what_the_walk_finds() {
        let dir = tree(&["app/core/a.hy", "app/core/a.hyk", "app/core/b.py", "app/v1.2/x.hy", "app/.venv/lib/y.hy", "app/target/z.hy", "top.hy", "app/core/c.txt"]);
        let probe = ModuleProbe::new(dir.path(), &["hy", "hyk", "hyp"], &[".venv", "node_modules", "target", ".git", "__pycache__"]);
        let walked = doeff_indexer::hy_index::collect_hy_files(dir.path());
        let mut expected: HashMap<String, Vec<String>> = HashMap::new();
        for path in walked {
            let rel = path.strip_prefix(dir.path()).unwrap().to_string_lossy().into_owned();
            expected.entry(super::super::names::module_of(&rel)).or_default().push(rel);
        }
        for (module, mut want) in expected {
            want.sort();
            assert_eq!(rels(&probe, &module), want, "{module}");
        }
        // 歩かない所と、Hy でない file と、在らない module は探しても無い。
        for module in ["app.target.z", "app.venv.lib.y", "app.core.b", "app.core.c", "app.core.nothing", "app.core"] {
            assert!(!probe.contains(module), "{module}");
        }
        // 段の名に `.` が入る path・同じ綴りに当たる複数の file。
        assert_eq!(rels(&probe, "app.v1.2.x"), vec!["app/v1.2/x.hy"]);
        assert_eq!(rels(&probe, "app.core.a"), vec!["app/core/a.hy", "app/core/a.hyk"]);
    }

    #[test]
    fn a_spelling_that_escapes_the_root_finds_nothing() {
        let dir = tree(&["a.hy"]);
        let probe = ModuleProbe::new(dir.path(), &["hy"], &[]);
        for module in ["", "..", "../a", "a/b", "a..b", ".a", "a."] {
            assert!(!probe.contains(module), "{module:?}");
        }
        assert!(probe.contains("a"));
    }

    #[cfg(unix)]
    #[test]
    fn a_symlinked_file_or_directory_is_not_followed() {
        let dir = tree(&["real/m.hy", "other.hy"]);
        std::os::unix::fs::symlink(dir.path().join("real"), dir.path().join("linked")).unwrap();
        std::os::unix::fs::symlink(dir.path().join("other.hy"), dir.path().join("alias.hy")).unwrap();
        let probe = ModuleProbe::new(dir.path(), &["hy"], &[]);
        assert!(!probe.contains("linked.m") && !probe.contains("alias") && probe.contains("real.m") && probe.contains("other"));
    }
}

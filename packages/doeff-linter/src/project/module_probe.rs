//! Find a file from a module spelling without walking the tree (single-file run).
//!
//! A single-file run looks up only a few dozen modules named by the written file. Walking the whole repo tree to find those files (about 1200 dirs in agora-controllers)
//! reads the disk once per dir on a cold worktree (with `iowait` at 12-25% on a busy Pod, one walk took 5 to 11 seconds). A module spelling `a.b.c` is
//! the path `a/b/c.<extension>` from the root, and each step of the path is exactly one dir or file, so probing step by step from the root is enough (a few stats per module).
//!
//! The probed range is the same as that of the walkers (`hy_index::collect_hy_files`, `walk_files`) — regular files under the root (symlinks are followed neither as files nor as dirs),
//! and nothing is probed under dir names the walkers do not descend into (`skipped_dirs`). A module spelling is the inverse of `names::module_of` (strip the last extension and turn `/` into `.`),
//! and a path whose step name contains `.` (`v1.2/x.hy` -> `v1.2.x`) is also found by trying every position at which to cut on `.`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Module spelling -> the module's files (path from the root and absolute path — in path order). All files matching the same spelling (`a/b.hy` and `a/b.hyk`).
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

    /// The module's files (the same module is never probed twice). Empty if none.
    pub fn files(&self, module: &str) -> Arc<Vec<(String, PathBuf)>> {
        if let Some(found) = self.memo.lock().expect("module probe memo").get(module) {
            return Arc::clone(found);
        }
        let found = Arc::new(self.probe(module));
        self.memo.lock().expect("module probe memo").insert(module.to_string(), Arc::clone(&found));
        found
    }

    /// Whether a file matching the module exists.
    pub fn contains(&self, module: &str) -> bool {
        !self.files(module).is_empty()
    }

    fn probe(&self, module: &str) -> Vec<(String, PathBuf)> {
        let parts: Vec<&str> = module.split('.').collect();
        // The spelling is a symbol in a file, so shapes that would escape the path (empty step, separator, NUL) are not probed.
        if parts.iter().any(|part| part.is_empty() || part.contains(['/', '\\', '\0'])) {
            return Vec::new();
        }
        let mut found = Vec::new();
        self.descend(&self.root, "", &parts, &mut found);
        found.sort();
        found.dedup();
        found
    }

    /// Under `dir` (spelling from the root `rel`), find files matching the remaining steps `parts`. Every position to cut on `.` is tried (for paths whose step name contains `.`).
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

    /// The same answer as the walkers (the table of spellings that `hy_index::collect_hy_files` passed through module_of).
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
        // Places not walked, non-Hy files, and nonexistent modules are not found even if probed.
        for module in ["app.target.z", "app.venv.lib.y", "app.core.b", "app.core.c", "app.core.nothing", "app.core"] {
            assert!(!probe.contains(module), "{module}");
        }
        // Step names containing `.`, and multiple files matching the same spelling.
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

//! 既知の破れの登録簿を読む — 1 鍵 1 file の dir(中の `*.txt` の 1 行目が鍵)と、1 行 1 鍵の file。
//! 読めない dir・file は止めずに理由を返す(登録簿が読めないことを緑にしないため、呼び手は errors に出す)。

use std::collections::BTreeSet;
use std::path::Path;

/// 読んだ登録簿(鍵の集合)と、読めなかった理由。
#[derive(Debug, Default, Clone)]
pub struct Registry {
    pub keys: BTreeSet<String>,
    pub problems: Vec<String>,
}

impl Registry {
    /// root からの相対の dir と file の登録簿を全部読む。
    pub fn load(root: &Path, dirs: &[String], files: &[String]) -> Registry {
        let mut registry = Registry::default();
        for dir in dirs {
            registry.read_dir(&root.join(dir), dir);
        }
        for file in files {
            registry.read_list(&root.join(file), file);
        }
        registry
    }

    /// 1 鍵 1 file の dir を読む(`*.txt` の 1 行目が鍵)。
    fn read_dir(&mut self, path: &Path, shown: &str) {
        let entries = match std::fs::read_dir(path) {
            Ok(entries) => entries,
            Err(error) => {
                self.problems.push(format!("登録簿の dir {} を読めない: {}", shown, error));
                return;
            }
        };
        let mut files: Vec<_> = entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|p| p.extension().is_some_and(|ext| ext == "txt"))
            .collect();
        files.sort();
        for file in files {
            match std::fs::read_to_string(&file) {
                Ok(text) => match text.lines().next().map(str::trim).filter(|line| !line.is_empty()) {
                    Some(key) => {
                        self.keys.insert(key.to_string());
                    }
                    None => self.problems.push(format!("登録簿の file {} の 1 行目が空", file.display())),
                },
                Err(error) => self.problems.push(format!("登録簿の file {} を読めない: {}", file.display(), error)),
            }
        }
    }

    /// 1 行 1 鍵の file を読む(空行と `#` で始まる行は飛ばす)。
    fn read_list(&mut self, path: &Path, shown: &str) {
        match std::fs::read_to_string(path) {
            Ok(text) => {
                for line in text.lines().map(str::trim).filter(|line| !line.is_empty() && !line.starts_with('#')) {
                    self.keys.insert(line.to_string());
                }
            }
            Err(error) => self.problems.push(format!("登録簿の file {} を読めない: {}", shown, error)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_dir_and_list_and_reports_missing() {
        let dir = tempfile::TempDir::new().unwrap();
        let table = dir.path().join("BREACHES");
        std::fs::create_dir(&table).unwrap();
        std::fs::write(table.join("aaa.txt"), "a.hy::rule::x\n理由\n").unwrap();
        std::fs::write(table.join(".keep"), "").unwrap();
        std::fs::write(dir.path().join("keys.txt"), "# 註\n\nb.hy::rule\n").unwrap();
        let registry = Registry::load(dir.path(), &["BREACHES".to_string(), "MISSING".to_string()], &["keys.txt".to_string()]);
        assert!(registry.keys.contains("a.hy::rule::x"));
        assert!(registry.keys.contains("b.hy::rule"));
        assert_eq!(registry.keys.len(), 2);
        assert_eq!(registry.problems.len(), 1);
        assert!(registry.problems[0].contains("MISSING"));
    }
}

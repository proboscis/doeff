//! 既知の破れの登録簿を読む — 1 鍵 1 file の dir(中の `*.txt` の 1 行目が鍵)と、1 行 1 鍵の file。
//! 読めない dir・file は止めずに理由を返す(登録簿が読めないことを緑にしないため、呼び手は errors に出す)。

use std::collections::{BTreeMap, BTreeSet};
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
        let files = match txt_files(path) {
            Ok(files) => files,
            Err(error) => {
                self.problems.push(format!("登録簿の dir {} を読めない: {}", shown, error));
                return;
            }
        };
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

/// 人が判定した鍵の一覧(1 鍵 1 file の dir・`*.txt` の 1 行目が鍵・2 行目から後が判定の理由)— 意味の規則の誤判定の一覧と正例の一覧。
/// 理由の無い file は判定として読まない(理由の書けない判定を黙って効かせない)。
#[derive(Debug, Default, Clone)]
pub struct JudgedKeys {
    /// 鍵 → 判定の理由。
    pub reasons: BTreeMap<String, String>,
    pub problems: Vec<String>,
}

impl JudgedKeys {
    /// root からの相対の dir を全部読む。
    pub fn load(root: &Path, dirs: &[String]) -> JudgedKeys {
        let mut judged = JudgedKeys::default();
        for dir in dirs {
            let files = match txt_files(&root.join(dir)) {
                Ok(files) => files,
                Err(error) => {
                    judged.problems.push(format!("判定の一覧の dir {} を読めない: {}", dir, error));
                    continue;
                }
            };
            for file in files {
                let text = match std::fs::read_to_string(&file) {
                    Ok(text) => text,
                    Err(error) => {
                        judged.problems.push(format!("判定の一覧の file {} を読めない: {}", file.display(), error));
                        continue;
                    }
                };
                let mut lines = text.lines();
                let key = lines.next().map(str::trim).unwrap_or("");
                let reason = lines.map(str::trim).filter(|line| !line.is_empty()).collect::<Vec<_>>().join(" ");
                match (key.is_empty(), reason.is_empty()) {
                    (true, _) => judged.problems.push(format!("判定の一覧の file {} の 1 行目(鍵)が空", file.display())),
                    (false, true) => judged.problems.push(format!("判定の一覧の file {} に判定の理由(2 行目から後)が無い — 読まない", file.display())),
                    (false, false) => {
                        judged.reasons.insert(key.to_string(), reason);
                    }
                }
            }
        }
        judged
    }
}

/// dir の中の `*.txt` を名の順に並べる。
fn txt_files(path: &Path) -> std::io::Result<Vec<std::path::PathBuf>> {
    let mut files: Vec<_> = std::fs::read_dir(path)?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|p| p.extension().is_some_and(|ext| ext == "txt"))
        .collect();
    files.sort();
    Ok(files)
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

    #[test]
    fn judged_keys_need_a_reason() {
        let dir = tempfile::TempDir::new().unwrap();
        let list = dir.path().join("FALSE");
        std::fs::create_dir(&list).unwrap();
        std::fs::write(list.join("a.txt"), "a.hy::DOEFF201::f\n行の形を読むだけ\n業務の判断ではない\n").unwrap();
        std::fs::write(list.join("b.txt"), "b.hy::DOEFF201::g\n\n").unwrap();
        let judged = JudgedKeys::load(dir.path(), &["FALSE".to_string(), "MISSING".to_string()]);
        assert_eq!(judged.reasons.get("a.hy::DOEFF201::f").map(String::as_str), Some("行の形を読むだけ 業務の判断ではない"));
        assert_eq!(judged.reasons.len(), 1, "理由の無い判定は読まない");
        assert_eq!(judged.problems.len(), 2);
        assert!(judged.problems.iter().any(|p| p.contains("理由")));
        assert!(judged.problems.iter().any(|p| p.contains("MISSING")));
    }
}

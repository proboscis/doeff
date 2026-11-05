//! Reads the registries of known breaches -- a dir of one key per file (the first line of each `*.txt` inside is the key), and a file of one key per line.
//! A dir or file that cannot be read is reported with its reason without stopping (so that an unreadable registry is not made green, the caller puts it in errors). A missing dir is an empty registry
//! (a notice in notes -- because deleting the last line makes git delete the dir itself).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::{bulk_read, dir_stamp};

/// The registry that was read (a set of keys) and the reasons for what could not be read.
#[derive(Debug, Default, Clone)]
pub struct Registry {
    pub keys: BTreeSet<String>,
    /// Key -> the registry file it appears in (spelled from the root -- the notice for an unmatched line in DOEFF166 names the file to delete).
    pub origins: BTreeMap<String, String>,
    pub problems: Vec<String>,
    /// Notices that are not read errors (a missing dir was read as an empty registry).
    pub notes: Vec<String>,
}

impl Registry {
    /// Reads all the registries of dirs and files relative to root.
    pub fn load(root: &Path, dirs: &[String], files: &[String]) -> Registry {
        let mut registry = Registry::default();
        for dir in dirs {
            registry.read_dir(root, &root.join(dir), dir);
        }
        for file in files {
            registry.read_list(&root.join(file), file);
        }
        registry
    }

    /// Reads a dir of one key per file (the first line of each `*.txt` is the key).
    ///
    /// A missing dir is read as an empty registry (one notice line). The registry is a table that only shrinks, and when the last line is deleted git does not keep
    /// an empty dir, so the dir disappears with it -- that is a correct operation, so it is not treated as unreadable. The registry is a table that only loosens known findings, so
    /// even reading a missing dir (including a misspelled one) as empty can only tip the verdict toward the stricter side (findings that should have been listed show up as new findings).
    fn read_dir(&mut self, root: &Path, path: &Path, shown: &str) {
        if !path.exists() {
            self.notes.push(format!("registry dir {} does not exist -- read as an empty registry", shown));
            return;
        }
        let files = match txt_files(path) {
            Ok(files) => files,
            Err(error) => {
                self.problems.push(format!("cannot read registry dir {}: {}", shown, error));
                return;
            }
        };
        // A dir of one key per file runs to hundreds of files -- if (name, size, mtime) match the previous run, the previous read is used without reading the contents (`dir_stamp`);
        // when reading, waits are overlapped so a cold working tree does not wait on disk file by file.
        let read = dir_stamp::remembered(root, "registry-dir", &files, || read_key_files(&files));
        let shown = shown.trim_end_matches('/');
        for (key, name) in read.keys {
            self.origins.insert(key.clone(), format!("{}/{}", shown, name));
            self.keys.insert(key);
        }
        self.problems.extend(read.problems);
    }

    /// Reads a file of one key per line (blank lines and lines starting with `#` are skipped).
    fn read_list(&mut self, path: &Path, shown: &str) {
        match std::fs::read_to_string(path) {
            Ok(text) => {
                for line in text.lines().map(str::trim).filter(|line| !line.is_empty() && !line.starts_with('#')) {
                    self.keys.insert(line.to_string());
                    self.origins.insert(line.to_string(), shown.to_string());
                }
            }
            Err(error) => self.problems.push(format!("cannot read registry file {}: {}", shown, error)),
        }
    }
}

/// A list of keys judged by a person (a dir of one key per file; the first line of each `*.txt` is the key, and line 2 onward is the reason for the judgment) -- the list of misjudgments and the list of positive examples of the semantic rules.
/// A file without a reason is not read as a judgment (a judgment that cannot state its reason is not silently allowed to take effect).
#[derive(Debug, Default, Clone)]
pub struct JudgedKeys {
    /// Key -> reason for the judgment.
    pub reasons: BTreeMap<String, String>,
    pub problems: Vec<String>,
    /// Notices that are not read errors (a missing dir for an `Absent::Empty` list was read as empty).
    pub notes: Vec<String>,
}

/// How to read when the list's dir is missing (the caller chooses by the nature of the list).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Absent {
    /// Unreadable (put in errors) -- the declaration lists (effects of the outside world, counterexamples) and the list of Jev's judgments. If a missing dir were read as empty, a misspelling would
    /// silently make the declarations disappear.
    Unreadable,
    /// An empty list (one line in notes) -- a table that only shrinks and pins down violations (`:unserved`). When the last line is deleted git does not keep an empty dir, so the dir
    /// disappears with it -- that is a correct operation (same as Registry::read_dir of the registry). Even reading it as empty, the verdict
    /// can only tip toward the stricter side (shortfalls that should have been listed show up as findings).
    Empty,
}

impl JudgedKeys {
    /// Reads all dirs relative to root. A missing dir is read the way `absent` says.
    pub fn load(root: &Path, dirs: &[String], absent: Absent) -> JudgedKeys {
        let mut judged = JudgedKeys::default();
        for dir in dirs {
            if absent == Absent::Empty && !root.join(dir).exists() {
                judged.notes.push(format!("judgment list dir {} does not exist -- read as an empty list", dir));
                continue;
            }
            let files = match txt_files(&root.join(dir)) {
                Ok(files) => files,
                Err(error) => {
                    judged.problems.push(format!("cannot read judgment list dir {}: {}", dir, error));
                    continue;
                }
            };
            let read = dir_stamp::remembered(root, "judged-dir", &files, || read_judged_files(&files));
            for (key, reason) in read.reasons {
                judged.reasons.insert(key, reason);
            }
            judged.problems.extend(read.problems);
        }
        judged
    }
}

/// The result of reading a one-key-per-file registry dir -- (key, file name) in the order of the `*.txt` files, and the reasons for what could not be read. Something remembered by the dir's fingerprint (`dir_stamp`).
#[derive(Serialize, Deserialize)]
struct KeyFiles {
    keys: Vec<(String, String)>,
    problems: Vec<String>,
}

/// Actually reads the contents of the registry files (overlapping the read waits). Returns the result and "whether it may be remembered" -- the reasons for unreadable files contain the file's absolute path, so a read with reasons is not remembered
/// (the store refuses values that contain the root's absolute path).
fn read_key_files(files: &[PathBuf]) -> (KeyFiles, bool) {
    let texts = crate::timing::timed("registry.read", || bulk_read::fan_out(files, |file| std::fs::read_to_string(file)));
    let mut read = KeyFiles { keys: Vec::new(), problems: Vec::new() };
    for (file, text) in files.iter().zip(texts) {
        match text {
            Ok(text) => match text.lines().next().map(str::trim).filter(|line| !line.is_empty()) {
                Some(key) => {
                    let name = file.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                    read.keys.push((key.to_string(), name));
                }
                None => read.problems.push(format!("line 1 of registry file {} is empty", file.display())),
            },
            Err(error) => read.problems.push(format!("cannot read registry file {}: {}", file.display(), error)),
        }
    }
    let keep = read.problems.is_empty();
    (read, keep)
}

/// The result of reading a dir of person-judged keys -- (key, reason) in the order of the `*.txt` files, and the reasons for what could not be read. Like `KeyFiles`, something remembered by the dir's fingerprint.
#[derive(Serialize, Deserialize)]
struct JudgedFiles {
    reasons: Vec<(String, String)>,
    problems: Vec<String>,
}

/// Actually reads the contents of the judgment list files. Returns the result and "whether it may be remembered" (same as `read_key_files` -- a read with unreadable reasons is not remembered).
fn read_judged_files(files: &[PathBuf]) -> (JudgedFiles, bool) {
    let texts = crate::timing::timed("judged.read", || bulk_read::fan_out(files, |file| std::fs::read_to_string(file)));
    let mut read = JudgedFiles { reasons: Vec::new(), problems: Vec::new() };
    for (file, text) in files.iter().zip(texts) {
        let text = match text {
            Ok(text) => text,
            Err(error) => {
                read.problems.push(format!("cannot read judgment list file {}: {}", file.display(), error));
                continue;
            }
        };
        let mut lines = text.lines();
        let key = lines.next().map(str::trim).unwrap_or("");
        let reason = lines.map(str::trim).filter(|line| !line.is_empty()).collect::<Vec<_>>().join(" ");
        match (key.is_empty(), reason.is_empty()) {
            (true, _) => read.problems.push(format!("line 1 (the key) of judgment list file {} is empty", file.display())),
            (false, true) => read.problems.push(format!("judgment list file {} has no reason for the judgment (line 2 onward) -- not read", file.display())),
            (false, false) => read.reasons.push((key.to_string(), reason)),
        }
    }
    let keep = read.problems.is_empty();
    (read, keep)
}

/// Lists the `*.txt` files in a dir in name order.
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
    fn reads_dir_and_list_and_reads_a_missing_dir_as_empty() {
        let dir = tempfile::TempDir::new().unwrap();
        let table = dir.path().join("BREACHES");
        std::fs::create_dir(&table).unwrap();
        std::fs::write(table.join("aaa.txt"), "a.hy::rule::x\nreason\n").unwrap();
        std::fs::write(table.join(".keep"), "").unwrap();
        std::fs::write(dir.path().join("keys.txt"), "# comment\n\nb.hy::rule\n").unwrap();
        let registry = Registry::load(dir.path(), &["BREACHES".to_string(), "MISSING".to_string()], &["keys.txt".to_string()]);
        assert!(registry.keys.contains("a.hy::rule::x"));
        assert!(registry.keys.contains("b.hy::rule"));
        assert_eq!(registry.keys.len(), 2);
        // A registry whose last line was deleted so git removed the dir itself is empty, not unreadable (one notice line).
        assert!(registry.problems.is_empty(), "{:?}", registry.problems);
        assert_eq!(registry.notes.len(), 1);
        assert!(registry.notes[0].contains("MISSING"));
    }

    #[test]
    fn a_registry_path_that_is_not_a_readable_dir_is_still_a_problem() {
        // Counterexample: a registry that exists but cannot be read as a dir (a file is placed there) is reported as unreadable, not read as empty.
        let dir = tempfile::TempDir::new().unwrap();
        std::fs::write(dir.path().join("BREACHES"), "a.hy::rule::x\n").unwrap();
        let registry = Registry::load(dir.path(), &["BREACHES".to_string()], &[]);
        assert!(registry.keys.is_empty());
        assert_eq!(registry.problems.len(), 1, "{:?}", registry.problems);
        assert!(registry.notes.is_empty());
    }

    #[test]
    fn judged_keys_need_a_reason() {
        let dir = tempfile::TempDir::new().unwrap();
        let list = dir.path().join("FALSE");
        std::fs::create_dir(&list).unwrap();
        std::fs::write(list.join("a.txt"), "a.hy::DOEFF201::f\nonly reads the shape of the line\nnot a business judgment\n").unwrap();
        std::fs::write(list.join("b.txt"), "b.hy::DOEFF201::g\n\n").unwrap();
        let judged = JudgedKeys::load(dir.path(), &["FALSE".to_string(), "MISSING".to_string()], Absent::Unreadable);
        assert_eq!(judged.reasons.get("a.hy::DOEFF201::f").map(String::as_str), Some("only reads the shape of the line not a business judgment"));
        assert_eq!(judged.reasons.len(), 1, "a judgment without a reason is not read");
        assert_eq!(judged.problems.len(), 2);
        assert!(judged.problems.iter().any(|p| p.contains("reason")));
        assert!(judged.problems.iter().any(|p| p.contains("MISSING")), "a missing dir for a declaration list is unreadable");
        assert!(judged.notes.is_empty());
    }

    /// A dir of one key per file waits on disk file by file in a cold working tree, so the read waits are overlapped (reading a 447-file registry
    /// sequentially took a median of 2.4 seconds in a cold working tree on a busy Pod). The answer is the same as reading sequentially, and notices for unreadable files are in file order.
    #[test]
    fn a_dir_of_one_key_files_is_read_with_overlapping_waits_and_answers_in_file_order() {
        use super::super::bulk_read::watch::watching;
        let dir = tempfile::TempDir::new().unwrap();
        let table = dir.path().join("BREACHES");
        std::fs::create_dir(&table).unwrap();
        for n in 0..60 {
            std::fs::write(table.join(format!("k{n:02}.txt")), format!("app/f{n}.hy::DOEFF124::g{n}\nreason\n")).unwrap();
        }
        std::fs::write(table.join("k10a.txt"), "\nreason\n").unwrap(); // line 1 is empty
        std::fs::create_dir(table.join("k20a.txt")).unwrap(); // cannot be read as a file
        std::fs::write(table.join("k30a.txt"), [0xff, 0xfe, b'\n']).unwrap(); // not UTF-8
        let (registry, peak) = watching(std::time::Duration::from_millis(20), || Registry::load(dir.path(), &["BREACHES".to_string()], &[]));
        assert!(peak >= 8, "registry read waits are not overlapping (max in flight at once: {peak})");
        assert_eq!(registry.keys.len(), 60);
        assert_eq!(registry.origins.get("app/f7.hy::DOEFF124::g7").map(String::as_str), Some("BREACHES/k07.txt"));
        let order: Vec<&str> = ["k10a.txt", "k20a.txt", "k30a.txt"].to_vec();
        assert_eq!(registry.problems.len(), 3, "{:?}", registry.problems);
        for (problem, name) in registry.problems.iter().zip(order) {
            assert!(problem.contains(name), "{problem} should be the notice for {name} (file order)");
        }
    }

    #[test]
    fn judged_keys_of_a_big_dir_are_read_with_overlapping_waits() {
        use super::super::bulk_read::watch::watching;
        let dir = tempfile::TempDir::new().unwrap();
        let list = dir.path().join("FALSE");
        std::fs::create_dir(&list).unwrap();
        for n in 0..40 {
            std::fs::write(list.join(format!("j{n:02}.txt")), format!("a.hy::DOEFF201::f{n}\njudgment reason {n}\n")).unwrap();
        }
        let (judged, peak) = watching(std::time::Duration::from_millis(20), || JudgedKeys::load(dir.path(), &["FALSE".to_string()], Absent::Unreadable));
        assert!(peak >= 8, "judgment list read waits are not overlapping (max in flight at once: {peak})");
        assert_eq!(judged.reasons.len(), 40);
        assert_eq!(judged.reasons.get("a.hy::DOEFF201::f5").map(String::as_str), Some("judgment reason 5"));
        assert!(judged.problems.is_empty(), "{:?}", judged.problems);
    }

    /// A missing dir of a shrinking table (Absent::Empty) is an empty list (one notice line, not a read error). Adding one key
    /// makes it take effect as before.
    #[test]
    fn an_absent_shrinking_list_reads_as_empty_and_one_added_key_counts() {
        let dir = tempfile::TempDir::new().unwrap();
        let absent = JudgedKeys::load(dir.path(), &["UNSERVED".to_string()], Absent::Empty);
        assert!(absent.reasons.is_empty());
        assert!(absent.problems.is_empty(), "{:?}", absent.problems);
        assert_eq!(absent.notes.len(), 1);
        assert!(absent.notes[0].contains("UNSERVED"));
        let list = dir.path().join("UNSERVED");
        std::fs::create_dir(&list).unwrap();
        std::fs::write(list.join("a.txt"), "app.orders.intent.Send\nwrite in #1\n").unwrap();
        let one = JudgedKeys::load(dir.path(), &["UNSERVED".to_string()], Absent::Empty);
        assert_eq!(one.reasons.get("app.orders.intent.Send").map(String::as_str), Some("write in #1"));
        assert!(one.problems.is_empty() && one.notes.is_empty());
    }
}

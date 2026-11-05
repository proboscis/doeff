//! DOEFF148: spellings confined to the files that may write them (#1436 — originally a temporary check in agora-controllers
//! controllers/screen/tests/intake_only_rules.hy, part of 3, 4, the words of 6 and 7, the first half of 8, and 9).
//!
//! For each group in architecture.hy's `:confined-spellings [(confined-spelling "name" :patterns [r"…"] :files [..] :except [..] :why "…") …]`,
//! read every Hy/Python file that matches `:files` and does not match `:except`; if any of the `:patterns` matches the text with comments removed
//! (strings are kept), report one finding per file (the match count goes in the message; the position is the first match). `:except` is the files that may write
//! this spelling; if empty, the spelling is written nowhere in `:files`. If no file matches `:files`, report one finding at the position in architecture.hy (`missing` —
//! a population of 0 is not allowed to be green).
//! Unlike DOEFF146 (:single-point-vocabulary), this also counts inside strings and reads Python files — the verb of the outside entry `"POST"`, the route spelling
//! `"/api/intake"`, and the file declaring effects (effects.py) all live in strings or in Python.
//! Names derived from a group's `:names-bound-in` (bound_names.rs) are appended after the group's `:patterns` when architecture.hy is read, and here they are matched
//! the same as written spellings.

use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::Range;

use super::architecture::ConfinedSpelling;
use super::spelling_scope::{code_text, line_of, selected_files};

/// Kind of match (a closed set of 2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfinedProblem {
    /// There are count matches outside the files that may write it.
    Outside { count: usize },
    /// No file matches `:files`.
    Missing,
}

/// One match (file x group).
#[derive(Debug, Clone)]
pub struct ConfinedFinding {
    pub rel: String,
    /// Name of the group (detail of the registry key — for missing it is `<group>:missing`).
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: ConfinedProblem,
    /// Spelling of the first match (None for missing — when a derived name turns it red, so the notice can say which name).
    pub first: Option<String>,
    /// Names derived from the group's :names-bound-in (written in the notice's explanation — so one can see, from outside architecture.hy, why an unwritten name is a spelling of the group).
    pub bound: Vec<String>,
}

/// For each group, finds matches outside `:except` (in the order of group declarations, then path order). Files that could not be read are pushed to errors. If focus (a name given on the command line)
/// is present, reads only the files under it — a match attaches to its file, one per file, so the answer is the same as reading everything and then filtering by the named paths
/// (the population-0 notice is decided by the glob and is not read). A one-file commit hook used to read the whole population for every group.
pub fn find(root: &Path, groups: &[ConfinedSpelling], architecture_rel: &str, focus: Option<&[PathBuf]>) -> (Vec<ConfinedFinding>, Vec<String>) {
    let mut out = Vec::new();
    let mut errors = Vec::new();
    for group in groups {
        let regexes: Vec<regex::Regex> = group.patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
        let population = selected_files(root, &group.files, &[]);
        if population.is_empty() {
            out.push(ConfinedFinding {
                rel: architecture_rel.to_string(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: group.range,
                problem: ConfinedProblem::Missing,
                first: None,
                bound: group.bound_names.clone(),
            });
            continue;
        }
        for rel in population
            .iter()
            .filter(|rel| !group.except.iter().any(|p| super::paths::glob_matches(p, rel)))
            .filter(|rel| focus.is_none_or(|only| only.iter().any(|p| root.join(rel.as_str()).starts_with(p))))
        {
            let source = match std::fs::read_to_string(root.join(rel)) {
                Ok(source) => source,
                Err(error) => {
                    errors.push(format!("{}: could not read: {}", rel, error));
                    continue;
                }
            };
            let code = code_text(rel, &source);
            let mut hits: Vec<(usize, &str)> = regexes.iter().flat_map(|re| re.find_iter(&code).map(|m| (m.start(), m.as_str()))).collect();
            if hits.is_empty() {
                continue;
            }
            hits.sort_unstable();
            hits.dedup_by_key(|(start, _)| *start);
            let line = line_of(&code, hits[0].0);
            let at = doeff_indexer::hy_index::Position { line, character: 0 };
            out.push(ConfinedFinding {
                rel: rel.clone(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: Range { start: at, end: at },
                problem: ConfinedProblem::Outside { count: hits.len() },
                first: Some(hits[0].1.to_string()),
                bound: group.bound_names.clone(),
            });
        }
    }
    (out, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn group(patterns: &[&str], files: &[&str], except: &[&str]) -> ConfinedSpelling {
        ConfinedSpelling {
            name: "g".to_string(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            files: files.iter().map(|p| p.to_string()).collect(),
            except: except.iter().map(|p| p.to_string()).collect(),
            names_bound_in: Vec::new(),
            bound_names: Vec::new(),
            why: "reason".to_string(),
            range: Range::default(),
        }
    }

    #[test]
    fn a_spelling_in_a_string_is_counted_but_not_in_a_comment() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "; \"/api/intake\" was retired\n(setv u \"/api/intake\")\n").unwrap();
        std::fs::write(dir.path().join("s/b.py"), "# /api/intake\nx = 1\n").unwrap();
        let (hits, errors) = find(dir.path(), &[group(&[r"/api/intake"], &["s/**"], &[])], "architecture.hy", None);
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].range.start.line, 1);
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 1 });
    }

    #[test]
    fn the_except_files_may_hold_the_spelling() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake x)\n").unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"\bPostIntake\b"], &["s/**"], &["s/b.hy"])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 2 });
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = tempfile::tempdir().unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"x"], &["gone/**"], &[])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "architecture.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Missing);
    }

    #[test]
    fn named_paths_read_only_the_named_files() {
        // Read only the files under the named paths — do not read an unreadable file (not UTF-8) outside them; matches come only from the named ones.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/broken.hy"), [0xff_u8, 0xfe]).unwrap();
        let groups = [group(&[r"\bPostIntake\b"], &["s/**"], &[])];
        let (all, all_errors) = find(dir.path(), &groups, "architecture.hy", None);
        assert_eq!(all.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy", "s/b.hy"]);
        assert_eq!(all_errors.len(), 1, "{:?}", all_errors);
        let named = [dir.path().join("s/a.hy")];
        let (hits, errors) = find(dir.path(), &groups, "architecture.hy", Some(&named));
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy"]);
    }
}

//! DOEFF161: spellings whose count is fixed (#1437; originally a temporary check in agora-controllers:
//! controllers/screen/tests/intake_only_rules.hy items 1 and 3 (the counts and the seat), the remainder of 4, the sections of 5 and 7, the second half of 8, and the existence of the semgrep rules).
//!
//! For each declaration `:counted-spellings [(counted-spelling "name" :pattern r"…" :files [..] :count N :why "…") …]` in architecture.hy,
//! counts the matches of `:pattern` in the text of the files matching `:files` (any extension: Hy and Python are read with comments dropped, others as they are),
//! and emits one finding if the count is not the fixed count. `:count N` means exactly N and `:at-least N` means N or more (exactly one of the two).
//! If `:within ["name" …]` is given, for each named top-level definition (a form whose head starts with def) in the Hy files of `:files`, counts the matches
//! inside that form (this fixes that the matches are confined to one definition; a count alone cannot tell apart a match that grew outside the definition).
//! If no file matches `:files`, or a named definition does not exist, it is `missing` (a population of 0 is not made green).

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::Reader;
use doeff_indexer::hy_index::{Position, Range};
use serde::{Deserialize, Serialize};

use super::architecture::{CountedSpelling, WantedCount};
use super::facts_cache;
use super::paths::glob_matches;
use super::spelling_scope::{code_text, line_of, walk_candidates, Extensions};
use super::top_level;

/// Kind of finding (a closed set of two).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CountProblem {
    /// The count does not match (within = name of the definition counted; None means the whole of :files).
    Mismatch { found: usize, wanted: WantedCount, within: Option<String> },
    /// There is nothing to count.
    Missing { reason: String },
}

/// One finding.
#[derive(Debug, Clone)]
pub struct CountFinding {
    pub rel: String,
    /// Name of the declaration.
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: CountProblem,
    /// Detail of the registry key (`<name>` / `<name>:<definition>` / `<name>:missing` / `<name>:<definition>:missing`).
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// Cached per-file counts for one declaration (path-free so `objects/` is shared across worktrees).
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedCountForDecl {
    decl_idx: usize,
    total_count: usize,
    first_line: Option<u32>,
    /// For each matching `:within` definition in this `.hy` file: `(within_idx, def_start_line, count_inside_def)`.
    within_hits: Vec<(usize, u32, usize)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum CachedCountedFile {
    Data { rel: String, decls: Vec<CachedCountForDecl> },
    Error(String),
}

/// Judges all declarations. `declared_in` spells a declaration file from the root (paths::declared_rel) — a declaration with nothing to count is reported in the file it is written in.
/// Walks collapsed roots once, reads, comment-strips, and parses each file at most once in parallel via `facts_cache` (keyed by declarations), preserving exact output order and `Missing` checks.
pub fn find(root: &Path, declarations: &[CountedSpelling], declared_in: impl Fn(&Path) -> String) -> (Vec<CountFinding>, Vec<String>) {
    if declarations.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let all_candidates = walk_candidates(root, declarations.iter().flat_map(|d| d.files.iter()), Extensions::Any);
    let compiled_regexes: Vec<Option<regex::Regex>> = declarations.iter().map(|d| regex::Regex::new(&d.pattern).ok()).collect();

    let mut missing_population = vec![false; declarations.len()];
    let mut file_decls: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (di, declared) in declarations.iter().enumerate() {
        if compiled_regexes[di].is_none() {
            continue;
        }
        let mut any = false;
        for rel in &all_candidates {
            if declared.files.iter().any(|g| glob_matches(g, rel)) {
                any = true;
                file_decls.entry(rel.clone()).or_default().push(di);
            }
        }
        if !any {
            missing_population[di] = true;
        }
    }

    let wanted_files: Vec<(String, PathBuf)> = file_decls.keys().map(|rel| (rel.clone(), root.join(rel))).collect();
    let decl_lookup: HashMap<&str, &[usize]> = file_decls.iter().map(|(rel, ds)| (rel.as_str(), ds.as_slice())).collect();

    let mut errors = Vec::new();
    // Per declaration, in sorted `rel` order: readable files' cached counts.
    let mut per_decl_files: Vec<Vec<(String, CachedCountForDecl)>> = vec![Vec::new(); declarations.len()];

    if !wanted_files.is_empty() {
        let key = facts_cache::hash_key(&declarations);
        let cached: Vec<CachedCountedFile> = facts_cache::per_file_compact_keyed(root, "counted-spellings", &key, &wanted_files, |rel, path| {
            #[cfg(test)]
            super::perf_counters::record_read("counted_spellings", rel);
            let source = match std::fs::read_to_string(path) {
                Ok(source) => source,
                Err(error) => return Some(CachedCountedFile::Error(format!("{}: unreadable: {}", rel, error))),
            };
            #[cfg(test)]
            super::perf_counters::record_strip("counted_spellings", rel);
            let code = code_text(rel, &source);
            let matching = decl_lookup.get(rel).copied().unwrap_or(&[]);
            let need_hy_forms = rel.ends_with(".hy") && matching.iter().any(|&di| !declarations[di].within.is_empty());
            let hy_forms = need_hy_forms.then(|| {
                #[cfg(test)]
                super::perf_counters::record_parse("counted_spellings", rel);
                Reader::new(&source, 0, source.len()).read_all()
            });
            let mut decls = Vec::with_capacity(matching.len());
            for &di in matching {
                let Some(regex) = compiled_regexes[di].as_ref() else { continue };
                let declared = &declarations[di];
                if declared.within.is_empty() {
                    let mut total_count = 0usize;
                    let mut first_start: Option<usize> = None;
                    for m in regex.find_iter(&code) {
                        if first_start.is_none() {
                            first_start = Some(m.start());
                        }
                        total_count += 1;
                    }
                    decls.push(CachedCountForDecl {
                        decl_idx: di,
                        total_count,
                        first_line: first_start.map(|at| line_of(&code, at)),
                        within_hits: Vec::new(),
                    });
                } else {
                    let mut within_hits = Vec::new();
                    if let Some(forms) = hy_forms.as_deref() {
                        for (wi, name) in declared.within.iter().enumerate() {
                            if let Some(form) = top_level::definition(&source, forms, name) {
                                let (start, end) = (form.span.start, form.span.end);
                                let found = regex.find_iter(&code).filter(|m| m.start() >= start && m.start() < end).count();
                                within_hits.push((wi, line_of(&code, start), found));
                            }
                        }
                    }
                    decls.push(CachedCountForDecl { decl_idx: di, total_count: 0, first_line: None, within_hits });
                }
            }
            Some(CachedCountedFile::Data { rel: rel.to_string(), decls })
        });

        for entry in cached {
            match entry {
                CachedCountedFile::Error(err) => errors.push(err),
                CachedCountedFile::Data { rel, decls } => {
                    for item in decls {
                        per_decl_files[item.decl_idx].push((rel.clone(), item));
                    }
                }
            }
        }
    }

    let mut out = Vec::new();
    for (di, declared) in declarations.iter().enumerate() {
        if compiled_regexes[di].is_none() {
            continue;
        }
        let declaration_rel = declared_in(&declared.file);
        let finding = |rel: String, range: Range, problem: CountProblem, detail: String| CountFinding {
            rel,
            group: declared.name.clone(),
            why: declared.why.clone(),
            range,
            problem,
            detail,
        };
        if missing_population[di] {
            out.push(finding(
                declaration_rel,
                declared.range,
                CountProblem::Missing { reason: "no file matches :files".to_string() },
                format!("{}:missing", declared.name),
            ));
            continue;
        }
        let files_data = &per_decl_files[di];
        if declared.within.is_empty() {
            let total: usize = files_data.iter().map(|(_, d)| d.total_count).sum();
            if declared.wanted.accepts(total) {
                continue;
            }
            let (rel, line) = match files_data.iter().find_map(|(rel, d)| d.first_line.map(|l| (rel.clone(), l))) {
                Some(hit) => hit,
                None => (files_data.first().map(|(rel, _)| rel.clone()).unwrap_or(declaration_rel), 0),
            };
            out.push(finding(rel, at_line(line), CountProblem::Mismatch { found: total, wanted: declared.wanted, within: None }, declared.name.clone()));
        } else {
            for (wi, name) in declared.within.iter().enumerate() {
                let located = files_data
                    .iter()
                    .filter(|(rel, _)| rel.ends_with(".hy"))
                    .find_map(|(rel, d)| d.within_hits.iter().find(|(idx, _, _)| *idx == wi).map(|&(_, start_line, found)| (rel, start_line, found)));
                let Some((rel, start_line, found)) = located else {
                    out.push(finding(
                        declaration_rel.clone(),
                        declared.range,
                        CountProblem::Missing { reason: format!("the Hy files of :files have no definition {}", name) },
                        format!("{}:{}:missing", declared.name, name),
                    ));
                    continue;
                };
                if !declared.wanted.accepts(found) {
                    out.push(finding(
                        rel.clone(),
                        at_line(start_line),
                        CountProblem::Mismatch { found, wanted: declared.wanted, within: Some(name.clone()) },
                        format!("{}:{}", declared.name, name),
                    ));
                }
            }
        }
    }
    (out, errors)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// The declarations are written in architecture.hy at the root.
    fn declared_here(_: &Path) -> String {
        "architecture.hy".to_string()
    }

    fn declared(pattern: &str, files: &[&str], within: &[&str], wanted: WantedCount) -> CountedSpelling {
        CountedSpelling {
            name: "g".to_string(),
            pattern: pattern.to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            within: within.iter().map(|p| p.to_string()).collect(),
            wanted,
            why: "reason".to_string(),
            range: Range::default(),
            file: PathBuf::from("architecture.hy"),
        }
    }

    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    #[test]
    fn the_total_over_the_files_must_be_the_count_and_comments_do_not_count() {
        let dir = repo(&[("p/a.hy", "(f \"POST\") ; \"POST\"\n"), ("p/b.hy", "(g \"POST\")\n")]);
        let (hits, _) = find(dir.path(), &[declared(r#""POST""#, &["p/*.hy"], &[], WantedCount::Exactly(2))], declared_here);
        assert!(hits.is_empty(), "{:?}", hits);
        let (hits, _) = find(dir.path(), &[declared(r#""POST""#, &["p/*.hy"], &[], WantedCount::Exactly(3))], declared_here);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, CountProblem::Mismatch { found: 2, wanted: WantedCount::Exactly(3), within: None });
        assert_eq!(hits[0].rel, "p/a.hy");
    }

    #[test]
    fn at_least_accepts_more_and_other_files_are_read_as_they_are() {
        let dir = repo(&[("s/rules.yaml", "- id: one\n- id: one\n")]);
        let (hits, _) = find(dir.path(), &[declared(r"id: one", &["s/rules.yaml"], &[], WantedCount::AtLeast(1))], declared_here);
        assert!(hits.is_empty(), "{:?}", hits);
        let (hits, _) = find(dir.path(), &[declared(r"id: two", &["s/rules.yaml"], &[], WantedCount::AtLeast(1))], declared_here);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/rules.yaml");
    }

    #[test]
    fn within_counts_inside_each_named_definition() {
        let text = "(defk send [x] (Req \"POST\" x))\n\n(defk read [x] (Req \"GET\" x))\n(setv loose (Req \"POST\" 1))\n";
        let dir = repo(&[("p/peer.hy", text)]);
        let (hits, _) =
            find(dir.path(), &[declared(r#""POST""#, &["p/peer.hy"], &["send", "read", "gone"], WantedCount::Exactly(1))], declared_here);
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["g:read", "g:gone:missing"], "{:?}", hits);
        assert_eq!(hits[0].range.start.line, 2);
        assert_eq!(hits[1].rel, "architecture.hy");
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = repo(&[]);
        let (hits, _) = find(dir.path(), &[declared(r"x", &["gone/*.hy"], &[], WantedCount::Exactly(0))], declared_here);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].detail, "g:missing");
    }
}

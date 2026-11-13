//! DOEFF161: spellings whose count is fixed (#1437; originally a temporary check in agora-controllers:
//! controllers/screen/tests/intake_only_rules.hy items 1 and 3 (the counts and the seat), the remainder of 4, the sections of 5 and 7, the second half of 8, and the existence of the semgrep rules).
//!
//! For each declaration `:counted-spellings [(counted-spelling "name" :pattern r"…" :files [..] :count N :why "…") …]` in architecture.hy,
//! counts the matches of `:pattern` in the text of the files matching `:files` (any extension: Hy and Python are read with comments dropped, others as they are),
//! and emits one finding if the count is not the fixed count. `:count N` means exactly N and `:at-least N` means N or more (exactly one of the two).
//! If `:within ["name" …]` is given, for each named top-level definition (a form whose head starts with def) in the Hy files of `:files`, counts the matches
//! inside that form (this fixes that the matches are confined to one definition; a count alone cannot tell apart a match that grew outside the definition).
//! If no file matches `:files`, or a named definition does not exist, it is `missing` (a population of 0 is not made green).

use std::path::Path;

use doeff_indexer::hy_index::reader::Reader;
use doeff_indexer::hy_index::{Position, Range};

use super::architecture::{CountedSpelling, WantedCount};
use super::spelling_scope::{code_text, line_of, selected, Extensions};
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

/// Byte range of a top-level form whose head starts with def and whose second element is a name.
fn definition_span(source: &str, name: &str) -> Option<(usize, usize)> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    top_level::definition(source, &forms, name).map(|form| (form.span.start, form.span.end))
}

/// Judges one declaration (declaration_rel = the file it is written in, from the root). Files that could not be read are pushed to errors.
fn judge_one(root: &Path, declared: &CountedSpelling, declaration_rel: &str, errors: &mut Vec<String>) -> Vec<CountFinding> {
    let finding = |rel: String, range: Range, problem: CountProblem, detail: String| CountFinding {
        rel,
        group: declared.name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let Ok(regex) = regex::Regex::new(&declared.pattern) else { return Vec::new() };
    let population = selected(root, &declared.files, &[], Extensions::Any);
    if population.is_empty() {
        return vec![finding(
            declaration_rel.to_string(),
            declared.range,
            CountProblem::Missing { reason: "no file matches :files".to_string() },
            format!("{}:missing", declared.name),
        )];
    }
    // Per file: (path, original text, text with comments dropped).
    let mut texts: Vec<(String, String, String)> = Vec::new();
    for rel in population {
        match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => {
                let code = code_text(&rel, &source);
                texts.push((rel, source, code));
            }
            Err(error) => errors.push(format!("{}: unreadable: {}", rel, error)),
        }
    }
    if declared.within.is_empty() {
        let starts: Vec<(usize, usize)> =
            texts.iter().enumerate().flat_map(|(i, (_, _, code))| regex.find_iter(code).map(move |m| (i, m.start()))).collect();
        if declared.wanted.accepts(starts.len()) {
            return Vec::new();
        }
        let (rel, line) = match starts.first() {
            Some(&(i, at)) => (texts[i].0.clone(), line_of(&texts[i].2, at)),
            None => (texts.first().map(|t| t.0.clone()).unwrap_or_else(|| declaration_rel.to_string()), 0),
        };
        return vec![finding(
            rel,
            at_line(line),
            CountProblem::Mismatch { found: starts.len(), wanted: declared.wanted, within: None },
            declared.name.clone(),
        )];
    }
    let mut out = Vec::new();
    for name in &declared.within {
        let located = texts
            .iter()
            .filter(|(rel, _, _)| rel.ends_with(".hy"))
            .find_map(|(rel, source, code)| definition_span(source, name).map(|span| (rel, code, span)));
        let Some((rel, code, (start, end))) = located else {
            out.push(finding(
                declaration_rel.to_string(),
                declared.range,
                CountProblem::Missing { reason: format!("the Hy files of :files have no definition {}", name) },
                format!("{}:{}:missing", declared.name, name),
            ));
            continue;
        };
        let found = regex.find_iter(code).filter(|m| m.start() >= start && m.start() < end).count();
        if !declared.wanted.accepts(found) {
            out.push(finding(
                rel.clone(),
                at_line(line_of(code, start)),
                CountProblem::Mismatch { found, wanted: declared.wanted, within: Some(name.clone()) },
                format!("{}:{}", declared.name, name),
            ));
        }
    }
    out
}

/// Judges all declarations. `declared_in` spells a declaration file from the root (paths::declared_rel) — a declaration with nothing to count is reported in the file it is written in.
pub fn find(root: &Path, declarations: &[CountedSpelling], declared_in: impl Fn(&Path) -> String) -> (Vec<CountFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, &declared_in(&declared.file), &mut errors)).collect();
    (found, errors)
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

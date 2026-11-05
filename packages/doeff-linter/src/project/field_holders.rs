//! DOEFF149: the roster of classes that hold a typed field (parent #1192 -- originally agora-controllers' temporary check
//! item 3 of controllers/screen/tests/record_body_rules.hy).
//!
//! For each declaration `:field-holders [(field-holders "name" :type "T" :files [..] :classes [..] :holders [..] :why "…") …]` in architecture.hy,
//! reads the module-level classes of the Python files (.py, .pyi) matching `:files`, counts as a "holder" each class that has, directly in its body, a field (an annotated assignment `name: annotation`)
//! whose annotation contains the spelling of `:type` as a word, and compares with the `:holders` list.
//!   * A holder not in the list: one finding at the class's position (key detail `<name>:<class>`).
//!   * A class in the list that is not a holder (it has no field of that type, or the class does not exist): one finding at the position in architecture.hy (`<name>:<class>:absent`).
//!   * If `:classes` is written, only the named classes are counted. If a named class does not exist, one finding at the position in architecture.hy
//!     (`<name>:<class>:missing`).
//!   * If no Python file matches `:files`, one finding at the position in architecture.hy (`<name>:missing` -- an empty population is not made green).
//! Files that do not parse into a syntax tree are pushed into errors as unreadable (not silently made green).

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::{Position, Range};
use rustpython_ast::{Expr, Mod, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FieldHolders;
use super::spelling_scope::{line_of, selected_files};

/// Kind of finding (a closed set of 4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HolderProblem {
    /// A class not in the list has a field of that type.
    Unlisted { class: String },
    /// A class in the list does not have a field of that type (also when the class does not exist).
    Absent { class: String },
    /// A class named in :classes does not exist.
    NoClass { class: String },
    /// No Python file matches `:files`.
    NoFiles,
}

/// One finding.
#[derive(Debug, Clone)]
pub struct HolderFinding {
    pub rel: String,
    /// Name of the declaration.
    pub group: String,
    /// Spelling of the counted type.
    pub type_name: String,
    pub why: String,
    pub range: Range,
    pub problem: HolderProblem,
    /// Detail of the registry key.
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// For each module-level class in a Python source, (name, line, spellings of the annotations of the annotated fields directly in the body).
fn classes_of(rel: &str, source: &str) -> Result<Vec<(String, u32, Vec<String>)>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("does not parse into a syntax tree: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    Ok(module
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(class) => {
                let annotations = class
                    .body
                    .iter()
                    .filter_map(|field| match field {
                        Stmt::AnnAssign(field) if matches!(field.target.as_ref(), Expr::Name(_)) => {
                            let range = field.annotation.range();
                            source.get(range.start().to_usize()..range.end().to_usize()).map(str::to_string)
                        }
                        _ => None,
                    })
                    .collect();
                Some((class.name.to_string(), line_of(source, class.range().start().to_usize()), annotations))
            }
            _ => None,
        })
        .collect())
}

/// Judges one declaration. Unreadable files are pushed into errors.
fn judge_one(root: &Path, declared: &FieldHolders, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<HolderFinding> {
    let finding = |rel: String, range: Range, problem: HolderProblem, detail: String| HolderFinding {
        rel,
        group: declared.name.clone(),
        type_name: declared.type_name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let population: Vec<String> =
        selected_files(root, &declared.files, &[]).into_iter().filter(|rel| rel.ends_with(".py") || rel.ends_with(".pyi")).collect();
    if population.is_empty() {
        return vec![finding(architecture_rel.to_string(), declared.range, HolderProblem::NoFiles, format!("{}:missing", declared.name))];
    }
    let Ok(word) = regex::Regex::new(&format!(r"\b{}\b", regex::escape(&declared.type_name))) else { return Vec::new() };
    // Class name -> (file, line, whether it has a field of that type). If two classes share a name, take the one that has it (do not miss a holder).
    let mut seen: BTreeMap<String, (String, u32, bool)> = BTreeMap::new();
    for rel in population {
        let source = match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: unreadable: {}", rel, error));
                continue;
            }
        };
        let classes = match classes_of(&rel, &source) {
            Ok(classes) => classes,
            Err(reason) => {
                errors.push(format!("{}: {}", rel, reason));
                continue;
            }
        };
        for (name, line, annotations) in classes {
            let holds = annotations.iter().any(|annotation| word.is_match(annotation));
            seen.entry(name)
                .and_modify(|entry| {
                    if holds && !entry.2 {
                        *entry = (rel.clone(), line, true);
                    }
                })
                .or_insert((rel.clone(), line, holds));
        }
    }
    let counted = |name: &str| declared.classes.is_empty() || declared.classes.iter().any(|c| c == name);
    let mut out = Vec::new();
    for name in declared.classes.iter().filter(|name| !seen.contains_key(*name)) {
        out.push(finding(
            architecture_rel.to_string(),
            declared.range,
            HolderProblem::NoClass { class: name.clone() },
            format!("{}:{}:missing", declared.name, name),
        ));
    }
    for (name, (rel, line, holds)) in &seen {
        if *holds && counted(name) && !declared.holders.contains(name) {
            out.push(finding(rel.clone(), at_line(*line), HolderProblem::Unlisted { class: name.clone() }, format!("{}:{}", declared.name, name)));
        }
    }
    for name in &declared.holders {
        let holds = seen.get(name).is_some_and(|entry| entry.2);
        // If a class is named in :classes but does not exist, the missing finding above has already been emitted (do not make one thing two findings).
        let reported = !declared.classes.is_empty() && !seen.contains_key(name);
        if !holds && !reported {
            out.push(finding(
                architecture_rel.to_string(),
                declared.range,
                HolderProblem::Absent { class: name.clone() },
                format!("{}:{}:absent", declared.name, name),
            ));
        }
    }
    out
}

/// Judges all declarations. architecture_rel is the path of architecture.hy from the root (the position for a missing class in the list).
pub fn find(root: &Path, declarations: &[FieldHolders], architecture_rel: &str) -> (Vec<HolderFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn holders(type_name: &str, files: &[&str], classes: &[&str], holders: &[&str]) -> FieldHolders {
        FieldHolders {
            name: "cache".to_string(),
            type_name: type_name.to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            classes: classes.iter().map(|p| p.to_string()).collect(),
            holders: holders.iter().map(|p| p.to_string()).collect(),
            why: "reason".to_string(),
            range: Range::default(),
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

    const TYPES: &str = "from dataclasses import dataclass\n\n\
        @dataclass(frozen=True)\nclass RecordCache:\n    \"\"\"Note: RecordCache is the container of the body.\"\"\"\n    rows: tuple\n\n\
        @dataclass(frozen=True)\nclass ConversationSent:\n    cid: str\n    records: RecordCache | None = None\n\n\
        class Helper:\n    def f(self) -> None:\n        cache: RecordCache = RecordCache(())\n\n\
        @dataclass(frozen=True)\nclass ServerState:\n    sent: dict\n    record_note: str = \"RecordCache\"\n";

    #[test]
    fn a_field_annotation_holds_the_type_and_docstrings_methods_and_strings_do_not() {
        let found = classes_of("t.py", TYPES).unwrap();
        let word = regex::Regex::new(r"\bRecordCache\b").unwrap();
        let held: Vec<&str> =
            found.iter().filter(|(_, _, annotations)| annotations.iter().any(|a| word.is_match(a))).map(|(name, _, _)| name.as_str()).collect();
        assert_eq!(held, vec!["ConversationSent"]);
    }

    #[test]
    fn the_word_must_stand_alone() {
        let dir = repo(&[("m/types.py", "class Page:\n    body: RecordBody\n\nclass State:\n    note: Record\n")]);
        let (hits, errors) = find(dir.path(), &[holders("Record", &["m/types.py"], &[], &["State"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        assert!(hits.is_empty(), "{:?}", hits);
    }

    #[test]
    fn unlisted_and_absent_holders_are_each_found() {
        let dir = repo(&[
            ("m/types.py", TYPES),
            ("m/more.py", "class Sneaky:\n    cache: list[RecordCache]\n"),
        ]);
        let (hits, errors) =
            find(dir.path(), &[holders("RecordCache", &["m/types.py", "m/more.py"], &[], &["ConversationSent", "RecordPage"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["cache:Sneaky", "cache:RecordPage:absent"], "{:?}", hits);
        assert_eq!(hits[0].rel, "m/more.py");
        assert_eq!(hits[0].range.start.line, 0);
        assert_eq!(hits[1].rel, "architecture.hy");
    }

    #[test]
    fn named_classes_narrow_the_census_and_a_missing_one_is_found() {
        let dir = repo(&[("m/types.py", "class ServerState:\n    cache: Record\n\nclass Other:\n    cache: Record\n")]);
        let (hits, _) = find(dir.path(), &[holders("Record", &["m/types.py"], &["ServerState", "Gone"], &[])], "architecture.hy");
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["cache:Gone:missing", "cache:ServerState"], "{:?}", hits);
    }

    #[test]
    fn an_empty_population_is_missing_and_an_unparsable_file_is_an_error() {
        let empty = repo(&[("m/types.hy", "(defclass X [])\n")]);
        let (hits, _) = find(empty.path(), &[holders("X", &["m/*"], &[], &[])], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, HolderProblem::NoFiles);
        let broken = repo(&[("m/types.py", "class Broken(:\n")]);
        let (_, errors) = find(broken.path(), &[holders("X", &["m/types.py"], &[], &[])], "architecture.hy");
        assert!(errors.iter().any(|e| e.contains("does not parse into a syntax tree")), "{:?}", errors);
    }
}

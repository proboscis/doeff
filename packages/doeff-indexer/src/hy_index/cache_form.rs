//! The form in which one file's index is placed in the disk cache.
//!
//! doeff-linter builds the Hy index of the whole repo even for a single-file run. Most of that is per-file reading and name resolution (`index_file` —
//! decided only by the file's contents and path), so the linter caches in this form per file and re-reads only changed files.
//! The trace of raw side-effect via-evidence across files (`annotate_raw`) has an answer that changes with other files' contents and the catalog, so it is not cached —
//! what this form holds is the index before tracing.
//!
//! The serde form of the index types is the contract's JSON form, and of the fields the linter's judgments read, the following two kinds cannot be carried in that form. This form carries them separately:
//! - Fields not emitted in the contract's JSON (`serde(skip)`) — `member`, `target`, `type_only`, `inspected` of `Reference`; `form_range`, `keywords`, `arguments` of `Call`.
//! - Fields emitted or not depending on whether there is a value (`skip_serializing_if`) — `checks` of `Definition`. The cache is placed in a binary form with no field names
//!   (the linter's facts_cache compact), so if the number of fields varies with values, reading back goes out of alignment. The index side always
//!   writes it aligned to `Some`, and on reading back restores the separately held value.
//!
//! Forgetting to add a field is stopped at compile time: `fields_are_accounted_for` destructures every index type, naming the fields (without using `..`).
//! Adding a field to a type makes that place fail to compile — if the added field is one emitted in the contract's JSON (has neither `serde` skip nor skip_serializing_if),
//! receive it with `_`; otherwise carry it in this form and then receive it by name (when mainline ecccbd446 added `keywords` to `Call`,
//! it was missed from a hand-counted list, and only runs using the cache counted calls that passed `:transport` as "not passed").

use serde::{Deserialize, Serialize};

use super::model::{
    Call, CallArgument, ContractClause, Definition, HyFileIndex, Import, NameRef, ParamType, RawEvidence, RawMark, RawStep, RawVia, Reference,
    TypeNote,
};
use super::position::{Position, Range};

/// The fields of one reference that are not emitted in the contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ReferenceExtra {
    member: bool,
    target: Option<String>,
    type_only: bool,
    inspected: bool,
    call_arity: Option<usize>,
}

/// The fields of one call that are not emitted in the contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CallExtra {
    form_range: Range,
    keywords: Vec<String>,
    arguments: Vec<CallArgument>,
}

/// The index of one file placed in the cache (before tracing), and the fields that cannot be carried in the contract's JSON form (in the same order as references, calls, and definitions).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedHyFile {
    file: HyFileIndex,
    references: Vec<ReferenceExtra>,
    calls: Vec<CallExtra>,
    definition_checks: Vec<Option<Vec<String>>>,
}

/// The number of fields in the cache form does not match the index (a broken cache) — holds the path of the file to re-read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CachedHyFileMismatch {
    pub path: String,
}

impl CachedHyFile {
        /// Turns one index into the cache form (copies separately the fields that cannot be carried in the contract form, and aligns `checks` to always `Some`).
    pub fn of(mut file: HyFileIndex) -> Self {
        let references = file.references.iter().map(|r| ReferenceExtra { member: r.member, target: r.target.clone(), type_only: r.type_only, inspected: r.inspected, call_arity: r.call_arity }).collect();
        let calls = file.calls.iter().map(|c| CallExtra { form_range: c.form_range, keywords: c.keywords.clone(), arguments: c.arguments.clone() }).collect();
        let definition_checks = file.definitions.iter_mut().map(|d| d.checks.replace(Vec::new())).collect();
        CachedHyFile { file, references, calls, definition_checks }
    }

        /// Replaces only the index file's path (`HyFileIndex::path` — a spelling that includes the root's absolute path). Because the cache is shared across worktrees,
        /// it is stored with an empty path, and after reading the current root's file path is attached (fields other than path are decided only by the file's contents and the path from the root).
    pub fn set_path(&mut self, path: &str) {
        self.file.path = path.to_string();
    }

        /// Restores the index from the cache form. If the number of fields does not match (a broken cache), returns the path of the file to re-read.
    pub fn into_file(self) -> Result<HyFileIndex, CachedHyFileMismatch> {
        let CachedHyFile { mut file, references, calls, definition_checks } = self;
        if references.len() != file.references.len() || calls.len() != file.calls.len() || definition_checks.len() != file.definitions.len() {
            return Err(CachedHyFileMismatch { path: file.path });
        }
        for (definition, checks) in file.definitions.iter_mut().zip(definition_checks) {
            definition.checks = checks;
        }
        for (reference, extra) in file.references.iter_mut().zip(references) {
            reference.member = extra.member;
            reference.target = extra.target;
            reference.type_only = extra.type_only;
            reference.inspected = extra.inspected;
            reference.call_arity = extra.call_arity;
        }
        for (call, extra) in file.calls.iter_mut().zip(calls) {
            call.form_range = extra.form_range;
            call.keywords = extra.keywords;
            call.arguments = extra.arguments;
        }
        Ok(file)
    }
}

/// Guard over the index types' fields (the head comment) — fields the contract's JSON carries are `_`, fields this form carries separately are received by name. Never called (only compilation works).
#[allow(dead_code)]
fn fields_are_accounted_for(file: &HyFileIndex) {
    let HyFileIndex { path: _, module: _, definitions, imports, references, calls, errors: _ } = file;
    for definition in definitions {
        let Definition {
            name: _,
            mangled: _,
            qualified_name: _,
            kind: _,
            range,
            full_range,
            container: _,
            docstring: _,
            params: _,
            bases: _,
            raw,
            tags: _,
            checks: _carried_as_definition_checks,
            effects,
            param_types,
            answer_type,
            contracts,
            handles,
            decorators: _,
        } = definition;
        ranges(&[*range, *full_range]);
        raw_mark(raw);
        effects.iter().flatten().for_each(name_ref);
        for ParamType { name: _, type_note } in param_types {
            type_note_fields(type_note);
        }
        answer_type.iter().for_each(type_note_fields);
        for ContractClause { side: _, text: _ } in contracts {}
        handles.iter().for_each(name_ref);
    }
    for Import { module: _, name: _, alias: _, range, is_require: _ } in imports {
        ranges(&[*range]);
    }
    for Reference { name: _, mangled: _, qualifier: _, range, member: _carried, target: _carried_too, type_only: _carried_type, inspected: _carried_inspected, call_arity: _carried_arity } in references {
        ranges(&[*range]);
    }
    for Call { callee: _, mangled: _, qualifier: _, range, form_range: _carried, keywords: _carried_too, arguments: _carried_arguments, caller: _, performed: _, target: _ } in calls {
        ranges(&[*range]);
    }
}

#[allow(dead_code)]
fn ranges(ranges: &[Range]) {
    for Range { start, end } in ranges {
        for Position { line: _, character: _ } in [start, end] {}
    }
}

#[allow(dead_code)]
fn raw_mark(mark: &RawMark) {
    let RawMark { direct, via } = mark;
    direct.iter().for_each(raw_evidence);
    for RawVia { through, evidence } in via {
        for RawStep { path: _, index: _, name: _ } in through {}
        raw_evidence(evidence);
    }
}

#[allow(dead_code)]
fn raw_evidence(evidence: &RawEvidence) {
    let RawEvidence { category: _, name: _, kind: _, strength: _, path: _, range } = evidence;
    ranges(&[*range]);
}

#[allow(dead_code)]
fn name_ref(name: &NameRef) {
    let NameRef { name: _, target: _ } = name;
}

#[allow(dead_code)]
fn type_note_fields(note: &TypeNote) {
    let TypeNote { text: _, names } = note;
    names.iter().for_each(name_ref);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hy_index::index_source;
    use std::path::Path;

    const SOURCE: &str = r#"(require doeff-hy.macros [defk <-])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_records.http_client [EffectTransport RecordsEndpoint])
(import pathlib [Path])

(defk read-it [path]
  {:pre [(: path str)] :post [(: % str)]}
  "to read"
  (<- text (.read-text (Path path)))
  (with-handlers [os-file-handler] (helper text))
  (RecordsEndpoint "http://r" "t" :poll-seconds 0.2 :transport (EffectTransport))
  text)

(defrecord Span {:check [(>= start 0) (<= start end)]} #^ int start #^ int end)
"#;

    #[test]
    fn a_cached_file_reads_back_the_same() {
        let file = index_source(Path::new("/r"), Path::new("/r/pkg/m.hy"), SOURCE);
                // Check with a file where the separately carried fields actually have values (if they had none, the test would guard nothing).
                assert!(file.references.iter().any(|r| r.member), "no method separator (member)");
                assert!(file.references.iter().any(|r| r.target.is_some()), "no named destination (target)");
                assert!(file.calls.iter().any(|c| c.form_range != c.range), "no call range (form_range)");
                assert!(file.calls.iter().any(|c| !c.keywords.is_empty()), "no call passing keywords (keywords)");
                assert!(file.definitions.iter().any(|d| d.checks.is_some()), "no definition with :check");
                assert!(file.definitions.iter().any(|d| d.checks.is_none()), "no definition without :check");
                // Round-trip in the binary form with no field names (bincode), the same as the linter's cache.
        let bytes = bincode::serialize(&CachedHyFile::of(file.clone())).unwrap();
        let back = bincode::deserialize::<CachedHyFile>(&bytes).unwrap().into_file().unwrap();
        assert_eq!(format!("{:?}", back), format!("{:?}", file));
    }

    #[test]
    fn a_cache_whose_extras_do_not_match_is_refused() {
        let file = index_source(Path::new("/r"), Path::new("/r/pkg/m.hy"), SOURCE);
        let mut cached = CachedHyFile::of(file);
        cached.references.pop();
        assert_eq!(cached.into_file().unwrap_err(), CachedHyFileMismatch { path: "/r/pkg/m.hy".to_string() });
    }
}

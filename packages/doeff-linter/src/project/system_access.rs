//! DOEFF207 — whether the set of external storage pairs read by a job of the production system (the defsystem named by `:system` of a defservice in architecture.hy) has a writer
//! (#3496 — a structural fix for the case where the conversation body did not appear on the new entry screen. The screen read a record that nothing in production wrote,
//! and it was green because a stand-in produced that part in the local mock).
//!
//! It applies only to repos whose architecture.hy has `:outside-writers {"<storage>:<name>" "writer" …}` (a table of pairs written by writers that are not production-system jobs — tools in external repos,
//! human operations, tasks that are not jobs). The population is the same production systems as DOEFF173 (the `:system` names and the systems
//! pointed to by `{:part-of …}` — the same system once). Any of the following is returned as a gap:
//! - A production-system job has no `:reads` or `:writes` (an optional field in doeff's defsystem — this rule requires it only of production-system jobs).
//! - A pair in a job's `:reads` is in no production job's `:writes` and not in the `:outside-writers` table (what it reads has no writer).
//!
//! The shape of field values (a set of strings, spelled `<storage>:<name>`) is checked by the expansion of doeff-hy's defsystem — here we only collect spellings and compare them.
//! When the named system is missing or is not a defsystem, DOEFF173 reports it, so it is skipped here.

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::{DefinitionKind, HyFileIndex};

use super::architecture::{Architecture, DefinitionRef, SystemDecl};
use super::system_decls::{find, items_of, symbol};

/// Kind of gap (a closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AccessGap {
    /// A production-system job lacks a field (the spelling of the field key, `:reads` / `:writes`).
    MissingField { key: &'static str },
    /// A read pair has neither a production-job writer nor a row in the outside-writers table (the pair's spelling).
    ReadWithoutWriter { access: String },
}

/// One gap of one production-system job (relative path of the defsystem file, line number of the job's row (0-based), system name, job name, gap).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccessFinding {
    pub rel: String,
    pub line: u32,
    pub system: String,
    pub job: String,
    pub gap: AccessGap,
}

impl AccessFinding {
    /// Detail of the key (system::job::field-key or system::job::pair).
    pub fn detail(&self) -> String {
        match &self.gap {
            AccessGap::MissingField { key } => format!("{}::{}::{}", self.system, self.job, key.trim_start_matches(':')),
            AccessGap::ReadWithoutWriter { access } => format!("{}::{}::{}", self.system, self.job, access),
        }
    }

    /// The body of the violation text (what is missing).
    pub fn describe(&self) -> String {
        match &self.gap {
            AccessGap::MissingField { key } => format!(
                "production system {} job {} has no {} — a production-system job writes the pairs of external storage it reads and the pairs it writes as sets of strings (use #{{}} if unused)",
                self.system, self.job, key
            ),
            AccessGap::ReadWithoutWriter { access } => format!(
                "production system {} job {} reads {} which has no writer — it is in no production job's :writes, in no :writes of a function definition reachable from a production entry point, and not in architecture.hy's :outside-writers",
                self.system, self.job, access
            ),
        }
    }
}

/// The read/write fields of one job row (None if the fields are absent).
#[derive(Debug, Clone, PartialEq, Eq)]
struct JobAccess {
    name: String,
    line: u32,
    reads: Option<BTreeSet<String>>,
    writes: Option<BTreeSet<String>>,
}

/// The contents of a set of string literals `#{"a" "b"}` (a non-set value is empty — shape checking belongs to doeff-hy's expansion).
fn strings_of(source: &str, form: &Form) -> BTreeSet<String> {
    items_of(form, Delim::Set)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| match &item.node {
                    Node::Str { body, .. } => source.get(body.start..body.end).map(str::to_string),
                    _ => None,
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Read the per-job-row fields of defsystem `system` (a job row = a name symbol, a Program call, and `:key value` pairs).
fn job_rows(source: &str, system: &str) -> Vec<JobAccess> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let Some(body) = forms.iter().find_map(|form| {
        let items = items_of(form, Delim::Paren)?;
        (items.len() >= 3 && symbol(source, &items[0]) == Some("defsystem") && symbol(source, &items[1]) == Some(system)).then_some(items)
    }) else {
        return Vec::new();
    };
    body.iter()
        .skip(3)
        .filter_map(|row| items_of(row, Delim::Paren).map(|items| (row, items)))
        .filter_map(|(row, items)| {
            let name = symbol(source, items.first()?)?.to_string();
            let line = source[..row.span.start].matches('\n').count() as u32;
            let mut access = JobAccess { name, line, reads: None, writes: None };
            let options = items.get(2..).unwrap_or(&[]);
            for pair in options.chunks(2) {
                let [key, value] = pair else { continue };
                match source.get(key.span.start..key.span.end) {
                    Some(":reads") => access.reads = Some(strings_of(source, value)),
                    Some(":writes") => access.writes = Some(strings_of(source, value)),
                    _ => {}
                }
            }
            Some(access)
        })
        .collect()
}

/// The `:writes` pairs in the contract dict of a function definition `(defk name [args…] "description"? {… :writes #{…} …} body…)` (deff is the same) (declared by writers that are not jobs —
/// such as Programs of tasks sent from a system's job — FUNCTION-CONTRACT-KEYS in doeff-hy). None if there is no definition or the dict has no :writes.
pub fn function_writes(source: &str, name: &str) -> Option<BTreeSet<String>> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let items = forms.iter().find_map(|form| {
        let items = items_of(form, Delim::Paren)?;
        (items.len() >= 3 && matches!(symbol(source, &items[0]), Some("defk" | "deff")) && symbol(source, &items[1]) == Some(name))
            .then_some(items)
    })?;
    // The first dict after the argument list, skipping the description string, is the contract (same reading as doeff-hy's _find-contract).
    let contract = items
        .get(3..)?
        .iter()
        .find(|form| !matches!(form.node, Node::Str { .. }))
        .and_then(|form| items_of(form, Delim::Brace))?;
    contract.chunks(2).find_map(|pair| match pair {
        [key, value] if source.get(key.span.start..key.span.end) == Some(":writes") => Some(strings_of(source, value)),
        _ => None,
    })
}

/// Names of production systems (in the order of architecture.hy's declarations; the same spelling once). A service with an exception declaration has no system.
fn production_systems(architecture: &Architecture) -> Vec<DefinitionRef> {
    let mut seen = BTreeSet::new();
    architecture
        .services
        .iter()
        .flat_map(|service| match service.system.as_ref() {
            Some(SystemDecl::Systems(refs)) => refs.clone(),
            Some(SystemDecl::PartOf(target)) => vec![target.clone()],
            Some(SystemDecl::Exempt(_)) | None => Vec::new(),
        })
        .filter(|system| seen.insert(system.spelling()))
        .collect()
}

/// Gaps of production-system jobs (in system order, then job-row order). Not applied (empty) to repos that do not write `:outside-writers`. `function_writers` = the pairs declared in the contract `:writes` of function definitions
/// reachable from a production entry point (writers that are not jobs — collected by a caller that has a diagram; declarations of unreachable definitions are not included).
pub fn gaps(
    root_path: &Path,
    architecture: &Architecture,
    hy: &HashMap<String, HyFileIndex>,
    function_writers: &BTreeSet<String>,
) -> Vec<AccessFinding> {
    let Some(outside) = architecture.outside_writers.as_ref() else { return Vec::new() };
    let outside: BTreeSet<&str> = outside.iter().map(|writer| writer.access.as_str()).collect();
    let jobs: Vec<(String, String, JobAccess)> = production_systems(architecture)
        .iter()
        .filter_map(|system| {
            let (rel, index, at) = find(system, hy)?;
            let definition = &index.definitions[at];
            (definition.kind == DefinitionKind::Defsystem).then_some(())?;
            let source = std::fs::read_to_string(root_path.join(rel)).ok()?;
            Some(job_rows(&source, &definition.name).into_iter().map(|job| (rel.to_string(), definition.name.clone(), job)).collect::<Vec<_>>())
        })
        .flatten()
        .collect();
    let written: BTreeSet<&str> = jobs
        .iter()
        .flat_map(|(_, _, job)| job.writes.iter().flatten().map(String::as_str))
        .chain(function_writers.iter().map(String::as_str))
        .collect();
    jobs.iter()
        .flat_map(|(rel, system, job)| {
            let finding = |gap: AccessGap| AccessFinding { rel: rel.clone(), line: job.line, system: system.clone(), job: job.name.clone(), gap };
            let missing = [(":reads", job.reads.is_none()), (":writes", job.writes.is_none())]
                .into_iter()
                .filter(|(_, absent)| *absent)
                .map(|(key, _)| finding(AccessGap::MissingField { key }));
            let unwritten = job
                .reads
                .iter()
                .flatten()
                .filter(|access| !written.contains(access.as_str()) && !outside.contains(access.as_str()))
                .map(|access| finding(AccessGap::ReadWithoutWriter { access: access.clone() }));
            missing.chain(unwritten).collect::<Vec<_>>()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn job_rows_read_the_access_fields_and_leave_absent_ones_none() {
        let source = "(defsystem s [#^ T foundation]\n  \"doc\"\n  (a (job-a foundation) :needs #{\"x\"} :reads #{\"records:intake\" \"record:turn\"} :writes #{})\n  \
                      (b (job-b foundation) :needs #{\"x\"} :replicas 1))\n";
        let rows = job_rows(source, "s");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].name, "a");
        assert_eq!(rows[0].line, 2);
        assert_eq!(rows[0].reads, Some(BTreeSet::from(["record:turn".to_string(), "records:intake".to_string()])));
        assert_eq!(rows[0].writes, Some(BTreeSet::new()));
        assert_eq!((rows[1].reads.clone(), rows[1].writes.clone()), (None, None));
        assert!(job_rows(source, "other").is_empty());
    }

    #[test]
    fn function_writes_read_the_contract_after_the_docstring() {
        let source = "(defk run-task [assignment]\n  \"A task's Program.\"\n  {:post [(: % int)] :writes #{\"record:turn\" \"records:turn-event\"}}\n  1)\n\
                      (deff plain [x] {:post [(: % int)]} x)\n(defk bare [x] x)\n";
        assert_eq!(function_writes(source, "run-task"), Some(BTreeSet::from(["record:turn".to_string(), "records:turn-event".to_string()])));
        assert_eq!(function_writes(source, "plain"), None);
        assert_eq!(function_writes(source, "bare"), None);
        assert_eq!(function_writes(source, "missing"), None);
    }
}

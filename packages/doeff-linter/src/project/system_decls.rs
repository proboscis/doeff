//! DOEFF173 — for each defservice in architecture.hy, declare the system placed in the cluster (defsystem), and check that the system is in a form that
//! can go into the generic mock-cluster test (paired with DOEFF136).
//!
//! The population is all defservices in architecture.hy (services without an entry layer are counted too — so that the operator's decision (every service has a defsystem or
//! an exception with a reason) is not silently bypassed by services lacking an entry layer). Reads the defservice
//! `:system "module:name"` (a list is also allowed) and returns any of the following as a gap:
//! - `:system` not written, or an empty list.
//! - The reason of `:system {:exempt "reason"}` is empty (an exception is for a service left on the old path or a part-service that has no process — the content of the reason is not judged).
//! - The named module is outside the service's entry layer (`<root>/<dir>/entry/`) (for a service with `:entry-modules`, inside that module).
//!   Only `:system {:part-of "module:name"}` (the service's code runs inside another service's system) may point to a system in any service's entry layer
//!   (the argument check is applied to the system it points at). A non-part-of declaration pointing at another service's system is red.
//! - The named definition does not exist or is not a defsystem (only a defsystem gets the static description `__doeff_system__` — judged by the index kind).
//! - The defsystem does not take exactly one argument.
//! - The argument type of the job function that receives the single argument (the foundation) is not written, or is a bare Callable, a mapping, or a tuple with no element types (the generic test
//!   cannot build a mock foundation from the type).
//!
//! The layer name entry is read from the layer dir name, as in the other rules (DOEFF136, 163).

use std::collections::HashMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::{Definition, DefinitionKind, HyFileIndex};

use super::architecture::{ArchService, Architecture, DefinitionRef, SystemDecl};
use super::invariants::module_path;
use super::layers::normalize_dir;

/// Type heads not accepted as a foundation type (the generic test cannot build a foundation from them).
const UNTYPED_FOUNDATION_HEADS: &[&str] =
    &["Callable", "dict", "Dict", "Mapping", "FrozenMap", "tuple", "list", "Any", "object", "JsonBody", "JsonValue", "OpaqueJson"];

/// One gap of one service (a closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SystemGap {
    /// `:system` not written, or an empty list.
    Undeclared,
    /// The reason of `{:exempt "reason"}` is empty.
    EmptyExemption,
    /// The named module is outside the entry layer.
    OutsideEntry(DefinitionRef),
    /// The named definition does not exist.
    Missing(DefinitionRef),
    /// The named definition is not a defsystem.
    NotSystem(DefinitionRef),
    /// Not exactly one argument (the list of argument names).
    NotOneFoundation { system: DefinitionRef, params: Vec<String> },
    /// The job function that receives the foundation has no argument type or a non-foundation type (job function name, type spelling — None if absent).
    UntypedFoundation { system: DefinitionRef, job: String, type_text: Option<String> },
    /// No job function that receives the foundation was found (the type cannot be checked).
    FoundationUnused { system: DefinitionRef },
}

impl SystemGap {
    /// The detail after the service name in the key (none for a missing declaration; the named spelling for a system gap).
    pub fn detail(&self) -> Option<String> {
        match self {
            SystemGap::Undeclared => None,
            SystemGap::EmptyExemption => Some("exempt".to_string()),
            SystemGap::OutsideEntry(system) | SystemGap::Missing(system) | SystemGap::NotSystem(system) => Some(system.spelling()),
            SystemGap::NotOneFoundation { system, .. } => Some(format!("{}::params", system.spelling())),
            SystemGap::FoundationUnused { system } => Some(format!("{}::unused", system.spelling())),
            SystemGap::UntypedFoundation { system, job, .. } => Some(format!("{}::{}", system.spelling(), job)),
        }
    }

    /// The body of the violation text (what is missing).
    pub fn describe(&self, service: &str) -> String {
        match self {
            SystemGap::Undeclared => format!("service {} did not declare :system (a defsystem for the system placed in the cluster)", service),
            SystemGap::EmptyExemption => format!("service {} has an empty reason in :system {{:exempt …}}", service),
            SystemGap::OutsideEntry(system) => {
                format!("service {} :system {} — the module is not in the entry layer of that service", service, system.spelling())
            }
            SystemGap::Missing(system) => {
                format!("service {} :system {} — no definition found (wrong module or name)", service, system.spelling())
            }
            SystemGap::NotSystem(system) => format!("service {} :system {} — not a defsystem", service, system.spelling()),
            SystemGap::NotOneFoundation { system, params } => format!(
                "service {} :system {} — arguments are not exactly one foundation ([{}])",
                service,
                system.spelling(),
                params.join(" ")
            ),
            SystemGap::UntypedFoundation { system, job, type_text } => format!(
                "service {} :system {} — the argument type of job function {} that receives the foundation is {}",
                service,
                system.spelling(),
                job,
                match type_text {
                    Some(text) => format!("{} (a bare Callable, mapping or tuple cannot be a foundation type)", text),
                    None => "not written".to_string(),
                }
            ),
            SystemGap::FoundationUnused { system } => {
                format!("service {} :system {} — no job function that receives the foundation was found (the type cannot be checked)", service, system.spelling())
            }
        }
    }
}

/// The named definition (relative file path, definition).
pub(super) fn find<'h>(definition: &DefinitionRef, hy: &'h HashMap<String, HyFileIndex>) -> Option<(&'h str, &'h HyFileIndex, usize)> {
    let target = definition.target();
    hy.iter().find_map(|(rel, index)| {
        index.definitions.iter().position(|d| d.qualified_name == target).map(|at| (rel.as_str(), index, at))
    })
}

/// Whether it is a module in that service's entry layer.
fn in_entry(root: &str, service: &ArchService, rel: &str) -> bool {
    match service.entry_modules.as_deref() {
        Some(modules) => modules.iter().any(|m| module_path(m) == rel),
        None => rel.starts_with(&format!("{}/{}/entry/", root, service.dir)),
    }
}

pub(super) fn symbol<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    match form.node {
        Node::Symbol => source.get(form.span.start..form.span.end),
        _ => None,
    }
}

pub(super) fn items_of(form: &Form, want: Delim) -> Option<&[Form]> {
    match &form.node {
        Node::Seq { delim, items } if *delim == want => Some(items),
        _ => None,
    }
}

/// The form with the type annotation stripped (the name of `#^ T name`).
fn bare(form: &Form) -> &Form {
    match &form.node {
        Node::Annotated { target: Some(target), .. } => bare(target),
        _ => form,
    }
}

/// The spelling of the type annotation (the T of `#^ T name`). None if there is no annotation.
fn annotation_text(source: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Annotated { annotation: Some(annotation), .. } => {
            source.get(annotation.span.start..annotation.span.end).map(str::to_string)
        }
        _ => None,
    }
}

/// The shape of one defsystem (the index holds no defsystem arguments, so it is read from the source): the argument names (for `#^ T name`, the name),
/// the type annotation of the first argument (the foundation) (`[#^ T foundation]` of doeff L983 — None if absent),
/// and the (function spelling, position) of the job calls that receive that symbol.
#[derive(Debug, Clone, PartialEq, Eq)]
struct SystemShape {
    params: Vec<String>,
    foundation_type: Option<String>,
    foundation_calls: Vec<(String, usize)>,
}

fn read_system(source: &str, system: &str) -> Option<SystemShape> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let body: &[Form] = forms.iter().find_map(|form| {
        let items = items_of(form, Delim::Paren)?;
        (items.len() >= 3 && symbol(source, &items[0]) == Some("defsystem") && symbol(source, &items[1]) == Some(system)).then_some(items)
    })?;
    let declared = items_of(&body[2], Delim::Bracket)?;
    let params: Vec<String> = declared.iter().filter_map(|p| symbol(source, bare(p))).map(str::to_string).collect();
    let foundation_type = declared.first().and_then(|p| annotation_text(source, p));
    let foundation_calls = match params.first() {
        Some(foundation) => foundation_calls(source, body, foundation),
        None => Vec::new(),
    };
    Some(SystemShape { params, foundation_type, foundation_calls })
}

/// Collects the (function spelling, position) of calls that receive the foundation symbol from the job lines of a defsystem.
fn foundation_calls(source: &str, body: &[Form], foundation: &str) -> Vec<(String, usize)> {
    body.iter()
        .skip(3)
        .filter_map(|row| items_of(row, Delim::Paren))
        .filter_map(|row| row.get(1).and_then(|program| items_of(program, Delim::Paren)))
        .filter_map(|program| {
            let head = symbol(source, program.first()?)?.to_string();
            // Count positional arguments only (skip `:key value` pairs).
            let mut positional = Vec::new();
            let mut rest = program[1..].iter();
            while let Some(arg) = rest.next() {
                if matches!(arg.node, Node::Keyword) {
                    rest.next();
                } else {
                    positional.push(arg);
                }
            }
            positional.iter().position(|arg| symbol(source, arg) == Some(foundation)).map(|at| (head, at))
        })
        .collect()
}

/// Resolves the head spelling of a call to a Hy definition via the name resolution of calls inside the defsystem (the index target).
fn callee<'h>(
    index: &HyFileIndex,
    caller: usize,
    head: &str,
    hy: &'h HashMap<String, HyFileIndex>,
) -> Option<&'h Definition> {
    let target = index.calls.iter().find(|c| c.caller == Some(caller) && c.callee == head && c.target.is_some())?.target.clone()?;
    hy.values().flat_map(|i| i.definitions.iter()).find(|d| d.qualified_name == target)
}

/// The head of a type spelling (`(of Callable …)` -> Callable, `(| A None)` -> A).
fn type_head(text: &str) -> &str {
    text.split(|c: char| c.is_whitespace() || c == '(' || c == ')' || c == '[' || c == ']')
        .find(|word| !word.is_empty() && !matches!(*word, "of" | "get" | "|"))
        .unwrap_or("")
}

/// Placement rule for systems: its own system lives in that service's own entry layer; `{:part-of …}` in any service's entry layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Placement {
    OwnEntry,
    AnyEntry,
}

/// Shape gaps of one system.
fn system_gaps(
    root_path: &Path,
    architecture: &Architecture,
    service: &ArchService,
    system: &DefinitionRef,
    placement: Placement,
    hy: &HashMap<String, HyFileIndex>,
) -> Vec<SystemGap> {
    let root = normalize_dir(&architecture.root);
    let Some((rel, index, at)) = find(system, hy) else { return vec![SystemGap::Missing(system.clone())] };
    let definition = &index.definitions[at];
    if definition.kind != DefinitionKind::Defsystem {
        return vec![SystemGap::NotSystem(system.clone())];
    }
    let mut out = Vec::new();
    let placed = match placement {
        Placement::OwnEntry => in_entry(&root, service, rel),
        Placement::AnyEntry => architecture.services.iter().any(|s| in_entry(&root, s, rel)),
    };
    if !placed {
        out.push(SystemGap::OutsideEntry(system.clone()));
    }
    let Some(shape) = std::fs::read_to_string(root_path.join(rel)).ok().and_then(|source| read_system(&source, &definition.name)) else {
        return out;
    };
    if shape.params.len() != 1 {
        out.push(SystemGap::NotOneFoundation { system: system.clone(), params: shape.params });
        return out;
    }
    // A system whose foundation type is written in the defsystem argument (`[#^ T foundation]` of doeff L983) is judged by that annotation (before the :pre of the job function).
    if let Some(text) = shape.foundation_type {
        if UNTYPED_FOUNDATION_HEADS.contains(&type_head(&text)) {
            out.push(SystemGap::UntypedFoundation { system: system.clone(), job: definition.name.clone(), type_text: Some(text) });
        }
        return out;
    }
    let calls = shape.foundation_calls;
    let judged: Vec<SystemGap> = calls
        .iter()
        .filter_map(|(head, position)| {
            let job = callee(index, at, head, hy)?;
            let param = job.params.get(*position)?;
            let type_text = job.param_types.iter().find(|t| &t.name == param).map(|t| t.type_note.text.clone());
            match type_text.as_deref() {
                Some(text) if !UNTYPED_FOUNDATION_HEADS.contains(&type_head(text)) => None,
                _ => Some(SystemGap::UntypedFoundation { system: system.clone(), job: job.name.clone(), type_text }),
            }
        })
        .collect();
    if calls.is_empty() {
        out.push(SystemGap::FoundationUnused { system: system.clone() });
    }
    out.extend(judged);
    out
}

/// Gaps per service (in the declaration order of architecture.hy; within a service, in :system order).
pub fn gaps<'a>(root_path: &Path, architecture: &'a Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<(&'a ArchService, SystemGap)> {
    architecture
        .services
        .iter()
        .flat_map(|service| {
            let found: Vec<SystemGap> = match service.system.as_ref() {
                None => vec![SystemGap::Undeclared],
                Some(SystemDecl::Systems(refs)) if refs.is_empty() => vec![SystemGap::Undeclared],
                Some(SystemDecl::Exempt(reason)) if reason.trim().is_empty() => vec![SystemGap::EmptyExemption],
                Some(SystemDecl::Exempt(_)) => Vec::new(),
                Some(SystemDecl::PartOf(target)) => system_gaps(root_path, architecture, service, target, Placement::AnyEntry, hy),
                Some(SystemDecl::Systems(refs)) => {
                    refs.iter().flat_map(|r| system_gaps(root_path, architecture, service, r, Placement::OwnEntry, hy)).collect()
                }
            };
            found.into_iter().map(move |gap| (service, gap))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_system_reads_params_and_positional_foundation_arguments() {
        let source = "(defsystem s [foundation]\n  \"doc\"\n  (a (job-a foundation) :needs #{\"x\"})\n  (b (job-b \"lit\" foundation :k foundation)))\n\
                      (defsystem two [foundation programs]\n  (c (job-c foundation programs)))\n";
        assert_eq!(
            read_system(source, "s"),
            Some(SystemShape {
                params: vec!["foundation".to_string()],
                foundation_type: None,
                foundation_calls: vec![("job-a".to_string(), 0), ("job-b".to_string(), 1)]
            })
        );
        assert_eq!(read_system(source, "two").map(|s| s.params), Some(vec!["foundation".to_string(), "programs".to_string()]));
        assert_eq!(read_system(source, "other"), None);
    }

    #[test]
    fn read_system_reads_an_annotated_foundation_by_its_name() {
        // `[#^ T foundation]` of doeff L983 — count annotated arguments by name without dropping them, and keep the annotation as the foundation type (#2215).
        let source = "(defsystem typed [#^ AgoraHost foundation]\n  (a (job-a foundation)))\n\
                      (defsystem typed-two [#^ AgoraHost foundation programs]\n  (b (job-b foundation programs)))\n";
        assert_eq!(
            read_system(source, "typed"),
            Some(SystemShape {
                params: vec!["foundation".to_string()],
                foundation_type: Some("AgoraHost".to_string()),
                foundation_calls: vec![("job-a".to_string(), 0)]
            })
        );
        assert_eq!(read_system(source, "typed-two").map(|s| s.params), Some(vec!["foundation".to_string(), "programs".to_string()]));
    }

    #[test]
    fn type_head_reads_the_outer_name() {
        assert_eq!(type_head("Callable"), "Callable");
        assert_eq!(type_head("(of Callable [str] int)"), "Callable");
        assert_eq!(type_head("(| JobFoundation None)"), "JobFoundation");
        assert_eq!(type_head("JobFoundation"), "JobFoundation");
    }
}

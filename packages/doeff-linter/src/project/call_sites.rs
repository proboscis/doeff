//! DOEFF159: spellings that fix where and how many times a head may be called (#1414 — originally a temporary check in agora-controllers,
//! controllers/screen/tests/fault_boundary_rules.hy (a)(b)(c)(f)(h)).
//!
//! architecture.hy `:call-sites [(call-site "head" :files [..] :except [..] :sites [(site "module:name" :count N :parent "head" :branch "name")]
//! :why "…")]` — for each declaration, Hy files matching the :files globs (excluding :except) are walked with the reader's tree, and `( … )` calls whose head is that spelling are collected
//! (anything inside strings, comments, or `#_` is not a call). Then:
//!   * a call that is inside none of the :sites definitions -> one hit per file (`<head>:outside`, position = the first call).
//!   * the number of calls inside a site with :count differs -> one hit per site (`<head>:count:<name>`).
//!   * among calls inside a site with :parent, those whose immediately enclosing form's head is not that spelling -> one hit per site (`<head>:parent:<name>`).
//!   * among calls inside a site with :branch, those not inside a branch of cond, when, if, or unless whose condition form contains that symbol
//!     -> one hit per site (`<head>:branch:<name>`).
//!   * a site definition is missing -> one hit at the architecture.hy position (`<head>:missing:<name>`). No file matches :files -> one hit (`<head>:empty`).
//! An empty population and a vanished definition are never green. A site definition is a top-level form of the file whose head starts with `def` and whose second element is the declared name (same as DOEFF147).
//! Only the literal leading dir of the :files glob is walked (the whole repo is not walked).

use std::collections::BTreeSet;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::Range;

use super::architecture::{CallSite, CallSiteSite};
use super::paths::{glob_matches, relative_path};
use super::top_level;
use crate::position::LineIndex;

/// Kind of hit (a closed set of 6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallSiteProblem {
    /// A call outside the sites (count within the file).
    Outside { count: usize },
    /// The number of calls inside a site differs from the declaration.
    Count { site: String, expected: usize, actual: usize },
    /// The head of the form immediately enclosing a call inside a site differs from the declaration.
    Parent { site: String, parent: String },
    /// A call inside a site is outside the declared branch.
    Branch { site: String, branch: String },
    /// The site definition is missing.
    Missing { site: String, reason: String },
    /// No file matches :files.
    Empty,
}

/// One hit.
#[derive(Debug, Clone)]
pub struct CallSiteFinding {
    pub head: String,
    pub why: String,
    /// The hit's file (path from the root) and the position within it. Missing and Empty use architecture.hy.
    pub rel: String,
    pub range: Range,
    pub problem: CallSiteProblem,
    /// Detail of the registry key.
    pub detail: String,
}

/// One call found.
struct Call {
    rel: String,
    start: usize,
    end: usize,
    /// Head of the immediately enclosing `( … )` (None if not a symbol).
    parent: Option<String>,
    /// Symbols in the condition forms of the branches containing the call (all, from the outermost branch).
    branch_symbols: BTreeSet<String>,
}

/// Location of one site.
struct Located<'s> {
    site: &'s CallSiteSite,
    rel: String,
    start: usize,
    end: usize,
    /// Position of the definition's name (the hit position for counts and so on).
    name_at: (usize, usize),
    source: String,
}

/// Dirs that are not walked (hidden dirs and generated output).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

fn symbol_text<'t>(source: &'t str, form: &Form) -> Option<&'t str> {
    matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end])
}

/// Collect all symbols in a form tree.
fn symbols_of(source: &str, form: &Form, out: &mut BTreeSet<String>) {
    match &form.node {
        Node::Symbol => {
            out.insert(source[form.span.start..form.span.end].to_string());
        }
        Node::Seq { items, .. } => items.iter().for_each(|item| symbols_of(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => symbols_of(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| symbols_of(source, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// Walk the tree and collect calls whose head is head. parent is the head of the immediately enclosing `( … )`; branch is the symbols of the enclosing branch conditions.
fn collect(source: &str, rel: &str, form: &Form, head: &str, parent: Option<&str>, branch: &BTreeSet<String>, out: &mut Vec<Call>) {
    match &form.node {
        Node::Seq { delim, items } => {
            let own = match (delim, items.first()) {
                (Delim::Paren, Some(first)) => symbol_text(source, first),
                _ => None,
            };
            if own == Some(head) {
                out.push(Call { rel: rel.to_string(), start: form.span.start, end: form.span.end, parent: parent.map(str::to_string), branch_symbols: branch.clone() });
            }
            let child_parent = if matches!(delim, Delim::Paren) { own } else { parent };
            for (index, item) in items.iter().enumerate() {
                // Branch arms: for cond, every other element from the second is an arm (the one before it is the condition); for when / if / unless, the arms follow the condition.
                let test = match own {
                    Some("cond") if index >= 2 && index % 2 == 0 => items.get(index - 1),
                    Some("when" | "if" | "unless") if index >= 2 => items.get(1),
                    _ => None,
                };
                match test {
                    Some(test) => {
                        let mut inner = branch.clone();
                        symbols_of(source, test, &mut inner);
                        collect(source, rel, item, head, child_parent, &inner, out);
                    }
                    None => collect(source, rel, item, head, child_parent, branch, out),
                }
            }
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => collect(source, rel, inner, head, parent, branch, out),
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                collect(source, rel, part, head, parent, branch, out);
            }
        }
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// The literal leading segments of the glob (up to the segment before the first one containing `*`) — where the walk starts. A pattern without `/` is the root.
fn literal_prefix(pattern: &str) -> String {
    if !pattern.contains('/') {
        return String::new();
    }
    pattern.split('/').take_while(|s| !s.contains(['*', '?', '['])).collect::<Vec<_>>().join("/")
}

/// Hy files that match :files and do not match :except (in path-from-root order).
fn files_of(root: &Path, declared: &CallSite) -> Vec<String> {
    let mut rels: BTreeSet<String> = BTreeSet::new();
    for pattern in &declared.files {
        let start = root.join(literal_prefix(pattern));
        let walker = walkdir::WalkDir::new(&start).follow_links(false).into_iter().filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        });
        rels.extend(
            walker
                .filter_map(Result::ok)
                .filter(|entry| entry.file_type().is_file() && entry.path().extension().is_some_and(|e| e == "hy"))
                .filter_map(|entry| relative_path(root, entry.path()))
                .filter(|rel| glob_matches(pattern, rel) && !declared.except.iter().any(|p| glob_matches(p, rel))),
        );
    }
    rels.into_iter().collect()
}

/// Find the site's definition (a top-level form of the file whose head starts with def and whose second element is the name).
fn locate<'s>(root: &Path, site: &'s CallSiteSite) -> Result<Located<'s>, String> {
    let rel = format!("{}.hy", site.definition.mangled_module().replace('.', "/"));
    let source = std::fs::read_to_string(root.join(&rel)).map_err(|_| format!("cannot read Hy file ({}) of module {}", rel, site.definition.module))?;
    let top = Reader::new(&source, 0, source.len()).read_all();
    let found = top_level::definition(&source, &top, &site.definition.name).and_then(|form| match form.paren_items() {
        Some([_, name, ..]) => Some((form.span.start, form.span.end, (name.span.start, name.span.end))),
        _ => None,
    });
    let (start, end, name_at) = found.ok_or_else(|| format!("{} has no definition {}", rel, site.definition.name))?;
    Ok(Located { site, rel, start, end, name_at, source })
}

/// Judge one declaration.
fn judge_one(root: &Path, declared: &CallSite, architecture_rel: &str) -> Vec<CallSiteFinding> {
    let head = declared.head.as_str();
    let finding = |rel: String, range: Range, problem: CallSiteProblem, detail: String| CallSiteFinding {
        head: head.to_string(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail: format!("{}:{}", head, detail),
    };
    let mut out = Vec::new();
    let mut located: Vec<Located> = Vec::new();
    for site in &declared.sites {
        match locate(root, site) {
            Ok(found) => located.push(found),
            Err(reason) => {
                let name = site.definition.name.clone();
                out.push(finding(architecture_rel.to_string(), site.range, CallSiteProblem::Missing { site: site.definition.spelling(), reason }, format!("missing:{}", name)));
            }
        }
    }
    let rels = files_of(root, declared);
    if rels.is_empty() {
        out.push(finding(architecture_rel.to_string(), declared.range, CallSiteProblem::Empty, "empty".to_string()));
        return out;
    }
    let mut calls: Vec<Call> = Vec::new();
    for rel in &rels {
        let Ok(source) = std::fs::read_to_string(root.join(rel)) else { continue };
        for form in Reader::new(&source, 0, source.len()).read_all() {
            collect(&source, rel, &form, head, None, &BTreeSet::new(), &mut calls);
        }
    }
    let inside = |call: &Call, place: &Located| call.rel == place.rel && place.start <= call.start && call.end <= place.end;
    // Calls outside the sites (one hit per file).
    let mut outside_by_file: Vec<(String, Vec<&Call>)> = Vec::new();
    for call in calls.iter().filter(|call| !located.iter().any(|place| inside(call, place))) {
        match outside_by_file.iter_mut().find(|(rel, _)| rel == &call.rel) {
            Some((_, list)) => list.push(call),
            None => outside_by_file.push((call.rel.clone(), vec![call])),
        }
    }
    for (rel, list) in outside_by_file {
        let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
        let range = LineIndex::new(&source).range(list[0].start, list[0].end.min(list[0].start + 1 + head.len()));
        out.push(finding(rel, range, CallSiteProblem::Outside { count: list.len() }, "outside".to_string()));
    }
    // Per-site count, enclosing form, and branch.
    for place in &located {
        let lines = LineIndex::new(&place.source);
        let name = place.site.definition.name.clone();
        let spelling = place.site.definition.spelling();
        let here: Vec<&Call> = calls.iter().filter(|call| inside(call, place)).collect();
        let at = |call: Option<&&Call>| match call {
            Some(call) => lines.range(call.start, call.end.min(call.start + 1 + head.len())),
            None => lines.range(place.name_at.0, place.name_at.1),
        };
        if let Some(expected) = place.site.count.filter(|expected| *expected != here.len()) {
            out.push(finding(
                place.rel.clone(),
                at(None),
                CallSiteProblem::Count { site: spelling.clone(), expected, actual: here.len() },
                format!("count:{}", name),
            ));
        }
        if let Some(parent) = &place.site.parent {
            let wrong: Vec<&&Call> = here.iter().filter(|call| call.parent.as_deref() != Some(parent.as_str())).collect();
            if !wrong.is_empty() {
                out.push(finding(place.rel.clone(), at(wrong.first().copied()), CallSiteProblem::Parent { site: spelling.clone(), parent: parent.clone() }, format!("parent:{}", name)));
            }
        }
        if let Some(branch) = &place.site.branch {
            let wrong: Vec<&&Call> = here.iter().filter(|call| !call.branch_symbols.contains(branch)).collect();
            if !wrong.is_empty() {
                out.push(finding(place.rel.clone(), at(wrong.first().copied()), CallSiteProblem::Branch { site: spelling.clone(), branch: branch.clone() }, format!("branch:{}", name)));
            }
        }
    }
    out
}

/// One-line sentence for a hit (the notice message and the explanation's "this is" use the same sentence).
pub fn describe(head: &str, problem: &CallSiteProblem) -> String {
    match problem {
        CallSiteProblem::Outside { count } => format!("({} …) is called {} times outside the declared sites", head, count),
        CallSiteProblem::Count { site, expected, actual } => format!("inside {}, ({} …) appears {} times (the declaration says exactly {})", site, head, actual, expected),
        CallSiteProblem::Parent { site, parent } => format!("inside {}, the form immediately enclosing ({} …) is not ({} …)", site, head, parent),
        CallSiteProblem::Branch { site, branch } => format!("inside {}, ({} …) is outside a branch whose condition has {}", site, head, branch),
        CallSiteProblem::Missing { site, reason } => format!("({} …) call site {} is missing — {}", head, site, reason),
        CallSiteProblem::Empty => format!("no Hy file matches the :files for finding ({} …)", head),
    }
}

/// Judge all declarations. architecture_rel is the path of architecture.hy from the root (the position for missing sites and empty populations).
pub fn find(root: &Path, declarations: &[CallSite], architecture_rel: &str) -> Vec<CallSiteFinding> {
    declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn calls(text: &str, head: &str) -> Vec<Call> {
        let mut out = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            collect(text, "x.hy", &form, head, None, &BTreeSet::new(), &mut out);
        }
        out
    }

    #[test]
    fn calls_carry_their_parent_head_and_branch_tests() {
        let text = "(defk f [a]\n  (cond (isinstance a Done) (x (go a)) (isinstance a Failed) (go a))\n  ;; (go a) is a comment\n  \"(go a)\" #_(go a) (when (ok a) (go a)))";
        let found = calls(text, "go");
        assert_eq!(found.len(), 3, "calls inside comments, strings, and #_ are not calls");
        assert_eq!(found[0].parent.as_deref(), Some("x"));
        assert!(found[0].branch_symbols.contains("Done"));
        assert!(!found[0].branch_symbols.contains("Failed"));
        assert!(found[1].branch_symbols.contains("Failed"));
        assert_eq!(found[1].parent.as_deref(), Some("cond"));
        assert!(found[2].branch_symbols.contains("ok"));
    }

    #[test]
    fn walking_starts_at_the_literal_head_of_the_glob() {
        assert_eq!(literal_prefix("controllers/screen/**/*.hy"), "controllers/screen");
        assert_eq!(literal_prefix("controllers/screen/entry/server.hy"), "controllers/screen/entry/server.hy");
        assert_eq!(literal_prefix("*.hy"), "");
    }
}

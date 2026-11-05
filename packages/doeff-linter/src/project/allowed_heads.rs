//! DOEFF147: definitions with a fixed set of heads that may be called (#1413 — originally a temporary check in agora-controllers,
//! controllers/screen/tests/fault_boundary_rules.hy (e)(g)).
//!
//! For each definition in architecture.hy's `:allowed-heads [(allowed-heads "module:name" :heads [..] :why "…")]`:
//!   * Read the definition's form (including nested definitions) with the reader and collect the spellings of heads of `( … )` that are symbols or keywords. Forms discarded
//!     by strings, comments, or `#_` are not read. One finding per head not in :heads (key detail = the head's spelling, position = where it first appears in the definition).
//!   * If the declared definition is not found (no Hy file for the module, or no such definition), one finding (`missing`) at architecture.hy's position.
//! Heads include not only function calls but also special forms (`when`, `setv`) and macros (`defk`, `<-`) — the linter cannot know whether one may raise an exception,
//! so the declaration decides by spelling. Elements of a `#( … )` tuple, `[ … ]`, and `{ … }` are not calls and do not count as heads (the original check's regular expression
//! also counted the first element of a tuple as a head — there are fewer hits by exactly that amount).
//! Only the file of the declared definition's module is read (not the whole repo). A definition is a top-level form in the file whose head is a symbol starting with `def`
//! (defk, defn, defhandler …) and whose second element is the declared name — no index is built (only the definition's extent is needed, and index analysis of one 2,000-line file
//! takes seconds).

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::Range;

use super::architecture::AllowedHeads;
use super::top_level;
use crate::position::LineIndex;

/// Kind of finding (a closed set of two).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HeadProblem {
    /// A head `head` outside the list exists in the definition.
    Unlisted { head: String },
    /// The declared definition is not found.
    Missing { reason: String },
}

/// One finding.
#[derive(Debug, Clone)]
pub struct HeadFinding {
    /// The declared spelling (`module:name`).
    pub declared: String,
    pub why: String,
    /// The file of the finding (path from the root) and the position within it. For Missing, it is architecture.hy.
    pub rel: String,
    pub range: Range,
    pub problem: HeadProblem,
    /// Detail of the registry key.
    pub detail: String,
}

/// Push the byte ranges of heads (symbols and keywords) of `( … )` in the form tree, in order of appearance.
fn heads_of(form: &Form, out: &mut Vec<(usize, usize)>) {
    match &form.node {
        Node::Seq { delim, items } => {
            if let (Delim::Paren, Some(first)) = (delim, items.first()) {
                if matches!(first.node, Node::Symbol | Node::Keyword) {
                    out.push((first.span.start, first.span.end));
                }
            }
            for item in items {
                heads_of(item, out);
            }
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => heads_of(inner, out),
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                heads_of(part, out);
            }
        }
        Node::Prefixed { inner: None, .. }
        | Node::Tagged { inner: None }
        | Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Discarded => {}
    }
}

/// Judge one declaration. Files that could not be read are pushed to errors.
fn judge_one(root: &Path, declared: &AllowedHeads, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<HeadFinding> {
    let spelling = declared.definition.spelling();
    let finding = |rel: String, range: Range, problem: HeadProblem, detail: String| HeadFinding {
        declared: spelling.clone(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail,
    };
    let missing = |reason: String| vec![finding(architecture_rel.to_string(), declared.range, HeadProblem::Missing { reason }, "missing".to_string())];
    let module = declared.definition.mangled_module();
    let rel = format!("{}.hy", module.replace('.', "/"));
    let path = root.join(&rel);
    let source = match std::fs::read_to_string(&path) {
        Ok(source) => source,
        Err(error) if path.exists() => {
            errors.push(format!("{}: unreadable: {}", rel, error));
            return Vec::new();
        }
        Err(_) => return missing(format!("Hy file ({}) for module {} does not exist in the repo", rel, declared.definition.module)),
    };
    let top = Reader::new(&source, 0, source.len()).read_all();
    let Some(definition) = top_level::definition(&source, &top, &declared.definition.name) else {
        return missing(format!("definition {} not found in {}", declared.definition.name, rel));
    };
    let mut spans = Vec::new();
    heads_of(definition, &mut spans);
    // The first position per head (in spelling order — this decides the order of findings).
    let mut first_at: BTreeMap<&str, (usize, usize)> = BTreeMap::new();
    for (s, e) in spans {
        first_at.entry(&source[s..e]).or_insert((s, e));
    }
    let lines = LineIndex::new(&source);
    first_at
        .into_iter()
        .filter(|(head, _)| !declared.heads.iter().any(|allowed| allowed == head))
        .map(|(head, (s, e))| finding(rel.clone(), lines.range(s, e), HeadProblem::Unlisted { head: head.to_string() }, head.to_string()))
        .collect()
}

/// Judge all declarations. architecture_rel is the path of architecture.hy from the root (the position of declarations that are not found).
pub fn find(root: &Path, declarations: &[AllowedHeads], architecture_rel: &str) -> (Vec<HeadFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found: Vec<HeadFinding> = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn heads(text: &str) -> Vec<String> {
        let mut spans = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            heads_of(&form, &mut spans);
        }
        spans.into_iter().map(|(s, e)| text[s..e].to_string()).collect()
    }

    #[test]
    fn heads_are_the_first_symbol_of_each_paren_form_outside_strings_and_comments() {
        let text = "(defk f [x] {:pre [(: x str)]} ; (decode x)  is a comment\n  \"(parse x)\" (when (.get x 1) (Send x)) #_(boom) #(tuple 1))";
        assert_eq!(heads(text), vec!["defk", ":", "when", ".get", "Send"]);
    }
}

//! DOEFF160: broad exception catches only at carrier boundaries (#1415 — originally agora-controllers' temporary check
//! (d) of controllers/screen/tests/fault_boundary_rules.hy).
//!
//! architecture.hy `:broad-catches [(broad-catch "NAME" :files [..] :except [..] :carriers [(carrier "module:NAME" :event "EVENT")] :why "…")]`
//! For each group, collects the broad catches in Hy and Python files matching the :files globs (excluding :except). A broad catch is:
//!   * Hy: `(except [] …)`, and `(except [TYPE] …)` / `(except [NAME TYPE] …)` where the type form contains a symbol from BROAD_NAMES (the last segment split on `.`).
//!     Anything inside strings, comments, or `#_` is not a catch (walked on the reader's tree).
//!   * Python: an `except:` with no type, and an `except …:` whose type is a name, attribute, or tuple of them from BROAD_NAMES (walked on the rustpython syntax tree).
//! What is allowed is only a Hy catch inside a :carriers definition (a top-level form of a Hy file) that binds the exception to a name (`(except [NAME TYPE] …)`) and
//! whose catch form contains an `(EVENT …)` call passing that name directly as an argument (a boundary that carries it into the list instead of swallowing it). Then:
//!   * A broad catch outside a carrier boundary -> 1 finding per file (`<name>:outside`, position = the first catch).
//!   * A broad catch inside a boundary that does not bind a name, or does not pass the name to the event -> 1 finding per boundary (`<name>:unbound:<definition name>`).
//!   * A boundary definition is missing -> 1 finding at the position in architecture.hy (`<name>:missing:<definition name>`).
//!   * 0 files match :files -> 1 finding (`<name>:empty`).
//! A population of 0 and vanished definitions are not turned green. For a Python file whose syntax tree cannot be read, push to errors (do not silently count 0).

use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::Range;
use rustpython_ast::{ExceptHandler, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::{BroadCatch, BroadCatchCarrier};
use super::spelling_scope::selected_files;
use super::top_level;
use crate::position::LineIndex;

/// Names of types whose catch is broad (the contract violation [AssertionError] and the types above it).
const BROAD_NAMES: &[&str] = &["Exception", "BaseException", "AssertionError"];

/// The kind of finding (a closed set of 4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BroadCatchProblem {
    /// A broad catch outside a carrier boundary (the count within the file).
    Outside { count: usize },
    /// A broad catch inside a boundary that does not bind the exception to a name and pass it to the event.
    Unbound { carrier: String, event: String },
    /// The boundary definition is missing.
    Missing { carrier: String, reason: String },
    /// 0 files match :files.
    Empty,
}

/// One finding.
#[derive(Debug, Clone)]
pub struct BroadCatchFinding {
    /// The name of the group.
    pub group: String,
    pub why: String,
    /// The file of the finding (path from the root) and the position within it. Missing and Empty are architecture.hy.
    pub rel: String,
    pub range: Range,
    pub problem: BroadCatchProblem,
    /// The detail of the registry key.
    pub detail: String,
}

/// One broad catch found (a byte range).
struct Catch {
    rel: String,
    start: usize,
    end: usize,
    /// The name the Hy catch bound the exception to (None for Python and for catches with no binding).
    bound: Option<String>,
}

/// The location of one boundary.
struct Located<'c> {
    carrier: &'c BroadCatchCarrier,
    rel: String,
    start: usize,
    end: usize,
}

fn symbol_text<'t>(source: &'t str, form: &Form) -> Option<&'t str> {
    matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end])
}

fn is_broad_name(name: &str) -> bool {
    BROAD_NAMES.contains(&name.rsplit('.').next().unwrap_or(name))
}

/// Whether a broad type symbol is in the form tree.
fn names_broad(source: &str, form: &Form) -> bool {
    match &form.node {
        Node::Symbol => is_broad_name(&source[form.span.start..form.span.end]),
        Node::Seq { items, .. } => items.iter().any(|item| names_broad(source, item)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => names_broad(source, inner),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().any(|part| names_broad(source, part)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => false,
    }
}

/// If the form is an `(except [..] …)` form, whether it is broad and the bound name. None for a non-broad catch and for a form that is not a catch.
fn broad_hy_catch(source: &str, form: &Form) -> Option<Option<String>> {
    let items = form.paren_items()?;
    let [head, spec, ..] = items else { return None };
    if symbol_text(source, head) != Some("except") {
        return None;
    }
    let Node::Seq { delim: Delim::Bracket, items: spec } = &spec.node else { return None };
    match spec.as_slice() {
        [] => Some(None),
        [types] => names_broad(source, types).then_some(None),
        [name, types, ..] => names_broad(source, types).then(|| symbol_text(source, name).map(str::to_string)),
    }
}

/// Walks the Hy tree and collects broad catches.
fn collect_hy(source: &str, rel: &str, form: &Form, out: &mut Vec<Catch>) {
    match &form.node {
        Node::Seq { items, .. } => {
            if let Some(bound) = broad_hy_catch(source, form) {
                out.push(Catch { rel: rel.to_string(), start: form.span.start, end: form.span.end, bound });
            }
            items.iter().for_each(|item| collect_hy(source, rel, item, out));
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => collect_hy(source, rel, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| collect_hy(source, rel, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// Whether the type expression is broad (a name, attribute, or tuple of them).
fn expr_is_broad(expr: &Expr) -> bool {
    // For expressions that can appear in the type position, look only at names, attributes, and tuples (a catch that builds the type with a call or subscript has a breadth that cannot be decided statically, so it is not counted).
    if let Expr::Name(name) = expr {
        is_broad_name(name.id.as_str())
    } else if let Expr::Attribute(attribute) = expr {
        is_broad_name(attribute.attr.as_str())
    } else if let Expr::Tuple(tuple) = expr {
        tuple.elts.iter().any(expr_is_broad)
    } else {
        false
    }
}

fn python_handlers(rel: &str, handlers: &[ExceptHandler], out: &mut Vec<Catch>) {
    for handler in handlers {
        let ExceptHandler::ExceptHandler(h) = handler;
        if h.type_.as_deref().is_none_or(expr_is_broad) {
            let start = h.range.start().to_usize();
            out.push(Catch { rel: rel.to_string(), start, end: start + "except".len(), bound: None });
        }
        collect_python(rel, &h.body, out);
    }
}

/// Walks a list of Python statements and collects broad catches (there is no catch inside an expression, so only nested statements are walked).
fn collect_python(rel: &str, body: &[Stmt], out: &mut Vec<Catch>) {
    for stmt in body {
        match stmt {
            Stmt::FunctionDef(node) => collect_python(rel, &node.body, out),
            Stmt::AsyncFunctionDef(node) => collect_python(rel, &node.body, out),
            Stmt::ClassDef(node) => collect_python(rel, &node.body, out),
            Stmt::For(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::AsyncFor(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::While(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::If(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::With(node) => collect_python(rel, &node.body, out),
            Stmt::AsyncWith(node) => collect_python(rel, &node.body, out),
            Stmt::Match(node) => node.cases.iter().for_each(|case| collect_python(rel, &case.body, out)),
            Stmt::Try(node) => {
                collect_python(rel, &node.body, out);
                python_handlers(rel, &node.handlers, out);
                collect_python(rel, &node.orelse, out);
                collect_python(rel, &node.finalbody, out);
            }
            Stmt::TryStar(node) => {
                collect_python(rel, &node.body, out);
                python_handlers(rel, &node.handlers, out);
                collect_python(rel, &node.orelse, out);
                collect_python(rel, &node.finalbody, out);
            }
            Stmt::Return(_)
            | Stmt::Delete(_)
            | Stmt::Assign(_)
            | Stmt::TypeAlias(_)
            | Stmt::AugAssign(_)
            | Stmt::AnnAssign(_)
            | Stmt::Raise(_)
            | Stmt::Assert(_)
            | Stmt::Import(_)
            | Stmt::ImportFrom(_)
            | Stmt::Global(_)
            | Stmt::Nonlocal(_)
            | Stmt::Expr(_)
            | Stmt::Pass(_)
            | Stmt::Break(_)
            | Stmt::Continue(_) => {}
        }
    }
}

/// Whether the form tree contains a call whose head is event and that has the name bound directly as an argument.
fn passes_to(source: &str, form: &Form, event: &str, bound: &str) -> bool {
    match &form.node {
        Node::Seq { delim, items } => {
            let here = matches!(delim, Delim::Paren)
                && items.first().and_then(|head| symbol_text(source, head)) == Some(event)
                && items.iter().skip(1).any(|item| symbol_text(source, item) == Some(bound));
            here || items.iter().any(|item| passes_to(source, item, event, bound))
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => passes_to(source, inner, event, bound),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().any(|part| passes_to(source, part, event, bound)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => false,
    }
}

/// Whether the catch inside a boundary passes the bound name to the event (re-reads the catch form to check).
fn carries(source: &str, catch: &Catch, event: &str) -> bool {
    let Some(bound) = &catch.bound else { return false };
    Reader::new(source, catch.start, catch.end).read_all().iter().any(|form| passes_to(source, form, event, bound))
}

/// Looks up the boundary definition (a top-level definition of a Hy file).
fn locate<'c>(root: &Path, carrier: &'c BroadCatchCarrier) -> Result<Located<'c>, String> {
    let rel = format!("{}.hy", carrier.definition.mangled_module().replace('.', "/"));
    let source = std::fs::read_to_string(root.join(&rel)).map_err(|_| format!("cannot read the Hy file ({}) of module {}", rel, carrier.definition.module))?;
    let top = Reader::new(&source, 0, source.len()).read_all();
    let form = top_level::definition(&source, &top, &carrier.definition.name).ok_or_else(|| format!("definition {} is missing in {}", carrier.definition.name, rel))?;
    Ok(Located { carrier, rel, start: form.span.start, end: form.span.end })
}

/// Judges one group. Files that could not be read are pushed to errors.
fn judge_one(root: &Path, declared: &BroadCatch, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<BroadCatchFinding> {
    let finding = |rel: String, range: Range, problem: BroadCatchProblem, detail: String| BroadCatchFinding {
        group: declared.name.clone(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail: format!("{}:{}", declared.name, detail),
    };
    let mut out = Vec::new();
    let mut located: Vec<Located> = Vec::new();
    for carrier in &declared.carriers {
        match locate(root, carrier) {
            Ok(found) => located.push(found),
            Err(reason) => out.push(finding(
                architecture_rel.to_string(),
                carrier.range,
                BroadCatchProblem::Missing { carrier: carrier.definition.spelling(), reason },
                format!("missing:{}", carrier.definition.name),
            )),
        }
    }
    let rels = selected_files(root, &declared.files, &declared.except);
    if rels.is_empty() {
        out.push(finding(architecture_rel.to_string(), declared.range, BroadCatchProblem::Empty, "empty".to_string()));
        return out;
    }
    for rel in &rels {
        let source = match std::fs::read_to_string(root.join(rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: cannot read: {}", rel, error));
                continue;
            }
        };
        let mut catches: Vec<Catch> = Vec::new();
        if rel.ends_with(".hy") {
            for form in Reader::new(&source, 0, source.len()).read_all() {
                collect_hy(&source, rel, &form, &mut catches);
            }
        } else {
            match parse(&source, Mode::Module, rel) {
                Ok(Mod::Module(module)) => collect_python(rel, &module.body, &mut catches),
                Ok(Mod::Interactive(_) | Mod::Expression(_) | Mod::FunctionType(_)) => {}
                Err(error) => {
                    errors.push(format!("{}: cannot read the Python syntax tree (broad catches cannot be counted): {}", rel, error));
                    continue;
                }
            }
        }
        let lines = LineIndex::new(&source);
        let within = |catch: &Catch| located.iter().find(|place| place.rel == catch.rel && place.start <= catch.start && catch.end <= place.end);
        let outside: Vec<&Catch> = catches.iter().filter(|catch| within(catch).is_none()).collect();
        if let Some(first) = outside.first() {
            out.push(finding(rel.clone(), lines.range(first.start, first.start + 1), BroadCatchProblem::Outside { count: outside.len() }, "outside".to_string()));
        }
        // For each boundary, a catch that does not pass the name to the event (1 finding per boundary, position = the first one).
        for place in &located {
            let event = &place.carrier.event;
            if let Some(first) = catches.iter().filter(|catch| within(catch).is_some_and(|p| std::ptr::eq(p, place))).find(|catch| !carries(&source, catch, event)) {
                out.push(finding(
                    rel.clone(),
                    lines.range(first.start, first.start + 1),
                    BroadCatchProblem::Unbound { carrier: place.carrier.definition.spelling(), event: event.clone() },
                    format!("unbound:{}", place.carrier.definition.name),
                ));
            }
        }
    }
    out
}

/// The one-line sentence of a finding (the notice's message and the explanation's "this is" use the same sentence).
pub fn describe(group: &str, problem: &BroadCatchProblem) -> String {
    match problem {
        BroadCatchProblem::Outside { count } => format!("{} broad exception catch(es) are outside a carrier boundary (group {})", count, group),
        BroadCatchProblem::Unbound { carrier, event } => {
            format!("the broad catch of carrier boundary {} does not bind the exception to a name and pass it to ({} …) (group {})", carrier, event, group)
        }
        BroadCatchProblem::Missing { carrier, reason } => format!("carrier boundary {} of group {} is missing — {}", carrier, group, reason),
        BroadCatchProblem::Empty => format!("no Hy or Python files match :files of group {}", group),
    }
}

/// Judges all groups. architecture_rel is the path of architecture.hy from the root (the position of missing boundaries and an empty population).
pub fn find(root: &Path, declarations: &[BroadCatch], architecture_rel: &str) -> (Vec<BroadCatchFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hy_catches(text: &str) -> Vec<Catch> {
        let mut out = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            collect_hy(text, "x.hy", &form, &mut out);
        }
        out
    }

    fn py_catches(text: &str) -> Vec<Catch> {
        let mut out = Vec::new();
        if let Ok(Mod::Module(module)) = parse(text, Mode::Module, "x.py") {
            collect_python("x.py", &module.body, &mut out);
        }
        out
    }

    #[test]
    fn broad_hy_catches_are_found_outside_strings_and_comments() {
        let text = "(try (a) (except [] 1) (except [ValueError] 2) (except [e Exception] 3) (except [e [KeyError builtins.BaseException]] 4)\n\
                    (except [AssertionError] 5))\n;; (except [Exception] x)\n\"(except [] y)\" #_(except [] z)";
        let found = hy_catches(text);
        assert_eq!(found.len(), 4, "catches that are narrow, in comments, in strings, or inside #_ are not counted");
        assert_eq!(found[0].bound, None);
        assert_eq!(found[1].bound.as_deref(), Some("e"));
        assert_eq!(found[3].bound, None);
    }

    #[test]
    fn broad_python_catches_are_found_in_nested_statements() {
        let text = "def f():\n    try:\n        a()\n    except ValueError:\n        pass\n    except:\n        pass\n\nclass C:\n    def g(self):\n        \
                    for x in y:\n            try:\n                b()\n            except (KeyError, Exception) as error:\n                raise\n\n\
                    # except Exception:\ntry:\n    c()\nexcept builtins.AssertionError:\n    pass\n";
        assert_eq!(py_catches(text).len(), 3);
    }

    #[test]
    fn a_carrier_passes_the_bound_name_to_its_event() {
        let good = "(except [error Exception] (<- (PutChannel inbox (Queued :work w :failure error))))";
        let swallow = "(except [error Exception] (<- (PutChannel inbox (Queued :work w :failure None))))";
        let unbound = "(except [Exception] (<- (PutChannel inbox (Queued :work w))))";
        for (text, expected) in [(good, true), (swallow, false), (unbound, false)] {
            let found = hy_catches(text);
            assert_eq!(found.len(), 1);
            assert_eq!(carries(text, &found[0], "Queued"), expected, "{}", text);
        }
    }
}

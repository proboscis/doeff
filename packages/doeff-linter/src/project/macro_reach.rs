//! Whether a defn in a module exempted from DOEFF110 (defn) by a layer declaration is reachable from any defmacro in the package.
//!
//! The reason for exempting via `:exempt [(rule "DOEFF110" "reason")]` is that the function "is called as a Python function at macro-expansion time (or by macro-generated code)
//! and cannot return a Program". That claim is checked by name reachability: take as roots the names appearing in all defmacro forms of the Hy files in the package (the dir of the declaration file),
//! then follow the names appearing in the bodies of top-level function definitions (defn, defn/a, deff, defk, defp, defpp, `(setv name (fn …))`) to build the set of
//! reachable names. A defn in an exempted module that no defmacro reaches is a runtime-time definition, so it is not exempted and DOEFF110 fires
//! (the guard for decision 2 of #2877 — it fires if a runtime-time definition gets into an exempted module).
//!
//! - Names inside quasiquote in a defmacro form are counted too — both the expansion-time calls and the parts that the expanded code calls as Python functions (such as defk's guard)
//!   are "functions owned by the macro".
//! - Names are compared by spelling only (modules are not resolved). A function of the same name in another module counts as reached — lenient only in the direction of missing a violation.
//!   A name of the form `a.b.c` is also counted per segment (`(doeff_hy.declarations.effect-types …)` is picked up as `effect-types`).
//! - defmacro in files of a test dir (a path with a `tests` segment) is not counted as a root (functions called by a test macro are treated the same as runtime-time ones).
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use doeff_indexer::hy_index::reader::{Form, Node, Reader};

use super::names::hy_mangle;

/// Head of a macro definition (same as MACROS in defn_to_defk).
const MACRO_HEADS: &[&str] = &["defmacro", "defmacro/g!", "defreader"];
/// Heads of function definitions whose names are followed.
const FUNCTION_HEADS: &[&str] = &["defn", "defn/a", "deff", "defk", "defp", "defpp"];

/// Reading of one source: names appearing in defmacro forms, and function definition name -> names appearing in its body.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct SourceReach {
    pub roots: BTreeSet<String>,
    pub bodies: BTreeMap<String, BTreeSet<String>>,
}

/// Read one source (the top level and the insides of `do`, `eval-and-compile`, `eval-when-compile` are seen as top level).
pub fn read_source(src: &str) -> SourceReach {
    let forms = Reader::new(src, 0, src.len()).read_all();
    let mut reach = SourceReach::default();
    for form in &forms {
        top_level(src, form, &mut reach);
    }
    reach
}

fn top_level(src: &str, form: &Form, reach: &mut SourceReach) {
    let Some(items) = form.paren_items() else { return };
    let Some(head) = items.first().and_then(|h| symbol(src, h)) else { return };
    match head {
        "do" | "eval-and-compile" | "eval-when-compile" => {
            for inner in &items[1..] {
                top_level(src, inner, reach);
            }
        }
        _ if MACRO_HEADS.contains(&head) => reach.roots.extend(names_in(src, form)),
        _ if FUNCTION_HEADS.contains(&head) && items.len() >= 3 => {
            // `(defn [decorators] name …)` skips the decorator list and reads the name.
            let name_form = if items[1].bracket_items().is_some() { &items[2] } else { &items[1] };
            if let Some(name) = defined_name(src, name_form) {
                reach.bodies.entry(name).or_default().extend(names_in(src, form));
            }
        }
        "setv" | "val" if items.len() == 3 => {
            let is_fn = items[2].paren_items().and_then(|inner| inner.first()).and_then(|h| symbol(src, h)).is_some_and(|h| matches!(h, "fn" | "fn/a"));
            if let (true, Some(name)) = (is_fn, symbol(src, &items[1])) {
                reach.bodies.entry(hy_mangle(name)).or_default().extend(names_in(src, &items[2]));
            }
        }
        _ => {}
    }
}

/// The name of a definition (drops the `#^ T name` annotation).
fn defined_name(src: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Symbol => Some(hy_mangle(text(src, form))),
        Node::Annotated { target: Some(target), .. } => defined_name(src, target),
        _ => None,
    }
}

/// All names appearing inside a form (mangled; `a.b.c` is taken both whole and per segment).
fn names_in(src: &str, form: &Form) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    walk(src, form, &mut names);
    names
}

fn walk(src: &str, form: &Form, names: &mut BTreeSet<String>) {
    match &form.node {
        Node::Symbol => {
            let spelled = text(src, form);
            names.insert(hy_mangle(spelled));
            if spelled.contains('.') {
                names.extend(spelled.split('.').filter(|part| !part.is_empty()).map(hy_mangle));
            }
        }
        Node::Seq { items, .. } => items.iter().for_each(|item| walk(src, item, names)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => walk(src, inner, names),
        Node::Annotated { annotation, target } => {
            annotation.iter().chain(target.iter()).for_each(|part| walk(src, part, names));
        }
        _ => {}
    }
}

fn symbol<'a>(src: &'a str, form: &Form) -> Option<&'a str> {
    matches!(form.node, Node::Symbol).then(|| text(src, form))
}

fn text<'a>(src: &'a str, form: &Form) -> &'a str {
    &src[form.span.start..form.span.end]
}

/// All names reachable by following function bodies from the roots (names appearing in defmacro).
pub fn reached(sources: &[SourceReach]) -> BTreeSet<String> {
    // Call edges (definition name, names appearing in its body) — if definitions of the same name are in two files, both edges are kept.
    let edges: BTreeSet<(&str, &str)> = sources
        .iter()
        .flat_map(|source| source.bodies.iter())
        .flat_map(|(name, body)| body.iter().map(move |callee| (name.as_str(), callee.as_str())))
        .collect();
    let roots: BTreeSet<String> = sources.iter().flat_map(|source| source.roots.iter().cloned()).collect();
    let mut seen = roots.clone();
    let mut frontier: Vec<String> = roots.into_iter().collect();
    while let Some(name) = frontier.pop() {
        let next: Vec<String> = edges
            .range((name.as_str(), "")..)
            .take_while(|(caller, _)| *caller == name.as_str())
            .map(|(_, callee)| *callee)
            .filter(|callee| !seen.contains(*callee))
            .map(str::to_string)
            .collect();
        seen.extend(next.iter().cloned());
        frontier.extend(next);
    }
    seen
}

/// Read the Hy files of the declaration file's dir (package) and return the names reachable from defmacro (the same package is read only once per run).
/// Files in a test dir (`tests` segment) are not counted. Unreadable files are skipped (fails only toward "not reached" = the firing direction).
pub fn reached_in_package(package: &Path) -> Arc<BTreeSet<String>> {
    static MEMO: OnceLock<Mutex<HashMap<PathBuf, Arc<BTreeSet<String>>>>> = OnceLock::new();
    let memo = MEMO.get_or_init(Default::default);
    if let Some(found) = memo.lock().expect("reach memo").get(package) {
        return Arc::clone(found);
    }
    let sources: Vec<SourceReach> = super::hy_files::collect(package)
        .into_iter()
        .filter(|path| !path.strip_prefix(package).unwrap_or(path).components().any(|part| part.as_os_str() == "tests"))
        .filter_map(|path| std::fs::read_to_string(&path).ok())
        .map(|src| read_source(&src))
        .collect();
    let found = Arc::new(reached(&sources));
    memo.lock().expect("reach memo").insert(package.to_path_buf(), Arc::clone(&found));
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_defn_named_in_a_macro_and_its_callees_are_reached_and_others_are_not() {
        let macros = read_source(
            "(defn _expand [form] (_spell form))\n\
             (defn _spell [form] form)\n\
             (defmacro m [form] (_expand form))\n\
             (defmacro g [] `(do (doeff_hy.guards._guard 1)))\n",
        );
        let runtime = read_source("(defn thaw [x] x)\n(defn _guard [x] x)\n");
        let found = reached(&[macros, runtime]);
        // Expansion-time call (_expand), what its body calls (_spell), and the call from expanded code (_guard, the segment inside quasiquote) are reached.
        for name in ["_expand", "_spell", "_guard"] {
            assert!(found.contains(name), "{name} is not reached: {found:?}");
        }
        // Failure case: a runtime-time function that no macro reaches is not reached.
        assert!(!found.contains("thaw"), "{found:?}");
    }

    #[test]
    fn annotated_names_decorators_and_compile_time_blocks_are_read_as_definitions() {
        let source = read_source(
            "(eval-and-compile (defn #^ str _quoted [x] (_inner x)))\n\
             (defn [staticmethod] _decorated [x] x)\n\
             (defn _inner [x] x)\n\
             (defmacro q [x] (_quoted x))\n",
        );
        assert!(source.bodies.contains_key("_quoted"), "{source:?}");
        assert!(source.bodies.contains_key("_decorated"), "{source:?}");
        let found = reached(&[source]);
        assert!(found.contains("_inner"), "{found:?}");
        assert!(!found.contains("_decorated"), "{found:?}");
    }
}

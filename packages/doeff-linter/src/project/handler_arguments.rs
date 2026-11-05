//! DOEFF142: a handler does not take connection objects or a rewritable store as arguments (#1366 — originally the
//! temporary check handler_arguments_rules.hy in agora-controllers, #686).
//!
//! Splits the defhandler argument list `[ … ]` into individual arguments:
//!   * `client`    — the type name ends with Client / Connection / Pool / Engine, or the name is client / conn / connection / …-client / …-conn
//!   * `container` — the type is a mutable container (dict / list / set / bytearray / MutableMapping … — including `(get dict …)`)
//!   * `object`    — the type is a mutable object: a class in the repo's class index that is not frozen (frozen dataclass, frozen config, Enum,
//!                   NamedTuple and defrecord are values), or a type missing from the index (a resource object)
//!   * `named`     — there is no type annotation and the name points to a store (`:store-names` / `:store-suffixes` in architecture.hy)
//! Value arguments (str / int / … / Path / tuple / frozenset / Callable and frozen classes) are not counted.
//!
//! The verdict depends only on the file passed in. The exception is `object`'s "is the class frozen": the index of the repo's Hy and Python classes is looked up through a per-file
//! cache (facts_cache) — even a single-file run re-reads only the changed files. The population, the store names and the marker for the keep-reason comment are read from the repo declaration
//! (`:handler-arguments`); only generic Python names are placed here.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};
use rustpython_ast::{Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use super::architecture::HandlerArguments;
use super::blob_chunks::{ChunkedFacts, Lookup};
use super::hy_files::{self, WalkRules};
use super::facts::ByteSpan;
use super::paths::glob_matches;

/// Kind of argument (a closed set).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    Client,
    Container,
    Object,
    Named,
}

impl ArgKind {
    pub fn word(self) -> &'static str {
        match self {
            ArgKind::Client => "client",
            ArgKind::Container => "container",
            ArgKind::Object => "object",
            ArgKind::Named => "named",
        }
    }
}

/// One finding (which argument of which handler).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgFinding {
    pub handler: String,
    pub param: String,
    pub kind: ArgKind,
    /// Spelling of the type annotation (empty if none).
    pub type_text: String,
    pub span: ByteSpan,
}

impl ArgFinding {
    /// Detail of the registry key (`<handler>::<argument>`).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.handler, self.param)
    }
}

/// Class name and whether it is frozen (kept in the per-file cache).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClassFrozen {
    pub name: String,
    pub frozen: bool,
}

/// Class name → frozen? (a name is not frozen if even one non-frozen class has the same name).
pub type ClassIndex = HashMap<String, bool>;

/// Something that can look up whether a class name is a value (frozen) — the repo-wide index (`ClassIndex`), or an index built on demand (`Deferred<ClassIndex>`, single-file run).
/// A name missing from the index is not a value.
pub trait ClassLookup {
    fn is_value(&self, name: &str) -> bool;
}

impl ClassLookup for ClassIndex {
    fn is_value(&self, name: &str) -> bool {
        self.get(name).copied().unwrap_or(false)
    }
}

impl ClassLookup for super::deferred::Deferred<'_, ClassIndex> {
    fn is_value(&self, name: &str) -> bool {
        self.get().is_value(name)
    }
}

const CLIENT_TYPE_SUFFIXES: &[&str] = &["Client", "Connection", "Pool", "Engine"];
const CLIENT_NAMES: &[&str] = &["client", "conn", "connection"];
const CLIENT_NAME_SUFFIXES: &[&str] = &["-client", "-conn", "_client", "_conn"];
const MUTABLE_CONTAINERS: &[&str] = &[
    "dict", "list", "set", "bytearray", "Dict", "List", "Set", "MutableMapping", "MutableSequence", "MutableSet", "typing.Dict", "typing.List",
    "typing.Set", "collections.abc.MutableMapping",
];
const VALUE_TYPES: &[&str] = &[
    "str", "int", "float", "bool", "bytes", "Path", "pathlib.Path", "tuple", "Tuple", "frozenset", "FrozenSet", "Callable", "typing.Callable",
    "collections.abc.Callable", "None", "Decimal", "object", "Any",
];
const SKIP_ATOMS: &[&str] = &["&optional", "&rest", "&kwonly", "&kwargs", "*", "/"];
const VALUE_BASES: &[&str] = &["Enum", "StrEnum", "IntEnum", "NamedTuple", "enum.Enum"];
/// Directories not to walk (hidden directories and generated output).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// One argument (name and type annotation).
struct Param<'a> {
    name: String,
    annotation: Option<&'a Form>,
    span: ByteSpan,
}

fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    &source[form.span.start..form.span.end]
}

fn span_of(form: &Form) -> ByteSpan {
    ByteSpan { start: form.span.start, end: form.span.end }
}

fn head_symbol<'s>(source: &'s str, items: &[Form]) -> Option<&'s str> {
    items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| text(source, f))
}

fn read_forms(source: &str) -> Vec<Form> {
    Reader::new(source, 0, source.len()).read_all()
}

/// Type annotation → list of names to inspect: a name as is; `(get T …)` is T; `(| A B)` per branch; a string type is its branches split by `|`.
fn type_bases(source: &str, form: &Form) -> Vec<String> {
    match &form.node {
        Node::Symbol => vec![text(source, form).to_string()],
        Node::Str { body, .. } => source[body.start..body.end]
            .split('|')
            .map(|part| part.trim().split('[').next().unwrap_or("").trim().to_string())
            .filter(|b| !b.is_empty())
            .collect(),
        Node::Seq { delim: Delim::Paren, items } => match head_symbol(source, items) {
            Some("|") => items[1..].iter().flat_map(|branch| type_bases(source, branch)).collect(),
            Some("get") => items.get(1).map(|base| type_bases(source, base)).unwrap_or_default(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}

/// Argument list `[ … ]` → list of arguments (reads `#^` annotations, `[name default]`, `(annotate name type)` and `#*` / `#**` variadics).
fn params_of<'a>(source: &str, items: &'a [Form]) -> Vec<Param<'a>> {
    let mut out = Vec::new();
    for item in items {
        match &item.node {
            Node::Symbol => {
                let name = text(source, item);
                if !SKIP_ATOMS.contains(&name) {
                    out.push(Param { name: name.to_string(), annotation: None, span: span_of(item) });
                }
            }
            Node::Annotated { annotation, target: Some(target) } => {
                out.push(Param { name: text(source, target).to_string(), annotation: annotation.as_deref(), span: span_of(item) });
            }
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, inner: Some(inner) } => {
                out.extend(params_of(source, std::slice::from_ref(inner.as_ref())));
            }
            Node::Seq { delim: Delim::Paren, items: inner } if head_symbol(source, inner) == Some("annotate") && inner.len() >= 3 => {
                out.push(Param { name: text(source, &inner[1]).to_string(), annotation: Some(&inner[2]), span: span_of(item) });
            }
            Node::Seq { delim: Delim::Bracket, items: inner } => {
                if let Some(first) = inner.first() {
                    out.extend(params_of(source, std::slice::from_ref(first)).into_iter().map(|p| Param { span: span_of(item), ..p }));
                }
            }
            _ => {}
        }
    }
    out
}

/// One argument → kind (None for a value argument).
fn kind_of(source: &str, param: &Param, decl: &HandlerArguments, classes: &dyn ClassLookup) -> Option<ArgKind> {
    let name = param.name.as_str();
    let bases: Vec<String> = param.annotation.map(|a| type_bases(source, a)).unwrap_or_default();
    let tails: Vec<&str> = bases.iter().map(|b| b.rsplit('.').next().unwrap_or(b)).collect();
    let client_name = CLIENT_NAMES.contains(&name) || CLIENT_NAME_SUFFIXES.iter().any(|s| name.ends_with(s));
    let client_type = tails.iter().any(|t| CLIENT_TYPE_SUFFIXES.iter().any(|s| t.ends_with(s)));
    if client_name || client_type {
        return Some(ArgKind::Client);
    }
    if bases.iter().any(|b| MUTABLE_CONTAINERS.contains(&b.as_str())) {
        return Some(ArgKind::Container);
    }
    if param.annotation.is_none() {
        let store = decl.store_names.iter().any(|s| s == name) || decl.store_suffixes.iter().any(|s| name.ends_with(s.as_str()));
        return store.then_some(ArgKind::Named);
    }
    let is_value = |b: &str| VALUE_TYPES.contains(&b) || decl.value_types.iter().any(|v| v == b) || classes.is_value(b);
    let mutable = bases.iter().zip(&tails).any(|(b, t)| !(is_value(b) || is_value(t)));
    mutable.then_some(ArgKind::Object)
}

/// All `(defhandler name [args] …)` in the form tree (including nested ones).
fn handler_forms<'a>(source: &str, forms: &'a [Form], out: &mut Vec<&'a Form>) {
    for form in forms {
        let items = match &form.node {
            Node::Seq { items, .. } => items,
            Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => {
                handler_forms(source, std::slice::from_ref(inner.as_ref()), out);
                continue;
            }
            _ => continue,
        };
        if matches!(form.node, Node::Seq { delim: Delim::Paren, .. }) && items.len() >= 2 && head_symbol(source, items) == Some("defhandler") {
            out.push(form);
        }
        handler_forms(source, items, out);
    }
}

/// Findings of one file. A handler whose body (from `(defhandler` to the next top-level form — including trailing comments) has the keep-reason marker is not counted.
pub fn findings_in(source: &str, decl: &HandlerArguments, classes: &dyn ClassLookup) -> Vec<ArgFinding> {
    let forms = read_forms(source);
    let tops: Vec<usize> = forms.iter().map(|f| f.span.start).collect();
    let mut handlers = Vec::new();
    handler_forms(source, &forms, &mut handlers);
    let mut out = Vec::new();
    for form in handlers {
        let Node::Seq { items, .. } = &form.node else { continue };
        let name = text(source, &items[1]).to_string();
        let block_end = tops.iter().copied().find(|&start| start > form.span.start).unwrap_or(source.len()).max(form.span.end);
        if decl.keep_mark.as_deref().is_some_and(|mark| source[form.span.start..block_end].contains(mark)) {
            continue;
        }
        let Some(vector) = items.get(2).and_then(Form::bracket_items) else { continue };
        for param in params_of(source, vector) {
            if let Some(kind) = kind_of(source, &param, decl, classes) {
                let type_text = param.annotation.map(|a| text(source, a).split_whitespace().collect::<Vec<_>>().join(" ")).unwrap_or_default();
                out.push(ArgFinding { handler: name.clone(), param: param.name, kind, type_text, span: param.span });
            }
        }
    }
    out
}

/// Whether the file's path falls in the population (matches some of :files and none of :exclude).
pub fn in_population(rel: &str, decl: &HandlerArguments) -> bool {
    rel.ends_with(".hy") && decl.files.iter().any(|p| glob_matches(p, rel)) && !decl.exclude.iter().any(|p| glob_matches(p, rel))
}

/// Select files under root by extension (not walking hidden directories and generated output).
fn walk(root: &Path, extensions: &[&str]) -> Vec<(String, PathBuf)> {
    let walker = WalkDir::new(root).follow_links(false).into_iter().filter_entry(|entry| {
        let name = entry.file_name().to_string_lossy();
        entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
    });
    let mut out: Vec<(String, PathBuf)> = walker
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| entry.path().extension().and_then(|e| e.to_str()).is_some_and(|e| extensions.contains(&e)))
        .filter_map(|entry| super::paths::relative_path(root, entry.path()).map(|rel| (rel, entry.path().to_path_buf())))
        .collect();
    out.sort();
    out
}

/// Population files (whole-repo run).
pub fn population(root: &Path, decl: &HandlerArguments) -> Vec<(String, PathBuf)> {
    walk(root, &["hy"]).into_iter().filter(|(rel, _)| in_population(rel, decl)).collect()
}

/// Hy source: class → frozen? A defclass is a value if its decorator has `:frozen True` or its base is Enum / NamedTuple or the like.
/// defrecord (frozen record) and the alias `(val|setv name (get Callable …))` are values.
pub fn hy_classes(source: &str) -> Vec<ClassFrozen> {
    fn visit(source: &str, forms: &[Form], out: &mut Vec<ClassFrozen>) {
        for form in forms {
            let Node::Seq { delim, items } = &form.node else { continue };
            if *delim == Delim::Paren {
                match head_symbol(source, items) {
                    Some("defclass") => {
                        let (decorators, rest) = match items.get(1) {
                            Some(first) if first.bracket_items().is_some() => (text(source, first), &items[2.min(items.len())..]),
                            _ => ("", &items[1.min(items.len())..]),
                        };
                        if let Some(name) = rest.first().filter(|f| matches!(f.node, Node::Symbol)) {
                            let frozen_decorator = decorators.contains(":frozen True") || decorators.contains("frozen=True");
                            let value_base = rest.get(1).and_then(Form::bracket_items).is_some_and(|bases| {
                                bases.iter().any(|b| VALUE_BASES.contains(&text(source, b)) || VALUE_BASES.contains(&text(source, b).rsplit('.').next().unwrap_or("")))
                            });
                            out.push(ClassFrozen { name: text(source, name).to_string(), frozen: frozen_decorator || value_base });
                        }
                    }
                    Some("defrecord") => {
                        if let Some(name) = items.get(1).filter(|f| matches!(f.node, Node::Symbol)) {
                            out.push(ClassFrozen { name: text(source, name).to_string(), frozen: true });
                        }
                    }
                    Some("val" | "setv") if items.len() == 3 => {
                        let callable = items[2].paren_items().is_some_and(|inner| {
                            head_symbol(source, inner) == Some("get") && inner.get(1).is_some_and(|b| text(source, b) == "Callable")
                        });
                        if callable && matches!(items[1].node, Node::Symbol) {
                            out.push(ClassFrozen { name: text(source, &items[1]).to_string(), frozen: true });
                        }
                    }
                    _ => {}
                }
            }
            visit(source, items, out);
        }
    }
    let mut out = Vec::new();
    visit(source, &read_forms(source), &mut out);
    out
}

/// Python source: class → frozen? (frozen=True decorator, Enum / NamedTuple base, frozen setting in the body). Empty if unreadable.
pub fn py_classes(source: &str) -> Vec<ClassFrozen> {
    fn slice<'s>(source: &'s str, range: rustpython_parser::text_size::TextRange) -> &'s str {
        source.get(usize::from(range.start())..usize::from(range.end())).unwrap_or("")
    }
    fn visit(source: &str, body: &[Stmt], out: &mut Vec<ClassFrozen>) {
        for stmt in body {
            match stmt {
                Stmt::ClassDef(class) => {
                    let decorated = class.decorator_list.iter().any(|d| {
                        let t: String = slice(source, rustpython_ast::Ranged::range(d)).chars().filter(|c| !c.is_whitespace()).collect();
                        t.contains("frozen=True")
                    });
                    let value_base = class.bases.iter().any(|b| match b {
                        Expr::Name(n) => VALUE_BASES.contains(&n.id.as_str()),
                        Expr::Attribute(a) => VALUE_BASES.contains(&a.attr.as_str()),
                        _ => false,
                    });
                    let setting = class.body.iter().any(|s| matches!(s, Stmt::Assign(_)) && slice(source, rustpython_ast::Ranged::range(s)).contains("frozen"));
                    out.push(ClassFrozen { name: class.name.to_string(), frozen: decorated || value_base || setting });
                    visit(source, &class.body, out);
                }
                Stmt::FunctionDef(f) => visit(source, &f.body, out),
                Stmt::If(s) => {
                    visit(source, &s.body, out);
                    visit(source, &s.orelse, out);
                }
                _ => {}
            }
        }
    }
    let mut out = Vec::new();
    if let Ok(Mod::Module(module)) = parse(source, Mode::Module, "<class-index>") {
        visit(source, &module.body, &mut out);
    }
    out
}

/// Index of the repo's Hy and Python classes (per-file cache — only changed files are re-read). overlay = the pre-save content for a single-file run.
pub fn class_index(root: &Path, overlay: Option<(&str, &str)>) -> ClassIndex {
    let files = walk(root, &["hy", "py"]);
    let found: Vec<(String, Vec<ClassFrozen>)> = super::facts_cache::per_file(root, "handler-argument-classes", &files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        Some((rel.to_string(), classes_in(rel, &source)))
    });
    index_of(&found, overlay)
}

/// Classes of one file (Hy or Python is decided by the path from the root).
fn classes_in(rel: &str, source: &str) -> Vec<ClassFrozen> {
    if rel.ends_with(".hy") {
        hy_classes(source)
    } else {
        py_classes(source)
    }
}

/// How to walk the files that go into the index (the same set as `walk` — does not descend into hidden or generated directories, and picks up Hy and Python files).
const CLASS_WALK: WalkRules = WalkRules { descend: class_descends, take: class_takes };

fn class_descends(name: &str) -> bool {
    !(name.starts_with('.') || SKIPPED_DIRS.contains(&name))
}

fn class_takes(extension: &str) -> bool {
    extension == "hy" || extension == "py"
}

/// Class index for a single-file run (the answer is the same as `class_index`). Walks the repo's Hy and Python files with directory-open waits overlapped (`walk` goes one at a time, so on a cold
/// worktree it would wait for about 2000 directories one by one), and looks up the classes of unchanged files by git blob id without reading the content (`ChunkedFacts`).
/// The same content is read differently as Hy and as Python, so the cache kinds are kept separate.
pub fn class_index_by_blobs(root: &Path, overlay: Option<(&str, &str)>) -> ClassIndex {
    let hy = ChunkedFacts::<Vec<ClassFrozen>>::open(root, "class-frozen-hy", |rel| rel.ends_with(".hy") && hy_files::walks_to(&CLASS_WALK, rel));
    let py = ChunkedFacts::<Vec<ClassFrozen>>::open(root, "class-frozen-py", |rel| rel.ends_with(".py") && hy_files::walks_to(&CLASS_WALK, rel));
    let read = AtomicUsize::new(0);
    let classes_of = |path: &Path| -> Option<(String, Vec<ClassFrozen>)> {
        let rel = super::paths::relative_path(root, path)?;
        let memory = if rel.ends_with(".hy") { hy.as_ref() } else { py.as_ref() };
        let looked = memory.and_then(|memory| {
            let (found, meta) = memory.lookup_path(&rel, path)?;
            Some((memory, found, meta))
        });
        let unknown = match looked {
            Some((_, Lookup::Known(classes), _)) => return Some((rel, classes)),
            Some((memory, Lookup::Unknown(oid), meta)) => Some((memory, oid, meta)),
            Some((_, Lookup::Untrusted, _)) | None => None,
        };
        read.fetch_add(1, Ordering::Relaxed);
        let source = std::fs::read_to_string(path).ok()?;
        let classes = classes_in(&rel, &source);
        if let Some((memory, oid, meta)) = unknown {
            memory.learn_read(&rel, oid, path, meta.as_ref(), source.as_bytes(), classes.clone());
        }
        Some((rel, classes))
    };
    let found: Vec<(String, Vec<ClassFrozen>)> = crate::timing::timed("handler-argument-classes.scan", || match hy_files::walk_reading_with(root, &CLASS_WALK, &classes_of) {
        Some(walked) => walked.into_iter().filter_map(|(_, found)| found).collect(),
        // If the root cannot be opened as a directory, leave it to the walker (`walk`).
        None => walk(root, &["hy", "py"]).iter().filter_map(|(_, path)| classes_of(path)).collect(),
    });
    for memory in [&hy, &py].into_iter().flatten() {
        memory.finish();
    }
    crate::timing::count("handler-argument-classes.scan", "read", read.load(Ordering::Relaxed));
    index_of(&found, overlay)
}

/// Build the index from per-file classes (a name is not frozen if even one class with that name is not frozen). Overlay files are counted with their pre-save content.
fn index_of(found: &[(String, Vec<ClassFrozen>)], overlay: Option<(&str, &str)>) -> ClassIndex {
    let mut index = ClassIndex::new();
    let mut add = |classes: &[ClassFrozen]| {
        for class in classes {
            let entry = index.entry(class.name.clone()).or_insert(true);
            *entry = *entry && class.frozen;
        }
    };
    for (rel, classes) in found {
        if overlay.is_some_and(|(o, _)| o == rel) {
            continue;
        }
        add(classes);
    }
    if let Some((rel, source)) = overlay {
        if rel.ends_with(".hy") {
            add(&hy_classes(source));
        }
    }
    index
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decl() -> HandlerArguments {
        HandlerArguments {
            files: vec!["controllers/**/*.hy".into()],
            exclude: vec!["**/tests/**".into()],
            store_names: vec!["state".into(), "store".into()],
            store_suffixes: vec!["-store".into()],
            keep_mark: Some("Reason to keep as argument:".into()),
            value_types: vec!["Settings".into()],
        }
    }

    fn kinds(source: &str, classes: &ClassIndex) -> Vec<(String, &'static str)> {
        findings_in(source, &decl(), classes).into_iter().map(|f| (f.detail(), f.kind.word())).collect()
    }

    #[test]
    fn counterexamples_are_red() {
        let source = r#"
(defclass Ctx [] (setv x 1))
(defhandler h [#^ HttpClient http conn #^ (get dict str int) table #^ Ctx ctx state #^ Lock lock]
  {:tags {}}
  None)
"#;
        let classes: ClassIndex = hy_classes(source).into_iter().map(|c| (c.name, c.frozen)).collect();
        assert_eq!(
            kinds(source, &classes),
            vec![
                ("h::http".to_string(), "client"),
                ("h::conn".to_string(), "client"),
                ("h::table".to_string(), "container"),
                ("h::ctx".to_string(), "object"),
                ("h::state".to_string(), "named"),
                ("h::lock".to_string(), "object"),
            ]
        );
    }

    #[test]
    fn values_and_kept_handlers_are_green() {
        let source = r#"
(defrecord Cfg (#^ str url))
(defclass [(dataclass :frozen True)] Frozen [])
(defhandler h [#^ str url #^ int n #^ Cfg cfg #^ Frozen f #^ Settings s #^ (| str None) maybe #^ (get Callable [int] int) fn label [retries 3]]
  None)

(defhandler kept [#^ (get dict str int) table]
  ;; Reason to keep as argument: a store the check builds the content of before passing it
  None)
"#;
        let classes: ClassIndex = hy_classes(source).into_iter().map(|c| (c.name, c.frozen)).collect();
        assert!(kinds(source, &classes).is_empty(), "{:?}", kinds(source, &classes));
    }

    #[test]
    fn nested_handlers_are_read() {
        let source = "(defk make [] (defhandler inner [store] None) inner)\n";
        assert_eq!(kinds(source, &ClassIndex::new()), vec![("inner::store".to_string(), "named")]);
    }

    #[test]
    fn python_classes_read_frozen() {
        let source = "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass A:\n    x: int\n\nclass B:\n    pass\n\nclass C(Enum):\n    X = 1\n";
        let found = py_classes(source);
        assert_eq!(
            found,
            vec![
                ClassFrozen { name: "A".into(), frozen: true },
                ClassFrozen { name: "B".into(), frozen: false },
                ClassFrozen { name: "C".into(), frozen: true },
            ]
        );
    }

    #[test]
    fn population_uses_the_declaration() {
        assert!(in_population("controllers/a/core/x.hy", &decl()));
        assert!(!in_population("controllers/a/tests/x.hy", &decl()));
        assert!(!in_population("scripts/x.hy", &decl()));
    }

    /// The single-file-run index (walks in parallel, looks up by blob id) gives the same answer as `class_index` (walks in order, per-file cache) — including hidden dirs, generated dirs,
    /// symlinks, files that are neither Hy nor Python, same-named frozen and non-frozen classes, and pre-save content (overlay).
    #[test]
    fn the_index_made_while_walking_in_parallel_equals_the_plain_index() {
        let dir = tempfile::tempdir().unwrap();
        let files: [(&str, &str); 9] = [
            ("app/a.hy", "(defrecord Cfg [url])\n(defclass [(dataclass :frozen True)] Frozen [])\n"),
            ("app/b.py", "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass Settings:\n    x: int\n\nclass Store:\n    pass\n"),
            ("app/legacy.hy", "(defclass Cfg [] (setv x 1))\n"),
            ("app/.hidden/c.hy", "(defclass Hidden [])\n"),
            ("app/node_modules/d.py", "class Skipped:\n    pass\n"),
            ("app/venv/e.py", "class Venv:\n    pass\n"),
            ("app/notes.txt", "class NotCode:\n    pass\n"),
            ("deep/x/y/z.hy", "(defclass [(dataclass :frozen True)] Deep [])\n"),
            ("app/w.hy", "(defclass Written [])\n"),
        ];
        for (rel, text) in files {
            std::fs::create_dir_all(dir.path().join(rel).parent().unwrap()).unwrap();
            std::fs::write(dir.path().join(rel), text).unwrap();
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(dir.path().join("app"), dir.path().join("linked")).unwrap();
        for overlay in [None, Some(("app/w.hy", "(defclass [(dataclass :frozen True)] Written [])\n"))] {
            let plain = class_index(dir.path(), overlay);
            assert!(plain.len() >= 6 && plain.get("Cfg") == Some(&false) && plain.get("Settings") == Some(&true), "not enough check material: {plain:?}");
            assert_eq!(class_index_by_blobs(dir.path(), overlay), plain);
        }
    }
}

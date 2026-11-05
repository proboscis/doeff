//! Telling apart attributes a class places itself from attributes its parents own (DOEFF002).
//!
//! DOEFF002 makes attributes mutated outside init use a `_mut_` name. For a class that writes to an attribute defined by a parent (framework), renaming it would make the
//! framework stop reading it (writing Textual's `App.sub_title` changes the header display). Whether an attribute name belongs to the class itself or a parent is
//! answered here, per class:
//!
//! - Placed by itself = names written to `self` by init (`__init__`, `__post_init__`, `__set_name__`) and construction (`__new__`), names written by methods of the same class
//!   called from there as `self.<method>()`, and names written in the class body (annotated fields, assignments, methods, nested classes, `__slots__` names).
//! - Parents are resolved from the class's bases via imports. For a class in the same repo (same file, or the file an import points to), the names it places itself are lifted and
//!   read, and its bases are descended into too. Bases outside the repo do not expose all their attributes — if there is even one invisible (opaque) base, a name that the class and its in-repo parents
//!   do not place is read as a name owned by the invisible base.
//! - Names known to be owned by a base outside the repo are added from a table (`data/framework_attributes.json`). Names in the table are parent names even if the class also places them in init.
//!   Bases known to have no attributes (`object`, `abc.ABC`, and others) are not counted as invisible bases.
//!
//! The path to find a class in the same repo (the file an import points to): an absolute import is resolved by taking the module path from the directories above the file being read (up to the `.git` dir)
//! that lack `__init__.py` (dirs that serve as import roots — a package dir is not a root) and from its `src`. A relative import climbs from the dir of the file being read
//! by the number of levels. Re-exports in `__init__.py` (`from .a import B`, names of `__getattr__` -> module table) are followed up to 8 levels by the same
//! `crate::origins` walk used to resolve effect names. A class that cannot be found or read is read the same as one outside the repo
//! (a base whose attributes are not all visible) — the failure direction is only "do not fire", never toward firing.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::convert::Infallible;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

use rustpython_ast::text_size::TextRange;
use rustpython_ast::{Constant, Expr, Fold, Mod, Ranged, Stmt, StmtClassDef};
use rustpython_parser::{parse, Mode};
use serde::Deserialize;

use crate::origins::{dotted_parts, import_roots_within, lazy_exports, lookup, python_bindings, reexport_hop, Binding, MAX_REEXPORT_DEPTH, PYTHON_FILES};
use crate::utils::nested_bodies;

/// Initialization methods — names written to `self` here are names the class places, and DOEFF002 does not fire. `__set_name__` is descriptor initialization
/// (called once when the class is created — PEP 487).
const INIT_METHODS: [&str; 3] = ["__init__", "__post_init__", "__set_name__"];

/// The kind of method that contains writes to `self`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// `__init__`, `__post_init__`, `__set_name__` — initialization. DOEFF002 does not fire.
    Init,
    /// `__new__` — construction. A place where attributes are set, but DOEFF002 still does not count it as initialization and fires, as before.
    Construct,
    /// Any other method — mutation after construction.
    Later,
}

impl Phase {
    fn of_method(name: &str) -> Self {
        if INIT_METHODS.contains(&name) {
            Phase::Init
        } else if name == "__new__" {
            Phase::Construct
        } else {
            Phase::Later
        }
    }

    /// Whether writes in this method are writes where the class places attributes.
    pub fn introduces(self) -> bool {
        self != Phase::Later
    }
}

/// One write to `self.<name>` (with the position of the start of the statement).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SelfWrite {
    pub name: String,
    pub offset: usize,
    pub phase: Phase,
}

/// All writes to `self` in the bodies of the class's methods (`def` and `async def`), in the order written.
pub fn self_writes(class_def: &StmtClassDef) -> Vec<SelfWrite> {
    let mut writes = Vec::new();
    for stmt in &class_def.body {
        if let Some((name, body)) = method_of(stmt) {
            collect_body_writes(body, Phase::of_method(name), &mut writes);
        }
    }
    writes
}

/// The name and body of a method (`def` or `async def`) in the class body.
fn method_of(stmt: &Stmt) -> Option<(&str, &[Stmt])> {
    match stmt {
        Stmt::FunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
        Stmt::AsyncFunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
        _ => None,
    }
}

/// Names written to `self` in a method body and the nested bodies in it (the list of bodies lives in one place, `nested_bodies`). Nested classes are skipped:
/// the `self` of that method is an object of the nested class.
fn collect_body_writes(body: &[Stmt], phase: Phase, writes: &mut Vec<SelfWrite>) {
    for stmt in body {
        collect_stmt_writes(stmt, phase, writes);
        if matches!(stmt, Stmt::ClassDef(_)) {
            continue;
        }
        for nested in nested_bodies(stmt) {
            collect_body_writes(nested, phase, writes);
        }
    }
}

/// Names that one statement itself writes to `self` (excluding those from nested bodies).
fn collect_stmt_writes(stmt: &Stmt, phase: Phase, writes: &mut Vec<SelfWrite>) {
    let offset = stmt.range().start().to_usize();
    match stmt {
        Stmt::Assign(assign) => {
            for target in &assign.targets {
                collect_target_writes(target, offset, phase, writes);
            }
        }
        Stmt::AugAssign(aug_assign) => collect_target_writes(&aug_assign.target, offset, phase, writes),
        Stmt::AnnAssign(ann_assign) => collect_target_writes(&ann_assign.target, offset, phase, writes),
        _ => {}
    }
}

/// `self.X` on the left side of an assignment (including inside tuples, lists, and starred).
fn collect_target_writes(target: &Expr, offset: usize, phase: Phase, writes: &mut Vec<SelfWrite>) {
    match target {
        Expr::Tuple(tuple) => {
            for element in &tuple.elts {
                collect_target_writes(element, offset, phase, writes);
            }
        }
        Expr::List(list) => {
            for element in &list.elts {
                collect_target_writes(element, offset, phase, writes);
            }
        }
        Expr::Starred(starred) => collect_target_writes(&starred.value, offset, phase, writes),
        _ => {
            if let Some(name) = self_attribute(target) {
                writes.push(SelfWrite { name: name.to_string(), offset, phase });
            }
        }
    }
}

/// The name in `self.<name>`.
fn self_attribute(expr: &Expr) -> Option<&str> {
    if let Expr::Attribute(attribute) = expr {
        if let Expr::Name(name) = &*attribute.value {
            if name.id.as_str() == "self" {
                return Some(attribute.attr.as_str());
            }
        }
    }
    None
}

/// Names written in the class's fields (directly under the body, and in nested bodies of if, try, etc.): annotated fields, assignment names, names of methods and nested classes, and
/// names listed in `__slots__`.
fn class_level_names(body: &[Stmt], names: &mut BTreeSet<String>) {
    for stmt in body {
        match stmt {
            Stmt::FunctionDef(func) => {
                names.insert(func.name.to_string());
            }
            Stmt::AsyncFunctionDef(func) => {
                names.insert(func.name.to_string());
            }
            Stmt::ClassDef(class_def) => {
                names.insert(class_def.name.to_string());
            }
            Stmt::AnnAssign(ann_assign) => bound_names(&ann_assign.target, names),
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    bound_names(target, names);
                }
                if assign.targets.iter().any(|target| matches!(target, Expr::Name(name) if name.id.as_str() == "__slots__")) {
                    slot_names(&assign.value, names);
                }
            }
            _ => {
                for nested in nested_bodies(stmt) {
                    class_level_names(nested, names);
                }
            }
        }
    }
}

/// Plain names bound by the left side of an assignment (including inside tuples, lists, and starred).
fn bound_names(target: &Expr, names: &mut BTreeSet<String>) {
    match target {
        Expr::Name(name) => {
            names.insert(name.id.to_string());
        }
        Expr::Tuple(tuple) => tuple.elts.iter().for_each(|element| bound_names(element, names)),
        Expr::List(list) => list.elts.iter().for_each(|element| bound_names(element, names)),
        Expr::Starred(starred) => bound_names(&starred.value, names),
        _ => {}
    }
}

/// The string names of `__slots__ = ("a", "b")` (a single string, tuple, or list).
fn slot_names(value: &Expr, names: &mut BTreeSet<String>) {
    match value {
        Expr::Constant(constant) => {
            if let Constant::Str(name) = &constant.value {
                names.insert(name.clone());
            }
        }
        Expr::Tuple(tuple) => tuple.elts.iter().for_each(|element| slot_names(element, names)),
        Expr::List(list) => list.elts.iter().for_each(|element| slot_names(element, names)),
        _ => {}
    }
}

/// One base of a class.
#[derive(Debug, Clone, PartialEq, Eq)]
enum BaseRef {
    /// A name or attribute chain (`Base`, `mod.Base`; a subscripted `Generic[T]` becomes `Generic` with the subscript removed).
    Dotted(Vec<String>),
    /// A base that cannot be pointed to by name, such as a call or starred.
    Opaque,
}

fn base_ref(expr: &Expr) -> BaseRef {
    match expr {
        Expr::Name(name) => BaseRef::Dotted(vec![name.id.to_string()]),
        Expr::Attribute(attribute) => match base_ref(&attribute.value) {
            BaseRef::Dotted(mut parts) => {
                parts.push(attribute.attr.to_string());
                BaseRef::Dotted(parts)
            }
            BaseRef::Opaque => BaseRef::Opaque,
        },
        Expr::Subscript(subscript) => base_ref(&subscript.value),
        _ => BaseRef::Opaque,
    }
}

/// Facts about one class in a module.
#[derive(Debug)]
struct ClassFacts {
    name: String,
    /// Position of the start of the class statement (the key that identifies one class).
    offset: usize,
    /// Whether it is directly under the module (not inside a function or class). Only such classes can be imported from another file.
    module_level: bool,
    /// Names the class itself places (writes to `self` in init and construction + names in the class body).
    introduced: BTreeSet<String>,
    bases: Vec<BaseRef>,
}

/// Names of `self.<name>(…)` calls (including inside calls and nested bodies).
#[derive(Default)]
struct SelfCalls {
    names: BTreeSet<String>,
}

impl Fold<TextRange> for SelfCalls {
    type TargetU = TextRange;
    type Error = Infallible;
    type UserContext = ();

    fn will_map_user(&mut self, _user: &TextRange) -> Self::UserContext {}

    fn map_user(&mut self, user: TextRange, _context: ()) -> Result<TextRange, Infallible> {
        Ok(user)
    }

    fn fold_expr(&mut self, node: Expr) -> Result<Expr, Infallible> {
        if let Expr::Call(call) = &node {
            if let Some(name) = self_attribute(&call.func) {
                self.names.insert(name.to_string());
            }
        }
        rustpython_ast::fold::fold_expr(self, node)
    }
}

/// Names of methods of the same class reachable from initialization (init and construction) through `self.<method>()` calls (including calls of calls). The methods of the init group themselves are not included.
/// Attributes placed by helper methods that initialization calls are also names the class places during construction (`__init__` calls `self._load()`, and `_load` places `self._files`).
fn initialization_helpers(class_def: &StmtClassDef) -> BTreeSet<String> {
    let methods: HashMap<&str, &[Stmt]> = class_def.body.iter().filter_map(method_of).collect();
    let mut pending: Vec<&str> = methods.keys().copied().filter(|name| Phase::of_method(name).introduces()).collect();
    let mut reached = BTreeSet::new();
    while let Some(name) = pending.pop() {
        let mut calls = SelfCalls::default();
        for stmt in methods[name] {
            // The error type is Infallible, so the result is always Ok
            let _ = calls.fold_stmt(stmt.clone());
        }
        for called in calls.names {
            if let Some((&method, _)) = methods.get_key_value(called.as_str()) {
                if !Phase::of_method(method).introduces() && reached.insert(method.to_string()) {
                    pending.push(method);
                }
            }
        }
    }
    reached
}

fn class_facts(class_def: &StmtClassDef, module_level: bool) -> ClassFacts {
    let helpers = initialization_helpers(class_def);
    let mut introduced = BTreeSet::new();
    for stmt in &class_def.body {
        if let Some((name, body)) = method_of(stmt) {
            if Phase::of_method(name).introduces() || helpers.contains(name) {
                let mut writes = Vec::new();
                collect_body_writes(body, Phase::Init, &mut writes);
                introduced.extend(writes.into_iter().map(|write| write.name));
            }
        }
    }
    class_level_names(&class_def.body, &mut introduced);
    ClassFacts {
        name: class_def.name.to_string(),
        offset: class_def.range.start().to_usize(),
        module_level,
        introduced,
        bases: class_def.bases.iter().map(base_ref).collect(),
    }
}

/// All classes of a module in the order written (including classes inside functions and classes).
fn collect_classes(body: &[Stmt], module_level: bool, classes: &mut Vec<ClassFacts>) {
    for stmt in body {
        match stmt {
            Stmt::ClassDef(class_def) => {
                classes.push(class_facts(class_def, module_level));
                collect_classes(&class_def.body, false, classes);
            }
            Stmt::FunctionDef(func) => collect_classes(&func.body, false, classes),
            Stmt::AsyncFunctionDef(func) => collect_classes(&func.body, false, classes),
            _ => {
                for nested in nested_bodies(stmt) {
                    collect_classes(nested, module_level, classes);
                }
            }
        }
    }
}

/// Facts about one module: the bundle of classes and imports.
#[derive(Debug)]
struct ModuleFacts {
    path: PathBuf,
    bindings: HashMap<String, Binding>,
    /// Table of names loaded lazily by `__getattr__` (name -> module).
    lazy: HashMap<String, String>,
    classes: Vec<ClassFacts>,
}

impl ModuleFacts {
    fn of(ast: &Mod, path: PathBuf) -> Self {
        let mut facts = Self { path, bindings: HashMap::new(), lazy: HashMap::new(), classes: Vec::new() };
        if let Mod::Module(module) = ast {
            facts.bindings = python_bindings(&module.body);
            facts.lazy = lazy_exports(&module.body);
            collect_classes(&module.body, true, &mut facts.classes);
        }
        facts
    }
}

/// What is known about bases outside the repo (the bundled `data/framework_attributes.json`).
#[derive(Debug, Deserialize)]
struct Catalog {
    /// Full names of bases known to have no attributes at all.
    transparent_bases: BTreeSet<String>,
    /// Full name of a base -> attributes known to be owned by that base (not all attributes of the base).
    framework_attributes: BTreeMap<String, BTreeSet<String>>,
}

fn catalog() -> &'static Catalog {
    static CATALOG: OnceLock<Catalog> = OnceLock::new();
    CATALOG.get_or_init(|| {
        serde_json::from_str(include_str!("../../data/framework_attributes.json"))
            .expect("the bundled framework_attributes.json cannot be read (the test bundled_catalog_is_well_formed should be red)")
    })
}

type FileCache = Mutex<HashMap<PathBuf, Option<Arc<ModuleFacts>>>>;

/// File -> module facts read (None for a file that could not be read). The same file is read only once per run.
fn file_cache() -> &'static FileCache {
    static CACHE: OnceLock<FileCache> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn locked(cache: &FileCache) -> MutexGuard<'_, HashMap<PathBuf, Option<Arc<ModuleFacts>>>> {
    cache.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn facts_of_file(path: &Path) -> Option<Arc<ModuleFacts>> {
    if let Some(hit) = locked(file_cache()).get(path) {
        return hit.clone();
    }
    // Reading and parsing happen outside the lock (so as not to block runs that read other files). When read concurrently, the one stored first is used.
    let facts = std::fs::read_to_string(path)
        .ok()
        .and_then(|source| parse(&source, Mode::Module, &path.to_string_lossy()).ok())
        .map(|ast| Arc::new(ModuleFacts::of(&ast, path.to_path_buf())));
    locked(file_cache()).entry(path.to_path_buf()).or_insert(facts).clone()
}

fn absolute_path(file_path: &str) -> PathBuf {
    let path = Path::new(file_path);
    path.canonicalize().unwrap_or_else(|_| {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir().map_or_else(|_| path.to_path_buf(), |cwd| cwd.join(path))
        }
    })
}

/// The repo root of this run (the root main decided from the config). The upper limit of the path that searches upward dirs — so that even a tree without `.git` (the HEAD tree of a commit hook, a daily copy)
/// searches only the same dirs as a working tree that has `.git`.
static REPO_ROOT: OnceLock<PathBuf> = OnceLock::new();

/// Remember the repo root of the run (only once). If not remembered, the dir containing `.git` is regarded as the root.
pub fn set_repo_root(root: &Path) {
    let _ = REPO_ROOT.set(root.to_path_buf());
}

/// Origin dirs (nearest first) from which absolute imports are resolved.
fn import_roots(from: &Path) -> Vec<PathBuf> {
    import_roots_within(from, REPO_ROOT.get().map(PathBuf::as_path).filter(|root| from.starts_with(root)))
}

/// The file of the module an import points to (level = number of dots of a relative import; 0 means absolute).
fn module_file(from: &Path, level: u32, parts: &[String]) -> Option<PathBuf> {
    let found = if level > 0 {
        let mut dir = from.parent()?.to_path_buf();
        for _ in 1..level {
            dir = dir.parent()?.to_path_buf();
        }
        lookup(&dir, parts, PYTHON_FILES)?
    } else {
        if parts.is_empty() {
            return None;
        }
        import_roots(from).iter().find_map(|root| lookup(root, parts, PYTHON_FILES))?
    };
    Some(found.canonicalize().unwrap_or(found))
}

/// What a base resolved to.
enum Resolved {
    /// A class in the same repo (the module's facts, and the index into that module's class list).
    Class(Arc<ModuleFacts>, usize),
    /// A base outside the repo (the full name resolved from imports — None if it cannot be resolved).
    External(Option<String>),
}

/// The result of looking up a class.
enum Found {
    Class(Arc<ModuleFacts>, usize),
    /// The module is not in the repo (a base outside the repo).
    NotInRepo,
    /// The module is in the repo, but it cannot be read or the class cannot be found.
    Unreadable,
}

/// A class of the same name written before position `before` in the module (a base in the same file). A class directly under the module is preferred; otherwise,
/// one inside a function or class if there is exactly one.
fn local_class(module: &ModuleFacts, name: &str, before: usize) -> Option<usize> {
    if let Some(index) = module.classes.iter().rposition(|class| class.module_level && class.name == name && class.offset < before) {
        return Some(index);
    }
    let mut same = module.classes.iter().enumerate().filter(|(_, class)| class.name == name && class.offset != before);
    match (same.next(), same.next()) {
        (Some((index, _)), None) => Some(index),
        _ => None,
    }
}

/// Look up the class in the module an import points to.
fn find_class(from: &Path, level: u32, module_parts: &[String], class_name: &str, depth: usize) -> Found {
    let Some(file) = module_file(from, level, module_parts) else {
        return Found::NotInRepo;
    };
    let Some(target) = facts_of_file(&file) else {
        return Found::Unreadable;
    };
    match class_in(&target, class_name, depth) {
        Some((module, index)) => Found::Class(module, index),
        None => Found::Unreadable,
    }
}

/// A class directly under the module, otherwise the class at the destination of a `from .a import B` re-export.
fn class_in(target: &Arc<ModuleFacts>, name: &str, depth: usize) -> Option<(Arc<ModuleFacts>, usize)> {
    if let Some(index) = target.classes.iter().rposition(|class| class.module_level && class.name == name) {
        return Some((Arc::clone(target), index));
    }
    if depth >= MAX_REEXPORT_DEPTH {
        return None;
    }
    let hop = reexport_hop(&target.bindings, &target.lazy, name)?;
    let parts: Vec<String> = dotted_parts(&hop.module).collect();
    match find_class(&target.path, hop.level, &parts, &hop.name, depth + 1) {
        Found::Class(module, index) => Some((module, index)),
        Found::NotInRepo | Found::Unreadable => None,
    }
}

/// Resolve base `base` of `class` to either a class in the same repo or a base outside the repo.
fn resolve(module: &Arc<ModuleFacts>, class: &ClassFacts, base: &BaseRef) -> Resolved {
    let BaseRef::Dotted(parts) = base else {
        return Resolved::External(None);
    };
    let head = &parts[0];
    if parts.len() == 1 {
        if let Some(index) = local_class(module, head, class.offset) {
            return Resolved::Class(Arc::clone(module), index);
        }
    }
    let rest = &parts[1..];
    let (level, mut full): (u32, Vec<String>) = match module.bindings.get(head) {
        Some(Binding::Member { module: from, level, name }) => {
            (*level, dotted_parts(from).chain(std::iter::once(name.clone())).chain(rest.iter().cloned()).collect())
        }
        Some(Binding::Module { dotted }) => (0, dotted_parts(dotted).chain(rest.iter().cloned()).collect()),
        Some(Binding::Ambiguous) => return Resolved::External(None),
        // A plain name that is not imported is a builtin (`object` and others)
        None if parts.len() == 1 => return Resolved::External(Some(head.clone())),
        None => return Resolved::External(None),
    };
    let Some(class_name) = full.pop() else {
        return Resolved::External(None);
    };
    match find_class(&module.path, level, &full, &class_name, 0) {
        Found::Class(found, index) => Resolved::Class(found, index),
        Found::Unreadable => Resolved::External(None),
        Found::NotInRepo => {
            full.push(class_name);
            Resolved::External((level == 0).then(|| full.join(".")))
        }
    }
}

/// How a class's inheritance appears.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Inheritance {
    /// Names placed by the class itself and by parents in the same repo.
    pub introduced: BTreeSet<String>,
    /// Names known to be owned by parents outside the repo (those in the table).
    pub framework: BTreeSet<String>,
    /// Whether there is a parent whose attributes are not all visible (a base outside the repo, or an unreadable base).
    pub opaque: bool,
}

impl Inheritance {
    /// Whether attribute `name` is read as a parent's: a name in the table, or, when an invisible parent exists, a name that the class and its in-repo parents do not place.
    pub fn is_inherited(&self, name: &str) -> bool {
        self.framework.contains(name) || (self.opaque && !self.introduced.contains(name))
    }
}

#[derive(Default)]
struct Walk {
    seen: HashSet<(PathBuf, usize)>,
    inheritance: Inheritance,
}

impl Walk {
    fn class(&mut self, module: &Arc<ModuleFacts>, class: &ClassFacts) {
        if !self.seen.insert((module.path.clone(), class.offset)) {
            return;
        }
        self.inheritance.introduced.extend(class.introduced.iter().cloned());
        for base in &class.bases {
            match resolve(module, class, base) {
                Resolved::Class(found, index) => self.class(&found, &found.classes[index]),
                Resolved::External(name) => self.external(name),
            }
        }
    }

    fn external(&mut self, name: Option<String>) {
        let catalog = catalog();
        match name {
            Some(name) if catalog.transparent_bases.contains(&name) => {}
            Some(name) => {
                self.inheritance.opaque = true;
                if let Some(attributes) = catalog.framework_attributes.get(&name) {
                    self.inheritance.framework.extend(attributes.iter().cloned());
                }
            }
            None => self.inheritance.opaque = true,
        }
    }
}

/// Facts about the classes of the file being read (built once per file, answering per-class queries).
pub struct FileClasses {
    facts: Arc<ModuleFacts>,
}

impl FileClasses {
    pub fn of(ast: &Mod, file_path: &str) -> Self {
        Self { facts: Arc::new(ModuleFacts::of(ast, absolute_path(file_path))) }
    }

    /// The inheritance appearance of `class_def` (a class in this file).
    pub fn inheritance(&self, class_def: &StmtClassDef) -> Inheritance {
        let offset = class_def.range.start().to_usize();
        let mut walk = Walk::default();
        match self.facts.classes.iter().find(|class| class.offset == offset) {
            Some(class) => walk.class(&self.facts, class),
            // When the syntax tree and the facts disagree, treat it as having an invisible parent, on the do-not-fire side
            None => walk.inheritance.opaque = true,
        }
        walk.inheritance
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_catalog_is_well_formed() {
        let catalog = catalog();
        assert!(!catalog.transparent_bases.is_empty());
        assert!(!catalog.framework_attributes.is_empty());
        for name in catalog.transparent_bases.iter().chain(catalog.framework_attributes.keys()) {
            assert!(!name.is_empty() && name.split('.').all(|part| !part.is_empty()), "not a full name: {name:?}");
        }
        for (class, attributes) in &catalog.framework_attributes {
            assert!(!attributes.is_empty(), "{class} has empty attributes (bases without attributes belong in transparent_bases)");
            assert!(!catalog.transparent_bases.contains(class), "{class} is in both transparent_bases and framework_attributes");
            for attribute in attributes {
                assert!(
                    !attribute.is_empty() && attribute.chars().all(|c| c.is_alphanumeric() || c == '_'),
                    "attribute name of {class} is not an identifier: {attribute:?}"
                );
            }
        }
    }

    fn facts_of(code: &str) -> ModuleFacts {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        ModuleFacts::of(&ast, PathBuf::from("test.py"))
    }

    #[test]
    fn a_class_introduces_what_init_construction_and_its_fields_name() {
        let facts = facts_of(
            r#"
class Held(Base):
    kept: int = 0
    other = 1
    __slots__ = ("slotted", "also")

    def __init__(self):
        self.in_init = 1

    def __new__(cls):
        self = super().__new__(cls)
        self.in_new = 1
        return self

    def later(self):
        self.only_later = 1

    class Inner:
        pass
"#,
        );
        let held = &facts.classes[0];
        let expected: BTreeSet<String> = ["kept", "other", "__slots__", "slotted", "also", "in_init", "in_new", "later", "Inner", "__init__", "__new__"]
            .iter()
            .map(|name| name.to_string())
            .collect();
        assert_eq!(held.introduced, expected);
        assert!(!held.introduced.contains("only_later"));
    }

    #[test]
    fn bases_are_read_without_their_subscripts() {
        let facts = facts_of("class A(Generic[T], mod.Base, make(), *more, metaclass=Meta):\n    pass\n");
        assert_eq!(
            facts.classes[0].bases,
            vec![
                BaseRef::Dotted(vec!["Generic".to_string()]),
                BaseRef::Dotted(vec!["mod".to_string(), "Base".to_string()]),
                BaseRef::Opaque,
                BaseRef::Opaque,
            ]
        );
    }

    #[test]
    fn a_name_bound_two_ways_is_ambiguous() {
        let facts = facts_of("try:\n    from a import X\nexcept ImportError:\n    from b import X\nimport c.d\nimport c.e\n");
        assert_eq!(facts.bindings.get("X"), Some(&Binding::Ambiguous));
        assert_eq!(facts.bindings.get("c"), Some(&Binding::Module { dotted: "c".to_string() }));
    }

    #[test]
    fn import_roots_skip_package_dirs_and_stop_at_the_repo_root() {
        let repo = tempfile::tempdir().unwrap();
        let root = repo.path();
        std::fs::create_dir_all(root.join(".git")).unwrap();
        std::fs::create_dir_all(root.join("pkg/src/inner")).unwrap();
        std::fs::write(root.join("pkg/src/inner/__init__.py"), "").unwrap();
        let file = root.join("pkg/src/inner/mod.py");
        let roots = import_roots_within(&file, None);
        assert!(!roots.contains(&root.join("pkg/src/inner")), "a package dir is not a root");
        assert_eq!(roots, vec![root.join("pkg/src"), root.join("pkg/src/src"), root.join("pkg"), root.to_path_buf(), root.join("src")]);
    }

    #[test]
    fn import_roots_stop_at_the_given_repo_root_whether_or_not_there_is_a_git_dir() {
        // Even for a tree without `.git` (the HEAD tree of a commit hook, a daily copy), dirs above the root are not searched.
        let tree = tempfile::tempdir().unwrap();
        let root = tree.path().join("tree");
        std::fs::create_dir_all(root.join("pkg")).unwrap();
        let file = root.join("pkg/mod.py");
        assert_eq!(import_roots_within(&file, Some(&root)), vec![root.join("pkg"), root.join("pkg/src"), root.clone(), root.join("src")]);
        let above = import_roots_within(&file, None);
        assert!(above.contains(&tree.path().to_path_buf()), "without knowing the root, it searches up to the upper dirs: {above:?}");
    }
}

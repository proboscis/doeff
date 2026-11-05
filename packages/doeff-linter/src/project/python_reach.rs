//! Follows what DOEFF133 reaches into the Python functions (`.py`) in the repo that a Hy definition names.
//!
//! The Hy index reads only Hy files, so a reach of the shape deftest -> Hy definition -> imported Python function -> `subprocess.run`
//! was cut at the Python function on the graph, and the test was judged "local". Here, the facts of each Python function (strong evidence of raw side
//! effects, and callees decided by name) are taken from the rustpython syntax tree, and the answer is given by following between Python functions.
//!
//! How to read (the same catalog and the same strength rules as the Hy raw side-effect evidence — raw.rs of doeff-indexer):
//! - Evidence = a fully qualified name through imports that matches a catalog pattern (`subprocess.run`, `os.system`), and a built-in
//!   (`open`) written as an unqualified name at the head of a call without being imported. Exception types (names ending in Error, etc.) and ignored names are not counted.
//!   Weak evidence by method name alone is not counted (DOEFF133 counts only strong evidence as an edge).
//! - Type annotations (of arguments, answers, and annotated assignments) and except types are not executions of values, so they are not read.
//! - Callees = only those decided by name: imported names, top-level functions and classes of the same module, and `self.m` / `cls.m` inside a class.
//!   Naming a class reaches construction (`__init__`, `__post_init__`, `__new__`). Attributes on values (`obj.m()`) and runtime imports
//!   (`importlib`) are not decided by name and cannot be followed — the same range as the graph of Hy definitions.
//! - There is no limit on the depth followed (the closure of functions in the repo is followed breadth-first in full — cycles are stopped by not revisiting a function already visited).
//! - A module's location is a path relative to the repo root (`a/b.py` or `a/b/__init__.py`) — the same basis as the Hy index's module names.
//!   An unreadable or syntactically broken module is not silently treated as "does not reach"; the reason is pushed to `errors` (shown in the report's errors field).

use std::cell::RefCell;
use std::collections::{HashMap, HashSet, VecDeque};
use std::convert::Infallible;
use std::path::Path;
use std::rc::Rc;

use doeff_indexer::hy_index::{matches_pattern, RawCatalog, RawCategory};
use rustpython_ast::text_size::TextRange;
use rustpython_ast::{Arg, ExceptHandler, Expr, Fold, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use super::names::absolute_module;

/// One piece of strong evidence of a raw side effect (the classification, and the fully qualified name through imports, or the built-in's name).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PythonEvidence {
    pub category: RawCategory,
    pub name: String,
}

/// The facts of one Python function.
#[derive(Debug, Clone, Default)]
struct FunctionFacts {
    evidence: Vec<PythonEvidence>,
    /// Callees and named full names decided by name (whether it is a repo function or a Hy definition is looked up at query time).
    callees: Vec<String>,
}

/// One Python module that was read.
#[derive(Debug, Default)]
struct PythonModule {
    /// Name inside the module of the function (`f`, `C.m`) -> facts.
    functions: HashMap<String, FunctionFacts>,
    classes: HashSet<String>,
}

/// What is reached from a Python function that a Hy definition names.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PythonReached {
    /// The shortest path to evidence of touching the outside world (named function -> ... -> the evidence's name). None if not reached.
    pub world: Option<Vec<String>>,
    /// Fully qualified names of the Hy definitions that name it through Python functions (to be edges of the Hy graph).
    pub hy: Vec<String>,
}

/// Queries on the reach of Python functions in the repo — remembers modules and answers (only within one run).
pub struct PythonReach<'a> {
    root: &'a Path,
    catalog: &'a RawCatalog,
    modules: RefCell<HashMap<String, Option<Rc<PythonModule>>>>,
    errors: RefCell<Vec<String>>,
}

impl<'a> PythonReach<'a> {
    pub fn new(root: &'a Path, catalog: &'a RawCatalog) -> PythonReach<'a> {
        PythonReach { root, catalog, modules: RefCell::new(HashMap::new()), errors: RefCell::new(Vec::new()) }
    }

    /// Reasons for modules that could not be read (for the report's errors field).
    pub fn errors(&self) -> Vec<String> {
        self.errors.borrow().clone()
    }

    /// Whether a fully qualified name names a Python function in the repo (or a class that has construction).
    pub fn names_function(&self, full: &str) -> bool {
        !self.functions_of(full).is_empty()
    }

    /// From the function a fully qualified name names, follows callees decided by name and answers what is reached. edge = whether the classification counts as an edge, is_hy = whether it is the name of a Hy definition.
    pub fn reach(&self, full: &str, edge: &dyn Fn(RawCategory) -> bool, is_hy: &dyn Fn(&str) -> bool) -> PythonReached {
        let mut parent: HashMap<String, Option<String>> = HashMap::new();
        let mut queue: VecDeque<String> = VecDeque::new();
        for start in self.functions_of(full) {
            if !parent.contains_key(&start) {
                parent.insert(start.clone(), None);
                queue.push_back(start);
            }
        }
        let trail = |parent: &HashMap<String, Option<String>>, at: &str| -> Vec<String> {
            let mut steps = vec![at.to_string()];
            let mut here = at.to_string();
            while let Some(Some(up)) = parent.get(&here) {
                steps.push(up.clone());
                here = up.clone();
            }
            steps.reverse();
            steps
        };
        let mut reached = PythonReached::default();
        let mut hy_seen: HashSet<String> = HashSet::new();
        while let Some(node) = queue.pop_front() {
            let Some(facts) = self.facts_of(&node) else { continue };
            if reached.world.is_none() {
                if let Some(evidence) = facts.evidence.iter().find(|e| edge(e.category)) {
                    let mut steps = trail(&parent, &node);
                    steps.push(evidence.name.clone());
                    reached.world = Some(steps);
                }
            }
            for callee in &facts.callees {
                if is_hy(callee) {
                    if hy_seen.insert(callee.clone()) {
                        reached.hy.push(callee.clone());
                    }
                    continue;
                }
                for next in self.functions_of(callee) {
                    if !parent.contains_key(&next) {
                        parent.insert(next.clone(), Some(node.clone()));
                        queue.push_back(next);
                    }
                }
            }
        }
        reached
    }

    /// The fully qualified name of the function that a fully qualified name names (the longest module match — a function, a class's method, or class construction).
    fn functions_of(&self, full: &str) -> Vec<String> {
        let parts: Vec<&str> = full.split('.').collect();
        for split in (1..parts.len()).rev() {
            let module_name = parts[..split].join(".");
            let Some(module) = self.module(&module_name) else { continue };
            let rest = parts[split..].join(".");
            if module.functions.contains_key(&rest) {
                return vec![format!("{}.{}", module_name, rest)];
            }
            if module.classes.contains(&rest) {
                return ["__init__", "__post_init__", "__new__"]
                    .iter()
                    .map(|m| format!("{}.{}", rest, m))
                    .filter(|m| module.functions.contains_key(m))
                    .map(|m| format!("{}.{}", module_name, m))
                    .collect();
            }
            return Vec::new();
        }
        Vec::new()
    }

    /// Facts for a function's fully qualified name.
    fn facts_of(&self, function: &str) -> Option<FunctionFacts> {
        let parts: Vec<&str> = function.split('.').collect();
        (1..parts.len()).rev().find_map(|split| {
            let module = self.module(&parts[..split].join("."))?;
            module.functions.get(&parts[split..].join(".")).cloned()
        })
    }

    /// The module for a dotted module name (`a/b.py` or `a/b/__init__.py` from the repo root). None if absent.
    fn module(&self, dotted: &str) -> Option<Rc<PythonModule>> {
        if let Some(known) = self.modules.borrow().get(dotted) {
            return known.clone();
        }
        let loaded = self.load(dotted);
        self.modules.borrow_mut().insert(dotted.to_string(), loaded.clone());
        loaded
    }

    fn load(&self, dotted: &str) -> Option<Rc<PythonModule>> {
        if dotted.is_empty() || dotted.split('.').any(|p| p.is_empty() || p.contains(['/', '\\'])) {
            return None;
        }
        let base = dotted.replace('.', "/");
        let (rel, package) = [(format!("{}.py", base), false), (format!("{}/__init__.py", base), true)]
            .into_iter()
            .find(|(rel, _)| self.root.join(rel).is_file())?;
        let source = match std::fs::read_to_string(self.root.join(&rel)) {
            Ok(source) => source,
            Err(error) => {
                self.errors.borrow_mut().push(format!("DOEFF133: cannot read Python module {} ({}) — what its functions reach was not judged", rel, error));
                return None;
            }
        };
        match parse(&source, Mode::Module, &rel) {
            Ok(Mod::Module(module)) => Some(Rc::new(read_module(dotted, package, &module.body, self.catalog))),
            Ok(_) => None,
            Err(error) => {
                self.errors.borrow_mut().push(format!("DOEFF133: cannot parse Python module {} ({}) — what its functions reach was not judged", rel, error));
                None
            }
        }
    }
}

/// Reads the facts of functions and class methods from a module's top level.
fn read_module(dotted: &str, package: bool, body: &[Stmt], catalog: &RawCatalog) -> PythonModule {
    // Base of relative imports: a package's __init__ is itself the package; an ordinary module's parent is the package.
    let anchor = if package { format!("{}.__init__", dotted) } else { dotted.to_string() };
    let mut imports: HashMap<String, String> = HashMap::new();
    let mut defined: HashSet<String> = HashSet::new();
    for stmt in body {
        record_import(stmt, &anchor, &mut imports);
        match stmt {
            Stmt::FunctionDef(f) => {
                defined.insert(f.name.to_string());
            }
            Stmt::AsyncFunctionDef(f) => {
                defined.insert(f.name.to_string());
            }
            Stmt::ClassDef(c) => {
                defined.insert(c.name.to_string());
            }
            _ => {}
        }
    }
    let scope = ModuleScope { dotted, anchor: &anchor, imports: &imports, defined: &defined, catalog };
    let mut module = PythonModule::default();
    for stmt in body {
        match stmt {
            Stmt::FunctionDef(f) => {
                module.functions.insert(f.name.to_string(), scope.facts(&f.body, None));
            }
            Stmt::AsyncFunctionDef(f) => {
                module.functions.insert(f.name.to_string(), scope.facts(&f.body, None));
            }
            Stmt::ClassDef(c) => {
                module.classes.insert(c.name.to_string());
                for inner in &c.body {
                    let (name, fn_body) = match inner {
                        Stmt::FunctionDef(f) => (f.name.to_string(), &f.body),
                        Stmt::AsyncFunctionDef(f) => (f.name.to_string(), &f.body),
                        _ => continue,
                    };
                    module.functions.insert(format!("{}.{}", c.name, name), scope.facts(fn_body, Some(c.name.as_str())));
                }
            }
            _ => {}
        }
    }
    module
}

/// Adds the names that one import statement binds (bound name -> fully qualified name).
fn record_import(stmt: &Stmt, anchor: &str, imports: &mut HashMap<String, String>) {
    match stmt {
        Stmt::Import(import) => {
            for alias in &import.names {
                let module = alias.name.to_string();
                match &alias.asname {
                    Some(asname) => imports.insert(asname.to_string(), module),
                    // `import a.b` binds `a` (`a.b.f` is the fully qualified name as written).
                    None => {
                        let head = module.split('.').next().unwrap_or_default().to_string();
                        imports.insert(head.clone(), head)
                    }
                };
            }
        }
        Stmt::ImportFrom(from) => {
            let dots = from.level.as_ref().map(|l| l.to_u32() as usize).unwrap_or(0);
            let written = format!("{}{}", ".".repeat(dots), from.module.as_ref().map(|m| m.to_string()).unwrap_or_default());
            let module = absolute_module(anchor, &written);
            for alias in &from.names {
                if alias.name.as_str() == "*" {
                    continue;
                }
                let bound = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                let full = if module.is_empty() { alias.name.to_string() } else { format!("{}.{}", module, alias.name) };
                imports.insert(bound, full);
            }
        }
        _ => {}
    }
}

/// The place for name resolution — one module's imports and top-level names.
struct ModuleScope<'s> {
    dotted: &'s str,
    anchor: &'s str,
    imports: &'s HashMap<String, String>,
    defined: &'s HashSet<String>,
    catalog: &'s RawCatalog,
}

/// The result of name resolution.
enum Resolved {
    /// A fully qualified name through imports (checked against the catalog; a callee candidate).
    Imported(String),
    /// A name of the same module (only a callee candidate — not checked against the catalog).
    Own(String),
    /// An unqualified name that is not imported (a built-in or a local binding).
    Unbound(String),
}

impl ModuleScope<'_> {
    /// Facts of one function body (imports inside the body are counted too).
    fn facts(&self, body: &[Stmt], class: Option<&str>) -> FunctionFacts {
        let mut collector = Collector { anchor: self.anchor, local_imports: HashMap::new(), chains: Vec::new() };
        for stmt in body {
            let _ = collector.fold_stmt(stmt.clone());
        }
        let mut facts = FunctionFacts::default();
        let mut seen: HashSet<String> = HashSet::new();
        for (chain, at_call_head) in &collector.chains {
            match self.resolve(chain, class, &collector.local_imports) {
                Resolved::Imported(full) => {
                    if let Some(category) = self.category_of(&full) {
                        facts.evidence.push(PythonEvidence { category, name: full.clone() });
                    }
                    if seen.insert(full.clone()) {
                        facts.callees.push(full);
                    }
                }
                Resolved::Own(full) => {
                    if seen.insert(full.clone()) {
                        facts.callees.push(full);
                    }
                }
                Resolved::Unbound(name) => {
                    if *at_call_head {
                        if let Some(entry) = self.catalog.categories.iter().find(|e| e.builtins.iter().any(|b| b == &name)) {
                            facts.evidence.push(PythonEvidence { category: entry.category, name });
                        }
                    }
                }
            }
        }
        facts
    }

    /// Turns a dotted name as written into a fully qualified name (import in the body > module's import > module's top-level name > self / cls).
    fn resolve(&self, chain: &[String], class: Option<&str>, local: &HashMap<String, String>) -> Resolved {
        let head = chain[0].as_str();
        let rest = &chain[1..];
        let joined = |base: &str| std::iter::once(base.to_string()).chain(rest.iter().cloned()).collect::<Vec<_>>().join(".");
        if let Some(full) = local.get(head).or_else(|| self.imports.get(head)) {
            return Resolved::Imported(joined(full));
        }
        if self.defined.contains(head) {
            return Resolved::Own(joined(&format!("{}.{}", self.dotted, head)));
        }
        match (class, head, rest.first()) {
            (Some(class), "self" | "cls", Some(method)) => Resolved::Own(format!("{}.{}.{}", self.dotted, class, method)),
            _ if rest.is_empty() => Resolved::Unbound(head.to_string()),
            // An attribute on a local value (`x.m`) is not decided by name.
            _ => Resolved::Unbound(String::new()),
        }
    }

    /// Classification of a fully qualified name in the catalog (exception types and ignored names are not counted).
    fn category_of(&self, full: &str) -> Option<RawCategory> {
        let last = full.rsplit('.').next().unwrap_or("");
        if self.catalog.exception_suffixes.iter().any(|s| last.ends_with(s.as_str())) || self.catalog.ignored.iter().any(|p| matches_pattern(full, p)) {
            return None;
        }
        self.catalog.categories.iter().find(|e| e.patterns.iter().any(|p| matches_pattern(full, p))).map(|e| e.category)
    }
}

/// Collects the chain of names in a function body (`a.b.c` — the head is a name and the rest are attributes) and whether it is the head of a call. Imports inside the body are also bound.
struct Collector<'s> {
    anchor: &'s str,
    local_imports: HashMap<String, String>,
    chains: Vec<(Vec<String>, bool)>,
}

/// A chain of an expression made only of names and attributes (`a.b.c` -> [a, b, c]).
fn chain_of(expr: &Expr) -> Option<Vec<String>> {
    match expr {
        Expr::Name(name) => Some(vec![name.id.to_string()]),
        Expr::Attribute(attribute) => {
            let mut chain = chain_of(&attribute.value)?;
            chain.push(attribute.attr.to_string());
            Some(chain)
        }
        _ => None,
    }
}

impl Fold<TextRange> for Collector<'_> {
    type TargetU = TextRange;
    type Error = Infallible;
    type UserContext = ();

    fn will_map_user(&mut self, _user: &TextRange) -> Self::UserContext {}

    fn map_user(&mut self, user: TextRange, _context: ()) -> Result<TextRange, Infallible> {
        Ok(user)
    }

    fn fold_stmt(&mut self, node: Stmt) -> Result<Stmt, Infallible> {
        record_import(&node, self.anchor, &mut self.local_imports);
        match &node {
            // For an annotated assignment, read only the value (the annotation is not an execution of a value).
            Stmt::AnnAssign(assign) => {
                if let Some(value) = &assign.value {
                    self.fold_expr((**value).clone())?;
                }
                Ok(node)
            }
            // A nested function reads its body, decorators and defaults, and does not read the answer's annotation (fold_arg skips the argument annotations).
            Stmt::FunctionDef(f) => {
                self.fold_nested(&f.body, &f.decorator_list, &f.args)?;
                Ok(node)
            }
            Stmt::AsyncFunctionDef(f) => {
                self.fold_nested(&f.body, &f.decorator_list, &f.args)?;
                Ok(node)
            }
            _ => rustpython_ast::fold::fold_stmt(self, node),
        }
    }

    fn fold_expr(&mut self, node: Expr) -> Result<Expr, Infallible> {
        if let Some(chain) = chain_of(&node) {
            self.chains.push((chain, false));
            return Ok(node);
        }
        if let Expr::Call(call) = &node {
            if let Some(chain) = chain_of(&call.func) {
                self.chains.push((chain, true));
            }
        }
        rustpython_ast::fold::fold_expr(self, node)
    }

    /// Argument annotations are not read.
    fn fold_arg(&mut self, node: Arg) -> Result<Arg, Infallible> {
        Ok(node)
    }

    /// An except type is not an execution of a value, so it is not read; only the body is read.
    fn fold_excepthandler(&mut self, node: ExceptHandler) -> Result<ExceptHandler, Infallible> {
        let ExceptHandler::ExceptHandler(handler) = &node;
        for stmt in &handler.body {
            self.fold_stmt(stmt.clone())?;
        }
        Ok(node)
    }
}

impl Collector<'_> {
    fn fold_nested(&mut self, body: &[Stmt], decorators: &[Expr], args: &rustpython_ast::Arguments) -> Result<(), Infallible> {
        for stmt in body {
            self.fold_stmt(stmt.clone())?;
        }
        for decorator in decorators {
            self.fold_expr(decorator.clone())?;
        }
        for default in args.posonlyargs.iter().chain(&args.args).chain(&args.kwonlyargs).filter_map(|a| a.default.as_ref()) {
            self.fold_expr((**default).clone())?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    fn reach_of(dir: &Path, full: &str) -> PythonReached {
        let catalog = RawCatalog::bundled().unwrap();
        let reach = PythonReach::new(dir, &catalog);
        reach.reach(full, &|c| c == RawCategory::Process, &|name| name == "app.hyside.helper")
    }

    #[test]
    fn follows_python_calls_to_a_subprocess() {
        let dir = repo(&[
            ("tool/run.py", "import subprocess as sp\nfrom tool.inner import spawn\n\ndef main(argv: list[str]) -> int:\n    return spawn(argv)\n\ndef quiet() -> int:\n    return 0\n"),
            ("tool/inner.py", "from .deep import go\n\ndef spawn(argv):\n    return go(argv)\n"),
            ("tool/deep.py", "def go(argv):\n    import subprocess\n    return subprocess.run(argv).returncode\n"),
        ]);
        let reached = reach_of(dir.path(), "tool.run.main");
        assert_eq!(
            reached.world,
            Some(vec!["tool.run.main".into(), "tool.inner.spawn".into(), "tool.deep.go".into(), "subprocess.run".into()])
        );
        assert_eq!(reach_of(dir.path(), "tool.run.quiet").world, None);
    }

    #[test]
    fn annotations_and_except_types_are_not_evidence() {
        let dir = repo(&[(
            "tool/run.py",
            "import subprocess\n\ndef typed(done: subprocess.CompletedProcess) -> subprocess.CompletedProcess:\n    x: subprocess.Popen = done\n    try:\n        return x\n    except subprocess.TimeoutExpired:\n        return done\n",
        )]);
        assert_eq!(reach_of(dir.path(), "tool.run.typed").world, None);
    }

    #[test]
    fn methods_constructors_and_hy_callees_are_followed() {
        let dir = repo(&[(
            "tool/run.py",
            "import os\nfrom app.hyside import helper\n\nclass Box:\n    def __init__(self):\n        self.go()\n    def go(self):\n        os.system('x')\n\ndef make():\n    return Box()\n\ndef hand():\n    return helper()\n",
        )]);
        assert_eq!(reach_of(dir.path(), "tool.run.make").world, Some(vec!["tool.run.make".into(), "tool.run.Box.__init__".into(), "tool.run.Box.go".into(), "os.system".into()]));
        assert_eq!(reach_of(dir.path(), "tool.run.hand").hy, vec!["app.hyside.helper".to_string()]);
    }

    #[test]
    fn unparseable_module_is_named_in_errors() {
        let dir = repo(&[("tool/bad.py", "def broken(:\n")]);
        let catalog = RawCatalog::bundled().unwrap();
        let reach = PythonReach::new(dir.path(), &catalog);
        assert!(!reach.names_function("tool.bad.broken"));
        assert_eq!(reach.errors().len(), 1, "{:?}", reach.errors());
    }
}

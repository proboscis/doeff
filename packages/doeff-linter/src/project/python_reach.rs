//! DOEFF133 の届く先を、Hy の定義が名指す repo の中の Python の関数(`.py`)の中まで辿る(agora-redesign #1798)。
//!
//! Hy の索引は Hy の file だけを読むので、deftest → Hy の定義 → import した Python の関数 → `subprocess.run` の形の到達は、
//! 図の上で Python の関数の所で途切れ、テストは「手元」と判じられていた。ここは Python の関数 1 つずつの事実(生の副作用の
//! 強い証拠と、名前で決まる呼び先)を rustpython の構文木から取り、Python の関数の間を辿って答える。
//!
//! 読み方(Hy の生の副作用の証拠 — doeff-indexer の raw.rs — と同じ目録・同じ強さの規則):
//! - 証拠 = import を通した完全な名前が目録の pattern に合う物(`subprocess.run`・`os.system`)と、import されていない
//!   修飾の無い名前を呼び出しの頭に書いた組み込み(`open`)。例外の型(名の終わりが Error 等)と ignored の名は数えない。
//!   method 名だけの弱い証拠は数えない(DOEFF133 は強い証拠だけを縁に数える)。
//! - 型の注記(引数・答え・注記つきの代入の注記)と except の型は値の実行ではないので読まない。
//! - 呼び先 = 名前で決まる物だけ: import した名、同じ module の top level の関数・class、class の中の `self.m` / `cls.m`。
//!   class の名指しは構築(`__init__`・`__post_init__`・`__new__`)へ届く。値の上の属性(`obj.m()`)と実行時の import
//!   (`importlib`)は名前で決まらず辿れない — Hy の定義の図と同じ範囲。
//! - 辿る深さに上限は置かない(repo の中の関数の閉包を幅優先で全部辿る — 循環は 1 度訪ねた関数を訪ね直さないことで止める)。
//! - module の在り処は repo の根からの相対 path(`a/b.py` か `a/b/__init__.py`)— Hy の索引の module 名と同じ基準。
//!   読めない・構文の壊れた module は黙って「届かない」にせず、理由を `errors` に積む(報告の誤りの欄に出る)。

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

/// 生の副作用の強い証拠 1 件(分類と、import を通した完全な名前か組み込みの名)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PythonEvidence {
    pub category: RawCategory,
    pub name: String,
}

/// Python の関数 1 つの事実。
#[derive(Debug, Clone, Default)]
struct FunctionFacts {
    evidence: Vec<PythonEvidence>,
    /// 名前で決まった呼び先・名指しの完全な名(repo の関数か Hy の定義かは問い合わせの時に引く)。
    callees: Vec<String>,
}

/// 読んだ Python の module 1 つ。
#[derive(Debug, Default)]
struct PythonModule {
    /// 関数の module の中の名(`f`・`C.m`)→ 事実。
    functions: HashMap<String, FunctionFacts>,
    classes: HashSet<String>,
}

/// Hy の定義が名指した Python の関数から届く先。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PythonReached {
    /// 外の世界に触れる証拠へ届く最短の道(名指した関数 → … → 証拠の名)。届かなければ None。
    pub world: Option<Vec<String>>,
    /// Python の関数を通って名指す Hy の定義の完全修飾名(Hy の図の辺にする)。
    pub hy: Vec<String>,
}

/// repo の中の Python の関数の到達の問い合わせ — module と答えを覚える(1 度の実行の中だけ)。
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

    /// 読めなかった module の理由(報告の誤りの欄へ)。
    pub fn errors(&self) -> Vec<String> {
        self.errors.borrow().clone()
    }

    /// 完全な名が repo の中の Python の関数(か構築の在る class)を名指すか。
    pub fn names_function(&self, full: &str) -> bool {
        !self.functions_of(full).is_empty()
    }

    /// 完全な名が名指す関数から、名前で決まる呼び先を辿って届く先を答える。edge = 縁に数える分類か・is_hy = Hy の定義の名か。
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

    /// 完全な名が名指す関数の完全な名(module の最も長い一致 — 関数・class の method・class の構築)。
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

    /// 関数の完全な名の事実。
    fn facts_of(&self, function: &str) -> Option<FunctionFacts> {
        let parts: Vec<&str> = function.split('.').collect();
        (1..parts.len()).rev().find_map(|split| {
            let module = self.module(&parts[..split].join("."))?;
            module.functions.get(&parts[split..].join(".")).cloned()
        })
    }

    /// dotted の module 名の module(repo の根からの `a/b.py` か `a/b/__init__.py`)。無ければ None。
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
                self.errors.borrow_mut().push(format!("DOEFF133: Python の module {} を読めない({}) — その関数の届く先は判じていない", rel, error));
                return None;
            }
        };
        match parse(&source, Mode::Module, &rel) {
            Ok(Mod::Module(module)) => Some(Rc::new(read_module(dotted, package, &module.body, self.catalog))),
            Ok(_) => None,
            Err(error) => {
                self.errors.borrow_mut().push(format!("DOEFF133: Python の module {} を構文で読めない({}) — その関数の届く先は判じていない", rel, error));
                None
            }
        }
    }
}

/// module の top level から関数と class の method の事実を読む。
fn read_module(dotted: &str, package: bool, body: &[Stmt], catalog: &RawCatalog) -> PythonModule {
    // 相対 import の基準: package の __init__ は自分が package、ふつうの module は親が package。
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

/// import の文 1 つが束ねる名(束ねた名 → 完全な名)を足す。
fn record_import(stmt: &Stmt, anchor: &str, imports: &mut HashMap<String, String>) {
    match stmt {
        Stmt::Import(import) => {
            for alias in &import.names {
                let module = alias.name.to_string();
                match &alias.asname {
                    Some(asname) => imports.insert(asname.to_string(), module),
                    // `import a.b` は `a` を束ねる(`a.b.f` は書かれたとおりの完全な名)。
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

/// 名前の解決の場 — module 1 つの import と top level の名。
struct ModuleScope<'s> {
    dotted: &'s str,
    anchor: &'s str,
    imports: &'s HashMap<String, String>,
    defined: &'s HashSet<String>,
    catalog: &'s RawCatalog,
}

/// 名前の解決の結果。
enum Resolved {
    /// import を通した完全な名(目録と照らす・呼び先の候補)。
    Imported(String),
    /// 同じ module の名(呼び先の候補だけ — 目録とは照らさない)。
    Own(String),
    /// import されていない修飾の無い名(組み込みか局所の束縛)。
    Unbound(String),
}

impl ModuleScope<'_> {
    /// 関数の本体 1 つの事実(本体の中の import も数える)。
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

    /// dotted の書かれた名を完全な名へ直す(本体の中の import > module の import > module の top level の名 > self / cls)。
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
            // 局所の値の上の属性(`x.m`)は名前で決まらない。
            _ => Resolved::Unbound(String::new()),
        }
    }

    /// 完全な名の目録の分類(例外の型と ignored の名は数えない)。
    fn category_of(&self, full: &str) -> Option<RawCategory> {
        let last = full.rsplit('.').next().unwrap_or("");
        if self.catalog.exception_suffixes.iter().any(|s| last.ends_with(s.as_str())) || self.catalog.ignored.iter().any(|p| matches_pattern(full, p)) {
            return None;
        }
        self.catalog.categories.iter().find(|e| e.patterns.iter().any(|p| matches_pattern(full, p))).map(|e| e.category)
    }
}

/// 関数の本体の名の鎖(`a.b.c` — 頭が名で、残りが属性)と、呼び出しの頭か、を集める。本体の中の import も束ねる。
struct Collector<'s> {
    anchor: &'s str,
    local_imports: HashMap<String, String>,
    chains: Vec<(Vec<String>, bool)>,
}

/// 名と属性だけでできた式の鎖(`a.b.c` → [a, b, c])。
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
            // 注記つきの代入は値だけを読む(注記は値の実行ではない)。
            Stmt::AnnAssign(assign) => {
                if let Some(value) = &assign.value {
                    self.fold_expr((**value).clone())?;
                }
                Ok(node)
            }
            // 入れ子の関数は本体・decorator・既定値を読み、答えの注記は読まない(引数の注記は fold_arg が飛ばす)。
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

    /// 引数の注記は読まない。
    fn fold_arg(&mut self, node: Arg) -> Result<Arg, Infallible> {
        Ok(node)
    }

    /// except の型は値の実行ではないので読まず、本体だけを読む。
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

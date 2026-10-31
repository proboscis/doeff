//! class が自分で置く属性と、継承元が持つ属性の見分け(DOEFF002)。
//!
//! DOEFF002 は init の外で書き換える属性を `_mut_` の名にさせる。継承元(framework)が定義した属性へ書く class は、名を変えると
//! framework が読まなくなる(Textual の `App.sub_title` は、書くと header の表示が変わる)。属性の名が class 自身の物か継承元の物かを、
//! ここが class ごとに答える:
//!
//! - 自分で置く = init(`__init__`・`__post_init__`・`__set_name__`)と構築(`__new__`)が `self` へ書く名・そこから `self.<method>()` で
//!   呼ぶ同じ class の method が書く名・class の欄に書いた名(注釈つきの欄・代入・method・入れ子の class・`__slots__` の名)。
//! - 継承元は class の基底を import から解く。同じ repo の class(同じ file・import が指す file)は、その class が自分で置く名を持ち上げて
//!   読み、その基底へも降りる。repo の外の基底は属性の全部が見えない — 見えない基底(opaque)が 1 つでも在れば、class とその repo の中の継承元が
//!   置いていない名は、見えない基底が持つ名と読む。
//! - repo の外の基底が持つと分かっている名は表(`data/framework_attributes.json`)で足す。表の名は、class 自身が init でも置いていても継承元の名。
//!   属性を持たないと分かっている基底(`object`・`abc.ABC` ほか)は、見えない基底に数えない。
//!
//! 同じ repo の class を探す道(import が指す file): 絶対 import は、読んでいる file の上の dir(`.git` の dir まで)のうち `__init__.py` を持たない
//! dir(import の起点になる dir — package の dir は起点ではない)と、その `src` から module の path を引く。相対 import は読んでいる file の dir から
//! 段の数だけ上る。`__init__.py` の再 export(`from .a import B`)は 8 段まで辿る。どれでも見つからない・読めない class は repo の外と同じに読む
//! (属性の全部が見えない基底)— 外れる向きは「鳴らさない」だけで、鳴らす向きに外れない。

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::convert::Infallible;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

use rustpython_ast::text_size::TextRange;
use rustpython_ast::{Constant, Expr, Fold, Mod, Ranged, Stmt, StmtClassDef};
use rustpython_parser::{parse, Mode};
use serde::Deserialize;

use crate::utils::{each_statement, nested_bodies};

/// 初期化の method — ここで `self` へ書く名は class が置く名で、DOEFF002 は鳴らさない。`__set_name__` は descriptor の初期化
/// (class が作られる時に 1 度だけ呼ばれる — PEP 487)。
const INIT_METHODS: [&str; 3] = ["__init__", "__post_init__", "__set_name__"];

/// `__init__.py` の再 export(`from .a import B`)を辿る段の上限。
const MAX_REEXPORT_DEPTH: usize = 8;

/// `self` への書きが在る method の種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// `__init__`・`__post_init__`・`__set_name__` — 初期化。DOEFF002 は鳴らさない。
    Init,
    /// `__new__` — 構築。属性を置く場所だが、DOEFF002 は今までどおり初期化に数えず鳴らす。
    Construct,
    /// それ以外の method — 作った後の書き換え。
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

    /// この method の書きは、class が属性を置く書きか。
    pub fn introduces(self) -> bool {
        self != Phase::Later
    }
}

/// `self.<名>` への書き 1 つ(文の始まりの位置つき)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SelfWrite {
    pub name: String,
    pub offset: usize,
    pub phase: Phase,
}

/// class の method(`def` と `async def`)の本体の `self` への書きを、書いた順に全部。
pub fn self_writes(class_def: &StmtClassDef) -> Vec<SelfWrite> {
    let mut writes = Vec::new();
    for stmt in &class_def.body {
        if let Some((name, body)) = method_of(stmt) {
            collect_body_writes(body, Phase::of_method(name), &mut writes);
        }
    }
    writes
}

/// class の本体の method(`def` か `async def`)の名と本体。
fn method_of(stmt: &Stmt) -> Option<(&str, &[Stmt])> {
    match stmt {
        Stmt::FunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
        Stmt::AsyncFunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
        _ => None,
    }
}

/// method の本体とその中の入れ子の本体(本体の一覧は `nested_bodies` の 1 か所)で `self` へ書いた名。入れ子の class は飛ばす:
/// その method の `self` は入れ子の class の object。
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

/// 文 1 つが自分で `self` へ書く名(入れ子の本体の分は含まない)。
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

/// 代入の左辺の `self.X`(組・list・starred の中も)。
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

/// `self.<名>` の名。
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

/// class の欄(本体の直下と、if・try ほかの入れ子の本体)に書いた名: 注釈つきの欄・代入の名・method と入れ子の class の名・
/// `__slots__` に並べた名。
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

/// 代入の左辺が束ねる素の名(組・list・starred の中も)。
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

/// `__slots__ = ("a", "b")` の文字列の名(1 つの文字列・組・list)。
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

/// class の基底の 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
enum BaseRef {
    /// 名か属性の連なり(`Base`・`mod.Base`・添字つきの `Generic[T]` は添字を外した `Generic`)。
    Dotted(Vec<String>),
    /// 呼び出し・starred など、名では指せない基底。
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

/// module の class 1 つの事実。
#[derive(Debug)]
struct ClassFacts {
    name: String,
    /// class 文の始まりの位置(class を 1 つに決める鍵)。
    offset: usize,
    /// module の直下(関数や class の中でない)か。別の file から import できるのはこの class だけ。
    module_level: bool,
    /// class 自身が置く名(init と構築の `self` への書き + class の欄の名)。
    introduced: BTreeSet<String>,
    bases: Vec<BaseRef>,
}

/// `self.<名>(…)` の呼びの名(呼びの中・入れ子の本体の中も)。
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

/// 初期化(init と構築)から `self.<method>()` の呼びで届く、同じ class の method の名(呼びの先の先も)。init の組の method 自身は含めない。
/// 初期化が呼ぶ補助の method が置く属性も、class が構築の間に置く名(`__init__` が `self._load()` を呼び、`_load` が `self._files` を置く)。
fn initialization_helpers(class_def: &StmtClassDef) -> BTreeSet<String> {
    let methods: HashMap<&str, &[Stmt]> = class_def.body.iter().filter_map(method_of).collect();
    let mut pending: Vec<&str> = methods.keys().copied().filter(|name| Phase::of_method(name).introduces()).collect();
    let mut reached = BTreeSet::new();
    while let Some(name) = pending.pop() {
        let mut calls = SelfCalls::default();
        for stmt in methods[name] {
            // 誤りの型が Infallible なので、結果は常に Ok
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

/// module の class を書いた順に全部(関数と class の中の class も)。
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

/// import が束ねた名。
#[derive(Debug, Clone, PartialEq, Eq)]
enum Binding {
    /// module を指す名: `import a.b as m` の m は `a.b`、`import a.b` の a は `a`。
    Module { dotted: String },
    /// `from <module> import <name>` の名(level = 相対 import の点の数)。
    Member { module: String, level: u32, name: String },
    /// 同じ名を違う物へ束ねた(try の import の逃げ道ほか)— どちらとも決めない。
    Ambiguous,
}

fn collect_bindings(body: &[Stmt]) -> HashMap<String, Binding> {
    let mut bindings = HashMap::new();
    each_statement(body, &mut |stmt| match stmt {
        Stmt::Import(import) => {
            for alias in &import.names {
                let (bound, dotted) = match &alias.asname {
                    Some(asname) => (asname.to_string(), alias.name.to_string()),
                    None => {
                        let head = alias.name.split('.').next().unwrap_or_default().to_string();
                        (head.clone(), head)
                    }
                };
                bind(&mut bindings, bound, Binding::Module { dotted });
            }
        }
        Stmt::ImportFrom(from) => {
            let level = from.level.as_ref().map_or(0, |level| level.to_u32());
            let module = from.module.as_ref().map(|module| module.to_string()).unwrap_or_default();
            for alias in &from.names {
                if alias.name.as_str() == "*" {
                    continue;
                }
                let bound = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                bind(&mut bindings, bound, Binding::Member { module: module.clone(), level, name: alias.name.to_string() });
            }
        }
        _ => {}
    });
    bindings
}

fn bind(bindings: &mut HashMap<String, Binding>, name: String, binding: Binding) {
    match bindings.get(&name) {
        Some(existing) if *existing != binding => {
            bindings.insert(name, Binding::Ambiguous);
        }
        Some(_) => {}
        None => {
            bindings.insert(name, binding);
        }
    }
}

/// module 1 つの事実: class と import の束ね。
#[derive(Debug)]
struct ModuleFacts {
    path: PathBuf,
    bindings: HashMap<String, Binding>,
    classes: Vec<ClassFacts>,
}

impl ModuleFacts {
    fn of(ast: &Mod, path: PathBuf) -> Self {
        let mut facts = Self { path, bindings: HashMap::new(), classes: Vec::new() };
        if let Mod::Module(module) = ast {
            facts.bindings = collect_bindings(&module.body);
            collect_classes(&module.body, true, &mut facts.classes);
        }
        facts
    }
}

/// repo の外の基底について分かっている事(同梱の `data/framework_attributes.json`)。
#[derive(Debug, Deserialize)]
struct Catalog {
    /// 属性を 1 つも持たないと分かっている基底の完全な名。
    transparent_bases: BTreeSet<String>,
    /// 基底の完全な名 → その基底が持つと分かっている属性(基底の属性の全部ではない)。
    framework_attributes: BTreeMap<String, BTreeSet<String>>,
}

fn catalog() -> &'static Catalog {
    static CATALOG: OnceLock<Catalog> = OnceLock::new();
    CATALOG.get_or_init(|| {
        serde_json::from_str(include_str!("../../data/framework_attributes.json"))
            .expect("同梱の framework_attributes.json が読めない(検 bundled_catalog_is_well_formed が赤のはず)")
    })
}

type FileCache = Mutex<HashMap<PathBuf, Option<Arc<ModuleFacts>>>>;

/// file → 読んだ module の事実(読めなかった file は None)。1 回の実行の中で同じ file を 1 度だけ読む。
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
    // 読みと構文解析は鎖の外で行う(別の file を読む実行を待たせない)。同時に読んだ時は先に置いた方を使う。
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

/// この実行の repo の根(main が設定から決めた根)。上の dir を探す道の上限 — `.git` の無い木(commit の hook の HEAD の木・日次の写し)でも、
/// `.git` のある作業木と同じ dir だけを探すため。
static REPO_ROOT: OnceLock<PathBuf> = OnceLock::new();

/// 実行の repo の根を覚える(1 度だけ)。覚えなければ `.git` を持つ dir を根とみなす。
pub fn set_repo_root(root: &Path) {
    let _ = REPO_ROOT.set(root.to_path_buf());
}

/// 絶対 import を引く起点の dir(近い順)。
fn import_roots(from: &Path) -> Vec<PathBuf> {
    import_roots_within(from, REPO_ROOT.get().map(PathBuf::as_path).filter(|root| from.starts_with(root)))
}

/// 読んでいる file の上の dir のうち `__init__.py` を持たない dir と、その `src`。`bound`(repo の根)までで、無ければ `.git` を持つ dir まで。
fn import_roots_within(from: &Path, bound: Option<&Path>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    let mut dir = from.parent();
    while let Some(current) = dir {
        if !current.join("__init__.py").is_file() {
            for root in [current.to_path_buf(), current.join("src")] {
                if !roots.contains(&root) {
                    roots.push(root);
                }
            }
        }
        let at_root = match bound {
            Some(root) => current == root,
            None => current.join(".git").exists(),
        };
        if at_root {
            break;
        }
        dir = current.parent();
    }
    roots
}

/// 起点の dir から module の path を引く(`a/b.py` か `a/b/__init__.py`。名の無い module は起点の `__init__.py`)。
fn lookup(root: &Path, parts: &[String]) -> Option<PathBuf> {
    let base = parts.iter().fold(root.to_path_buf(), |path, part| path.join(part));
    if !parts.is_empty() {
        let module = base.with_extension("py");
        if module.is_file() {
            return Some(module);
        }
    }
    let package = base.join("__init__.py");
    package.is_file().then_some(package)
}

/// import が指す module の file(level = 相対 import の点の数・0 は絶対)。
fn module_file(from: &Path, level: u32, parts: &[String]) -> Option<PathBuf> {
    let found = if level > 0 {
        let mut dir = from.parent()?.to_path_buf();
        for _ in 1..level {
            dir = dir.parent()?.to_path_buf();
        }
        lookup(&dir, parts)?
    } else {
        if parts.is_empty() {
            return None;
        }
        import_roots(from).iter().find_map(|root| lookup(root, parts))?
    };
    Some(found.canonicalize().unwrap_or(found))
}

/// 基底を解いた先。
enum Resolved {
    /// 同じ repo の class(module の事実と、その中の class の並びの添字)。
    Class(Arc<ModuleFacts>, usize),
    /// repo の外の基底(import から解いた完全な名 — 解けなければ None)。
    External(Option<String>),
}

/// class を探した結果。
enum Found {
    Class(Arc<ModuleFacts>, usize),
    /// module が repo の中に無い(repo の外の基底)。
    NotInRepo,
    /// module は repo の中に在るが、読めないか class が見つからない。
    Unreadable,
}

fn dotted_parts(dotted: &str) -> impl Iterator<Item = String> + '_ {
    dotted.split('.').filter(|part| !part.is_empty()).map(str::to_string)
}

/// module の中で、位置 `before` より前に書いた同じ名の class(同じ file の基底)。module の直下の class を優先し、無ければ
/// 関数や class の中でも 1 つだけならそれ。
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

/// import が指す module の class を探す。
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

/// module の直下の class、無ければ `from .a import B` の再 export の先の class。
fn class_in(target: &Arc<ModuleFacts>, name: &str, depth: usize) -> Option<(Arc<ModuleFacts>, usize)> {
    if let Some(index) = target.classes.iter().rposition(|class| class.module_level && class.name == name) {
        return Some((Arc::clone(target), index));
    }
    if depth >= MAX_REEXPORT_DEPTH {
        return None;
    }
    match target.bindings.get(name)? {
        Binding::Member { module, level, name: original } => {
            let parts: Vec<String> = dotted_parts(module).collect();
            match find_class(&target.path, *level, &parts, original, depth + 1) {
                Found::Class(module, index) => Some((module, index)),
                Found::NotInRepo | Found::Unreadable => None,
            }
        }
        Binding::Module { .. } | Binding::Ambiguous => None,
    }
}

/// `class` の基底 `base` を、同じ repo の class か repo の外の基底かに解く。
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
        // import していない素の名は組み込み(`object` ほか)
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

/// class の継承の見え方。
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Inheritance {
    /// class 自身と、同じ repo の継承元が置く名。
    pub introduced: BTreeSet<String>,
    /// repo の外の継承元が持つと分かっている名(表に載る物)。
    pub framework: BTreeSet<String>,
    /// 属性の全部が見えない継承元(repo の外の基底・読めない基底)が在るか。
    pub opaque: bool,
}

impl Inheritance {
    /// 属性 `name` は継承元の物と読むか: 表の名か、見えない継承元が在って class と repo の中の継承元が置いていない名。
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

/// 読んでいる file の class の事実(file 1 つにつき 1 度だけ作り、class ごとの問いに答える)。
pub struct FileClasses {
    facts: Arc<ModuleFacts>,
}

impl FileClasses {
    pub fn of(ast: &Mod, file_path: &str) -> Self {
        Self { facts: Arc::new(ModuleFacts::of(ast, absolute_path(file_path))) }
    }

    /// `class_def`(この file の class)の継承の見え方。
    pub fn inheritance(&self, class_def: &StmtClassDef) -> Inheritance {
        let offset = class_def.range.start().to_usize();
        let mut walk = Walk::default();
        match self.facts.classes.iter().find(|class| class.offset == offset) {
            Some(class) => walk.class(&self.facts, class),
            // 構文木と事実が食い違う時は、見えない継承元が在る物として鳴らさない側へ
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
            assert!(!name.is_empty() && name.split('.').all(|part| !part.is_empty()), "完全な名でない: {name:?}");
        }
        for (class, attributes) in &catalog.framework_attributes {
            assert!(!attributes.is_empty(), "{class} の属性が空(属性を持たない基底は transparent_bases へ)");
            assert!(!catalog.transparent_bases.contains(class), "{class} が transparent_bases と framework_attributes の両方に在る");
            for attribute in attributes {
                assert!(
                    !attribute.is_empty() && attribute.chars().all(|c| c.is_alphanumeric() || c == '_'),
                    "{class} の属性の名が識別子でない: {attribute:?}"
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
        assert!(!roots.contains(&root.join("pkg/src/inner")), "package の dir は起点でない");
        assert_eq!(roots, vec![root.join("pkg/src"), root.join("pkg/src/src"), root.join("pkg"), root.to_path_buf(), root.join("src")]);
    }

    #[test]
    fn import_roots_stop_at_the_given_repo_root_whether_or_not_there_is_a_git_dir() {
        // `.git` の無い木(commit の hook の HEAD の木・日次の写し)でも、根の上の dir は探さない。
        let tree = tempfile::tempdir().unwrap();
        let root = tree.path().join("tree");
        std::fs::create_dir_all(root.join("pkg")).unwrap();
        let file = root.join("pkg/mod.py");
        assert_eq!(import_roots_within(&file, Some(&root)), vec![root.join("pkg"), root.join("pkg/src"), root.clone(), root.join("src")]);
        let above = import_roots_within(&file, None);
        assert!(above.contains(&tree.path().to_path_buf()), "根を知らなければ上の dir まで探す: {above:?}");
    }
}

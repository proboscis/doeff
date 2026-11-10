//! DOEFF126: finds places that call a defk definition bare.
//!
//! Calling a defk bare returns a Program instead of the answer. It does not fail with a type error and flows on quietly as a wrong value (
//! `latest-by-ref`, which had been changed to a defk, was still being called bare by a deff and 4 test files — found by a test). Changes from defn / deff to defk number in the hundreds and
//! keep coming, so this watches for callers left behind. A convention (reversible).
//!
//! A definition known to be a defk = a top-level `(defk name …)` in a Hy file of the repo (also inside `do` and `eval-and-compile`). The spelling of a call head is resolved to a module using the file's imports and
//! the definition's location (`smells::Scope`) and compared with the set of defks. Calls that cannot be traced (functions received as arguments, methods) are not picked up.
//!
//! Only places that use the call's answer as a value are picked up (where a Program flows in place of a value and quietly goes wrong): comparisons, operations, and boolean combinations
//! (`=`, `+`, `in`, `not`, `and` …), built-in functions that read the answer (`len`, `str`, `get`, `sorted`, `isinstance` …), the target and arguments of a method
//! (`(.get (f …) "field")`, `(.append out (f …))`) and attributes (`(. (f …) field)`), conditions (the head of `if`, `when`, `while`; the condition of `cond`), the source of repetition (the binding of `for` and
//! the source of a comprehension), and record fields (calls that build a type whose head is capitalized — effects of doeff packages are excluded, and so are fields whose type declared by the repo's `defrecord` names Program or
//! EffectBase — `ProgramFields`). Branches of `if`, `when`, `cond`, `do`, `let` inside such a position
//! stay as places that use the answer.
//! Among arguments to a repo defk call, positions whose receiving parameter's `:pre` type cannot accept a Program (`ValueParams` — built-in value types, None, the repo's
//! `defrecord`, `defwire`, `defenum`, the top-level classes of the repo's Python files, and their `(get T …)`, `(of T …)`, `(| …)`) are also places that use the answer (`(counts-sources-of (measure-rows-of standing))`
//! — a Call is passed in, and it first hits the `:pre` type check at run time and fails. The real case: agora-controllers' job patrol failed every time about 5 minutes after startup on 2026-11-04.
//! It was not run until the stage's cycle opened, and slipped through merge and declaration). Parameters without a type annotation, or that accept Program, object, Any, or an unknown alias are not picked up.
//! Python classes count since agora-controllers' screen server passed `(_post-trip-start …)` bare to `preceded`, whose `:pre` is `(: step Step)` with `Step` a frozen dataclass of
//! a .py file (2026-11-10 — every answer of a letter failed the contract for about 2 hours, card [id]). Classes whose bases name Program, EffectBase, DoExpr or Protocol are
//! not counted (they may accept a Program — `py_value_classes_in`).
//! Not picked up: the right side of `(<- …)`, `(! …)`, `(return …)`, arguments of a call that accepts a Program (including the form passed to a repo function — it may be a function that accepts a Program and runs it
//! (such as run-on) and cannot be traced), and bindings to a name (it may be passed later as a Program).
//!
//! Outside definitions (top-level expressions of a module — `(val TABLE [(entry "a" (f …)) …])`, `(setv PAIRS #(…))`), regardless of position, they are picked up as places that use the answer.
//! Outside a definition there is nowhere to run a Program, and a Program bound into a table row or a name flows on in place of a value (the real case: agora-controllers c6271008a
//! added a defk tests-of without run to the top-level table of scripts/land_focus_gate.hy, and `--all` failed at startup). However, inside calls to `run`, `<-`, `!`, and
//! doeff packages, arguments of defk calls, function bodies (`fn`, `fnk`, `defmacro`, `defclass` …), and quote / quasiquote, the same judgment as inside a definition applies as it descends.
//! `defsystem` is a definition (expanded to a function), and the Program expression on a job row is not a place that uses the answer.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};

use super::facts::ByteSpan;
use super::facts_cache;
use super::names::{hy_mangle, module_of};
use super::rule::ProjectRule;
use super::smells::{children, live, span_of, Hy, Scope};

/// The set of defks (names including the module, mangled) and, for each defk's parameters, those whose `:pre` type is named (`ValueParams`).
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct DefkNames {
    names: BTreeSet<String>,
    /// Name of a defk including the module -> its parameter list and `:pre` types (a defk that names no parameter type is not recorded).
    #[serde(default)]
    params: BTreeMap<String, ValueParams>,
}

/// One defk's parameter list and the types named by `(: name type)` in `:pre`. The type is, per union branch, an empty spelling for a built-in type that cannot accept a Program or for None,
/// otherwise the name resolved to a module using the defining file's imports (compared at the call site with the repo's record names — `ProgramFields::is_record`).
/// A parameter with any branch of an unreadable form (a string annotation, type arguments, an expression that may name Program) is not recorded.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ValueParams {
    /// Names of parameters that can be passed by position (mangled; up to before `*` or `#*`).
    positional: Vec<String>,
    /// Parameter name (mangled) -> the branches of the type's union.
    types: BTreeMap<String, Vec<String>>,
}

impl ValueParams {
    /// Whether the parameter (name mangled) names only types that cannot accept a Program (`records` = the repo's record type names).
    fn refuses_program(&self, name: &str, records: &ProgramFields) -> bool {
        self.types.get(name).is_some_and(|arms| !arms.is_empty() && arms.iter().all(|arm| arm.is_empty() || records.is_record(arm)))
    }
}

impl DefkNames {
    /// Built from a list of names including the module (for tests).
    pub fn of(names: &[&str]) -> Self {
        DefkNames { names: names.iter().map(|n| n.to_string()).collect(), params: BTreeMap::new() }
    }

    /// Adds another set.
    pub fn extend(&mut self, other: DefkNames) {
        self.names.extend(other.names);
        self.params.extend(other.params);
    }

    /// The parameter list and types of a defk (name including the module).
    fn params_of(&self, qualified: &str) -> Option<&ValueParams> {
        self.params.get(qualified)
    }

    /// Whether the name including the module is a defk.
    pub fn contains(&self, qualified: &str) -> bool {
        self.names.contains(qualified)
    }

    /// Count (for reporting).
    pub fn len(&self) -> usize {
        self.names.len()
    }

    /// Whether it is empty.
    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }

    /// Fingerprint of the collection (used for the mark of the per-file facts cache — facts that depend on this collection are rebuilt when the collection changes).
    pub fn digest(&self) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        for name in &self.names {
            hasher.update(name.as_bytes());
            hasher.update([0u8]);
        }
        for (name, params) in &self.params {
            hasher.update(name.as_bytes());
            hasher.update([1u8]);
            hasher.update(params.positional.join(" ").as_bytes());
            for (param, arms) in &params.types {
                hasher.update([2u8]);
                hasher.update(param.as_bytes());
                hasher.update(arms.join(" ").as_bytes());
            }
        }
        format!("{:x}", hasher.finalize())
    }
}

/// Names of top-level defks in one source (`<module>.<name>`).
pub fn defk_names_in(source: &str, module: &str) -> DefkNames {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let bindings = super::facts::hy_bindings(source, module);
    let scope = Scope { module, bindings: &bindings };
    let mut names = BTreeSet::new();
    let mut params = BTreeMap::new();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defk") => {
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form.map(|f| match &f.node {
                    Node::Annotated { target: Some(target), .. } => hy.text(target),
                    _ => hy.text(f),
                });
                if let Some(name) = name.filter(|n| !n.is_empty()) {
                    let qualified = format!("{}.{}", module, hy_mangle(name));
                    if let Some(found) = value_params_of(&hy, scope, &items).filter(|p| !p.types.is_empty()) {
                        params.insert(qualified.clone(), found);
                    }
                    names.insert(qualified);
                }
            }
            _ => {}
        }
    }
    DefkNames { names, params }
}

/// Built-in types that cannot accept a Program (a Call is not an instance of any of them — the `:pre` isinstance always rejects it).
const VALUE_BUILTINS: &[&str] = &[
    "None", "str", "int", "float", "bool", "bytes", "bytearray", "complex", "tuple", "list", "dict", "set", "frozenset",
];

/// The parameter list and types of `(defk name [params] {:pre [(: name type) …] …} …)` (the items of `defk` — the decorator list before the name is not read).
fn value_params_of(hy: &Hy<'_>, scope: Scope<'_>, items: &[&Form]) -> Option<ValueParams> {
    let params = items.get(2).and_then(|p| p.bracket_items())?;
    let mut positional = Vec::new();
    let mut open = true;
    for param in super::smells::live_items(params) {
        let name = match &param.node {
            Node::Symbol => hy.symbol(param),
            Node::Annotated { target: Some(target), .. } => hy.symbol(target),
            Node::Seq { delim: Delim::Bracket, .. } => param.bracket_items().and_then(|i| i.first()).and_then(|first| hy.symbol(first)),
            _ => {
                // `#* rest`, `#** kw` — what follows cannot be passed by position.
                open = false;
                continue;
            }
        };
        match name {
            Some("*") => open = false,
            Some("/") | None => {}
            Some(name) if open && !name.starts_with('&') => positional.push(hy_mangle(name)),
            Some(_) => {}
        }
    }
    let contract = items.iter().skip(3).take(2).find(|f| f.is_brace())?;
    let entries = match &contract.node {
        Node::Seq { delim: Delim::Brace, items } => super::smells::live_items(items),
        _ => return None,
    };
    let pre = entries.chunks(2).find(|pair| pair.first().is_some_and(|k| hy.text(k) == ":pre")).and_then(|pair| pair.get(1))?;
    let mut types = BTreeMap::new();
    for check in pre.bracket_items().map(super::smells::live_items).unwrap_or_default() {
        let Some(parts) = live(check) else { continue };
        let (Some(":"), Some(name), Some(ty)) = (parts.first().map(|h| hy.text(h)), parts.get(1).and_then(|n| hy.symbol(n)), parts.get(2)) else {
            continue;
        };
        if let (Some(arms), 3) = (type_arms(hy, scope, ty), parts.len()) {
            types.insert(hy_mangle(name), arms);
        }
    }
    Some(ValueParams { positional, types })
}

/// The union branches of a type expression (built-in value types and None are an empty spelling; other names are names resolved to a module). None for unreadable forms.
fn type_arms(hy: &Hy<'_>, scope: Scope<'_>, ty: &Form) -> Option<Vec<String>> {
    if let Some(name) = hy.symbol(ty) {
        return Some(vec![if VALUE_BUILTINS.contains(&name) { String::new() } else { scope.qualify(name) }]);
    }
    let parts = live(ty)?;
    match parts.first().and_then(|h| hy.symbol(h))? {
        "|" => {
            let mut arms = Vec::new();
            for arm in &parts[1..] {
                arms.extend(type_arms(hy, scope, arm)?);
            }
            Some(arms)
        }
        // `(get tuple #(X ...))`, `(of dict K V)` — isinstance looks only at the outer type.
        "get" | "of" => type_arms(hy, scope, parts.get(1)?),
        _ => None,
    }
}

/// The set of defks in the repo's Hy files — takes the files to collect (`files`) and the lookup method (`reach`) (empty if the rule is disabled). Reads only files that contain the spelling `(defk`, and makes the names of top-level defks
/// names including the module. A single-file run passes only the module files that the written file names (the set of defks is looked up by names including the module, and the names looked up are those obtained by resolving the written file's
/// symbols — the answer is the same with only those modules' files).
pub fn defk_names_over(root: &Path, enabled: &BTreeSet<ProjectRule>, reach: facts_cache::Reach, files: &[(String, PathBuf)]) -> DefkNames {
    let mut all = DefkNames::default();
    if !enabled.contains(&ProjectRule::DefkCalledBare) {
        return all;
    }
    // A file's defk names are decided by that file's contents alone, so only changed files are re-read (even a single-file run on every save
    // needs the names of the whole repo — #1025).
    let found: Vec<DefkNames> = facts_cache::per_file_in(reach, root, "defk-names", "", files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        source.contains("(defk").then(|| defk_names_in(&source, &module_of(rel)))
    });
    for names in found {
        all.extend(names);
    }
    all
}

/// Among the fields of a record type, those whose declared type names Program or EffectBase (type name including the module -> field names, both mangled).
/// Such a field is a declaration that carries a Program, so a bare defk call placed in that field of a type-building call is not a place that uses the answer (
/// doeff-records' RecordsServing has :prepare as (| Program EffectBase), and serve-records runs it as a prepared task).
/// Not included: fields without a type annotation, object / Any fields, and fields hidden behind an alias (the alias's name is not spelled Program) — all still counted as answers as before.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ProgramFields {
    fields: BTreeMap<String, BTreeSet<String>>,
    /// Type names of the repo's `defrecord`, `defwire`, `defenum` and Python classes (names including the module, mangled — `py_value_classes_in`) — types that cannot accept a Program (if a defk parameter's
    /// `:pre` type is only these, a bare defk call placed in that parameter is a place that uses the answer — `ValueParams`).
    #[serde(default)]
    records: BTreeSet<String>,
}

impl ProgramFields {
    /// Adds another table.
    pub fn extend(&mut self, other: ProgramFields) {
        for (record, fields) in other.fields {
            self.fields.entry(record).or_default().extend(fields);
        }
        self.records.extend(other.records);
    }

    /// Whether the field (mangled) of the type name including the module is a declaration that carries a Program.
    pub fn carries_program(&self, record: &str, field: &str) -> bool {
        self.fields.get(record).is_some_and(|names| names.contains(field))
    }

    /// Whether the type name including the module is a repo record (`defrecord`, `defwire`, `defenum`, a Python class).
    pub fn is_record(&self, qualified: &str) -> bool {
        self.records.contains(qualified)
    }

    /// Whether it is empty.
    pub fn is_empty(&self) -> bool {
        self.fields.is_empty() && self.records.is_empty()
    }
}

/// Type names that make a field one that carries a Program when named by the field's type declaration (a spelling with a module is compared by its last segment — `doeff.Program` too).
const PROGRAM_TYPES: &[&str] = &["Program", "EffectBase"];

/// Whether the field's type annotation (spelling) names Program or EffectBase — splits the annotation into words, and true if the last segment of any word is a name in PROGRAM_TYPES
/// (`(| Program EffectBase)`, `Program`, `doeff.Program`). `object`, `Any`, and aliases do not name them.
fn names_program_type(annotation: &str) -> bool {
    annotation
        .split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '.' || c == '-'))
        .any(|word| PROGRAM_TYPES.contains(&word.rsplit('.').next().unwrap_or(word)))
}

/// Fields of a source's top-level `defrecord` whose declared type names Program or EffectBase (`<module>.<type name>` -> field names).
/// A field has 2 spellings, `(#^ type name)` and `#^ type name` (doeff-hy's defrecord). The type names of top-level `defrecord`, `defwire`, `defenum` are also recorded.
pub fn program_fields_in(source: &str, module: &str) -> ProgramFields {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found = ProgramFields::default();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defwire" | "defenum") => {
                if let Some(name) = items.get(1).and_then(|f| hy.symbol(f)) {
                    found.records.insert(format!("{}.{}", module, hy_mangle(name)));
                }
            }
            Some("defrecord") => {
                let Some(name) = items.get(1).and_then(|f| hy.symbol(f)) else { continue };
                let record = format!("{}.{}", module, hy_mangle(name));
                found.records.insert(record.clone());
                for item in items.iter().skip(2) {
                    let annotated = match &item.node {
                        Node::Annotated { .. } => Some(*item),
                        Node::Seq { delim: Delim::Paren, .. } => live(item).and_then(|inner| inner.first().copied()),
                        _ => None,
                    };
                    if let Some(Form { node: Node::Annotated { annotation: Some(annotation), target: Some(target) }, .. }) = annotated {
                        if let (true, Some(field)) = (names_program_type(hy.text(annotation)), hy.symbol(target)) {
                            found.fields.entry(record.clone()).or_default().insert(hy_mangle(field));
                        }
                    }
                }
            }
            _ => {}
        }
    }
    found
}

/// Bases that make a Python class one that may accept a Program (compared by the last segment of the spelling — `doeff.Program` too). A runtime-checkable Protocol matches by shape.
const PROGRAM_BASES: &[&str] = &["Program", "EffectBase", "DoExpr", "Protocol"];

/// Type names (`<module>.<class name>`) of the top-level classes of a Python source that cannot accept a Program (including those inside a top-level `if` — `if TYPE_CHECKING:`).
/// A class is counted unless one of its bases names a type in PROGRAM_BASES (`Generic[T]`, `Protocol[T]` are read by the subscripted name). Empty if the source does not parse.
pub fn py_value_classes_in(source: &str, module: &str) -> ProgramFields {
    use rustpython_ast::{Expr, Mod, Stmt};
    fn base_name(base: &Expr) -> Option<&str> {
        match base {
            Expr::Name(n) => Some(n.id.as_str()),
            Expr::Attribute(a) => Some(a.attr.as_str()),
            Expr::Subscript(s) => base_name(&s.value),
            _ => None,
        }
    }
    fn visit(body: &[Stmt], module: &str, found: &mut ProgramFields) {
        for stmt in body {
            match stmt {
                Stmt::ClassDef(class) => {
                    if !class.bases.iter().any(|b| base_name(b).is_some_and(|name| PROGRAM_BASES.contains(&name))) {
                        found.records.insert(format!("{}.{}", module, class.name));
                    }
                }
                Stmt::If(s) => {
                    visit(&s.body, module, found);
                    visit(&s.orelse, module, found);
                }
                _ => {}
            }
        }
    }
    let mut found = ProgramFields::default();
    if let Ok(Mod::Module(parsed)) = rustpython_parser::parse(source, rustpython_parser::Mode::Module, "<value-classes>") {
        visit(&parsed.body, module, &mut found);
    }
    found
}

/// One bare call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BareCall {
    /// Name of the top-level definition that contains the call (mangled — `<module>` if outside a definition).
    pub definition: String,
    /// Head of the definition that contains the call (defk, deff, defn, deftest …) — for the explanation text.
    pub container: String,
    /// Spelling of the called defk (as written).
    pub callee: String,
    pub span: ByteSpan,
}

impl BareCall {
    /// Detail of the registry key (`<definition>::<called defk>`).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, hy_mangle(&self.callee))
    }
}

/// Python operators and built-in functions that read the answer as a value (all their arguments are places that use the answer).
const VALUE_HEADS: &[&str] = &[
    "=", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "//", "%", "**", "in", "not-in", "not", "and", "or", "is", "is-not", "len", "str",
    "int", "float", "bool", "tuple", "list", "dict", "set", "frozenset", "sorted", "reversed", "any", "all", "sum", "min", "max", "repr",
    "get", "isinstance", "print", "enumerate", "zip", "iter", "next", "hash", "format", "abs", "round", "assert",
];

/// Forms that bind to a name (pairs of `(setv name expr …)`, `(val name expr)`, `(var name expr)`, `(:= name expr)`) — to follow forms that bind a bare call to a name and use the answer later.
const PLAIN_BINDING_HEADS: &[&str] = &["setv", "setx", "val", "var", ":="];

/// Forms that carry the answer-use position into inner branches (the condition expression is separately a place that uses the answer).
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let"];

/// Comprehensions (the source is items[2]).
const COMPREHENSIONS: &[&str] = &["lfor", "sfor", "gfor", "dfor"];

/// Definition heads (to get the name of the definition that contains the call). defsystem (doeff-hy) expands to a function, and the expression on a job row is the Program value itself of
/// "name -> Program" (not run — read by the declaration tooling and sim-cluster), so the same judgment as inside a definition applies as it descends (a job expression is not a place that uses the answer, #913).
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler", "deftest", "defeffect", "defsystem"];

/// Forms outside a definition that take a Program inside (`run` and the form emitted as an effect) — the inside is descended with the same judgment as inside a definition. Calls of doeff packages are treated the same.
const MODULE_LEVEL_PROGRAM_HEADS: &[&str] = &["run", "<-", "!", "yield", "yield-from"];

/// Function bodies, macros, classes, and imports written outside a definition — the inside is descended with the same judgment as inside a definition (they return an answer when called; they are not outside a definition).
const MODULE_LEVEL_BODY_HEADS: &[&str] = &[
    "fn", "fn/a", "fnk", "defmacro", "defmacro/g!", "defreader", "defclass", "defrecord", "defmain", "import", "require", "quote", "quasiquote",
];

/// Picks up every defk called at a place that uses the answer in one source (only the first of the same callee in the same definition). Does not know record field types
/// (all fields of a type-building call are counted as answers — the field types are used by `bare_calls_with`).
pub fn bare_calls_in(source: &str, scope: Scope<'_>, defks: &DefkNames) -> Vec<BareCall> {
    bare_calls_with(source, scope, defks, &ProgramFields::default())
}

/// Same as `bare_calls_in` — except that for a `:field value` in a type-building call, if the field's declared type names Program or EffectBase (`fields`), the value is
/// not counted as an answer (the field is a declaration that carries a Program).
pub fn bare_calls_with(source: &str, scope: Scope<'_>, defks: &DefkNames, fields: &ProgramFields) -> Vec<BareCall> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let walker = Walker { hy: Hy { src: source }, scope, defks, fields };
    let mut found: Vec<BareCall> = Vec::new();
    for form in &forms {
        walker.top(form, &mut found);
    }
    let mut seen = BTreeSet::new();
    found.retain(|call| seen.insert(call.detail()));
    found
}

/// The tool that looks for bare calls and descends the tree.
struct Walker<'a> {
    hy: Hy<'a>,
    scope: Scope<'a>,
    defks: &'a DefkNames,
    fields: &'a ProgramFields,
}

impl Walker<'_> {
    /// One top-level form — for a definition, notes the name and head and descends into the body; `do` and the like are entered.
    fn top(&self, form: &Form, out: &mut Vec<BareCall>) {
        let head = self.hy.head(form);
        match head {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                for child in live(form).unwrap_or_default().into_iter().skip(1) {
                    self.top(child, out);
                }
            }
            Some(h) if DEFINITION_HEADS.contains(&h) => {
                let items = live(form).unwrap_or_default();
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form
                    .map(|f| match &f.node {
                        Node::Annotated { target: Some(target), .. } => self.hy.text(target),
                        _ => self.hy.text(f),
                    })
                    .unwrap_or("");
                let context = Context {
                    definition: hy_mangle(name),
                    container: h.to_string(),
                    bound: self.bare_bindings(form),
                    shadowed: self.local_names(form),
                    module_level: false,
                };
                for child in items.into_iter().skip(1) {
                    match live(child) {
                        // The head of a defsystem job row `(name Program-expr :field value …)` is the job's name, not a call (even if a defk of the same name exists) — only the inside is descended.
                        Some(row) if h == "defsystem" && row.first().is_some_and(|head| matches!(head.node, Node::Symbol)) => {
                            for part in row.into_iter().skip(1) {
                                self.walk(part, false, &context, out);
                            }
                        }
                        _ => self.walk(child, false, &context, out),
                    }
                }
            }
            _ => self.walk(
                form,
                false,
                &Context {
                    definition: format!("<{}>", self.scope.module),
                    container: "module".to_string(),
                    bound: BTreeMap::new(),
                    shadowed: BTreeSet::new(),
                    module_level: true,
                },
                out,
            ),
        }
    }

    /// Descends into a form. `value` is whether this form's answer is in a position used as a value.
    fn walk(&self, form: &Form, value: bool, context: &Context, out: &mut Vec<BareCall>) {
        self.walk_at(form, value, false, context, out);
    }

    /// Descends into a form. `yielded` is whether this form is in a position emitted as an effect (the right side of `(<- … form)`, `(! form)`, `(yield form)`).
    fn walk_at(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.call(form, value, yielded, context, out),
            // A name bound to a bare defk call (`all`, `all.x`) is used as an answer — the bound call becomes a violation.
            Node::Symbol if value => {
                let text = self.hy.text(form);
                let name = text.split('.').next().unwrap_or(text);
                if let Some((callee, span)) = context.bound.get(name) {
                    out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
                }
            }
            // Inside quote / quasiquote outside a definition (`'(f …)`, `` `(f …) ``) and reader tags is not a call — descended with the same judgment as inside a definition.
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } | Node::Tagged { .. } if context.module_level => {
                let inner = context.inside_definition();
                for child in children(form) {
                    self.walk(child, false, &inner, out);
                }
            }
            _ => {
                for child in children(form) {
                    self.walk(child, false, context, out);
                }
            }
        }
    }

    /// One `( … )` — if it is a defk call used as an answer, push it, and pass to children "whether it is a position used as an answer".
    fn call(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        let items = live(form).unwrap_or_default();
        let head = items.first().and_then(|h| self.hy.head_text(h));
        let qualified = head.filter(|h| !h.starts_with(':')).map(|h| self.scope.qualify(h));
        let is_defk = qualified.as_deref().is_some_and(|q| self.defks.contains(q)) && !head.is_some_and(|h| context.shadowed.contains(h));
        // `(all.get k)` — calling a method of a bound name is also a place that uses that name as an answer.
        if let Some((callee, span)) = head.and_then(|h| h.split_once('.')).and_then(|(name, _)| context.bound.get(name)) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
        }
        if let (true, true, Some(callee)) = (is_defk, value || context.module_level, head) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.to_string(), span: span_of(form) });
        }
        let doeff = qualified.as_deref().is_some_and(|q| q.starts_with("doeff"));
        // Outside a definition, in a place where the inside takes a Program, a function body, or an argument to a defk call (that defk may take a Program — the call itself was pushed)
        // the inside is descended with the same judgment as inside a definition.
        // A call that receives `run` as a value (`(map run #((f …) …))`) is also a place that runs the Programs inside.
        let passes_runner =
            items.iter().skip(1).filter_map(|item| self.hy.symbol(item)).any(|s| s == "run" || self.scope.qualify(s) == "doeff.run");
        let leaves_module_level = context.module_level
            && (doeff
                || is_defk
                || passes_runner
                || head.is_some_and(|h| {
                    MODULE_LEVEL_PROGRAM_HEADS.contains(&h) || MODULE_LEVEL_BODY_HEADS.contains(&h) || DEFINITION_HEADS.contains(&h)
                }));
        let inner;
        let context = if leaves_module_level {
            inner = context.inside_definition();
            &inner
        } else {
            context
        };
        // A record field is a place that uses the answer — except that fields of a type emitted as an effect (`(<- (AnswerLater :answer (f …)))`) may carry a Program, so they are excluded.
        let constructor = head.is_some_and(|h| h.chars().next().is_some_and(|c| c.is_ascii_uppercase())) && !doeff && !is_defk && !yielded;
        // The value of `:field value` whose declared field type names Program or EffectBase is placed in a field that carries a Program — not a place that uses the answer (#2877).
        let program_field = |index: usize| -> bool {
            let record = qualified.as_deref().filter(|_| constructor);
            let keyword = items.get(index.wrapping_sub(1)).filter(|prev| index >= 2 && matches!(prev.node, Node::Keyword));
            match (record, keyword) {
                (Some(record), Some(prev)) => self.fields.carries_program(record, &hy_mangle(self.hy.text(prev).trim_start_matches(':'))),
                _ => false,
            }
        };
        let binds = head == Some("<-");
        // Among arguments to a repo defk call, positions whose receiving parameter's `:pre` type cannot accept a Program are places that use the answer (the Call fails the :pre type check).
        let value_slots = qualified
            .as_deref()
            .filter(|_| is_defk)
            .and_then(|q| self.defks.params_of(q))
            .map(|params| self.value_slots(&items, params))
            .unwrap_or_default();
        let last = items.len().saturating_sub(1);
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_value = match head {
                _ if value_slots.get(index).copied().unwrap_or(false) => true,
                Some(h) if VALUE_HEADS.contains(&h) => true,
                Some(".") => index == 1,
                // Both the target and the arguments of a method are places that use the answer (`(.get (f …) "field")`, `(.append out (f …))`).
                Some(h) if h.starts_with('.') && h.len() > 1 => true,
                Some("if" | "when" | "unless" | "while") if index == 1 => true,
                Some("cond") => index % 2 == 1 || value,
                Some(h) if BRANCHING_FORMS.contains(&h) => value,
                Some(h) if COMPREHENSIONS.contains(&h) => index == 2,
                Some("for") if index == 1 => {
                    // `(for [x (f …)] …)` — the source of the binding list (odd positions) is the source of repetition.
                    let sources = child.bracket_items().map(super::smells::live_items).unwrap_or_default();
                    for (at, source) in sources.iter().enumerate() {
                        self.walk(source, at % 2 == 1, context, out);
                    }
                    continue;
                }
                _ => constructor && !matches!(child.node, Node::Keyword) && !program_field(index),
            };
            let child_yielded = match head {
                Some("!" | "yield" | "yield-from") => true,
                _ => binds && (index == last || items.get(index + 1).is_some_and(|next| matches!(next.node, Node::Keyword))),
            };
            self.walk_at(child, child_value, child_yielded, context, out);
        }
    }
}

/// The definition being descended (for the key and explanation), and the names bound within it to bare defk calls (name -> called defk, range of the bound call).
struct Context {
    definition: String,
    container: String,
    bound: BTreeMap<String, (String, ByteSpan)>,
    /// Names bound locally within the definition (as spelled — names, parameters, and binding targets of nested deff, defn, defk). A call with the same spelling calls the local one, so it is not read as a defk call
    /// (a `(deff mail-of …)` in a test of agora-controllers hides the top-level defk mail-of of the same file).
    shadowed: BTreeSet<String>,
    /// Whether it is descending outside definitions (a module's top-level expressions). Outside definitions there is nowhere to run a Program, so defk calls outside forms that take a Program such as `run`
    /// (`MODULE_LEVEL_PROGRAM_HEADS`, calls of doeff packages), outside function bodies (`MODULE_LEVEL_BODY_HEADS`), and outside quote are
    /// places that use the answer regardless of position (table rows, arguments of `+`, arguments of repo functions, bindings to names).
    module_level: bool,
}

impl Context {
    /// A copy with the outside-definition judgment removed (to descend into forms that take a Program, function bodies, and quote with the same judgment as inside a definition).
    fn inside_definition(&self) -> Context {
        Context {
            definition: self.definition.clone(),
            container: self.container.clone(),
            bound: self.bound.clone(),
            shadowed: self.shadowed.clone(),
            module_level: false,
        }
    }
}

impl Walker<'_> {
    /// For each position in the items of a defk call `(f a b :c d)`, whether the receiving parameter's `:pre` type cannot accept a Program (parameters at positions after `#*` or `#**` are not followed).
    fn value_slots(&self, items: &[&Form], params: &ValueParams) -> Vec<bool> {
        let mut slots = vec![false; items.len()];
        let mut position = Some(0usize);
        let mut index = 1;
        while index < items.len() {
            match &items[index].node {
                Node::Keyword => {
                    let name = hy_mangle(self.hy.text(items[index]).trim_start_matches(':'));
                    if let Some(slot) = slots.get_mut(index + 1) {
                        *slot = params.refuses_program(&name, self.fields);
                    }
                    index += 2;
                    continue;
                }
                Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. } => position = None,
                _ => {
                    if let Some(at) = position {
                        slots[index] = params.positional.get(at).is_some_and(|name| params.refuses_program(name, self.fields));
                        position = Some(at + 1);
                    }
                }
            }
            index += 1;
        }
        slots
    }

    /// Names bound locally within the definition (parameters, names and parameters of nested definitions, binding targets) — a call with the same spelling is not read as a defk call.
    fn local_names(&self, definition: &Form) -> BTreeSet<String> {
        let mut names = BTreeSet::new();
        let items = live(definition).unwrap_or_default();
        // The definition's own name is not hidden (a defk that calls itself again is a defk call) — only bindings in the parameters and body are collected.
        let mut body_at = if items.get(1).is_some_and(|first| first.bracket_items().is_some()) { 3 } else { 2 };
        // Some definitions have no parameter list (`(deftest name body …)`) — the first form of the body is not read as parameters.
        if let Some(params) = items.get(body_at).filter(|p| p.bracket_items().is_some()) {
            self.param_names(params, &mut names);
            body_at += 1;
        }
        let mut pending: Vec<&Form> = items.iter().skip(body_at).copied().collect();
        while let Some(form) = pending.pop() {
            pending.extend(children(form));
            let Some(parts) = live(form) else { continue };
            match parts.first().and_then(|h| self.hy.head_text(h)) {
                Some("deff" | "defn" | "defn/a" | "defk" | "defmacro") => {
                    if let Some(name) = parts.get(1).and_then(|f| self.bound_symbol(f)) {
                        names.insert(name.to_string());
                    }
                    if let Some(params) = parts.get(2) {
                        self.param_names(params, &mut names);
                    }
                }
                Some("fn" | "fnk" | "fn/a") => {
                    if let Some(params) = parts.get(1) {
                        self.param_names(params, &mut names);
                    }
                }
                Some(h) if PLAIN_BINDING_HEADS.contains(&h) => {
                    names.extend(parts[1..].iter().step_by(2).filter_map(|t| self.bound_symbol(t)).map(str::to_string));
                }
                Some("<-") if parts.len() >= 3 => names.extend(parts.get(1).and_then(|t| self.hy.symbol(t)).map(str::to_string)),
                Some("let" | "for") => {
                    let pairs = parts.get(1).and_then(|b| b.bracket_items()).map(super::smells::live_items).unwrap_or_default();
                    names.extend(pairs.iter().step_by(2).filter_map(|t| self.bound_symbol(t)).map(str::to_string));
                }
                _ => {}
            }
        }
        names
    }

    /// Names in the parameter list `[a #^ T b [c 1] #* rest]` (as spelled).
    fn param_names(&self, params: &Form, names: &mut BTreeSet<String>) {
        for param in params.bracket_items().map(super::smells::live_items).unwrap_or_default() {
            let name = match &param.node {
                Node::Seq { delim: Delim::Bracket, .. } => param.bracket_items().and_then(|i| i.first()).and_then(|f| self.bound_symbol(f)),
                Node::Prefixed { inner: Some(inner), .. } => self.bound_symbol(inner),
                _ => self.bound_symbol(param),
            };
            names.extend(name.map(str::to_string));
        }
    }

    /// Names of binding targets (a symbol, or the name in `#^ T name`).
    fn bound_symbol(&self, form: &Form) -> Option<&str> {
        match &form.node {
            Node::Annotated { target: Some(target), .. } => self.hy.symbol(target),
            _ => self.hy.symbol(form),
        }
    }

    /// Collects, within a definition, places where a bare defk call is bound to a name with `setv`, `val`, `var`, `:=`, `let`. In a definition that binds the same name also with `(<- name …)`,
    /// that name is not followed (it is undecided which value it is). Followed only within one definition.
    fn bare_bindings(&self, definition: &Form) -> BTreeMap<String, (String, ByteSpan)> {
        let mut bound = BTreeMap::new();
        let mut effect_bound = BTreeSet::new();
        let mut pending = vec![definition];
        while let Some(form) = pending.pop() {
            if let Some(items) = live(form) {
                let head = items.first().and_then(|h| self.hy.head_text(h));
                let pairs: Vec<(&Form, &Form)> = match head {
                    Some(h) if PLAIN_BINDING_HEADS.contains(&h) => items[1..].chunks(2).filter_map(|p| Some((*p.first()?, *p.get(1)?))).collect(),
                    Some("let") => items
                        .get(1)
                        .and_then(|b| b.bracket_items())
                        .map(super::smells::live_items)
                        .unwrap_or_default()
                        .chunks(2)
                        .filter_map(|p| Some((*p.first()?, *p.get(1)?)))
                        .collect(),
                    Some("<-") => {
                        if let Some(name) = items.get(1).and_then(|t| self.hy.symbol(t)) {
                            effect_bound.insert(name.to_string());
                        }
                        Vec::new()
                    }
                    _ => Vec::new(),
                };
                for (target, value) in pairs {
                    let (Some(name), Some(callee)) = (self.hy.symbol(target), self.hy.head(value)) else { continue };
                    if self.defks.contains(&self.scope.qualify(callee)) {
                        bound.entry(name.to_string()).or_insert((callee.to_string(), span_of(value)));
                    }
                }
            }
            pending.extend(children(form));
        }
        bound.retain(|name, _| !effect_bound.contains(name));
        bound
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// For module m and import bindings `bindings`, makes the bare calls into a list of (definition::callee). The defks are `m.fetch` and `lib.load`.
    fn found(source: &str, bindings: &[(&str, &str)]) -> Vec<String> {
        let bindings: BTreeMap<String, String> = bindings.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let defks = DefkNames::of(&["m.fetch", "lib.load"]);
        bare_calls_in(source, Scope { module: "m", bindings: &bindings }, &defks).iter().map(|c| c.detail()).collect()
    }

    #[test]
    fn defk_names_come_from_top_level_defk_definitions() {
        let names = defk_names_in("(defk fetch [x] x)\n(deff plain [x] x)\n(do (defk inner [] 1))\n(defk #^ int typed [] 1)\n", "app.core.x");
        assert!(names.contains("app.core.x.fetch") && names.contains("app.core.x.inner") && names.contains("app.core.x.typed"));
        assert!(!names.contains("app.core.x.plain"));
        assert_eq!(names.len(), 3);
    }

    /// Only when a record field's declared type names Program or EffectBase, the bare call in that field of a type-building call is not counted.
    /// Fields without a type annotation, object / Any fields, fields hidden behind an alias, and values passed by position are counted as before.
    #[test]
    fn record_fields_declared_as_programs_carry_bare_calls_and_others_do_not() {
        let source = r#"(defrecord Serving
  "settings"
  (#^ (| Program EffectBase) prepare)
  #^ Program later
  (#^ doeff.Program qualified)
  (#^ object anything)
  (#^ typing.Any whatever)
  (#^ PreparedProgram aliased)
  (#^ dict rows)
  plain)
(defk carries [x] (Serving :prepare (fetch x) :later (fetch x) :qualified (fetch x)))
(defk untyped [x] (Serving :plain (fetch x)))
(defk opaque [x] (Serving :anything (fetch x)))
(defk any-typed [x] (Serving :whatever (fetch x)))
(defk hidden [x] (Serving :aliased (fetch x)))
(defk data [x] (Serving :rows (fetch x)))
(defk positional [x] (Serving (fetch x)))"#;
        let fields = program_fields_in(source, "m");
        assert!(fields.carries_program("m.Serving", "prepare") && fields.carries_program("m.Serving", "later"));
        assert!(fields.carries_program("m.Serving", "qualified"));
        for field in ["anything", "whatever", "aliased", "rows", "plain"] {
            assert!(!fields.carries_program("m.Serving", field), "{}", field);
        }
        let bindings = BTreeMap::new();
        let defks = DefkNames::of(&["m.fetch"]);
        let got: Vec<String> =
            bare_calls_with(source, Scope { module: "m", bindings: &bindings }, &defks, &fields).iter().map(|c| c.detail()).collect();
        // The carries of fields that carry a Program (prepare, later, qualified) are not picked up. Fields without annotation, object, Any, alias, dict, and positional values are picked up.
        assert_eq!(got, vec!["untyped::fetch", "opaque::fetch", "any_typed::fetch", "hidden::fetch", "data::fetch", "positional::fetch"]);
        // A call that does not know the field types (bare_calls_in) still picks up everything as before.
        assert!(found(source, &[]).contains(&"carries::fetch".to_string()));
    }

    #[test]
    fn bare_calls_are_found_where_the_answer_is_used_as_a_value() {
        let source = r#"(deff text-at [ref] (.get (fetch ref) "text"))
(defn count-rows [] (len (load "a")))
(deff collect [out ref] (.append out (fetch ref)))
(defk checks [ref]
  (when (fetch ref) (return 1))
  (val same (= (fetch ref) ref))
  (val field (. (fetch ref) text))
  (val row (Row :text (fetch ref)))
  (<- later (AnswerLater :answer (fetch ref)))
  (for [x (load "b")] (print x))
  (val xs (lfor y (fetch ref) y))
  (return (+ 1 (if same (fetch 1) 2))))
(val MODULE-LEVEL (str (fetch 0)))"#;
        assert_eq!(found(source, &[("load", "lib")]), vec!["text_at::fetch", "count_rows::load", "collect::fetch", "checks::fetch", "checks::load", "<m>::fetch"]);
    }

    #[test]
    fn programs_that_are_passed_bound_or_returned_are_not_bare() {
        let source = r#"(defk top []
  (<- out (run (with_handlers [h] (fetch 1))))
  (<- many (Gather [(fetch 1) (fetch 2)]))
  (<- task (Spawn (fetch 3)))
  (<- r (result (maybe (fetch 4))))
  (<- chosen (if flag (fetch 6) (load 7)))
  (<- nested (fetch (load 8)))
  (val later (fetch 9))
  (return out))
(deff runner [x] (run-on (fetch x) handlers))
(deff builder [x] (fetch x))
(deff callback [x] (helper x) (len (other.fetch x)) (len (f x)))"#;
        // Arguments of calls that accept a Program, bindings to names, and forms returned as a function's answer are not picked up (they may be passed as a Program).
        // Calls that cannot be traced (helper, method, parameter f) are not picked up either.
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("Gather", "doeff"), ("Spawn", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }

    /// Treats one source as the whole repo, builds the defk names and record table from that source, and picks up bare calls (no module m or imports).
    fn found_in_repo(source: &str) -> Vec<String> {
        let bindings = BTreeMap::new();
        let defks = defk_names_in(source, "m");
        let fields = program_fields_in(source, "m");
        bare_calls_with(source, Scope { module: "m", bindings: &bindings }, &defks, &fields).iter().map(|c| c.detail()).collect()
    }

    /// A form that passes a bare defk call as an argument to a defk call whose receiving parameter's `:pre` type cannot accept a Program (built-in value types, None, repo records) is
    /// picked up — the real case: agora-controllers' job patrol (2026-11-04) `(counts-sources-of (measure-rows-of standing))`.
    /// A Call was passed in, and it first hit the `:pre` type check at run time and failed. Positional arguments, keyword arguments, record type arguments, and inside branches are the same.
    #[test]
    fn bare_defk_calls_passed_where_the_receiving_defk_refuses_programs_are_found() {
        let source = r#"(defrecord StandingState "note" (#^ int n))
(defrecord ContinuingEntry "row" (#^ str word))
(defk measure-rows-of [standing]
  {:pre [(: standing (| StandingState None))] :post [(: % (get tuple #(ContinuingEntry ...)))]}
  #())
(defk counts-sources-of [measures]
  {:pre [(: measures (get tuple #(ContinuingEntry ...)))] :post [(: % tuple)]}
  measures)
(defk fresh-standing [] {:post [(: % StandingState)]} (StandingState :n 1))
(defk stage [standing]
  (<- counts (get tuple #(str ...)) (counts-sources-of (measure-rows-of standing)))
  counts)
(defk by-keyword [standing]
  (<- counts (counts-sources-of :measures (measure-rows-of standing)))
  counts)
(defk by-record []
  (<- rows (measure-rows-of (fresh-standing)))
  rows)
(defk in-branch [standing flag]
  (<- counts (counts-sources-of (if flag (measure-rows-of standing) #())))
  counts)"#;
        assert_eq!(
            found_in_repo(source),
            vec!["stage::measure_rows_of", "by_keyword::measure_rows_of", "by_record::fresh_standing", "in_branch::measure_rows_of"]
        );
    }

    /// A Python class of the repo also refuses a Program — the real case: agora-controllers' screen server (2026-11-10) `(preceded out (_post-trip-start …))`, where `preceded`'s
    /// `:pre` is `(: step Step)` and `Step` is a frozen dataclass of a .py file. A class whose base names Program, EffectBase, DoExpr or Protocol, and an alias assignment, still may take one.
    #[test]
    fn bare_defk_calls_passed_where_a_python_class_is_received_are_found() {
        let types = r#"from dataclasses import dataclass
from typing import Protocol, TypeAlias
from doeff import Program

@dataclass(frozen=True)
class Step:
    state: int

class Plain:
    pass

class Shaped(Protocol):
    def run(self) -> int: ...

class Lazy(Program):
    pass

Either: TypeAlias = Step | Plain
"#;
        let fields = py_value_classes_in(types, "app.types");
        assert!(["app.types.Step", "app.types.Plain"].iter().all(|name| fields.is_record(name)));
        assert!(["app.types.Shaped", "app.types.Lazy", "app.types.Either"].iter().all(|name| !fields.is_record(name)));
        let source = r#"(import app.types [Step Plain Shaped Lazy Either])
(defk preceded [commands step] {:pre [(: commands tuple) (: step Step)] :post [(: % Step)]} step)
(defk plain-of [value] {:pre [(: value Plain)] :post [(: % Plain)]} value)
(defk shaped-of [value] {:pre [(: value Shaped)] :post [(: % Shaped)]} value)
(defk lazy-of [value] {:pre [(: value Lazy)] :post [(: % Lazy)]} value)
(defk either-of [value] {:pre [(: value Either)] :post [(: % Step)]} value)
(defk _trip-start [n] {:pre [(: n int)] :post [(: % Step)]} (Step :state n))
(defk bare [out n]
  (return (! (preceded out (_trip-start n)))))
(defk banged [out n]
  (return (! (preceded out (! (_trip-start n))))))
(defk others [n]
  (plain-of (_trip-start n))
  (shaped-of (_trip-start n))
  (lazy-of (_trip-start n))
  (either-of (_trip-start n)))"#;
        let bindings = crate::project::facts::hy_bindings(source, "m");
        let defks = defk_names_in(source, "m");
        let found: Vec<String> =
            bare_calls_with(source, Scope { module: "m", bindings: &bindings }, &defks, &fields).iter().map(|c| c.detail()).collect();
        assert_eq!(found, vec!["bare::_trip_start", "others::_trip_start"]);
    }

    /// Not picked up when the receiving parameter may accept a Program: a parameter without a type annotation, Program, object, an unknown alias, a union branch with type arguments,
    /// positions after `#*`, forms made into a value with `<-` or `!` before passing, calls where a local `deff` hides a defk of the same spelling, and the head of a defsystem job row (the job's name).
    #[test]
    fn bare_defk_calls_passed_where_the_receiving_defk_may_take_programs_are_not_found() {
        let source = r#"(defrecord Row "row" (#^ str word))
(defk rows-of [n] {:pre [(: n int)] :post [(: % tuple)]} #())
(defk count-of [rows] {:pre [(: rows tuple)]} (len rows))
(defk untyped [body] body)
(defk run-twice [body] {:pre [(: body Program)]} (<- a body) a)
(defk anything [value] {:pre [(: value object)]} value)
(defk aliased [value] {:pre [(: value ProgramLike)]} value)
(defk either [value] {:pre [(: value (| tuple Program))]} value)
(defk spread [first #* rest] {:pre [(: first int) (: rest tuple)]} first)
(defk caller [n]
  (<- a (untyped (rows-of n)))
  (<- b (run-twice (rows-of n)))
  (<- c (anything (rows-of n)))
  (<- d (aliased (rows-of n)))
  (<- e (either (rows-of n)))
  (<- f (spread #* xs (rows-of n)))
  (<- rows (rows-of n))
  (<- g (count-of rows))
  (<- h (count-of (! (rows-of n))))
  #(a b c d e f g h))
(deftest test-local-function-hides-the-defk
  (deff rows-of [n] {:pre [(: n int)] :post [(: % tuple)]} #())
  (<- counted (count-of (rows-of 1)))
  counted)
(defsystem jobs [foundation]
  (rows-of (rows-of foundation) :replicas 1))"#;
        assert!(found_in_repo(source).is_empty(), "{:?}", found_in_repo(source));
    }

    #[test]
    fn bound_bare_calls_used_as_values_and_assert_messages_are_found() {
        // The form of agora's incident_placement.hy (before the fix 9e2574a6^): (.items (histories run)) is a method target,
        // and (get all job) after (setv … all (histories run)) is a place that uses the bound name as an answer. The assert statement (describe …) also uses the answer.
        let source = r#"(deff turn-survives-roll [run]
  (for [[job history] (.items (fetch run))] (print job)))
(deff waits-of-run [run]
  (setv out [] all (fetch run))
  (for [job (.keys all)] (.append out (get all job)))
  out)
(deff via-let [run] (let [rows (fetch run)] (len rows)))
(deftest test-laws (assert (in "TI1" laws) (fetch out.breaches)))
(defk fine [run]
  (setv program (fetch run))
  (<- got (run-on program))
  (val later (fetch run))
  (return later))
(defk rebound [run] (setv x (fetch run)) (<- x (fetch run)) (len x))"#;
        assert_eq!(found(source, &[]), vec!["turn_survives_roll::fetch", "waits_of_run::fetch", "via_let::fetch", "test_laws::fetch"]);
    }

    #[test]
    fn bare_calls_inside_module_level_tables_are_found() {
        // A shrunken form of scripts/land_focus_gate.hy of agora-controllers c6271008a: added a defk tests-of without run to a module top-level table row,
        // a Program got mixed into the table and `--all` failed at startup. Also inside the construction of table rows (`+`, arguments of the repo function entry), outside a definition it is a place that uses the answer.
        let source = r#"(defn entry [path cmds] [path cmds])
(setv SIM-TESTS [(run (fetch "sim"))])
(val TABLE [(entry "a" [(run (fetch "x"))])
            ["b" (+ [(fetch "y") (run (load "z"))] SIM-TESTS)]])
(setv PAIRS #((entry "c" (load "w"))))"#;
        assert_eq!(found(source, &[("load", "lib"), ("run", "doeff")]), vec!["<m>::fetch", "<m>::load"]);
    }

    #[test]
    fn module_level_programs_that_are_run_passed_or_quoted_are_not_bare() {
        // Not picked up even outside definitions: inside run, calls that accept a doeff Program, `<-`, `!`, places that pass a defk as a value (not called),
        // function bodies (fn, fnk, defmacro, defclass methods), and inside quote / quasiquote.
        let source = r#"(setv ANSWER (run (with_handlers [h] (fetch 1))))
(setv DIRECT (doeff.run (fetch 2)))
(setv REGISTRY {"fetch" fetch "load" load})
(setv LATER (fn [x] (fetch x)))
(setv LATER-K (fnk [x] (<- got (fetch x)) (return got)))
(defmacro fetch-all [#* xs] `(lfor x ~xs (fetch x)))
(defclass Holder [] (defn method [self] (fetch 3)))
(setv QUOTED '(fetch 4))
(val REQUESTS (tuple (map run #((fetch 6) (fetch 7)))))
(when (= __name__ "__main__") (run (fetch 5)))"#;
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }

    #[test]
    fn defsystem_job_programs_are_declared_not_bare() {
        // doeff-hy's defsystem expands to a function, and the expression on a job row is the Program value itself of "name -> Program" (not run).
        // A job name of the same form as a defk name (controllers/kanban/entry/writes_service.hy's (fetch (fetch foundation))) is also not a call.
        let source = r#"(defsystem sample-system [foundation]
  "sample system"
  (fetch (fetch foundation 3 1.0) :needs #{"sim"})
  (placer (load foundation) :needs #{"sim"} :environ {"POLL" "5.0"}))
(defsystem wrong-system [foundation]
  (reader (fetch foundation) :environ {"X" (str (load "k"))}))
(val OUTSIDE (fetch 1))"#;
        // Even inside defsystem, bare calls in places that use the answer (arguments of `str`) and bare calls outside definitions are picked up as before.
        assert_eq!(found(source, &[("load", "lib")]), vec!["wrong_system::load", "<m>::fetch"]);
    }
}


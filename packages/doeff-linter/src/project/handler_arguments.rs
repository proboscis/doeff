//! DOEFF142: handler は接続の object や書き換える店を引数で受け取らない(agora-redesign #1189 / #1366 — 元は agora-controllers の
//! 一時の検 handler_arguments_rules.hy・#686)。
//!
//! defhandler の引数の並び `[ … ]` の 1 つずつを分ける:
//!   * `client`    — 型の名の末尾が Client / Connection / Pool / Engine、または名が client / conn / connection / …-client / …-conn
//!   * `container` — 型が可変の入れ物(dict / list / set / bytearray / MutableMapping … — `(get dict …)` を含む)
//!   * `object`    — 型が可変の object: repo の class の索引で frozen でない class(frozen の dataclass・frozen の設定・Enum・
//!                   NamedTuple・defrecord は値)、または索引に無い型(資源の object)
//!   * `named`     — 型の注記が無く、名が店を指す(architecture.hy の `:store-names` / `:store-suffixes`)
//! 値の引数(str / int / … / Path / tuple / frozenset / Callable と frozen の class)は数えない。
//!
//! 判定は渡した file だけで決まる。例外は `object` の「class が frozen か」で、repo の Hy と Python の class の索引を file ごとの
//! キャッシュ(facts_cache)で引く — 1 file の実行でも変わった file だけ読み直す。母集団・店の名・残す理由の註の印は repo の宣言
//! (`:handler-arguments`)から読み、ここには Python の一般の名だけを置く。

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};
use rustpython_ast::{Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use super::architecture::HandlerArguments;
use super::facts::ByteSpan;
use super::paths::glob_matches;

/// 引数の種類(閉じた集合)。
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

/// 当たり 1 件(どの handler の、どの引数)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgFinding {
    pub handler: String,
    pub param: String,
    pub kind: ArgKind,
    /// 型の注記の綴り(無ければ空)。
    pub type_text: String,
    pub span: ByteSpan,
}

impl ArgFinding {
    /// 登録簿の鍵の細目(`<handler>::<引数>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.handler, self.param)
    }
}

/// class の名と frozen か(file ごとのキャッシュに置く)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClassFrozen {
    pub name: String,
    pub frozen: bool,
}

/// class の名 → frozen か(同じ名が frozen でない所に 1 つでもあれば frozen でない)。
pub type ClassIndex = HashMap<String, bool>;

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
/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// 引数 1 つ(名と型の注記)。
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

/// 型の注記 → 見る名の列: 名はそのまま・`(get T …)` は T・`(| A B)` は枝ごと・文字列の型は `|` で分けた枝。
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

/// 引数の並び `[ … ]` → 引数の列(`#^` の注記・`[名 既定値]`・`(annotate 名 型)`・`#*` / `#**` の可変長を読む)。
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

/// 引数 1 つ → 種類(値の引数は None)。
fn kind_of(source: &str, param: &Param, decl: &HandlerArguments, classes: &ClassIndex) -> Option<ArgKind> {
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
    let is_value = |b: &str| VALUE_TYPES.contains(&b) || decl.value_types.iter().any(|v| v == b) || classes.get(b).copied().unwrap_or(false);
    let mutable = bases.iter().zip(&tails).any(|(b, t)| !(is_value(b) || is_value(t)));
    mutable.then_some(ArgKind::Object)
}

/// form の木の中の `(defhandler 名 [引数] …)` を全部(入れ子も)。
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

/// 1 file の当たり。本文(`(defhandler` から次の最上位の form まで — 後ろの註を含む)に残す理由の印が在る handler は数えない。
pub fn findings_in(source: &str, decl: &HandlerArguments, classes: &ClassIndex) -> Vec<ArgFinding> {
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

/// file の綴りが母集団に入るか(:files のどれかに当たり、:exclude のどれにも当たらない)。
pub fn in_population(rel: &str, decl: &HandlerArguments) -> bool {
    rel.ends_with(".hy") && decl.files.iter().any(|p| glob_matches(p, rel)) && !decl.exclude.iter().any(|p| glob_matches(p, rel))
}

/// root の下の file(隠し dir と生成物を歩かない)を拡張子で選ぶ。
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

/// 母集団の file(全体の実行)。
pub fn population(root: &Path, decl: &HandlerArguments) -> Vec<(String, PathBuf)> {
    walk(root, &["hy"]).into_iter().filter(|(rel, _)| in_population(rel, decl)).collect()
}

/// Hy の source の class → frozen か。defclass は decorator の `:frozen True` か、基底が Enum / NamedTuple の類なら値。
/// defrecord(凍結の record)と `(val|setv 名 (get Callable …))` の別名は値。
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

/// Python の source の class → frozen か(frozen=True の decorator・Enum / NamedTuple の基底・本文の frozen の設定)。読めなければ空。
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

/// repo の Hy と Python の class の索引(file ごとのキャッシュ — 変わった file だけ読み直す)。overlay = 1 file の実行の保存前の中身。
pub fn class_index(root: &Path, overlay: Option<(&str, &str)>) -> ClassIndex {
    let files = walk(root, &["hy", "py"]);
    let found: Vec<(String, Vec<ClassFrozen>)> = super::facts_cache::per_file(root, "handler-argument-classes", &files, |rel, path| {
        let source = std::fs::read_to_string(path).ok()?;
        let classes = if rel.ends_with(".hy") { hy_classes(&source) } else { py_classes(&source) };
        Some((rel.to_string(), classes))
    });
    let mut index = ClassIndex::new();
    let mut add = |classes: &[ClassFrozen]| {
        for class in classes {
            let entry = index.entry(class.name.clone()).or_insert(true);
            *entry = *entry && class.frozen;
        }
    };
    for (rel, classes) in &found {
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
            keep_mark: Some("引数に残す理由:".into()),
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
  ;; 引数に残す理由: 検が中身を組んでから渡す店
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
}

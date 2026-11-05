//! DOEFF144: a value with structure is expressed by a type that statically holds field names and types — judges, one file at a time, that the type annotations of the public surface contain no bare mapping or bare tuple
//! (the new home of the judgment of agora-controllers' scripts/check_typed_values.hy. The meaning of the judgment is kept the same as the original; it is neither widened nor narrowed).
//!
//! The files to read exist only in the repo's architecture.hy under `:typed-values {:files [..] :except [..]}` and are not written here. The judgment is decided by reading one file,
//! so no index of the whole repo is built — if a named path (`focus`) exists, only files under it are read; otherwise only the leading
//! dirs of the declared globs are walked (the same reading as DOEFF150 and 151).
//!
//! Public surface = the type annotations at the following 3 places (names starting with `_` are not counted — `Class.field` is looked at by its last segment):
//!   * Class fields: `(#^ T name)`, `#^ T name`, `(setv #^ T name …)` directly under Hy's defclass, defrecord, defwire / `name: T` directly under a Python class.
//!   * Function return values: Hy's `(defn #^ T name …)` (also annotations on the names of defk and deff) and `:post [(: % T)]` of defk and deff / Python's `-> T`.
//!     Top-level functions and class methods are counted; functions nested inside a function are not.
//!   * Answer tuples: the form where a Hy defn, defk, or deff makes a tuple literal `#(a b)` of length 2 or more its answer (the last expression or `(return #(a b))`).
//!
//! Types made red (union branches and the contents of containers are also looked at): bare mappings (dict, Mapping, MutableMapping, and the JSON-value aliases JsonValue, JsonObject …),
//! bare tuples (tuple, Tuple), mappings whose value type is object / Any, fixed-length tuples `tuple[A, B]`, and tuples whose element type is object / Any
//! `tuple[Any, ...]` (this does not count as giving a type). Types not made red: indexes looked up by key `dict[str, Row]`, sequences of the same type `tuple[X, ...]`, and
//! frozen mappings. These names carry Python's type meaning, so the linter holds them. Hy's `(get tuple #(X ...))`, `(of tuple X ...)`, `(of dict K V)`, and
//! `(get dict #(K V))` are read the same way — doeff-hy made this form writable in :pre / :post and fields (#1791).
//!
//! The 3 forms that are not values with structure are decided by the shape of the name, and only that kind of red is excluded (decision Q2-2 of #1762 — Plain, plain_shape):
//! bare tuples and tuple literals for sort keys (names `…key-of`, `sort-key`, `order-key`) and SQL argument lists (`params`, `…-params`),
//! and bare mappings for indexes looked up by key (`…-by-…`). Bare tuples and bare mappings on the public surface under other names still fire as before.
//!
//! `:post` is an isinstance contract and cannot state the types of the contents, so for the `:post` of a defk or deff whose name has a type annotation, only mappings are made red (a bare tuple may be a sequence of the same
//! type). The `:post` of a defk or deff whose name has no annotation is the only type declaration, so bare tuples are made red too.
//!
//! Hy reading is aligned to the same tree as the lexical reading of the original judgment — quote symbols are dropped, `#*` and reader macro tags are listed as one name,
//! `#{…}` is counted as the same brackets as a mapping, and a bracket string is made one string per delimiter.

use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};
use rustpython_ast::{Constant, Expr, Mod, Operator, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FileSelection;
use super::retired::{judge_files, selected};

/// Bare mappings (mappings with no type for the contents). Aliases of JSON values are bare mappings renamed to dict | list | … and dict[str, JsonValue], so they are counted the same.
const BARE_MAPPINGS: &[&str] = &[
    "dict",
    "Dict",
    "Mapping",
    "MutableMapping",
    "typing.Dict",
    "typing.Mapping",
    "typing.MutableMapping",
    "collections.abc.Mapping",
    "collections.abc.MutableMapping",
    "JsonValue",
    "JSONValue",
    "JsonObject",
    "JSONObject",
];
/// Bare tuples.
const BARE_TUPLES: &[&str] = &["tuple", "Tuple", "typing.Tuple"];
/// Types that accept anything (if a mapping's value is one of these, it carries a collection of fields).
const OPEN_VALUES: &[&str] = &["object", "Any", "typing.Any"];
/// Containers whose contents' types are looked at (and the ways of writing a union).
const CONTAINERS: &[&str] = &[
    "list",
    "List",
    "set",
    "Set",
    "frozenset",
    "FrozenSet",
    "Sequence",
    "Iterable",
    "Iterator",
    "FrozenMap",
    "typing.List",
    "typing.Sequence",
    "collections.abc.Sequence",
    "Optional",
    "typing.Optional",
    "Union",
    "typing.Union",
];
/// Heads of nested functions not entered when searching for answer tuples.
const NESTED_FUNCTIONS: &[&str] = &["fn", "defn", "defk", "deff", "fnk"];

/// Shapes that are not values with structure (no point in making them records; decision Q2-2 of #1762). Decided by the shape of the last segment of the name, and only that shape's
/// kind of red is excluded (red of a different kind under the same name still fires). Which shapes are exceptions is written in only one place, this type and plain_shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Plain {
    /// Sort keys (`order-key-of`, `sort-key` — things like `#((- at) subject key)`). Excludes bare tuples and tuple literals.
    SortKey,
    /// SQL argument lists (`params`, `…-params`). Excludes bare tuples and tuple literals.
    SqlArgs,
    /// Indexes looked up by key (`…-by-…` as in `ledger-by-agent`). Excludes bare mappings (a mapping with an open value type still fires).
    Index,
}

impl Plain {
    /// Whether the red reason `problem` is a kind this shape excludes.
    fn covers(self, problem: &str) -> bool {
        match self {
            Plain::SortKey | Plain::SqlArgs => problem.starts_with("bare tuple") || problem.starts_with("fixed-length tuple") || problem.starts_with("tuple literal of length"),
            Plain::Index => problem.starts_with("bare mapping"),
        }
    }
}

/// Name (`Class.name` uses the last segment) -> shape that is not a value with structure (snake names are also looked at aligned to kebab).
fn plain_shape(name: &str) -> Option<Plain> {
    let last = name.rsplit('.').next().unwrap_or(name).replace('_', "-");
    if last.ends_with("key-of") || ["sort-key", "order-key"].iter().any(|k| last == *k || last.ends_with(&format!("-{}", k))) {
        Some(Plain::SortKey)
    } else if last == "params" || last.ends_with("-params") {
        Some(Plain::SqlArgs)
    } else if last.contains("-by-") {
        Some(Plain::Index)
    } else {
        None
    }
}

/// Drops the red reason `problem` of name `name` if it is a kind excluded by the shape that is not a value with structure.
fn unless_plain(name: &str, problem: String) -> Option<String> {
    (!plain_shape(name).is_some_and(|shape| shape.covers(&problem))).then_some(problem)
}

/// Where the annotation is (the kind of the registry key detail, and the word in the notice text).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum What {
    /// A class field.
    Field,
    /// The return annotation of a function or method.
    Return,
    /// The :post of a defk or deff whose name has a type annotation (only mappings are made red).
    Post,
    /// The :post of a defk or deff whose name has no type annotation (bare tuples are made red too).
    PostStrict,
    /// A tuple literal made the answer.
    Pair,
}

impl What {
    /// The kind of the registry key detail.
    pub fn kind(self) -> &'static str {
        match self {
            What::Field => "field",
            What::Return => "return",
            What::Post | What::PostStrict => "post",
            What::Pair => "pair",
        }
    }

    /// The word in the notice text.
    pub fn label(self) -> &'static str {
        match self {
            What::Field => "field",
            What::Return => "return value",
            What::Post => "answer (:post)",
            What::PostStrict => "answer (:post, no annotation on the name)",
            What::Pair => "answer (tuple literal)",
        }
    }
}

/// One hit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypedHit {
    /// The byte range of the annotation that hit.
    pub start: usize,
    pub end: usize,
    /// The name (`Class.name` for class fields and methods).
    pub name: String,
    pub what: What,
    /// The red reason.
    pub problem: String,
}

impl TypedHit {
    /// The detail of the registry key (`<kind>:<name>`).
    pub fn detail(&self) -> String {
        format!("{}:{}", self.what.kind(), self.name)
    }
}

/// The hits of one file read.
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub hits: Vec<TypedHit>,
}

// --- Hy tree (the same shape as the lexical reading of the original judgment) ------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Paren,
    Bracket,
    Tuple,
    Brace,
}

#[derive(Debug, Clone)]
enum Hy {
    /// Symbol, keyword, number (the str of the original judgment).
    Name { text: String, start: usize, end: usize },
    /// String literal (contents).
    Text { value: String, start: usize, end: usize },
    Form { kind: Kind, items: Vec<Hy>, start: usize, end: usize },
}

impl Hy {
    fn span(&self) -> (usize, usize) {
        match self {
            Hy::Name { start, end, .. } | Hy::Text { start, end, .. } | Hy::Form { start, end, .. } => (*start, *end),
        }
    }

    fn name(&self) -> Option<&str> {
        match self {
            Hy::Name { text, .. } => Some(text),
            _ => None,
        }
    }

    fn is(&self, word: &str) -> bool {
        self.name() == Some(word)
    }

    fn form(&self, want: Kind) -> Option<&[Hy]> {
        match self {
            Hy::Form { kind, items, .. } if *kind == want => Some(items),
            _ => None,
        }
    }

    /// The contents of an expression (…) that has contents.
    fn expression(&self) -> Option<&[Hy]> {
        self.form(Kind::Paren).filter(|items| !items.is_empty())
    }

    /// The name of the head of an expression (empty if it is not an expression or the head is not a name).
    fn head(&self) -> &str {
        self.expression().and_then(|items| items[0].name()).unwrap_or("")
    }
}

/// Turns the reader's forms into a list of items of the tree of the original judgment's lexical reading (one form may become 0 items, or 2 or more).
fn lower_into(source: &str, form: &Form, out: &mut Vec<Hy>) {
    let text = |start: usize, end: usize| source.get(start..end).unwrap_or("").to_string();
    let (start, end) = (form.span.start, form.span.end);
    match &form.node {
        Node::Seq { delim, items } => {
            let kind = match delim {
                Delim::Paren => Kind::Paren,
                Delim::Bracket => Kind::Bracket,
                Delim::Tuple => Kind::Tuple,
                Delim::Brace | Delim::Set => Kind::Brace,
            };
            let mut lowered = Vec::new();
            for item in items {
                lower_into(source, item, &mut lowered);
            }
            out.push(Hy::Form { kind, items: lowered, start, end });
        }
        Node::Symbol | Node::Keyword | Node::Number => out.push(Hy::Name { text: text(start, end), start, end }),
        Node::Str { kind: StrKind::Bracket | StrKind::FormatBracket, .. } => out.push(Hy::Text { value: text(start, end), start, end }),
        Node::Str { body, .. } => out.push(Hy::Text { value: text(body.start, body.end), start, end }),
        Node::Prefixed { prefix, inner } => {
            let spelled = match prefix {
                Prefix::Unpack => Some("#*"),
                Prefix::UnpackMapping => Some("#**"),
                Prefix::Quote | Prefix::Quasiquote | Prefix::Unquote | Prefix::UnquoteSplice => None,
            };
            match (spelled, inner) {
                // `#*xs` is one name lexically (two if whitespace is between).
                (Some(_), Some(inner)) if matches!(inner.node, Node::Symbol | Node::Keyword | Node::Number) && inner.span.start == start + spelled.map_or(0, str::len) => {
                    out.push(Hy::Name { text: text(start, inner.span.end), start, end: inner.span.end })
                }
                (Some(spelled), inner) => {
                    out.push(Hy::Name { text: spelled.to_string(), start, end: start + spelled.len() });
                    if let Some(inner) = inner {
                        lower_into(source, inner, out);
                    }
                }
                (None, Some(inner)) => lower_into(source, inner, out),
                (None, None) => {}
            }
        }
        Node::Annotated { annotation, target } => {
            let mut parts = Vec::new();
            for part in [annotation, target].into_iter().flatten() {
                lower_into(source, part, &mut parts);
            }
            if parts.len() >= 2 {
                let mut rest = parts.into_iter();
                let annotation = rest.next().expect("two exist");
                let target = rest.next().expect("two exist");
                let marker = Hy::Name { text: "annotate".to_string(), start, end: start + 2 };
                out.push(Hy::Form { kind: Kind::Paren, items: vec![marker, target, annotation], start, end });
                out.extend(rest);
            } else {
                out.push(Hy::Name { text: "#^".to_string(), start, end: start + 2 });
                out.extend(parts);
            }
        }
        Node::Discarded => {}
        Node::Tagged { inner } => {
            let tag_end = inner.as_ref().map_or(end, |i| i.span.start);
            let tag = text(start, tag_end);
            out.push(Hy::Name { text: tag.trim_end().to_string(), start, end: start + tag.trim_end().len() });
            if let Some(inner) = inner {
                lower_into(source, inner, out);
            }
        }
    }
}

/// Hy source -> list of top-level items (the reason if there is a place that cannot be read).
fn read_hy(source: &str) -> Result<Vec<Hy>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("the Hy reader could not read {} places (first = {:?})", reader.issues.len(), issue));
    }
    let mut out = Vec::new();
    for form in &forms {
        lower_into(source, form, &mut out);
    }
    Ok(out)
}

// --- Type judgment (the part common to Hy and Python) ---------------------------------------------------------------------------

/// Judgment of one name (bare mapping, bare tuple).
fn name_problem(name: &str) -> Option<String> {
    if BARE_MAPPINGS.contains(&name) {
        Some(format!("bare mapping {}", name))
    } else if BARE_TUPLES.contains(&name) {
        Some(format!("bare tuple {}", name))
    } else {
        None
    }
}

/// A type written as a string ("A | B") — looks only at the names of the branches split by `|`.
fn string_type_problem(text: &str) -> Option<String> {
    text.split('|').find_map(|part| name_problem(part.trim()))
}

/// Judgment of a type of the form X[args] (common to Hy and Python).
fn generic_problem<T>(base: &str, args: &[&T], problem: &dyn Fn(&T) -> Option<String>, open: &dyn Fn(&T) -> bool, ellipsis: &dyn Fn(&T) -> bool) -> Option<String> {
    if BARE_MAPPINGS.contains(&base) {
        if args.len() == 2 && open(args[1]) {
            return Some(format!("open-value-type mapping {}[…, object/Any]", base));
        }
        return args.iter().find_map(|a| problem(a));
    }
    if BARE_TUPLES.contains(&base) {
        if args.len() >= 2 && !ellipsis(args[args.len() - 1]) {
            return Some(format!("fixed-length tuple {}[A, B]", base));
        }
        // Even a sequence of the same type tuple[X, ...] does not count as giving a type if the element type is object / Any.
        if args.len() == 2 && open(args[0]) {
            return Some(format!("open-element-type tuple {}[object/Any, ...]", base));
        }
        return args.iter().filter(|a| !ellipsis(a)).find_map(|a| problem(a));
    }
    if CONTAINERS.contains(&base) {
        return args.iter().find_map(|a| problem(a));
    }
    None
}

// --- Hy type judgment --------------------------------------------------------------------------------------------------

/// Whether the mapping's value type is "anything" (object / Any, or a union containing it).
fn hy_open(node: &Hy) -> bool {
    if let Some(name) = node.name() {
        return OPEN_VALUES.contains(&name);
    }
    node.head() == "|" && node.expression().is_some_and(|items| items[1..].iter().any(hy_open))
}

fn hy_ellipsis(node: &Hy) -> bool {
    node.is("...")
}

/// The argument list of the type of (get X #(a b)) / (get X a) / (of X a b).
fn type_args(items: &[Hy]) -> Vec<&Hy> {
    if items[0].is("of") {
        return items[2..].iter().collect();
    }
    if items.len() == 3 {
        if let Some(inner) = items[2].form(Kind::Tuple) {
            return inner.iter().collect();
        }
    }
    items[2..].iter().collect()
}

/// One Hy type annotation -> red reason.
fn hy_type_problem(node: &Hy) -> Option<String> {
    match node {
        Hy::Name { text, .. } => name_problem(text),
        Hy::Text { value, .. } => string_type_problem(value),
        Hy::Form { .. } => {
            let head = node.head();
            let items = node.expression()?;
            if head == "|" {
                return items[1..].iter().find_map(hy_type_problem);
            }
            if (head == "get" || head == "of") && items.len() >= 3 {
                if let Some(base) = items[1].name() {
                    return generic_problem(base, &type_args(items), &hy_type_problem, &hy_open, &hy_ellipsis);
                }
            }
            None
        }
    }
}

// --- Python type judgment ----------------------------------------------------------------------------------------------

/// The name of a Python name or attribute annotation joined by dots (empty otherwise).
fn py_dotted(node: &Expr) -> String {
    match node {
        Expr::Name(name) => name.id.to_string(),
        Expr::Attribute(attribute) => format!("{}.{}", py_dotted(&attribute.value), attribute.attr),
        _ => String::new(),
    }
}

fn is_bit_or(node: &Expr) -> Option<(&Expr, &Expr)> {
    match node {
        Expr::BinOp(op) if op.op == Operator::BitOr => Some((&op.left, &op.right)),
        _ => None,
    }
}

fn py_open(node: &Expr) -> bool {
    match node {
        Expr::Name(_) | Expr::Attribute(_) => OPEN_VALUES.contains(&py_dotted(node).as_str()),
        _ => is_bit_or(node).is_some_and(|(l, r)| py_open(l) || py_open(r)),
    }
}

fn py_ellipsis(node: &Expr) -> bool {
    matches!(node, Expr::Constant(c) if matches!(c.value, Constant::Ellipsis))
}

/// One Python type annotation -> red reason.
fn py_type_problem(node: &Expr) -> Option<String> {
    match node {
        Expr::Name(_) | Expr::Attribute(_) => name_problem(&py_dotted(node)),
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(text) => match parse(text, Mode::Expression, "<annotation>") {
                Ok(Mod::Expression(expression)) => py_type_problem(&expression.body),
                _ => string_type_problem(text),
            },
            _ => None,
        },
        Expr::Subscript(subscript) => {
            let args: Vec<&Expr> = match subscript.slice.as_ref() {
                Expr::Tuple(tuple) => tuple.elts.iter().collect(),
                other => vec![other],
            };
            generic_problem(&py_dotted(&subscript.value), &args, &py_type_problem, &py_open, &py_ellipsis)
        }
        _ => is_bit_or(node).and_then(|(l, r)| py_type_problem(l).or_else(|| py_type_problem(r))),
    }
}

// --- Collecting annotations of the public surface ----------------------------------------------------------------------------------------------

/// One annotation (before judging).
enum Annotation<'a> {
    Hy(&'a Hy),
    /// T of the :post of a defk or deff whose name has a type annotation (only mappings are made red).
    Post(&'a Hy),
    /// The answer of a fixed-length tuple literal (the length).
    Pair(usize),
}

struct Site<'a> {
    name: String,
    annotation: Annotation<'a>,
    what: What,
    span: (usize, usize),
}

impl Site<'_> {
    fn problem(&self) -> Option<String> {
        match &self.annotation {
            Annotation::Hy(node) => hy_type_problem(node),
            Annotation::Post(node) => hy_type_problem(node).filter(|p| !p.starts_with("bare tuple")),
            Annotation::Pair(width) => Some(format!("tuple literal of length {} as the answer (a tuple lining up several meanings)", width)),
        }
    }
}

/// The spelling of an item (the spelling for a name; the source text otherwise).
fn spelled(source: &str, node: &Hy) -> String {
    match node {
        Hy::Name { text, .. } => text.clone(),
        other => {
            let (start, end) = other.span();
            source.get(start..end).unwrap_or("").to_string()
        }
    }
}

/// (annotate name type) -> (name, type, range of the name).
fn annotated<'a>(source: &str, node: &'a Hy) -> Option<(String, &'a Hy, (usize, usize))> {
    let items = node.expression()?;
    (node.head() == "annotate" && items.len() == 3).then(|| (spelled(source, &items[1]), &items[2], items[1].span()))
}

/// The list of T in :post [(: % T)] of a defk or deff.
fn defk_posts(items: &[Hy]) -> Vec<&Hy> {
    let mut out = Vec::new();
    for part in items {
        let Some(map) = part.form(Kind::Brace) else { continue };
        for i in (0..map.len().saturating_sub(1)).step_by(2) {
            let Some(checks) = map[i + 1].form(Kind::Bracket).filter(|_| map[i].is(":post")) else { continue };
            for check in checks {
                if let Some(parts) = check.expression().filter(|p| check.head() == ":" && p.len() == 3 && p[1].is("%")) {
                    out.push(&parts[2]);
                }
            }
        }
    }
    out
}

/// The length of a tuple literal of length 2 or more.
fn pair_width(node: &Hy) -> Option<usize> {
    node.form(Kind::Tuple).map(<[Hy]>::len).filter(|&n| n >= 2)
}

/// The tuple literal that the body of a defn, defk, or deff makes its answer (the last expression and (return #(…))) — nested functions are not looked into.
fn returned_pairs(items: &[Hy]) -> Vec<(usize, (usize, usize))> {
    let body = items.get(3..).unwrap_or(&[]);
    let mut out = Vec::new();
    if let Some(last) = body.last() {
        if let Some(width) = pair_width(last) {
            out.push((width, last.span()));
        }
    }
    let mut stack: Vec<&Hy> = body.iter().collect();
    while let Some(node) = stack.pop() {
        let Hy::Form { items, .. } = node else { continue };
        let head = node.head();
        if NESTED_FUNCTIONS.contains(&head) {
            continue;
        }
        if head == "return" && items.len() == 2 {
            if let Some(width) = pair_width(&items[1]) {
                out.push((width, items[1].span()));
                continue;
            }
        }
        stack.extend(items.iter());
    }
    out
}

/// One defn, defk, or deff -> return annotation, :post, and answer tuple.
fn hy_function_sites<'a>(source: &str, form: &'a Hy) -> Vec<Site<'a>> {
    let head = form.head();
    let Some(items) = form.expression().filter(|items| items.len() >= 2) else { return Vec::new() };
    let named = annotated(source, &items[1]);
    let name = match (&named, items[1].name()) {
        (Some((name, _, _)), _) => name.clone(),
        (None, Some(name)) => name.to_string(),
        (None, None) => return Vec::new(),
    };
    let mut out = Vec::new();
    if let Some((_, node, _)) = &named {
        out.push(Site { name: name.clone(), annotation: Annotation::Hy(node), what: What::Return, span: node.span() });
    }
    if head == "defk" || head == "deff" {
        for t in defk_posts(items) {
            let (annotation, what) = if named.is_some() { (Annotation::Post(t), What::Post) } else { (Annotation::Hy(t), What::PostStrict) };
            out.push(Site { name: name.clone(), annotation, what, span: t.span() });
        }
    }
    for (width, span) in returned_pairs(items) {
        out.push(Site { name: name.clone(), annotation: Annotation::Pair(width), what: What::Pair, span });
    }
    out
}

/// One defclass, defrecord, or defwire -> field annotations and method annotations (names are `Class.name`).
fn hy_class_sites<'a>(source: &str, form: &'a Hy) -> Vec<Site<'a>> {
    let Some(items) = form.expression() else { return Vec::new() };
    let name_at = if items.len() > 2 && matches!(items[1], Hy::Form { .. }) { 2 } else { 1 };
    let Some(class_node) = items.get(name_at) else { return Vec::new() };
    let class = spelled(source, class_node);
    let mut out = Vec::new();
    for part in &items[name_at + 1..] {
        let Some(part_items) = part.expression() else { continue };
        let named = annotated(source, &part_items[0]).or_else(|| annotated(source, part));
        let head = part.head();
        if let Some((name, node, _)) = named {
            out.push(Site { name: format!("{}.{}", class, name), annotation: Annotation::Hy(node), what: What::Field, span: node.span() });
        } else if head == "setv" {
            for arg in &part_items[1..] {
                if let Some((name, node, _)) = annotated(source, arg) {
                    out.push(Site { name: format!("{}.{}", class, name), annotation: Annotation::Hy(node), what: What::Field, span: node.span() });
                }
            }
        } else if matches!(head, "defn" | "defk" | "deff") {
            out.extend(hy_function_sites(source, part).into_iter().map(|site| Site { name: format!("{}.{}", class, site.name), ..site }));
        }
    }
    out
}

/// The hits of a Hy file.
fn hy_hits(source: &str) -> Result<Vec<TypedHit>, String> {
    let forms = read_hy(source)?;
    let mut sites = Vec::new();
    for form in &forms {
        match form.head() {
            "defclass" | "defrecord" | "defwire" => sites.extend(hy_class_sites(source, form)),
            "defn" | "defk" | "deff" => sites.extend(hy_function_sites(source, form)),
            _ => {}
        }
    }
    Ok(sites
        .into_iter()
        .filter_map(|site| {
            let problem = unless_plain(&site.name, site.problem()?)?;
            public(&site.name).then(|| TypedHit { start: site.span.0, end: site.span.1, name: site.name, what: site.what, problem })
        })
        .collect())
}

/// Whether it is a public name (`Class.name` is looked at by the last segment).
fn public(name: &str) -> bool {
    !name.rsplit('.').next().unwrap_or(name).starts_with('_')
}

/// The hits of a Python file.
fn py_hits(source: &str, rel: &str) -> Result<Vec<TypedHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("does not parse into a syntax tree: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out = Vec::new();
    let mut judge = |name: String, what: What, annotation: Option<&Expr>, fallback: (usize, usize)| {
        let Some(node) = annotation else { return };
        if let Some(problem) = py_type_problem(node).filter(|_| public(&name)).and_then(|problem| unless_plain(&name, problem)) {
            let range = node.range();
            let span = if range.is_empty() { fallback } else { (range.start().to_usize(), range.end().to_usize()) };
            out.push(TypedHit { start: span.0, end: span.1, name, what, problem });
        }
    };
    let whole = |range: rustpython_ast::text_size::TextRange| (range.start().to_usize(), range.end().to_usize());
    for statement in &module.body {
        match statement {
            Stmt::ClassDef(class) => {
                for item in &class.body {
                    match item {
                        Stmt::AnnAssign(assign) => {
                            if let Expr::Name(target) = assign.target.as_ref() {
                                judge(format!("{}.{}", class.name, target.id), What::Field, Some(&assign.annotation), whole(assign.range));
                            }
                        }
                        Stmt::FunctionDef(function) => judge(format!("{}.{}", class.name, function.name), What::Return, function.returns.as_deref(), whole(function.range)),
                        Stmt::AsyncFunctionDef(function) => {
                            judge(format!("{}.{}", class.name, function.name), What::Return, function.returns.as_deref(), whole(function.range))
                        }
                        _ => {}
                    }
                }
            }
            Stmt::FunctionDef(function) => judge(function.name.to_string(), What::Return, function.returns.as_deref(), whole(function.range)),
            Stmt::AsyncFunctionDef(function) => judge(function.name.to_string(), What::Return, function.returns.as_deref(), whole(function.range)),
            _ => {}
        }
    }
    Ok(out)
}

/// Judges one file (path rel from the root, and contents) — only Hy and Python files (others are empty). The reason if it cannot be read.
pub fn judge(rel: &str, source: &str) -> Result<Vec<TypedHit>, String> {
    let mut hits = if rel.ends_with(".hy") {
        hy_hits(source)?
    } else if rel.ends_with(".py") {
        py_hits(source, rel)?
    } else {
        Vec::new()
    };
    hits.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(hits)
}

/// Whether it is a file to judge (a Hy or Python file that matches a declared glob).
pub fn wants(rel: &str, selection: &FileSelection) -> bool {
    (rel.ends_with(".hy") || rel.ends_with(".py")) && selected(rel, &selection.files, &selection.except)
}

/// Reads and judges the declared files (if focus exists, only files under it). The reason is returned for a file that cannot be read.
pub fn find(root: &Path, selection: &FileSelection, focus: Option<&[PathBuf]>) -> (Vec<FileHits>, Vec<String>) {
    judge_files(
        root,
        selection.files.iter(),
        focus,
        |rel, _| wants(rel, selection),
        |rel, path, source| {
            let hits = judge(&rel, &source).map_err(|reason| format!("{}: the DOEFF144 judgment cannot be read ({})", rel, reason))?;
            Ok((!hits.is_empty()).then(|| FileHits { rel, path, source, hits }))
        },
    )
}

// --- Layer that passes values only by type (DOEFF170, 171) ----------------------------------------------------------
//
// A layer declared `:wire-free True` (agora's core) has no wire types (defwire) and passes neither mappings nor tuples / sequences with no type for the contents
// (defwire is not placed in core, and mappings are passed only when building the mapping is itself the purpose. A mapping for looking up data is
// made a key -> value function or an effect). Unlike DOEFF144,
// typed mappings (`(of dict str Row)`) also fire (a mapping for looking up data is made a key -> value function or an effect). The spelling whose purpose is building a mapping is placed at the single point of the translation layer,
// so no exception is placed in this layer — except that tuples for sort keys (Plain::SortKey) are not passing values, so they are excluded.

/// Mappings (whether or not they have a type for the contents) — DOEFF144's bare mappings plus the aliases of JSON bodies and frozen mappings.
const WIRE_FREE_MAPPINGS: &[&str] = &["JsonBody", "JSONBody", "OpaqueJson", "FrozenMap"];
/// Sequences that fire when they have no type for the contents (tuples are BARE_TUPLES).
const BARE_LISTS: &[&str] = &["list", "List", "typing.List"];

/// Kind of hit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WireFreeWhat {
    /// A defwire declaration (DOEFF170).
    Wire,
    /// The type of a :pre argument (DOEFF171).
    Pre,
    /// The type of the :post answer (DOEFF171).
    Post,
    /// The type of a defclass or defrecord field (DOEFF171).
    Field,
}

impl WireFreeWhat {
    /// The kind of the registry key detail.
    pub fn kind(self) -> &'static str {
        match self {
            WireFreeWhat::Wire => "wire",
            WireFreeWhat::Pre => "pre",
            WireFreeWhat::Post => "post",
            WireFreeWhat::Field => "field",
        }
    }
}

/// One hit of the layer that passes values only by type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireFreeHit {
    pub start: usize,
    pub end: usize,
    /// Name (the name of a defwire, `definition:argument`, `definition`, `Class.field`).
    pub name: String,
    pub what: WireFreeWhat,
    /// The red reason (empty for defwire).
    pub problem: String,
}

impl WireFreeHit {
    /// The detail of the registry key (`<kind>:<name>`).
    pub fn detail(&self) -> String {
        format!("{}:{}", self.what.kind(), self.name)
    }
}

/// One name — a mapping, or a tuple / sequence with no type for the contents.
fn wire_free_name_problem(name: &str) -> Option<String> {
    if BARE_MAPPINGS.contains(&name) || WIRE_FREE_MAPPINGS.contains(&name) {
        Some(format!("mapping {}", name))
    } else if BARE_TUPLES.contains(&name) || BARE_LISTS.contains(&name) {
        Some(format!("{} with no element type", name))
    } else {
        None
    }
}

/// One type annotation -> red reason (union branches and the contents of containers are also looked at). A mapping fires even if it has a type for the contents.
fn wire_free_type_problem(node: &Hy) -> Option<String> {
    match node {
        Hy::Name { text, .. } => wire_free_name_problem(text),
        Hy::Text { value, .. } => value.split('|').find_map(|part| wire_free_name_problem(part.trim())),
        Hy::Form { .. } => {
            let head = node.head();
            let items = node.expression()?;
            if head == "|" {
                return items[1..].iter().find_map(wire_free_type_problem);
            }
            if !((head == "get" || head == "of") && items.len() >= 3) {
                return None;
            }
            let base = items[1].name()?;
            wire_free_generic(base, &type_args(items), &wire_free_type_problem, &hy_open, &hy_ellipsis)
        }
    }
}

/// One generic type base[args] -> red reason (the same judgment for Hy and Python annotations). A mapping fires even if it has a type for the contents.
fn wire_free_generic<T>(base: &str, args: &[&T], problem: &dyn Fn(&T) -> Option<String>, open: &dyn Fn(&T) -> bool, ellipsis: &dyn Fn(&T) -> bool) -> Option<String> {
    if BARE_MAPPINGS.contains(&base) || WIRE_FREE_MAPPINGS.contains(&base) {
        return Some(format!("mapping {}[…] (a mapping for looking up data is not passed even if it has a type for the contents)", base));
    }
    if BARE_TUPLES.contains(&base) {
        if args.len() >= 2 && !ellipsis(args[args.len() - 1]) {
            return Some(format!("fixed-length tuple {}[A, B]", base));
        }
        if args.first().is_some_and(|a| open(a)) {
            return Some(format!("open-element-type tuple {}[object/Any, ...]", base));
        }
    }
    if BARE_TUPLES.contains(&base) || BARE_LISTS.contains(&base) || CONTAINERS.contains(&base) {
        return args.iter().filter(|a| !ellipsis(a)).find_map(|a| problem(a));
    }
    None
}

/// One Python type annotation -> red reason (the Python version of wire_free_type_problem — a string annotation is re-read as an expression).
fn py_wire_free_type_problem(node: &Expr) -> Option<String> {
    match node {
        Expr::Name(_) | Expr::Attribute(_) => wire_free_name_problem(&py_dotted(node)),
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(text) => match parse(text, Mode::Expression, "<annotation>") {
                Ok(Mod::Expression(expression)) => py_wire_free_type_problem(&expression.body),
                _ => text.split('|').find_map(|part| wire_free_name_problem(part.trim())),
            },
            _ => None,
        },
        Expr::Subscript(subscript) => {
            let args: Vec<&Expr> = match subscript.slice.as_ref() {
                Expr::Tuple(tuple) => tuple.elts.iter().collect(),
                other => vec![other],
            };
            wire_free_generic(&py_dotted(&subscript.value), &args, &py_wire_free_type_problem, &py_open, &py_ellipsis)
        }
        _ => is_bit_or(node).and_then(|(l, r)| py_wire_free_type_problem(l).or_else(|| py_wire_free_type_problem(r))),
    }
}

/// The annotations of the class fields (`Class.field`, annotation) of one Python file in the layer that passes values only by type — all of them, including fields starting with _ and ClassVar (fields are the shape of passing values).
fn py_field_sites(module: &[Stmt]) -> Vec<(String, &Expr)> {
    module
        .iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(class) => Some(class),
            _ => None,
        })
        .flat_map(|class| {
            class.body.iter().filter_map(move |item| match item {
                Stmt::AnnAssign(assign) => match assign.target.as_ref() {
                    Expr::Name(target) => Some((format!("{}.{}", class.name, target.id), assign.annotation.as_ref())),
                    _ => None,
                },
                _ => None,
            })
        })
        .collect()
}

/// Judges one Python file in the layer that passes values only by type (the field types of DOEFF171 — annotations of dataclass, TypedDict, and class fields).
/// Python has neither defwire nor :pre / :post, so only fields are looked at. The reason if it cannot be read.
pub fn py_wire_free_hits(source: &str, rel: &str) -> Result<Vec<WireFreeHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("does not parse into a syntax tree: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out: Vec<WireFreeHit> = py_field_sites(&module.body)
        .into_iter()
        .filter_map(|(name, node)| {
            let problem = py_wire_free_type_problem(node)?;
            let range = node.range();
            Some(WireFreeHit { start: range.start().to_usize(), end: range.end().to_usize(), name, what: WireFreeWhat::Field, problem })
        })
        .collect();
    out.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(out)
}

/// The mapping fields of one Python file outside the layer that passes values only by type (the field smell of DOEFF172). The reason if it cannot be read.
pub fn py_dict_smell_hits(source: &str, rel: &str) -> Result<Vec<DictSmellHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("does not parse into a syntax tree: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out: Vec<DictSmellHit> = py_field_sites(&module.body)
        .into_iter()
        .filter(|(_, node)| !py_relays_opaque_json(node))
        .filter_map(|(name, node)| {
            let problem = py_wire_free_type_problem(node).filter(|problem| problem.starts_with("mapping"))?;
            let range = node.range();
            Some(DictSmellHit { start: range.start().to_usize(), end: range.end().to_usize(), name, what: DictSmellWhat::Field, problem })
        })
        .collect();
    out.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(out)
}

/// The list of (argument, T) of :pre [(: argument T) …] of a defk or deff.
fn defk_pres(items: &[Hy]) -> Vec<(String, &Hy)> {
    let mut out = Vec::new();
    for part in items {
        let Some(map) = part.form(Kind::Brace) else { continue };
        for i in (0..map.len().saturating_sub(1)).step_by(2) {
            let Some(checks) = map[i + 1].form(Kind::Bracket).filter(|_| map[i].is(":pre")) else { continue };
            for check in checks {
                if let Some(parts) = check.expression().filter(|p| check.head() == ":" && p.len() == 3) {
                    if let Some(arg) = parts[1].name() {
                        out.push((arg.to_string(), &parts[2]));
                    }
                }
            }
        }
    }
    out
}

/// The list of (`Class.field`, type annotation) of the fields of one defclass, defrecord, or defwire (method annotations are not included).
fn field_sites<'a>(source: &str, form: &'a Hy) -> Vec<(String, &'a Hy)> {
    hy_class_sites(source, form)
        .into_iter()
        .filter(|site| site.what == What::Field)
        .filter_map(|site| match site.annotation {
            Annotation::Hy(node) => Some((site.name, node)),
            _ => None,
        })
        .collect()
}

/// Judges one Hy file in the layer that passes values only by type (the defwire of DOEFF170 and the :pre / :post / field types of DOEFF171). The reason if it cannot be read.
pub fn wire_free_hits(source: &str) -> Result<Vec<WireFreeHit>, String> {
    let forms = read_hy(source)?;
    let mut out = Vec::new();
    let typed = |name: String, what: WireFreeWhat, node: &Hy| -> Option<WireFreeHit> {
        let problem = wire_free_type_problem(node)?;
        // The answer tuple of a sort-key function (`…key-of`, `sort-key`) is not passing a value (the same name shape as DOEFF144's Plain::SortKey).
        if what == WireFreeWhat::Post && plain_shape(&name) == Some(Plain::SortKey) && !problem.starts_with("mapping") {
            return None;
        }
        let (start, end) = node.span();
        Some(WireFreeHit { start, end, name, what, problem })
    };
    for form in &forms {
        let Some(items) = form.expression() else { continue };
        match form.head() {
            "defwire" => {
                let class = items.get(1).filter(|n| !matches!(n, Hy::Form { .. })).or_else(|| items.get(2));
                if let Some(class) = class {
                    let (start, end) = class.span();
                    out.push(WireFreeHit { start, end, name: spelled(source, class), what: WireFreeWhat::Wire, problem: String::new() });
                }
                // The field types of a defwire are looked at the same as defrecord and defclass (giving a defrecord field
                // a dict is only for when it is really needed. For dict-type fields, all 3 declarations).
                out.extend(field_sites(source, form).into_iter().filter_map(|(name, node)| typed(name, WireFreeWhat::Field, node)));
            }
            "defclass" | "defrecord" => {
                out.extend(field_sites(source, form).into_iter().filter_map(|(name, node)| typed(name, WireFreeWhat::Field, node)));
            }
            "defk" | "deff" | "defn" if items.len() >= 2 => {
                let name = annotated(source, &items[1]).map(|(n, _, _)| n).or_else(|| items[1].name().map(str::to_string));
                let Some(name) = name else { continue };
                out.extend(defk_pres(items).into_iter().filter_map(|(arg, node)| typed(format!("{}:{}", name, arg), WireFreeWhat::Pre, node)));
                out.extend(defk_posts(items).into_iter().filter_map(|node| typed(name.clone(), WireFreeWhat::Post, node)));
            }
            _ => {}
        }
    }
    out.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(out)
}

// --- Smells of where mappings are placed (DOEFF172) ------------------------------------------------------------
//
// agora's data is in the DB and read through effects. In-memory mappings are only for caches, and caches are held by the effect layer (handler).
// So definitions that build a mapping outside a handler and types that hold a mapping in a field are smells (only counted — major, warning). Definitions whose purpose is building a mapping (:post is
// a mapping — the single point that spells at the JSON boundary) and inside defhandler are not counted. Mapping fields in the layer that passes values only by type are seen by DOEFF171 (critical), so field
// smells are only for the other layers.

/// Kinds of smell of where mappings are placed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DictSmellWhat {
    /// The field type is a mapping (defclass, defrecord, defwire).
    Field,
    /// A definition builds a mapping outside a handler (`{…}`, `(dfor …)`, `(dict …)`).
    Built,
}

/// One smell of where mappings are placed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DictSmellHit {
    pub start: usize,
    pub end: usize,
    /// Name (`Class.field` or the definition's name).
    pub name: String,
    pub what: DictSmellWhat,
    /// For a field, the reason of the type; for a build site, the count sentence.
    pub problem: String,
}

impl DictSmellHit {
    /// The detail of the registry key (`field:<Class.field>`, `built:<definition>`).
    pub fn detail(&self) -> String {
        let kind = match self.what {
            DictSmellWhat::Field => "field",
            DictSmellWhat::Built => "built",
        };
        format!("{}:{}", kind, self.name)
    }
}

/// Whether the type annotation is a mapping (whether or not it has a type for the contents).
fn mapping_problem(node: &Hy) -> Option<String> {
    wire_free_type_problem(node).filter(|problem| problem.starts_with("mapping"))
}

/// The terms of `(match subject type body type :if guard body …)` excluding the type positions (subject, guard, body) — the `{…}` of a type does not build a value and only matches a shape, so it is not counted
/// (before, the type `{"type" "call"}` was counted as "building a mapping").
fn match_evaluated(items: &[Hy]) -> Vec<&Hy> {
    let mut out: Vec<&Hy> = items.get(1).into_iter().collect();
    let mut at = 2;
    while at < items.len() {
        // items[at] is a type — skipped. The guard after the following `:if guard` and the bodies after it are expressions, so they are counted.
        at += 1;
        if items.get(at).is_some_and(|item| item.is(":if")) {
            out.extend(items.get(at + 1));
            at += 2;
        }
        out.extend(items.get(at));
        at += 1;
    }
    out
}

/// The number of places that build a mapping in an expression tree — `{…}` (`#{…}` sets are not counted), `(dfor …)`, `(dict …)`. Inside a defhandler and the type positions of match are not counted.
fn built_mappings(source: &str, node: &Hy) -> usize {
    let Hy::Form { kind, items, start, .. } = node else { return 0 };
    let head = node.head();
    if head == "defhandler" {
        return 0;
    }
    if head == "match" && matches!(kind, Kind::Paren) {
        return match_evaluated(items).into_iter().map(|item| built_mappings(source, item)).sum();
    }
    let inner: usize = items.iter().map(|item| built_mappings(source, item)).sum();
    let builds = match kind {
        Kind::Brace => !source.get(*start..).is_some_and(|rest| rest.starts_with("#{")),
        Kind::Paren => head == "dfor" || head == "dict",
        _ => false,
    };
    inner + usize::from(builds)
}

/// Whether it is a contract dict (`{:pre …}`, `{:post …}`, `{:tags …}` — a {…} whose keys start with a contract word).
fn contract_map(item: &Hy) -> bool {
    item.form(Kind::Brace).is_some_and(|map| map.first().is_some_and(|key| key.is(":pre") || key.is(":post") || key.is(":tags")))
}

/// The start of the body of a defk, deff, or defn (the position after the name and arguments, skipping the leading docstring and the contract dict). A docstring can be placed both before and after the contract dict,
/// so the strings and contract dicts lined up at the head are skipped in any order (before, only the {…} right after the arguments was read as the contract,
/// and the contract dict after a docstring was counted as a "mapping built outside a handler").
fn body_start(items: &[Hy]) -> usize {
    let mut at = 3;
    while items.get(at).is_some_and(|item| matches!(item, Hy::Text { .. }) || contract_map(item)) {
        at += 1;
    }
    at
}

/// Names of external shapes (the same closed list as SPELLS in doeff-hy's declarations.hy — only one of these may be declared).
const BOUNDARY_FORMS: &[&str] = &["json", "http", "env", "schema", "records"];

/// The shape name of a boundary declaration (:spells = the single point that spells an external shape, :reads = the single point that reads an external shape into a type) in the contract's :tags. None if absent or outside the list
/// (before, any non-empty value of :spells was accepted).
fn boundary_form_of(items: &[Hy]) -> Option<String> {
    let contract = items.get(3..body_start(items))?.iter().find_map(|item| item.form(Kind::Brace).filter(|_| contract_map(item)))?;
    tags_boundary_form(contract)
}

/// The shape name of the :tags boundary declaration of a head dict (the contract dict, or the head dict of a defwire).
fn tags_boundary_form(head: &[Hy]) -> Option<String> {
    let tags = (0..head.len().saturating_sub(1)).step_by(2).find_map(|i| head[i + 1].form(Kind::Brace).filter(|_| head[i].is(":tags")))?;
    (0..tags.len().saturating_sub(1)).step_by(2).find_map(|i| match &tags[i + 1] {
        Hy::Text { value, .. } if (tags[i].is(":spells") || tags[i].is(":reads")) && BOUNDARY_FORMS.contains(&value.as_str()) => Some(value.clone()),
        _ => None,
    })
}

/// The shape name of the boundary declaration of the head dict of a defwire (the {…} after the name and docstring) (a wire type that has fields of mappings in the contract's external shape
/// does not count the fields if it declares what boundary it is with :spells / :reads. defrecord is a value type, so the declaration has no effect).
fn wire_boundary_form(items: &[Hy]) -> Option<String> {
    items.get(2..)?.iter().filter(|item| !matches!(item, Hy::Text { .. })).take(1).find_map(|item| item.form(Kind::Brace)).and_then(tags_boundary_form)
}

/// Whether it is the name of a sequence type (if the element type is a relay type, the sequence also carries it without reading inside).
const RELAY_SEQUENCES: [&str; 4] = ["tuple", "list", "typing.Tuple", "typing.List"];

/// Whether the field type is exactly the relay type OpaqueJson that does not read inside (including a union with None) — DOEFF172 does not count it as a mapping (the relay form adopted in #2077,
/// #2211, #2068. A union of OpaqueJson and another mapping is counted). A tuple / list whose element type is the relay type (`(get tuple #(OpaqueJson ...))`,
/// `(of tuple OpaqueJson ...)`, `(get list OpaqueJson)`) also only carries zero or more relays, so it is not counted (#2586).
fn relays_opaque_json(node: &Hy) -> bool {
    match node {
        Hy::Name { text, .. } => text == "OpaqueJson" || text.ends_with(".OpaqueJson"),
        _ => match node.expression() {
            // A union is a relay if at least one branch is a relay and the remaining branches are None or non-mapping types (such as records) (#2629 — `(| OpaqueJson Record)`).
            // A union containing a mapping branch (dict etc.) is counted as before.
            Some(items) if node.head() == "|" => {
                let parts = &items[1..];
                parts.iter().any(relays_opaque_json)
                    && parts.iter().all(|part| part.is("None") || relays_opaque_json(part) || mapping_problem(part).is_none())
            }
            Some(items) if (node.head() == "get" || node.head() == "of") && items.len() >= 3 => {
                let elements: Vec<&Hy> = type_args(items).into_iter().filter(|arg| !hy_ellipsis(arg)).collect();
                items[1].name().is_some_and(|base| RELAY_SEQUENCES.contains(&base))
                    && !elements.is_empty()
                    && elements.into_iter().all(relays_opaque_json)
            }
            _ => false,
        },
    }
}

/// Whether a Python annotation is exactly OpaqueJson (including `OpaqueJson | None`, `Optional[OpaqueJson]`, and string annotations).
fn py_relays_opaque_json(node: &Expr) -> bool {
    let is_none = |expr: &Expr| matches!(expr, Expr::Constant(constant) if matches!(constant.value, Constant::None)) || py_dotted(expr) == "None";
    match node {
        Expr::Name(_) | Expr::Attribute(_) => {
            let dotted = py_dotted(node);
            dotted == "OpaqueJson" || dotted.ends_with(".OpaqueJson")
        }
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(text) => matches!(parse(text, Mode::Expression, "<annotation>"), Ok(Mod::Expression(expression)) if py_relays_opaque_json(&expression.body)),
            _ => false,
        },
        Expr::Subscript(subscript) => {
            let base = py_dotted(&subscript.value);
            if matches!(base.as_str(), "Optional" | "typing.Optional") {
                return py_relays_opaque_json(&subscript.slice);
            }
            // tuple[OpaqueJson, ...], list[OpaqueJson] — sequences whose element type is the relay type are also not counted (#2586).
            let elements: Vec<&Expr> = match subscript.slice.as_ref() {
                Expr::Tuple(tuple) => tuple.elts.iter().filter(|elt| !py_ellipsis(elt)).collect(),
                single => vec![single],
            };
            RELAY_SEQUENCES.contains(&base.as_str()) && !elements.is_empty() && elements.into_iter().all(py_relays_opaque_json)
        }
        // A union is a relay if at least one branch is a relay and the other branches are None or non-mapping types (such as records). A union containing a mapping branch is counted.
        _ => is_bit_or(node).is_some_and(|(l, r)| {
            let other_ok = |expr: &Expr| {
                py_relays_opaque_json(expr) || is_none(expr) || py_wire_free_type_problem(expr).is_none_or(|problem| !problem.starts_with("mapping"))
            };
            (py_relays_opaque_json(l) || py_relays_opaque_json(r)) && other_ok(l) && other_ok(r)
        }),
    }
}

/// The smell of where mappings are placed in one Hy file. fields = whether it is outside the layer that passes values only by type (if false, fields are not looked at — fields of that layer are seen by DOEFF171. The :spells
/// declaration also takes effect only outside that layer). The reason if it cannot be read.
pub fn dict_smell_hits(source: &str, fields: bool) -> Result<Vec<DictSmellHit>, String> {
    let forms = read_hy(source)?;
    let mut out = Vec::new();
    for form in &forms {
        let Some(items) = form.expression() else { continue };
        match form.head() {
            "defwire" if fields && wire_boundary_form(items).is_some() => {}
            "defclass" | "defrecord" | "defwire" if fields => {
                out.extend(field_sites(source, form).into_iter().filter(|(_, node)| !relays_opaque_json(node)).filter_map(|(name, node)| {
                    let problem = mapping_problem(node)?;
                    let (start, end) = node.span();
                    Some(DictSmellHit { start, end, name, what: DictSmellWhat::Field, problem })
                }));
            }
            "defk" | "deff" | "defn" if items.len() >= 3 => {
                let name = annotated(source, &items[1]).map(|(n, _, _)| n).or_else(|| items[1].name().map(str::to_string));
                let Some(name) = name else { continue };
                // A definition whose purpose is building a mapping (:post is a mapping) is not counted — the single point of spelling.
                if defk_posts(items).into_iter().any(|node| mapping_problem(node).is_some()) {
                    continue;
                }
                // The single point whose purpose is spelling or reading an external shape (declare :spells / :reads with a shape name in the contract's :tags) is not counted — the line the operator chose (b), only the single point whose purpose is building a dict is allowed
                // (#2515). In the layer that passes values only by type (core), it is counted even if declared (that layer does not spell).
                if fields && boundary_form_of(items).is_some() {
                    continue;
                }
                // The leading docstring and the contract dict are not mapping values (in any order).
                let count: usize = items.get(body_start(items)..).unwrap_or(&[]).iter().map(|item| built_mappings(source, item)).sum();
                if count > 0 {
                    let (start, end) = items[1].span();
                    out.push(DictSmellHit { start, end, name, what: DictSmellWhat::Built, problem: format!("builds a mapping in {} places outside a handler", count) });
                }
            }
            _ => {}
        }
    }
    out.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dict_smell_counts_built_mappings_and_map_fields_outside_their_place() {
        let source = r#"
(defrecord Page (#^ str id) (#^ (of dict str int) counts) (#^ (of tuple str ...) tags))
(defwire PageWire [] (#^ dict extra))
(defk index-of [rows]
  {:pre [(: rows (of list Page))] :post [(: % int)] :tags {:context "x" :role "judgment"}}
  (val by-id (dfor r rows r.id r))
  (len {:a 1 :b #{1 2}}))
(defk dump [page]
  {:pre [(: page Page)] :post [(: % dict)]}
  {"id" page.id})
(defhandler with-cache [effect k]
  (val cache {})
  (k cache))
(defk plain [rows]
  {:pre [(: rows (of list Page))] :post [(: % int)]}
  (len #{1 2}))
"#;
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("readable").into_iter().map(|h| h.detail()).collect() };
        // Fires: mapping fields (defrecord, defwire), a definition that builds a mapping outside a handler (2 places: dfor and {…}).
        // Does not fire: tuple-typed fields, a dump whose :post is a mapping (the single point of spelling), inside defhandler, contract dicts and :tags, sets #{…}.
        assert_eq!(details(true), vec!["field:Page.counts", "field:PageWire.extra", "built:index-of"]);
        // In the layer that passes values only by type, fields are seen by DOEFF171 and so are not counted.
        assert_eq!(details(false), vec!["built:index-of"]);
        let built = dict_smell_hits(source, false).unwrap();
        assert!(built[0].problem.contains("2 places"), "{:?}", built);
    }

    #[test]
    fn dict_smell_skips_match_patterns_but_counts_the_subject_guard_and_body() {
        // The {…} at a match type position does not build a value and only matches a shape (before, it was counted as "building a mapping" — turn_event_codec's
        // detail-of-json). Mappings built in the subject, the `:if` guard, and the body are counted as before.
        let source = r#"
(defk detail-of [value]
  {:pre [(: value dict)] :post [(: % str)] :tags {:context "x" :role "protocol"}}
  (match value
    {"type" "call"} "call"
    {"type" "outcome"} :if (> (len value) 1) "outcome"
    _ "other"))
(defk built-in-the-body [value]
  {:pre [(: value dict)] :post [(: % int)] :tags {:context "x" :role "protocol"}}
  (match value
    {"type" "call"} (len {"a" 1})
    _ 0))
(defk built-in-the-guard [value]
  {:pre [(: value dict)] :post [(: % int)] :tags {:context "x" :role "protocol"}}
  (match value
    {"type" "call"} :if (in "a" (dfor k value k 1)) 1
    _ 0))
(defk built-in-the-subject [rows]
  {:pre [(: rows list)] :post [(: % int)] :tags {:context "x" :role "protocol"}}
  (match (dfor r rows r r)
    {} 0
    _ 1))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["built:built-in-the-body", "built:built-in-the-guard", "built:built-in-the-subject"]);
    }

    #[test]
    fn dict_smell_skips_the_contract_after_a_docstring_and_honours_spells_outside_core() {
        // A contract dict placed after a docstring is not a mapping value (before, only the {…} right after the arguments was read as the contract and was counted).
        // Mappings built in the body are counted whether or not there is a docstring. The :spells of :tags (the declaration of the single point that spells a wire shape) takes effect only outside the layer that passes values only by type.
        let source = r#"
(defk note [n]
  "docstring comes first."
  {:pre [(: n int)] :post [(: % str)] :tags {:context "x" :role "judgment"}}
  f"{n} items")
(defk index-of [rows]
  "A mapping in the body is counted even when the docstring comes first."
  {:pre [(: rows list)] :post [(: % int)] :tags {:context "x" :role "judgment"}}
  (len (dfor r rows r.id r)))
(defk payload-text [row]
  {:pre [(: row Row)] :post [(: % str)] :tags {:context "x" :role "protocol" :spells "json"}}
  (json.dumps {"id" row.id}))
"#;
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("readable").into_iter().map(|h| h.detail()).collect() };
        assert_eq!(details(true), vec!["built:index-of"], "a definition with only a contract dict and a :spells declaration are not counted");
        assert_eq!(details(false), vec!["built:index-of", "built:payload-text"], "in the layer that passes values only by type, :spells is counted even if declared");
    }

    #[test]
    fn dict_smell_honours_spells_and_reads_with_a_form_name_only() {
        // The value of :spells / :reads is the name of an external shape (json / http / env / schema). A declaration without a shape name is counted (failure case 1);
        // in core (the layer that passes values only by type), it is counted even when declared with a shape name (failure case 2).
        let source = r#"
(defk header-map [x]
  {:pre [(: x int)] :post [(: % str)] :tags {:context "x" :role "protocol" :spells "http"}}
  (str {"a" x}))
(defk read-row [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "x" :role "protocol" :reads "json"}}
  (len {"a" x}))
(defk env-map [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "x" :role "entry" :spells "env"}}
  (len {"A" x}))
(defk where-of [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "x" :role "protocol" :spells "records"}}
  (len {"a" x}))
(defk yaml-map [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "x" :role "protocol" :spells "yaml"}}
  (len {"a" x}))
(defk empty-reads [x]
  {:pre [(: x int)] :post [(: % int)] :tags {:context "x" :role "protocol" :reads ""}}
  (len {"a" x}))
"#;
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("readable").into_iter().map(|h| h.detail()).collect() };
        assert_eq!(details(true), vec!["built:yaml-map", "built:empty-reads"], "only a declaration with a shape name is not counted");
        assert_eq!(
            details(false),
            vec!["built:header-map", "built:read-row", "built:env-map", "built:where-of", "built:yaml-map", "built:empty-reads"],
            "counted even when declared in core"
        );
    }

    #[test]
    fn dict_smell_does_not_count_opaque_json_relay_fields() {
        // A field of the relay type OpaqueJson that does not read inside (including a union with None) is not counted as a mapping. Fields of mappings other than OpaqueJson and
        // unions of OpaqueJson and another mapping are counted (failure case 3).
        let source = r#"
(defrecord Relay (#^ OpaqueJson body) (#^ (| OpaqueJson None) extra) (#^ dict index) (#^ (| OpaqueJson dict) mixed) (#^ JsonBody raw))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Relay.index", "field:Relay.mixed", "field:Relay.raw"]);
        let py = "class Relay:\n    body: OpaqueJson\n    extra: OpaqueJson | None\n    later: 'Optional[OpaqueJson]'\n    index: dict[str, int]\n    mixed: OpaqueJson | dict\n";
        let py_found: Vec<String> = py_dict_smell_hits(py, "x.py").expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(py_found, vec!["field:Relay.index", "field:Relay.mixed"]);
    }

    #[test]
    fn dict_smell_does_not_count_sequences_of_opaque_json_relays() {
        // A tuple / list whose element type is the relay type OpaqueJson (only carries zero or more JSON that is not read inside) is not counted as a mapping.
        // Mapping fields and sequences whose element type is a union of OpaqueJson and a mapping are counted as before (failure case).
        let source = r#"
(defrecord Docs (#^ (get tuple #(OpaqueJson ...)) documents) (#^ (of tuple OpaqueJson ...) more) (#^ (get list OpaqueJson) listed)
  (#^ (| (get tuple #(OpaqueJson ...)) None) maybe) (#^ dict index) (#^ (get tuple #((| OpaqueJson dict) ...)) mixed))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Docs.index", "field:Docs.mixed"]);
        let py = "class Docs:\n    documents: tuple[OpaqueJson, ...]\n    listed: list[OpaqueJson]\n    maybe: 'tuple[OpaqueJson, ...] | None'\n    index: dict[str, int]\n    mixed: tuple[OpaqueJson | dict, ...]\n";
        let py_found: Vec<String> = py_dict_smell_hits(py, "x.py").expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(py_found, vec!["field:Docs.index", "field:Docs.mixed"]);
    }

    #[test]
    fn dict_smell_does_not_count_unions_of_opaque_json_and_records() {
        // PostSettled.result: a union of the relay type OpaqueJson and a non-mapping type (record) is not
        // counted as a mapping. A union containing a mapping branch, and a union of mappings with no relay branch, are counted as before (failure case).
        let source = r#"
(defrecord Settled (#^ (| OpaqueJson IntakeRecord) result) (#^ (| OpaqueJson IntakeRecord None) maybe) (#^ (| IntakeRecord OpaqueJson) flipped)
  (#^ (| OpaqueJson dict) mixed) (#^ (| IntakeRecord dict) plain))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Settled.mixed", "field:Settled.plain"]);
        let py = "class Settled:\n    result: OpaqueJson | IntakeRecord\n    maybe: 'OpaqueJson | IntakeRecord | None'\n    flipped: IntakeRecord | OpaqueJson\n    mixed: OpaqueJson | dict[str, int]\n    plain: IntakeRecord | dict[str, int]\n";
        let py_found: Vec<String> = py_dict_smell_hits(py, "x.py").expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(py_found, vec!["field:Settled.mixed", "field:Settled.plain"]);
    }

    #[test]
    fn dict_smell_honours_a_boundary_named_defwire_only() {
        // A defwire that has a field of a mapping in the contract's external shape is not counted if it declares
        // :spells / :reads with a shape name in its head dict's :tags. A defwire that does not declare, a declaration that is not a shape name, and defrecord fields are counted.
        let source = r#"
(defwire Usage "A free-key index to publish." {:tags {:context "x" :role "type" :spells "json"} :names :camel} (#^ (of dict str int) counts))
(defwire Plain "No declaration." {:tags {:context "x" :role "type"} :names :camel} (#^ (of dict str int) counts))
(defwire Yaml "Not a shape name." {:tags {:context "x" :role "type" :spells "yaml"}} (#^ (of dict str int) counts))
(defrecord Rec "A value type." {:tags {:context "x" :role "type" :spells "json"}} (#^ (of dict str int) counts))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Plain.counts", "field:Yaml.counts", "field:Rec.counts"]);
    }

    /// The list of details of the hits of the layer that passes values only by type.
    fn wire_free(source: &str) -> Vec<String> {
        wire_free_hits(source).expect("readable").into_iter().map(|h| h.detail()).collect()
    }

    #[test]
    fn wire_free_layer_names_defwire_and_maps_in_contracts_and_fields() {
        let source = r#"
(defwire RowWire [] (#^ str name))
(defrecord Row (#^ str name) (#^ dict extra) (#^ (of tuple str ...) tags) (#^ (of list Row) rows))
(defk pick [rows index body raw opaque]
  {:pre [(: rows (of list Row)) (: index (of dict str Row)) (: body JsonBody) (: raw tuple) (: opaque OpaqueJson)]
   :post [(: % (| dict None))]}
  rows)
(defk counted [rows]
  {:pre [(: rows (of tuple Row ...))] :post [(: % int)]}
  1)
(defk order-key-of [row]
  {:pre [(: row Row)] :post [(: % tuple)]}
  #(row.name))
"#;
        assert_eq!(
            wire_free(source),
            vec!["wire:RowWire", "field:Row.extra", "pre:pick:index", "pre:pick:body", "pre:pick:raw", "pre:pick:opaque", "post:pick"]
        );
    }

    #[test]
    fn wire_free_layer_lets_typed_records_and_sequences_through() {
        // Examples that do not hit: typed defrecord, a sequence of the same type, str arguments, tuples of sort keys.
        let source = "(defrecord Row (#^ str name) (#^ (of tuple str ...) tags))\n(defk names [rows]\n  {:pre [(: rows (of list Row))] :post [(: % (of tuple str ...))]}\n  #())\n";
        assert!(wire_free(source).is_empty(), "{:?}", wire_free(source));
    }

    #[test]
    fn python_fields_in_the_wire_free_layer() {
        // A .py class field — typed mappings, string annotations, inside Optional, tuples with no element type, and frozen mappings fire,
        // and types with names and field types and sequences of the same type do not. Fields whose names start with _ are fields too, so they are looked at (different from DOEFF144's narrowing of the public surface).
        let source = "from typing import Mapping, Optional\n\nclass View:\n    rows: Mapping[str, 'Row']\n    meta: 'dict[str, int]'\n    \
                      maybe: Optional[dict]\n    pair: tuple\n    _cache: FrozenMap\n    ids: tuple[str, ...]\n    row: Row\n    \
                      def method(self) -> dict:\n        return {}\n";
        let found: Vec<String> = py_wire_free_hits(source, "core/view.py").expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:View.rows", "field:View.meta", "field:View.maybe", "field:View.pair", "field:View._cache"]);
        let smells: Vec<String> = py_dict_smell_hits(source, "entry/view.py").expect("readable").into_iter().map(|h| h.detail()).collect();
        assert_eq!(smells, vec!["field:View.rows", "field:View.meta", "field:View.maybe", "field:View._cache"], "172 looks at mapping fields only (tuples are not counted)");
    }

/// (detail, head of the reason) list.
    fn hits(rel: &str, source: &str) -> Vec<(String, String)> {
        judge(rel, source).expect("readable").into_iter().map(|h| (h.detail(), h.problem)).collect()
    }

    fn details(rel: &str, source: &str) -> Vec<String> {
        let mut out: Vec<String> = hits(rel, source).into_iter().map(|(d, _)| d).collect();
        out.sort();
        out
    }

    #[test]
    fn hy_fields_of_defclass_defrecord_defwire_in_all_three_spellings() {
        let source = "(defclass Row []\n  (#^ dict meta)\n  #^ Mapping extra\n  (setv #^ tuple pair None #^ str ok \"x\")\n  (#^ str name))\n\
                      (defrecord Rec (#^ JsonValue body) (#^ (get dict #(str Row)) index))\n\
                      (defwire Wire (#^ (get dict #(str object)) raw))\n\
                      (defclass [(dataclass :frozen True)] Deco [] (#^ Dict d))\n";
        assert_eq!(details("a.hy", source), vec!["field:Deco.d", "field:Rec.body", "field:Row.extra", "field:Row.meta", "field:Row.pair", "field:Wire.raw"]);
    }

    #[test]
    fn hy_flagged_and_allowed_types() {
        let flagged = [
            "dict",
            "Mapping",
            "MutableMapping",
            "typing.Dict",
            "JSONObject",
            "tuple",
            "Tuple",
            "(get dict #(str Any))",
            "(get dict #(str (| int object)))",
            "(get tuple #(str int))",
            "(get list dict)",
            "(of list (get tuple #(int str)))",
            "(| str None dict)",
            "\"Row | dict\"",
            "(get Optional tuple)",
        ];
        for t in flagged {
            let source = format!("(defn #^ {} f [] 1)\n", t);
            assert_eq!(details("a.hy", &source), vec!["return:f"], "red: {}", t);
        }
        let allowed = [
            "str",
            "(get dict #(str Row))",
            "(get tuple #(Row ...))",
            "(get tuple #(str ...))",
            "FrozenMap",
            "(get FrozenMap #(str Row))",
            "(get list Row)",
            "(| str None)",
            "\"Row | None\"",
            "(get Custom dict)",
        ];
        for t in allowed {
            let source = format!("(defn #^ {} f [] 1)\n", t);
            assert!(details("a.hy", &source).is_empty(), "green: {}", t);
        }
    }

    #[test]
    fn post_reds_only_mappings_when_the_name_is_annotated_and_tuples_too_when_not() {
        let source = "(defk plain [x] {:pre [(: x int)] :post [(: % tuple)]} x)\n\
                      (defk mapped [x] {:post [(: % dict)]} x)\n\
                      (deff #^ Row named [x] {:post [(: % tuple)]} x)\n\
                      (deff #^ Row named-map [x] {:post [(: % Mapping)]} x)\n\
                      (defn plain-defn [x] {:post [(: % dict)]} x)\n";
        let found = hits("a.hy", source);
        let mut keys: Vec<&str> = found.iter().map(|(d, _)| d.as_str()).collect();
        keys.sort();
        assert_eq!(keys, vec!["post:mapped", "post:named-map", "post:plain"], "the :post of defn is not read; a bare tuple in a :post whose name has an annotation is green");
        let what: Vec<What> = judge("a.hy", source).unwrap().iter().map(|h| h.what).collect();
        assert!(what.contains(&What::PostStrict) && what.contains(&What::Post));
    }

    #[test]
    fn returned_pair_literals_are_red_but_nested_functions_are_not() {
        let source = "(defk pair [x] {:post [(: % Row)]} (when x (return #(1 2))) #(x x))\n\
                      (defn one [x] #(x))\n\
                      (defn nested [x] (defn inner [] #(1 2)) (fn [] (return #(3 4))) x)\n\
                      (defn early [x] (if x (return #(1 2 3)) None))\n";
        assert_eq!(details("a.hy", source), vec!["pair:early", "pair:pair", "pair:pair"]);
    }

    #[test]
    fn private_names_are_not_counted_and_methods_are() {
        let source = "(defn #^ dict _hidden [] 1)\n(defclass _Private [] (#^ dict shown))\n(defclass Row [] (#^ dict _inner)\n  (defn #^ tuple pair [self] 1)\n  (defk _secret [self] {:post [(: % dict)]} 1))\n";
        assert_eq!(details("a.hy", source), vec!["field:_Private.shown", "return:Row.pair"], "_ is looked at by the last segment");
    }

    #[test]
    fn discarded_and_quoted_forms_follow_the_lexical_reading() {
        let source = "#_(defn #^ dict gone [] 1)\n'(defn #^ dict quoted [] 1)\n(defn #^ dict ok [] \"s\") ; (defn #^ dict comment [] 1)\n";
        assert_eq!(details("a.hy", source), vec!["return:ok", "return:quoted"]);
    }

    #[test]
    fn python_fields_returns_methods_but_not_nested_or_private() {
        let source = "from typing import Any\n\
                      class Row:\n    meta: dict[str, Any]\n    index: dict[str, Row]\n    _hidden: dict\n    def pair(self) -> tuple[int, str]: ...\n    async def rows(self) -> list[dict]: ...\n\
                      def top() -> 'Row | dict':\n    def inner() -> dict: ...\n    return inner\n\
                      def fine() -> tuple[int, ...]: ...\n\
                      def opt() -> Optional[Mapping]: ...\n\
                      def union() -> str | typing.Tuple: ...\n\
                      def unknown() -> 'dict | [': ...\ndef unread() -> 'dict[': ...\n\
                      if True:\n    def guarded() -> dict: ...\n";
        assert_eq!(
            details("a.py", source),
            vec!["field:Row.meta", "return:Row.pair", "return:Row.rows", "return:opt", "return:top", "return:union", "return:unknown"]
        );
    }

    #[test]
    fn unreadable_files_are_reasons_not_silence() {
        assert!(judge("a.hy", "(defn #^ dict f [] 1").is_err());
        assert!(judge("a.py", "def f(:\n").is_err());
        assert!(judge("a.md", "dict").unwrap().is_empty(), "only Hy and Python files are read");
    }

    // --- Shapes that are not values with structure ---------------------------------------------------------------

    #[test]
    fn a_sort_key_tuple_is_not_flagged() {
        // Bare tuples in the :post of sort keys, and tuple literals as answers, do not fire (in both Hy and Python).
        let source = "(defk order-key-of [item]\n  {:pre [] :post [(: % tuple)]}\n  #((- item.at) item.key))\n\
                      (defk activity-sort-key [item]\n  {:pre [] :post [(: % tuple)]}\n  #(item.at item.key))\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
        assert_eq!(details("a.py", "def order_key_of(item) -> tuple:\n    return (item.at, item.key)\n"), Vec::<String>::new());
    }

    #[test]
    fn sql_arguments_in_order_are_not_flagged() {
        // Bare tuples of SQL argument lists (the answers of fields params and …-params) do not fire.
        let source = "(defrecord InList (#^ str text) (#^ tuple params))\n\
                      (defk select-params [key]\n  {:pre [] :post [(: % tuple)]}\n  #(key 1))\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn an_index_by_key_is_not_flagged() {
        // Bare mappings of indexes looked up by key (…-by-…) do not fire.
        let source = "(defk ledger-by-agent [entries]\n  {:pre [] :post [(: % dict)]}\n  {})\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
        assert_eq!(details("a.py", "def rows_by_key(rows) -> dict:\n    return {}\n"), Vec::<String>::new());
    }

    #[test]
    fn a_public_return_tuple_and_a_record_field_dict_still_flag() {
        // Counterexample: bare tuples as public return values with names that are not one of the 3 shapes, and bare mappings in record fields, still fire as before.
        let source = "(defk decide [x]\n  {:pre [] :post [(: % tuple)]}\n  #(x 1))\n\
                      (defrecord Charge (#^ dict meta))\n";
        assert_eq!(details("a.hy", source), vec!["field:Charge.meta", "pair:decide", "post:decide"]);
        assert_eq!(details("a.py", "def load() -> tuple:\n    return (1, 2)\n"), vec!["return:load"]);
    }

    #[test]
    fn a_plain_shape_name_still_flags_the_other_kind() {
        // Counterexample: even with the shape's name, red of a kind not excluded fires — a bare tuple under an index name, a bare mapping under a sort-key name, and a mapping with an open value type under an index.
        let source = "(defk rows-by-key [rows]\n  {:pre [] :post [(: % tuple)]}\n  rows)\n\
                      (defk order-key-of [item]\n  {:pre [] :post [(: % dict)]}\n  {})\n\
                      (defrecord View (#^ (get dict #(str object)) items-by-id))\n";
        assert_eq!(details("a.hy", source), vec!["field:View.items-by-id", "post:order-key-of", "post:rows-by-key"]);
    }

    // --- Generic types with element types (the form of doeff-hy's #1790) -------------------------------------------

    #[test]
    fn element_typed_tuple_in_post_is_not_flagged() {
        // Examples that do not fire: a sequence of the same type with an element type written in :post (two spellings, get and of, and inside a union).
        let source = "(defk names [n]\n  {:pre [] :post [(: % (get tuple #(str ...)))]}\n  n)\n\
                      (defk ids [n]\n  {:pre [] :post [(: % (of tuple int ...))]}\n  n)\n\
                      (defk maybe [n]\n  {:pre [] :post [(: % (| (get tuple #(str ...)) None))]}\n  n)\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn key_and_value_typed_dict_field_is_not_flagged() {
        // Examples that do not fire: a mapping with key and value types written in a record field and :post.
        let source = "(defrecord Index (#^ (of dict str int) counts) (#^ (get dict #(str Row)) rows))\n\
                      (defk totals [n]\n  {:pre [] :post [(: % (of dict str int))]}\n  n)\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn bare_tuple_in_post_still_flags() {
        // Example that fires: the bare tuple in the :post of a defk whose name has no annotation (no element type).
        let source = "(defk pairs [n]\n  {:pre [] :post [(: % tuple)]}\n  n)\n";
        assert_eq!(hits("a.hy", source), vec![("post:pairs".to_string(), "bare tuple tuple".to_string())]);
    }

    #[test]
    fn open_element_generics_still_flag() {
        // Examples that fire: a generic type whose element type is Any / object is not giving a type (tuple elements, mapping values) — in both Hy and Python.
        let source = "(defk loose [n]\n  {:pre [] :post [(: % (of tuple Any ...))]}\n  n)\n\
                      (defrecord Bag (#^ (get tuple #(object ...)) items) (#^ (of dict str Any) extra))\n";
        assert_eq!(details("a.hy", source), vec!["field:Bag.extra", "field:Bag.items", "post:loose"]);
        assert_eq!(details("a.py", "def load() -> tuple[Any, ...]:\n    return ()\n"), vec!["return:load"]);
    }
}

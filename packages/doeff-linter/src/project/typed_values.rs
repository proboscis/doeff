//! DOEFF144: 構造を持つ値は欄の名前と型を静的に持つ型で表す — 公開面の型の注記に素の写像・素の組が無いことを file 1 つずつ判じる
//! (agora-redesign #1191 — agora-controllers の scripts/check_typed_values.hy の判定の移し先。判定の意味は元と同じにし、広げも狭めもしない)。
//!
//! 読む file は repo の architecture.hy の `:typed-values {:files [..] :except [..]}` にだけ在り、ここには書かない。判定は file 1 つを
//! 読めば決まるので repo 全体の索引を組まない — 名指しの path(`focus`)が在ればその下の file だけを読み、無ければ宣言の glob の頭の
//! dir だけを歩く(DOEFF150・151 と同じ読み)。
//!
//! 公開面 = 次の 3 か所の型の注記(名が `_` で始まる物は数えない — `Class.欄` は最後の段の名で見る):
//!   * class の欄: Hy の defclass・defrecord・defwire の直下の `(#^ T 名)`・`#^ T 名`・`(setv #^ T 名 …)`/ Python の class の直下の `名: T`。
//!   * 関数の戻り値: Hy の `(defn #^ T 名 …)`(defk・deff の名の注記も)と defk・deff の `:post [(: % T)]` / Python の `-> T`。
//!     最上位の関数と class の method を数え、関数の中の入れ子の関数は数えない。
//!   * 答えの組: Hy の defn・defk・deff が長さ 2 以上の組の literal `#(a b)` を答えにする形(最後の式か `(return #(a b))`)。
//!
//! 赤にする型(union の枝・入れ物の中身も見る): 素の写像(dict・Mapping・MutableMapping と JSON の値の別名 JsonValue・JsonObject …)・
//! 素の組(tuple・Tuple)・値の型が object / Any の写像・長さの決まった組 `tuple[A, B]`・要素の型が object / Any の組
//! `tuple[Any, ...]`(型を付けたことにならない)。赤にしない型: キーで引く索引 `dict[str, Row]`・同じ型の列 `tuple[X, ...]`・
//! 凍らせた写像。これらの名は Python の型の意味なので linter が持つ。Hy の `(get tuple #(X ...))`・`(of tuple X ...)`・`(of dict K V)`・
//! `(get dict #(K V))` も同じに読む — doeff-hy がこの形を :pre / :post と欄に書けるようにした(agora-redesign #1790・#1791)。
//!
//! 構造を持つ値ではない 3 つの形は、名の形で決めてその種類の赤だけを外す(agora-redesign #1792・#1762 の決定 Q2-2 — Plain・plain_shape):
//! 並べ替えのキー(名が `…key-of`・`sort-key`・`order-key`)と SQL の引数の並び(`params`・`…-params`)の素の組・組の literal、
//! キーで引く索引(`…-by-…`)の素の写像。それ以外の名の公開面の素の組・素の写像は今までどおり鳴る。
//!
//! `:post` は isinstance の契約で中身の型を書けないので、名に型の注記の在る defk・deff の `:post` は写像だけを赤にする(素の組は同じ型の
//! 列かもしれない)。名に注記の無い defk・deff の `:post` は唯一の型の宣言なので、素の組も赤にする。
//!
//! Hy の読みは元の判定の字句の読みと同じ木にそろえる — quote の記号は捨て、`#*`・reader macro の tag は 1 つの名として並べ、
//! `#{…}` は写像と同じ括弧に数え、bracket 文字列は区切りごと 1 つの文字列にする。

use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};
use rustpython_ast::{Constant, Expr, Mod, Operator, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FileSelection;
use super::retired::{judge_files, selected};

/// 素の写像(中身の型の無い写像)。JSON の値の別名は dict | list | … と dict[str, JsonValue] に名を替えた素の写像なので同じに数える。
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
/// 素の組。
const BARE_TUPLES: &[&str] = &["tuple", "Tuple", "typing.Tuple"];
/// 何でも入る値の型(写像の値がこれなら欄の集まりを運ぶ物)。
const OPEN_VALUES: &[&str] = &["object", "Any", "typing.Any"];
/// 中身の型を見る入れ物(と union の書き方)。
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
/// 答えの組を探す時に入らない入れ子の関数の頭。
const NESTED_FUNCTIONS: &[&str] = &["fn", "defn", "defk", "deff", "fnk"];

/// 構造を持つ値ではない形(record にする意味が無い — agora-redesign #1792・#1762 の決定 Q2-2)。名の最後の段の形で決め、その形の
/// 赤の種類だけを外す(同じ名でも別の種類の赤は鳴る)。どの形が例外かはこの型と plain_shape の 1 か所にだけ書く。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Plain {
    /// 並べ替えのキー(`order-key-of`・`sort-key` — `#((- at) subject key)` の類)。素の組と組の literal を外す。
    SortKey,
    /// SQL の引数の並び(`params`・`…-params`)。素の組と組の literal を外す。
    SqlArgs,
    /// キーで引く索引(`ledger-by-agent` の `…-by-…`)。素の写像を外す(値の型が開いた写像は鳴る)。
    Index,
}

impl Plain {
    /// 赤の理由 problem がこの形の外す種類か。
    fn covers(self, problem: &str) -> bool {
        match self {
            Plain::SortKey | Plain::SqlArgs => problem.starts_with("素の組") || problem.starts_with("長さ"),
            Plain::Index => problem.starts_with("素の写像"),
        }
    }
}

/// 名(`Class.名` は最後の段)→ 構造を持つ値ではない形(snake の名も kebab に揃えて見る)。
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

/// 名 name の赤の理由 problem を、構造を持つ値ではない形の外す種類なら落とす。
fn unless_plain(name: &str, problem: String) -> Option<String> {
    (!plain_shape(name).is_some_and(|shape| shape.covers(&problem))).then_some(problem)
}

/// 注記の在り場所(登録簿の鍵の細目の種類と、知らせの文の語)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum What {
    /// class の欄。
    Field,
    /// 関数・method の戻り値の注記。
    Return,
    /// 名に型の注記の在る defk・deff の :post(写像だけを赤にする)。
    Post,
    /// 名に型の注記の無い defk・deff の :post(素の組も赤にする)。
    PostStrict,
    /// 答えにする組の literal。
    Pair,
}

impl What {
    /// 登録簿の鍵の細目の種類。
    pub fn kind(self) -> &'static str {
        match self {
            What::Field => "field",
            What::Return => "return",
            What::Post | What::PostStrict => "post",
            What::Pair => "pair",
        }
    }

    /// 知らせの文の語。
    pub fn label(self) -> &'static str {
        match self {
            What::Field => "欄",
            What::Return => "戻り値",
            What::Post => "答え(:post)",
            What::PostStrict => "答え(:post・名の注釈なし)",
            What::Pair => "答え(組の literal)",
        }
    }
}

/// 当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypedHit {
    /// 当たった注記の byte の範囲。
    pub start: usize,
    pub end: usize,
    /// 名(class の欄と method は `Class.名`)。
    pub name: String,
    pub what: What,
    /// 赤の理由。
    pub problem: String,
}

impl TypedHit {
    /// 登録簿の鍵の細目(`<種類>:<名>`)。
    pub fn detail(&self) -> String {
        format!("{}:{}", self.what.kind(), self.name)
    }
}

/// 読んだ file 1 つの当たり。
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub hits: Vec<TypedHit>,
}

// --- Hy の木(元の判定の字句の読みと同じ形)------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Paren,
    Bracket,
    Tuple,
    Brace,
}

#[derive(Debug, Clone)]
enum Hy {
    /// 記号・keyword・数(元の判定の str)。
    Name { text: String, start: usize, end: usize },
    /// 文字列の literal(中身)。
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

    /// 式 (…) で中身が在る物の中身。
    fn expression(&self) -> Option<&[Hy]> {
        self.form(Kind::Paren).filter(|items| !items.is_empty())
    }

    /// 式の頭の名(式でない・頭が名でない時は空)。
    fn head(&self) -> &str {
        self.expression().and_then(|items| items[0].name()).unwrap_or("")
    }
}

/// 読み取り器の form を、元の判定の字句の読みの木の項目の列にする(1 つの form が 0 個か 2 個以上の項目になることもある)。
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
                // `#*xs` は字句の上で 1 つの名(空白を挟めば 2 つ)。
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
                let annotation = rest.next().expect("2 つ在る");
                let target = rest.next().expect("2 つ在る");
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

/// Hy の source → 最上位の項目の列(読めない所が在れば理由)。
fn read_hy(source: &str) -> Result<Vec<Hy>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("Hy の読み取り器が読めない所が {} か所(最初 = {:?})", reader.issues.len(), issue));
    }
    let mut out = Vec::new();
    for form in &forms {
        lower_into(source, form, &mut out);
    }
    Ok(out)
}

// --- 型の判定(Hy と Python で共通の部分)---------------------------------------------------------------------------

/// 名 1 つの判定(素の写像・素の組)。
fn name_problem(name: &str) -> Option<String> {
    if BARE_MAPPINGS.contains(&name) {
        Some(format!("素の写像 {}", name))
    } else if BARE_TUPLES.contains(&name) {
        Some(format!("素の組 {}", name))
    } else {
        None
    }
}

/// 文字列で書いた型("A | B")— `|` で分けた枝の名だけを見る。
fn string_type_problem(text: &str) -> Option<String> {
    text.split('|').find_map(|part| name_problem(part.trim()))
}

/// X[args] の形の型の判定(Hy と Python で共通)。
fn generic_problem<T>(base: &str, args: &[&T], problem: &dyn Fn(&T) -> Option<String>, open: &dyn Fn(&T) -> bool, ellipsis: &dyn Fn(&T) -> bool) -> Option<String> {
    if BARE_MAPPINGS.contains(&base) {
        if args.len() == 2 && open(args[1]) {
            return Some(format!("値の型が開いた写像 {}[…, object/Any]", base));
        }
        return args.iter().find_map(|a| problem(a));
    }
    if BARE_TUPLES.contains(&base) {
        if args.len() >= 2 && !ellipsis(args[args.len() - 1]) {
            return Some(format!("長さの決まった組 {}[A, B]", base));
        }
        // 同じ型の列 tuple[X, ...] でも、要素の型が object / Any なら型を付けたことにならない(agora-redesign #1791)。
        if args.len() == 2 && open(args[0]) {
            return Some(format!("要素の型が開いた組 {}[object/Any, ...]", base));
        }
        return args.iter().filter(|a| !ellipsis(a)).find_map(|a| problem(a));
    }
    if CONTAINERS.contains(&base) {
        return args.iter().find_map(|a| problem(a));
    }
    None
}

// --- Hy の型の判定 --------------------------------------------------------------------------------------------------

/// 写像の値の型が「何でも」(object / Any・それを含む union)。
fn hy_open(node: &Hy) -> bool {
    if let Some(name) = node.name() {
        return OPEN_VALUES.contains(&name);
    }
    node.head() == "|" && node.expression().is_some_and(|items| items[1..].iter().any(hy_open))
}

fn hy_ellipsis(node: &Hy) -> bool {
    node.is("...")
}

/// (get X #(a b)) / (get X a) / (of X a b) の型の引数の列。
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

/// Hy の型の注記 1 つ → 赤の理由。
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

// --- Python の型の判定 ----------------------------------------------------------------------------------------------

/// Python の名・属性の注記を点で繋いだ名(それ以外は空)。
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

/// Python の型の注記 1 つ → 赤の理由。
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

// --- 公開面の注記を拾う ----------------------------------------------------------------------------------------------

/// 注記 1 つ(判じる前)。
enum Annotation<'a> {
    Hy(&'a Hy),
    /// 名に型の注記の在る defk・deff の :post の T(写像だけを赤にする)。
    Post(&'a Hy),
    /// 長さの決まった組の literal の答え(長さ)。
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
            Annotation::Post(node) => hy_type_problem(node).filter(|p| !p.starts_with("素の組")),
            Annotation::Pair(width) => Some(format!("長さ {} の組の literal を答えにする(複数の意味を並べた組)", width)),
        }
    }
}

/// 項目の綴り(名は綴り・それ以外は source の字面)。
fn spelled(source: &str, node: &Hy) -> String {
    match node {
        Hy::Name { text, .. } => text.clone(),
        other => {
            let (start, end) = other.span();
            source.get(start..end).unwrap_or("").to_string()
        }
    }
}

/// (annotate 名 型) → (名・型・名の範囲)。
fn annotated<'a>(source: &str, node: &'a Hy) -> Option<(String, &'a Hy, (usize, usize))> {
    let items = node.expression()?;
    (node.head() == "annotate" && items.len() == 3).then(|| (spelled(source, &items[1]), &items[2], items[1].span()))
}

/// defk・deff の :post [(: % T)] の T の列。
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

/// 長さ 2 以上の組の literal の長さ。
fn pair_width(node: &Hy) -> Option<usize> {
    node.form(Kind::Tuple).map(<[Hy]>::len).filter(|&n| n >= 2)
}

/// defn・defk・deff の本体が答えにする組の literal(最後の式と (return #(…)))— 入れ子の関数の中は見ない。
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

/// defn・defk・deff 1 つ → 戻り値の注記・:post・答えの組。
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

/// defclass・defrecord・defwire 1 つ → 欄の注記と method の注記(名は `Class.名`)。
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

/// Hy の file の当たり。
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

/// 公開の名か(`Class.名` は最後の段の名で見る)。
fn public(name: &str) -> bool {
    !name.rsplit('.').next().unwrap_or(name).starts_with('_')
}

/// Python の file の当たり。
fn py_hits(source: &str, rel: &str) -> Result<Vec<TypedHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("構文木にならない: {}", error))?;
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

/// file 1 つ(根からの path rel と中身)を判じる — Hy と Python の file だけ(ほかは空)。読めなければ理由。
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

/// 判じる file か(宣言の glob に当たる Hy か Python の file)。
pub fn wants(rel: &str, selection: &FileSelection) -> bool {
    (rel.ends_with(".hy") || rel.ends_with(".py")) && selected(rel, &selection.files, &selection.except)
}

/// 宣言の file を読んで判じる(focus が在ればその下の file だけ)。読めない file は理由を返す。
pub fn find(root: &Path, selection: &FileSelection, focus: Option<&[PathBuf]>) -> (Vec<FileHits>, Vec<String>) {
    judge_files(
        root,
        selection.files.iter(),
        focus,
        |rel, _| wants(rel, selection),
        |rel, path, source| {
            let hits = judge(&rel, &source).map_err(|reason| format!("{}: DOEFF144 の判定が読めない({})", rel, reason))?;
            Ok((!hits.is_empty()).then(|| FileHits { rel, path, source, hits }))
        },
    )
}

// --- 値を型だけで渡す層(DOEFF170・171 — agora-redesign #2143)----------------------------------------------------------
//
// 層の宣言 `:wire-free True` の層(agora の core)は、wire の型(defwire)を持たず、写像も中身の型の無い組 / 列も受け渡さない
// (operator 2026-10-01 13:0x〜13:2x 逐語 "defwire should never live in core right???" / "we never pass dict around if it's not about
// building the dict itself. if it's for getting data, such must be more like key->data func or such effect")。DOEFF144 と違い、
// 型つきの写像(`(of dict str Row)`)も鳴る(データを引く写像はキー → 値の関数か効果にする)。写像を組むのが目的の綴りは翻訳の層の
// 1 点に置くので、この層には例外を置かない — ただし並べ替えのキーの組(Plain::SortKey)は値の受け渡しではないので外す。

/// 写像(中身の型の有無を問わない)— DOEFF144 の素の写像に、JSON の本文の別名と凍らせた写像を足した物。
const WIRE_FREE_MAPPINGS: &[&str] = &["JsonBody", "JSONBody", "OpaqueJson", "FrozenMap"];
/// 中身の型が無ければ鳴る列(組は BARE_TUPLES)。
const BARE_LISTS: &[&str] = &["list", "List", "typing.List"];

/// 当たりの種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WireFreeWhat {
    /// defwire の宣言(DOEFF170)。
    Wire,
    /// :pre の引数の型(DOEFF171)。
    Pre,
    /// :post の答えの型(DOEFF171)。
    Post,
    /// defclass・defrecord の欄の型(DOEFF171)。
    Field,
}

impl WireFreeWhat {
    /// 登録簿の鍵の細目の種類。
    pub fn kind(self) -> &'static str {
        match self {
            WireFreeWhat::Wire => "wire",
            WireFreeWhat::Pre => "pre",
            WireFreeWhat::Post => "post",
            WireFreeWhat::Field => "field",
        }
    }
}

/// 値を型だけで渡す層の当たり 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireFreeHit {
    pub start: usize,
    pub end: usize,
    /// 名(defwire の名・`定義:引数`・`定義`・`Class.欄`)。
    pub name: String,
    pub what: WireFreeWhat,
    /// 赤の理由(defwire は空)。
    pub problem: String,
}

impl WireFreeHit {
    /// 登録簿の鍵の細目(`<種類>:<名>`)。
    pub fn detail(&self) -> String {
        format!("{}:{}", self.what.kind(), self.name)
    }
}

/// 名 1 つ — 写像か、中身の型の無い組・列。
fn wire_free_name_problem(name: &str) -> Option<String> {
    if BARE_MAPPINGS.contains(&name) || WIRE_FREE_MAPPINGS.contains(&name) {
        Some(format!("写像 {}", name))
    } else if BARE_TUPLES.contains(&name) || BARE_LISTS.contains(&name) {
        Some(format!("中身の型の無い {}", name))
    } else {
        None
    }
}

/// 型の注記 1 つ → 赤の理由(union の枝・入れ物の中身も見る)。写像は中身の型が在っても鳴る。
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

/// 総称型 base[args] 1 つ → 赤の理由(Hy と Python の注記で同じ判定 — agora-redesign #2203)。写像は中身の型が在っても鳴る。
fn wire_free_generic<T>(base: &str, args: &[&T], problem: &dyn Fn(&T) -> Option<String>, open: &dyn Fn(&T) -> bool, ellipsis: &dyn Fn(&T) -> bool) -> Option<String> {
    if BARE_MAPPINGS.contains(&base) || WIRE_FREE_MAPPINGS.contains(&base) {
        return Some(format!("写像 {}[…](中身の型が在ってもデータを引く写像は渡さない)", base));
    }
    if BARE_TUPLES.contains(&base) {
        if args.len() >= 2 && !ellipsis(args[args.len() - 1]) {
            return Some(format!("長さの決まった組 {}[A, B]", base));
        }
        if args.first().is_some_and(|a| open(a)) {
            return Some(format!("要素の型が開いた組 {}[object/Any, ...]", base));
        }
    }
    if BARE_TUPLES.contains(&base) || BARE_LISTS.contains(&base) || CONTAINERS.contains(&base) {
        return args.iter().filter(|a| !ellipsis(a)).find_map(|a| problem(a));
    }
    None
}

/// Python の型の注記 1 つ → 赤の理由(wire_free_type_problem の Python 版 — 文字列の注記は式として読み直す)。
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

/// Python の file 1 つの class の欄の注記(`Class.欄`・注記)の列 — 名が _ で始まる欄・ClassVar も含めて全部(欄は値の受け渡しの形)。
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

/// 値を型だけで渡す層の Python の file 1 つを判じる(DOEFF171 の欄の型 — dataclass・TypedDict・class の欄の注記・agora-redesign #2203)。
/// Python には defwire も :pre / :post も無いので、見るのは欄だけ。読めなければ理由。
pub fn py_wire_free_hits(source: &str, rel: &str) -> Result<Vec<WireFreeHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("構文木にならない: {}", error))?;
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

/// 値を型だけで渡す層の外の Python の file 1 つの欄の写像(DOEFF172 の欄の臭い — agora-redesign #2203)。読めなければ理由。
pub fn py_dict_smell_hits(source: &str, rel: &str) -> Result<Vec<DictSmellHit>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("構文木にならない: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out: Vec<DictSmellHit> = py_field_sites(&module.body)
        .into_iter()
        .filter(|(_, node)| !py_relays_opaque_json(node))
        .filter_map(|(name, node)| {
            let problem = py_wire_free_type_problem(node).filter(|problem| problem.starts_with("写像"))?;
            let range = node.range();
            Some(DictSmellHit { start: range.start().to_usize(), end: range.end().to_usize(), name, what: DictSmellWhat::Field, problem })
        })
        .collect();
    out.sort_by(|a, b| (a.start, a.detail()).cmp(&(b.start, b.detail())));
    Ok(out)
}

/// defk・deff の :pre [(: 引数 T) …] の (引数・T) の列。
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

/// defclass・defrecord・defwire 1 つの欄の(`Class.欄`・型の注記)の列(method の注記は含めない)。
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

/// 値を型だけで渡す層の Hy の file 1 つを判じる(DOEFF170 の defwire と DOEFF171 の :pre / :post / 欄の型)。読めなければ理由。
pub fn wire_free_hits(source: &str) -> Result<Vec<WireFreeHit>, String> {
    let forms = read_hy(source)?;
    let mut out = Vec::new();
    let typed = |name: String, what: WireFreeWhat, node: &Hy| -> Option<WireFreeHit> {
        let problem = wire_free_type_problem(node)?;
        // 並べ替えのキーの関数(`…key-of`・`sort-key`)の答えの組は値の受け渡しではない(DOEFF144 の Plain::SortKey と同じ名の形)。
        if what == WireFreeWhat::Post && plain_shape(&name) == Some(Plain::SortKey) && !problem.starts_with("写像") {
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
                // defwire の欄の型も defrecord・defclass と同じに見る(operator 2026-10-01 13:5x "defrecord should never had dict as attr
                // unless they are absolutely necessary" — 欄の dict 系は 3 つの宣言のどれでも)。
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

// --- 写像の置き場の臭い(DOEFF172 — agora-redesign #2143 の (2))------------------------------------------------------------
//
// agora の data は DB に在り効果で読む。memory の写像は cache のためだけで、cache は効果の層(handler)が持つ(operator 2026-10-01 13:3x
// "since all data on agora is to live on DB via effect, the use of dict should be about caching, and caching is done in effect layer")。
// だから handler の外で写像を組む定義と、欄に写像を持つ型は臭い(数えるだけ — major・warning)。写像を組むのが目的の定義(:post が
// 写像 — JSON の境界で綴る 1 点)と defhandler の中は数えない。値を型だけで渡す層の欄の写像は DOEFF171(critical)が見るので、欄の
// 臭いはそれ以外の層だけ。

/// 写像の置き場の臭いの種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DictSmellWhat {
    /// 欄の型が写像(defclass・defrecord・defwire)。
    Field,
    /// handler の外の定義の本体で写像を組む(`{…}`・`(dfor …)`・`(dict …)`)。
    Built,
}

/// 写像の置き場の臭い 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DictSmellHit {
    pub start: usize,
    pub end: usize,
    /// 名(`Class.欄` か定義の名)。
    pub name: String,
    pub what: DictSmellWhat,
    /// 欄なら型の理由、組む所なら数の文。
    pub problem: String,
}

impl DictSmellHit {
    /// 登録簿の鍵の細目(`field:<Class.欄>`・`built:<定義>`)。
    pub fn detail(&self) -> String {
        let kind = match self.what {
            DictSmellWhat::Field => "field",
            DictSmellWhat::Built => "built",
        };
        format!("{}:{}", kind, self.name)
    }
}

/// 型の注記が写像か(中身の型の有無を問わない)。
fn mapping_problem(node: &Hy) -> Option<String> {
    wire_free_type_problem(node).filter(|problem| problem.starts_with("写像"))
}

/// `(match 主 型 本体 型 :if 守り 本体 …)` の、型の位置を除いた項(主・守り・本体)— 型の `{…}` は値を組まず形を照らすだけなので数えない
/// (agora-redesign #2310 — 以前は `{"type" "call"}` の型を「写像を組む」と数えていた)。
fn match_evaluated(items: &[Hy]) -> Vec<&Hy> {
    let mut out: Vec<&Hy> = items.get(1).into_iter().collect();
    let mut at = 2;
    while at < items.len() {
        // items[at] は型 — 飛ばす。続く `:if 守り` の守りと、その後の本体は式なので数える。
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

/// 式の木の中で写像を組む所の数 — `{…}`(`#{…}` の集合は数えない)・`(dfor …)`・`(dict …)`。defhandler の中と match の型の位置は数えない。
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

/// 契約の辞書か(`{:pre …}`・`{:post …}`・`{:tags …}` — 鍵が契約の語で始まる {…})。
fn contract_map(item: &Hy) -> bool {
    item.form(Kind::Brace).is_some_and(|map| map.first().is_some_and(|key| key.is(":pre") || key.is(":post") || key.is(":tags")))
}

/// defk・deff・defn の本体の始まり(名・引数の後の先頭の docstring と契約の辞書を飛ばした位置)。docstring は契約の辞書の前にも後にも
/// 置けるので、先頭に並ぶ文字列と契約の辞書を順を問わず飛ばす(agora-redesign #2265 — 以前は引数の直後の {…} だけを契約と読み、
/// docstring の後の契約の辞書を「handler の外で組む写像」と数えていた)。
fn body_start(items: &[Hy]) -> usize {
    let mut at = 3;
    while items.get(at).is_some_and(|item| matches!(item, Hy::Text { .. }) || contract_map(item)) {
        at += 1;
    }
    at
}

/// 外の形の名(doeff-hy の declarations.hy の SPELLS と同じ閉じた一覧 — 名乗れるのはこのどれか)。
const BOUNDARY_FORMS: &[&str] = &["json", "http", "env", "schema", "records"];

/// 契約の :tags の境界の名乗り(:spells = 外の形を綴る 1 点・:reads = 外の形を型へ読む 1 点)の形の名。無いか一覧の外なら None
/// (agora-redesign #2515 — 以前は :spells の空でない値なら何でも受けた)。
fn boundary_form_of(items: &[Hy]) -> Option<String> {
    let contract = items.get(3..body_start(items))?.iter().find_map(|item| item.form(Kind::Brace).filter(|_| contract_map(item)))?;
    tags_boundary_form(contract)
}

/// 頭の辞書(契約の辞書・defwire の頭の辞書)の :tags の境界の名乗りの形の名。
fn tags_boundary_form(head: &[Hy]) -> Option<String> {
    let tags = (0..head.len().saturating_sub(1)).step_by(2).find_map(|i| head[i + 1].form(Kind::Brace).filter(|_| head[i].is(":tags")))?;
    (0..tags.len().saturating_sub(1)).step_by(2).find_map(|i| match &tags[i + 1] {
        Hy::Text { value, .. } if (tags[i].is(":spells") || tags[i].is(":reads")) && BOUNDARY_FORMS.contains(&value.as_str()) => Some(value.clone()),
        _ => None,
    })
}

/// defwire の頭の辞書(名前と docstring の後の {…})の境界の名乗りの形の名(agora-redesign #2515 — 契約どおりの外の形の写像の欄を持つ
/// wire の型は、何の境界かを :spells / :reads で名乗れば欄を数えない。defrecord は値の型なので名乗りを効かせない)。
fn wire_boundary_form(items: &[Hy]) -> Option<String> {
    items.get(2..)?.iter().filter(|item| !matches!(item, Hy::Text { .. })).take(1).find_map(|item| item.form(Kind::Brace)).and_then(tags_boundary_form)
}

/// 列の型の名か(要素の型が中継の型なら、列も中を読まずに運ぶ — agora-redesign #2586)。
const RELAY_SEQUENCES: [&str; 4] = ["tuple", "list", "typing.Tuple", "typing.List"];

/// 欄の型が中を読まない中継の型 OpaqueJson ちょうどか(None との和を含む)— DOEFF172 は写像と数えない(agora-redesign #2515 — #2077・
/// #2211・#2068 で採った中継の形。OpaqueJson と他の写像の和は数える)。要素の型が中継の型の tuple / list(`(get tuple #(OpaqueJson ...))`・
/// `(of tuple OpaqueJson ...)`・`(get list OpaqueJson)`)も、中継を 0 個以上運ぶだけなので数えない(#2586)。
fn relays_opaque_json(node: &Hy) -> bool {
    match node {
        Hy::Name { text, .. } => text == "OpaqueJson" || text.ends_with(".OpaqueJson"),
        _ => match node.expression() {
            Some(items) if node.head() == "|" => {
                let parts = &items[1..];
                parts.iter().any(|part| !part.is("None")) && parts.iter().all(|part| part.is("None") || relays_opaque_json(part))
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

/// Python の注記が OpaqueJson ちょうどか(`OpaqueJson | None`・`Optional[OpaqueJson]`・文字列の注記を含む)。
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
            // tuple[OpaqueJson, ...]・list[OpaqueJson] — 要素の型が中継の型の列も数えない(#2586)。
            let elements: Vec<&Expr> = match subscript.slice.as_ref() {
                Expr::Tuple(tuple) => tuple.elts.iter().filter(|elt| !py_ellipsis(elt)).collect(),
                single => vec![single],
            };
            RELAY_SEQUENCES.contains(&base.as_str()) && !elements.is_empty() && elements.into_iter().all(py_relays_opaque_json)
        }
        _ => is_bit_or(node).is_some_and(|(l, r)| {
            (py_relays_opaque_json(l) || is_none(l)) && (py_relays_opaque_json(r) || is_none(r)) && !(is_none(l) && is_none(r))
        }),
    }
}

/// Hy の file 1 つの写像の置き場の臭い。fields = 値を型だけで渡す層の外か(偽なら欄は見ない — その層の欄は DOEFF171 が見る。:spells の
/// 名乗りもその層の外だけで効く)。読めなければ理由。
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
                // 写像を組むのが目的の定義(:post が写像)は数えない — 綴りの 1 点。
                if defk_posts(items).into_iter().any(|node| mapping_problem(node).is_some()) {
                    continue;
                }
                // 外の形を綴る・読むのが目的の 1 点(契約の :tags に :spells / :reads を形の名で名乗る)は数えない — operator の線 (b)「dict を
                // 組む事が目的の 1 点だけ可」(agora-redesign #2265・#2515)。値を型だけで渡す層(core)では名乗っても数える(その層は綴らない)。
                if fields && boundary_form_of(items).is_some() {
                    continue;
                }
                // 先頭の docstring と契約の辞書は写像の値ではない(順を問わない)。
                let count: usize = items.get(body_start(items)..).unwrap_or(&[]).iter().map(|item| built_mappings(source, item)).sum();
                if count > 0 {
                    let (start, end) = items[1].span();
                    out.push(DictSmellHit { start, end, name, what: DictSmellWhat::Built, problem: format!("handler の外で写像を {} か所で組む", count) });
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
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("読める").into_iter().map(|h| h.detail()).collect() };
        // 鳴る: 写像の欄(defrecord・defwire)・handler の外で写像を組む定義(dfor と {…} の 2 か所)。
        // 鳴らない: 型つきの組の欄・:post が写像の dump(綴りの 1 点)・defhandler の中・契約の辞書と :tags・集合 #{…}。
        assert_eq!(details(true), vec!["field:Page.counts", "field:PageWire.extra", "built:index-of"]);
        // 値を型だけで渡す層では欄は DOEFF171 が見るので数えない。
        assert_eq!(details(false), vec!["built:index-of"]);
        let built = dict_smell_hits(source, false).unwrap();
        assert!(built[0].problem.contains("2 か所"), "{:?}", built);
    }

    #[test]
    fn dict_smell_skips_match_patterns_but_counts_the_subject_guard_and_body() {
        // agora-redesign #2310: match の型の位置の {…} は値を組まず形を照らすだけ(以前は「写像を組む」と数えた — turn_event_codec の
        // detail-of-json)。主・`:if` の守り・本体で組む写像は今までどおり数える。
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
        let found: Vec<String> = dict_smell_hits(source, true).expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["built:built-in-the-body", "built:built-in-the-guard", "built:built-in-the-subject"]);
    }

    #[test]
    fn dict_smell_skips_the_contract_after_a_docstring_and_honours_spells_outside_core() {
        // agora-redesign #2265: docstring の後に置いた契約の辞書は写像の値ではない(以前は引数の直後の {…} だけを契約と読み、数えていた)。
        // 本文で組む写像は docstring の有無を問わず数える。:tags の :spells(wire の形を綴る 1 点の名乗り)は値を型だけで渡す層の外でだけ効く。
        let source = r#"
(defk note [n]
  "docstring が先。"
  {:pre [(: n int)] :post [(: % str)] :tags {:context "x" :role "judgment"}}
  f"{n} 件")
(defk index-of [rows]
  "docstring が先でも本文の写像は数える。"
  {:pre [(: rows list)] :post [(: % int)] :tags {:context "x" :role "judgment"}}
  (len (dfor r rows r.id r)))
(defk payload-text [row]
  {:pre [(: row Row)] :post [(: % str)] :tags {:context "x" :role "protocol" :spells "json"}}
  (json.dumps {"id" row.id}))
"#;
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("読める").into_iter().map(|h| h.detail()).collect() };
        assert_eq!(details(true), vec!["built:index-of"], "契約の辞書だけの定義と :spells の名乗りは数えない");
        assert_eq!(details(false), vec!["built:index-of", "built:payload-text"], "値を型だけで渡す層では :spells を名乗っても数える");
    }

    #[test]
    fn dict_smell_honours_spells_and_reads_with_a_form_name_only() {
        // agora-redesign #2515: :spells / :reads の値は外の形の名(json / http / env / schema)。形の名の無い名乗りは数える(失敗ケース 1)・
        // core(値を型だけで渡す層)では形の名で名乗っても数える(失敗ケース 2)。
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
        let details = |fields: bool| -> Vec<String> { dict_smell_hits(source, fields).expect("読める").into_iter().map(|h| h.detail()).collect() };
        assert_eq!(details(true), vec!["built:yaml-map", "built:empty-reads"], "形の名の名乗りだけが数えない");
        assert_eq!(
            details(false),
            vec!["built:header-map", "built:read-row", "built:env-map", "built:where-of", "built:yaml-map", "built:empty-reads"],
            "core では名乗っても数える"
        );
    }

    #[test]
    fn dict_smell_does_not_count_opaque_json_relay_fields() {
        // agora-redesign #2515: 中を読まない中継の型 OpaqueJson の欄(None との和を含む)は写像と数えない。OpaqueJson 以外の写像の欄・
        // OpaqueJson と他の写像の和は数える(失敗ケース 3)。
        let source = r#"
(defrecord Relay (#^ OpaqueJson body) (#^ (| OpaqueJson None) extra) (#^ dict index) (#^ (| OpaqueJson dict) mixed) (#^ JsonBody raw))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Relay.index", "field:Relay.mixed", "field:Relay.raw"]);
        let py = "class Relay:\n    body: OpaqueJson\n    extra: OpaqueJson | None\n    later: 'Optional[OpaqueJson]'\n    index: dict[str, int]\n    mixed: OpaqueJson | dict\n";
        let py_found: Vec<String> = py_dict_smell_hits(py, "x.py").expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(py_found, vec!["field:Relay.index", "field:Relay.mixed"]);
    }

    #[test]
    fn dict_smell_does_not_count_sequences_of_opaque_json_relays() {
        // agora-redesign #2586: 要素の型が中継の型 OpaqueJson の tuple / list(中を読まない JSON を 0 個以上運ぶだけ)は写像と数えない。
        // 写像の欄と、要素の型が OpaqueJson と写像の和の列は今までどおり数える(失敗ケース)。
        let source = r#"
(defrecord Docs (#^ (get tuple #(OpaqueJson ...)) documents) (#^ (of tuple OpaqueJson ...) more) (#^ (get list OpaqueJson) listed)
  (#^ (| (get tuple #(OpaqueJson ...)) None) maybe) (#^ dict index) (#^ (get tuple #((| OpaqueJson dict) ...)) mixed))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Docs.index", "field:Docs.mixed"]);
        let py = "class Docs:\n    documents: tuple[OpaqueJson, ...]\n    listed: list[OpaqueJson]\n    maybe: 'tuple[OpaqueJson, ...] | None'\n    index: dict[str, int]\n    mixed: tuple[OpaqueJson | dict, ...]\n";
        let py_found: Vec<String> = py_dict_smell_hits(py, "x.py").expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(py_found, vec!["field:Docs.index", "field:Docs.mixed"]);
    }

    #[test]
    fn dict_smell_honours_a_boundary_named_defwire_only() {
        // agora-redesign #2515(kn-w21 の #2505 の知らせ): 契約どおりの外の形の写像の欄を持つ defwire は、頭の辞書の :tags に :spells /
        // :reads を形の名で名乗れば欄を数えない。名乗らない defwire・形の名でない名乗り・defrecord の欄は数える。
        let source = r#"
(defwire Usage "公開する自由なキーの索引。" {:tags {:context "x" :role "type" :spells "json"} :names :camel} (#^ (of dict str int) counts))
(defwire Plain "名乗りなし。" {:tags {:context "x" :role "type"} :names :camel} (#^ (of dict str int) counts))
(defwire Yaml "形の名でない。" {:tags {:context "x" :role "type" :spells "yaml"}} (#^ (of dict str int) counts))
(defrecord Rec "値の型。" {:tags {:context "x" :role "type" :spells "json"}} (#^ (of dict str int) counts))
"#;
        let found: Vec<String> = dict_smell_hits(source, true).expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:Plain.counts", "field:Yaml.counts", "field:Rec.counts"]);
    }

    /// 値を型だけで渡す層の当たりの細目の列。
    fn wire_free(source: &str) -> Vec<String> {
        wire_free_hits(source).expect("読める").into_iter().map(|h| h.detail()).collect()
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
        // 当たらない例: 型つきの defrecord・同じ型の列・str の引数・並べ替えのキーの組。
        let source = "(defrecord Row (#^ str name) (#^ (of tuple str ...) tags))\n(defk names [rows]\n  {:pre [(: rows (of list Row))] :post [(: % (of tuple str ...))]}\n  #())\n";
        assert!(wire_free(source).is_empty(), "{:?}", wire_free(source));
    }

    #[test]
    fn python_fields_in_the_wire_free_layer() {
        // agora-redesign #2203: .py の class の欄 — 型つきの写像・文字列の注記・Optional の中・中身の型の無い組・凍った写像は鳴り、
        // 名と欄の型を持つ型と同じ型の列は鳴らない。名が _ で始まる欄も欄なので見る(DOEFF144 の公開面の絞りとは違う)。
        let source = "from typing import Mapping, Optional\n\nclass View:\n    rows: Mapping[str, 'Row']\n    meta: 'dict[str, int]'\n    \
                      maybe: Optional[dict]\n    pair: tuple\n    _cache: FrozenMap\n    ids: tuple[str, ...]\n    row: Row\n    \
                      def method(self) -> dict:\n        return {}\n";
        let found: Vec<String> = py_wire_free_hits(source, "core/view.py").expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(found, vec!["field:View.rows", "field:View.meta", "field:View.maybe", "field:View.pair", "field:View._cache"]);
        let smells: Vec<String> = py_dict_smell_hits(source, "entry/view.py").expect("読める").into_iter().map(|h| h.detail()).collect();
        assert_eq!(smells, vec!["field:View.rows", "field:View.meta", "field:View.maybe", "field:View._cache"], "172 は写像の欄だけ(組は数えない)");
    }

    /// (細目・理由の頭)の列。
    fn hits(rel: &str, source: &str) -> Vec<(String, String)> {
        judge(rel, source).expect("読める").into_iter().map(|h| (h.detail(), h.problem)).collect()
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
            assert_eq!(details("a.hy", &source), vec!["return:f"], "赤: {}", t);
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
            assert!(details("a.hy", &source).is_empty(), "緑: {}", t);
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
        assert_eq!(keys, vec!["post:mapped", "post:named-map", "post:plain"], "defn の :post は読まない・名に注記の在る :post の素の組は緑");
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
        assert_eq!(details("a.hy", source), vec!["field:_Private.shown", "return:Row.pair"], "_ は最後の段の名で見る");
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
        assert!(judge("a.md", "dict").unwrap().is_empty(), "Hy と Python の file だけを読む");
    }

    // --- 構造を持つ値ではない形(agora-redesign #1792)---------------------------------------------------------------

    #[test]
    fn a_sort_key_tuple_is_not_flagged() {
        // 並べ替えのキーの :post の素の組と、答えの組の literal は鳴らない(Hy・Python とも)。
        let source = "(defk order-key-of [item]\n  {:pre [] :post [(: % tuple)]}\n  #((- item.at) item.key))\n\
                      (defk activity-sort-key [item]\n  {:pre [] :post [(: % tuple)]}\n  #(item.at item.key))\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
        assert_eq!(details("a.py", "def order_key_of(item) -> tuple:\n    return (item.at, item.key)\n"), Vec::<String>::new());
    }

    #[test]
    fn sql_arguments_in_order_are_not_flagged() {
        // SQL の引数の並び(欄 params・…-params の答え)の素の組は鳴らない。
        let source = "(defrecord InList (#^ str text) (#^ tuple params))\n\
                      (defk select-params [key]\n  {:pre [] :post [(: % tuple)]}\n  #(key 1))\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn an_index_by_key_is_not_flagged() {
        // キーで引く索引(…-by-…)の素の写像は鳴らない。
        let source = "(defk ledger-by-agent [entries]\n  {:pre [] :post [(: % dict)]}\n  {})\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
        assert_eq!(details("a.py", "def rows_by_key(rows) -> dict:\n    return {}\n"), Vec::<String>::new());
    }

    #[test]
    fn a_public_return_tuple_and_a_record_field_dict_still_flag() {
        // 反例: 3 つの形の名でない公開の戻り値の素の組・record の欄の素の写像は今までどおり鳴る。
        let source = "(defk decide [x]\n  {:pre [] :post [(: % tuple)]}\n  #(x 1))\n\
                      (defrecord Charge (#^ dict meta))\n";
        assert_eq!(details("a.hy", source), vec!["field:Charge.meta", "pair:decide", "post:decide"]);
        assert_eq!(details("a.py", "def load() -> tuple:\n    return (1, 2)\n"), vec!["return:load"]);
    }

    #[test]
    fn a_plain_shape_name_still_flags_the_other_kind() {
        // 反例: 形の名でも、外す種類でない赤は鳴る — 索引の名の素の組・並べ替えの名の素の写像・索引の値の型が開いた写像。
        let source = "(defk rows-by-key [rows]\n  {:pre [] :post [(: % tuple)]}\n  rows)\n\
                      (defk order-key-of [item]\n  {:pre [] :post [(: % dict)]}\n  {})\n\
                      (defrecord View (#^ (get dict #(str object)) items-by-id))\n";
        assert_eq!(details("a.hy", source), vec!["field:View.items-by-id", "post:order-key-of", "post:rows-by-key"]);
    }

    // --- 要素の型つきの総称型(agora-redesign #1791・doeff-hy の #1790 の形)-------------------------------------------

    #[test]
    fn element_typed_tuple_in_post_is_not_flagged() {
        // 鳴らない例: 要素の型つきの同じ型の列を :post に書いた形(get と of の 2 つの綴り・和の中)。
        let source = "(defk names [n]\n  {:pre [] :post [(: % (get tuple #(str ...)))]}\n  n)\n\
                      (defk ids [n]\n  {:pre [] :post [(: % (of tuple int ...))]}\n  n)\n\
                      (defk maybe [n]\n  {:pre [] :post [(: % (| (get tuple #(str ...)) None))]}\n  n)\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn key_and_value_typed_dict_field_is_not_flagged() {
        // 鳴らない例: キーと値の型つきの写像を record の欄と :post に書いた形。
        let source = "(defrecord Index (#^ (of dict str int) counts) (#^ (get dict #(str Row)) rows))\n\
                      (defk totals [n]\n  {:pre [] :post [(: % (of dict str int))]}\n  n)\n";
        assert_eq!(details("a.hy", source), Vec::<String>::new());
    }

    #[test]
    fn bare_tuple_in_post_still_flags() {
        // 鳴る例: 名に注記の無い defk の :post の素の組(要素の型が無い)。
        let source = "(defk pairs [n]\n  {:pre [] :post [(: % tuple)]}\n  n)\n";
        assert_eq!(hits("a.hy", source), vec![("post:pairs".to_string(), "素の組 tuple".to_string())]);
    }

    #[test]
    fn open_element_generics_still_flag() {
        // 鳴る例: 要素の型が Any / object の総称型は型を付けたことにならない(組の要素・写像の値)— Hy と Python。
        let source = "(defk loose [n]\n  {:pre [] :post [(: % (of tuple Any ...))]}\n  n)\n\
                      (defrecord Bag (#^ (get tuple #(object ...)) items) (#^ (of dict str Any) extra))\n";
        assert_eq!(details("a.hy", source), vec!["field:Bag.extra", "field:Bag.items", "post:loose"]);
        assert_eq!(details("a.py", "def load() -> tuple[Any, ...]:\n    return ()\n"), vec!["return:load"]);
    }
}

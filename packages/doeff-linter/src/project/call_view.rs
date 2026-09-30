//! 定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え — editor-json の `rewrites`(agora-redesign #849)。
//!
//! operator 2026-09-28 の決定(逐語は #849): "also, maybe we could make the func call look like f(a,b) instead of (f a b)?" /
//! "yeah so the code would look like: int x <- some-func(0) + 1" / 式の途中の effect は `!` の印を残す案 A("lets try A")。
//! 読むための表示だけで、source の Hy はそのまま(書く向きの変換は持たない)。式の形を読むのはここ 1 か所で、エディタは
//! 置き換えを描くだけ(Hy を読み直さない)。
//!
//! 置き換えは「元の文字を隠す範囲と、そこに見せる文字」の列(edit)で表す。名・引数・字面は元の文字のまま残すので、
//! エディタの定義へ飛ぶ機能・hover はその位置でそのまま効く。並べ替えが要る所(method の `(.m obj a)` → `obj.m(a)`)だけ、
//! 名を挿し込む文字で見せる。
//!
//! | lisp | 表示 |
//! |---|---|
//! | `(f a b)` | `f(a, b)` |
//! | `(f a :key v)` | `f(a, key=v)` |
//! | `(.get row "k")` | `row.get("k")` |
//! | `(+ a b)`・`(= a b)`・`(is x None)`・`(not x)` | `a + b`・`a == b`・`x is None`・`not x`(優先順位が変わる所だけ括弧) |
//! | `(! (f a))` | `!f(a)`(撃つ呼びが effect なら、その effect の名を添えてエディタが装置の絵を描く) |
//! | `(<- (f a))`(名の無い `<-`) | `<- f(a)` |
//! | `(Effect a)`(effect の値を作る) | `Effect(a)`(effect の名を添える) |
//!
//! 置き換えるのは defk / deff の本体だけ(見出しと契約の辞書は見出しの係の持ち物)。制御の形(`when`・`if`・`match`・`for` …)と
//! 知らない macro は lisp のまま — 呼びと見なすのは、頭が呼べる物だと分かる時だけ(import した名・repo の定義・Python の組み込み・
//! 定義の中の局所の名・`a.b` の形の名)。`require` で入る macro は import の表に載らないので呼びにならない。
//! 字下げは作り直さない: 引数が次の行へ続く所は、前の引数の後ろに `,` を挿すだけで改行と字下げは元のまま。

use std::collections::HashSet;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix};
use serde::Serialize;

use crate::position::Range;

use super::signatures::{
    binding_pairs, definition_shape, top_definitions, BindingModifier, DefinitionShape, FileReader, Location, SignatureKind,
    TypeRef, World,
};
use super::smells::live;

// --- 契約の形 ------------------------------------------------------------------------------

/// 置き換えの種類(契約の閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RewriteKind {
    /// `(f a b)` → `f(a, b)`
    Call,
    /// `(.m obj a)` → `obj.m(a)`
    Method,
    /// `(+ a b)` → `a + b`
    Infix,
    /// `(- a)`・`(not a)` → `-a`・`not a`
    Prefix,
    /// `(! e)` → `!e`
    Perform,
    /// 名の無い `(<- e)` → `<- e`
    Bind,
    /// `(get x k)` → `x[k]`
    Subscript,
    /// `(. obj attr)` → `obj.attr`
    Attribute,
}

/// 元の文字の範囲を隠し、そこに `text` を見せる(範囲が空なら挿すだけ・`text` が空なら隠すだけ)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewriteEdit {
    pub range: Range,
    pub text: String,
    /// `text` の前に effect の装置の絵を描く時の effect の名(絵はエディタが名から選ぶ)。無ければ null。
    pub effect: Option<String>,
}

/// 部品(呼びの頭)の種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PartRole {
    /// defeffect か repo の外の effect(effect の値を作る)
    Effect,
    /// defk
    Defk,
    /// deff
    Deff,
    /// repo の型の名(record などを作る)
    Type,
    /// import した名・この file の定義
    Function,
    /// Python の組み込み
    Builtin,
    /// 定義の中の局所の名(引数・束縛)
    Local,
    /// `(.m obj …)` の m
    Method,
}

/// 置き換えた式の部品 1 つ(hover が型と定義への link を出すため)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewritePart {
    /// 元の文字の中の名の範囲。
    pub range: Range,
    pub name: String,
    pub role: PartRole,
    /// repo の中の定義(組み込み・repo の外の名は null)。
    pub definition: Option<Location>,
    /// 呼びの答えの型(defk / deff の答え・effect の値の答え・型の名なら型そのもの。分からなければ null)。
    pub answer: Option<TypeRef>,
}

/// 置き換え 1 つ(括弧の組 1 つ)。入れ子の呼びは別の置き換えで、`parent` が外の置き換えの番号。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Rewrite {
    pub kind: RewriteKind,
    pub path: String,
    /// 元の式全体(括弧を含む)。
    pub range: Range,
    /// 元の lisp の文字(一字一句)。
    pub original: String,
    /// 置き換えた後の文字(中の置き換えも全部当てた物 — 改行と字下げは元のまま)。
    pub text: String,
    /// この式の分の edit(中の式の分は中の置き換えが持つ)。本文の順。
    pub edits: Vec<RewriteEdit>,
    pub parts: Vec<RewritePart>,
    /// 外側の置き換えの番号(`rewrites` の中の位置)。外側を元の lisp で見せる時は、中も元の lisp で見せる(括弧の要否が外に依るため)。
    pub parent: Option<usize>,
}

// --- 演算子 ---------------------------------------------------------------------------------

/// 優先順位(Python と同じ順 — 大きいほど強く結ぶ)。
mod prec {
    pub const OR: u8 = 1;
    pub const AND: u8 = 2;
    pub const NOT: u8 = 3;
    pub const COMPARE: u8 = 4;
    pub const BIT_OR: u8 = 5;
    pub const BIT_XOR: u8 = 6;
    pub const BIT_AND: u8 = 7;
    pub const SHIFT: u8 = 8;
    pub const ADD: u8 = 9;
    pub const MUL: u8 = 10;
    pub const UNARY: u8 = 11;
    pub const POWER: u8 = 12;
    /// `!e` — 撃った答えは値 1 つ(引数・演算の項に括弧は要らない)。
    pub const PERFORM: u8 = 13;
    /// 呼び・method・名・字面。
    pub const ATOM: u8 = 16;
}

/// 中置の演算子(lisp の頭 → 表示・優先順位・同じ順位の連なりを括弧なしで読めるか)。
fn infix(head: &str) -> Option<(&'static str, u8)> {
    Some(match head {
        "or" => ("or", prec::OR),
        "and" => ("and", prec::AND),
        "=" => ("==", prec::COMPARE),
        "!=" => ("!=", prec::COMPARE),
        "<" => ("<", prec::COMPARE),
        "<=" => ("<=", prec::COMPARE),
        ">" => (">", prec::COMPARE),
        ">=" => (">=", prec::COMPARE),
        "is" => ("is", prec::COMPARE),
        "is-not" => ("is not", prec::COMPARE),
        "in" => ("in", prec::COMPARE),
        "not-in" => ("not in", prec::COMPARE),
        "|" => ("|", prec::BIT_OR),
        "^" => ("^", prec::BIT_XOR),
        "&" => ("&", prec::BIT_AND),
        "<<" => ("<<", prec::SHIFT),
        ">>" => (">>", prec::SHIFT),
        "+" => ("+", prec::ADD),
        "-" => ("-", prec::ADD),
        "*" => ("*", prec::MUL),
        "/" => ("/", prec::MUL),
        "//" => ("//", prec::MUL),
        "%" => ("%", prec::MUL),
        "@" => ("@", prec::MUL),
        "**" => ("**", prec::POWER),
        _ => return None,
    })
}

/// 前置の演算子(引数 1 つの時)。
fn prefix(head: &str) -> Option<(&'static str, u8)> {
    Some(match head {
        "not" => ("not ", prec::NOT),
        "-" => ("-", prec::UNARY),
        "+" => ("+", prec::UNARY),
        "~" => ("~", prec::UNARY),
        _ => return None,
    })
}

/// 呼びとは読まない頭(制御の形・束縛・定義・Hy の特別な形と doeff-hy の macro)。局所の名や import に同じ綴りがあっても lisp のまま。
const LISP_HEADS: &[&str] = &[
    "if", "when", "unless", "cond", "do", "match", "case", "for", "while", "let", "fn", "fnk", "fn/a", "lambda", "defn",
    "defn/a", "defk", "deff", "defp", "defmacro", "defclass", "defrecord", "defwire", "defenum", "defeffect", "defhandler",
    "deftest", "setv", "setx", "val", "var", "<-", "!", ":=", "return", "yield", "await", "raise", "try", "except", "finally",
    "else", "with", "with/a", "import", "require", "quote", "quasiquote", "unquote", "get", "cut", "lfor", "sfor", "dfor", "gfor",
    "assert", "del", "global", "nonlocal", "break", "continue", "pass", "->", "->>", "as->", "doto", "absent-as", "on-raise",
    "handle", "resume", "finish", "of", "annotate", "py", "pys", "hy", "eval-and-compile", "eval-when-compile", ".", "chainc",
    "try-except", "unpack-iterable", "unpack-mapping", "nonlocal", "defmain", "comment", "lazy", "session",
];

/// Python の組み込みの呼べる名(頭がこれなら呼び)。
const BUILTINS: &[&str] = &[
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "dir", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "getattr", "hasattr", "hash", "id", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "object", "ord", "pow", "print", "range", "repr", "reversed", "round", "set", "setattr",
    "slice", "sorted", "str", "sum", "super", "tuple", "type", "vars", "zip", "ValueError", "TypeError", "KeyError",
    "RuntimeError", "Exception", "NotImplementedError", "AssertionError", "StopIteration", "IndexError", "AttributeError",
];

/// 局所の名を束ねる頭(最初の `[…]` の中の記号が名)。
const BINDER_BRACKETS: &[&str] = &["fn", "fnk", "lambda", "defn", "for", "lfor", "sfor", "dfor", "gfor", "let", "with", "except"];

// --- 読み ---------------------------------------------------------------------------------

/// 式の置かれた場所(括弧が要るかを決める)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Ctx {
    /// 置き換えない lisp の形の中(`(when (and a b) …)`)— 演算は括弧を残す。
    Lisp,
    /// 引数・束縛の値・本体の文 — 括弧は要らない。
    Free,
    /// `!` の中 — effect の絵は `!` が持つ(呼びの頭には描かない)。
    Performed,
    /// 中置の項(外の優先順位と、右の項か)。
    Operand { outer: u8, right: bool },
    /// method の的・`!` の中身の前 — 名か呼びでないと括弧が要る。
    Receiver,
}

impl Ctx {
    /// この場所に優先順位 `p` の式を括弧なしで置けないか。
    fn needs_parens(self, p: u8) -> bool {
        match self {
            Ctx::Lisp => p < prec::PERFORM,
            Ctx::Free | Ctx::Performed => false,
            // 同じ順位の右の項は括弧を残す(a - (b - c))。比べるの連なり a < b < c は意味が変わるので同じ順位でも残す
            Ctx::Operand { outer, right } => p < outer || (p == outer && (right || outer == prec::COMPARE)),
            Ctx::Receiver => p < prec::ATOM,
        }
    }
}

/// 1 file の置き換えを作る(defk / deff の本体だけ)。
pub(super) fn file_rewrites(world: &World, reader: &FileReader, forms: &[Form]) -> Vec<Rewrite> {
    let file_names = file_level_names(reader, forms);
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, forms) {
        let Some(shape) = definition_shape(&reader.hy, form) else { continue };
        let locals = definition_locals(reader, &shape);
        let mut walker =
            Walker { world, reader, locals: &locals, file_names: &file_names, bang: BangStyle::Mark, out: Vec::new() };
        for item in &shape.body {
            walker.walk(item, Ctx::Free, None);
        }
        let base = out.len();
        out.extend(walker.out.into_iter().map(|mut r| {
            r.parent = r.parent.map(|p| p + base);
            r
        }));
    }
    fill_texts(reader, &mut out);
    out
}

/// 呼びと読める頭を決める名(定義の中の局所の名と、file の最上位の名)— 本体の文字(`body_view.rs`)が式 1 つずつ読むため。
pub(super) struct CallNames {
    pub(super) locals: HashSet<String>,
    pub(super) file_names: HashSet<String>,
}

impl CallNames {
    /// 定義 1 つの名(file の最上位の名は `file_names` で渡す)。
    pub(super) fn of(reader: &FileReader, file_names: &HashSet<String>, shape: &DefinitionShape) -> CallNames {
        CallNames { locals: definition_locals(reader, shape), file_names: file_names.clone() }
    }
}

/// 本体の文字の式 1 つの置き換え(文の値の場所 = 括弧の要らない場所で読む)。effect を撃つ `!` は印を出さず、呼びの頭の
/// effect の部品が絵を持つ(`(! (E a))` → `E(a)` — 読む面の本体の文字の表・agora-redesign #910)。effect でない `!` は残す。
pub(super) fn expression_rewrites(world: &World, reader: &FileReader, names: &CallNames, form: &Form) -> Vec<Rewrite> {
    let mut walker =
        Walker { world, reader, locals: &names.locals, file_names: &names.file_names, bang: BangStyle::Glyph, out: Vec::new() };
    walker.walk(form, Ctx::Free, None);
    let mut out = walker.out;
    fill_texts(reader, &mut out);
    out
}

/// 定義の引数と、本体で束ねた局所の名。
fn definition_locals(reader: &FileReader, shape: &DefinitionShape) -> HashSet<String> {
    let mut locals = HashSet::new();
    if let Some(params) = shape.params {
        collect_symbols(reader, params, &mut locals);
    }
    for item in &shape.body {
        collect_locals(reader, item, &mut locals);
    }
    locals
}

/// 撃つ式(`!` / `<-` の中身)の頭が effect ならその名(呼びと読む頭の決め方は置き換えと同じ)。
pub(super) fn performed_effect(world: &World, reader: &FileReader, names: &CallNames, inner: &Form) -> Option<String> {
    let walker =
        Walker { world, reader, locals: &names.locals, file_names: &names.file_names, bang: BangStyle::Glyph, out: Vec::new() };
    walker.performed_effect(inner)
}

/// この file の最上位で定義・束縛した名(`defn`・`setv`・`val` … の名)。
pub(super) fn file_level_names(reader: &FileReader, forms: &[Form]) -> HashSet<String> {
    let mut names = HashSet::new();
    for form in top_definitions(&reader.hy, forms) {
        let Some(items) = live(form) else { continue };
        let head = items.first().and_then(|h| reader.hy.symbol(h)).unwrap_or("");
        if head == "defmacro" {
            continue;
        }
        if head.starts_with("def") || matches!(head, "setv" | "val" | "var") {
            if let Some(name) = items.get(1) {
                let target = match &name.node {
                    Node::Annotated { target: Some(t), .. } => t.as_ref(),
                    _ => name,
                };
                if let Some(s) = reader.hy.symbol(target) {
                    names.insert(s.to_string());
                }
            }
        }
    }
    names
}

/// form の中の記号を全部集める(引数の並び・`[x xs]` の束ね)。
fn collect_symbols(reader: &FileReader, form: &Form, out: &mut HashSet<String>) {
    match &form.node {
        Node::Symbol => {
            out.insert(reader.hy.text(form).to_string());
        }
        Node::Seq { items, .. } => items.iter().for_each(|i| collect_symbols(reader, i, out)),
        Node::Annotated { target: Some(t), .. } => collect_symbols(reader, t, out),
        Node::Prefixed { inner: Some(i), prefix: Prefix::Unpack | Prefix::UnpackMapping } => collect_symbols(reader, i, out),
        _ => {}
    }
}

/// 定義の中で束ねた局所の名(`val`・`var`・`setv`・`<-`・`:=` の名と、`fn`・`for` … の最初の `[…]` の記号)。
fn collect_locals(reader: &FileReader, form: &Form, out: &mut HashSet<String>) {
    let Some(items) = live(form) else {
        if let Node::Seq { items, .. } = &form.node {
            items.iter().for_each(|i| collect_locals(reader, i, out));
        }
        return;
    };
    let head = items.first().and_then(|h| match h.node {
        Node::Symbol | Node::Keyword => Some(reader.hy.text(h)),
        _ => None,
    });
    match head {
        Some("quote" | "quasiquote") => return,
        Some("setv") => {
            for pair in items[1..].chunks(2) {
                if let Some(s) = reader.hy.symbol(pair[0]) {
                    out.insert(s.to_string());
                }
            }
        }
        Some(word @ ("val" | "var" | "lazy" | "session")) => {
            let start = if BindingModifier::of(word).is_some() { 2 } else { 1 };
            for pair in binding_pairs(&reader.hy, &items[start.min(items.len())..]) {
                if let Some(s) = reader.hy.symbol(pair.name) {
                    out.insert(s.to_string());
                }
            }
        }
        Some("<-" | ":=") if items.len() >= 3 => {
            if let Some(s) = reader.hy.symbol(items[1]) {
                out.insert(s.to_string());
            }
        }
        Some(h) if BINDER_BRACKETS.contains(&h) => {
            if let Some(bracket) = items.iter().skip(1).find(|i| i.bracket_items().is_some()) {
                collect_symbols(reader, bracket, out);
            }
        }
        _ => {}
    }
    for item in &items[1.min(items.len())..] {
        collect_locals(reader, item, out);
    }
}

/// 呼びの頭の読み(何を呼ぶか)。
struct Callee {
    role: PartRole,
    definition: Option<Location>,
    answer: Option<TypeRef>,
    /// effect の値を作る呼びなら effect の名。
    effect: Option<String>,
}

/// effect を撃つ `!` の見せ方。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BangStyle {
    /// `!E(a)` — `!` を残し、`!` の edit が effect の名を持つ(editor の上の置き換え・17 節)。
    Mark,
    /// `E(a)` — `!` を出さず、呼びの頭の `(` の edit が effect の名を持つ(読む面の本体の文字)。effect でない `!` は残す。
    Glyph,
}

struct Walker<'w, 'r, 'a> {
    world: &'w World,
    reader: &'r FileReader<'a>,
    locals: &'r HashSet<String>,
    file_names: &'r HashSet<String>,
    bang: BangStyle,
    out: Vec<Rewrite>,
}

impl Walker<'_, '_, '_> {
    fn range(&self, start: usize, end: usize) -> Range {
        self.reader.lines.range(start, end)
    }

    fn line_of(&self, offset: usize) -> u32 {
        self.reader.lines.position(offset).line
    }

    /// `start..end` の文字を隠して `text` を見せる edit。
    fn edit(&self, start: usize, end: usize, text: &str, effect: Option<String>) -> RewriteEdit {
        RewriteEdit { range: self.range(start, end), text: text.to_string(), effect }
    }

    /// 2 つの form の間を `sep` にする edit — 間が同じ行の空白だけなら空白を置き換え、改行や註を挟むなら前の form の後ろに
    /// `sep` の空白を除いた物を挿す(改行と字下げは元のまま)。
    fn separator(&self, before: &Form, after: &Form, sep: &str) -> RewriteEdit {
        let gap = self.reader.hy.src.get(before.span.end..after.span.start).unwrap_or("");
        if !gap.is_empty() && gap.chars().all(|c| c == ' ' || c == '\t') {
            self.edit(before.span.end, after.span.start, sep, None)
        } else {
            self.edit(before.span.end, before.span.end, sep.trim_end(), None)
        }
    }

    /// 頭の記号を呼べる物として読む(呼びと読めなければ None — lisp のまま)。
    fn callee(&self, head: &str) -> Option<Callee> {
        if LISP_HEADS.contains(&head) || head.starts_with('.') || head.starts_with('#') {
            return None;
        }
        let qualified = self.reader.scope.qualify(head);
        let world = self.world;
        if let Some(effect) = world.effects.get(&qualified) {
            return Some(Callee {
                role: PartRole::Effect,
                definition: Some(effect.location.clone()),
                answer: world.effect_value(effect),
                effect: Some(last_segment(head).to_string()),
            });
        }
        if let Some(definition) = world.definitions.get(&qualified) {
            return Some(Callee {
                role: if definition.kind == SignatureKind::Defk { PartRole::Defk } else { PartRole::Deff },
                definition: Some(definition.location.clone()),
                answer: world.answer_of(&qualified),
                effect: None,
            });
        }
        if let Some(location) = world.types.get(&qualified) {
            return Some(Callee {
                role: PartRole::Type,
                definition: Some(location.clone()),
                answer: Some(TypeRef::Name { name: head.to_string(), definition: Some(location.clone()) }),
                effect: None,
            });
        }
        let plain = |role| Some(Callee { role, definition: None, answer: None, effect: None });
        if self.locals.contains(head) {
            return plain(PartRole::Local);
        }
        let imported = self.reader.scope.bindings.contains_key(head.split('.').next().unwrap_or(head));
        if imported && world.is_foreign_effect(&qualified) {
            // repo の外の effect(doeff 本体の Delay・Ask など — import した大文字の名で repo の型でも定義でもない)
            return Some(Callee { role: PartRole::Effect, definition: None, answer: None, effect: Some(last_segment(head).to_string()) });
        }
        if imported || self.file_names.contains(head) {
            return plain(PartRole::Function);
        }
        if head.contains('.') && !head.ends_with('.') {
            // `json.dumps`・`self.f`・`request.of` — 名の中の属性の呼び(macro は属性にならない)
            return plain(PartRole::Function);
        }
        if BUILTINS.contains(&head) {
            return plain(PartRole::Builtin);
        }
        None
    }

    /// form を読み、置き換えを足す(`parent` = 外の置き換えの番号)。
    fn walk(&mut self, form: &Form, ctx: Ctx, parent: Option<usize>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.walk_paren(form, ctx, parent),
            Node::Seq { items, .. } => {
                for item in items.iter().filter(|i| !matches!(i.node, Node::Discarded)) {
                    self.walk(item, Ctx::Lisp, parent);
                }
            }
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
            Node::Prefixed { inner: Some(inner), .. } => self.walk(inner, Ctx::Lisp, parent),
            Node::Annotated { target: Some(target), .. } => self.walk(target, ctx, parent),
            Node::Tagged { inner: Some(inner) } => self.walk(inner, Ctx::Lisp, parent),
            _ => {}
        }
    }

    /// 子を lisp の中として読む(置き換えない形の中身)。
    fn walk_lisp_children(&mut self, items: &[&Form], parent: Option<usize>) {
        for item in items {
            self.walk(item, Ctx::Lisp, parent);
        }
    }

    fn walk_paren(&mut self, form: &Form, ctx: Ctx, parent: Option<usize>) {
        let Some(items) = live(form) else { return };
        let Some(head_form) = items.first().copied() else { return };
        let head = match head_form.node {
            Node::Symbol | Node::Keyword => self.reader.hy.text(head_form),
            _ => {
                self.walk_lisp_children(&items, parent);
                return;
            }
        };
        let args = &items[1..];
        match head {
            "quote" | "quasiquote" => {}
            "!" if args.len() == 1 => self.perform(form, head_form, args[0], ctx, parent),
            "get" if args.len() >= 2 && !args.iter().any(|a| matches!(a.node, Node::Keyword)) => {
                self.chain(form, args, RewriteKind::Subscript, parent)
            }
            "." if args.len() >= 2 && args[1..].iter().all(|a| matches!(a.node, Node::Symbol)) => {
                self.chain(form, args, RewriteKind::Attribute, parent)
            }
            "<-" => self.bind(form, head_form, args, parent),
            "val" | "var" | "lazy" | "session" => {
                let start = if BindingModifier::of(head).is_some() { 1 } else { 0 };
                if let Some(word) = args.first().filter(|_| start == 1) {
                    self.walk(word, Ctx::Lisp, parent);
                }
                for pair in binding_pairs(&self.reader.hy, &args[start.min(args.len())..]) {
                    self.walk(pair.name, Ctx::Lisp, parent);
                    if let Some(value) = pair.value {
                        self.walk(value, Ctx::Free, parent);
                    }
                }
            }
            "setv" => {
                for pair in args.chunks(2) {
                    match pair {
                        [name, value] => {
                            self.walk(name, Ctx::Lisp, parent);
                            self.walk(value, Ctx::Free, parent);
                        }
                        [rest] => self.walk(rest, Ctx::Lisp, parent),
                        _ => {}
                    }
                }
            }
            ":=" => {
                for (i, arg) in args.iter().enumerate() {
                    self.walk(arg, if i == 1 { Ctx::Free } else { Ctx::Lisp }, parent);
                }
            }
            _ => {
                let has_keyword = args.iter().any(|a| matches!(a.node, Node::Keyword));
                if let (Some((text, p)), [_, _, ..], false) = (infix(head), args, has_keyword) {
                    self.infix(form, args, text, p, ctx, parent);
                } else if let (Some((text, p)), [only], false) = (prefix(head), args, has_keyword) {
                    self.prefix_op(form, only, text, p, ctx, parent);
                } else if head.len() > 1 && head.starts_with('.') && !head.starts_with("..") && !args.is_empty() {
                    self.method(form, head_form, head, args, parent);
                } else if let Some(callee) = self.callee(head) {
                    self.call(form, head_form, args, callee, ctx, parent);
                } else {
                    self.walk_lisp_children(&items, parent);
                }
            }
        }
    }

    /// 新しい置き換えを足して番号を返す(edit と部品は後で入れる)。
    fn push(&mut self, kind: RewriteKind, form: &Form, parent: Option<usize>) -> usize {
        self.out.push(Rewrite {
            kind,
            path: self.reader.path.clone(),
            range: self.range(form.span.start, form.span.end),
            original: self.reader.hy.text(form).to_string(),
            text: String::new(),
            edits: Vec::new(),
            parts: Vec::new(),
            parent,
        });
        self.out.len() - 1
    }

    /// 引数の並び(`a b :k v #* xs`)の区切りと keyword の edit を足し、引数を読む。`first_sep` は最初の引数の前の区切りを置く前の form。
    fn arguments(&mut self, index: usize, previous: &Form, args: &[&Form], mut opened: Option<RewriteEdit>) {
        let mut edits = Vec::new();
        let mut prev = previous;
        let mut i = 0;
        let mut first = true;
        while i < args.len() {
            let arg = args[i];
            let sep = if first { "" } else { ", " };
            if first {
                if let Some(edit) = opened.take() {
                    edits.push(edit);
                }
            } else {
                edits.push(self.separator(prev, arg, sep));
            }
            first = false;
            if matches!(arg.node, Node::Keyword) && i + 1 < args.len() {
                // `:key v` → `key=v`
                edits.push(self.edit(arg.span.start, arg.span.start + 1, "", None));
                let value = args[i + 1];
                edits.push(self.separator(arg, value, "="));
                self.walk(value, Ctx::Free, Some(index));
                prev = value;
                i += 2;
                continue;
            }
            match &arg.node {
                Node::Prefixed { prefix: prefix @ (Prefix::Unpack | Prefix::UnpackMapping), inner: Some(inner) } => {
                    let star = if matches!(prefix, Prefix::Unpack) { "*" } else { "**" };
                    edits.push(self.edit(arg.span.start, inner.span.start, star, None));
                    self.walk(inner, Ctx::Free, Some(index));
                }
                _ => self.walk(arg, Ctx::Free, Some(index)),
            }
            prev = arg;
            i += 1;
        }
        if let Some(edit) = opened {
            edits.push(edit);
        }
        self.out[index].edits.extend(edits);
    }

    /// `(f a b)` → `f(a, b)`。
    fn call(&mut self, form: &Form, head: &Form, args: &[&Form], callee: Callee, ctx: Ctx, parent: Option<usize>) {
        let index = self.push(RewriteKind::Call, form, parent);
        let glyph = if ctx == Ctx::Performed { None } else { callee.effect.clone() };
        let open = form.span.start;
        let mut edits = vec![self.edit(open, open + 1, "", glyph)];
        let opened = match args.first() {
            Some(first) if self.line_of(head.span.end) == self.line_of(first.span.start) => {
                let gap = self.reader.hy.src.get(head.span.end..first.span.start).unwrap_or("");
                if gap.chars().all(|c| c == ' ' || c == '\t') {
                    self.edit(head.span.end, first.span.start, "(", None)
                } else {
                    self.edit(head.span.end, head.span.end, "(", None)
                }
            }
            _ => self.edit(head.span.end, head.span.end, "(", None),
        };
        let head_range = self.range(head.span.start, head.span.end);
        self.out[index].parts.push(RewritePart {
            range: head_range,
            name: self.reader.hy.text(head).to_string(),
            role: callee.role,
            definition: callee.definition,
            answer: callee.answer,
        });
        if args.is_empty() {
            edits.push(opened);
            self.out[index].edits.extend(edits);
            return;
        }
        self.out[index].edits.extend(edits);
        self.arguments(index, head, args, Some(opened));
    }

    /// `(.m obj a)` → `obj.m(a)`(的と m が別の行なら lisp のまま)。
    fn method(&mut self, form: &Form, head: &Form, name: &str, args: &[&Form], parent: Option<usize>) {
        let target = args[0];
        if self.line_of(form.span.start) != self.line_of(target.span.start) {
            let items = live(form).unwrap_or_default();
            self.walk_lisp_children(&items, parent);
            return;
        }
        let index = self.push(RewriteKind::Method, form, parent);
        let hide = self.edit(form.span.start, target.span.start, "", None);
        self.walk(target, Ctx::Receiver, Some(index));
        let call = format!("{}(", name);
        let rest = &args[1..];
        let opened = match rest.first() {
            Some(first) => {
                let gap = self.reader.hy.src.get(target.span.end..first.span.start).unwrap_or("");
                if !gap.is_empty() && gap.chars().all(|c| c == ' ' || c == '\t') {
                    self.edit(target.span.end, first.span.start, &call, None)
                } else {
                    self.edit(target.span.end, target.span.end, &call, None)
                }
            }
            None => self.edit(target.span.end, target.span.end, &call, None),
        };
        let head_range = self.range(head.span.start, head.span.end);
        self.out[index].parts.push(RewritePart {
            range: head_range,
            name: name.trim_start_matches('.').to_string(),
            role: PartRole::Method,
            definition: None,
            answer: None,
        });
        self.out[index].edits.push(hide);
        if rest.is_empty() {
            self.out[index].edits.push(opened);
            return;
        }
        self.arguments(index, target, rest, Some(opened));
    }

    /// `(op a b …)` → `a op b op …`(括弧が要る場所なら括弧を残す)。頭と最初の項が別の行なら lisp のまま。
    fn infix(&mut self, form: &Form, args: &[&Form], text: &str, p: u8, ctx: Ctx, parent: Option<usize>) {
        let first = args[0];
        if self.line_of(form.span.start) != self.line_of(first.span.start) {
            let items = live(form).unwrap_or_default();
            self.walk_lisp_children(&items, parent);
            return;
        }
        let keep = ctx.needs_parens(p);
        let index = self.push(RewriteKind::Infix, form, parent);
        let open = form.span.start;
        let mut edits = vec![self.edit(if keep { open + 1 } else { open }, first.span.start, "", None)];
        for (i, arg) in args.iter().enumerate() {
            if i > 0 {
                edits.push(self.separator(args[i - 1], arg, &format!(" {} ", text)));
            }
            self.walk(arg, Ctx::Operand { outer: p, right: i > 0 }, Some(index));
        }
        if !keep {
            edits.push(self.edit(form.span.end - 1, form.span.end, "", None));
        }
        self.out[index].edits.extend(edits);
    }

    /// `(- a)` → `-a`・`(not a)` → `not a`。
    fn prefix_op(&mut self, form: &Form, arg: &Form, text: &str, p: u8, ctx: Ctx, parent: Option<usize>) {
        if self.line_of(form.span.start) != self.line_of(arg.span.start) {
            let items = live(form).unwrap_or_default();
            self.walk_lisp_children(&items, parent);
            return;
        }
        let keep = ctx.needs_parens(p);
        let index = self.push(RewriteKind::Prefix, form, parent);
        let open = form.span.start;
        let shown = if keep { format!("({}", text) } else { text.to_string() };
        let mut edits = vec![self.edit(open, arg.span.start, &shown, None)];
        self.walk(arg, Ctx::Operand { outer: p, right: true }, Some(index));
        if !keep {
            edits.push(self.edit(form.span.end - 1, form.span.end, "", None));
        }
        self.out[index].edits.extend(edits);
    }

    /// `(get x a b)` → `x[a][b]`・`(. obj a b)` → `obj.a.b`(的と最初の鍵が別の行なら lisp のまま)。
    fn chain(&mut self, form: &Form, args: &[&Form], kind: RewriteKind, parent: Option<usize>) {
        let target = args[0];
        if self.line_of(form.span.start) != self.line_of(target.span.start) {
            let items = live(form).unwrap_or_default();
            self.walk_lisp_children(&items, parent);
            return;
        }
        let index = self.push(kind, form, parent);
        let mut edits = vec![self.edit(form.span.start, target.span.start, "", None)];
        self.walk(target, Ctx::Receiver, Some(index));
        let (open, close) = if kind == RewriteKind::Subscript { ("[", "]") } else { (".", "") };
        for (i, key) in args[1..].iter().enumerate() {
            let before = args[i];
            let shown = if i == 0 { open.to_string() } else { format!("{}{}", close, open) };
            let gap = self.reader.hy.src.get(before.span.end..key.span.start).unwrap_or("");
            if gap.chars().all(|c| c == ' ' || c == '\t') {
                edits.push(self.edit(before.span.end, key.span.start, &shown, None));
            } else {
                // 鍵が次の行へ続く形は 1 行に並べられないので lisp のまま
                self.out.truncate(index);
                let items = live(form).unwrap_or_default();
                self.walk_lisp_children(&items, parent);
                return;
            }
            self.walk(key, Ctx::Free, Some(index));
        }
        edits.push(self.edit(form.span.end - 1, form.span.end, close, None));
        self.out[index].edits.extend(edits);
    }

    /// `(! e)` → `!e`(撃つ呼びが effect なら `!` に effect の名を添える)。
    fn perform(&mut self, form: &Form, head: &Form, inner: &Form, ctx: Ctx, parent: Option<usize>) {
        if self.line_of(head.span.start) != self.line_of(inner.span.start) {
            self.walk(inner, Ctx::Lisp, parent);
            return;
        }
        let keep = ctx.needs_parens(prec::PERFORM);
        let effect = self.performed_effect(inner);
        let index = self.push(RewriteKind::Perform, form, parent);
        let open = form.span.start;
        let (shown, glyph, inner_ctx) = match (self.bang, effect) {
            (BangStyle::Glyph, Some(_)) => (if keep { "(" } else { "" }, None, Ctx::Free),
            (BangStyle::Glyph, None) => (if keep { "(!" } else { "!" }, None, Ctx::Performed),
            (BangStyle::Mark, effect) => (if keep { "(!" } else { "!" }, effect, Ctx::Performed),
        };
        let mut edits = vec![self.edit(open, inner.span.start, shown, glyph)];
        self.walk(inner, inner_ctx, Some(index));
        if !keep {
            edits.push(self.edit(form.span.end - 1, form.span.end, "", None));
        }
        self.out[index].edits.extend(edits);
    }

    /// 撃つ式の頭が effect ならその名。
    fn performed_effect(&self, inner: &Form) -> Option<String> {
        let head = live(inner)?.first().and_then(|h| self.reader.hy.symbol(h))?;
        self.callee(head).and_then(|c| c.effect)
    }

    /// `<-`: 名の無い `(<- e)` は `<- e` に、名のある `(<- x T e)` は束縛の係が描くので値だけ読む。
    fn bind(&mut self, form: &Form, head: &Form, args: &[&Form], parent: Option<usize>) {
        let absent_suffix = args.len() >= 3
            && matches!(args[args.len() - 2].node, Node::Keyword)
            && self.reader.hy.text(args[args.len() - 2]) == ":absent";
        let core = if absent_suffix { &args[..args.len() - 2] } else { args };
        match core {
            [value] if !absent_suffix => {
                let index = self.push(RewriteKind::Bind, form, parent);
                let open = form.span.start;
                let opened = self.edit(open, head.span.start, "", None);
                let closed = self.edit(form.span.end - 1, form.span.end, "", None);
                self.walk(value, Ctx::Free, Some(index));
                self.out[index].edits.extend([opened, closed]);
            }
            [.., value] => {
                self.walk(value, Ctx::Free, parent);
                if absent_suffix {
                    self.walk(args[args.len() - 1], Ctx::Lisp, parent);
                }
            }
            [] => {}
        }
    }
}

/// module まで含めた名の最後の区切り。
fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}

/// 置き換えごとに、置き換えた後の文字(中の置き換えも当てた物)を入れ、edit を本文の順に並べる。
fn fill_texts(reader: &FileReader, rewrites: &mut [Rewrite]) {
    // 位置は行の表から戻す(source の頭から数え直さない)・編集の位置は 1 度だけ戻す(並べ替えの比べのたびに戻さない)— 1 file の
    // 書き換えと編集の数の分だけ file の長さを数え直し、見出しの 1 回で 0.06〜0.08 秒かかっていた(agora-redesign #1632)。
    let offset = |p: crate::position::Position| reader.lines.offset(p);
    let spans: Vec<(usize, usize)> = rewrites.iter().map(|r| (offset(r.range.start), offset(r.range.end))).collect();
    let mut all: Vec<(usize, usize, String)> = Vec::new();
    for rewrite in rewrites.iter_mut() {
        let mut keyed: Vec<((usize, usize), RewriteEdit)> =
            std::mem::take(&mut rewrite.edits).into_iter().map(|e| ((offset(e.range.start), offset(e.range.end)), e)).collect();
        keyed.sort_by_key(|(at, _)| *at);
        all.extend(keyed.iter().map(|((s, e), edit)| (*s, *e, edit.text.clone())));
        rewrite.edits = keyed.into_iter().map(|(_, e)| e).collect();
    }
    all.sort_by_key(|(s, e, _)| (*s, *e));
    for (rewrite, (start, end)) in rewrites.iter_mut().zip(spans) {
        let mut text = String::new();
        let mut at = start;
        // all は始まりの順 — 範囲の始まりから二分探索で入り、始まりが範囲の終わりを越えたら止める(書き換えごとに全部の編集をなめない)
        let first = all.partition_point(|(s, _, _)| *s < start);
        for (s, e, shown) in all[first..].iter().take_while(|(s, _, _)| *s <= end).filter(|(_, e, _)| *e <= end) {
            if *s < at {
                continue;
            }
            text.push_str(reader.hy.src.get(at..*s).unwrap_or(""));
            text.push_str(shown);
            at = *e;
        }
        text.push_str(reader.hy.src.get(at..end).unwrap_or(""));
        rewrite.text = text;
    }
}

#[cfg(test)]
mod tests {
    use super::super::signatures::{file_signatures, World};
    use super::*;

    /// 根に file を並べ、1 file の置き換えを読む。
    fn rewrites(files: &[(&str, &str)], target: &str) -> Vec<Rewrite> {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        file_signatures(&world, &root, target, source).rewrites
    }

    /// 外側の置き換え(parent が無い物)の `元 → 表示` の対。
    fn shown(found: &[Rewrite]) -> Vec<(String, String)> {
        found.iter().filter(|r| r.parent.is_none()).map(|r| (r.original.clone(), r.text.clone())).collect()
    }

    /// 1 つの defk の本体を読み、外側の置き換えの表示を返す。
    fn body(lines: &str) -> Vec<String> {
        let intent = r#"
(defrecord Row "行" (#^ str id))
(defeffect ReadRow "行を読む" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "出す" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
"#;
        let core = format!(
            r#"(require doeff-hy.macros [defk <- val var])
(import demo.intent [Row ReadRow Emit])
(import json)
(import helpers [shape-of])
(defk some-func [n] {{:pre [(: n int)] :post [(: % int)]}} "doc" n)
(defk subject [a b row items]
  {{:pre [(: a int)] :post [(: % int)] :tags {{:context "d" :role "program"}}}}
  "doc"
{})
"#,
            lines
        );
        let found = rewrites(&[("demo/intent.hy", intent), ("demo/core.hy", &core)], "demo/core.hy");
        shown(&found).into_iter().map(|(_, text)| text).collect()
    }

    #[test]
    fn call_keyword_method_and_nested_calls() {
        assert_eq!(body("  (shape-of a b)"), vec!["shape-of(a, b)"]);
        assert_eq!(body("  (shape-of a :key b)"), vec!["shape-of(a, key=b)"]);
        assert_eq!(body("  (.get row \"k\")"), vec!["row.get(\"k\")"]);
        assert_eq!(body("  (.keys row)"), vec!["row.keys()"]);
        assert_eq!(body("  (shape-of)"), vec!["shape-of()"]);
        assert_eq!(body("  (shape-of (len items) (json.dumps a :indent 2))"), vec!["shape-of(len(items), json.dumps(a, indent=2))"]);
        assert_eq!(body("  (shape-of #* items #** row)"), vec!["shape-of(*items, **row)"]);
        assert_eq!(body("  (.get (shape-of a) \"k\")"), vec!["shape-of(a).get(\"k\")"]);
    }

    #[test]
    fn infix_keeps_parens_only_where_precedence_changes() {
        assert_eq!(body("  (+ a b)"), vec!["a + b"]);
        assert_eq!(body("  (= a b)"), vec!["a == b"]);
        assert_eq!(body("  (is a None)"), vec!["a is None"]);
        assert_eq!(body("  (is-not a None)"), vec!["a is not None"]);
        assert_eq!(body("  (not-in a items)"), vec!["a not in items"]);
        assert_eq!(body("  (* (+ a 1) b)"), vec!["(a + 1) * b"]);
        assert_eq!(body("  (+ (* a 2) b)"), vec!["a * 2 + b"]);
        assert_eq!(body("  (- a (- b 1))"), vec!["a - (b - 1)"]);
        assert_eq!(body("  (and (not a) (< a b))"), vec!["not a and a < b"]);
        assert_eq!(body("  (- a)"), vec!["-a"]);
        assert_eq!(body("  (.get (+ a b) 1)"), vec!["(a + b).get(1)"]);
    }

    #[test]
    fn perform_keeps_the_bang_and_names_the_effect() {
        let core = r#"(require doeff-hy.macros [defk <- val var])
(import demo.intent [Row ReadRow Emit])
(defk some-func [n] {:pre [(: n int)] :post [(: % int)]} "doc" n)
(defk subject [a]
  {:pre [(: a int)] :post [(: % int)]}
  "doc"
  (val x (+ (! (some-func 0)) 1))
  (val r (! (ReadRow "id")))
  (<- (Emit "hi"))
  (<- row Row (ReadRow "id"))
  (val made (ReadRow "id"))
  x)
"#;
        let intent = r#"
(defrecord Row "行" (#^ str id))
(defeffect ReadRow "行を読む" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "出す" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
"#;
        let found = rewrites(&[("demo/intent.hy", intent), ("demo/core.hy", core)], "demo/core.hy");
        let texts: Vec<String> = shown(&found).into_iter().map(|(_, t)| t).collect();
        assert_eq!(texts, vec!["!some-func(0) + 1", "!ReadRow(\"id\")", "<- Emit(\"hi\")", "ReadRow(\"id\")", "ReadRow(\"id\")"]);
        // 式の途中の defk の `!` は絵なし、effect の `!` は effect の名を持つ
        let bang = |text: &str| found.iter().find(|r| r.kind == RewriteKind::Perform && r.text == text).unwrap().edits[0].effect.clone();
        assert_eq!(bang("!some-func(0)"), None);
        assert_eq!(bang("!ReadRow(\"id\")"), Some("ReadRow".to_string()));
        // `!` の中の effect の呼びは絵を二重に持たない。`<-` の右辺と、値を作るだけの呼びは頭に絵を持つ
        let call_glyphs: Vec<Option<String>> =
            found.iter().filter(|r| r.kind == RewriteKind::Call && r.parts[0].role == PartRole::Effect).map(|r| r.edits[0].effect.clone()).collect();
        assert_eq!(call_glyphs, vec![None, Some("Emit".to_string()), Some("ReadRow".to_string()), Some("ReadRow".to_string())]);
        // 部品は定義の位置と答えの型を持つ
        let part = &found.iter().find(|r| r.text == "some-func(0)").unwrap().parts[0];
        assert_eq!(part.role, PartRole::Defk);
        assert!(part.definition.is_some());
        assert_eq!(part.answer, Some(TypeRef::Name { name: "int".to_string(), definition: None }));
    }

    #[test]
    fn unknown_macros_and_control_forms_stay_lisp() {
        // 知らない頭(require の macro・定義の無い名)と制御の形は lisp のまま。中の呼びだけ置き換える(演算は lisp の中で括弧を残す)
        assert_eq!(body("  (my-macro a b)"), Vec::<String>::new());
        assert_eq!(body("  (when (and a b) (shape-of a))"), vec!["(a and b)", "shape-of(a)"]);
        assert_eq!(body("  (lfor x items (len x))"), vec!["len(x)"]);
        assert_eq!(body("  (cut items 0 2)"), Vec::<String>::new());
        assert_eq!(body("  '(shape-of a)"), Vec::<String>::new());
    }

    #[test]
    fn subscript_and_attribute_forms() {
        assert_eq!(body("  (get row \"k\")"), vec!["row[\"k\"]"]);
        assert_eq!(body("  (get row \"k\" 0)"), vec!["row[\"k\"][0]"]);
        assert_eq!(body("  (len (get row (+ a 1)))"), vec!["len(row[a + 1])"]);
        assert_eq!(body("  (. row id)"), vec!["row.id"]);
        assert_eq!(body("  (get (+ a b) 0)"), vec!["(a + b)[0]"]);
    }

    #[test]
    fn multi_line_arguments_keep_line_breaks() {
        assert_eq!(body("  (shape-of a\n            b\n            :key (len items))"), vec!["shape-of(a,\n            b,\n            key=len(items))"]);
    }

    #[test]
    fn local_names_are_callable() {
        assert_eq!(body("  (val f (fn [y] (y a)))\n  (f a)"), vec!["y(a)", "f(a)"]);
    }

    #[test]
    fn nested_rewrites_point_to_their_parent() {
        let core = r#"(require doeff-hy.macros [defk])
(defk subject [a items]
  {:pre [(: a int)] :post [(: % int)]}
  "doc"
  (+ (len items) a))
"#;
        let found = rewrites(&[("demo/core.hy", core)], "demo/core.hy");
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].parent, None);
        assert_eq!(found[1].parent, Some(0));
        assert_eq!(found[0].text, "len(items) + a");
        assert_eq!(found[1].text, "len(items)");
    }
}

//! Display replacement that shows the calls in a definition body in the form `f(a, b)` — the `rewrites` of editor-json.
//!
//! Shows a call as `f(a,b)` instead of `(f a b)`, and also makes a binding readable, as in `int x <- some-func(0) + 1`.
//! Proposal A leaves the `!` mark for an effect in the middle of an expression.
//! It is a display for reading only; the Hy source stays as it is (there is no conversion in the writing direction). Reading the shape of expressions is in this one place, and the editor only
//! draws the replacements (it does not re-read Hy).
//!
//! A replacement is expressed as a list (edit) of "a range of original characters to hide, and the characters to show there". Names, arguments and literals remain as the original characters,
//! so the editor's jump-to-definition and hover work as they are at that position. Only where reordering is needed (a method's `(.m obj a)` -> `obj.m(a)`),
//! the name is shown with inserted characters.
//!
//! | lisp | display |
//! |---|---|
//! | `(f a b)` | `f(a, b)` |
//! | `(f a :key v)` | `f(a, key=v)` |
//! | `(.get row "k")` | `row.get("k")` |
//! | `(+ a b)`, `(= a b)`, `(is x None)`, `(not x)` | `a + b`, `a == b`, `x is None`, `not x` (parentheses only where precedence changes) |
//! | `(! (f a))` | `!f(a)` (if the call being fired is an effect, the effect's name is attached and the editor draws a device glyph) |
//! | `(<- (f a))` (a nameless `<-`) | `<- f(a)` |
//! | `(Effect a)` (creating an effect value) | `Effect(a)` (the effect's name is attached) |
//!
//! Only the bodies of defk / deff are replaced (the heading and the contract dict belong to the heading's owner). Control forms (`when`, `if`, `match`, `for` ...) and
//! unknown macros stay lisp — a form is treated as a call only when its head is known to be callable (an imported name, a repo definition, a Python built-in,
//! a local name in the definition, a name of the form `a.b`). Macros that come in via `require` are not in the import table, so they are not calls.
//! Indentation is not rebuilt: where arguments continue onto the next line, only a `,` is inserted after the previous argument, and line breaks and indentation stay as the original.

use std::collections::HashSet;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix};
use serde::Serialize;

use crate::position::Range;

use super::signatures::{
    binding_pairs, definition_shape, top_definitions, BindingModifier, DefinitionShape, FileReader, Location, SignatureKind,
    TypeRef, World,
};
use super::smells::live;

// --- Shape of the contract ------------------------------------------------------------------------------

/// Kind of replacement (a closed set of the contract).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RewriteKind {
    /// `(f a b)` → `f(a, b)`
    Call,
    /// `(.m obj a)` → `obj.m(a)`
    Method,
    /// `(+ a b)` → `a + b`
    Infix,
    /// `(- a)`, `(not a)` -> `-a`, `not a`
    Prefix,
    /// `(! e)` → `!e`
    Perform,
    /// A nameless `(<- e)` -> `<- e`
    Bind,
    /// `(get x k)` → `x[k]`
    Subscript,
    /// `(. obj attr)` → `obj.attr`
    Attribute,
}

/// Hides the range of original characters and shows `text` there (if the range is empty, it only inserts; if `text` is empty, it only hides).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewriteEdit {
    pub range: Range,
    pub text: String,
    /// The effect's name when drawing the effect's device glyph before `text` (the editor chooses the glyph from the name). null if none.
    pub effect: Option<String>,
}

/// Kind of a part (the head of a call).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PartRole {
    /// A defeffect or an effect outside the repo (creating an effect value)
    Effect,
    /// defk
    Defk,
    /// deff
    Deff,
    /// A repo type's name (creating a record, etc.)
    Type,
    /// An imported name, or a definition of this file
    Function,
    /// A Python built-in
    Builtin,
    /// A local name in the definition (an argument or binding)
    Local,
    /// The m of `(.m obj …)`
    Method,
}

/// One part of a replaced expression (so that hover can show the type and a link to the definition).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewritePart {
    /// The range of the name within the original characters.
    pub range: Range,
    pub name: String,
    pub role: PartRole,
    /// The definition in the repo (null for built-ins and names outside the repo).
    pub definition: Option<Location>,
    /// The type of the call's answer (the answer of a defk / deff, the answer of an effect value, or the type itself if it is a type name; null if unknown).
    pub answer: Option<TypeRef>,
}

/// One replacement (one pair of parentheses). A nested call is a separate replacement, and `parent` is the index of the outer replacement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Rewrite {
    pub kind: RewriteKind,
    pub path: String,
    /// The whole original expression (including parentheses).
    pub range: Range,
    /// The original lisp characters (verbatim).
    pub original: String,
    /// The characters after replacement (with the inner replacements all applied — line breaks and indentation stay as the original).
    pub text: String,
    /// The edits for this expression (the inner expressions' edits belong to the inner replacements). In body order.
    pub edits: Vec<RewriteEdit>,
    pub parts: Vec<RewritePart>,
    /// The index of the outer replacement (its position in `rewrites`). When the outer one is shown as the original lisp, the inner one is also shown as the original lisp (whether parentheses are needed depends on the outer one).
    pub parent: Option<usize>,
}

// --- Operators ---------------------------------------------------------------------------------

/// Precedence (the same order as Python — the larger, the tighter it binds).
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
    /// `!e` — the fired answer is a single value (no parentheses needed for arguments or operands).
    pub const PERFORM: u8 = 13;
    /// A call, method, name or literal.
    pub const ATOM: u8 = 16;
}

/// Infix operators (lisp head -> display, precedence, whether a chain of the same precedence can be read without parentheses).
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

/// Prefix operators (when there is one argument).
fn prefix(head: &str) -> Option<(&'static str, u8)> {
    Some(match head {
        "not" => ("not ", prec::NOT),
        "-" => ("-", prec::UNARY),
        "+" => ("+", prec::UNARY),
        "~" => ("~", prec::UNARY),
        _ => return None,
    })
}

/// Heads not read as calls (control forms, bindings, definitions, Hy special forms and doeff-hy macros). Even if a local name or an import has the same spelling, they stay lisp.
const LISP_HEADS: &[&str] = &[
    "if", "when", "unless", "cond", "do", "match", "case", "for", "while", "let", "fn", "fnk", "fn/a", "lambda", "defn",
    "defn/a", "defk", "deff", "defp", "defmacro", "defclass", "defrecord", "defwire", "defenum", "defeffect", "defhandler",
    "deftest", "setv", "setx", "val", "var", "<-", "!", ":=", "return", "yield", "await", "raise", "try", "except", "finally",
    "else", "with", "with/a", "import", "require", "quote", "quasiquote", "unquote", "get", "cut", "lfor", "sfor", "dfor", "gfor",
    "assert", "del", "global", "nonlocal", "break", "continue", "pass", "->", "->>", "as->", "doto", "absent-as", "on-raise",
    "handle", "resume", "finish", "of", "annotate", "py", "pys", "hy", "eval-and-compile", "eval-when-compile", ".", "chainc",
    "try-except", "unpack-iterable", "unpack-mapping", "nonlocal", "defmain", "comment", "lazy", "session",
];

/// Callable names of Python built-ins (if the head is one of these, it is a call).
const BUILTINS: &[&str] = &[
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "dir", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "getattr", "hasattr", "hash", "id", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "object", "ord", "pow", "print", "range", "repr", "reversed", "round", "set", "setattr",
    "slice", "sorted", "str", "sum", "super", "tuple", "type", "vars", "zip", "ValueError", "TypeError", "KeyError",
    "RuntimeError", "Exception", "NotImplementedError", "AssertionError", "StopIteration", "IndexError", "AttributeError",
];

/// Heads that bind local names (the symbols in the first `[…]` are the names).
const BINDER_BRACKETS: &[&str] = &["fn", "fnk", "lambda", "defn", "for", "lfor", "sfor", "dfor", "gfor", "let", "with", "except"];

// --- Reading ---------------------------------------------------------------------------------

/// The place an expression is put (decides whether parentheses are needed).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Ctx {
    /// Inside a lisp form that is not replaced (`(when (and a b) …)`) — operations keep their parentheses.
    Lisp,
    /// An argument, a binding's value, or a statement of the body — no parentheses needed.
    Free,
    /// Inside `!` — the effect glyph belongs to `!` (it is not drawn on the call's head).
    Performed,
    /// An infix operand (the outer precedence, and whether it is the right operand).
    Operand { outer: u8, right: bool },
    /// The target of a method, or before the contents of `!` — parentheses are needed unless it is a name or a call.
    Receiver,
}

impl Ctx {
    /// Whether an expression of precedence `p` cannot be placed here without parentheses.
    fn needs_parens(self, p: u8) -> bool {
        match self {
            Ctx::Lisp => p < prec::PERFORM,
            Ctx::Free | Ctx::Performed => false,
            // A right operand of the same precedence keeps its parentheses (a - (b - c)). A comparison chain a < b < c changes meaning, so it keeps them even at the same precedence
            Ctx::Operand { outer, right } => p < outer || (p == outer && (right || outer == prec::COMPARE)),
            Ctx::Receiver => p < prec::ATOM,
        }
    }
}

/// Builds the replacements of one file (only the bodies of defk / deff).
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

/// The names that decide which heads are read as calls (local names in the definition, and the file's top-level names) — so that the body view (`body_view.rs`) reads expressions one at a time.
pub(super) struct CallNames {
    pub(super) locals: HashSet<String>,
    pub(super) file_names: HashSet<String>,
}

impl CallNames {
    /// The names of one definition (the file's top-level names are passed in `file_names`).
    pub(super) fn of(reader: &FileReader, file_names: &HashSet<String>, shape: &DefinitionShape) -> CallNames {
        CallNames { locals: definition_locals(reader, shape), file_names: file_names.clone() }
    }
}

/// A replacement of one expression of the body view (the place of a statement's value = read as a place where parentheses are not needed). The `!` that fires an effect gives no mark, and the effect part of the call's head
/// carries the glyph (`(! (E a))` -> `E(a)` — the table of the body view for the reading surface). A `!` that is not an effect is kept.
pub(super) fn expression_rewrites(world: &World, reader: &FileReader, names: &CallNames, form: &Form) -> Vec<Rewrite> {
    let mut walker =
        Walker { world, reader, locals: &names.locals, file_names: &names.file_names, bang: BangStyle::Glyph, out: Vec::new() };
    walker.walk(form, Ctx::Free, None);
    let mut out = walker.out;
    fill_texts(reader, &mut out);
    out
}

/// The definition's arguments, and the local names bound in the body.
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

/// If the head of a fired expression (the contents of `!` / `<-`) is an effect, its name (the way of deciding which heads are read as calls is the same as for replacements).
pub(super) fn performed_effect(world: &World, reader: &FileReader, names: &CallNames, inner: &Form) -> Option<String> {
    let walker =
        Walker { world, reader, locals: &names.locals, file_names: &names.file_names, bang: BangStyle::Glyph, out: Vec::new() };
    walker.performed_effect(inner)
}

/// Names defined or bound at the top level of this file (the names of `defn`, `setv`, `val` ...).
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

/// Collects all symbols in a form (the parameter list, the bindings of `[x xs]`).
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

/// Local names bound inside a definition (the names of `val`, `var`, `setv`, `<-`, `:=`, and the symbols in the first `[…]` of `fn`, `for` ...).
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

/// Reading of the call's head (what is called).
struct Callee {
    role: PartRole,
    definition: Option<Location>,
    answer: Option<TypeRef>,
    /// If it is a call creating an effect value, the effect's name.
    effect: Option<String>,
}

/// How to show a `!` that fires an effect.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BangStyle {
    /// `!E(a)` — keeps the `!`, and the `!` edit carries the effect's name (the replacement on the editor, section 17).
    Mark,
    /// `E(a)` — does not show the `!`, and the edit of the call head's `(` carries the effect's name (the body view for the reading surface). A `!` that is not an effect is kept.
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

    /// An edit that hides the characters `start..end` and shows `text`.
    fn edit(&self, start: usize, end: usize, text: &str, effect: Option<String>) -> RewriteEdit {
        RewriteEdit { range: self.range(start, end), text: text.to_string(), effect }
    }

    /// An edit that makes `sep` between two forms — if the gap is only whitespace on the same line, the whitespace is replaced; if it spans a line break or comment, what remains of `sep` without its whitespace
    /// is inserted after the previous form (line breaks and indentation stay as the original).
    fn separator(&self, before: &Form, after: &Form, sep: &str) -> RewriteEdit {
        let gap = self.reader.hy.src.get(before.span.end..after.span.start).unwrap_or("");
        if !gap.is_empty() && gap.chars().all(|c| c == ' ' || c == '\t') {
            self.edit(before.span.end, after.span.start, sep, None)
        } else {
            self.edit(before.span.end, before.span.end, sep.trim_end(), None)
        }
    }

    /// Reads the head symbol as something callable (None if it cannot be read as a call — stays lisp).
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
            // An effect outside the repo (doeff's own Delay, Ask, etc. — an imported capitalized name that is neither a repo type nor a definition)
            return Some(Callee { role: PartRole::Effect, definition: None, answer: None, effect: Some(last_segment(head).to_string()) });
        }
        if imported || self.file_names.contains(head) {
            return plain(PartRole::Function);
        }
        if head.contains('.') && !head.ends_with('.') {
            // `json.dumps`, `self.f`, `request.of` — a call of an attribute within a name (a macro does not become an attribute)
            return plain(PartRole::Function);
        }
        if BUILTINS.contains(&head) {
            return plain(PartRole::Builtin);
        }
        None
    }

    /// Reads a form and adds replacements (`parent` = the index of the outer replacement).
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

    /// Reads the children as being inside lisp (the contents of a form that is not replaced).
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

    /// Adds a new replacement and returns its index (edits and parts are filled in later).
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

    /// Adds the separators and keyword edits of an argument list (`a b :k v #* xs`), and reads the arguments. `first_sep` is the form before which the separator before the first argument is placed.
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

    /// `(.m obj a)` -> `obj.m(a)` (stays lisp if the target and m are on different lines).
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

    /// `(op a b …)` -> `a op b op …` (keeps parentheses where they are needed). Stays lisp if the head and the first operand are on different lines.
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

    /// `(- a)` -> `-a`, `(not a)` -> `not a`.
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

    /// `(get x a b)` -> `x[a][b]`, `(. obj a b)` -> `obj.a.b` (stays lisp if the target and the first key are on different lines).
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
                // A form whose key continues onto the next line cannot be put on one line, so it stays lisp
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

    /// `(! e)` -> `!e` (if the call being fired is an effect, the effect's name is attached to the `!`).
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

    /// If the head of a fired expression is an effect, its name.
    fn performed_effect(&self, inner: &Form) -> Option<String> {
        let head = live(inner)?.first().and_then(|h| self.reader.hy.symbol(h))?;
        self.callee(head).and_then(|c| c.effect)
    }

    /// `<-`: a nameless `(<- e)` becomes `<- e`; a named `(<- x T e)` is drawn by the binding's owner, so only the value is read.
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

/// The last separator of a name including its module.
fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}

/// For each replacement, put in the characters after replacement (with the inner replacements applied), and arrange the edits in body order.
fn fill_texts(reader: &FileReader, rewrites: &mut [Rewrite]) {
    // Positions are converted back from the line table (not recounted from the head of the source), and the edit positions are converted only once (not on every comparison of sorting) — the length of the file was recounted
    // as many times as the file's rewrites and edits, and it took 0.06-0.08 seconds for a single heading.
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
        // all is in order of start — enter by binary search from the range's start, and stop when a start passes the range's end (do not scan all edits for each rewrite)
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
    use super::super::signatures::{read_file, World};
    use super::*;

    /// Arrange the file under a root and read the replacements of one file.
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
        let rewrites = read_file(&world, &root, target, source, |reader, forms, _| file_rewrites(&world, reader, forms));
        // The table of a single-file run (only the files the written file names) gives the same replacements.
        let named = World::build_for_file(&root, target, source);
        assert_eq!(read_file(&named, &root, target, source, |reader, forms, _| file_rewrites(&named, reader, forms)), rewrites, "{target}");
        rewrites
    }

    /// The pairs of `original -> display` of the outer replacements (those with no parent).
    fn shown(found: &[Rewrite]) -> Vec<(String, String)> {
        found.iter().filter(|r| r.parent.is_none()).map(|r| (r.original.clone(), r.text.clone())).collect()
    }

    /// Reads the body of one defk and returns the display of the outer replacements.
    fn body(lines: &str) -> Vec<String> {
        let intent = r#"
(defrecord Row "Row" (#^ str id))
(defeffect ReadRow "Read a row" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "Emit" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
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
(defrecord Row "Row" (#^ str id))
(defeffect ReadRow "Read a row" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "Emit" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
"#;
        let found = rewrites(&[("demo/intent.hy", intent), ("demo/core.hy", core)], "demo/core.hy");
        let texts: Vec<String> = shown(&found).into_iter().map(|(_, t)| t).collect();
        assert_eq!(texts, vec!["!some-func(0) + 1", "!ReadRow(\"id\")", "<- Emit(\"hi\")", "ReadRow(\"id\")", "ReadRow(\"id\")"]);
        // A defk's `!` in the middle of an expression has no glyph; an effect's `!` carries the effect's name
        let bang = |text: &str| found.iter().find(|r| r.kind == RewriteKind::Perform && r.text == text).unwrap().edits[0].effect.clone();
        assert_eq!(bang("!some-func(0)"), None);
        assert_eq!(bang("!ReadRow(\"id\")"), Some("ReadRow".to_string()));
        // An effect's call inside `!` does not carry the glyph twice. The right side of `<-` and calls that only create a value carry the glyph on the head
        let call_glyphs: Vec<Option<String>> =
            found.iter().filter(|r| r.kind == RewriteKind::Call && r.parts[0].role == PartRole::Effect).map(|r| r.edits[0].effect.clone()).collect();
        assert_eq!(call_glyphs, vec![None, Some("Emit".to_string()), Some("ReadRow".to_string()), Some("ReadRow".to_string())]);
        // A part carries the definition's position and the answer's type
        let part = &found.iter().find(|r| r.text == "some-func(0)").unwrap().parts[0];
        assert_eq!(part.role, PartRole::Defk);
        assert!(part.definition.is_some());
        assert_eq!(part.answer, Some(TypeRef::Name { name: "int".to_string(), definition: None }));
    }

    #[test]
    fn unknown_macros_and_control_forms_stay_lisp() {
        // Unknown heads (a macro from require, a name with no definition) and control forms stay lisp. Only the calls inside are replaced (operations keep parentheses inside lisp)
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

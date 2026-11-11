//! Body text of definitions — `bodies` of editor-json (the body lines for the reading plane).
//!
//! The reading plane of doeff-runner (webview) shows entities such as definitions as HTML cards, and **shows only the body as text**. The shape of the body text was
//! approved, and the source of truth for the table is section 2.2 of `docs/design/hy-reading-plane/artifacts/v2/design.md` and
//! section 2 of `v3/design.md` (the source of truth for meaning is ADR-DOE-HY-006). How Hy forms are read is kept in this one place in the linter (the decision of #849, "do not keep
//! a copy of the reading method"), and the plane only draws lines and ranges of text.
//!
//! | Original form | Body text |
//! |---|---|
//! | `(<- x T e)` | `val T x ⇐ e` |
//! | `(val x e)` / `(var x e)` | `val T x = e` / `var T x = e` (`?` if the type is unknown. It is not filled with another type) |
//! | `(val x ! e)` / `(val x (! e))` | `val T x ⇐ e` (a value that is fired — the same meaning as `(<- x e)`) |
//! | `(lazy val x e)` / `(session var x e)` … | `lazy val T x = e` / `session var T x = e` |
//! | `(:= x v)` | `x := v` |
//! | `(setv x e)` in a body | `setv x = e` + a warning mark (move to val / var) |
//! | `(E a)` / `(! (E a))` / `(<- (E a))` | `E(a)` (the text E has the effect role — the plane draws the icon) |
//! | `(! (f a))` / `(<- (f a))` (not an effect) | `!f(a)` |
//! | `(f a b)` and other calls | `f(a, b)` … (the same reading as the replacements in `call_view.rs`) |
//! | `(return v)` / `(resume v)` | `return v` / `resume v` |
//! | `(when c …)` / `(if c a b)` | `when c` + indentation / `if c` + a, `else` + b |
//! | `(match v (A) x _ y)` | `match v` + `A → x` / `_ → y` (the `→` are aligned vertically) |
//! | `(for [x xs] …)` | `for x in xs` + indentation |
//! | `(lfor x xs e)` / `gfor` / `sfor` | `[e for x in xs]` / `(e for …)` / `{e for …}` |
//! | A form not in the table | kept as the original lisp (with the text role `lisp` marker) — not drawn by guessing (v1 constraint 2) |
//!
//! Each line has the source line (`line` = 0-based — the plane adds 1 when showing it), and each text range has a source range and a role. The reading of calls
//! (what counts as a call, whether parentheses are needed) calls the readers of `call_view.rs` one expression at a time, and no copy is kept here.

use std::collections::HashSet;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix};
use serde::Serialize;

use crate::position::{offset_of, Range};

use super::call_view::{
    expression_rewrites, file_level_names, performed_effect, CallNames, PartRole, Rewrite, RewriteKind,
};
use super::signatures::{
    bind_shape, binding_pairs, definition_shape, top_definitions, Binding, BindingModifier, FileReader, Location,
    SignatureKind, TypeRef, World,
};
use super::smells::live;

// --- Contract shape ------------------------------------------------------------------------------

/// The role of a piece of text (a closed set in the contract).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum SegmentRole {
    /// Binding words (`val`, `var`, `lazy`, `session`, `setv`) and `return`, `resume`
    Keyword,
    /// The binding type (the binding type of `binding`, spelled out)
    Type,
    /// The mark `?` for an unknown type
    UnknownType,
    /// The name being bound
    Name,
    /// The arrow `⇐` that passes through an effect
    Bind,
    /// The value's `=` and the rewriting `:=`
    Assign,
    /// The head of a call that makes an effect value (the E of `E(a)` — the plane adds an icon)
    Effect,
    /// The head of a call (defk, deff, function, built-in, local name, type name)
    Call,
    /// Text of other expressions (arguments, operators, literals, separators)
    Text,
    /// A form not in the table — drawn as the original lisp (with a marker)
    Lisp,
    /// A whole-line comment in the middle of a body (`;; …` → `# …`)
    Comment,
}

/// One text range. A line's text is the segments' text concatenated.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodySegment {
    pub text: String,
    pub role: SegmentRole,
    /// The range in the original source (null for text not in the source, such as separators and `⇐`). `keyword` and `type` may have text that differs from the spelling
    /// (`<-` → `val`, `(| A B)` → `A | B`).
    pub range: Option<Range>,
    /// The effect's name when the role is `effect` (the plane chooses the icon).
    pub effect: Option<String>,
    /// The destination to jump to on click (the definition within the repo of a call head or type name. null if none).
    pub definition: Option<Location>,
}

/// The kind of a line's warning (a closed set in the contract).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BodyWarningKind {
    /// `setv` in a body — whether it can be rewritten cannot be read (move to val / var; ADR-DOE-HY-006)
    Setv,
}

/// A line's warning (the plane draws the warning mark).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodyWarning {
    pub kind: BodyWarningKind,
    pub message: String,
}

/// One line of a body.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodyLine {
    /// The source line (0-based). If one source line has two statements, the same line number repeats.
    pub line: u32,
    /// The indentation level (the outermost level of a body = 0; one deeper inside when / if / match / for).
    pub depth: u32,
    /// The number of spaces added after the level's indentation (the plane places the text after `"  " × depth + " " × pad`). The continuation lines of a multi-line expression
    /// (the difference from the head of the source's indented statement) and the contents of a block that starts inside an arm, like `A → match v` (aligned to the block word's column), are nonzero.
    pub pad: u32,
    pub segments: Vec<BodySegment>,
    /// The number of the binding this line draws (the position within the same output's `bindings`).
    pub binding: Option<usize>,
    pub warning: Option<BodyWarning>,
}

/// The body of one definition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Body {
    pub kind: SignatureKind,
    pub name: String,
    pub path: String,
    /// The name's range (the same as the same definition in `signatures`).
    pub range: Range,
    pub full_range: Range,
    pub lines: Vec<BodyLine>,
}

// --- Reading ---------------------------------------------------------------------------------

/// The body lines per definition of one file (defk / deff — the same definitions as the headings).
pub(super) fn file_bodies(world: &World, reader: &FileReader, forms: &[Form], bindings: &[Binding]) -> Vec<Body> {
    let file_names = file_level_names(reader, forms);
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, forms) {
        let Some(shape) = definition_shape(&reader.hy, form) else { continue };
        let names = CallNames::of(reader, &file_names, &shape);
        let mut printer =
            Printer {
                world,
                reader,
                names: &names,
                bindings,
                lines: Vec::new(),
                indent: Indent { depth: 0, pad: 0 },
                flat: false,
                flat_broken: false,
            };
        // A string at the head of a body is a description (only when statements follow — a body of only one string has that string as its answer)
        let skip = usize::from(matches!(shape.body.as_slice(), [first, _, ..] if matches!(first.node, Node::Str { .. })));
        // Draw the body's statements within the definition form's sequence (to pick up comments between statements)
        let siblings = live(form).unwrap_or_default();
        let first = shape.body.get(skip).map(|f| f.span.start);
        let from = siblings.iter().position(|f| Some(f.span.start) == first).unwrap_or(siblings.len());
        printer.statements(&siblings, from, Indent { depth: 0, pad: 0 });
        out.push(Body {
            kind: shape.kind,
            name: reader.hy.text(shape.name).to_string(),
            path: reader.path.clone(),
            range: reader.lines.range(shape.name.span.start, shape.name.span.end),
            full_range: reader.lines.range(form.span.start, form.span.end),
            lines: printer.lines,
        });
    }
    out
}

/// Something that overrides the source within an expression (a replacement edit, or a parenthesized form that was not replaced = an island of lisp).
enum Overlay<'t> {
    Edit(&'t str),
    Island,
    /// Text for a comprehension (`lfor` …) drawn on one line.
    Shown(Vec<BodySegment>),
}

/// A line's indentation (the level, and the spaces after the level).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Indent {
    depth: u32,
    pad: u32,
}

struct Printer<'w, 'r, 'a> {
    world: &'w World,
    reader: &'r FileReader<'a>,
    names: &'r CallNames,
    bindings: &'r [Binding],
    lines: Vec<BodyLine>,
    /// The current statement's indentation (the basis for continuation lines of a multi-line expression).
    indent: Indent,
    /// When an expression is being drawn flattened onto one line (newlines and indentation become one space).
    flat: bool,
    /// Hit something that cannot be flattened (a comment in the middle of a line).
    flat_broken: bool,
}

impl Printer<'_, '_, '_> {
    fn src(&self) -> &str {
        self.reader.hy.src
    }

    fn range(&self, start: usize, end: usize) -> Range {
        self.reader.lines.range(start, end)
    }

    fn form_range(&self, form: &Form) -> Range {
        self.range(form.span.start, form.span.end)
    }

    fn column(&self, offset: usize) -> u32 {
        self.reader.lines.position(offset).character
    }

    /// Start a new line (the source line is the line at `offset`).
    fn start_line(&mut self, offset: usize, indent: Indent) {
        let line = self.reader.lines.position(offset).line;
        self.lines.push(BodyLine {
            line,
            depth: indent.depth,
            pad: indent.pad,
            segments: Vec::new(),
            binding: None,
            warning: None,
        });
    }

    /// The column where the next text will be placed on the current line (how the plane counts text — one level = two spaces).
    fn cursor(&mut self) -> u32 {
        let line = self.current();
        let width: usize = line.segments.iter().map(|s| s.text.chars().count()).sum();
        line.depth * 2 + line.pad + width as u32
    }

    /// The indentation aligned to the block word (when, match …) that starts at column `column` of the current line. The contents are one level deeper than this.
    fn aligned(&mut self, column: u32) -> Indent {
        let depth = self.current().depth;
        Indent { depth, pad: column.saturating_sub(depth * 2) }
    }

    /// Draw the text that `f` draws on a temporary line outside the current line and take it out (None if it does not fit on one line — to measure arm alignment and comprehension shape first).
    fn capture(&mut self, f: impl FnOnce(&mut Self) -> bool) -> Option<Vec<BodySegment>> {
        let mark = self.lines.len();
        let indent = self.indent;
        self.lines.push(BodyLine { line: 0, depth: 0, pad: 0, segments: Vec::new(), binding: None, warning: None });
        let drawn = f(self);
        self.indent = indent;
        let mut added: Vec<BodyLine> = self.lines.drain(mark..).collect();
        match (drawn, added.len()) {
            (true, 1) => added.pop().map(|l| l.segments),
            (true, _) | (false, _) => None,
        }
    }

    /// Take out the expression that `f` draws, flattened onto one line (None if it cannot be flattened).
    fn capture_flat(&mut self, f: impl FnOnce(&mut Self) -> bool) -> Option<Vec<BodySegment>> {
        let (flat, broken) = (self.flat, self.flat_broken);
        self.flat = true;
        self.flat_broken = false;
        let captured = self.capture(f);
        let failed = self.flat_broken;
        self.flat = flat;
        self.flat_broken = broken || (flat && failed);
        captured.filter(|_| !failed)
    }

    /// Append the extracted text to the current line.
    fn extend(&mut self, segments: Vec<BodySegment>) {
        for segment in segments {
            self.push(segment);
        }
    }

    fn current(&mut self) -> &mut BodyLine {
        self.lines.last_mut().expect("text was added before a line was started")
    }

    /// Add text (consecutive `text` pieces are merged into one — pieces not in the source, and consecutive source ranges).
    fn push(&mut self, segment: BodySegment) {
        if segment.text.is_empty() {
            return;
        }
        let line = self.current();
        if let Some(last) = line.segments.last_mut() {
            if last.role == SegmentRole::Text && segment.role == SegmentRole::Text {
                match (&mut last.range, segment.range) {
                    (None, None) => {
                        last.text.push_str(&segment.text);
                        return;
                    }
                    (Some(prev), Some(next)) if prev.end == next.start => {
                        prev.end = next.end;
                        last.text.push_str(&segment.text);
                        return;
                    }
                    (Some(_), Some(_)) | (Some(_), None) | (None, Some(_)) => {}
                }
            }
        }
        line.segments.push(segment);
    }

    /// Text not in the source.
    fn text(&mut self, text: &str, role: SegmentRole) {
        self.push(BodySegment { text: text.to_string(), role, range: None, effect: None, definition: None });
    }

    /// Add the form's text as is, with a role.
    fn word(&mut self, form: &Form, role: SegmentRole) {
        let text = self.reader.hy.text(form).to_string();
        self.shown(form, &text, role);
    }

    /// Add text that shows `text` over the form's range (text whose spelling changes, like `<-` → `val`).
    fn shown(&mut self, form: &Form, text: &str, role: SegmentRole) {
        let range = Some(self.form_range(form));
        self.push(BodySegment { text: text.to_string(), role, range, effect: None, definition: None });
    }

    /// Add a form not in the table as the original lisp (split into lines if it spans several lines).
    fn lisp(&mut self, start: usize, end: usize, base: u32) {
        self.source(start, end, base, SegmentRole::Lisp);
    }

    /// Add the source's `start..end` with role `role`. After a newline, start a new line, and keep the indentation as spaces of the difference from the statement head's column `base`.
    fn source(&mut self, start: usize, end: usize, base: u32, role: SegmentRole) {
        let src = self.reader.hy.src;
        // When flattening, if there is a comment in the text (a `;` outside a string), it cannot be flattened (the comment swallows the following text)
        if self.flat && has_comment(src.get(start..end).unwrap_or("")) {
            self.flat_broken = true;
        }
        let mut at = start;
        let mut first = true;
        for piece in src.get(start..end).unwrap_or("").split('\n') {
            let piece_start = at;
            at += piece.len() + 1;
            let (lead, body) = if first {
                (0, piece)
            } else if self.flat {
                // Flatten: newlines and indentation become one space (tight right after an open paren and right before a close paren)
                let trimmed = piece.trim_start_matches([' ', '\t']);
                let after_open = self.current().segments.last().is_some_and(|s| s.text.ends_with(['(', '[', '{']));
                if !after_open && !trimmed.starts_with([')', ']', '}']) && !trimmed.is_empty() {
                    self.text(" ", SegmentRole::Text);
                }
                (piece.len() - trimmed.len(), trimmed)
            } else {
                let trimmed = piece.trim_start_matches([' ', '\t']);
                let lead = piece.len() - trimmed.len();
                let indent = Indent { depth: self.indent.depth, pad: self.indent.pad + (lead as u32).saturating_sub(base) };
                self.start_line(piece_start + lead, indent);
                (lead, trimmed)
            };
            first = false;
            let body = body.trim_end_matches('\r');
            if !body.is_empty() {
                let s = piece_start + lead;
                let range = Some(self.range(s, s + body.len()));
                self.push(BodySegment { text: body.to_string(), role, range, effect: None, definition: None });
            }
        }
    }

    /// The text of a binding's type (the `bindings` type — `?` if unknown).
    fn binding_type(&mut self, index: Option<usize>) {
        let binding = index.and_then(|i| self.bindings.get(i));
        match binding.and_then(|b| b.type_ref.as_ref().map(|t| (t, b.annotation_range))) {
            Some((type_ref, annotation)) => {
                let definition = match type_ref {
                    TypeRef::Name { definition, .. } => definition.clone(),
                    TypeRef::Union { .. } | TypeRef::Apply { .. } | TypeRef::Unknown { .. } => None,
                };
                self.push(BodySegment {
                    text: type_text(type_ref),
                    role: SegmentRole::Type,
                    range: annotation,
                    effect: None,
                    definition,
                });
            }
            None => self.text("?", SegmentRole::UnknownType),
        }
    }

    /// The number of the binding that bound the name form.
    fn binding_of(&self, name: &Form) -> Option<usize> {
        let range = self.form_range(name);
        self.bindings.iter().position(|b| b.range == range)
    }

    // --- Statements ----------------------------------------------------------------------------------

    /// One statement of a body (`depth` = indentation level).
    /// `inline` = draw as a continuation of the current line (after the `→` of a match arm).
    fn statement(&mut self, form: &Form, indent: Indent, inline: bool) {
        // `(do a b …)` does not emit the `do` text but lists its contents (in statement position, at the current level; after an arm's `→`, the first continues on that line,
        // and the rest align to that column)
        if let Some(items) = live(form).filter(|items| {
            items.len() > 1 && items.first().and_then(|h| self.reader.hy.symbol(h)) == Some("do")
        }) {
            self.do_block(&items, indent, inline);
            return;
        }
        if !inline {
            self.start_line(form.span.start, indent);
        }
        let outer = std::mem::replace(&mut self.indent, indent);
        self.statement_body(form);
        self.indent = outer;
    }

    /// Draw sequential statements (`siblings[from..]`) at level `indent`, one statement at a time. A whole-line `;;` comment between statements becomes a `# …` line.
    fn statements(&mut self, siblings: &[&Form], from: usize, indent: Indent) {
        for i in from..siblings.len() {
            let gap_start = match i.checked_sub(1).and_then(|p| siblings.get(p)) {
                Some(previous) => previous.span.end,
                None => siblings[i].span.start,
            };
            self.comments(gap_start, siblings[i].span.start, indent);
            self.statement(siblings[i], indent, false);
        }
    }

    /// Turn whole-line comments (lines starting with `;`) in source `start..end` into `# …` lines (the first line = the remainder of the previous form's line is not looked at).
    fn comments(&mut self, start: usize, end: usize, indent: Indent) {
        let src = self.reader.hy.src;
        let gap = src.get(start..end).unwrap_or("");
        let mut at = start;
        for (i, piece) in gap.split('\n').enumerate() {
            let piece_start = at;
            at += piece.len() + 1;
            let trimmed = piece.trim_start_matches([' ', '\t']);
            if i == 0 || !trimmed.starts_with(';') {
                continue;
            }
            let lead = piece.len() - trimmed.len();
            let body = trimmed.trim_end_matches('\r');
            let text = body.trim_start_matches(';').trim_start();
            let s = piece_start + lead;
            self.start_line(s, indent);
            let range = Some(self.range(s, s + body.len()));
            self.push(BodySegment {
                text: format!("# {text}"),
                role: SegmentRole::Comment,
                range,
                effect: None,
                definition: None,
            });
        }
    }

    /// The contents of `(do a b …)`.
    fn do_block(&mut self, items: &[&Form], indent: Indent, inline: bool) {
        if !inline {
            self.statements(items, 1, indent);
            return;
        }
        let column = self.cursor();
        let aligned = self.aligned(column);
        self.statement(items[1], indent, true);
        for i in 2..items.len() {
            self.comments(items[i - 1].span.end, items[i].span.start, aligned);
            self.statement(items[i], aligned, false);
        }
    }

    fn statement_body(&mut self, form: &Form) {
        let base = self.column(form.span.start);
        let Some(items) = live(form) else {
            self.expression(form, base);
            return;
        };
        let mark = (self.lines.len(), self.current().segments.len());
        let head = items.first().and_then(|h| match h.node {
            Node::Symbol | Node::Keyword => Some(self.reader.hy.text(h)),
            Node::Seq { .. }
            | Node::Str { .. }
            | Node::Number
            | Node::Prefixed { .. }
            | Node::Annotated { .. }
            | Node::Discarded
            | Node::Tagged { .. } => None,
        });
        let drawn = match head {
            Some("<-") => self.bind(&items, base),
            Some("val" | "var" | "lazy" | "session") => self.declaration(&items, base),
            Some("setv") => self.setv(&items, base),
            Some(":=") => self.assign(&items, base),
            Some("return" | "resume") => self.keyword_statement(&items, base),
            Some("continue" | "break") => self.bare_keyword(&items),
            Some("raise") => self.raise(&items, base),
            Some("del") => self.del(&items, base),
            Some("assert") => self.assert(&items, base),
            Some("when" | "while") => self.when(&items, base),
            Some("cond") => self.cond_arms(&items),
            Some("try") => self.try_block(&items),
            // An if after an arm's `→` (continuing the current line) is a ternary if it fits on one line
            Some("if") if !self.current().segments.is_empty() => {
                match self.capture_flat(|p| p.ternary(&items)).filter(|s| segments_width(s) <= TERNARY_WIDTH) {
                    Some(segments) => {
                        self.extend(segments);
                        true
                    }
                    None => self.if_else(&items, base),
                }
            }
            Some("if") => self.if_else(&items, base),
            Some("match") => self.match_arms(&items, base),
            Some("for") => self.for_loop(&items, base),
            // Calls, unknown heads, and parens with no head are read as expressions (parens that were not replaced are islands of lisp)
            Some(_) | None => {
                self.expression(form, base);
                true
            }
        };
        if !drawn {
            // It could not be read as a table form (the name is not a symbol, a value is missing …) — not drawn by guessing, kept as lisp. Half-drawn text and lines are discarded
            self.lines.truncate(mark.0);
            let line = self.current();
            line.segments.truncate(mark.1);
            if mark.1 == 0 {
                line.binding = None;
                line.warning = None;
            }
            self.lisp(form.span.start, form.span.end, base);
        }
    }

    /// The statements inside a block (`siblings[from..]` one level deeper, one statement at a time, with comments between). `column` = the block word's column.
    fn block(&mut self, column: u32, siblings: &[&Form], from: usize) {
        let inner = self.aligned(column);
        let inner = Indent { depth: inner.depth + 1, pad: inner.pad };
        self.statements(siblings, from, inner);
    }

    /// `(when c …)` → `when c` + indented contents.
    fn when(&mut self, items: &[&Form], base: u32) -> bool {
        let [head, condition, _body @ ..] = items else { return false };
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(condition, base);
        self.block(column, items, 2);
        true
    }

    /// `(continue)` / `(break)`.
    fn bare_keyword(&mut self, items: &[&Form]) -> bool {
        let [head] = items else { return false };
        self.word(head, SegmentRole::Keyword);
        true
    }

    /// `(del x …)` → `del x, …`.
    fn del(&mut self, items: &[&Form], base: u32) -> bool {
        let [head, targets @ ..] = items else { return false };
        if targets.is_empty() {
            return false;
        }
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        for (i, target) in targets.iter().enumerate() {
            if i > 0 {
                self.text(", ", SegmentRole::Text);
            }
            self.expression(target, base);
        }
        true
    }

    /// `(assert c)` / `(assert c m)` → `assert c` / `assert c, m`.
    fn assert(&mut self, items: &[&Form], base: u32) -> bool {
        let (head, condition, message) = match items {
            [head, condition] => (*head, *condition, None),
            [head, condition, message] => (*head, *condition, Some(*message)),
            [] | [_] | [_, _, _, _, ..] => return false,
        };
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(condition, base);
        if let Some(message) = message {
            self.text(", ", SegmentRole::Text);
            self.expression(message, base);
        }
        true
    }

    /// `(raise)` / `(raise e)` / `(raise e :from c)` → `raise` / `raise e` / `raise e from c`.
    fn raise(&mut self, items: &[&Form], base: u32) -> bool {
        let (head, error, cause) = match items {
            [head] => (*head, None, None),
            [head, error] => (*head, Some(*error), None),
            [head, error, from, cause] if self.reader.hy.text(from) == ":from" => (*head, Some(*error), Some(*cause)),
            [] | [_, _, _] | [_, _, _, _] | [_, _, _, _, _, ..] => return false,
        };
        self.word(head, SegmentRole::Keyword);
        if let Some(error) = error {
            self.text(" ", SegmentRole::Text);
            self.expression(error, base);
        }
        if let Some(cause) = cause {
            self.text(" ", SegmentRole::Text);
            self.text("from", SegmentRole::Keyword);
            self.text(" ", SegmentRole::Text);
            self.expression(cause, base);
        }
        true
    }

    /// `(cond c x c y … True z)` → `cond` + `c → x` per arm (align the condition widths and line up the `→` vertically; the last `True` is `else`).
    fn cond_arms(&mut self, items: &[&Form]) -> bool {
        let [head, rest @ ..] = items else { return false };
        if rest.is_empty() || !rest.len().is_multiple_of(2) {
            return false;
        }
        let arms: Vec<(&Form, &Form)> = rest.chunks(2).map(|pair| (pair[0], pair[1])).collect();
        let last = arms.len() - 1;
        // Measure the conditions one line at a time first — if any condition does not fit on one line, the whole cond stays as lisp
        let mut shown = Vec::new();
        for (i, (condition, _)) in arms.iter().enumerate() {
            let otherwise = i == last && self.reader.hy.symbol(condition) == Some("True");
            let captured = self.capture_flat(|p| {
                if otherwise {
                    p.text("else", SegmentRole::Keyword);
                } else {
                    let base = p.column(condition.span.start);
                    p.expression(condition, base);
                }
                true
            });
            match captured {
                Some(segments) => shown.push(segments),
                None => return false,
            }
        }
        let width = |segments: &[BodySegment]| segments.iter().map(|s| s.text.chars().count()).sum::<usize>();
        let widest = shown.iter().map(|s| width(s)).max().unwrap_or(0);
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        let at = self.aligned(column);
        let arm_indent = Indent { depth: at.depth + 1, pad: at.pad };
        for ((condition, result), segments) in arms.iter().zip(shown) {
            self.start_line(condition.span.start, arm_indent);
            let fill = widest - width(&segments);
            self.extend(segments);
            self.text(&format!("{} → ", " ".repeat(fill)), SegmentRole::Text);
            self.statement(result, arm_indent, true);
        }
        true
    }

    /// `(try … (except [e T] …) (else …) (finally …))` → `try` + contents, `except T as e` + contents … (clause words align to the try's column).
    fn try_block(&mut self, items: &[&Form]) -> bool {
        let [head, rest @ ..] = items else { return false };
        // Find where the clauses start (the parens of except / else / finally). Everything before that is the try's contents
        let clause_word = |f: &Form| -> Option<&str> {
            let items = live(f)?;
            let word = self.reader.hy.symbol(items.first()?)?;
            matches!(word, "except" | "else" | "finally").then_some(word)
        };
        let split = rest.iter().position(|f| clause_word(f).is_some()).unwrap_or(rest.len());
        let (body, clauses) = rest.split_at(split);
        if clauses.iter().any(|f| clause_word(f).is_none()) {
            return false;
        }
        // Measure the except head `[e T]` / `[T]` / `[]` / `[e [A B]]` first
        let mut heads = Vec::new();
        for clause in clauses {
            let parts = live(clause).unwrap_or_default();
            let word = clause_word(clause).unwrap_or("");
            let captured = self.capture_flat(|p| {
                p.word(parts[0], SegmentRole::Keyword);
                if word != "except" {
                    return true;
                }
                let Some(binder) = parts.get(1).and_then(|b| b.bracket_items()) else { return false };
                let binder: Vec<&Form> = binder.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
                let (name, kind) = match binder.as_slice() {
                    [] => (None, None),
                    [kind] => (None, Some(*kind)),
                    [name, kind] if matches!(name.node, Node::Symbol) => (Some(*name), Some(*kind)),
                    [_, _] | [_, _, _, ..] => return false,
                };
                if let Some(kind) = kind {
                    p.text(" ", SegmentRole::Text);
                    match kind.bracket_items() {
                        Some(kinds) => {
                            let kinds: Vec<&Form> = kinds.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
                            p.text("(", SegmentRole::Text);
                            for (i, k) in kinds.iter().enumerate() {
                                if i > 0 {
                                    p.text(", ", SegmentRole::Text);
                                }
                                p.expression(k, p.column(k.span.start));
                            }
                            p.text(")", SegmentRole::Text);
                        }
                        None => p.expression(kind, p.column(kind.span.start)),
                    }
                }
                if let Some(name) = name {
                    p.text(" ", SegmentRole::Text);
                    p.text("as", SegmentRole::Keyword);
                    p.text(" ", SegmentRole::Text);
                    p.word(name, SegmentRole::Name);
                }
                true
            });
            match captured {
                Some(segments) => heads.push(segments),
                None => return false,
            }
        }
        let column = self.cursor();
        let at = self.aligned(column);
        self.word(head, SegmentRole::Keyword);
        self.block(column, &items[..1 + body.len()], 1);
        for (clause, segments) in clauses.iter().zip(heads) {
            self.start_line(clause.span.start, at);
            self.extend(segments);
            let parts = live(clause).unwrap_or_default();
            let from = if clause_word(clause) == Some("except") { 2 } else { 1 };
            self.block(column, &parts, from);
        }
        true
    }

    /// `(if c a b)` → `if c` + a, `else` + b (else aligns to the if's column, and the line number is b's line).
    fn if_else(&mut self, items: &[&Form], base: u32) -> bool {
        let (head, condition, then, otherwise) = match items {
            [head, condition, then] => (*head, *condition, *then, None),
            [head, condition, then, otherwise] => (*head, *condition, *then, Some(*otherwise)),
            [] | [_] | [_, _] | [_, _, _, _, _, ..] => return false,
        };
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(condition, base);
        let at = self.aligned(column);
        self.block(column, &[then], 0);
        if let Some(otherwise) = otherwise {
            self.start_line(otherwise.span.start, at);
            self.text("else", SegmentRole::Keyword);
            self.block(column, &[otherwise], 0);
        }
        true
    }

    /// `(match v p x p :if g y …)` → `match v` + `p → x` per arm (align the arms' pattern widths and line up the `→` vertically).
    fn match_arms(&mut self, items: &[&Form], base: u32) -> bool {
        let [head, subject, rest @ ..] = items else { return false };
        let mut arms: Vec<(&Form, Option<&Form>, &Form)> = Vec::new();
        let mut i = 0;
        while i < rest.len() {
            let guarded = rest.get(i + 1).is_some_and(|k| {
                matches!(k.node, Node::Keyword) && self.reader.hy.text(k) == ":if"
            });
            match (guarded, rest.get(i + 1), rest.get(i + 2), rest.get(i + 3)) {
                (true, Some(_), Some(guard), Some(result)) => {
                    arms.push((rest[i], Some(*guard), *result));
                    i += 4;
                }
                (false, Some(result), _, _) => {
                    arms.push((rest[i], None, *result));
                    i += 2;
                }
                (true, _, _, _) | (false, None, _, _) => return false,
            }
        }
        // Measure the arms' patterns (and the :if conditions) one line at a time first — if even one pattern cannot be drawn, the whole match stays as lisp
        let mut shown = Vec::new();
        for (pattern, guard, _) in &arms {
            let captured = self.capture_flat(|p| {
                let drawn = p.pattern(pattern);
                if let (true, Some(guard)) = (drawn, guard) {
                    p.text(" ", SegmentRole::Text);
                    p.text("if", SegmentRole::Keyword);
                    p.text(" ", SegmentRole::Text);
                    let base = p.column(guard.span.start);
                    p.expression(guard, base);
                }
                drawn
            });
            match captured {
                Some(segments) => shown.push(segments),
                None => return false,
            }
        }
        let width = |segments: &[BodySegment]| segments.iter().map(|s| s.text.chars().count()).sum::<usize>();
        let widest = shown.iter().map(|s| width(s)).max().unwrap_or(0);
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(subject, base);
        let at = self.aligned(column);
        let arm_indent = Indent { depth: at.depth + 1, pad: at.pad };
        for ((pattern, _, result), segments) in arms.iter().zip(shown) {
            self.start_line(pattern.span.start, arm_indent);
            let fill = widest - width(&segments);
            self.extend(segments);
            self.text(&format!("{} → ", " ".repeat(fill)), SegmentRole::Text);
            self.statement(result, arm_indent, true);
        }
        true
    }

    /// One match pattern (`(Cls)` → `Cls`, `(Cls :k p)` → `Cls(k=p)`, `(| p q)` → `p | q`, `[p q]` → `[p, q]`; names and literals are
    /// kept as they are). false for a form not in the table (the whole match stays as lisp).
    fn pattern(&mut self, form: &Form) -> bool {
        match &form.node {
            Node::Symbol | Node::Str { .. } | Node::Number | Node::Keyword => {
                self.word(form, SegmentRole::Text);
                true
            }
            Node::Seq { delim: doeff_indexer::hy_index::reader::Delim::Paren, .. } => {
                let items = live(form).unwrap_or_default();
                let Some((head, args)) = items.split_first() else { return false };
                match self.reader.hy.symbol(head) {
                    Some("|") => self.pattern_list(args, " | "),
                    Some(name) if name.starts_with(|c: char| c.is_ascii_uppercase()) => {
                        let qualified = self.reader.scope.qualify(name);
                        let range = Some(self.form_range(head));
                        let definition = self.world.types.get(&qualified).cloned();
                        let text = name.to_string();
                        self.push(BodySegment { text, role: SegmentRole::Call, range, effect: None, definition });
                        if args.is_empty() {
                            return true;
                        }
                        self.text("(", SegmentRole::Text);
                        let drawn = self.pattern_arguments(args);
                        self.text(")", SegmentRole::Text);
                        drawn
                    }
                    Some(_) | None => false,
                }
            }
            Node::Seq { delim: doeff_indexer::hy_index::reader::Delim::Bracket, items } => {
                let items: Vec<&Form> = items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
                self.text("[", SegmentRole::Text);
                let drawn = self.pattern_list(&items, ", ");
                self.text("]", SegmentRole::Text);
                drawn
            }
            Node::Seq { .. }
            | Node::Prefixed { .. }
            | Node::Annotated { .. }
            | Node::Discarded
            | Node::Tagged { .. } => false,
        }
    }

    /// List patterns separated by `sep`.
    fn pattern_list(&mut self, items: &[&Form], sep: &str) -> bool {
        for (i, item) in items.iter().enumerate() {
            if i > 0 {
                self.text(sep, SegmentRole::Text);
            }
            if !self.pattern(item) {
                return false;
            }
        }
        true
    }

    /// The arguments of a class pattern (`a b :k p` → `a, b, k=p`).
    fn pattern_arguments(&mut self, args: &[&Form]) -> bool {
        let mut i = 0;
        let mut first = true;
        while i < args.len() {
            if !first {
                self.text(", ", SegmentRole::Text);
            }
            first = false;
            let arg = args[i];
            if matches!(arg.node, Node::Keyword) {
                let Some(value) = args.get(i + 1) else { return false };
                let key = self.reader.hy.text(arg).trim_start_matches(':').to_string();
                self.shown(arg, &key, SegmentRole::Text);
                self.text("=", SegmentRole::Text);
                if !self.pattern(value) {
                    return false;
                }
                i += 2;
            } else {
                if !self.pattern(arg) {
                    return false;
                }
                i += 1;
            }
        }
        true
    }

    /// `(for [x xs] …)` → `for x in xs` + indented contents.
    fn for_loop(&mut self, items: &[&Form], base: u32) -> bool {
        let [head, binder, _body @ ..] = items else { return false };
        let Some(pairs) = binder.bracket_items() else { return false };
        let pairs: Vec<&Form> = pairs.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
        let [target, iterable] = pairs.as_slice() else { return false };
        let Some(shown) = self.capture(|p| p.target(target)) else { return false };
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.extend(shown);
        self.text(" ", SegmentRole::Text);
        self.text("in", SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(iterable, base);
        self.block(column, items, 2);
        true
    }

    /// The name(s) of the iteration (`x` → `x`, `[a b]` → `a, b`). false for anything else.
    fn target(&mut self, form: &Form) -> bool {
        let grouped = match &form.node {
            Node::Seq { delim: Delim::Bracket | Delim::Tuple, items } => Some(items.as_slice()),
            Node::Seq { delim: Delim::Paren | Delim::Brace | Delim::Set, .. }
            | Node::Symbol
            | Node::Keyword
            | Node::Str { .. }
            | Node::Number
            | Node::Prefixed { .. }
            | Node::Annotated { .. }
            | Node::Discarded
            | Node::Tagged { .. } => None,
        };
        match grouped {
            Some(items) => {
                let items: Vec<&Form> = items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
                if items.is_empty() || !items.iter().all(|i| matches!(i.node, Node::Symbol)) {
                    return false;
                }
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        self.text(", ", SegmentRole::Text);
                    }
                    self.word(item, SegmentRole::Name);
                }
                true
            }
            None if matches!(form.node, Node::Symbol) => {
                self.word(form, SegmentRole::Name);
                true
            }
            None => false,
        }
    }

    /// Of the parens in an expression that were not replaced, draw those with a form in the table on one line (false if they cannot be drawn — they stay as islands of lisp). `wrap` = whether to wrap
    /// low-precedence forms (ternary, lambda) in parentheses (in an operand of an operation or the target of a method).
    fn inline_form(&mut self, form: &Form, wrap: bool) -> bool {
        let items = live(form).unwrap_or_default();
        match items.first().and_then(|h| self.reader.hy.symbol(h)) {
            Some("lfor" | "gfor" | "sfor" | "dfor") => self.comprehension(form),
            Some("if") => self.wrapped(wrap, |p| p.ternary(&items)),
            Some("fn") => self.wrapped(wrap, |p| p.lambda(&items)),
            Some("cut") => self.slice(&items),
            Some(_) | None => false,
        }
    }

    /// The value of a binding or return. `if` is a ternary (`a if c else b`) if it fits on one line, and opens vertically if not. `cond`, `match`, `do`, and `try` open vertically
    /// even in value position (the first line continues the current line, and the contents align to the block word's column). Anything else is an expression.
    fn value(&mut self, form: &Form, base: u32) {
        let items = live(form).unwrap_or_default();
        match items.first().and_then(|h| self.reader.hy.symbol(h)) {
            Some("if") => {
                let shown = self.capture_flat(|p| p.ternary(&items)).filter(|s| segments_width(s) <= TERNARY_WIDTH);
                match shown {
                    Some(segments) => self.extend(segments),
                    None => self.statement(form, self.indent, true),
                }
            }
            Some("cond" | "match" | "do" | "try") => self.statement(form, self.indent, true),
            Some(_) | None => self.expression(form, base),
        }
    }

    /// Wrap the text that `f` draws in parentheses if `wrap`.
    fn wrapped(&mut self, wrap: bool, f: impl FnOnce(&mut Self) -> bool) -> bool {
        if wrap {
            self.text("(", SegmentRole::Text);
        }
        let drawn = f(self);
        if wrap {
            self.text(")", SegmentRole::Text);
        }
        drawn
    }

    /// `(if c a b)` → `a if c else b`.
    fn ternary(&mut self, items: &[&Form]) -> bool {
        let [head, condition, then, otherwise] = items else { return false };
        self.expression(then, self.column(then.span.start));
        self.text(" ", SegmentRole::Text);
        self.shown(head, "if", SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(condition, self.column(condition.span.start));
        self.text(" ", SegmentRole::Text);
        self.text("else", SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(otherwise, self.column(otherwise.span.start));
        true
    }

    /// `(fn [x] e)` → `x ⇒ e`, `(fn [a b] e)` → `(a, b) ⇒ e`, `(fn [] e)` → `() ⇒ e`. Arguments are names only (defaults and `#*` are not in the table); the body is one expression.
    fn lambda(&mut self, items: &[&Form]) -> bool {
        let [_, params, body] = items else { return false };
        let Some(params) = params.bracket_items() else { return false };
        let params: Vec<&Form> = params.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
        if !params.iter().all(|p| self.reader.hy.symbol(p).is_some_and(|s| !s.starts_with(['&', '#']))) {
            return false;
        }
        match params.as_slice() {
            [only] => self.word(only, SegmentRole::Name),
            [] | [_, _, ..] => {
                self.text("(", SegmentRole::Text);
                for (i, p) in params.iter().enumerate() {
                    if i > 0 {
                        self.text(", ", SegmentRole::Text);
                    }
                    self.word(p, SegmentRole::Name);
                }
                self.text(")", SegmentRole::Text);
            }
        }
        self.text(" ⇒ ", SegmentRole::Text);
        self.expression(body, self.column(body.span.start));
        true
    }

    /// `(cut xs a b)` → `xs[a:b]` (a `None` end is empty; `(cut xs)` → `xs[:]`, `(cut xs b)` → `xs[:b]`, `(cut xs a b s)` → `xs[a:b:s]`).
    fn slice(&mut self, items: &[&Form]) -> bool {
        let (target, bounds): (&Form, Vec<Option<&Form>>) = match items {
            [_, target] => (*target, vec![None, None]),
            [_, target, stop] => (*target, vec![None, Some(*stop)]),
            [_, target, start, stop] => (*target, vec![Some(*start), Some(*stop)]),
            [_, target, start, stop, step] => (*target, vec![Some(*start), Some(*stop), Some(*step)]),
            [] | [_] | [_, _, _, _, _, _, ..] => return false,
        };
        let operator = live(target)
            .and_then(|t| t.first().and_then(|h| self.reader.hy.symbol(h)))
            .is_some_and(|h| {
                matches!(
                    h,
                    "+" | "-" | "*" | "/" | "//" | "%" | "**" | "and" | "or" | "not" | "=" | "!=" | "<" | "<=" | ">"
                        | ">=" | "is" | "is-not" | "in" | "not-in" | "|" | "&" | "^" | "if" | "fn"
                )
            });
        self.wrapped(operator, |p| {
            p.expression(target, p.column(target.span.start));
            true
        });
        self.text("[", SegmentRole::Text);
        for (i, bound) in bounds.iter().enumerate() {
            if i > 0 {
                self.text(":", SegmentRole::Text);
            }
            if let Some(bound) = bound.filter(|b| self.reader.hy.symbol(b) != Some("None")) {
                self.expression(bound, self.column(bound.span.start));
            }
        }
        self.text("]", SegmentRole::Text);
        true
    }

    /// `(lfor x xs :if c e)` → `[e for x in xs if c]` (gfor is `( … )`, sfor is `{ … }`). Clauses not in the table such as `:setv` and `:do`, and
    /// anything that does not fit on one line are false (they stay as islands of lisp).
    fn comprehension(&mut self, form: &Form) -> bool {
        let items = live(form).unwrap_or_default();
        let Some((head, args)) = items.split_first() else { return false };
        let (open, close, keyed) = match self.reader.hy.symbol(head) {
            Some("lfor") => ("[", "]", false),
            Some("gfor") => ("(", ")", false),
            Some("sfor") => ("{", "}", false),
            Some("dfor") => ("{", "}", true),
            Some(_) | None => return false,
        };
        // For dfor, the last two are the key and value (`{k: v for …}`)
        let (key, body, clauses) = match (keyed, args) {
            (true, [clauses @ .., key, value]) => (Some(*key), *value, clauses),
            (false, [clauses @ .., body]) => (None, *body, clauses),
            (true, [] | [_]) | (false, []) => return false,
        };
        enum Clause<'f> {
            For(&'f Form, &'f Form),
            If(&'f Form),
        }
        let mut parsed = Vec::new();
        let mut i = 0;
        while i < clauses.len() {
            let item = clauses[i];
            match (&item.node, clauses.get(i + 1)) {
                (Node::Keyword, Some(cond)) if self.reader.hy.text(item) == ":if" && !parsed.is_empty() => {
                    parsed.push(Clause::If(cond));
                }
                (Node::Keyword, _) => return false,
                (_, Some(iterable)) => parsed.push(Clause::For(item, iterable)),
                (_, None) => return false,
            }
            i += 2;
        }
        if !matches!(parsed.first(), Some(Clause::For(..))) {
            return false;
        }
        self.text(open, SegmentRole::Text);
        if let Some(key) = key {
            self.expression(key, self.column(key.span.start));
            self.text(": ", SegmentRole::Text);
        }
        self.expression(body, self.column(body.span.start));
        for clause in parsed {
            self.text(" ", SegmentRole::Text);
            match clause {
                Clause::For(target, iterable) => {
                    self.text("for", SegmentRole::Keyword);
                    self.text(" ", SegmentRole::Text);
                    if !self.target(target) {
                        return false;
                    }
                    self.text(" ", SegmentRole::Text);
                    self.text("in", SegmentRole::Keyword);
                    self.text(" ", SegmentRole::Text);
                    self.expression(iterable, self.column(iterable.span.start));
                }
                Clause::If(cond) => {
                    self.text("if", SegmentRole::Keyword);
                    self.text(" ", SegmentRole::Text);
                    self.expression(cond, self.column(cond.span.start));
                }
            }
        }
        self.text(close, SegmentRole::Text);
        true
    }

    /// `(<- x T e)` → `val T x ⇐ e`; a nameless `(<- e)` → a fired expression.
    fn bind(&mut self, items: &[&Form], base: u32) -> bool {
        let Some(shape) = bind_shape(&self.reader.hy, items) else { return false };
        let Some(name) = shape.name else {
            self.performed(shape.value, base);
            return true;
        };
        if !matches!(name.node, Node::Symbol) {
            return false;
        }
        let binding = self.binding_of(name);
        self.current().binding = binding;
        self.shown(items[0], "val", SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.binding_type(binding);
        self.text(" ", SegmentRole::Text);
        self.word(name, SegmentRole::Name);
        self.text(" ", SegmentRole::Text);
        self.text("⇐", SegmentRole::Bind);
        self.text(" ", SegmentRole::Text);
        self.expression(shape.value, base);
        if let Some(raise) = shape.absent_as_raise {
            // `:absent F` is not in the body text table — append it as the original lisp
            let keyword = items[items.len() - 2];
            self.text(" ", SegmentRole::Text);
            self.lisp(keyword.span.start, raise.span.end, base);
        }
        true
    }

    /// `(val x e)`, `(var x e)`, `(lazy val x e)`, `(session var x e)` (one line per pair if there are several pairs).
    fn declaration(&mut self, items: &[&Form], base: u32) -> bool {
        let head = items[0];
        let (modifier, word, start) = match BindingModifier::of(self.reader.hy.text(head)) {
            Some(_) => match items.get(1).filter(|w| matches!(self.reader.hy.symbol(w), Some("val" | "var"))) {
                Some(word) => (Some(head), *word, 2),
                None => return false,
            },
            None => (None, head, 1),
        };
        let pairs = binding_pairs(&self.reader.hy, &items[start.min(items.len())..]);
        if pairs.is_empty() || pairs.iter().any(|p| p.value.is_none() || !matches!(p.name.node, Node::Symbol)) {
            return false;
        }
        for (i, pair) in pairs.iter().enumerate() {
            if i > 0 {
                self.start_line(pair.name.span.start, self.indent);
            }
            let binding = self.binding_of(pair.name);
            self.current().binding = binding;
            if let Some(modifier) = modifier {
                self.word(modifier, SegmentRole::Keyword);
                self.text(" ", SegmentRole::Text);
            }
            self.word(word, SegmentRole::Keyword);
            self.text(" ", SegmentRole::Text);
            self.binding_type(binding);
            self.text(" ", SegmentRole::Text);
            self.word(pair.name, SegmentRole::Name);
            self.text(" ", SegmentRole::Text);
            let value = pair.value.expect("a pair with a missing value was rejected above");
            match (pair.bang, self.bang_inner(value)) {
                (Some(_), _) => {
                    self.text("⇐", SegmentRole::Bind);
                    self.text(" ", SegmentRole::Text);
                    self.expression(value, base);
                }
                (None, Some(inner)) => {
                    self.text("⇐", SegmentRole::Bind);
                    self.text(" ", SegmentRole::Text);
                    self.expression(inner, base);
                }
                (None, None) => {
                    self.text("=", SegmentRole::Assign);
                    self.text(" ", SegmentRole::Text);
                    self.value(value, base);
                }
            }
        }
        true
    }

    /// `(setv x e)` in a body → `setv x = e` + a warning mark.
    fn setv(&mut self, items: &[&Form], base: u32) -> bool {
        let pairs: Vec<&[&Form]> = items[1..].chunks(2).collect();
        if pairs.is_empty() || pairs.iter().any(|p| p.len() != 2 || self.written_place(p[0]).is_none()) {
            return false;
        }
        for (i, pair) in pairs.iter().enumerate() {
            let (name, value) = (pair[0], pair[1]);
            if i > 0 {
                self.start_line(name.span.start, self.indent);
            }
            if self.written_place(name) == Some(false) {
                // Rewriting contents `(setv (get x k) v)` / `(setv o.a v)` → `x[k] = v` / `o.a = v` (not a binding — no setv warning is attached)
                self.expression(name, base);
                self.text(" ", SegmentRole::Text);
                self.text("=", SegmentRole::Assign);
                self.text(" ", SegmentRole::Text);
                self.value(value, base);
                continue;
            }
            let shown = self.reader.hy.text(name).to_string();
            self.current().binding = self.binding_of(name);
            self.current().warning = Some(BodyWarning {
                kind: BodyWarningKind::Setv,
                message: format!(
                    "Use (val {shown} …) instead of setv, or (var {shown} …) and (:= {shown} …) to rewrite it [ADR-DOE-HY-006]"
                ),
            });
            self.word(items[0], SegmentRole::Keyword);
            self.text(" ", SegmentRole::Text);
            self.word(name, SegmentRole::Name);
            self.text(" ", SegmentRole::Text);
            self.text("=", SegmentRole::Assign);
            self.text(" ", SegmentRole::Text);
            self.value(value, base);
        }
        true
    }

    /// The target setv writes to: Some(true) for a bound name, Some(false) for rewriting contents (`(get x k)`, `(. o a)`, `o.a`), None otherwise.
    fn written_place(&self, target: &Form) -> Option<bool> {
        match self.reader.hy.symbol(target) {
            Some(name) => Some(!name.contains('.')),
            None => {
                let items = live(target)?;
                let head = self.reader.hy.symbol(items.first()?)?;
                matches!(head, "get" | ".").then_some(false)
            }
        }
    }

    /// `(:= x v)` → `x := v`.
    fn assign(&mut self, items: &[&Form], base: u32) -> bool {
        let pairs = binding_pairs(&self.reader.hy, &items[1..]);
        let [pair] = pairs.as_slice() else { return false };
        let Some(value) = pair.value.filter(|_| matches!(pair.name.node, Node::Symbol)) else { return false };
        self.current().binding = self.binding_of(pair.name);
        self.word(pair.name, SegmentRole::Name);
        self.text(" ", SegmentRole::Text);
        self.word(items[0], SegmentRole::Assign);
        self.text(" ", SegmentRole::Text);
        match pair.bang {
            Some(_) => self.performed(value, base),
            None => self.value(value, base),
        }
        true
    }

    /// `(return v)` / `(resume v)` → `return v` / `resume v`.
    fn keyword_statement(&mut self, items: &[&Form], base: u32) -> bool {
        match items {
            [head] => self.word(head, SegmentRole::Keyword),
            [head, value] => {
                self.word(head, SegmentRole::Keyword);
                self.text(" ", SegmentRole::Text);
                self.value(value, base);
            }
            [] | [_, _, _, ..] => return false,
        }
        true
    }

    /// The e of `(! e)` (None otherwise).
    fn bang_inner<'f>(&self, value: &'f Form) -> Option<&'f Form> {
        let items = live(value)?;
        let [head, inner] = items.as_slice() else { return None };
        (self.reader.hy.symbol(head) == Some("!")).then_some(*inner)
    }

    /// A fired expression (the e of `(<- e)` and of `(:= x ! e)`): for an effect, `E(a)` (the head has the effect role); otherwise put `!` in front.
    fn performed(&mut self, inner: &Form, base: u32) {
        if performed_effect(self.world, self.reader, self.names, inner).is_none() {
            self.text("!", SegmentRole::Text);
        }
        self.expression(inner, base);
    }

    // --- Expressions ----------------------------------------------------------------------------------

    /// One expression (in the replacement reading, in the form `f(a, b)`. Parens that were not replaced are drawn on one line if their form is in the table, otherwise they are an island of lisp.
    /// Literals of tuples, lists, sets, and dicts become `(a, b)`, `[a, b]`, `{a, b}`, and `{k: v}`).
    fn expression(&mut self, form: &Form, base: u32) {
        let rewrites = expression_rewrites(self.world, self.reader, self.names, form);
        let src = self.reader.hy.src;
        let reader = self.reader;
        let lines = &reader.lines;
        let offset = |r: &Range| (lines.offset(r.start), lines.offset(r.end));
        let rewritten: HashSet<(usize, usize)> = rewrites.iter().map(|r| offset(&r.range)).collect();
        let mut found = Vec::new();
        find_islands(form, &rewritten, &mut found);
        // What covers (islands and literals): decide how to draw first, and discard replacement edits within the covered range
        let mut covered: Vec<(usize, usize, Overlay)> = Vec::new();
        for island in found {
            let (s, e) = (island.span.start, island.span.end);
            let wrap = self.needs_wrap(island, &rewrites, &offset);
            let overlay = match self.capture_flat(|p| p.inline_form(island, wrap)) {
                Some(segments) => Overlay::Shown(segments),
                None => Overlay::Island,
            };
            covered.push((s, e, overlay));
        }
        let islands: Vec<(usize, usize)> = covered.iter().map(|(s, e, _)| (*s, *e)).collect();
        let mut literals = Vec::new();
        find_literals(form, &islands, &mut literals);
        for literal in literals {
            if let Some(segments) = self.capture_flat(|p| p.literal(literal)) {
                covered.push((literal.span.start, literal.span.end, Overlay::Shown(segments)));
            }
        }
        let spans: Vec<(usize, usize)> = covered.iter().map(|(s, e, _)| (*s, *e)).collect();
        let inside = |s: usize, e: usize| {
            spans.iter().any(|&(cs, ce)| s >= cs && e <= ce && !(s == e && (s == cs || s == ce)))
        };
        let mut overlays: Vec<(usize, usize, Overlay)> = rewrites
            .iter()
            .flat_map(|r| r.edits.iter())
            .map(|e| (offset(&e.range), e.text.as_str()))
            .filter(|((s, e), _)| !inside(*s, *e))
            .map(|((s, e), text)| (s, e, Overlay::Edit(text)))
            .collect();
        overlays.extend(covered);
        overlays.sort_by_key(|(s, e, _)| (*s, *e));
        let parts = heads(&rewrites, &offset);
        let mut at = form.span.start;
        for (s, e, overlay) in &overlays {
            if *s < at {
                continue;
            }
            self.expression_source(at, *s, base, &parts);
            match overlay {
                Overlay::Edit(text) => self.text(text, SegmentRole::Text),
                Overlay::Island => self.lisp(*s, *e, base),
                Overlay::Shown(segments) => self.extend(segments.clone()),
            }
            at = *e;
        }
        self.expression_source(at, form.span.end, base, &parts);
    }

    /// Whether to wrap an island in parentheses when drawn in a low-precedence form (ternary, lambda) — when the innermost enclosing replacement is an operation, `!`, or an attribute, and when it is
    /// the target of a method (the island of `(.m island …)`). Not wrapped in a call argument, a subscript, or a statement value position.
    fn needs_wrap(&self, island: &Form, rewrites: &[Rewrite], offset: &dyn Fn(&Range) -> (usize, usize)) -> bool {
        let (s, e) = (island.span.start, island.span.end);
        let enclosing = rewrites
            .iter()
            .filter(|r| {
                let (rs, re) = offset(&r.range);
                rs <= s && e <= re && (rs, re) != (s, e)
            })
            .min_by_key(|r| {
                let (rs, re) = offset(&r.range);
                re - rs
            })
            .map(|r| r.kind);
        match enclosing {
            None | Some(RewriteKind::Call) | Some(RewriteKind::Subscript) | Some(RewriteKind::Bind) => false,
            Some(RewriteKind::Method) => {
                // The target = the form right after a method's head (the one word of the preceding text `(.name`)
                let before = self.reader.hy.src.get(..s).unwrap_or("").trim_end();
                before.rsplit(char::is_whitespace).next().is_some_and(|word| word.starts_with("(."))
            }
            Some(RewriteKind::Infix) | Some(RewriteKind::Prefix) | Some(RewriteKind::Perform) | Some(RewriteKind::Attribute) => {
                true
            }
        }
    }

    /// Literals of tuples, lists, sets, and dicts (`#(a b)` → `(a, b)`, a single one → `(a,)`, `[a b]` → `[a, b]`, `#{a b}` → `{a, b}`, `{k v}` → `{k: v}`).
    /// Dicts whose keys are keywords (`:k`) and dicts with a mismatched number of elements are not drawn (kept as the original text).
    fn literal(&mut self, form: &Form) -> bool {
        let Node::Seq { delim, items } = &form.node else { return false };
        let items: Vec<&Form> = items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
        let (open, close) = match delim {
            Delim::Tuple => ("(", ")"),
            Delim::Bracket => ("[", "]"),
            Delim::Set | Delim::Brace => ("{", "}"),
            Delim::Paren => return false,
        };
        let keyed = matches!(delim, Delim::Brace);
        if keyed && (!items.len().is_multiple_of(2) || items.iter().step_by(2).any(|k| matches!(k.node, Node::Keyword))) {
            return false;
        }
        self.text(open, SegmentRole::Text);
        let step = if keyed { 2 } else { 1 };
        for (i, chunk) in items.chunks(step).enumerate() {
            if i > 0 {
                self.text(", ", SegmentRole::Text);
            }
            let first = chunk[0];
            self.expression(first, self.column(first.span.start));
            if let [_, value] = chunk {
                self.text(": ", SegmentRole::Text);
                self.expression(value, self.column(value.span.start));
            }
        }
        if matches!(delim, Delim::Tuple) && items.len() == 1 {
            self.text(",", SegmentRole::Text);
        }
        self.text(close, SegmentRole::Text);
        true
    }

    /// Source text within an expression (the call head is separated and given a role).
    fn expression_source(&mut self, start: usize, end: usize, base: u32, parts: &[Head]) {
        let mut at = start;
        for part in parts.iter().filter(|p| p.start >= start && p.end <= end) {
            if part.start < at {
                continue;
            }
            self.source(at, part.start, base, SegmentRole::Text);
            let range = Some(self.range(part.start, part.end));
            let text = self.src().get(part.start..part.end).unwrap_or("").to_string();
            let (role, effect) = match part.role {
                PartRole::Effect => (SegmentRole::Effect, Some(last_segment(&text).to_string())),
                PartRole::Defk
                | PartRole::Deff
                | PartRole::Type
                | PartRole::Function
                | PartRole::Builtin
                | PartRole::Local
                | PartRole::Method => (SegmentRole::Call, None),
            };
            self.push(BodySegment { text, role, range, effect, definition: part.definition.clone() });
            at = part.end;
        }
        self.source(at, end, base, SegmentRole::Text);
    }
}

/// Whether the source text (starting at a token boundary) has a comment — a `;` outside a string (including `"…"` and `\"` escapes).
fn has_comment(text: &str) -> bool {
    let mut in_string = false;
    let mut escaped = false;
    for c in text.chars() {
        match (in_string, escaped, c) {
            (true, true, _) => escaped = false,
            (true, false, '\\') => escaped = true,
            (true, false, '"') => in_string = false,
            (false, _, '"') => in_string = true,
            (false, _, ';') => return true,
            (true, false, _) | (false, _, _) => {}
        }
    }
    false
}

/// The upper limit of characters to draw a ternary form (`a if c else b`) on one line. An `if` exceeding this opens into a vertical if / else.
const TERNARY_WIDTH: usize = 100;

/// The width of a run of text (how the plane counts text — one character = one column).
fn segments_width(segments: &[BodySegment]) -> usize {
    segments.iter().map(|s| s.text.chars().count()).sum()
}

/// One call head (a byte range).
struct Head {
    start: usize,
    end: usize,
    role: PartRole,
    definition: Option<Location>,
}

/// The replacement parts (call heads) in text order.
fn heads(rewrites: &[Rewrite], offset: &dyn Fn(&Range) -> (usize, usize)) -> Vec<Head> {
    let mut out: Vec<Head> = rewrites
        .iter()
        .flat_map(|r| r.parts.iter())
        .map(|p| {
            let (start, end) = offset(&p.range);
            Head { start, end, role: p.role, definition: p.definition.clone() }
        })
        .collect();
    out.sort_by_key(|h| (h.start, h.end));
    out
}

/// Collect the parenthesized forms that were not replaced (islands of lisp). Do not descend into islands (an island is shown as the original lisp). A quote remains as a literal.
fn find_islands<'f>(form: &'f Form, rewritten: &HashSet<(usize, usize)>, out: &mut Vec<&'f Form>) {
    match &form.node {
        Node::Seq { delim, items } => {
            let span = (form.span.start, form.span.end);
            if matches!(delim, doeff_indexer::hy_index::reader::Delim::Paren) && !rewritten.contains(&span) {
                out.push(form);
                return;
            }
            for item in items.iter().filter(|i| !matches!(i.node, Node::Discarded)) {
                find_islands(item, rewritten, out);
            }
        }
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => {
            find_islands(inner, rewritten, out)
        }
        Node::Annotated { target: Some(target), .. } => find_islands(target, rewritten, out),
        Node::Prefixed { inner: None, .. }
        | Node::Tagged { inner: None }
        | Node::Annotated { target: None, .. }
        | Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Discarded => {}
    }
}

/// Collect the outermost literals outside islands (tuples, lists, sets, dicts) (do not descend into literals — their insides are read as expressions when the literal is drawn).
fn find_literals<'f>(form: &'f Form, islands: &[(usize, usize)], out: &mut Vec<&'f Form>) {
    if islands.iter().any(|&(s, e)| s <= form.span.start && form.span.end <= e) {
        return;
    }
    match &form.node {
        Node::Seq { delim: Delim::Tuple | Delim::Bracket | Delim::Set | Delim::Brace, .. } => out.push(form),
        Node::Seq { delim: Delim::Paren, items } => {
            for item in items.iter().filter(|i| !matches!(i.node, Node::Discarded)) {
                find_literals(item, islands, out);
            }
        }
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => find_literals(inner, islands, out),
        Node::Annotated { target: Some(target), .. } => find_literals(target, islands, out),
        Node::Prefixed { inner: None, .. }
        | Node::Tagged { inner: None }
        | Node::Annotated { target: None, .. }
        | Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Discarded => {}
    }
}

/// Turn a type expression into body-text spelling (`A | B`, `H[a, b]`).
fn type_text(type_ref: &TypeRef) -> String {
    match type_ref {
        TypeRef::Name { name, .. } => name.clone(),
        TypeRef::Union { members } => members.iter().map(type_text).collect::<Vec<_>>().join(" | "),
        TypeRef::Apply { head, args } => {
            format!("{}[{}]", type_text(head), args.iter().map(type_text).collect::<Vec<_>>().join(", "))
        }
        TypeRef::Unknown { text } => text.clone(),
    }
}

/// The last separator of a name including the module.
fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}

#[cfg(test)]
mod tests {
    use super::super::signatures::read_file;
    use super::*;

    /// The part that the test looks at (bindings and bodies) — does not read the construction of the one-file reading (`file_view.rs`), and adds only bodies on top of the heading reading.
    struct Read {
        bindings: Vec<Binding>,
        bodies: Vec<Body>,
    }

    /// Read the bindings and bodies of one file.
    fn file_signatures(world: &World, root: &std::path::Path, rel: &str, source: &str) -> Read {
        read_file(world, root, rel, source, |reader, forms, heads| {
            let bodies = file_bodies(world, reader, forms, &heads.bindings);
            Read { bindings: heads.bindings, bodies }
        })
    }

    /// Build the table by listing the root files, and read the headings and bodies of one file.
    fn read(files: &[(&str, &str)], target: &str) -> Read {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        let read = file_signatures(&world, &root, target, source);
        assert_same_for_one_file(&read, &root, target, source);
        read
    }

    /// Even in the table of a single-file run (only the files of modules the written file names), bindings and bodies are the same as in the whole-repo table.
    fn assert_same_for_one_file(whole: &Read, root: &std::path::Path, rel: &str, source: &str) {
        let named = World::build_for_file(root, rel, source);
        let read = file_signatures(&named, root, rel, source);
        assert_eq!((&read.bindings, &read.bodies), (&whole.bindings, &whole.bodies), "{rel}");
    }

    /// Read the sample fixture (lines 1 to 94 of controllers/messaging/core/conversation_input.hy at agora-controllers d89796e67, copied
    /// as is with the same line numbers, plus an intent that has only the definitions of the effects that are fired).
    fn sample() -> (String, Read) {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/body_view");
        let root = root.canonicalize().unwrap();
        let rel = "controllers/messaging/core/conversation_input.hy";
        let source = std::fs::read_to_string(root.join(rel)).unwrap();
        let world = World::build(&root, Some((rel, &source)));
        let read = file_signatures(&world, &root, rel, &source);
        assert_same_for_one_file(&read, &root, rel, &source);
        (source, read)
    }

    /// Spell a line with the same text as the plane (one level = two spaces).
    fn render(line: &BodyLine) -> String {
        let text: String = line.segments.iter().map(|s| s.text.as_str()).collect();
        format!("{}{}{}", "  ".repeat(line.depth as usize), " ".repeat(line.pad as usize), text)
    }

    /// The lines of a definition's body as (the 1-based source line, spelling).
    fn rendered(read: &Read, name: &str) -> Vec<(u32, String)> {
        let body = read.bodies.iter().find(|b| b.name == name).unwrap_or_else(|| panic!("no body for {}", name));
        body.lines.iter().map(|l| (l.line + 1, render(l))).collect()
    }

    fn line_at<'b>(read: &'b Read, name: &str, line: u32) -> &'b BodyLine {
        let body = read.bodies.iter().find(|b| b.name == name).unwrap();
        body.lines.iter().find(|l| l.line + 1 == line).unwrap_or_else(|| panic!("{} has no line {}", name, line))
    }

    /// V4: the binding, call, effect-firing, and value lines of the sample's run-input-request and judged (the body lines of `artifacts/v5/entity.html`)
    /// are character-for-character the same as the sample's body lines (the control shapes when / match are U3).
    #[test]
    fn sample_bodies_match_the_reading_plane_binding_rows() {
        let (_, read) = sample();
        let judged = rendered(&read, "judged");
        let expect_judged = [
            (50, "val str | None target ⇐ target-of(request)"),
            (51, "var ? row = None"),
            (58, "val Judgment judgment ⇐ judge(request, row)"),
            (59, "judgment"),
        ];
        for (line, text) in expect_judged {
            assert!(judged.contains(&(line, text.to_string())), "judged line {}: expected {:?}\nactual {:#?}", line, text, judged);
        }
        let run = rendered(&read, "run-input-request");
        let expect_run = [
            (85, "val InputDone | InputRejected | None outcome ⇐ outcome-of(request)"),
            (88, "val IntakeSettlement settlement ⇐ settlement-of(request.request-id, ROUTE-INPUT, outcome)"),
            (89, "val IntakeSettleLanded | IntakeSettleRefused | IntakeUnreachable settled ⇐ SettleIntake(settlement)"),
        ];
        for (line, text) in expect_run {
            assert!(run.contains(&(line, text.to_string())), "run-input-request line {}: expected {:?}\nactual {:#?}", line, text, run);
        }
        // The head of a fired effect has the effect role (the plane draws the icon), and a defk call has the call role
        let settled = line_at(&read, "run-input-request", 89);
        let effect = settled.segments.iter().find(|s| s.role == SegmentRole::Effect).expect("no text with the effect role");
        assert_eq!(effect.text, "SettleIntake");
        assert_eq!(effect.effect.as_deref(), Some("SettleIntake"));
        assert!(effect.definition.is_some(), "no destination to jump to defeffect");
        let outcome = line_at(&read, "run-input-request", 85);
        assert!(outcome.segments.iter().any(|s| s.role == SegmentRole::Call && s.text == "outcome-of"));
        // A binding line has a binding number, and the type text equals the type in bindings
        let index = outcome.binding.expect("no binding number");
        assert_eq!(read.bindings[index].name, "outcome");
        let row = line_at(&read, "judged", 51);
        assert!(row.segments.iter().any(|s| s.role == SegmentRole::UnknownType && s.text == "?"));
    }

    /// V4(U3): all body lines of the sample's judged and run-input-request equal the body lines of `artifacts/v5/entity.html` (including the indentation of when / match
    /// and the alignment of `→`). The space before `→` is "the arm pattern's maximum width + 1" — the same as the sample's judged. Only the sample's run-input-request
    /// has one extra space (the alignment differs between the two samples because they are hand-written — recorded in the comment of #910).
    #[test]
    fn sample_bodies_match_the_reading_plane_rows_with_control_forms() {
        let (_, read) = sample();
        let judged: Vec<(u32, String)> = [
            (50, "val str | None target ⇐ target-of(request)"),
            (51, "var ? row = None"),
            (52, "when target is not None"),
            (53, "  val InputRow | InputAbsent | InputUnreadable answer ⇐ ReadInput(target)"),
            (54, "  match answer"),
            (55, "    InputUnreadable → return None"),
            (56, "    InputRow        → row := answer"),
            (57, "    InputAbsent     → None"),
            (58, "val Judgment judgment ⇐ judge(request, row)"),
            (59, "judgment"),
        ]
        .iter()
        .map(|(l, t)| (*l, t.to_string()))
        .collect();
        assert_eq!(rendered(&read, "judged"), judged);
        let run: Vec<(u32, String)> = [
            (85, "val InputDone | InputRejected | None outcome ⇐ outcome-of(request)"),
            (86, "when outcome is None"),
            (87, "  return RunOutcome.DEFERRED"),
            (88, "val IntakeSettlement settlement ⇐ settlement-of(request.request-id, ROUTE-INPUT, outcome)"),
            (89, "val IntakeSettleLanded | IntakeSettleRefused | IntakeUnreachable settled ⇐ SettleIntake(settlement)"),
            (90, "match settled"),
            (91, "  IntakeSettleLanded → match outcome"),
            (92, "                         InputDone     → RunOutcome.DONE"),
            (93, "                         InputRejected → RunOutcome.REJECTED"),
            (94, "  _                  → RunOutcome.DEFERRED"),
        ]
        .iter()
        .map(|(l, t)| (*l, t.to_string()))
        .collect();
        assert_eq!(rendered(&read, "run-input-request"), run);
        let outcome: Vec<(u32, String)> = [
            (66, "for _ in range(ATTEMPTS)"),
            (67, "  val Judgment | None judgment ⇐ judged(request)"),
            (68, "  when judgment is None"),
            (69, "    return None"),
            (70, "  when judgment.write is None"),
            (71, "    return judgment.outcome"),
            (72, "  val WriteLanded | WriteConflict | WriteRefused | WriteUnreachable answer ⇐ WriteInput(judgment.write)"),
            (73, "  match answer"),
            (74, "    WriteLanded      → return judgment.outcome"),
            (75, "    WriteRefused     → return InputRejected(reason=REJECT-WRITER-REFUSED, detail=answer.detail)"),
            (76, "    WriteUnreachable → return None"),
            (77, "    WriteConflict    → None"),
            (78, "None"),
        ]
        .iter()
        .map(|(l, t)| (*l, t.to_string()))
        .collect();
        assert_eq!(rendered(&read, "outcome-of"), outcome);
        // The contents of a match that starts inside an arm are held as the level (arm level + 1) and spaces aligned to the match word's column
        let inner = line_at(&read, "run-input-request", 92);
        assert_eq!((inner.depth, inner.pad), (2, 21));
        // The line of an arm's rewriting `(:= row answer)` has a binding number
        let assign = line_at(&read, "judged", 56);
        assert_eq!(assign.binding.map(|i| read.bindings[i].name.as_str()), Some("row"));
        // A match pattern's class name has the call role and can jump to the repo's type
        let arm = line_at(&read, "judged", 55);
        let class = arm.segments.iter().find(|s| s.text == "InputUnreadable").unwrap();
        assert_eq!(class.role, SegmentRole::Call);
        assert!(class.definition.is_some());
    }

    const INTENT: &str = r#"
(defrecord Row "row" (#^ str id))
(defrecord Missing "missing" (#^ str id))
(defeffect ReadRow "reads a row" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "emits" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
"#;

    /// Read one defk's body and return the spelling of its lines.
    fn body(lines: &str) -> (Read, Vec<String>) {
        let core = format!(
            r#"(require doeff-hy.macros [defk <- val var])
(import demo.intent [Row Missing ReadRow Emit])
(import helpers [shape-of])
(defk some-func [n] {{:pre [(: n int)] :post [(: % int)]}} "doc" n)
(defk subject [a o d items]
  {{:pre [(: a int)] :post [(: % int)] :tags {{:context "d" :role "program"}}}}
  "doc"
{})
"#,
            lines
        );
        let read = read(&[("demo/intent.hy", INTENT), ("demo/core.hy", &core)], "demo/core.hy");
        let texts = read.bodies.iter().find(|b| b.name == "subject").unwrap().lines.iter().map(render).collect();
        (read, texts)
    }

    /// V4: the lines of the body text table (v2 2.2, v3 2) — binding, call, effect firing, and return / resume.
    #[test]
    fn binding_call_effect_and_keyword_rows_follow_the_table() {
        let cases = [
            ("  (<- row Row (ReadRow \"id\"))", "val Row row ⇐ ReadRow(\"id\")"),
            ("  (<- r (ReadRow \"id\"))", "val Row r ⇐ ReadRow(\"id\")"),
            ("  (val x (shape-of a 1))", "val ? x = shape-of(a, 1)"),
            ("  (val n 1)", "val int n = 1"),
            ("  (val r ! (ReadRow \"id\"))", "val Row r ⇐ ReadRow(\"id\")"),
            ("  (val s (! (ReadRow \"id\")))", "val Row s ⇐ ReadRow(\"id\")"),
            ("  (val k (! (some-func 0)))", "val int k ⇐ some-func(0)"),
            ("  (var c 0)", "var int c = 0"),
            ("  (var c 0)\n  (:= c (+ c 1))", "c := c + 1"),
            ("  (lazy val lz (shape-of a))", "lazy val ? lz = shape-of(a)"),
            ("  (lazy var lv 2)", "lazy var int lv = 2"),
            ("  (session val sv \"s\")", "session val str sv = \"s\""),
            ("  (session var sw 4)", "session var int sw = 4"),
            ("  (setv t 5)", "setv t = 5"),
            ("  (<- (Emit \"hi\"))", "Emit(\"hi\")"),
            ("  (! (Emit \"hi\"))", "Emit(\"hi\")"),
            ("  (Emit \"x\")", "Emit(\"x\")"),
            ("  (! (some-func 0))", "!some-func(0)"),
            ("  (<- (some-func 0))", "!some-func(0)"),
            ("  (.m o a)", "o.m(a)"),
            ("  (get d \"k\")", "d[\"k\"]"),
            ("  (. o a)", "o.a"),
            ("  (shape-of a :k 1)", "shape-of(a, k=1)"),
            ("  (+ (len items) a)", "len(items) + a"),
            ("  (return a)", "return a"),
            ("  (resume (shape-of a))", "resume shape-of(a)"),
            ("  (return)", "return"),
            ("  a", "a"),
        ];
        for (source, expected) in cases {
            let (_, texts) = body(source);
            assert_eq!(texts.last().map(String::as_str), Some(expected), "{:?}", source);
        }
        // A setv in a body has a warning mark. val / var lines do not
        let (read, _) = body("  (setv t 5)\n  (val u 1)");
        let lines = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines;
        assert_eq!(lines[0].warning.as_ref().map(|w| w.kind), Some(BodyWarningKind::Setv));
        assert!(lines[0].warning.as_ref().unwrap().message.contains("(val t …)"));
        assert_eq!(lines[1].warning, None);
        // A declaration with two pairs gets a line per pair
        let (_, texts) = body("  (val p 1\n       q \"s\")");
        assert_eq!(texts, vec!["val int p = 1", "val str q = \"s\""]);
        // The head of an effect firing has the effect role, and `!` is not emitted
        let (read, _) = body("  (! (Emit \"hi\"))");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments[0].role, SegmentRole::Effect);
        assert_eq!(line.segments[0].effect.as_deref(), Some("Emit"));
        // Bindings of lazy / session appear in bindings with the preceding word, and have types
        let (read, _) = body("  (lazy var lv 2)");
        let binding = read.bindings.iter().find(|b| b.name == "lv").expect("no binding for lazy var");
        assert_eq!(binding.modifier, Some(BindingModifier::Lazy));
        assert_eq!(binding.form, super::super::signatures::BindingForm::Var);
    }

    /// V4(U3): the table of control shapes (v2 2.2) — when / if … else … / match (`:if`, class keywords, `|`, `[…]`, blocks inside arms) /
    /// for (name pairs) / lfor / gfor / sfor (`:if`, stacked clauses).
    #[test]
    fn control_forms_follow_the_table() {
        let cases: [(&str, &[&str]); 10] = [
            ("  (when (> a 1)\n    (shape-of a)\n    (return a))", &["when a > 1", "  shape-of(a)", "  return a"]),
            ("  (if (is a None)\n    (return 0)\n    (return a))", &["if a is None", "  return 0", "else", "  return a"]),
            ("  (if a (shape-of a))", &["if a", "  shape-of(a)"]),
            (
                "  (match o\n    (Row :id \"x\") 1\n    (| (Missing) (Row)) 2\n    [x y] :if (> x 0) 3\n    _ (when a (shape-of a)))",
                &[
                    "match o",
                    "  Row(id=\"x\")     → 1",
                    "  Missing | Row   → 2",
                    "  [x, y] if x > 0 → 3",
                    "  _               → when a",
                    "                      shape-of(a)",
                ],
            ),
            ("  (for [x items]\n    (shape-of x))", &["for x in items", "  shape-of(x)"]),
            ("  (for [[k v] (.items d)]\n    (shape-of k v))", &["for k, v in d.items()", "  shape-of(k, v)"]),
            ("  (val xs (lfor x items (len x)))", &["val ? xs = [len(x) for x in items]"]),
            ("  (val ys (gfor x items :if (> x 0) x))", &["val ? ys = (x for x in items if x > 0)"]),
            ("  (val zs (sfor x items y d (+ x y)))", &["val ? zs = {x + y for x in items for y in d}"]),
            ("  (val ws (lfor x items\n                (shape-of x)))", &["val ? ws = [shape-of(x) for x in items]"]),
        ];
        for (source, expected) in cases {
            let (_, texts) = body(source);
            assert_eq!(texts, expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{:?}", source);
        }
        // Control shapes and clauses not in the table stay as lisp: a match with missing arms, a comprehension with :setv, a for whose name is not a pair
        let (_, texts) = body("  (match o (Row) 1 _)");
        assert_eq!(texts, vec!["(match o (Row) 1 _)"]);
        let (read, texts) = body("  (val vs (lfor x items :setv y (len x) y))");
        assert_eq!(texts, vec!["val ? vs = (lfor x items :setv y (len x) y)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.last().map(|s| s.role), Some(SegmentRole::Lisp));
        let (_, texts) = body("  (for [x items y d]\n    (shape-of x))");
        assert_eq!(texts, vec!["(for [x items y d]", "  (shape-of x))"]);
    }

    /// U20a(#1206): block shapes — do, cond, try / except / else / finally, while, continue / break / raise, comments, and rewriting contents.
    #[test]
    fn block_forms_follow_the_table() {
        let cases: [(&str, &[&str]); 11] = [
            (
                "  (when a\n    (do (shape-of a)\n        (return a)))",
                &["when a", "  shape-of(a)", "  return a"],
            ),
            (
                "  (match o\n    (Row) (do (shape-of o)\n              (return 1))\n    _ 2)",
                &["match o", "  Row → shape-of(o)", "        return 1", "  _   → 2"],
            ),
            (
                "  (cond\n    (= a 1) (return 1)\n    (is o None) 2\n    True 3)",
                &["cond", "  a == 1    → return 1", "  o is None → 2", "  else      → 3"],
            ),
            (
                "  (try\n    (shape-of a)\n    (except [e ValueError]\n      (raise e))\n    (except [[KeyError IndexError]]\n      (return None))\n    (finally\n      (shape-of o)))",
                &[
                    "try",
                    "  shape-of(a)",
                    "except ValueError as e",
                    "  raise e",
                    "except (KeyError, IndexError)",
                    "  return None",
                    "finally",
                    "  shape-of(o)",
                ],
            ),
            ("  (try\n    (shape-of a)\n    (except []\n      None)\n    (else\n      1))", &["try", "  shape-of(a)", "except", "  None", "else", "  1"]),
            ("  (while (is o None)\n    (:= o (shape-of a)))", &["while o is None", "  o := shape-of(a)"]),
            ("  (for [x items]\n    (when (is x None)\n      (continue))\n    (break))", &["for x in items", "  when x is None", "    continue", "  break"]),
            ("  (raise (ValueError \"x\") :from o)", &["raise ValueError(\"x\") from o"]),
            ("  (raise)", &["raise"]),
            ("  (setv (get d \"k\") 1\n        o.a 2)", &["d[\"k\"] = 1", "o.a = 2"]),
            ("  (shape-of a)\n  ;; comment line 1\n  ;;; comment line 2\n  (return a)", &["shape-of(a)", "# comment line 1", "# comment line 2", "return a"]),
        ];
        for (source, expected) in cases {
            let (_, texts) = body(source);
            assert_eq!(texts, expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{:?}", source);
        }
        // Rewriting contents is not a binding — no setv warning is attached
        let (read, _) = body("  (setv (get d \"k\") 1)");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.warning, None);
        // A comment has the comment role and has a source range
        let (read, _) = body("  (shape-of a)\n  ;; heading\n  (return a)");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[1];
        assert_eq!(line.segments[0].role, SegmentRole::Comment);
        assert!(line.segments[0].range.is_some());
        // A multi-line condition is also flattened onto one line and aligned
        let (_, texts) = body("  (cond\n    (and a\n         o) 1\n    True 2)");
        assert_eq!(texts, vec!["cond", "  a and o → 1", "  else    → 2"]);
        // A condition with a comment in the middle of a line cannot be flattened — the whole cond stays as lisp
        let (_, texts) = body("  (cond\n    (and a ;; comment\n         o) 1\n    True 2)");
        assert_eq!(texts[0], "(cond");
        let (_, texts) = body("  (del (get d \"k\") o.a)\n  (assert (is o None) \"m\")");
        assert_eq!(texts, vec!["del d[\"k\"], o.a", "assert o is None, \"m\""]);
    }

    /// U20b(#1207): expression shapes — if (ternary), fn (⇒), cut, comprehensions (including dfor), literals, and blocks in value position within expressions.
    #[test]
    fn expression_forms_follow_the_table() {
        let cases: [(&str, &[&str]); 14] = [
            ("  (val x (if (is o None) 0 (len items)))", &["val ? x = 0 if o is None else len(items)"]),
            ("  (shape-of (if a 1 2) :k (if a 3 4))", &["shape-of(1 if a else 2, k=3 if a else 4)"]),
            ("  (+ (if a 1 2) 3)", &["(1 if a else 2) + 3"]),
            ("  (.sort items :key (fn [p] (get p 0)))", &["items.sort(key=p ⇒ p[0])"]),
            ("  (.get (if a d o) \"k\")", &["(d if a else o).get(\"k\")"]),
            ("  (val f (fn [a b] (+ a b)))", &["val ? f = (a, b) ⇒ a + b"]),
            ("  (val g (fn [] 1))", &["val ? g = () ⇒ 1"]),
            ("  (val s (cut items 1 None))", &["val ? s = items[1:]"]),
            ("  (val s (cut items 2))", &["val ? s = items[:2]"]),
            ("  (val s (cut items 0 (len a) 2))", &["val ? s = items[0:len(a):2]"]),
            ("  (val m (dfor x items x.key (len x)))", &["val ? m = {x.key: len(x) for x in items}"]),
            ("  (val t #(a (- a) [1 2]))", &["val ? t = (a, -a, [1, 2])"]),
            ("  (val one #(a))", &["val ? one = (a,)"]),
            ("  (val d {\"k\" (len items) \"n\" 1})", &["val dict d = {\"k\": len(items), \"n\": 1}"]),
        ];
        for (source, expected) in cases {
            let (_, texts) = body(source);
            assert_eq!(texts, expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{:?}", source);
        }
        // An if inside a multi-line expression is also flattened onto one line
        let (_, texts) = body("  (val x (if (is o None)\n            0\n            (len items)))");
        assert_eq!(texts, vec!["val ? x = 0 if o is None else len(items)"]);
        // A cond / match / long if in value position opens vertically (the first line continues the binding line, and the contents align to the block word's column)
        let (_, texts) = body("  (val r (cond (is o None) 0\n               True 1))");
        assert_eq!(texts, vec!["val ? r = cond", "            o is None → 0", "            else      → 1"]);
        let long = "(shape-of a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a)";
        let (_, texts) = body(&format!("  (val r (if a\n             {long}\n             {long}))"));
        assert_eq!(texts[0], "val ? r = if a");
        assert_eq!(texts[2].trim_start(), "else");
        // A short if after an arm's → is a ternary
        let (_, texts) = body("  (match o\n    (Row) (if a 1 2)\n    _ 3)");
        assert_eq!(texts, vec!["match o", "  Row → 1 if a else 2", "  _   → 3"]);
        // A dict whose keys are keywords is not drawn (kept as the original text — no lisp marker is attached either)
        let (read, texts) = body("  (val d {:k 1})");
        assert_eq!(texts, vec!["val dict d = {:k 1}"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert!(line.segments.iter().all(|s| s.role != SegmentRole::Lisp));
        // A comprehension with a clause not in the table (:setv) and an fn with #* in its arguments stay as islands of lisp
        let (_, texts) = body("  (val v (lfor x items :setv y (len x) y))");
        assert_eq!(texts, vec!["val ? v = (lfor x items :setv y (len x) y)"]);
        let (_, texts) = body("  (val h (fn [#* xs] xs))");
        assert_eq!(texts, vec!["val ? h = (fn [#* xs] xs)"]);
    }

    /// V5: a form not in the table is not drawn by guessing; it is emitted as the original lisp with a marker (role lisp).
    #[test]
    fn forms_outside_the_table_stay_marked_lisp() {
        let (read, texts) = body("  (my-macro a b)");
        assert_eq!(texts, vec!["(my-macro a b)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.len(), 1);
        assert_eq!(line.segments[0].role, SegmentRole::Lisp);
        // An unknown form inside a call's arguments also gets the lisp marker, for just its parens
        let (read, texts) = body("  (shape-of (my-macro a) 1)");
        assert_eq!(texts, vec!["shape-of((my-macro a), 1)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        let lisp: Vec<&str> = line.segments.iter().filter(|s| s.role == SegmentRole::Lisp).map(|s| s.text.as_str()).collect();
        assert_eq!(lisp, vec!["(my-macro a)"]);
        // Even if a binding's value is an unknown form, the binding line is drawn, and only the value is lisp
        let (_, texts) = body("  (val z (check a))");
        assert_eq!(texts, vec!["val ? z = (check a)"]);
        // `:absent F` is not in the table — append it as the original lisp
        let (read, texts) = body("  (<- r Row (ReadRow \"id\") :absent Missing)");
        assert_eq!(texts, vec!["val Row r ⇐ ReadRow(\"id\") :absent Missing"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.last().map(|s| (s.role, s.text.as_str())), Some((SegmentRole::Lisp, ":absent Missing")));
        // A setv that destructures into a tuple is a form not in the table — the whole is lisp
        let (_, texts) = body("  (setv #(p q) items)");
        assert_eq!(texts, vec!["(setv #(p q) items)"]);
        // An unknown multi-line form is split into lines, keeping the difference between the source line and the indentation
        let (read, texts) = body("  (unless a\n    (shape-of a))");
        assert_eq!(texts, vec!["(unless a", "  (shape-of a))"]);
        let lines = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines;
        assert_eq!(lines[1].line, lines[0].line + 1);
        assert!(lines.iter().all(|l| l.segments.iter().all(|s| matches!(s.role, SegmentRole::Lisp | SegmentRole::Text))));
    }

    /// V6: each body line has a source line; the text ranges that come from the source lie on that line, and the call, name, effect, lisp, and expression text is
    /// character-for-character the same as the text of the source range. Binding words and types point to the original text's range.
    #[test]
    fn lines_and_ranges_point_back_to_the_source() {
        let (source, read) = sample();
        let slice = |r: &Range| source.get(offset_of(&source, r.start)..offset_of(&source, r.end)).unwrap().to_string();
        let mut checked = 0;
        for body in &read.bodies {
            for line in &body.lines {
                for segment in &line.segments {
                    let Some(range) = &segment.range else { continue };
                        assert_eq!(range.start.line, line.line, "{}: the range of {:?} is outside line {}", body.name, segment.text, line.line + 1);
                    match segment.role {
                        SegmentRole::Name | SegmentRole::Call | SegmentRole::Effect | SegmentRole::Lisp | SegmentRole::Text => {
                            assert_eq!(slice(range), segment.text, "{} line {}", body.name, line.line + 1);
                        }
                        SegmentRole::Keyword => {
                            // `<-` is shown as `val`. Other words keep the source's spelling
                            let spelled = slice(range);
                            assert!(spelled == "<-" || spelled == segment.text, "{} line {}: {:?}", body.name, line.line + 1, spelled);
                            assert!([
                                "<-", "val", "var", "lazy", "session", "setv", "return", "resume", "when", "if", "match", "for",
                                "while", "cond", "try", "except", "finally", "else", "raise", "continue", "break", "del", "assert",
                            ]
                            .contains(&spelled.as_str()));
                        }
                        SegmentRole::Comment => {
                            let spelled = slice(range);
                            assert!(spelled.starts_with(';'), "{} line {}: {:?}", body.name, line.line + 1, spelled);
                            assert_eq!(segment.text, format!("# {}", spelled.trim_start_matches(';').trim_start()));
                        }
                        SegmentRole::Type => assert!(slice(range).starts_with('(') || !slice(range).is_empty()),
                        SegmentRole::Assign => assert_eq!(slice(range), ":="),
                        SegmentRole::UnknownType | SegmentRole::Bind => panic!("{:?} has no source range", segment.role),
                    }
                    checked += 1;
                }
            }
        }
        assert!(checked > 20, "too few texts were compared: {}", checked);
        // The line numbers of the sample: judged's body starts at line 50, and run-input-request at line 85
        let first = |name: &str| read.bodies.iter().find(|b| b.name == name).unwrap().lines[0].line + 1;
        assert_eq!(first("judged"), 50);
        assert_eq!(first("run-input-request"), 85);
        // The type text's range is the T (annotation) of `(<- x T e)`
        let line = line_at(&read, "judged", 50);
        let ty = line.segments.iter().find(|s| s.role == SegmentRole::Type).unwrap();
        assert_eq!(slice(ty.range.as_ref().unwrap()), "(| str None)");
        // The range of the bound name that the binding number points to = the name text's range
        for body in &read.bodies {
            for line in body.lines.iter().filter(|l| l.binding.is_some()) {
                let binding = &read.bindings[line.binding.unwrap()];
                let name = line.segments.iter().find(|s| s.role == SegmentRole::Name).unwrap();
                assert_eq!(name.range.as_ref(), Some(&binding.range));
            }
        }
    }
}

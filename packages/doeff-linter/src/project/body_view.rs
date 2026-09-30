//! 定義の本体の文字 — editor-json の `bodies`(読む面の本体の行・agora-redesign #910 U2)。
//!
//! doeff-runner の読む面(webview)は定義などの実体を HTML のカードで見せ、**文字で出すのは本体だけ**。本体の文字の形は operator が
//! 承認済み("yeah val var when match is perfect.")で、表の正本は `docs/design/hy-reading-plane/artifacts/v2/design.md` 2.2 節と
//! `v3/design.md` 2 節(意味の正本 ADR-DOE-HY-006)。Hy の form の読み方はこの linter の 1 か所に置き(#849 の決定「読み方の写しを
//! 持たない」)、面は行と字の範囲を描くだけにする。
//!
//! | 元の form | 本体の文字 |
//! |---|---|
//! | `(<- x T e)` | `val T x ⇐ e` |
//! | `(val x e)` / `(var x e)` | `val T x = e` / `var T x = e`(型が分からなければ `?`。別の型で埋めない) |
//! | `(val x ! e)` / `(val x (! e))` | `val T x ⇐ e`(撃つ値 — `(<- x e)` と同じ意味) |
//! | `(lazy val x e)` / `(session var x e)` … | `lazy val T x = e` / `session var T x = e` |
//! | `(:= x v)` | `x := v` |
//! | 本体の `(setv x e)` | `setv x = e` + 警告の印(val / var へ) |
//! | `(E a)` / `(! (E a))` / `(<- (E a))` | `E(a)`(E の字が effect の役 — 絵は面が描く) |
//! | `(! (f a))` / `(<- (f a))`(effect でない) | `!f(a)` |
//! | `(f a b)` ほかの呼び | `f(a, b)` …(`call_view.rs` の置き換えと同じ読み) |
//! | `(return v)` / `(resume v)` | `return v` / `resume v` |
//! | `(when c …)` / `(if c a b)` | `when c` + 字下げ / `if c` + a、`else` + b |
//! | `(match v (A) x _ y)` | `match v` + `A → x` / `_ → y`(`→` を縦に揃える) |
//! | `(for [x xs] …)` | `for x in xs` + 字下げ |
//! | `(lfor x xs e)` / `gfor` / `sfor` | `[e for x in xs]` / `(e for …)` / `{e for …}` |
//! | 表に無い form | 元の lisp のまま(字の役 `lisp` の目印つき)— 推測で描かない(v1 制約 2) |
//!
//! 行ごとに source の行(`line` = 0 始まり — 面は 1 を足して見せる)、字の範囲ごとに source の範囲と役を持つ。呼びの読み
//! (何を呼びと読むか・括弧の要否)は `call_view.rs` の読み手を式 1 つずつ呼んで使い、ここに写しを置かない。

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

// --- 契約の形 ------------------------------------------------------------------------------

/// 字の役(契約の閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum SegmentRole {
    /// 束縛の語(`val`・`var`・`lazy`・`session`・`setv`)と `return`・`resume`
    Keyword,
    /// 束縛の型(`binding` の束縛の型を綴った物)
    Type,
    /// 型が分からない印 `?`
    UnknownType,
    /// 束ねる名
    Name,
    /// effect を通す矢印 `⇐`
    Bind,
    /// 値の `=` と書き換えの `:=`
    Assign,
    /// effect の値を作る呼びの頭(`E(a)` の E — 面が絵を添える)
    Effect,
    /// 呼びの頭(defk・deff・関数・組み込み・局所の名・型の名)
    Call,
    /// それ以外の式の字(引数・演算子・字面・区切り)
    Text,
    /// 表に無い form — 元の lisp のまま(目印つきで描く)
    Lisp,
    /// 本体の途中の行全体の註(`;; …` → `# …`)
    Comment,
}

/// 字の範囲 1 つ。行の字は segments の text をつないだ物。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodySegment {
    pub text: String,
    pub role: SegmentRole,
    /// 元の source の範囲(区切りや `⇐` のように source に無い字は null)。`keyword` と `type` は text と綴りが違いうる
    /// (`<-` → `val`・`(| A B)` → `A | B`)。
    pub range: Option<Range>,
    /// `effect` の役の時の effect の名(面が絵を選ぶ)。
    pub effect: Option<String>,
    /// 押して飛ぶ先(呼びの頭と型の名の repo の中の定義。無ければ null)。
    pub definition: Option<Location>,
}

/// 行の警告の種類(契約の閉じた集合)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BodyWarningKind {
    /// 本体の `setv` — 書き換えられるかどうかが読めない(val / var へ・ADR-DOE-HY-006)
    Setv,
}

/// 行の警告(面が警告の印を描く)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodyWarning {
    pub kind: BodyWarningKind,
    pub message: String,
}

/// 本体の行 1 つ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BodyLine {
    /// source の行(0 始まり)。1 つの source の行に文が 2 つあれば同じ行の番号が続く。
    pub line: u32,
    /// 字下げの段(本体の一番外 = 0・when / if / match / for の中身で 1 つ深くなる)。
    pub depth: u32,
    /// 段の字下げの後ろに足す空白の数(面は `"  " × depth + " " × pad` の後ろに字を並べる)。複数行の式の続きの行
    /// (source の字下げの文の頭からの差)と、`A → match v` のように腕の中で始まる塊の中身(塊の語の列に揃える)で 0 でない。
    pub pad: u32,
    pub segments: Vec<BodySegment>,
    /// この行が描く束縛の番号(同じ出力の `bindings` の中の位置)。
    pub binding: Option<usize>,
    pub warning: Option<BodyWarning>,
}

/// 定義 1 つの本体。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Body {
    pub kind: SignatureKind,
    pub name: String,
    pub path: String,
    /// 名の範囲(`signatures` の同じ定義と同じ)。
    pub range: Range,
    pub full_range: Range,
    pub lines: Vec<BodyLine>,
}

// --- 読み ---------------------------------------------------------------------------------

/// 1 file の定義ごとの本体の行(defk / deff — 見出しと同じ定義)。
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
        // 本体の頭の文字列は説明(後ろに文がある時だけ — 文字列 1 つだけの本体はその文字列が答え)
        let skip = usize::from(matches!(shape.body.as_slice(), [first, _, ..] if matches!(first.node, Node::Str { .. })));
        // 本体の文を定義の form の並びの中で描く(文と文の間の註を拾うため)
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

/// 式の中で source を上書きする物(置き換えの edit か、置き換えなかった括弧 = lisp の島)。
enum Overlay<'t> {
    Edit(&'t str),
    Island,
    /// 内包(`lfor` …)を 1 行に描いた字。
    Shown(Vec<BodySegment>),
}

/// 行の字下げ(段と、段の後ろの空白)。
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
    /// いま描いている文の字下げ(複数行の式の続きの行の基準)。
    indent: Indent,
    /// 式を 1 行に平らにして描いている時(改行と字下げを空白 1 つにする)。
    flat: bool,
    /// 平らにできない物(行の途中の註)に当たった。
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

    /// 新しい行を始める(source の行は `offset` の行)。
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

    /// 今の行の、次の字が置かれる列(面の字の数え方 — 段 1 つ = 空白 2 つ)。
    fn cursor(&mut self) -> u32 {
        let line = self.current();
        let width: usize = line.segments.iter().map(|s| s.text.chars().count()).sum();
        line.depth * 2 + line.pad + width as u32
    }

    /// 今の行の `column` の列で始まる塊の語(when・match …)に揃える字下げ。中身はこの 1 つ深い段。
    fn aligned(&mut self, column: u32) -> Indent {
        let depth = self.current().depth;
        Indent { depth, pad: column.saturating_sub(depth * 2) }
    }

    /// `f` が描く字を、今の行の外の仮の行に描いて取り出す(1 行に収まらなければ None — 腕の揃えや内包の形を先に測るため)。
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

    /// `f` の描く式を 1 行に平らにして取り出す(平らにできなければ None)。
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

    /// 取り出した字を今の行に足す。
    fn extend(&mut self, segments: Vec<BodySegment>) {
        for segment in segments {
            self.push(segment);
        }
    }

    fn current(&mut self) -> &mut BodyLine {
        self.lines.last_mut().expect("行を始める前に字を足した")
    }

    /// 字を足す(続く `text` の字は 1 つにまとめる — source に無い字どうし・source の続いた範囲どうし)。
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

    /// source に無い字。
    fn text(&mut self, text: &str, role: SegmentRole) {
        self.push(BodySegment { text: text.to_string(), role, range: None, effect: None, definition: None });
    }

    /// form の字をそのまま、役を付けて足す。
    fn word(&mut self, form: &Form, role: SegmentRole) {
        let text = self.reader.hy.text(form).to_string();
        self.shown(form, &text, role);
    }

    /// form の範囲に、`text` を見せる字を足す(`<-` → `val` のように綴りが変わる字)。
    fn shown(&mut self, form: &Form, text: &str, role: SegmentRole) {
        let range = Some(self.form_range(form));
        self.push(BodySegment { text: text.to_string(), role, range, effect: None, definition: None });
    }

    /// 表に無い form を元の lisp のまま足す(複数行なら行を分ける)。
    fn lisp(&mut self, start: usize, end: usize, base: u32) {
        self.source(start, end, base, SegmentRole::Lisp);
    }

    /// source の `start..end` を役 `role` で足す。改行の後は新しい行にし、字下げは文の頭の列 `base` からの差を空白で持つ。
    fn source(&mut self, start: usize, end: usize, base: u32, role: SegmentRole) {
        let src = self.reader.hy.src;
        // 平らにする時、字の中に註(文字列の外の `;`)があれば平らにできない(註が後ろの字を飲む)
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
                // 平らにする: 改行と字下げを空白 1 つに(開き括弧の直後と閉じ括弧の直前は詰める)
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

    /// 束縛の型の字(`bindings` の型 — 分からなければ `?`)。
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

    /// 名の form を束ねた束縛の番号。
    fn binding_of(&self, name: &Form) -> Option<usize> {
        let range = self.form_range(name);
        self.bindings.iter().position(|b| b.range == range)
    }

    // --- 文 ----------------------------------------------------------------------------------

    /// 本体の文 1 つ(`depth` = 字下げの段)。
    /// `inline` = 今の行の続きに描く(match の腕の `→` の後ろ)。
    fn statement(&mut self, form: &Form, indent: Indent, inline: bool) {
        // `(do a b …)` は `do` の字を出さず中身を並べる(文の場所なら今の段に、腕の `→` の後ろなら 1 つ目をその行に続け、
        // 残りをその列に揃える)
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

    /// 並んだ文(`siblings[from..]`)を `indent` の段に 1 文ずつ描く。文と文の間の行全体の `;;` の註は `# …` の行にする。
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

    /// source の `start..end` にある行全体の註(`;` で始まる行)を `# …` の行にする(最初の行 = 前の form の行の残りは見ない)。
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

    /// `(do a b …)` の中身。
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
            // 腕の `→` の後ろ(今の行の続き)の if は、1 行に収まれば 3 項
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
            // 呼び・知らない頭・頭の無い括弧は式として読む(置き換えなかった括弧は lisp の島)
            Some(_) | None => {
                self.expression(form, base);
                true
            }
        };
        if !drawn {
            // 表の形に読めなかった(名が記号でない・値が欠けた …)— 推測で描かず lisp のまま。描きかけの字と行は捨てる
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

    /// 塊の中身の文(`siblings[from..]` を 1 つ深い段に 1 文ずつ・間の註も)。`column` = 塊の語の列。
    fn block(&mut self, column: u32, siblings: &[&Form], from: usize) {
        let inner = self.aligned(column);
        let inner = Indent { depth: inner.depth + 1, pad: inner.pad };
        self.statements(siblings, from, inner);
    }

    /// `(when c …)` → `when c` + 字下げの中身。
    fn when(&mut self, items: &[&Form], base: u32) -> bool {
        let [head, condition, _body @ ..] = items else { return false };
        let column = self.cursor();
        self.word(head, SegmentRole::Keyword);
        self.text(" ", SegmentRole::Text);
        self.expression(condition, base);
        self.block(column, items, 2);
        true
    }

    /// `(continue)` / `(break)`。
    fn bare_keyword(&mut self, items: &[&Form]) -> bool {
        let [head] = items else { return false };
        self.word(head, SegmentRole::Keyword);
        true
    }

    /// `(del x …)` → `del x, …`。
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

    /// `(assert c)` / `(assert c m)` → `assert c` / `assert c, m`。
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

    /// `(raise)` / `(raise e)` / `(raise e :from c)` → `raise` / `raise e` / `raise e from c`。
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

    /// `(cond c x c y … True z)` → `cond` + 腕ごとに `c → x`(条件の幅を揃えて `→` を縦に並べる・最後の `True` は `else`)。
    fn cond_arms(&mut self, items: &[&Form]) -> bool {
        let [head, rest @ ..] = items else { return false };
        if rest.is_empty() || !rest.len().is_multiple_of(2) {
            return false;
        }
        let arms: Vec<(&Form, &Form)> = rest.chunks(2).map(|pair| (pair[0], pair[1])).collect();
        let last = arms.len() - 1;
        // 条件を先に 1 行ずつ測る — 1 行に収まらない条件があれば cond 全体を lisp のまま
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

    /// `(try … (except [e T] …) (else …) (finally …))` → `try` + 中身、`except T as e` + 中身 …(節の語は try の列に揃える)。
    fn try_block(&mut self, items: &[&Form]) -> bool {
        let [head, rest @ ..] = items else { return false };
        // 節の始まり(except / else / finally の括弧)を探す。それより前が try の中身
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
        // except の頭 `[e T]` / `[T]` / `[]` / `[e [A B]]` を先に測る
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

    /// `(if c a b)` → `if c` + a、`else` + b(else は if の列に揃え、行の番号は b の行)。
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

    /// `(match v p x p :if g y …)` → `match v` + 腕ごとに `p → x`(腕の pattern の幅を揃えて `→` を縦に並べる)。
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
        // 腕の pattern(と :if の条件)を先に 1 行ずつ測る — 描けない pattern が 1 つでもあれば match 全体を lisp のまま
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

    /// match の pattern 1 つ(`(Cls)` → `Cls`・`(Cls :k p)` → `Cls(k=p)`・`(| p q)` → `p | q`・`[p q]` → `[p, q]`・名と字面は
    /// そのまま)。表に無い形は false(match 全体を lisp のまま)。
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

    /// pattern を `sep` で並べる。
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

    /// class の pattern の引数(`a b :k p` → `a, b, k=p`)。
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

    /// `(for [x xs] …)` → `for x in xs` + 字下げの中身。
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

    /// 繰り返しの名(`x` → `x`・`[a b]` → `a, b`)。それ以外は false。
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

    /// 式の中で置き換えなかった括弧のうち、表にある形を 1 行で描く(描けなければ false — lisp の島のまま)。`wrap` = 優先順位の
    /// 低い形(3 項・lambda)を括弧で包むか(演算の項・method の的の中)。
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

    /// 束縛・return の値。`if` は 1 行に収まれば 3 項(`a if c else b`)、収まらなければ縦に開く。`cond`・`match`・`do`・`try` は
    /// 値の場所でも縦に開く(1 行目は今の行に続け、中身は塊の語の列に揃える)。それ以外は式。
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

    /// `f` の描く字を、`wrap` なら括弧で包む。
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

    /// `(if c a b)` → `a if c else b`。
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

    /// `(fn [x] e)` → `x ⇒ e`・`(fn [a b] e)` → `(a, b) ⇒ e`・`(fn [] e)` → `() ⇒ e`。引数は名だけ(既定値・`#*` は表に無い)・本体は式 1 つ。
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

    /// `(cut xs a b)` → `xs[a:b]`(`None` の端は空・`(cut xs)` → `xs[:]`・`(cut xs b)` → `xs[:b]`・`(cut xs a b s)` → `xs[a:b:s]`)。
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

    /// `(lfor x xs :if c e)` → `[e for x in xs if c]`(gfor は `( … )`・sfor は `{ … }`)。`:setv`・`:do` ほか表に無い節と、
    /// 1 行に収まらない物は false(lisp の島のまま)。
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
        // dfor は最後の 2 つが鍵と値(`{k: v for …}`)
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

    /// `(<- x T e)` → `val T x ⇐ e`・名の無い `(<- e)` → 撃つ式。
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
            // `:absent F` は本体の文字の表に無い — lisp のまま添える
            let keyword = items[items.len() - 2];
            self.text(" ", SegmentRole::Text);
            self.lisp(keyword.span.start, raise.span.end, base);
        }
        true
    }

    /// `(val x e)`・`(var x e)`・`(lazy val x e)`・`(session var x e)`(組が複数なら組ごとに行)。
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
            let value = pair.value.expect("値の欠けた組は上で弾いた");
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

    /// 本体の `(setv x e)` → `setv x = e` + 警告の印。
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
                // 中身の書き換え `(setv (get x k) v)` / `(setv o.a v)` → `x[k] = v` / `o.a = v`(束縛ではない — setv の警告は付けない)
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
                    "setv の代わりに (val {shown} …)、書き換えるなら (var {shown} …) と (:= {shown} …) を使う [ADR-DOE-HY-006]"
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

    /// setv の書く先: 束ねる名なら Some(true)、中身の書き換え(`(get x k)`・`(. o a)`・`o.a`)なら Some(false)、それ以外は None。
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

    /// `(:= x v)` → `x := v`。
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

    /// `(return v)` / `(resume v)` → `return v` / `resume v`。
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

    /// `(! e)` の e(それ以外は None)。
    fn bang_inner<'f>(&self, value: &'f Form) -> Option<&'f Form> {
        let items = live(value)?;
        let [head, inner] = items.as_slice() else { return None };
        (self.reader.hy.symbol(head) == Some("!")).then_some(*inner)
    }

    /// 撃つ式(`(<- e)` の e・`(:= x ! e)` の e): effect なら `E(a)`(頭が effect の役)、そうでなければ `!` を前に置く。
    fn performed(&mut self, inner: &Form, base: u32) {
        if performed_effect(self.world, self.reader, self.names, inner).is_none() {
            self.text("!", SegmentRole::Text);
        }
        self.expression(inner, base);
    }

    // --- 式 ----------------------------------------------------------------------------------

    /// 式 1 つ(置き換えの読みで `f(a, b)` の形に。置き換えなかった括弧は、表にある形なら 1 行に描き、無ければ lisp の島。
    /// 組・列・集合・辞書の字面は `(a, b)`・`[a, b]`・`{a, b}`・`{k: v}` にする)。
    fn expression(&mut self, form: &Form, base: u32) {
        let rewrites = expression_rewrites(self.world, self.reader, self.names, form);
        let src = self.reader.hy.src;
        let reader = self.reader;
        let lines = &reader.lines;
        let offset = |r: &Range| (lines.offset(r.start), lines.offset(r.end));
        let rewritten: HashSet<(usize, usize)> = rewrites.iter().map(|r| offset(&r.range)).collect();
        let mut found = Vec::new();
        find_islands(form, &rewritten, &mut found);
        // 覆う物(島と字面): 描き方を先に決め、覆った範囲の中の置き換えの edit は捨てる
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

    /// 島を優先順位の低い形(3 項・lambda)で描く時に括弧で包むか — 囲む一番内側の置き換えが演算・`!`・属性の時と、method の
    /// 的(`(.m 島 …)` の島)の時。呼びの引数・添字・文の値の場所では包まない。
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
                // 的 = method の頭の直後の form(直前の字が `(.名` の 1 語)
                let before = self.reader.hy.src.get(..s).unwrap_or("").trim_end();
                before.rsplit(char::is_whitespace).next().is_some_and(|word| word.starts_with("(."))
            }
            Some(RewriteKind::Infix) | Some(RewriteKind::Prefix) | Some(RewriteKind::Perform) | Some(RewriteKind::Attribute) => {
                true
            }
        }
    }

    /// 組・列・集合・辞書の字面(`#(a b)` → `(a, b)`・1 つなら `(a,)`・`[a b]` → `[a, b]`・`#{a b}` → `{a, b}`・`{k v}` → `{k: v}`)。
    /// 辞書の鍵が keyword(`:k`)の物・要素の数が合わない辞書は描かない(元の字のまま)。
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

    /// 式の中の source の字(呼びの頭は役を付けて分ける)。
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

/// source の字(token の境目から始まる)に註があるか — 文字列(`"…"`・`\"` の escape を含む)の外の `;`。
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

/// 3 項の形(`a if c else b`)で 1 行に描く上限の字数。これを超える `if` は縦の if / else に開く。
const TERNARY_WIDTH: usize = 100;

/// 字の列の幅(面の字の数え方 — 字 1 つ = 1 列)。
fn segments_width(segments: &[BodySegment]) -> usize {
    segments.iter().map(|s| s.text.chars().count()).sum()
}

/// 呼びの頭 1 つ(byte の範囲)。
struct Head {
    start: usize,
    end: usize,
    role: PartRole,
    definition: Option<Location>,
}

/// 置き換えの部品(呼びの頭)を本文の順に。
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

/// 置き換えなかった括弧の form(lisp の島)を集める。島の中へは降りない(島は元の lisp のまま見せる)。quote は字面として残す。
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

/// 島の外にある一番外側の字面(組・列・集合・辞書)を集める(字面の中へは降りない — 中は字面を描く時に式として読む)。
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

/// 型の式を本体の文字の綴りにする(`A | B`・`H[a, b]`)。
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

/// module まで含めた名の最後の区切り。
fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}

#[cfg(test)]
mod tests {
    use super::super::signatures::{file_signatures, FileSignatures};
    use super::*;

    /// 根の file を並べて表を作り、1 file の見出しと本体を読む。
    fn read(files: &[(&str, &str)], target: &str) -> FileSignatures {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        file_signatures(&world, &root, target, source)
    }

    /// 見本の fixture(agora-controllers d89796e67 の controllers/messaging/core/conversation_input.hy の 1〜94 行を行の番号ごと
    /// そのまま写した物と、撃つ effect の定義だけの intent)を読む。
    fn sample() -> (String, FileSignatures) {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/body_view");
        let root = root.canonicalize().unwrap();
        let rel = "controllers/messaging/core/conversation_input.hy";
        let source = std::fs::read_to_string(root.join(rel)).unwrap();
        let world = World::build(&root, Some((rel, &source)));
        let read = file_signatures(&world, &root, rel, &source);
        (source, read)
    }

    /// 行を面と同じ字で綴る(段 1 つ = 空白 2 つ)。
    fn render(line: &BodyLine) -> String {
        let text: String = line.segments.iter().map(|s| s.text.as_str()).collect();
        format!("{}{}{}", "  ".repeat(line.depth as usize), " ".repeat(line.pad as usize), text)
    }

    /// 定義の本体の行を (1 始まりの source の行, 綴り) で。
    fn rendered(read: &FileSignatures, name: &str) -> Vec<(u32, String)> {
        let body = read.bodies.iter().find(|b| b.name == name).unwrap_or_else(|| panic!("{} の本体が無い", name));
        body.lines.iter().map(|l| (l.line + 1, render(l))).collect()
    }

    fn line_at<'b>(read: &'b FileSignatures, name: &str, line: u32) -> &'b BodyLine {
        let body = read.bodies.iter().find(|b| b.name == name).unwrap();
        body.lines.iter().find(|l| l.line + 1 == line).unwrap_or_else(|| panic!("{} の {} 行が無い", name, line))
    }

    /// V4: 見本の run-input-request・judged(`artifacts/v5/entity.html` の本体の行)の束縛・呼び・effect の撃ち・値の行が、
    /// 見本の本体の行と一字一句同じ(制御の形 when / match は U3)。
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
            assert!(judged.contains(&(line, text.to_string())), "judged {} 行: 期待 {:?}\n実際 {:#?}", line, text, judged);
        }
        let run = rendered(&read, "run-input-request");
        let expect_run = [
            (85, "val InputDone | InputRejected | None outcome ⇐ outcome-of(request)"),
            (88, "val IntakeSettlement settlement ⇐ settlement-of(request.request-id, ROUTE-INPUT, outcome)"),
            (89, "val IntakeSettleLanded | IntakeSettleRefused | IntakeUnreachable settled ⇐ SettleIntake(settlement)"),
        ];
        for (line, text) in expect_run {
            assert!(run.contains(&(line, text.to_string())), "run-input-request {} 行: 期待 {:?}\n実際 {:#?}", line, text, run);
        }
        // 撃つ effect の頭は effect の役(絵は面が描く)、defk の呼びは呼びの役
        let settled = line_at(&read, "run-input-request", 89);
        let effect = settled.segments.iter().find(|s| s.role == SegmentRole::Effect).expect("effect の役の字が無い");
        assert_eq!(effect.text, "SettleIntake");
        assert_eq!(effect.effect.as_deref(), Some("SettleIntake"));
        assert!(effect.definition.is_some(), "defeffect へ飛ぶ先が無い");
        let outcome = line_at(&read, "run-input-request", 85);
        assert!(outcome.segments.iter().any(|s| s.role == SegmentRole::Call && s.text == "outcome-of"));
        // 束縛の行は束縛の番号を持ち、型の字は bindings の型と同じ
        let index = outcome.binding.expect("束縛の番号が無い");
        assert_eq!(read.bindings[index].name, "outcome");
        let row = line_at(&read, "judged", 51);
        assert!(row.segments.iter().any(|s| s.role == SegmentRole::UnknownType && s.text == "?"));
    }

    /// V4(U3): 見本の judged・run-input-request の本体の行が全部、`artifacts/v5/entity.html` の本体の行と同じ(when / match の
    /// 字下げと `→` の揃えを含む)。`→` の前の空白は「腕の pattern の最大幅 + 1」— 見本の judged と同じ。見本の run-input-request
    /// だけは空白が 1 つ多い(2 本の見本どうしで揃え方が食い違う手書き — #910 の comment に記録)。
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
        // 腕の中で始まる match の中身は、段(腕の段 + 1)と、match の語の列に揃える空白で持つ
        let inner = line_at(&read, "run-input-request", 92);
        assert_eq!((inner.depth, inner.pad), (2, 21));
        // 腕の書き換え `(:= row answer)` の行は束縛の番号を持つ
        let assign = line_at(&read, "judged", 56);
        assert_eq!(assign.binding.map(|i| read.bindings[i].name.as_str()), Some("row"));
        // match の pattern の class の名は呼びの役で、repo の型へ飛べる
        let arm = line_at(&read, "judged", 55);
        let class = arm.segments.iter().find(|s| s.text == "InputUnreadable").unwrap();
        assert_eq!(class.role, SegmentRole::Call);
        assert!(class.definition.is_some());
    }

    const INTENT: &str = r#"
(defrecord Row "行" (#^ str id))
(defrecord Missing "無い" (#^ str id))
(defeffect ReadRow "行を読む" {:fields [(: id str)] :answer Row :tags {:context "d" :role "intent"}})
(defeffect Emit "出す" {:fields [(: text str)] :answer None :tags {:context "d" :role "intent"}})
"#;

    /// 1 つの defk の本体を読み、行の綴りを返す。
    fn body(lines: &str) -> (FileSignatures, Vec<String>) {
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

    /// V4: 本体の文字の表(v2 2.2・v3 2)の束縛・呼び・effect の撃ち・return / resume の各行。
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
        // 本体の setv は警告の印を持つ。val / var の行は持たない
        let (read, _) = body("  (setv t 5)\n  (val u 1)");
        let lines = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines;
        assert_eq!(lines[0].warning.as_ref().map(|w| w.kind), Some(BodyWarningKind::Setv));
        assert!(lines[0].warning.as_ref().unwrap().message.contains("(val t …)"));
        assert_eq!(lines[1].warning, None);
        // 組が 2 つの宣言は組ごとに行
        let (_, texts) = body("  (val p 1\n       q \"s\")");
        assert_eq!(texts, vec!["val int p = 1", "val str q = \"s\""]);
        // effect の撃ちの頭は effect の役、`!` を出さない
        let (read, _) = body("  (! (Emit \"hi\"))");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments[0].role, SegmentRole::Effect);
        assert_eq!(line.segments[0].effect.as_deref(), Some("Emit"));
        // lazy / session の束縛は bindings に前の語つきで載り、型を持つ
        let (read, _) = body("  (lazy var lv 2)");
        let binding = read.bindings.iter().find(|b| b.name == "lv").expect("lazy var の束縛が無い");
        assert_eq!(binding.modifier, Some(BindingModifier::Lazy));
        assert_eq!(binding.form, super::super::signatures::BindingForm::Var);
    }

    /// V4(U3): 制御の形の表(v2 2.2)— when / if … else … / match(`:if`・class の keyword・`|`・`[…]`・腕の中の塊)/
    /// for(名の組)/ lfor / gfor / sfor(`:if`・節の重ね)。
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
        // 表に無い制御の形・節は lisp のまま: 腕の欠けた match・:setv の内包・名が組でない for
        let (_, texts) = body("  (match o (Row) 1 _)");
        assert_eq!(texts, vec!["(match o (Row) 1 _)"]);
        let (read, texts) = body("  (val vs (lfor x items :setv y (len x) y))");
        assert_eq!(texts, vec!["val ? vs = (lfor x items :setv y (len x) y)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.last().map(|s| s.role), Some(SegmentRole::Lisp));
        let (_, texts) = body("  (for [x items y d]\n    (shape-of x))");
        assert_eq!(texts, vec!["(for [x items y d]", "  (shape-of x))"]);
    }

    /// U20a(#1206): 塊の形 — do・cond・try / except / else / finally・while・continue / break / raise・註・中身の書き換え。
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
            ("  (shape-of a)\n  ;; 註の 1 行目\n  ;;; 註の 2 行目\n  (return a)", &["shape-of(a)", "# 註の 1 行目", "# 註の 2 行目", "return a"]),
        ];
        for (source, expected) in cases {
            let (_, texts) = body(source);
            assert_eq!(texts, expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{:?}", source);
        }
        // 中身の書き換えは束縛ではない — setv の警告を付けない
        let (read, _) = body("  (setv (get d \"k\") 1)");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.warning, None);
        // 註は comment の役で、source の範囲を持つ
        let (read, _) = body("  (shape-of a)\n  ;; 見出し\n  (return a)");
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[1];
        assert_eq!(line.segments[0].role, SegmentRole::Comment);
        assert!(line.segments[0].range.is_some());
        // 複数行の条件も 1 行に平らにして揃える
        let (_, texts) = body("  (cond\n    (and a\n         o) 1\n    True 2)");
        assert_eq!(texts, vec!["cond", "  a and o → 1", "  else    → 2"]);
        // 行の途中に註のある条件は平らにできない — cond 全体を lisp のまま
        let (_, texts) = body("  (cond\n    (and a ;; 註\n         o) 1\n    True 2)");
        assert_eq!(texts[0], "(cond");
        let (_, texts) = body("  (del (get d \"k\") o.a)\n  (assert (is o None) \"m\")");
        assert_eq!(texts, vec!["del d[\"k\"], o.a", "assert o is None, \"m\""]);
    }

    /// U20b(#1207): 式の形 — 式の中の if(3 項)・fn(⇒)・cut・内包(dfor を含む)・字面・値の場所の塊。
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
        // 複数行の式の中の if も 1 行に平らにする
        let (_, texts) = body("  (val x (if (is o None)\n            0\n            (len items)))");
        assert_eq!(texts, vec!["val ? x = 0 if o is None else len(items)"]);
        // 値の場所の cond / match / 長い if は縦に開く(1 行目は束縛の行に続け、中身は塊の語の列に揃える)
        let (_, texts) = body("  (val r (cond (is o None) 0\n               True 1))");
        assert_eq!(texts, vec!["val ? r = cond", "            o is None → 0", "            else      → 1"]);
        let long = "(shape-of a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a)";
        let (_, texts) = body(&format!("  (val r (if a\n             {long}\n             {long}))"));
        assert_eq!(texts[0], "val ? r = if a");
        assert_eq!(texts[2].trim_start(), "else");
        // 腕の → の後ろの短い if は 3 項
        let (_, texts) = body("  (match o\n    (Row) (if a 1 2)\n    _ 3)");
        assert_eq!(texts, vec!["match o", "  Row → 1 if a else 2", "  _   → 3"]);
        // 鍵が keyword の辞書は描かない(元の字のまま — lisp の目印も付けない)
        let (read, texts) = body("  (val d {:k 1})");
        assert_eq!(texts, vec!["val dict d = {:k 1}"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert!(line.segments.iter().all(|s| s.role != SegmentRole::Lisp));
        // 表に無い節(:setv)の内包・引数に #* のある fn は lisp の島のまま
        let (_, texts) = body("  (val v (lfor x items :setv y (len x) y))");
        assert_eq!(texts, vec!["val ? v = (lfor x items :setv y (len x) y)"]);
        let (_, texts) = body("  (val h (fn [#* xs] xs))");
        assert_eq!(texts, vec!["val ? h = (fn [#* xs] xs)"]);
    }

    /// V5: 表に無い form は推測で描かず、元の lisp のまま目印(役 lisp)を付けて出す。
    #[test]
    fn forms_outside_the_table_stay_marked_lisp() {
        let (read, texts) = body("  (my-macro a b)");
        assert_eq!(texts, vec!["(my-macro a b)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.len(), 1);
        assert_eq!(line.segments[0].role, SegmentRole::Lisp);
        // 呼びの引数の中の知らない form も、その括弧だけ lisp の目印
        let (read, texts) = body("  (shape-of (my-macro a) 1)");
        assert_eq!(texts, vec!["shape-of((my-macro a), 1)"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        let lisp: Vec<&str> = line.segments.iter().filter(|s| s.role == SegmentRole::Lisp).map(|s| s.text.as_str()).collect();
        assert_eq!(lisp, vec!["(my-macro a)"]);
        // 束縛の値が知らない form でも束縛の行は描き、値だけ lisp
        let (_, texts) = body("  (val z (check a))");
        assert_eq!(texts, vec!["val ? z = (check a)"]);
        // `:absent F` は表に無い — lisp のまま添える
        let (read, texts) = body("  (<- r Row (ReadRow \"id\") :absent Missing)");
        assert_eq!(texts, vec!["val Row r ⇐ ReadRow(\"id\") :absent Missing"]);
        let line = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines[0];
        assert_eq!(line.segments.last().map(|s| (s.role, s.text.as_str())), Some((SegmentRole::Lisp, ":absent Missing")));
        // 組への分解の setv は表に無い形 — 全体を lisp
        let (_, texts) = body("  (setv #(p q) items)");
        assert_eq!(texts, vec!["(setv #(p q) items)"]);
        // 複数行の知らない form は行を分け、source の行と字下げの差を保つ
        let (read, texts) = body("  (unless a\n    (shape-of a))");
        assert_eq!(texts, vec!["(unless a", "  (shape-of a))"]);
        let lines = &read.bodies.iter().find(|b| b.name == "subject").unwrap().lines;
        assert_eq!(lines[1].line, lines[0].line + 1);
        assert!(lines.iter().all(|l| l.segments.iter().all(|s| matches!(s.role, SegmentRole::Lisp | SegmentRole::Text))));
    }

    /// V6: 本体の各行は source の行を持ち、source から来た字の範囲はその行の上にあって、呼び・名・effect・lisp・式の字は
    /// source の範囲の字と一字一句同じ。束縛の語と型は元の字の範囲を指す。
    #[test]
    fn lines_and_ranges_point_back_to_the_source() {
        let (source, read) = sample();
        let slice = |r: &Range| source.get(offset_of(&source, r.start)..offset_of(&source, r.end)).unwrap().to_string();
        let mut checked = 0;
        for body in &read.bodies {
            for line in &body.lines {
                for segment in &line.segments {
                    let Some(range) = &segment.range else { continue };
                    assert_eq!(range.start.line, line.line, "{}: {:?} の範囲が行 {} の外", body.name, segment.text, line.line + 1);
                    match segment.role {
                        SegmentRole::Name | SegmentRole::Call | SegmentRole::Effect | SegmentRole::Lisp | SegmentRole::Text => {
                            assert_eq!(slice(range), segment.text, "{} の {} 行", body.name, line.line + 1);
                        }
                        SegmentRole::Keyword => {
                            // `<-` は `val` と見せる。それ以外の語は source の綴りのまま
                            let spelled = slice(range);
                            assert!(spelled == "<-" || spelled == segment.text, "{} の {} 行: {:?}", body.name, line.line + 1, spelled);
                            assert!([
                                "<-", "val", "var", "lazy", "session", "setv", "return", "resume", "when", "if", "match", "for",
                                "while", "cond", "try", "except", "finally", "else", "raise", "continue", "break", "del", "assert",
                            ]
                            .contains(&spelled.as_str()));
                        }
                        SegmentRole::Comment => {
                            let spelled = slice(range);
                            assert!(spelled.starts_with(';'), "{} の {} 行: {:?}", body.name, line.line + 1, spelled);
                            assert_eq!(segment.text, format!("# {}", spelled.trim_start_matches(';').trim_start()));
                        }
                        SegmentRole::Type => assert!(slice(range).starts_with('(') || !slice(range).is_empty()),
                        SegmentRole::Assign => assert_eq!(slice(range), ":="),
                        SegmentRole::UnknownType | SegmentRole::Bind => panic!("{:?} は source の範囲を持たない", segment.role),
                    }
                    checked += 1;
                }
            }
        }
        assert!(checked > 20, "照らした字が少なすぎる: {}", checked);
        // 見本の行番号: judged の本体は 50 行から、run-input-request は 85 行から
        let first = |name: &str| read.bodies.iter().find(|b| b.name == name).unwrap().lines[0].line + 1;
        assert_eq!(first("judged"), 50);
        assert_eq!(first("run-input-request"), 85);
        // 型の字の範囲は `(<- x T e)` の T(注釈)
        let line = line_at(&read, "judged", 50);
        let ty = line.segments.iter().find(|s| s.role == SegmentRole::Type).unwrap();
        assert_eq!(slice(ty.range.as_ref().unwrap()), "(| str None)");
        // 束縛の番号の指す束縛の名の範囲 = 名の字の範囲
        for body in &read.bodies {
            for line in body.lines.iter().filter(|l| l.binding.is_some()) {
                let binding = &read.bindings[line.binding.unwrap()];
                let name = line.segments.iter().find(|s| s.role == SegmentRole::Name).unwrap();
                assert_eq!(name.range.as_ref(), Some(&binding.range));
            }
        }
    }
}

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
//! | 表に無い form | 元の lisp のまま(字の役 `lisp` の目印つき)— 推測で描かない(v1 制約 2) |
//!
//! 行ごとに source の行(`line` = 0 始まり — 面は 1 を足して見せる)、字の範囲ごとに source の範囲と役を持つ。呼びの読み
//! (何を呼びと読むか・括弧の要否)は `call_view.rs` の読み手を式 1 つずつ呼んで使い、ここに写しを置かない。

use std::collections::HashSet;

use doeff_indexer::hy_index::reader::{Form, Node, Prefix};
use serde::Serialize;

use crate::position::{offset_of, Range};

use super::call_view::{expression_rewrites, file_level_names, performed_effect, CallNames, PartRole, Rewrite};
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
    /// 字下げの段(本体の一番外 = 0)。複数行の式の続きの行は同じ段で、source の字下げの差を頭の空白の字で持つ。
    pub depth: u32,
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
        let mut printer = Printer { world, reader, names: &names, bindings, lines: Vec::new() };
        // 本体の頭の文字列は説明(後ろに文がある時だけ — 文字列 1 つだけの本体はその文字列が答え)
        let skip = usize::from(matches!(shape.body.as_slice(), [first, _, ..] if matches!(first.node, Node::Str { .. })));
        for item in &shape.body[skip..] {
            printer.statement(item, 0);
        }
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
}

struct Printer<'w, 'r, 'a> {
    world: &'w World,
    reader: &'r FileReader<'a>,
    names: &'r CallNames,
    bindings: &'r [Binding],
    lines: Vec<BodyLine>,
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
    fn start_line(&mut self, offset: usize, depth: u32) {
        let line = self.reader.lines.position(offset).line;
        self.lines.push(BodyLine { line, depth, segments: Vec::new(), binding: None, warning: None });
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
        let mut at = start;
        let mut first = true;
        for piece in src.get(start..end).unwrap_or("").split('\n') {
            let piece_start = at;
            at += piece.len() + 1;
            let (lead, body) = if first {
                (0, piece)
            } else {
                let trimmed = piece.trim_start_matches([' ', '\t']);
                let lead = piece.len() - trimmed.len();
                let depth = self.current().depth;
                self.start_line(piece_start + lead, depth);
                (lead, trimmed)
            };
            if !first {
                let indent = (lead as u32).saturating_sub(base);
                self.text(&" ".repeat(indent as usize), SegmentRole::Text);
            }
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
    fn statement(&mut self, form: &Form, depth: u32) {
        self.start_line(form.span.start, depth);
        let base = self.column(form.span.start);
        let Some(items) = live(form) else {
            self.expression(form, base);
            return;
        };
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
            Some("val" | "var" | "lazy" | "session") => self.declaration(&items, base, depth),
            Some("setv") => self.setv(&items, base, depth),
            Some(":=") => self.assign(&items, base),
            Some("return" | "resume") => self.keyword_statement(&items, base),
            // 呼び・知らない頭・頭の無い括弧は式として読む(置き換えなかった括弧は lisp の島)
            Some(_) | None => {
                self.expression(form, base);
                true
            }
        };
        if !drawn {
            // 表の形に読めなかった(名が記号でない・値が欠けた …)— 推測で描かず lisp のまま
            self.current().segments.clear();
            self.current().binding = None;
            self.current().warning = None;
            self.lisp(form.span.start, form.span.end, base);
        }
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
    fn declaration(&mut self, items: &[&Form], base: u32, depth: u32) -> bool {
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
                self.start_line(pair.name.span.start, depth);
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
                    self.expression(value, base);
                }
            }
        }
        true
    }

    /// 本体の `(setv x e)` → `setv x = e` + 警告の印。
    fn setv(&mut self, items: &[&Form], base: u32, depth: u32) -> bool {
        let pairs: Vec<&[&Form]> = items[1..].chunks(2).collect();
        if pairs.is_empty() || pairs.iter().any(|p| p.len() != 2 || !matches!(p[0].node, Node::Symbol)) {
            return false;
        }
        for (i, pair) in pairs.iter().enumerate() {
            let (name, value) = (pair[0], pair[1]);
            if i > 0 {
                self.start_line(name.span.start, depth);
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
            self.expression(value, base);
        }
        true
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
            None => self.expression(value, base),
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
                self.expression(value, base);
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

    /// 式 1 つ(置き換えの読みで `f(a, b)` の形に。置き換えなかった括弧は lisp の島)。
    fn expression(&mut self, form: &Form, base: u32) {
        let rewrites = expression_rewrites(self.world, self.reader, self.names, form);
        let offset = |r: &Range| (offset_of(self.src(), r.start), offset_of(self.src(), r.end));
        let rewritten: HashSet<(usize, usize)> = rewrites.iter().map(|r| offset(&r.range)).collect();
        let mut islands = Vec::new();
        find_islands(form, &rewritten, &mut islands);
        let inside = |s: usize, e: usize| {
            islands.iter().any(|&(is, ie)| s >= is && e <= ie && !(s == e && (s == is || s == ie)))
        };
        let mut overlays: Vec<(usize, usize, Overlay)> = rewrites
            .iter()
            .flat_map(|r| r.edits.iter())
            .map(|e| (offset(&e.range), e.text.as_str()))
            .filter(|((s, e), _)| !inside(*s, *e))
            .map(|((s, e), text)| (s, e, Overlay::Edit(text)))
            .collect();
        overlays.extend(islands.iter().map(|&(s, e)| (s, e, Overlay::Island)));
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
            }
            at = *e;
        }
        self.expression_source(at, form.span.end, base, &parts);
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
fn find_islands(form: &Form, rewritten: &HashSet<(usize, usize)>, out: &mut Vec<(usize, usize)>) {
    match &form.node {
        Node::Seq { delim, items } => {
            let span = (form.span.start, form.span.end);
            if matches!(delim, doeff_indexer::hy_index::reader::Delim::Paren) && !rewritten.contains(&span) {
                out.push(span);
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
        format!("{}{}", "  ".repeat(line.depth as usize), text)
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
        // 名が記号でない setv(中身の書き換え)は表に無い形 — 全体を lisp
        let (_, texts) = body("  (setv (get d \"k\") 1)");
        assert_eq!(texts, vec!["(setv (get d \"k\") 1)"]);
        // 複数行の知らない form は行を分け、source の行と字下げの差を保つ
        let (read, texts) = body("  (when a\n    (shape-of a))");
        assert_eq!(texts, vec!["(when a", "  (shape-of a))"]);
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
                            assert!(["<-", "val", "var", "lazy", "session", "setv", "return", "resume"].contains(&slice(range).as_str()));
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

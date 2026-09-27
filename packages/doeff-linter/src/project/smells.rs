//! 臭いの規則(DOEFF121〜125)— 業務の Hy の定義の中の、型と effect で書けるのに手で書いた形を拾う。
//!
//! operator 2026-09-28(逐語 "lets add them")。題材は agora-controllers の controllers/kanban/core/tag_judgment.hy の decide-tag。
//! どれも決定的な形の照らしで、重さの既定は warning(ADR-DOE-HY-007 R9 — Absent / Raise が本線に入ったので info から上げた)。
//!
//! - DOEFF121: 判断の層(設定の `smells.shape_check_layers`)の定義が、文字列の鍵の `(.get x "欄")` と、その欄への `isinstance` で
//!   入力の形を検める(JSON の形の検めが core に入っている)。
//! - DOEFF122: match の腕が、失敗の型(宣言から取る — defrecord の `:failure True` と defeffect の `:failure` / `:absent`)を受けて、
//!   受けた値をそのまま、または包み直して return するだけ(手書きの例外の再送出)。
//! - DOEFF123: `(<- x T (f …))` の直後に `(return x)` が来て、x を他で使わない。
//! - DOEFF124: 同じ値の 2 つ以上の欄を、文字列と一緒に `+`(か f 文字列)で 1 本の文字列につなぐ。
//! - DOEFF125: for / while の中の `(:= xs (+ xs #(…)))`・`(setv xs (+ xs […]))`(毎回作り直す蓄え)。
//!
//! Hy は doeff-indexer の読み取り器で form の木にしてから読む(Hy の読み取り部を写さない)。

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};

use super::facts::ByteSpan;
use super::names::hy_mangle;

/// 臭いの種類と、説明の文に差し込む材料(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SmellKind {
    /// DOEFF121: 文字列の鍵で読んだ欄を isinstance で検める。
    ShapeCheck { field: String, holder: String },
    /// DOEFF122: 失敗の型の腕が、受けた値を return し直すだけ。
    FailureRethrow { failure_type: String, subject: String },
    /// DOEFF123: 束ねてすぐ返すだけの `<-`。
    BindThenReturn { name: String },
    /// DOEFF124: 同じ値の欄を文字列につなぐ。
    FieldsJoined { value: String, fields: Vec<String> },
    /// DOEFF125: ループの中で毎回作り直す蓄え。
    RebuiltAccumulator { name: String },
}

/// 臭い 1 件(どの定義の、どこで)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Smell {
    /// 定義の名(mangle 済み)。
    pub definition: String,
    pub span: ByteSpan,
    pub kind: SmellKind,
}

impl Smell {
    /// 登録簿の鍵の細目(`<定義>::<名>`)— 同じ定義の同じ名の臭いは 1 件に数える。
    pub fn detail(&self) -> String {
        let name = match &self.kind {
            SmellKind::ShapeCheck { field, .. } => hy_mangle(field),
            SmellKind::FailureRethrow { subject, .. } => hy_mangle(subject),
            SmellKind::BindThenReturn { name } | SmellKind::RebuiltAccumulator { name } => hy_mangle(name),
            SmellKind::FieldsJoined { value, .. } => hy_mangle(value),
        };
        format!("{}::{}", self.definition, name)
    }
}

/// 失敗の型の集合(module まで含めた名・mangle 済み — `controllers.core.kanban_write_rules.Refusal`)— 宣言から集める。
/// 型の名から推し量らない。同じ名の型が別の module にあっても、宣言した方だけが失敗の型になる。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct FailureTypes {
    names: BTreeSet<String>,
}

/// 名を解く材料 — file の module の綴りと、import の束縛(名 → module)。
#[derive(Debug, Clone, Copy)]
pub struct Scope<'a> {
    pub module: &'a str,
    pub bindings: &'a BTreeMap<String, String>,
}

impl Scope<'_> {
    /// 型の綴りを module まで含めた名に解く — `(import m [T])` の T は `m.T`、`(import m :as n)` の `n.T` は `m.T`、
    /// 束縛の無い裸の名はこの file の定義(`<module>.T`)。
    pub fn qualify(&self, spelled: &str) -> String {
        let mangled: Vec<String> = spelled.split('.').map(hy_mangle).collect();
        let head = spelled.split('.').next().unwrap_or(spelled);
        match (mangled.as_slice(), self.bindings.get(head)) {
            ([name], Some(module)) => format!("{}.{}", module, name),
            ([name], None) => format!("{}.{}", self.module, name),
            ([_, rest @ ..], Some(module)) => format!("{}.{}", module, rest.join(".")),
            (all, None) => all.join("."),
            ([], _) => String::new(),
        }
    }
}

impl FailureTypes {
    /// module まで含めた名の列から作る(検のため)。
    pub fn of(names: &[&str]) -> Self {
        FailureTypes { names: names.iter().map(|n| n.to_string()).collect() }
    }

    /// module まで含めた名が失敗の型か。
    pub fn contains(&self, qualified: &str) -> bool {
        self.names.contains(qualified)
    }

    /// 別の集合を足す。
    pub fn extend(&mut self, other: FailureTypes) {
        self.names.extend(other.names);
    }

    /// 宣言の数(報告のため)。
    pub fn len(&self) -> usize {
        self.names.len()
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }
}

/// 1 つの source の失敗の型の宣言 — `(defrecord 名 "doc"? {… :failure True …} …)` の名と、
/// `(defeffect 名 "doc"? {… :failure [A B] :absent [C] …})` の列の型(scope で module まで含めた名に解く)。
pub fn failure_types_in(source: &str, scope: Scope<'_>) -> FailureTypes {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut names = BTreeSet::new();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defrecord") => {
                let Some(name) = items.get(1).and_then(|n| hy.symbol(n)) else { continue };
                if let Some(header) = items[2..].iter().find(|f| f.is_brace()).filter(|_| hy.header_position(&items[2..])) {
                    if hy.dict_value(header, ":failure").and_then(|v| hy.symbol(v)) == Some("True") {
                        names.insert(format!("{}.{}", scope.module, hy_mangle(name)));
                    }
                }
            }
            Some("defeffect") => {
                let Some(contract) = items[2..].iter().take(2).find(|f| f.is_brace()) else { continue };
                for key in [":failure", ":absent"] {
                    let types = hy.dict_value(contract, key).and_then(|v| v.bracket_items()).map(live_items).unwrap_or_default();
                    names.extend(types.into_iter().filter_map(|t| hy.symbol(t)).map(|t| scope.qualify(t)));
                }
            }
            _ => {}
        }
    }
    FailureTypes { names }
}

/// 1 つの source の臭いを全部拾う。`shape_checks` は DOEFF121 を当てるか(判断の層の file だけ)。scope は match の腕の型を解くため。
pub fn smells_in(source: &str, scope: Scope<'_>, failure: &FailureTypes, shape_checks: bool) -> Vec<Smell> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found: Vec<Smell> = Vec::new();
    for (name, form) in hy.definitions(&forms.iter().collect::<Vec<_>>()) {
        let mut smells = Vec::new();
        if shape_checks {
            hy.shape_checks(form, &name, &mut smells);
        }
        hy.walk(form, &name, &Judge { failure, scope }, form, false, &mut smells);
        for smell in smells {
            // 同じ定義の同じ細目は最初の 1 件だけ(鍵を 1 つにする)。
            if !found.iter().any(|f| f.kind_id() == smell.kind_id() && f.detail() == smell.detail()) {
                found.push(smell);
            }
        }
    }
    found
}

impl Smell {
    /// 種類の番号(重なりの判定のため)。
    fn kind_id(&self) -> u8 {
        match self.kind {
            SmellKind::ShapeCheck { .. } => 1,
            SmellKind::FailureRethrow { .. } => 2,
            SmellKind::BindThenReturn { .. } => 3,
            SmellKind::FieldsJoined { .. } => 4,
            SmellKind::RebuiltAccumulator { .. } => 5,
        }
    }
}

/// `( … )` の中身(読み捨てを除く)。
pub(super) fn live(form: &Form) -> Option<Vec<&Form>> {
    form.paren_items().map(live_items)
}

/// 列の中身から読み捨て(`#_`)を除く。
pub(super) fn live_items(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// form の子(列の中身・注記の的・前置きの中身)。
pub(super) fn children(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { items, .. } => live_items(items),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => vec![inner.as_ref()],
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().map(|b| b.as_ref()).collect(),
        _ => Vec::new(),
    }
}

/// form の範囲を ByteSpan にする。
pub(super) fn span_of(form: &Form) -> ByteSpan {
    ByteSpan { start: form.span.start, end: form.span.end }
}

/// 定義の頭(臭いを探す定義)。
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler"];

/// ループの頭(DOEFF125 の「毎回」)。内包表記(lfor・gfor)は蓄えを作り直さないので数えない。
const LOOP_HEADS: &[&str] = &["for", "while"];

/// 束ねの頭(DOEFF121 と DOEFF122 が「値を名に置いた」と読む形)。
const BINDING_HEADS: &[&str] = &["val", "var", "setv", "setx", ":=", "<-"];

/// DOEFF122 の材料 — 失敗の型の集合と、型の綴りを解く scope。
struct Judge<'a> {
    failure: &'a FailureTypes,
    scope: Scope<'a>,
}

/// Hy の source と、form の綴りを読む道具。
pub(super) struct Hy<'a> {
    pub(super) src: &'a str,
}

impl<'a> Hy<'a> {
    /// form の綴り。
    pub(super) fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// 記号の綴り(記号でなければ None)。
    pub(super) fn symbol(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol => Some(self.text(form)),
            _ => None,
        }
    }

    /// 列の頭の綴り(記号か keyword — `(:= x v)` の `:=` は keyword として読まれる)。
    pub(super) fn head(&self, form: &Form) -> Option<&'a str> {
        let items = form.paren_items()?;
        let first = items.iter().find(|i| !matches!(i.node, Node::Discarded))?;
        match first.node {
            Node::Symbol | Node::Keyword => Some(self.text(first)),
            _ => None,
        }
    }

    /// 字面の文字列(f 文字列・bytes を除く)の中身。
    fn string_literal(&self, form: &Form) -> Option<&'a str> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end),
            _ => None,
        }
    }

    /// 頭の辞書が名(と docstring)の直後にあるか(defrecord の頭の辞書の位置)。
    fn header_position(&self, rest: &[&Form]) -> bool {
        match rest {
            [first, ..] if first.is_brace() => true,
            [doc, second, ..] if self.string_literal(doc).is_some() => second.is_brace(),
            _ => false,
        }
    }

    /// 辞書の key の値。
    fn dict_value<'f>(&self, dict: &'f Form, key: &str) -> Option<&'f Form> {
        let items = match &dict.node {
            Node::Seq { delim: Delim::Brace, items } => live_items(items),
            _ => return None,
        };
        items.chunks(2).find_map(|pair| match pair {
            [k, v] if matches!(k.node, Node::Keyword) && self.text(k) == key => Some(*v),
            _ => None,
        })
    }

    /// 最上位の定義(`do` と `eval-and-compile` の中も)の名と form。
    fn definitions<'f>(&self, forms: &[&'f Form]) -> Vec<(String, &'f Form)> {
        let mut out = Vec::new();
        for form in forms {
            let Some(items) = live(form) else { continue };
            match items.first().and_then(|h| self.symbol(h)) {
                Some("do" | "eval-and-compile" | "eval-when-compile") => out.extend(self.definitions(&items[1..])),
                Some(head) if DEFINITION_HEADS.contains(&head) => {
                    let name_form = match items.get(1) {
                        Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                        other => other.copied(),
                    };
                    let name = name_form.map(|f| match &f.node {
                        Node::Annotated { target: Some(target), .. } => self.text(target),
                        _ => self.text(f),
                    });
                    if let Some(name) = name.filter(|n| !n.is_empty()) {
                        out.push((hy_mangle(name), *form));
                    }
                }
                _ => {}
            }
        }
        out
    }

    /// `(.get x "欄")` なら (x の綴り・欄) を返す。
    fn string_key_get(&self, form: &Form) -> Option<(String, String)> {
        let items = live(form)?;
        match items.as_slice() {
            [head, holder, key, ..] if self.symbol(head) == Some(".get") => {
                Some((self.text(holder).to_string(), self.string_literal(key)?.to_string()))
            }
            _ => None,
        }
    }

    /// DOEFF121: 定義の中の、文字列の鍵で読んだ欄への isinstance を拾う。
    fn shape_checks(&self, definition: &Form, name: &str, out: &mut Vec<Smell>) {
        // 1. 名に置いた欄: (val n (.get x "欄")) など。
        let mut bound: Vec<(String, String, String, ByteSpan)> = Vec::new();
        self.each(definition, &mut |form| {
            let Some(items) = live(form) else { return };
            if !items.first().and_then(|h| self.head_text(h)).is_some_and(|h| BINDING_HEADS.contains(&h)) {
                return;
            }
            for pair in items[1..].windows(2) {
                if let (Some(target), Some((holder, field))) = (self.symbol(pair[0]), self.string_key_get(pair[1])) {
                    bound.push((target.to_string(), holder, field, span_of(pair[1])));
                }
            }
        });
        // 2. (isinstance 名 T) か (isinstance (.get x "欄") T)。
        self.each(definition, &mut |form| {
            let Some(items) = live(form) else { return };
            let [head, checked, ..] = items.as_slice() else { return };
            if self.symbol(head) != Some("isinstance") {
                return;
            }
            let hit = match (self.string_key_get(checked), self.symbol(checked)) {
                (Some((holder, field)), _) => Some((holder, field, span_of(checked))),
                (None, Some(symbol)) => bound.iter().find(|(t, ..)| t == symbol).map(|(_, h, f, s)| (h.clone(), f.clone(), *s)),
                (None, None) => None,
            };
            if let Some((holder, field, span)) = hit {
                out.push(Smell { definition: name.to_string(), span, kind: SmellKind::ShapeCheck { field, holder } });
            }
        });
    }

    /// 列の頭の綴り(記号か keyword)を form 1 つから。
    pub(super) fn head_text(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol | Node::Keyword => Some(self.text(form)),
            _ => None,
        }
    }

    /// form とその子孫を前から順に訪ねる。
    fn each(&self, form: &Form, visit: &mut dyn FnMut(&Form)) {
        visit(form);
        for child in children(form) {
            self.each(child, visit);
        }
    }

    /// 式が names のどれか(`名` か `名.欄`)を参照するか。
    fn refers(&self, form: &Form, names: &BTreeSet<String>) -> bool {
        let mut found = false;
        self.each(form, &mut |f| {
            if let Some(symbol) = self.symbol(f) {
                let head = symbol.split('.').next().unwrap_or(symbol);
                if names.contains(head) {
                    found = true;
                }
            }
        });
        found
    }

    /// 定義の中で記号 name(`name` か `name.欄`)が出る回数。
    fn uses(&self, definition: &Form, name: &str) -> usize {
        let mut count = 0;
        self.each(definition, &mut |f| {
            if let Some(symbol) = self.symbol(f) {
                if symbol == name || symbol.strip_prefix(name).is_some_and(|rest| rest.starts_with('.')) {
                    count += 1;
                }
            }
        });
        count
    }

    /// DOEFF122〜125 を拾いながら木を下る。`in_loop` は for / while の中か。
    fn walk(&self, form: &Form, name: &str, judge: &Judge<'_>, definition: &Form, in_loop: bool, out: &mut Vec<Smell>) {
        let head = self.head(form);
        if let Some(items) = live(form) {
            match head {
                Some("match") => self.failure_rethrows(&items, name, judge, out),
                Some("+") => self.joined_fields(form, &items, name, out),
                Some(":=" | "setv" | "setx") if in_loop => self.rebuilt_accumulator(form, &items, name, out),
                _ => {}
            }
            // DOEFF123: 並んだ 2 つの子 (<- x …) (return x)。
            for pair in items.windows(2) {
                self.bind_then_return(pair[0], pair[1], name, definition, out);
            }
        }
        if let Node::Str { kind: StrKind::Format | StrKind::FormatBracket, body } = &form.node {
            self.joined_fields_in_fstring(form, self.src.get(body.start..body.end).unwrap_or(""), name, out);
        }
        let inner_loop = in_loop || head.is_some_and(|h| LOOP_HEADS.contains(&h));
        for child in children(form) {
            self.walk(child, name, judge, definition, inner_loop, out);
        }
    }

    /// DOEFF122: `(match 主 (失敗の型 …) 腕 …)` の腕が、受けた値(か、それから作った値)を return するだけ。
    fn failure_rethrows(&self, items: &[&Form], name: &str, judge: &Judge<'_>, out: &mut Vec<Smell>) {
        let Some(subject) = items.get(1) else { return };
        let subject_name = self.symbol(subject).map(str::to_string);
        let mut index = 2;
        while index < items.len() {
            let pattern = items[index];
            index += 1;
            let mut bound: BTreeSet<String> = subject_name.iter().cloned().collect();
            // `:as 名` と `:if 条件` を飛ばす(:as の名は受けた値の別名)。
            while index + 1 < items.len() && matches!(items[index].node, Node::Keyword) {
                if self.text(items[index]) == ":as" {
                    if let Some(alias) = self.symbol(items[index + 1]) {
                        bound.insert(alias.to_string());
                    }
                }
                index += 2;
            }
            let Some(body) = items.get(index) else { return };
            index += 1;
            let Some(pattern_items) = live(pattern) else { continue };
            let Some(type_name) = pattern_items.first().and_then(|h| self.symbol(h)) else { continue };
            if !judge.failure.contains(&judge.scope.qualify(type_name)) {
                continue;
            }
            // pattern の中で束ねた名も受けた値の一部。
            for part in &pattern_items[1..] {
                self.each(part, &mut |f| {
                    if let Some(symbol) = self.symbol(f).filter(|s| *s != "_") {
                        bound.insert(symbol.to_string());
                    }
                });
            }
            let forms: Vec<&Form> = match live(body) {
                Some(inner) if self.head(body) == Some("do") => inner[1..].to_vec(),
                _ => vec![*body],
            };
            let Some((last, before)) = forms.split_last() else { continue };
            let Some([ret, value]) = live(last).as_deref().map(|l| l.to_vec()).and_then(|l| <[&Form; 2]>::try_from(l).ok()) else { continue };
            if self.symbol(ret) != Some("return") {
                continue;
            }
            // 腕の中で受けた値から作った名(`(<- x T (rejected (+ said.reason …)))`)も、受けた値の包み直し。
            for form in before {
                let Some(parts) = live(form) else { continue };
                let is_binding = parts.first().and_then(|h| self.head_text(h)).is_some_and(|h| BINDING_HEADS.contains(&h));
                if let (true, Some(target), Some(value)) = (is_binding, parts.get(1).and_then(|t| self.symbol(t)), parts.last()) {
                    if self.refers(value, &bound) {
                        bound.insert(target.to_string());
                    }
                }
            }
            if self.refers(value, &bound) {
                out.push(Smell {
                    definition: name.to_string(),
                    span: span_of(pattern),
                    kind: SmellKind::FailureRethrow {
                        failure_type: type_name.to_string(),
                        subject: subject_name.clone().unwrap_or_else(|| type_name.to_string()),
                    },
                });
            }
        }
    }

    /// DOEFF123: `(<- x …)` の直後の `(return x)` で、x を定義の中で他に使わない。
    fn bind_then_return(&self, first: &Form, second: &Form, name: &str, definition: &Form, out: &mut Vec<Smell>) {
        let (Some(bind), Some(ret)) = (live(first), live(second)) else { return };
        if bind.len() < 3 || bind.first().and_then(|h| self.symbol(h)) != Some("<-") {
            return;
        }
        let Some(target) = bind.get(1).and_then(|t| self.symbol(t)) else { return };
        let returned = match ret.as_slice() {
            [head, value] if self.symbol(head) == Some("return") => self.symbol(value),
            _ => None,
        };
        if returned == Some(target) && self.uses(definition, target) == 2 {
            out.push(Smell { definition: name.to_string(), span: span_of(first), kind: SmellKind::BindThenReturn { name: target.to_string() } });
        }
    }

    /// DOEFF124: `(+ …)` が文字列と、同じ値の 2 つ以上の欄(`v.a`・`(str v.b)`)をつなぐ。
    fn joined_fields(&self, form: &Form, items: &[&Form], name: &str, out: &mut Vec<Smell>) {
        let args = &items[1..];
        let has_text = args.iter().any(|a| matches!(a.node, Node::Str { .. }));
        if !has_text {
            return;
        }
        let mut fields: Vec<(String, String)> = Vec::new();
        for arg in args {
            let target = match (self.symbol(arg), live(arg)) {
                (Some(symbol), _) => Some(symbol),
                (None, Some(inner)) if inner.len() == 2 && matches!(self.symbol(inner[0]), Some("str" | "repr")) => self.symbol(inner[1]),
                _ => None,
            };
            if let Some((value, field)) = target.and_then(|t| t.split_once('.')) {
                fields.push((value.to_string(), field.to_string()));
            }
        }
        self.push_joined(span_of(form), &fields, name, out);
    }

    /// DOEFF124 の f 文字列の形: `f"{v.a}: {v.b}"`。
    fn joined_fields_in_fstring(&self, form: &Form, body: &str, name: &str, out: &mut Vec<Smell>) {
        let mut fields = Vec::new();
        for piece in body.split('{').skip(1) {
            let inner = piece.split(['}', '!', ':', ' ', ')']).next().unwrap_or("");
            if let Some((value, field)) = inner.split_once('.') {
                if !value.is_empty() && value.chars().all(|c| c.is_alphanumeric() || c == '-' || c == '_') {
                    fields.push((value.to_string(), field.to_string()));
                }
            }
        }
        self.push_joined(span_of(form), &fields, name, out);
    }

    /// 同じ値の欄が 2 つ以上なら DOEFF124 を積む。
    fn push_joined(&self, span: ByteSpan, fields: &[(String, String)], name: &str, out: &mut Vec<Smell>) {
        let values: BTreeSet<&String> = fields.iter().map(|(v, _)| v).collect();
        for value in values {
            let mut distinct: Vec<String> = Vec::new();
            for (v, f) in fields {
                if v == value && !distinct.contains(f) {
                    distinct.push(f.clone());
                }
            }
            if distinct.len() >= 2 {
                out.push(Smell { definition: name.to_string(), span, kind: SmellKind::FieldsJoined { value: value.clone(), fields: distinct } });
            }
        }
    }

    /// DOEFF125: ループの中の `(:= xs (+ xs #(…)))`・`(setv xs (+ xs […]))`。
    fn rebuilt_accumulator(&self, form: &Form, items: &[&Form], name: &str, out: &mut Vec<Smell>) {
        let [_, target, value] = items else { return };
        let Some(target) = self.symbol(target) else { return };
        let Some(parts) = live(value) else { return };
        let rebuilt = match parts.as_slice() {
            [plus, first, second] if self.symbol(plus) == Some("+") && self.symbol(first) == Some(target) => {
                matches!(&second.node, Node::Seq { delim: Delim::Tuple | Delim::Bracket, .. })
            }
            _ => false,
        };
        if rebuilt {
            out.push(Smell { definition: name.to_string(), span: span_of(form), kind: SmellKind::RebuiltAccumulator { name: target.to_string() } });
        }
    }
}

/// path が Hy の file か(失敗の型の宣言を集める母集団)。
pub fn is_hy(path: &Path) -> bool {
    matches!(path.extension().and_then(|e| e.to_str()), Some("hy" | "hyk" | "hyp"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 臭いを (種類の番号・細目) の列にする(module m・import なし — 失敗の型は `m.<名>` で渡す)。
    fn found(source: &str, failure: &[&str], shape: bool) -> Vec<(u8, String)> {
        let bindings = BTreeMap::new();
        smells_in(source, Scope { module: "m", bindings: &bindings }, &FailureTypes::of(failure), shape).iter().map(|s| (s.kind_id(), s.detail())).collect()
    }

    #[test]
    fn failure_types_come_from_declarations_not_names() {
        let source = r#"(defrecord Refusal "断り" {:failure True} #^ str reason)
(defrecord Accepted {:tags {:context "c" :role "type"}} #^ str word)
(defrecord Rejection #^ str reason)
(defeffect ReadRow "行を読む" {:fields [key] :answer (| Row Missing Unreachable) :absent [Missing] :failure [store.Unreachable]})
"#;
        let bindings: BTreeMap<String, String> = [("store".to_string(), "app.store".to_string())].into_iter().collect();
        let types = failure_types_in(source, Scope { module: "app.rules", bindings: &bindings });
        // defrecord の宣言はこの module の名、defeffect の列は import で解いた名。
        assert!(types.contains("app.rules.Refusal") && types.contains("app.rules.Missing") && types.contains("app.store.Unreachable"));
        // 名が失敗らしくても、宣言が無ければ失敗の型ではない。別の module の同じ名も違う型。
        assert!(!types.contains("app.rules.Rejection") && !types.contains("app.rules.Accepted") && !types.contains("app.webapp.Refusal"));
        assert_eq!(types.len(), 3);
        // 腕の型の綴りを解く: import した名・別名の module・裸の名(この file の定義)。
        let scope_bindings: BTreeMap<String, String> =
            [("Refusal".to_string(), "app.rules".to_string()), ("r".to_string(), "app.rules".to_string())].into_iter().collect();
        let scope = Scope { module: "app.core.x", bindings: &scope_bindings };
        assert_eq!(scope.qualify("Refusal"), "app.rules.Refusal");
        assert_eq!(scope.qualify("r.Refusal"), "app.rules.Refusal");
        assert_eq!(scope.qualify("Local"), "app.core.x.Local");
    }

    #[test]
    fn shape_checks_on_string_keys_are_found_only_when_asked() {
        let bad = r#"(defk decide [payload]
  (val subject (.get payload "subject"))
  (when (not (isinstance subject str)) (return None))
  (when (isinstance (.get payload "by") str) (return 1))
  (val size (.get payload "size"))
  size)"#;
        assert_eq!(found(bad, &[], true), vec![(1, "decide::subject".to_string()), (1, "decide::by".to_string())]);
        assert!(found(bad, &[], false).is_empty());
        // 型のある値の isinstance と、鍵の無い get は当たらない。
        let good = "(defk decide [row] (when (isinstance row.subject str) 1) (val x (get row 0)) (isinstance x int))";
        assert!(found(good, &[], true).is_empty());
    }

    #[test]
    fn failure_arms_that_only_return_the_failure_are_found() {
        let source = r#"(defk decide [x]
  (<- said (| Accepted Refusal) (verdict x))
  (match said
    (Refusal) (do (<- refused Plan (rejected (+ said.reason ": " said.detail)))
                  (return refused))
    (Accepted) None)
  (<- again (| Accepted Refusal) (verdict x))
  (match again
    (Refusal :reason r) (return (Plan :why r))
    (Accepted) (return (Accepted :word "w")))
  (<- third (| Accepted Refusal) (verdict x))
  (match third
    (Refusal) (do (log "断った") (return (Plan :why "固定の文")))
    (Accepted) None)
  (match (verdict x)
    (Refusal) :as r (return r)
    _ None))"#;
        let hits: Vec<(u8, String)> = found(source, &["m.Refusal"], false).into_iter().filter(|(k, _)| *k == 2).collect();
        // said(包み直し)・again(pattern で束ねた欄)・:as の別名は当たる。受けた値を使わない腕と成功の腕は当たらない。
        assert_eq!(hits, vec![(2, "decide::said".to_string()), (2, "decide::again".to_string()), (2, "decide::Refusal".to_string())]);
        // 宣言が無ければ(失敗の型が空)何も出さない。
        assert!(found(source, &[], false).iter().all(|(k, _)| *k != 2));
    }

    #[test]
    fn bind_then_return_only_when_the_name_is_not_used_again() {
        let source = r#"(defk f [x]
  (when x
    (<- plan Plan (build x))
    (return plan))
  (<- kept Plan (build x))
  (log kept)
  (return kept))"#;
        assert_eq!(found(source, &[], false), vec![(3, "f::plan".to_string())]);
    }

    #[test]
    fn fields_joined_into_a_string() {
        let source = r#"(defk f [said p]
  (val a (+ said.reason ": " said.detail))
  (val b (+ p.x p.y))
  (val c (+ "at " (str p.x) "," (str p.y)))
  (val d f"{said.reason}: {said.detail}")
  (val e (+ said.reason ": " other.detail)))"#;
        // 数の和(文字列の無い +)と、違う値の欄は当たらない。
        assert_eq!(found(source, &[], false), vec![(4, "f::said".to_string()), (4, "f::p".to_string())]);
    }

    #[test]
    fn rebuilt_accumulators_only_inside_loops() {
        let source = r#"(defk f [words]
  (var writes #())
  (for [w words]
    (when w
      (:= writes (+ writes #((Attach w))))))
  (while True (setv seen (+ seen [1])))
  (:= once (+ once #(1)))
  (lfor w words (:= xs (+ xs #(w)))))"#;
        assert_eq!(found(source, &[], false), vec![(5, "f::writes".to_string()), (5, "f::seen".to_string())]);
    }
}

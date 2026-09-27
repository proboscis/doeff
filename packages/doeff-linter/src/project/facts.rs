//! 層の module 1 つから、層の規則が読む事実(タグ・import の先・関数の定義)を取り出す。
//!
//! Hy は doeff-indexer の読み取り器(`doeff_indexer::hy_index::reader`)で form の木にしてから読む — Hy の読み取り部は
//! ここに写さない。Python は rustpython の構文木から読む。どちらも位置は byte の範囲で持ち、出力の時に UTF-16 へ直す。
//!
//! 読み方は agora-controllers の `scripts/module_tags.hy`(`read-modules`・`hy-definitions`・`hy-imports`・`py-imports`)と
//! 同じ結果になるように合わせてある(登録簿の鍵を揃えるため)。

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};
use rustpython_ast::{Constant, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use super::names::{absolute_module, hy_mangle};
use super::settings::TagReading;

/// source の中の byte の範囲 `[start, end)`。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct ByteSpan {
    pub start: usize,
    pub end: usize,
}

/// 名前とその位置。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NamedSpan {
    pub name: String,
    pub span: ByteSpan,
}

/// 名乗ったタグ 1 組(辞書の中の文字列の値を持つ鍵が 1 つ以上ある時だけ作る)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TagSet {
    pub context: Option<String>,
    pub role: Option<String>,
    /// 辞書の位置。
    pub span: ByteSpan,
}

/// import の先 1 つ(module ごとの import は module の綴り、名を並べた import は `module.名`)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportTarget {
    pub target: String,
    pub span: ByteSpan,
}

/// module の言語。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Hy,
    Python,
}

/// 定義 1 つのタグ(定義の名と、名乗ったタグ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaggedDefinition {
    pub name: NamedSpan,
    pub tags: TagSet,
}

/// 層の規則が読む module 1 つの事実。
#[derive(Debug, Clone)]
pub struct ModuleFacts {
    pub language: Language,
    /// module の頭のタグ(Hy の `(val MODULE-TAGS {…})`・Python の `MODULE_TAGS = {…}`)。
    pub module_tags: Option<TagSet>,
    /// 定義の :tags(同じ名の定義は後の物が前の物の値を上書きし、順は最初の物の位置 — Python の dict と同じ)。
    pub tagged: Vec<TaggedDefinition>,
    /// タグの無い定義(Python の module は常に空)。
    pub untagged: Vec<NamedSpan>,
    pub imports: Vec<ImportTarget>,
    /// 最上位の関数と handler の定義(型だけの層の規則のため)。
    pub functions: Vec<NamedSpan>,
    /// 読めなかった理由(読めた分の事実は残す)。
    pub errors: Vec<String>,
}

impl ModuleFacts {
    /// module の実効のタグの全部(定義の :tags と、タグの無い定義に効く module の頭のタグ)。
    pub fn tag_sets(&self) -> Vec<&TagSet> {
        let mut sets: Vec<&TagSet> = self.tagged.iter().map(|t| &t.tags).collect();
        if let Some(module_tags) = &self.module_tags {
            if !self.untagged.is_empty() || self.tagged.is_empty() {
                sets.push(module_tags);
            }
        }
        sets
    }

    /// 地図に出す 1 組のタグ(module の頭のタグ、無ければ最初の定義のタグ)。
    pub fn summary_tags(&self) -> Option<&TagSet> {
        self.module_tags.as_ref().or_else(|| self.tagged.first().map(|t| &t.tags))
    }
}

/// source を言語ごとの読み方で読む。module は相対 import を解く基準の綴り。
pub fn read_facts(language: Language, source: &str, module: &str, reading: &TagReading) -> ModuleFacts {
    match language {
        Language::Hy => hy_facts(source, module, reading),
        Language::Python => python_facts(source, module, reading),
    }
}

// --- Hy ---------------------------------------------------------------------------------

/// Hy の source を読んでタグ・import・関数の定義を取り出す。
fn hy_facts(source: &str, module: &str, reading: &TagReading) -> ModuleFacts {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = HySource { src: source };
    let mut facts = ModuleFacts {
        language: Language::Hy,
        module_tags: hy.module_tags(&forms, &reading.module_variable_hy),
        tagged: Vec::new(),
        untagged: Vec::new(),
        imports: Vec::new(),
        functions: Vec::new(),
        errors: if reader.issues.is_empty() { Vec::new() } else { vec![format!("括弧か文字列が閉じていない所が {} か所ある", reader.issues.len())] },
    };
    hy.definitions(&forms, reading, &mut facts);
    for form in &forms {
        hy.collect_imports(form, module, &mut facts.imports);
    }
    facts
}

/// Hy の source と、form の綴りを読む道具。
struct HySource<'a> {
    src: &'a str,
}

/// form の範囲を ByteSpan にする。
fn span_of(form: &Form) -> ByteSpan {
    ByteSpan { start: form.span.start, end: form.span.end }
}

/// 列の中身から読み捨て(`#_`)を除く — Hy の読み取りは読み捨てを列に残さない。
fn live_items(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// `( … )` の中身(読み捨てを除く)。
fn paren(form: &Form) -> Option<Vec<&Form>> {
    form.paren_items().map(live_items)
}

impl<'a> HySource<'a> {
    /// form の綴り。
    fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// 記号の綴り(記号でなければ None)。
    fn symbol(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol => Some(self.text(form)),
            _ => None,
        }
    }

    /// keyword の名(`:tags` → `tags`)。keyword でなければ None。
    fn keyword(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Keyword => Some(self.text(form).trim_start_matches(':')),
            _ => None,
        }
    }

    /// Hy の String の値(f 文字列と bytes は String ではないので None)。
    fn string_value(&self, form: &Form) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(str::to_string),
            _ => None,
        }
    }

    /// Hy の辞書 `{:context "…" :role "…"}` を読む(文字列の値の鍵が 1 つも無ければ None — 空の辞書は名乗っていない)。
    fn tags_of_dict(&self, form: &Form) -> Option<TagSet> {
        let items = match &form.node {
            Node::Seq { delim: Delim::Brace, items } => live_items(items),
            _ => return None,
        };
        let mut found = false;
        let (mut context, mut role) = (None, None);
        for pair in items.chunks(2) {
            if let [key, value] = pair {
                if let (Some(name), Some(text)) = (self.keyword(key), self.string_value(value)) {
                    found = true;
                    match name {
                        "context" => context = Some(text),
                        "role" => role = Some(text),
                        _ => {}
                    }
                }
            }
        }
        found.then(|| TagSet { context, role, span: span_of(form) })
    }

    /// module の最上位の `(setv|val MODULE-TAGS {…})` を読む(最初の 1 つ)。
    fn module_tags(&self, forms: &[Form], variable: &str) -> Option<TagSet> {
        forms.iter().find_map(|form| {
            let items = paren(form)?;
            match items.as_slice() {
                [head, name, value]
                    if matches!(self.symbol(head), Some("setv" | "val")) && self.symbol(name) == Some(variable) && value.is_brace() =>
                {
                    Some(self.tags_of_dict(value))
                }
                _ => None,
            }
        })?
    }

    /// 定義の式の名の form(`#^ T 名` は名の方)。
    fn defined_name(&self, form: &Form) -> NamedSpan {
        let target = match &form.node {
            Node::Annotated { target: Some(target), .. } => target.as_ref(),
            _ => form,
        };
        NamedSpan { name: hy_mangle(self.text(target)), span: span_of(target) }
    }

    /// 最上位の定義ごとのタグと、関数の定義を読む。
    fn definitions(&self, forms: &[Form], reading: &TagReading, facts: &mut ModuleFacts) {
        for form in forms {
            let Some(items) = paren(form) else { continue };
            if items.len() < 2 {
                continue;
            }
            let Some(head) = self.symbol(items[0]) else { continue };
            let name = self.defined_name(items[1]);
            if reading.function_definers.contains(head) {
                facts.functions.push(name.clone());
            }
            let found = if reading.contract_definers.contains(head) {
                Some(self.contract_tags(&items))
            } else if reading.plain_definers.contains(head) {
                Some(None)
            } else if reading.effect_definers.contains(head) {
                Some(self.keyword_tags(&items))
            } else {
                None
            };
            match found {
                Some(Some(tags)) => record_tagged(&mut facts.tagged, name, tags),
                Some(None) => facts.untagged.push(name),
                None => {}
            }
        }
    }

    /// 契約の辞書の :tags を読む — 名の後の引数の list と docstring を飛ばした最初の辞書(名から 4 つ目まで)。
    fn contract_tags(&self, items: &[&Form]) -> Option<TagSet> {
        let mut found = None;
        for part in items.iter().skip(2).take(4) {
            if part.is_brace() {
                let entries = match &part.node {
                    Node::Seq { items, .. } => live_items(items),
                    _ => Vec::new(),
                };
                // 鍵と値の組ではなく、隣り合う 2 つを全部の位置で見る(module_tags.hy の hy-definitions と同じ読み方)。
                for pair in entries.windows(2) {
                    if self.keyword(pair[0]) == Some("tags") && pair[1].is_brace() {
                        found = self.tags_of_dict(pair[1]);
                    }
                }
                break;
            }
            let skippable = self.string_value(part).is_some() || part.bracket_items().is_some();
            if !skippable {
                break;
            }
        }
        found
    }

    /// `(defeffect 名 … :tags {…} …)` の鍵と値の並びから :tags を読む。
    fn keyword_tags(&self, items: &[&Form]) -> Option<TagSet> {
        let mut found = None;
        for index in 2..items.len().saturating_sub(1) {
            if self.keyword(items[index]) == Some("tags") && items[index + 1].is_brace() {
                found = self.tags_of_dict(items[index + 1]);
            }
        }
        found
    }

    /// form の中の `(import …)` の式を全部読む(関数の中や入口の節の中の import も数える)。
    fn collect_imports(&self, form: &Form, module: &str, out: &mut Vec<ImportTarget>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, items } => {
                let live = live_items(items);
                if live.first().and_then(|head| self.symbol(head)) == Some("import") {
                    self.import_targets(&live[1..], module, out);
                } else {
                    for item in live {
                        self.collect_imports(item, module, out);
                    }
                }
            }
            Node::Seq { delim: Delim::Bracket, items } => {
                for item in live_items(items) {
                    self.collect_imports(item, module, out);
                }
            }
            Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => self.collect_imports(inner, module, out),
            Node::Annotated { annotation, target } => {
                for part in [annotation, target].into_iter().flatten() {
                    self.collect_imports(part, module, out);
                }
            }
            _ => {}
        }
    }

    /// import の式の引数を読む: `mod`・`mod :as m`・`mod [a b]`(名を並べた import は名ごと)。
    fn import_targets(&self, items: &[&Form], module: &str, out: &mut Vec<ImportTarget>) {
        let mut index = 0;
        while index < items.len() {
            let target = absolute_module(module, &hy_mangle(self.text(items[index])));
            match items.get(index + 1) {
                Some(next) if next.bracket_items().is_some() => {
                    let names = next.bracket_items().map(live_items).unwrap_or_default();
                    for name in names.into_iter().filter(|name| self.keyword(name).is_none()) {
                        out.push(ImportTarget { target: format!("{}.{}", target, hy_mangle(self.text(name))), span: span_of(name) });
                    }
                    index += 2;
                }
                Some(next) if self.text(next) == ":as" && matches!(next.node, Node::Keyword) => {
                    out.push(ImportTarget { target, span: span_of(items[index]) });
                    index += 3;
                }
                _ => {
                    out.push(ImportTarget { target, span: span_of(items[index]) });
                    index += 1;
                }
            }
        }
    }
}

/// 定義のタグを積む(同じ名は位置を保って値を上書きする — Python の dict の代入と同じ)。
fn record_tagged(tagged: &mut Vec<TaggedDefinition>, name: NamedSpan, tags: TagSet) {
    match tagged.iter_mut().find(|t| t.name.name == name.name) {
        Some(existing) => existing.tags = tags,
        None => tagged.push(TaggedDefinition { name, tags }),
    }
}

// --- Python -----------------------------------------------------------------------------

/// Python の source を読んでタグ・import・関数の定義を取り出す(構文の誤りは errors に積んで空の事実を返す)。
fn python_facts(source: &str, module: &str, reading: &TagReading) -> ModuleFacts {
    let mut facts = ModuleFacts {
        language: Language::Python,
        module_tags: None,
        tagged: Vec::new(),
        untagged: Vec::new(),
        imports: Vec::new(),
        functions: Vec::new(),
        errors: Vec::new(),
    };
    let body = match parse(source, Mode::Module, "<module>") {
        Ok(Mod::Module(module)) => module.body,
        Ok(_) => Vec::new(),
        Err(error) => {
            facts.errors.push(format!("Python の構文として読めない: {}", error));
            return facts;
        }
    };
    facts.module_tags = body.iter().find_map(|stmt| python_module_tags(stmt, &reading.module_variable_py));
    for stmt in &body {
        match stmt {
            Stmt::FunctionDef(def) => facts.functions.push(python_def_name(source, def.name.as_str(), usize::from(def.range.start()))),
            Stmt::AsyncFunctionDef(def) => facts.functions.push(python_def_name(source, def.name.as_str(), usize::from(def.range.start()))),
            _ => {}
        }
    }
    python_imports(&body, module, &mut facts.imports);
    facts
}

/// `MODULE_TAGS = {…}` の文ならタグを読む(鍵と値がどちらも定数の組だけ)。
fn python_module_tags(stmt: &Stmt, variable: &str) -> Option<TagSet> {
    let Stmt::Assign(assign) = stmt else { return None };
    let [Expr::Name(target)] = assign.targets.as_slice() else { return None };
    let Expr::Dict(dict) = assign.value.as_ref() else { return None };
    if target.id.as_str() != variable {
        return None;
    }
    let mut found = false;
    let (mut context, mut role) = (None, None);
    for (key, value) in dict.keys.iter().zip(dict.values.iter()) {
        if let (Some(Expr::Constant(key)), Expr::Constant(value)) = (key, value) {
            found = true;
            let text = python_str(&value.value);
            match python_str(&key.value).as_str() {
                "context" => context = Some(text),
                "role" => role = Some(text),
                _ => {}
            }
        }
    }
    found.then(|| ByteSpan { start: usize::from(dict.range.start()), end: usize::from(dict.range.end()) }).map(|span| TagSet { context, role, span })
}

/// 定数を Python の `str()` の綴りにする。
fn python_str(value: &Constant) -> String {
    match value {
        Constant::Str(text) => text.clone(),
        Constant::None => "None".to_string(),
        Constant::Bool(true) => "True".to_string(),
        Constant::Bool(false) => "False".to_string(),
        Constant::Int(number) => number.to_string(),
        Constant::Float(number) => number.to_string(),
        Constant::Bytes(bytes) => format!("b{:?}", String::from_utf8_lossy(bytes)),
        Constant::Ellipsis => "Ellipsis".to_string(),
        Constant::Tuple(_) | Constant::Complex { .. } => format!("{:?}", value),
    }
}

/// `def 名` の名の位置を探す(見つからなければ文の頭)。
fn python_def_name(source: &str, name: &str, stmt_start: usize) -> NamedSpan {
    let rest = source.get(stmt_start..).unwrap_or("");
    let at = rest
        .find("def ")
        .and_then(|def_at| rest[def_at..].find(name).map(|name_at| stmt_start + def_at + name_at))
        .unwrap_or(stmt_start);
    NamedSpan { name: name.to_string(), span: ByteSpan { start: at, end: at + name.len() } }
}

/// 文の列の中の import を全部読む(関数・class・if・try などの中も — `ast.walk` と同じ範囲)。
fn python_imports(body: &[Stmt], module: &str, out: &mut Vec<ImportTarget>) {
    for stmt in body {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    out.push(ImportTarget { target: alias.name.as_str().to_string(), span: text_span(alias.range) });
                }
            }
            Stmt::ImportFrom(import) => {
                let level = import.level.map(|l| l.to_usize()).unwrap_or(0);
                let spelled = format!("{}{}", ".".repeat(level), import.module.as_ref().map(|m| m.as_str()).unwrap_or(""));
                let base = absolute_module(module, &spelled);
                for alias in &import.names {
                    out.push(ImportTarget { target: format!("{}.{}", base, alias.name.as_str()), span: text_span(alias.range) });
                }
            }
            Stmt::FunctionDef(def) => python_imports(&def.body, module, out),
            Stmt::AsyncFunctionDef(def) => python_imports(&def.body, module, out),
            Stmt::ClassDef(def) => python_imports(&def.body, module, out),
            Stmt::For(node) => {
                python_imports(&node.body, module, out);
                python_imports(&node.orelse, module, out);
            }
            Stmt::AsyncFor(node) => {
                python_imports(&node.body, module, out);
                python_imports(&node.orelse, module, out);
            }
            Stmt::While(node) => {
                python_imports(&node.body, module, out);
                python_imports(&node.orelse, module, out);
            }
            Stmt::If(node) => {
                python_imports(&node.body, module, out);
                python_imports(&node.orelse, module, out);
            }
            Stmt::With(node) => python_imports(&node.body, module, out),
            Stmt::AsyncWith(node) => python_imports(&node.body, module, out),
            Stmt::Match(node) => {
                for case in &node.cases {
                    python_imports(&case.body, module, out);
                }
            }
            Stmt::Try(node) => {
                python_imports(&node.body, module, out);
                for handler in &node.handlers {
                    let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler;
                    python_imports(&h.body, module, out);
                }
                python_imports(&node.orelse, module, out);
                python_imports(&node.finalbody, module, out);
            }
            Stmt::TryStar(node) => {
                python_imports(&node.body, module, out);
                for handler in &node.handlers {
                    let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler;
                    python_imports(&h.body, module, out);
                }
                python_imports(&node.orelse, module, out);
                python_imports(&node.finalbody, module, out);
            }
            _ => {}
        }
    }
}

/// rustpython の範囲を ByteSpan にする。
fn text_span(range: rustpython_parser::text_size::TextRange) -> ByteSpan {
    ByteSpan { start: usize::from(range.start()), end: usize::from(range.end()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::settings::{LayersSection, ProjectSections, ProjectSettings};

    /// 既定のタグの読み方(設定の節が空の時の値)。
    fn reading() -> TagReading {
        let layers = LayersSection { order: vec!["core".into()], paths: [("core".to_string(), "c".to_string())].into(), ..Default::default() };
        let sections = ProjectSections {
            layers: Some(&layers),
            tags: None,
            roles: None,
            environment_names: None,
            raw_side_effects: None,
            laws: &[],
            registry: None,
        };
        ProjectSettings::validate(&sections).unwrap().layers.unwrap().tags
    }

    #[test]
    fn hy_tags_from_contract_dict_module_tags_and_defeffect() {
        let src = r#"
(defk plan [x] "計画" {:pre [] :tags {:context "billing" :role "program"}} x)
(defk #^ int typed [x] {:tags {}} x)
(defhandler h #_ ignored {:tags {:context "billing" :role "protocol"}} (E [e k] (k 1)))
(defeffect Charge :fields [amount] :tags {:context "billing" :role "intent"})
(defn helper [] 1)
(val MODULE-TAGS {:context "billing" :role "judgment"})
(defn [do] decorated [x] x)
"#;
        let facts = read_facts(Language::Hy, src, "app.core.m", &reading());
        let tagged: Vec<(&str, Option<&str>)> = facts.tagged.iter().map(|t| (t.name.name.as_str(), t.tags.role.as_deref())).collect();
        assert_eq!(tagged, vec![("plan", Some("program")), ("h", Some("protocol")), ("Charge", Some("intent"))]);
        // 空の :tags は名乗っていない。#^ の型注釈の後の名を読む。
        let untagged: Vec<&str> = facts.untagged.iter().map(|n| n.name.as_str()).collect();
        assert_eq!(untagged[..2], ["typed", "helper"]);
        assert_eq!(facts.module_tags.as_ref().and_then(|t| t.role.as_deref()), Some("judgment"));
        // module の頭のタグはタグの無い定義に効く。
        assert_eq!(facts.tag_sets().len(), 4);
        assert_eq!(facts.functions.len(), 5);
    }

    #[test]
    fn hy_imports_nested_relative_and_aliased() {
        let src = "(import app.intent.charge [Charge :as C])\n(import .sibling)\n(defn f [] (import subprocess) '(import quoted))\n(import os.path :as p)\n";
        let facts = read_facts(Language::Hy, src, "app.core.m", &reading());
        let targets: Vec<&str> = facts.imports.iter().map(|i| i.target.as_str()).collect();
        // `[a :as b]` の別名も綴りに数える(module_tags.hy の hy-imports と同じ)。
        assert_eq!(
            targets,
            vec!["app.intent.charge.Charge", "app.intent.charge.C", "app.core.sibling", "subprocess", "quoted", "os.path"]
        );
    }

    #[test]
    fn python_tags_imports_and_broken_source() {
        let src = "from ..intent import charge\nimport httpx\nMODULE_TAGS = {\"context\": \"billing\", \"role\": None}\nif True:\n    from x import y as z\nasync def f():\n    pass\n";
        let facts = read_facts(Language::Python, src, "app.core.m", &reading());
        let targets: Vec<&str> = facts.imports.iter().map(|i| i.target.as_str()).collect();
        assert_eq!(targets, vec!["app.intent.charge", "httpx", "x.y"]);
        assert_eq!(facts.module_tags.as_ref().and_then(|t| t.role.as_deref()), Some("None"));
        assert_eq!(facts.functions[0].name, "f");
        assert_eq!(&src[facts.functions[0].span.start..facts.functions[0].span.end], "f");
        let broken = read_facts(Language::Python, "def (:\n", "m", &reading());
        assert_eq!(broken.errors.len(), 1);
        let unclosed = read_facts(Language::Hy, "(defn f [x]\n", "m", &reading());
        assert_eq!(unclosed.errors.len(), 1);
        assert_eq!(unclosed.untagged.len(), 1);
    }
}

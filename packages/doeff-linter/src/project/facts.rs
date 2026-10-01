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
use super::layers::TagReading;

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
    /// 空でない文字列の値を持つ鍵の全部(必須の鍵の検査のため)。
    pub keys: std::collections::BTreeSet<String>,
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
    /// 定義の規則(defn の禁止・deff の理由・タグ必須)が読む定義(Hy だけ・`do` と compile の節の中も)。
    pub definitions: Vec<DefinitionFact>,
    /// defclass の形(DOEFF119 のため・Hy だけ・`do` と compile の節の中も)。
    pub classes: Vec<ClassFact>,
    /// import で束ねた名 → module の綴り(絶対)。`(import m [A :as B])` は B → m、`(import m :as n)` は n → m、`(import m)` は m → m。
    pub bindings: std::collections::BTreeMap<String, String>,
}

/// defclass 1 つの形(基底が外の library か・処理を持つ method があるかを判じるため)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassFact {
    pub name: NamedSpan,
    /// 書かれたとおりの基底の綴り(keyword とその値は除く)。
    pub bases: Vec<String>,
    /// decorator の頭の綴り(`(dataclass :frozen True)` は `dataclass`)。
    pub decorators: Vec<String>,
    pub methods: Vec<MethodFact>,
    /// 欄の宣言(`#^ T x` は `x: T`・注釈の無い `(setv x …)` は `x`)。Jev に渡す材料。
    pub fields: Vec<String>,
    /// defclass の form 全体の範囲(Jev に渡す source のため)。
    pub span: ByteSpan,
    /// `eval-and-compile` / `eval-when-compile` の中か。
    pub compile_time: bool,
}

/// class の body の method 1 つ(名と、本体に処理があるか)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MethodFact {
    pub name: String,
    /// docstring と `...`・`pass`・`None` を除いた本体の式が 1 つ以上あるか。
    pub has_body: bool,
    /// method が書き換える self の欄の名(`(setv self.x …)`・`(+= self.x …)`・`(.append self.x …)` など)。
    pub mutates: Vec<String>,
}

/// 定義の規則が読む Hy の定義 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DefinitionFact {
    /// 定義の頭(defn・defk・deff …)。
    pub head: String,
    pub name: NamedSpan,
    /// 定義の form の始まり(理由の註を探す行のため)。
    pub start: usize,
    /// 契約の辞書の :tags(defeffect は辞書の :tags)。
    pub tags: Option<TagSet>,
    /// `eval-and-compile` / `eval-when-compile` の中(マクロの展開の時の関数)か。
    pub compile_time: bool,
}

/// 定義の規則が見る定義の頭。
const DEFINITION_HEADS: &[&str] = &["defn", "defn/a", "defk", "deff", "defp", "defpp", "defhandler", "defeffect", "defrecord", "defwire"];

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

/// Hy の source の import の束縛(名 → module)だけを読む。
pub fn hy_bindings(source: &str, module: &str) -> std::collections::BTreeMap<String, String> {
    // repo の全部の file の呼びの頭を解く時(DOEFF126 の引数の追い)に、タグや定義を読まずに import の束縛だけを安く取るため。
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = HySource { src: source };
    let mut bindings = std::collections::BTreeMap::new();
    for form in &forms {
        hy.collect_bindings(form, module, &mut bindings);
    }
    bindings
}

/// source を言語ごとの読み方で読む。module は相対 import を解く基準の綴り。
pub fn read_facts(language: Language, source: &str, module: &str, reading: &TagReading) -> ModuleFacts {
    match language {
        Language::Hy => hy_facts(source, module, reading),
        Language::Python => python_facts(source, module, reading),
    }
}

/// module が依る先 1 つの種類 — `Import` は実行時の import、`Require` は Hy のマクロ(展開の時)の依存。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum DependencyKind {
    Import,
    Require,
}

/// module が依る先 1 つ(import の先は `import` の読みと同じ綴り — 名を並べた import は `module.名`)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub struct Dependency {
    pub target: String,
    pub kind: DependencyKind,
}

/// module 1 つの依存の読み(import と Hy の require)。読めなかった理由が在っても、読めた分の依存は残す。
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ModuleDependencies {
    pub dependencies: Vec<Dependency>,
    pub error: Option<String>,
}

/// source の import(層の規則と同じ読み — `collect_imports`・`python_imports`)と、Hy の `(require …)` の module だけを読む
/// (逆依存の索引のため — タグや定義は読まない)。module は相対 import を解く基準の綴り。
pub fn module_dependencies(language: Language, source: &str, module: &str) -> ModuleDependencies {
    let as_dependencies = |targets: Vec<String>, kind: DependencyKind| targets.into_iter().map(move |target| Dependency { target, kind });
    match language {
        Language::Hy => {
            let mut reader = Reader::new(source, 0, source.len());
            let forms = reader.read_all();
            let hy = HySource { src: source };
            let mut imports = Vec::new();
            let mut requires = Vec::new();
            for form in &forms {
                hy.collect_imports(form, module, &mut imports);
                hy.visit_headed(form, "require", &mut |args| hy.require_targets(args, module, &mut requires));
            }
            ModuleDependencies {
                dependencies: as_dependencies(imports.into_iter().map(|i| i.target).collect(), DependencyKind::Import)
                    .chain(as_dependencies(requires, DependencyKind::Require))
                    .collect(),
                error: (!reader.issues.is_empty()).then(|| format!("括弧か文字列が閉じていない所が {} か所ある", reader.issues.len())),
            }
        }
        Language::Python => match parse(source, Mode::Module, "<module>") {
            Ok(Mod::Module(parsed)) => {
                let mut imports = Vec::new();
                python_imports(&parsed.body, module, &mut imports);
                ModuleDependencies { dependencies: as_dependencies(imports.into_iter().map(|i| i.target).collect(), DependencyKind::Import).collect(), error: None }
            }
            Ok(_) => ModuleDependencies { dependencies: Vec::new(), error: None },
            Err(error) => ModuleDependencies { dependencies: Vec::new(), error: Some(format!("Python の構文として読めない: {}", error)) },
        },
    }
}

/// Hy の読んだ form から、import の束縛(名 → module の綴り)だけを取り出す(defk の見出しの型を定義へ結ぶため — 他の事実は読まない)。
pub fn form_bindings(forms: &[Form], source: &str, module: &str) -> std::collections::BTreeMap<String, String> {
    let hy = HySource { src: source };
    let mut out = std::collections::BTreeMap::new();
    for form in forms {
        hy.collect_bindings(form, module, &mut out);
    }
    out
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
        definitions: Vec::new(),
        classes: Vec::new(),
        bindings: std::collections::BTreeMap::new(),
        errors: if reader.issues.is_empty() { Vec::new() } else { vec![format!("括弧か文字列が閉じていない所が {} か所ある", reader.issues.len())] },
    };
    hy.definitions(&forms, reading, &mut facts);
    hy.definition_facts(&forms, false, reading, &mut facts.definitions);
    hy.class_facts(&forms.iter().collect::<Vec<_>>(), false, &mut facts.classes);
    for form in &forms {
        hy.collect_bindings(form, module, &mut facts.bindings);
    }
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
        let mut keys = std::collections::BTreeSet::new();
        for pair in items.chunks(2) {
            if let [key, value] = pair {
                if let (Some(name), Some(text)) = (self.keyword(key), self.string_value(value)) {
                    found = true;
                    if !text.is_empty() {
                        keys.insert(name.to_string());
                    }
                    match name {
                        "context" => context = Some(text),
                        "role" => role = Some(text),
                        _ => {}
                    }
                }
            }
        }
        found.then(|| TagSet { context, role, keys, span: span_of(form) })
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

    /// 定義の規則が読む定義を集める(`do` と `eval-and-compile` / `eval-when-compile` の中も最上位として見る)。
    fn definition_facts(&self, forms: &[Form], compile_time: bool, reading: &TagReading, out: &mut Vec<DefinitionFact>) {
        for form in forms {
            let Some(items) = paren(form) else { continue };
            let Some(head) = items.first().and_then(|h| self.symbol(h)) else { continue };
            match head {
                "do" => self.definition_facts_refs(&items[1..], compile_time, reading, out),
                "eval-and-compile" | "eval-when-compile" => self.definition_facts_refs(&items[1..], true, reading, out),
                _ if DEFINITION_HEADS.contains(&head) && items.len() >= 2 => {
                    // `(defn [decorators] name …)` は decorator の list を飛ばして名を読む。
                    let name_form = match items.get(1) {
                        Some(first) if first.bracket_items().is_some() && items.len() >= 3 => items[2],
                        Some(first) => *first,
                        None => continue,
                    };
                    let tags = if head == "defeffect" || reading.record_definers.contains(head) {
                        self.header_tags(&items)
                    } else if reading.contract_definers.contains(head) {
                        self.contract_tags(&items)
                    } else {
                        None
                    };
                    out.push(DefinitionFact {
                        head: head.to_string(),
                        name: self.defined_name(name_form),
                        start: form.span.start,
                        tags,
                        compile_time,
                    });
                }
                // `(setv 名 (fn …))` / `(val 名 (fn …))` は head = "fn" の定義として数える(検の関数の規則 DOEFF118 のため)。
                "setv" | "val" if items.len() == 3 => {
                    let is_fn = paren(items[2]).and_then(|inner| inner.first().and_then(|h| self.symbol(h))).is_some_and(|h| matches!(h, "fn" | "fn/a"));
                    if is_fn && self.symbol(items[1]).is_some() {
                        out.push(DefinitionFact {
                            head: "fn".to_string(),
                            name: self.defined_name(items[1]),
                            start: form.span.start,
                            tags: None,
                            compile_time,
                        });
                    }
                }
                _ => {}
            }
        }
    }

    /// defclass の形を集める(`do` と `eval-and-compile` / `eval-when-compile` の中も最上位として見る)。
    fn class_facts(&self, forms: &[&Form], compile_time: bool, out: &mut Vec<ClassFact>) {
        for form in forms {
            let Some(items) = paren(form) else { continue };
            match items.first().and_then(|h| self.symbol(h)) {
                Some("do") => self.class_facts(&items[1..], compile_time, out),
                Some("eval-and-compile" | "eval-when-compile") => self.class_facts(&items[1..], true, out),
                Some("defclass") => {
                    if let Some(fact) = self.class_fact(&items[1..], span_of(form), compile_time) {
                        out.push(fact);
                    }
                }
                _ => {}
            }
        }
    }

    /// `(defclass [decorators]? Name [bases]? "doc"? body…)` の頭を除いた列を読む(名が無ければ None)。
    fn class_fact(&self, rest: &[&Form], span: ByteSpan, compile_time: bool) -> Option<ClassFact> {
        let (decorators, rest) = match rest.first() {
            Some(first) if first.bracket_items().is_some() && rest.len() >= 2 => (self.decorator_heads(first), &rest[1..]),
            _ => (Vec::new(), rest),
        };
        let name = self.defined_name(rest.first()?);
        let (bases, body) = match rest.get(1).and_then(|f| f.bracket_items()) {
            Some(bases) => (self.base_names(&live_items(bases)), rest.get(2..).unwrap_or_default()),
            None => (Vec::new(), rest.get(1..).unwrap_or_default()),
        };
        let methods = body.iter().filter_map(|member| self.method_fact(member)).collect();
        // 欄の読み方は defrecord / defwire の正本(doeff-indexer の hy_index::fields — Hy 側は doeff_hy.declarations/field-targets)。
        let fields = doeff_indexer::hy_index::fields::record_field_targets(self.src, body)
            .into_iter()
            .map(|target| {
                let name = self.src.get(target.name.start..target.name.end).unwrap_or("");
                match target.annotation.and_then(|a| self.src.get(a.start..a.end)) {
                    Some(annotation) => format!("{}: {}", name, annotation),
                    None => name.to_string(),
                }
            })
            .collect();
        Some(ClassFact { name, bases, decorators, methods, fields, span, compile_time })
    }

    /// decorator の列の頭の綴り(記号はそのまま・`(f …)` は f)。
    fn decorator_heads(&self, list: &Form) -> Vec<String> {
        let items = list.bracket_items().map(live_items).unwrap_or_default();
        items
            .into_iter()
            .filter_map(|item| match paren(item) {
                Some(inner) => inner.first().and_then(|h| self.symbol(h)).map(str::to_string),
                None => self.symbol(item).map(str::to_string),
            })
            .collect()
    }

    /// 基底の列の記号(keyword とその値は除く)。
    fn base_names(&self, items: &[&Form]) -> Vec<String> {
        let mut names = Vec::new();
        let mut index = 0;
        while index < items.len() {
            match items[index].node {
                Node::Keyword => index += 2,
                _ => {
                    if let Some(symbol) = self.symbol(items[index]) {
                        names.push(symbol.to_string());
                    }
                    index += 1;
                }
            }
        }
        names
    }

    /// class の body の 1 つが method(`(defn [decorators]? name [params] "doc"? body…)` など)なら、名と本体の有無を読む。
    fn method_fact(&self, member: &Form) -> Option<MethodFact> {
        let items = paren(member)?;
        let head = items.first().and_then(|h| self.symbol(h))?;
        if !matches!(head, "defn" | "defn/a" | "defk" | "deff" | "defp") {
            return None;
        }
        let rest = &items[1..];
        let rest = match rest.first() {
            Some(first) if first.bracket_items().is_some() && rest.len() >= 3 => &rest[1..],
            _ => rest,
        };
        let name = self.defined_name(rest.first()?).name;
        let body: Vec<&&Form> = rest.iter().skip(2).collect();
        let body: Vec<&&Form> = match body.first() {
            Some(first) if self.string_value(first).is_some() && body.len() >= 2 => body[1..].to_vec(),
            Some(first) if self.string_value(first).is_some() => Vec::new(),
            _ => body,
        };
        let has_body = body.iter().any(|form| {
            // 契約の辞書({:pre …})と、何もしない式(`...`・pass・None)は処理に数えない。
            !form.is_brace() && !matches!(self.symbol(form), Some("..." | "pass" | "None"))
        });
        let mut mutates = Vec::new();
        for form in &body {
            self.self_mutations(form, &mut mutates);
        }
        Some(MethodFact { name, has_body, mutates })
    }

    /// 式の中で self の欄を書き換える所を探し、欄の名を積む(状態を持つ class を見分けるため)。
    fn self_mutations(&self, form: &Form, out: &mut Vec<String>) {
        /// 呼ぶと中身を書き換える method の名(list・dict・set・deque)。
        const MUTATORS: &[&str] = &["append", "extend", "insert", "pop", "popleft", "appendleft", "remove", "clear", "update", "add", "discard", "setdefault", "sort", "reverse"];
        let push = |out: &mut Vec<String>, field: &str| {
            let field = field.split('.').next().unwrap_or(field).to_string();
            if !field.is_empty() && !out.contains(&field) {
                out.push(field);
            }
        };
        // `self.x`・`self.x.y` の欄の名(self 以外なら None)。
        let self_field = |target: &Form| -> Option<String> {
            match paren(target) {
                // `(get self.x k)`・`(. self x)` も self の欄への書き込み。
                Some(inner) => match inner.first().and_then(|h| self.symbol(h)) {
                    Some("get") => inner.get(1).and_then(|t| self.symbol(t)).and_then(|t| t.strip_prefix("self.")).map(str::to_string),
                    Some(".") if inner.get(1).and_then(|t| self.symbol(t)) == Some("self") => inner.get(2).and_then(|t| self.symbol(t)).map(str::to_string),
                    _ => None,
                },
                None => self.symbol(target).and_then(|t| t.strip_prefix("self.")).map(str::to_string),
            }
        };
        let Some(items) = paren(form) else {
            if let Node::Seq { items: inner, .. } = &form.node {
                for item in live_items(inner) {
                    self.self_mutations(item, out);
                }
            }
            return;
        };
        match items.first().and_then(|h| self.symbol(h)) {
            Some("setv" | "setx") => {
                for pair in items[1..].chunks(2) {
                    if let Some(field) = self_field(pair[0]) {
                        push(out, &field);
                    }
                }
            }
            Some("+=" | "-=" | "*=" | "/=" | "//=" | "%=" | "**=" | "|=" | "&=" | "^=" | "<<=" | ">>=" | "@=" | "del") => {
                for target in &items[1..] {
                    if let Some(field) = self_field(target) {
                        push(out, &field);
                    }
                }
            }
            Some("setattr") if items.get(1).and_then(|t| self.symbol(t)) == Some("self") => push(out, "setattr"),
            Some(head) if head.starts_with('.') && MUTATORS.contains(&&head[1..]) => {
                if let Some(field) = items.get(1).and_then(|t| self_field(t)) {
                    push(out, &field);
                }
            }
            Some(head) if head.starts_with("self.") && head.rsplit('.').next().is_some_and(|m| MUTATORS.contains(&m)) && head.matches('.').count() >= 2 => {
                push(out, &head["self.".len()..]);
            }
            _ => {}
        }
        for item in &items[1..] {
            self.self_mutations(item, out);
        }
    }

    /// import の束縛(名 → module)を集める。`import_targets` と同じ形を読み、`:as` の別名も控える。
    fn collect_bindings(&self, form: &Form, module: &str, out: &mut std::collections::BTreeMap<String, String>) {
        let Some(items) = paren(form) else { return };
        match items.first().and_then(|h| self.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                for item in &items[1..] {
                    self.collect_bindings(item, module, out);
                }
            }
            Some("import") => {
                let args = &items[1..];
                let mut index = 0;
                while index < args.len() {
                    let spelled = self.text(args[index]).to_string();
                    let target = absolute_module(module, &hy_mangle(&spelled));
                    match args.get(index + 1) {
                        Some(next) if next.bracket_items().is_some() => {
                            let names = next.bracket_items().map(live_items).unwrap_or_default();
                            let mut at = 0;
                            while at < names.len() {
                                let name = self.text(names[at]).to_string();
                                match (names.get(at + 1), names.get(at + 2)) {
                                    (Some(kw), Some(alias)) if self.keyword(kw) == Some("as") => {
                                        out.insert(self.text(alias).to_string(), target.clone());
                                        at += 3;
                                    }
                                    _ => {
                                        if self.keyword(names[at]).is_none() {
                                            out.insert(name, target.clone());
                                        }
                                        at += 1;
                                    }
                                }
                            }
                            index += 2;
                        }
                        Some(next) if self.keyword(next) == Some("as") => {
                            if let Some(alias) = args.get(index + 2) {
                                out.insert(self.text(alias).to_string(), target.clone());
                            }
                            index += 3;
                        }
                        _ => {
                            out.insert(spelled, target);
                            index += 1;
                        }
                    }
                }
            }
            _ => {}
        }
    }

    /// `definition_facts` の、form の参照の列を受ける形。
    fn definition_facts_refs(&self, forms: &[&Form], compile_time: bool, reading: &TagReading, out: &mut Vec<DefinitionFact>) {
        for form in forms {
            self.definition_facts(std::slice::from_ref(*form), compile_time, reading, out);
        }
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
            } else if reading.effect_definers.contains(head) || reading.record_definers.contains(head) {
                Some(self.header_tags(&items))
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

    /// 辞書の form の :tags の値を、鍵と値の組で読む(同じ鍵が 2 度あれば後の物)。
    fn dict_tags(&self, dict: &Form) -> Option<TagSet> {
        let entries = match &dict.node {
            Node::Seq { delim: Delim::Brace, items } => live_items(items),
            _ => return None,
        };
        let mut found = None;
        for pair in entries.chunks(2) {
            if let [key, value] = pair {
                if self.keyword(key) == Some("tags") && value.is_brace() {
                    found = self.tags_of_dict(value);
                }
            }
        }
        found
    }

    /// 名の後の要素を順に見て、飛ばせる物(skippable)の後の最初の辞書の :tags を読む(limit = 名の後に見る数)。
    /// 辞書より前に飛ばせない物が来たら、タグは無い。
    fn first_dict_tags(&self, items: &[&Form], limit: usize, skippable: impl Fn(&Form) -> bool) -> Option<TagSet> {
        for part in items.iter().skip(2).take(limit) {
            if part.is_brace() {
                return self.dict_tags(part);
            }
            if !skippable(part) {
                return None;
            }
        }
        None
    }

    /// 契約の辞書の :tags を読む — 名の後の引数の list と docstring を飛ばした最初の辞書(名から 4 つ目まで)。
    fn contract_tags(&self, items: &[&Form]) -> Option<TagSet> {
        self.first_dict_tags(items, 4, |part| self.string_value(part).is_some() || part.bracket_items().is_some())
    }

    /// 名の後の頭の辞書の :tags を読む(名から 2 つ目まで・docstring だけ飛ばす)— `(defeffect 名 "doc"? {:fields […] :answer 型 :tags {…}})`
    /// と `(defrecord 名 "doc"? {:tags {…} :check […]} 欄 …)`(頭の辞書の無い defrecord は欄が来るのでタグ無し)。
    fn header_tags(&self, items: &[&Form]) -> Option<TagSet> {
        self.first_dict_tags(items, 2, |part| self.string_value(part).is_some())
    }

    /// form の中の `(import …)` の式を全部読む(関数の中や入口の節の中の import も数える)。
    fn collect_imports(&self, form: &Form, module: &str, out: &mut Vec<ImportTarget>) {
        self.visit_headed(form, "import", &mut |args| self.import_targets(args, module, out));
    }

    /// form の木の中の、頭が `head` の式を全部訪ねて引数の列を渡す(関数の中・入口の節の中・quote の中も — import の読みの範囲)。
    /// 訪ねた式の中へは降りない。
    fn visit_headed(&self, form: &Form, head: &str, visit: &mut dyn FnMut(&[&Form])) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, items } => {
                let live = live_items(items);
                if live.first().and_then(|first| self.symbol(first)) == Some(head) {
                    visit(&live[1..]);
                } else {
                    for item in live {
                        self.visit_headed(item, head, visit);
                    }
                }
            }
            Node::Seq { delim: Delim::Bracket, items } => {
                for item in live_items(items) {
                    self.visit_headed(item, head, visit);
                }
            }
            Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => self.visit_headed(inner, head, visit),
            Node::Annotated { annotation, target } => {
                for part in [annotation, target].into_iter().flatten() {
                    self.visit_headed(part, head, visit);
                }
            }
            _ => {}
        }
    }

    /// `(require …)` の式の引数から module の綴りを読む: `mod [a b]`・`mod *`・`mod :as m`・`mod :macros [..] :readers [..]`。
    /// 名の列・`*`・keyword とその値は module ではない。
    fn require_targets(&self, items: &[&Form], module: &str, out: &mut Vec<String>) {
        let mut after_keyword = false;
        for item in items {
            match (self.symbol(item), after_keyword) {
                (Some(spelled), false) if spelled != "*" => out.push(absolute_module(module, &hy_mangle(spelled))),
                _ => {}
            }
            after_keyword = matches!(item.node, Node::Keyword);
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
        definitions: Vec::new(),
        classes: Vec::new(),
        bindings: std::collections::BTreeMap::new(),
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
    let mut keys = std::collections::BTreeSet::new();
    for (key, value) in dict.keys.iter().zip(dict.values.iter()) {
        if let (Some(Expr::Constant(key)), Expr::Constant(value)) = (key, value) {
            found = true;
            let text = python_str(&value.value);
            if !text.is_empty() {
                keys.insert(python_str(&key.value));
            }
            match python_str(&key.value).as_str() {
                "context" => context = Some(text),
                "role" => role = Some(text),
                _ => {}
            }
        }
    }
    found.then(|| ByteSpan { start: usize::from(dict.range.start()), end: usize::from(dict.range.end()) }).map(|span| TagSet { context, role, keys, span })
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
        .and_then(|def_at| {
            let after = def_at + "def ".len();
            rest[after..].find(name).map(|name_at| stmt_start + after + name_at)
        })
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

// --- 名の使用(DOEFF120 — JsonValue の使い場所)---------------------------------------------

/// source の中の、名 names の使用の位置を全部集める(位置の順)。
///
/// - Hy: 読み取り器の記号で、最後の `.` の段が名の物(import・`(setv 名 …)`・`#^ 名 x`・`(: x 名)`・`#(str 名)` …)。
///   文字列・註・docstring・`#_` で読み捨てた form は数えない。位置は記号の最後の段。
/// - Python: 字句の名(名前・属性の名・import の名)と、注釈(引数・戻り値・`x: T`)と型の別名(`X: TypeAlias = "…"`・`type X = …`)の
///   文字列の中の語(前後が識別子の文字でない物)。docstring と註は数えない。
pub fn name_occurrences(language: Language, source: &str, names: &[&str]) -> Vec<ByteSpan> {
    let mut found = Vec::new();
    match language {
        Language::Hy => {
            let mut reader = Reader::new(source, 0, source.len());
            for form in &reader.read_all() {
                hy_name_occurrences(source, form, names, &mut found);
            }
        }
        Language::Python => {
            // 字句が読めなくなった所で止める(読めた所までの名は数える)。
            found.extend(rustpython_parser::lexer::lex(source, Mode::Module).map_while(Result::ok).filter_map(|(token, range)| match token {
                rustpython_parser::Tok::Name { name } if names.contains(&name.as_str()) => Some(text_span(range)),
                _ => None,
            }));
            if let Ok(Mod::Module(module)) = parse(source, Mode::Module, "<module>") {
                python_type_strings(source, &module.body, names, &mut found);
            }
        }
    }
    found.sort();
    found
}

/// Hy の form の木から、最後の段が名の記号を集める。
fn hy_name_occurrences(source: &str, form: &Form, names: &[&str], out: &mut Vec<ByteSpan>) {
    match &form.node {
        Node::Symbol => {
            let text = source.get(form.span.start..form.span.end).unwrap_or("");
            let last = text.rsplit('.').next().unwrap_or(text);
            if names.contains(&last) {
                out.push(ByteSpan { start: form.span.end - last.len(), end: form.span.end });
            }
        }
        Node::Seq { items, .. } => {
            for item in items {
                hy_name_occurrences(source, item, names, out);
            }
        }
        Node::Prefixed { inner, .. } | Node::Tagged { inner } => {
            if let Some(inner) = inner {
                hy_name_occurrences(source, inner, names, out);
            }
        }
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                hy_name_occurrences(source, part, names, out);
            }
        }
        Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// Python の文の列から、注釈と型の別名の式の中の文字列を探し、その中の名の語を集める(関数・class・if・try などの中も)。
fn python_type_strings(source: &str, body: &[Stmt], names: &[&str], out: &mut Vec<ByteSpan>) {
    let in_expr = |expr: &Expr, out: &mut Vec<ByteSpan>| python_expr_strings(source, expr, names, out);
    for stmt in body {
        match stmt {
            Stmt::AnnAssign(node) => {
                in_expr(&node.annotation, out);
                // `X: TypeAlias = "…"` の値は型の式(文字列で前方参照を書く)。
                let alias = match node.annotation.as_ref() {
                    Expr::Name(name) => name.id.as_str() == "TypeAlias",
                    Expr::Attribute(attribute) => attribute.attr.as_str() == "TypeAlias",
                    _ => false,
                };
                if let (true, Some(value)) = (alias, &node.value) {
                    in_expr(value, out);
                }
            }
            Stmt::TypeAlias(node) => in_expr(&node.value, out),
            Stmt::FunctionDef(def) => {
                python_arguments_strings(source, &def.args, names, out);
                if let Some(returns) = &def.returns {
                    in_expr(returns, out);
                }
                python_type_strings(source, &def.body, names, out);
            }
            Stmt::AsyncFunctionDef(def) => {
                python_arguments_strings(source, &def.args, names, out);
                if let Some(returns) = &def.returns {
                    in_expr(returns, out);
                }
                python_type_strings(source, &def.body, names, out);
            }
            Stmt::ClassDef(def) => python_type_strings(source, &def.body, names, out),
            Stmt::For(node) => {
                python_type_strings(source, &node.body, names, out);
                python_type_strings(source, &node.orelse, names, out);
            }
            Stmt::AsyncFor(node) => {
                python_type_strings(source, &node.body, names, out);
                python_type_strings(source, &node.orelse, names, out);
            }
            Stmt::While(node) => {
                python_type_strings(source, &node.body, names, out);
                python_type_strings(source, &node.orelse, names, out);
            }
            Stmt::If(node) => {
                python_type_strings(source, &node.body, names, out);
                python_type_strings(source, &node.orelse, names, out);
            }
            Stmt::With(node) => python_type_strings(source, &node.body, names, out),
            Stmt::AsyncWith(node) => python_type_strings(source, &node.body, names, out),
            Stmt::Match(node) => {
                for case in &node.cases {
                    python_type_strings(source, &case.body, names, out);
                }
            }
            Stmt::Try(node) => {
                python_type_strings(source, &node.body, names, out);
                for handler in &node.handlers {
                    let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler;
                    python_type_strings(source, &h.body, names, out);
                }
                python_type_strings(source, &node.orelse, names, out);
                python_type_strings(source, &node.finalbody, names, out);
            }
            Stmt::TryStar(node) => {
                python_type_strings(source, &node.body, names, out);
                for handler in &node.handlers {
                    let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler;
                    python_type_strings(source, &h.body, names, out);
                }
                python_type_strings(source, &node.orelse, names, out);
                python_type_strings(source, &node.finalbody, names, out);
            }
            _ => {}
        }
    }
}

/// 関数の引数の注釈の中の文字列の語を集める。
fn python_arguments_strings(source: &str, args: &rustpython_ast::Arguments, names: &[&str], out: &mut Vec<ByteSpan>) {
    let with_default = args.posonlyargs.iter().chain(args.args.iter()).chain(args.kwonlyargs.iter()).map(|a| &a.def);
    let bare = args.vararg.iter().chain(args.kwarg.iter()).map(|a| a.as_ref());
    for arg in with_default.chain(bare) {
        if let Some(annotation) = &arg.annotation {
            python_expr_strings(source, annotation, names, out);
        }
    }
}

/// 型の式の中の文字列(`"JsonValue"`・`dict[str, "JsonValue"]`・`"A | B"`)を探し、その literal の中の名の語の位置を集める。
fn python_expr_strings(source: &str, expr: &Expr, names: &[&str], out: &mut Vec<ByteSpan>) {
    let recurse = |inner: &Expr, out: &mut Vec<ByteSpan>| python_expr_strings(source, inner, names, out);
    match expr {
        Expr::Constant(constant) if matches!(constant.value, Constant::Str(_)) => {
            let span = text_span(constant.range);
            out.extend(word_occurrences(source.get(span.start..span.end).unwrap_or(""), names).into_iter().map(|at| ByteSpan { start: span.start + at.start, end: span.start + at.end }));
        }
        Expr::Subscript(node) => {
            recurse(&node.value, out);
            recurse(&node.slice, out);
        }
        Expr::BinOp(node) => {
            recurse(&node.left, out);
            recurse(&node.right, out);
        }
        Expr::Tuple(node) => node.elts.iter().for_each(|e| recurse(e, out)),
        Expr::List(node) => node.elts.iter().for_each(|e| recurse(e, out)),
        Expr::Attribute(node) => recurse(&node.value, out),
        Expr::Starred(node) => recurse(&node.value, out),
        Expr::Call(node) => {
            recurse(&node.func, out);
            node.args.iter().for_each(|e| recurse(e, out));
            node.keywords.iter().for_each(|k| recurse(&k.value, out));
        }
        _ => {}
    }
}

/// text の中の、名 names の語(前後が識別子の文字でない物)の位置(text の中の byte の範囲)。
fn word_occurrences(text: &str, names: &[&str]) -> Vec<ByteSpan> {
    let identifier = |c: char| c == '_' || c.is_alphanumeric();
    let mut found = Vec::new();
    for name in names {
        for (at, _) in text.match_indices(name) {
            let before = text[..at].chars().next_back();
            let after = text[at + name.len()..].chars().next();
            if !before.is_some_and(identifier) && !after.is_some_and(identifier) {
                found.push(ByteSpan { start: at, end: at + name.len() });
            }
        }
    }
    found
}

/// rustpython の範囲を ByteSpan にする。
fn text_span(range: rustpython_parser::text_size::TextRange) -> ByteSpan {
    ByteSpan { start: usize::from(range.start()), end: usize::from(range.end()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::layers::tag_reading;

    /// 既定のタグの読み方(設定の節が空の時の値)。
    fn reading() -> TagReading {
        tag_reading(None, None)
    }

    #[test]
    fn hy_tags_from_contract_dict_module_tags_and_defeffect() {
        let src = r#"
(defk plan [x] "計画" {:pre [] :tags {:context "billing" :role "program"}} x)
(defk #^ int typed [x] {:tags {}} x)
(defhandler h #_ ignored {:tags {:context "billing" :role "protocol"}} (E [e k] (k 1)))
(defeffect Charge "請求" {:fields [amount] :answer int :tags {:context "billing" :role "intent"}})
(defeffect Old :fields [amount] :tags {:context "billing" :role "intent"})
(defrecord Row "行" {:tags {:context "billing" :role "type"} :check [(> n 0)]} #^ int n)
(defrecord Bare #^ str key)
(defn helper [] 1)
(val MODULE-TAGS {:context "billing" :role "judgment"})
(defn [do] decorated [x] x)
"#;
        let facts = read_facts(Language::Hy, src, "app.core.m", &reading());
        let tagged: Vec<(&str, Option<&str>)> = facts.tagged.iter().map(|t| (t.name.name.as_str(), t.tags.role.as_deref())).collect();
        assert_eq!(tagged, vec![("plan", Some("program")), ("h", Some("protocol")), ("Charge", Some("intent")), ("Row", Some("type"))]);
        // 空の :tags は名乗っていない。#^ の型注釈の後の名を読む。
        let untagged: Vec<&str> = facts.untagged.iter().map(|n| n.name.as_str()).collect();
        // doeff-hy が受けない鍵と値の並びの形(Old)はタグとして読まない(module_tags.hy と同じ)。
        // 頭の辞書の無い defrecord はタグを書いていない定義。
        assert_eq!(untagged[..4], ["typed", "Old", "Bare", "helper"]);
        assert_eq!(facts.module_tags.as_ref().and_then(|t| t.role.as_deref()), Some("judgment"));
        // module の頭のタグはタグの無い定義に効く。
        assert_eq!(facts.tag_sets().len(), 5);
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
        // 名前 f は `def` の中の文字ではなく、名の位置を指す。
        assert_eq!(facts.functions[0].span.start, src.find("def f").unwrap() + 4);
        let broken = read_facts(Language::Python, "def (:\n", "m", &reading());
        assert_eq!(broken.errors.len(), 1);
        let unclosed = read_facts(Language::Hy, "(defn f [x]\n", "m", &reading());
        assert_eq!(unclosed.errors.len(), 1);
        assert_eq!(unclosed.untagged.len(), 1);
    }

    #[test]
    fn name_occurrences_count_symbols_and_type_strings_but_not_prose() {
        let names = ["JsonValue", "JsonObject"];
        let text = |source: &str, spans: Vec<ByteSpan>| spans.into_iter().map(|s| source[s.start..s.end].to_string()).collect::<Vec<_>>();
        // Hy: import・注釈・点つきの名の最後の段・quote の中は数え、文字列・註・#_・別の名(JsonValues)は数えない。
        let hy = "(import m [JsonValue])\n; JsonValue\n(setv x #^ wire.JsonObject y)\n\"JsonValue\"\n#_ JsonValue\n(setv JsonValues 1 q '(JsonValue))\n";
        assert_eq!(text(hy, name_occurrences(Language::Hy, hy, &names)), vec!["JsonValue", "JsonObject", "JsonValue"]);
        // Python: 名・属性・import の名と、注釈と型の別名の文字列の中の語。docstring・註・f 文字列・語の一部は数えない。
        let py = "\"\"\"JsonValue\"\"\"\nfrom m import JsonValue\n# JsonValue\ndef f(a: \"MyJsonValue\", b: \"list[JsonObject]\") -> \"JsonValue\":\n    return f\"{a} JsonValue\"\nx = m.JsonObject\n";
        assert_eq!(text(py, name_occurrences(Language::Python, py, &names)), vec!["JsonValue", "JsonObject", "JsonValue", "JsonObject"]);
    }

    #[test]
    fn class_fields_for_doeff204_match_the_shared_case_table() {
        // DOEFF204 に渡す class の欄の一覧は、defrecord / defwire と同じ読み手(doeff-indexer の hy_index::fields)で読む。
        // 同じ表を Hy 側の正本(doeff_hy.declarations/field-targets)の検も読む。
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../doeff-hy/tests/data/record_field_cases.json");
        let table: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        for case in table["cases"].as_array().unwrap() {
            let forms = case["forms"].as_str().unwrap();
            let names: Vec<&str> = case["names"].as_array().unwrap().iter().map(|n| n.as_str().unwrap()).collect();
            let facts = read_facts(Language::Hy, &format!("(defclass R []\n{})\n", forms), "m", &reading());
            let got: Vec<String> = facts.classes[0].fields.iter().map(|f| f.split(':').next().unwrap_or("").to_string()).collect();
            assert_eq!(got, names, "{:?}", forms);
        }
    }
}

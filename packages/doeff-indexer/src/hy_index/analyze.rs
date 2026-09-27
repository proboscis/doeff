//! Hy の form の木から、定義・import・参照を取り出す。
//!
//! 出自: 予約語の表(`HY_KEYWORDS`・`DOEFF_KEYWORDS`・`DOEFF_REQUIRED_KEYWORDS`・`CONSTANTS`)・
//! `mangle`・`is_operator`・require した名前の集め方・形ごとの分岐(defn の decorator の判定、
//! defclass の field、defhandler の docstring / params / effect 節、import の解釈、f 文字列の `{…}`)は
//! `vscode-semantic-highlighting/rust-highlighter/src/hy.rs` の `HyAnalyzer` から写した。
//! 写し元は色を付けるための分類を出す。ここは索引の契約(定義の名前と form 全体の範囲・
//! container・docstring・params、import、参照)を出す。

use std::collections::HashSet;

use super::model::{Definition, DefinitionKind, Import, Reference};
use super::position::LineIndex;
use super::reader::{matching_brace, Form, Node, Prefix, ReadIssue, Reader, Span, StrKind};

/// Hy の special form と core の macro — 先頭に置かれた時は予約語(参照に入れない)。
const HY_KEYWORDS: &[&str] = &[
    "if", "when", "unless", "cond", "do", "while", "break", "continue", "return", "yield",
    "yield-from", "await", "and", "or", "not", "raise", "assert", "del", "global", "nonlocal",
    "quote", "quasiquote", "unquote", "unquote-splice", "eval-and-compile", "eval-when-compile",
    "py", "pys", "chainc", "annotate", "cut", "try", "except", "except*", "else", "finally", "in",
    "not-in", "is", "is-not", "setv", "setx", "let", "fn", "fn/a", "defn", "defn/a", "defclass",
    "defmacro", "defreader", "deftype", "for", "for/a", "lfor", "sfor", "gfor", "dfor", "with",
    "with/a", "import", "require", "match", "pragma", "export", "local-macros", "get-macro",
    "defmain", "->", "->>", "as->", "doto", "lif", "branch", "ecase", "case", "ncut", ".",
];

/// doeff-hy の form のうち、どこに現れても予約語のもの(定義と束縛の構文)。
const DOEFF_KEYWORDS: &[&str] = &[
    "defk", "deff", "defp", "defpp", "fnk", "do!", "<-", "<->", "for/do", "deftest", "defpipeline",
    "defmcp-tool", "set!", "defhandler", "resume", "with-handler", "defrecord", "defenum",
    "defworkflow", "defphase", "defadr", "defsemgrep", "law", "lazy-val", "lazy-var",
];

/// 名前が普通の語の doeff-hy の macro — file が require した時だけ予約語。
const DOEFF_REQUIRED_KEYWORDS: &[&str] = &[
    "val", "var", "lazy", "session", "handle", "traverse", "validate", "check", "parallel",
    "parallel-for", "loop", "time!", "random!", "pipeline", "agent!", "gate!", "workspace!",
    "merge!",
];

/// 定数(参照に入れない)。
const CONSTANTS: &[&str] = &["True", "False", "None", "...", "Ellipsis", "NotImplemented", "Inf", "NaN"];

/// Hy の名前を Python の名前へ直す(契約の mangle: `-` を `_` に、先頭の `-` は残す)。
pub fn mangle(name: &str) -> String {
    let has_word = name.chars().any(|c| c.is_alphanumeric() || c == '_');
    if has_word && name.contains('-') {
        let trimmed = name.trim_start_matches('-');
        let leading = &name[..name.len() - trimmed.len()];
        format!("{}{}", leading, trimmed.replace('-', "_"))
    } else {
        name.to_string()
    }
}

/// 英数字も `_` も含まない記号(`+`・`<-`・`*` など)を演算子とみなす。
fn is_operator(text: &str) -> bool {
    !text.chars().any(|c| c.is_alphanumeric() || c == '_')
}

/// 1 つの file を解析した結果(契約の file の中身のうち、path と module を除く部分)。
pub struct FileAnalysis {
    pub definitions: Vec<Definition>,
    pub imports: Vec<Import>,
    pub references: Vec<Reference>,
    pub errors: Vec<String>,
}

/// source 全体を読み、定義・import・参照・読めなかった箇所を返す。
pub fn analyze(src: &str) -> FileAnalysis {
    let mut reader = Reader::new(src, 0, src.len());
    let forms = reader.read_all();
    let lines = LineIndex::new(src);
    let errors = reader.issues.iter().map(|issue| describe_issue(&lines, issue)).collect();
    let mut keywords: HashSet<&'static str> = HY_KEYWORDS.iter().chain(DOEFF_KEYWORDS).copied().collect();
    let required = RequiredNames::collect(&forms, src);
    for name in DOEFF_REQUIRED_KEYWORDS {
        if required.contains(name) {
            keywords.insert(name);
        }
    }
    let mut analyzer = Analyzer {
        src,
        lines,
        keywords,
        definitions: Vec::new(),
        imports: Vec::new(),
        references: Vec::new(),
    };
    for form in &forms {
        analyzer.visit_top(form);
    }
    for form in &forms {
        analyzer.walk(form, Quoting::None);
    }
    FileAnalysis {
        definitions: analyzer.definitions,
        imports: analyzer.imports,
        references: analyzer.references,
        errors,
    }
}

/// 読み取りの issue を `errors` の文言にする(位置は人が読む 1 始まりの 行:列)。
fn describe_issue(lines: &LineIndex, issue: &ReadIssue) -> String {
    let at = |offset: usize| {
        let position = lines.position(offset);
        format!("{}:{}", position.line + 1, position.character + 1)
    };
    match issue {
        ReadIssue::Unclosed { delim, open } => {
            format!("{}: `{}` が file の終わりまで閉じていない", at(*open), delim.opener_text())
        }
        ReadIssue::Mismatched { delim, open, at: close } => format!(
            "{}: `{}`({} で開いた)を対応しない閉じ括弧で閉じている",
            at(*close),
            delim.opener_text(),
            at(*open)
        ),
        ReadIssue::StrayCloser { at: close } => format!("{}: 対応する開き括弧の無い閉じ括弧", at(*close)),
        ReadIssue::UnterminatedString { start } => format!("{}: 文字列が file の終わりまで閉じていない", at(*start)),
    }
}

/// file が `(require …)` で持ち込んだ名前の集合(`*` を持ち込んだら全部)。
struct RequiredNames {
    names: HashSet<String>,
    everything: bool,
}

impl RequiredNames {
    /// file の全体を歩いて require した名前を集める(予約語の表を file ごとに決めるため)。
    fn collect(forms: &[Form], src: &str) -> Self {
        let mut required = RequiredNames { names: HashSet::new(), everything: false };
        required.visit(forms, src);
        required
    }

    /// form の列の中の `(require …)` を探す。
    fn visit(&mut self, forms: &[Form], src: &str) {
        for form in forms {
            let Node::Seq { items, .. } = &form.node else {
                continue;
            };
            match form.paren_items().and_then(|items| items.first()) {
                Some(head) if matches!(head.node, Node::Symbol) && &src[head.span.start..head.span.end] == "require" => {
                    for item in &items[1..] {
                        self.take(item, src);
                    }
                }
                _ => self.visit(items, src),
            }
        }
    }

    /// require の引数の 1 つ(`[names]` か `*`)から名前を取る。
    fn take(&mut self, item: &Form, src: &str) {
        match &item.node {
            Node::Seq { items, .. } if item.bracket_items().is_some() => {
                for name in items {
                    if matches!(name.node, Node::Symbol) {
                        self.insert(&src[name.span.start..name.span.end]);
                    }
                }
            }
            Node::Symbol if &src[item.span.start..item.span.end] == "*" => self.everything = true,
            _ => {}
        }
    }

    /// 名前を 1 つ足す(`*` なら全部)。
    fn insert(&mut self, name: &str) {
        if name == "*" {
            self.everything = true;
        } else {
            self.names.insert(name.to_string());
        }
    }

    /// その名前を require したかを返す。
    fn contains(&self, name: &str) -> bool {
        self.everything || self.names.contains(name)
    }
}

/// 歩いている位置が quote の中か(quote の中の記号は data で、参照ではない)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Quoting {
    None,
    Quote,
    Quasiquote,
}

/// docstring の置き場の決まり(定義の種類ごとに違う)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DocRule {
    /// 名前の直後の文字列(後に form が続く時だけ)。
    Leading,
    /// keyword の値(`law` の `:statement`、`defadr` の `:title`)。
    KeywordValue(&'static str),
    /// docstring を持たない。
    Absent,
}

/// 解析の途中の状態。定義・import・参照を積む。
struct Analyzer<'a> {
    src: &'a str,
    lines: LineIndex<'a>,
    keywords: HashSet<&'static str>,
    definitions: Vec<Definition>,
    imports: Vec<Import>,
    references: Vec<Reference>,
}

impl<'a> Analyzer<'a> {
    /// span の綴りを返す。
    fn text(&self, span: Span) -> &'a str {
        &self.src[span.start..span.end]
    }

    /// 列の先頭が記号ならその綴りを返す。
    fn head(&self, items: &[Form]) -> Option<&'a str> {
        match items.first() {
            Some(form) if matches!(form.node, Node::Symbol) => Some(self.text(form.span)),
            _ => None,
        }
    }

    /// 先頭に置かれた時に予約語になる綴りかを返す。
    fn is_keyword_head(&self, text: &str) -> bool {
        self.keywords.contains(text)
    }

    // --- 定義 ------------------------------------------------------------------------------

    /// top level の form を 1 つ見る(`do`・`eval-and-compile` の中は top level のまま)。
    fn visit_top(&mut self, form: &Form) {
        let Some(items) = form.paren_items() else {
            return;
        };
        let Some(head) = self.head(items) else {
            return;
        };
        match head {
            "do" | "eval-and-compile" | "eval-when-compile" => {
                for item in &items[1..] {
                    self.visit_top(item);
                }
            }
            _ => self.definition(form, head, items, None),
        }
    }

    /// 先頭の綴りが定義の形なら定義を積む(`container` は入れ物の定義の名前)。
    fn definition(&mut self, form: &Form, head: &str, items: &[Form], container: Option<&str>) {
        match head {
            "defn" => self.function_def(form, items, DefinitionKind::Defn, container),
            "defn/a" => self.function_def(form, items, DefinitionKind::DefnAsync, container),
            "defmacro" => self.function_def(form, items, DefinitionKind::Defmacro, container),
            "defk" => self.function_def(form, items, DefinitionKind::Defk, container),
            "deff" => self.function_def(form, items, DefinitionKind::Deff, container),
            "defclass" => self.class_def(form, items, container),
            "defrecord" => self.record_def(form, items, container),
            "defenum" => self.enum_def(form, items, container),
            "defhandler" => self.handler_def(form, items, container),
            "deftest" => {
                self.named_def(form, items, DefinitionKind::Deftest, DocRule::Leading, container);
            }
            "defsemgrep" => {
                self.named_def(form, items, DefinitionKind::Defsemgrep, DocRule::Absent, container);
            }
            "law" => {
                self.named_def(form, items, DefinitionKind::Law, DocRule::KeywordValue(":statement"), container);
            }
            "defp" => {
                self.named_def(form, items, DefinitionKind::Defp, DocRule::Leading, container);
            }
            "defpp" => {
                self.named_def(form, items, DefinitionKind::Defpp, DocRule::Leading, container);
            }
            "defpipeline" => {
                self.named_def(form, items, DefinitionKind::Defpipeline, DocRule::Leading, container);
            }
            "defphase" => {
                self.named_def(form, items, DefinitionKind::Defphase, DocRule::Leading, container);
            }
            "deftype" => {
                self.named_def(form, items, DefinitionKind::Deftype, DocRule::Absent, container);
            }
            "defadr" => {
                let kind = DefinitionKind::Defadr;
                if let Some(name) = self.named_def(form, items, kind, DocRule::KeywordValue(":title"), container) {
                    self.nested_defs(items.get(2..).unwrap_or_default(), &["law", "defsemgrep", "deftest"], &name);
                }
            }
            "defworkflow" => {
                let kind = DefinitionKind::Defworkflow;
                if let Some(name) = self.named_def(form, items, kind, DocRule::Leading, container) {
                    self.nested_defs(items.get(2..).unwrap_or_default(), &["defphase"], &name);
                }
            }
            "defmain" => self.main_def(form, items, container),
            "defmcp-tool" => self.mcp_tool_def(form, items, container),
            "setv" | "setx" => {
                for pair in items[1..].chunks(2) {
                    self.binding_targets(form, &pair[0], DefinitionKind::Variable, container);
                }
            }
            "val" | "var" | "lazy-val" | "lazy-var" => {
                if let Some(target) = items.get(1) {
                    self.binding_targets(form, target, DefinitionKind::Variable, container);
                }
            }
            "lazy" | "session" => {
                let rest = self.skip_val_var(&items[1..]);
                if let Some(target) = rest.first() {
                    self.binding_targets(form, target, DefinitionKind::Variable, container);
                }
            }
            _ => {}
        }
    }

    /// `(session var x …)` / `(lazy val x …)` の `var` / `val` を読み飛ばす。
    fn skip_val_var<'f>(&self, rest: &'f [Form]) -> &'f [Form] {
        match rest.first() {
            Some(form) if matches!(form.node, Node::Symbol) && matches!(self.text(form.span), "val" | "var") => &rest[1..],
            _ => rest,
        }
    }

    /// 定義の名前の form から名前の span を取る(`#^ T name` は name)。
    fn def_name(&self, form: &Form) -> Option<Span> {
        match &form.node {
            Node::Symbol if !is_operator(self.text(form.span)) => Some(form.span),
            Node::Annotated { target: Some(target), .. } => self.def_name(target),
            _ => None,
        }
    }

    /// 定義を 1 つ積み、積んだ名前を返す。
    fn push_def(
        &mut self,
        name: Span,
        kind: DefinitionKind,
        full: Span,
        container: Option<&str>,
        docstring: Option<String>,
        params: Vec<String>,
    ) -> String {
        let text = self.text(name).to_string();
        self.definitions.push(Definition {
            mangled: mangle(&text),
            name: text.clone(),
            kind,
            range: self.lines.range(name.start, name.end),
            full_range: self.lines.range(full.start, full.end),
            container: container.map(str::to_string),
            docstring,
            params,
        });
        text
    }

    /// `(defn [decorators]? :tp [T]? #^ Ret? name [params] "doc"? body…)` を読む(defk・deff・defmacro も)。
    fn function_def(&mut self, form: &Form, items: &[Form], kind: DefinitionKind, container: Option<&str>) {
        let rest = skip_type_params(self, skip_decorators(&items[1..]));
        let Some(name) = rest.first().and_then(|first| self.def_name(first)) else {
            return;
        };
        let (params, body) = match rest.get(1).and_then(Form::bracket_items) {
            Some(params) => (self.param_names(params), rest.get(2..).unwrap_or_default()),
            None => (Vec::new(), rest.get(1..).unwrap_or_default()),
        };
        let docstring = self.leading_docstring(body);
        self.push_def(name, kind, form.span, container, docstring, params);
    }

    /// `(defclass [decorators]? Name [bases]? "doc"? body…)` を読み、method と field を入れ子に積む。
    fn class_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let rest = skip_type_params(self, skip_decorators(&items[1..]));
        let Some(name) = rest.first().and_then(|first| self.def_name(first)) else {
            return;
        };
        let body = match rest.get(1) {
            Some(bases) if bases.bracket_items().is_some() => rest.get(2..).unwrap_or_default(),
            _ => rest.get(1..).unwrap_or_default(),
        };
        let (docstring, members) = self.body_docstring(body);
        let class_name = self.push_def(name, DefinitionKind::Defclass, form.span, container, docstring, Vec::new());
        for member in members {
            self.class_member(member, &class_name);
        }
    }

    /// class の body の 1 つを見る: `(defn …)` は method、`#^ T x` / `(#^ T x …)` / `(setv x …)` は field。
    fn class_member(&mut self, member: &Form, class_name: &str) {
        match &member.node {
            Node::Annotated { .. } => {
                if let Some(name) = self.def_name(member) {
                    self.push_def(name, DefinitionKind::Field, member.span, Some(class_name), None, Vec::new());
                }
            }
            Node::Seq { .. } => {
                let Some(items) = member.paren_items() else {
                    return;
                };
                match (self.head(items), items.first().map(|first| &first.node)) {
                    (Some("defn" | "defn/a" | "defk" | "deff" | "defmacro"), _) => {
                        self.function_def(member, items, DefinitionKind::Method, Some(class_name));
                    }
                    (Some("setv"), _) => {
                        for pair in items[1..].chunks(2) {
                            self.binding_targets(member, &pair[0], DefinitionKind::Field, Some(class_name));
                        }
                    }
                    (_, Some(Node::Annotated { .. })) => {
                        if let Some(name) = self.def_name(&items[0]) {
                            self.push_def(name, DefinitionKind::Field, member.span, Some(class_name), None, Vec::new());
                        }
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }

    /// `(defrecord Name "doc"? #^ T field …)` を読む(field は裸・括弧つき・注釈なしの記号のどれでもよい)。
    fn record_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let (docstring, fields) = self.body_docstring(items.get(2..).unwrap_or_default());
        let record = self.push_def(name, DefinitionKind::Defrecord, form.span, container, docstring, Vec::new());
        for field in fields {
            match &field.node {
                Node::Symbol if !is_operator(self.text(field.span)) => {
                    self.push_def(field.span, DefinitionKind::Field, field.span, Some(&record), None, Vec::new());
                }
                _ => self.class_member(field, &record),
            }
        }
    }

    /// `(defenum Name A B (C "value") …)` を読む。
    fn enum_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let (docstring, members) = self.body_docstring(items.get(2..).unwrap_or_default());
        let enum_name = self.push_def(name, DefinitionKind::Defenum, form.span, container, docstring, Vec::new());
        for member in members {
            let target = match member.paren_items() {
                Some(parts) => parts.first(),
                None => Some(member),
            };
            if let Some(span) = target.and_then(|target| self.def_name(target)) {
                self.push_def(span, DefinitionKind::EnumMember, member.span, Some(&enum_name), None, Vec::new());
            }
        }
    }

    /// `(defhandler name "doc"? [params]? "doc"? (session var x init)… (Effect [fields] body…)…)` を読む。
    fn handler_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let mut clauses = items.get(2..).unwrap_or_default();
        let mut docstring = None;
        let mut params = Vec::new();
        if let Some(doc) = clauses.first().and_then(string_value(self.src)) {
            docstring = Some(doc);
            clauses = &clauses[1..];
        }
        if let Some(fields) = clauses.first().and_then(Form::bracket_items) {
            params = self.param_names(fields);
            clauses = &clauses[1..];
        }
        if let Some(doc) = clauses.first().and_then(string_value(self.src)) {
            docstring = Some(doc);
            clauses = &clauses[1..];
        }
        let handler = self.push_def(name, DefinitionKind::Defhandler, form.span, container, docstring, params);
        for clause in clauses {
            let Some(parts) = clause.paren_items() else {
                continue;
            };
            match self.head(parts) {
                Some(head @ ("session" | "lazy" | "lazy-val" | "lazy-var" | "val" | "var")) => {
                    self.definition(clause, head, parts, Some(&handler));
                }
                Some(effect) if !self.is_keyword_head(effect) => {
                    if let Some(fields) = parts.get(1).and_then(Form::bracket_items) {
                        let fields = self.param_names(fields);
                        let effect_span = parts[0].span;
                        let kind = DefinitionKind::EffectClause;
                        self.push_def(effect_span, kind, clause.span, Some(&handler), None, fields);
                    }
                }
                _ => {}
            }
        }
    }

    /// 名前が 2 番目に来る定義(`(deftest name …)` など)を積み、名前を返す。
    fn named_def(
        &mut self,
        form: &Form,
        items: &[Form],
        kind: DefinitionKind,
        doc_rule: DocRule,
        container: Option<&str>,
    ) -> Option<String> {
        let rest = skip_type_params(self, items.get(1..).unwrap_or_default());
        let name = rest.first().and_then(|first| self.def_name(first))?;
        let body = rest.get(1..).unwrap_or_default();
        let docstring = match doc_rule {
            DocRule::Leading => self.leading_docstring(body),
            DocRule::KeywordValue(key) => self.keyword_value_docstring(body, key),
            DocRule::Absent => None,
        };
        Some(self.push_def(name, kind, form.span, container, docstring, Vec::new()))
    }

    /// `(defmain [args] body…)` を読む。名前を持たない形なので、先頭の `defmain` を名前にする。
    fn main_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        match items.get(1) {
            Some(params) if params.bracket_items().is_some() => {
                let names = self.param_names(params.bracket_items().unwrap_or_default());
                let docstring = self.leading_docstring(items.get(2..).unwrap_or_default());
                self.push_def(items[0].span, DefinitionKind::Defmain, form.span, container, docstring, names);
            }
            _ => {
                self.named_def(form, items, DefinitionKind::Defmain, DocRule::Leading, container);
            }
        }
    }

    /// `(defmcp-tool name "description" [params] body…)` を読む(description を docstring にする)。
    fn mcp_tool_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let rest = items.get(2..).unwrap_or_default();
        let docstring = rest.first().and_then(string_value(self.src));
        let params = rest
            .iter()
            .find_map(Form::bracket_items)
            .map(|params| self.param_names(params))
            .unwrap_or_default();
        self.push_def(name, DefinitionKind::DefmcpTool, form.span, container, docstring, params);
    }

    /// 入れ物の定義の中を探し、`heads` の形の定義を入れ子として積む(defadr の law など)。
    fn nested_defs(&mut self, forms: &[Form], heads: &[&str], container: &str) {
        for form in forms {
            let Node::Seq { items, .. } = &form.node else {
                continue;
            };
            match form.paren_items().and_then(|items| self.head(items)) {
                Some(head) if heads.contains(&head) => self.definition(form, head, items, Some(container)),
                _ => self.nested_defs(items, heads, container),
            }
        }
    }

    /// 束縛の的(記号・注釈つき・`[a b]` の分解)の名前を 1 つずつ定義として積む。
    fn binding_targets(&mut self, form: &Form, target: &Form, kind: DefinitionKind, container: Option<&str>) {
        match &target.node {
            Node::Symbol => {
                let text = self.text(target.span);
                if !is_operator(text) && !text.contains('.') {
                    self.push_def(target.span, kind, form.span, container, None, Vec::new());
                }
            }
            Node::Annotated { target: Some(inner), .. } => self.binding_targets(form, inner, kind, container),
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, inner: Some(inner) } => {
                self.binding_targets(form, inner, kind, container)
            }
            Node::Seq { items, .. } if !target.is_brace() && target.paren_items().is_none() => {
                for item in items {
                    self.binding_targets(form, item, kind, container);
                }
            }
            _ => {}
        }
    }

    /// 引数の list から引数の名前を書かれたとおりに取る(`#* args`・`[x default]`・`#^ T x` を含む)。
    fn param_names(&self, params: &[Form]) -> Vec<String> {
        let mut names = Vec::new();
        for param in params {
            self.param_name(param, &mut names);
        }
        names
    }

    /// 引数の 1 つから名前を取る。
    fn param_name(&self, param: &Form, names: &mut Vec<String>) {
        match &param.node {
            Node::Symbol => {
                let text = self.text(param.span);
                if !is_operator(text) {
                    names.push(text.to_string());
                }
            }
            Node::Annotated { target: Some(target), .. } => self.param_name(target, names),
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, inner: Some(inner) } => {
                self.param_name(inner, names)
            }
            Node::Seq { items, .. } if param.bracket_items().is_some() => {
                if let Some(first) = items.first() {
                    self.param_name(first, names);
                }
            }
            _ => {}
        }
    }

    /// 本体の先頭の docstring を取る。`{:pre … :post …}` の後でもよく、後に form が続く時だけ docstring。
    fn leading_docstring(&self, body: &[Form]) -> Option<String> {
        for (index, form) in body.iter().enumerate() {
            if form.is_brace() {
                continue;
            }
            return match string_value(self.src)(form) {
                Some(doc) if index + 1 < body.len() => Some(doc),
                _ => None,
            };
        }
        None
    }

    /// class 型の本体の先頭の文字列を docstring として取り(後に何も無くてもよい)、残りを返す。
    fn body_docstring<'f>(&self, body: &'f [Form]) -> (Option<String>, &'f [Form]) {
        match body.first().and_then(string_value(self.src)) {
            Some(doc) => (Some(doc), &body[1..]),
            None => (None, body),
        }
    }

    /// `:key "値"` の値を docstring として取る(law の `:statement`、defadr の `:title`)。
    fn keyword_value_docstring(&self, body: &[Form], key: &str) -> Option<String> {
        body.windows(2).find_map(|pair| match pair[0].node {
            Node::Keyword if self.text(pair[0].span) == key => string_value(self.src)(&pair[1]),
            _ => None,
        })
    }

    // --- import と参照 ---------------------------------------------------------------------

    /// form を歩き、記号の出現を参照に、`(import …)` / `(require …)` を import に積む。
    fn walk(&mut self, form: &Form, quoting: Quoting) {
        match &form.node {
            Node::Symbol => {
                if quoting == Quoting::None {
                    self.reference(form.span);
                }
            }
            Node::Keyword | Node::Number | Node::Discarded => {}
            Node::Str { kind: StrKind::Format | StrKind::FormatBracket, body } => {
                if quoting != Quoting::Quote {
                    self.walk_fstring(*body, quoting);
                }
            }
            Node::Str { .. } => {}
            Node::Seq { items, .. } if form.paren_items().is_some() => self.walk_list(items, quoting),
            Node::Seq { items, .. } => {
                for item in items {
                    self.walk(item, quoting);
                }
            }
            Node::Prefixed { prefix, inner } => {
                let inner_quoting = match (prefix, quoting) {
                    (_, Quoting::Quote) => Quoting::Quote,
                    (Prefix::Quote, _) => Quoting::Quote,
                    (Prefix::Quasiquote, _) => Quoting::Quasiquote,
                    (Prefix::Unquote | Prefix::UnquoteSplice, Quoting::Quasiquote) => Quoting::None,
                    (Prefix::Unquote | Prefix::UnquoteSplice | Prefix::Unpack | Prefix::UnpackMapping, _) => quoting,
                };
                if let Some(inner) = inner {
                    self.walk(inner, inner_quoting);
                }
            }
            Node::Annotated { annotation, target } => {
                for part in [annotation, target].into_iter().flatten() {
                    self.walk(part, quoting);
                }
            }
            Node::Tagged { inner } => {
                if let Some(inner) = inner {
                    self.walk(inner, quoting);
                }
            }
        }
    }

    /// `( … )` を歩く。先頭の予約語は参照に入れず、import / quote の形はそれぞれに読む。
    fn walk_list(&mut self, items: &[Form], quoting: Quoting) {
        if quoting != Quoting::None {
            for item in items {
                self.walk(item, quoting);
            }
            return;
        }
        let Some(head) = self.head(items) else {
            for item in items {
                self.walk(item, quoting);
            }
            return;
        };
        let mut rest = &items[1..];
        match head {
            "import" => self.parse_import(rest, false),
            "require" => self.parse_import(rest, true),
            "quote" => {
                for item in rest {
                    self.walk(item, Quoting::Quote);
                }
                return;
            }
            "quasiquote" => {
                for item in rest {
                    self.walk(item, Quoting::Quasiquote);
                }
                return;
            }
            "session" | "lazy" => rest = self.skip_val_var(rest),
            _ => {}
        }
        if !self.is_keyword_head(head) {
            self.reference(items[0].span);
        }
        for item in rest {
            self.walk(item, Quoting::None);
        }
    }

    /// f 文字列の `{form}` の中を読んで歩く(`{x:>10}` の書式は名前から外す)。
    fn walk_fstring(&mut self, body: Span, quoting: Quoting) {
        let bytes = self.src.as_bytes();
        let mut i = body.start;
        while i < body.end {
            match bytes[i] {
                b'{' if i + 1 < body.end && bytes[i + 1] == b'{' => i += 2,
                b'{' => {
                    let close = matching_brace(bytes, i, body.end);
                    let mut reader = Reader::new(self.src, i + 1, close);
                    if let Some(inner) = reader.read_form() {
                        match inner.node {
                            Node::Symbol => {
                                let text = self.text(inner.span);
                                let end = match text.char_indices().skip(1).find(|(_, c)| *c == ':') {
                                    Some((colon, _)) => inner.span.start + colon,
                                    None => inner.span.end,
                                };
                                if quoting == Quoting::None {
                                    self.reference(Span { start: inner.span.start, end });
                                }
                            }
                            _ => self.walk(&inner, quoting),
                        }
                    }
                    i = close + 1;
                }
                _ => i += 1,
            }
        }
    }

    /// 記号の出現を `.` で区切って参照に積む(演算子・定数・`_` は除く)。
    fn reference(&mut self, span: Span) {
        let text = self.text(span);
        if text.is_empty() || text == "_" || CONSTANTS.contains(&text) || is_operator(text) {
            return;
        }
        let mut qualifier: Option<String> = None;
        let mut offset = 0;
        for part in text.split('.') {
            let start = span.start + offset;
            offset += part.len() + 1;
            if part.is_empty() {
                continue;
            }
            self.references.push(Reference {
                name: part.to_string(),
                mangled: mangle(part),
                qualifier: qualifier.clone(),
                range: self.lines.range(start, start + part.len()),
            });
            qualifier = Some(match qualifier {
                Some(prefix) => format!("{}.{}", prefix, part),
                None => part.to_string(),
            });
        }
    }

    /// `(import …)` / `(require …)` の引数を読む: `mod`・`mod :as m`・`mod [a b :as c]`・`mod *`・
    /// `mod :macros [a] :readers [b]`。
    fn parse_import(&mut self, rest: &[Form], is_require: bool) {
        let mut index = 0;
        while index < rest.len() {
            let module_form = &rest[index];
            index += 1;
            if !matches!(module_form.node, Node::Symbol) || is_operator(self.text(module_form.span)) {
                continue;
            }
            let module = self.text(module_form.span);
            let mut emitted = false;
            while let Some(next) = rest.get(index) {
                match &next.node {
                    Node::Keyword if self.text(next.span) == ":as" => {
                        index += 1;
                        if let Some(alias) = rest.get(index).filter(|alias| matches!(alias.node, Node::Symbol)) {
                            let alias_text = Some(self.text(alias.span).to_string());
                            self.push_import(module, None, alias_text, alias.span, is_require);
                            emitted = true;
                            index += 1;
                        }
                    }
                    Node::Keyword if matches!(self.text(next.span), ":macros" | ":readers") => index += 1,
                    Node::Seq { items, .. } if next.bracket_items().is_some() => {
                        self.import_names(module, items, is_require);
                        emitted = true;
                        index += 1;
                    }
                    Node::Symbol if self.text(next.span) == "*" => {
                        self.push_import(module, Some("*".to_string()), None, next.span, is_require);
                        emitted = true;
                        index += 1;
                    }
                    _ => break,
                }
            }
            if !emitted {
                self.push_import(module, None, None, module_form.span, is_require);
            }
        }
    }

    /// `[a b :as c *]` の名前を import に積む。
    fn import_names(&mut self, module: &str, names: &[Form], is_require: bool) {
        let mut index = 0;
        while index < names.len() {
            let name = &names[index];
            index += 1;
            if !matches!(name.node, Node::Symbol) {
                continue;
            }
            let name_text = Some(self.text(name.span).to_string());
            let alias = match (names.get(index), names.get(index + 1)) {
                (Some(keyword), Some(alias))
                    if matches!(keyword.node, Node::Keyword)
                        && self.text(keyword.span) == ":as"
                        && matches!(alias.node, Node::Symbol) =>
                {
                    index += 2;
                    Some(alias)
                }
                _ => None,
            };
            match alias {
                Some(alias) => {
                    let alias_text = Some(self.text(alias.span).to_string());
                    self.push_import(module, name_text, alias_text, alias.span, is_require);
                }
                None => self.push_import(module, name_text, None, name.span, is_require),
            }
        }
    }

    /// import を 1 つ積む(`range` は名前・module・別名のうち契約が決めた方)。
    fn push_import(&mut self, module: &str, name: Option<String>, alias: Option<String>, range: Span, is_require: bool) {
        self.imports.push(Import {
            module: module.to_string(),
            name,
            alias,
            range: self.lines.range(range.start, range.end),
            is_require,
        });
    }
}

/// `(defn [decorators] name …)` の decorator の list を読み飛ばす(次が名前の形の時だけ decorator)。
fn skip_decorators(rest: &[Form]) -> &[Form] {
    match (rest.first().and_then(Form::bracket_items), rest.get(1).map(|next| &next.node)) {
        (Some(_), Some(Node::Symbol | Node::Annotated { .. } | Node::Keyword)) => &rest[1..],
        _ => rest,
    }
}

/// `:tp [T …]`(型引数)を読み飛ばす。
fn skip_type_params<'f>(analyzer: &Analyzer, rest: &'f [Form]) -> &'f [Form] {
    match rest.first() {
        Some(form) if matches!(form.node, Node::Keyword) && analyzer.text(form.span) == ":tp" => {
            rest.get(2..).unwrap_or_default()
        }
        _ => rest,
    }
}

/// docstring になれる文字列の form から中身を取る関数を返す(普通の文字列は escape を戻し、
/// bracket 文字列はそのまま。どちらも Python の inspect.cleandoc と同じく字下げを揃える)。
fn string_value(src: &str) -> impl Fn(&Form) -> Option<String> + '_ {
    move |form: &Form| match &form.node {
        Node::Str { kind: StrKind::Plain, body } => Some(clean_doc(&unescape(&src[body.start..body.end]))),
        Node::Str { kind: StrKind::Bracket, body } => Some(clean_doc(&src[body.start..body.end])),
        _ => None,
    }
}

/// Hy の文字列の escape(`\n`・`\t`・`\"`・`\\`・行末の `\`)を戻す。知らない escape はそのまま残す。
fn unescape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some('\\') => out.push('\\'),
            Some('"') => out.push('"'),
            Some('\'') => out.push('\''),
            Some('\n') => {}
            Some(other) => {
                out.push('\\');
                out.push(other);
            }
            None => out.push('\\'),
        }
    }
    out
}

/// docstring の字下げを揃える(Python の inspect.cleandoc と同じ: 2 行目以降の共通の字下げを除き、前後の空行を落とす)。
fn clean_doc(text: &str) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let indent = lines
        .iter()
        .skip(1)
        .filter(|line| !line.trim().is_empty())
        .map(|line| line.len() - line.trim_start().len())
        .min()
        .unwrap_or(0);
    let mut cleaned: Vec<String> = lines
        .iter()
        .enumerate()
        .map(|(index, line)| match index {
            0 => line.trim().to_string(),
            _ => line.get(indent..).unwrap_or_else(|| line.trim_start()).trim_end().to_string(),
        })
        .collect();
    while cleaned.first().is_some_and(|line| line.is_empty()) {
        cleaned.remove(0);
    }
    while cleaned.last().is_some_and(|line| line.is_empty()) {
        cleaned.pop();
    }
    cleaned.join("\n")
}

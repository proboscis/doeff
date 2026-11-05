//! Extracts definitions, imports and references from a Hy form tree.
//!
//! Origin: the reserved-word tables (`HY_KEYWORDS`, `DOEFF_KEYWORDS`, `DOEFF_REQUIRED_KEYWORDS`, `CONSTANTS`),
//! `mangle`, `is_operator`, how required names are collected, and the per-shape branches (decorator detection for defn,
//! defclass fields, defhandler docstring / params / effect clauses, import interpretation, `{…}` in f-strings) are
//! copied from `HyAnalyzer` in `vscode-semantic-highlighting/rust-highlighter/src/hy.rs`.
//! The source of the copy emits classifications used for coloring. This module emits the index contract (definition names and whole-form ranges,
//! container, docstring, params, imports, references).

use std::collections::{BTreeMap, HashSet};

use super::model::{
    ArgumentValue, Call, CallArgument, ContractClause, ContractSide, Definition, DefinitionKind, Import, NameRef, ParamType, RawMark, Reference, TypeNote,
};
use super::position::LineIndex;
use super::reader::{matching_brace, Delim, Form, Node, Prefix, ReadIssue, Reader, Span, StrKind};

/// Hy special forms and core macros — reserved words when placed at the head (not counted as references).
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

/// doeff-hy forms that are reserved words wherever they appear (definition and binding syntax).
const DOEFF_KEYWORDS: &[&str] = &[
    "defk", "deff", "defp", "defpp", "fnk", "do!", "<-", "<->", "for/do", "deftest", "defpipeline",
    "defmcp-tool", "set!", "defhandler", "defeffect", "resume", "with-handler", "defrecord", "defwire", "defenum",
    "defworkflow", "defphase", "defadr", "defsemgrep", "law", "lazy-val", "lazy-var",
];

/// doeff-hy macros whose names are ordinary words — reserved only when the file requires them.
const DOEFF_REQUIRED_KEYWORDS: &[&str] = &[
    "val", "var", "lazy", "session", "handle", "traverse", "validate", "check", "parallel",
    "parallel-for", "loop", "time!", "random!", "pipeline", "agent!", "gate!", "workspace!",
    "merge!",
];

/// Heads of definitions that can carry a contract dictionary (`{:pre … :post … :tags …}`).
const CONTRACT_DEFINERS: &[&str] = &["defk", "deff", "defp", "defpp", "defhandler"];

/// Syntax symbols inside type annotations (not counted as names).
const TYPE_SYNTAX: &[&str] = &["|", "of", "get"];

/// Heads of doeff-hy binding syntax. The defk / deftest / defhandler macros read them inside their bodies, so they are syntax even when the file does not
/// require them, and they are excluded from calls.
const BINDING_SYNTAX: &[&str] = &["val", "var", "lazy", "session"];

/// Constants (not counted as references).
const CONSTANTS: &[&str] = &["True", "False", "None", "...", "Ellipsis", "NotImplemented", "Inf", "NaN"];

/// Number of times `mangle` was called on this thread (tests use it to check whether the work is proportional to file size — `qualify::link`).
#[cfg(test)]
thread_local! {
    static MANGLES: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[cfg(test)]
pub(super) fn mangle_calls() -> usize {
    MANGLES.with(std::cell::Cell::get)
}

/// Converts a Hy name to a Python name (contract mangle: `-` becomes `_`, a leading `-` is kept).
pub fn mangle(name: &str) -> String {
    #[cfg(test)]
    MANGLES.with(|count| count.set(count.get() + 1));
    let has_word = name.chars().any(|c| c.is_alphanumeric() || c == '_');
    if has_word && name.contains('-') {
        let trimmed = name.trim_start_matches('-');
        let leading = &name[..name.len() - trimmed.len()];
        format!("{}{}", leading, trimmed.replace('-', "_"))
    } else {
        name.to_string()
    }
}

/// Treats symbols containing neither alphanumerics nor `_` (such as `+`, `<-`, `*`) as operators.
fn is_operator(text: &str) -> bool {
    !text.chars().any(|c| c.is_alphanumeric() || c == '_')
}

/// Result of analyzing one file (the contents of the contract's file, minus path and module).
pub struct FileAnalysis {
    pub definitions: Vec<Definition>,
    pub imports: Vec<Import>,
    pub references: Vec<Reference>,
    pub calls: Vec<Call>,
    pub errors: Vec<String>,
}

/// Reads the whole source and returns definitions, imports, references and the places that could not be read.
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
        definition_spans: Vec::new(),
        imports: Vec::new(),
        references: Vec::new(),
        calls: Vec::new(),
        call_suppression: 0,
        inspecting: 0,
        declared_names: HashSet::new(),
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
        calls: analyzer.calls,
        errors,
    }
}

/// Turns a read issue into an `errors` message (positions are human-readable 1-based line:column).
fn describe_issue(lines: &LineIndex, issue: &ReadIssue) -> String {
    let at = |offset: usize| {
        let position = lines.position(offset);
        format!("{}:{}", position.line + 1, position.character + 1)
    };
    match issue {
        ReadIssue::Unclosed { delim, open } => {
            format!("{}: `{}` is not closed before the end of the file", at(*open), delim.opener_text())
        }
        ReadIssue::Mismatched { delim, open, at: close } => format!(
            "{}: `{}`(opened at {}) is closed by a mismatched closing bracket",
            at(*close),
            delim.opener_text(),
            at(*open)
        ),
        ReadIssue::StrayCloser { at: close } => format!("{}: closing bracket with no matching opening bracket", at(*close)),
        ReadIssue::UnterminatedString { start } => format!("{}: string is not closed before the end of the file", at(*start)),
    }
}

/// Set of names a file brought in with `(require …)` (everything if `*` was brought in).
struct RequiredNames {
    names: HashSet<String>,
    everything: bool,
}

impl RequiredNames {
    /// Walks the whole file and collects required names (so the reserved-word table can be decided per file).
    fn collect(forms: &[Form], src: &str) -> Self {
        let mut required = RequiredNames { names: HashSet::new(), everything: false };
        required.visit(forms, src);
        required
    }

    /// Looks for `(require …)` inside a list of forms.
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

    /// Takes names from one require argument (`[names]` or `*`).
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

    /// Adds one name (everything for `*`).
    fn insert(&mut self, name: &str) {
        if name == "*" {
            self.everything = true;
        } else {
            self.names.insert(name.to_string());
        }
    }

    /// Returns whether the name was required.
    fn contains(&self, name: &str) -> bool {
        self.everything || self.names.contains(name)
    }
}

/// Whether the walk position is inside a quote (symbols inside a quote are data, not references).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Quoting {
    None,
    Quote,
    Quasiquote,
}

/// Rules for where the docstring goes (differs per kind of definition).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DocRule {
    /// A string right after the name (only when a form follows).
    Leading,
    /// The value of a keyword (`:statement` of `law`, `:title` of `defadr`).
    KeywordValue(&'static str),
    /// Has no docstring.
    Absent,
}

/// Intermediate state of the analysis. Accumulates definitions, imports and references.
struct Analyzer<'a> {
    src: &'a str,
    lines: LineIndex<'a>,
    keywords: HashSet<&'static str>,
    definitions: Vec<Definition>,
    /// Byte range of each definition's whole form, in the same order as definitions (used to look up the caller of a call).
    definition_spans: Vec<Span>,
    imports: Vec<Import>,
    references: Vec<Reference>,
    calls: Vec<Call>,
    /// While greater than 0, calls are not accumulated (inside a type annotation or a match pattern).
    call_suppression: u32,
    /// While greater than 0, accumulated references become mere mentions that only inspect a value (`Reference::inspected`) (inside operands of a comparison form).
    inspecting: u32,
    /// Start byte of a defrecord field name. It is a name being declared, not a mention of an imported (function) name with the same spelling, so it is not accumulated as a reference.
    declared_names: HashSet<usize>,
}

/// Heads of forms that only inspect their operands — comparisons (identity, equality, containment) and `assert` (it only builds the truth value and the failure message).
/// They do not call operand values and do not pass them to other definitions. Attribute reads outside the comparison (`(+ f.__module__ ":" f.__name__)` naming a handler
/// as an interpreter constant — DOEFF137) are still reported as before.
const COMPARISONS: &[&str] = &["is", "is-not", "=", "!=", "in", "not-in", "assert"];

impl<'a> Analyzer<'a> {
    /// Returns the spelling of the span.
    fn text(&self, span: Span) -> &'a str {
        &self.src[span.start..span.end]
    }

    /// If the head of the list is a symbol, returns its spelling.
    fn head(&self, items: &[Form]) -> Option<&'a str> {
        match items.first() {
            Some(form) if matches!(form.node, Node::Symbol) => Some(self.text(form.span)),
            _ => None,
        }
    }

    /// Returns whether the spelling becomes a reserved word when placed at the head.
    fn is_keyword_head(&self, text: &str) -> bool {
        self.keywords.contains(text)
    }

    // --- Definitions ------------------------------------------------------------------------------

    /// Looks at one top-level form (inside `do` and `eval-and-compile` it stays top level).
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

    /// If the head spelling is a definition form, accumulates the definition (`container` is the name of the container definition).
    fn definition(&mut self, form: &Form, head: &str, items: &[Form], container: Option<&str>) {
        let before = self.definitions.len();
        self.definition_by_head(form, head, items, container);
        // For definitions with a contract dictionary (defk, deff, defp, defpp, defhandler), record the dictionary's :tags as the definition's tags,
        // :effects as the declared effects, and :pre / :post as the argument and answer types and the non-type contracts.
        if CONTRACT_DEFINERS.contains(&head) && self.definitions.len() > before {
            let dict = self.contract_dict(items);
            let tags = dict.and_then(|d| self.dict_value(d, ":tags")).filter(|v| v.is_brace()).and_then(|v| self.tags_of_dict(v));
            self.definitions[before].tags = tags;
            if let Some(dict) = dict {
                self.read_contract(before, dict);
            }
        }
    }

    /// Reads a definition according to the head spelling.
    fn definition_by_head(&mut self, form: &Form, head: &str, items: &[Form], container: Option<&str>) {
        match head {
            "defn" => self.function_def(form, items, DefinitionKind::Defn, container),
            "defn/a" => self.function_def(form, items, DefinitionKind::DefnAsync, container),
            "defmacro" => self.function_def(form, items, DefinitionKind::Defmacro, container),
            "defk" => self.function_def(form, items, DefinitionKind::Defk, container),
            "deff" => self.function_def(form, items, DefinitionKind::Deff, container),
            "defclass" => self.class_def(form, items, container),
            // defwire (a JSON-boundary type — record.hy in doeff-hy) expands to defrecord, so it has the same reading and the same kind.
            "defrecord" | "defwire" => self.record_def(form, items, container),
            "defenum" => self.enum_def(form, items, container),
            "defhandler" => self.handler_def(form, items, container),
            "defeffect" => self.effect_def(form, items, container),
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
            "defsystem" => {
                self.named_def(form, items, DefinitionKind::Defsystem, DocRule::Leading, container);
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

    /// Skips the `var` / `val` of `(session var x …)` / `(lazy val x …)`.
    fn skip_val_var<'f>(&self, rest: &'f [Form]) -> &'f [Form] {
        match rest.first() {
            Some(form) if matches!(form.node, Node::Symbol) && matches!(self.text(form.span), "val" | "var") => &rest[1..],
            _ => rest,
        }
    }

    /// Takes the name span from a definition's name form (for `#^ T name` it is name).
    fn def_name(&self, form: &Form) -> Option<Span> {
        match &form.node {
            Node::Symbol if !is_operator(self.text(form.span)) => Some(form.span),
            Node::Annotated { target: Some(target), .. } => self.def_name(target),
            _ => None,
        }
    }

    /// Accumulates one definition and returns the accumulated name.
    fn push_def(
        &mut self,
        name: Span,
        kind: DefinitionKind,
        full: Span,
        container: Option<&str>,
        docstring: Option<String>,
        params: Vec<String>,
    ) -> String {
        self.push_def_with_bases(name, kind, full, container, docstring, params, Vec::new())
    }

    /// Accumulates one definition that has bases (defclass / defrecord) and returns the accumulated name. Also records the byte range of the whole form
    /// (used to look up the caller of a call).
    #[allow(clippy::too_many_arguments)]
    fn push_def_with_bases(
        &mut self,
        name: Span,
        kind: DefinitionKind,
        full: Span,
        container: Option<&str>,
        docstring: Option<String>,
        params: Vec<String>,
        bases: Vec<String>,
    ) -> String {
        let text = self.text(name).to_string();
        self.definitions.push(Definition {
            mangled: mangle(&text),
            name: text.clone(),
            // module is decided by the file index, so qualify::link fills it in after the analysis
            qualified_name: String::new(),
            kind,
            range: self.lines.range(name.start, name.end),
            full_range: self.lines.range(full.start, full.end),
            container: container.map(str::to_string),
            docstring,
            params,
            bases,
            // Evidence of raw side effects crosses files, so raw.rs fills it in after analyzing all files
            raw: RawMark::default(),
            // Tags exist only for definitions with a contract dictionary and for defeffect (the reader fills them in after the definition is accumulated)
            tags: None,
            // Check expressions at construction time (only :check in the head dictionary of defrecord — record_def fills them in)
            checks: None,
            // Declared effects, types and contracts exist only for definitions with a contract dictionary and for defeffect / defrecord (the per-shape readers fill them in)
            effects: None,
            param_types: Vec::new(),
            answer_type: None,
            contracts: Vec::new(),
            // The effects resolved by the effect clauses (handler_def fills them in)
            handles: None,
            // decorators (function_def and class_def fill them in)
            decorators: Vec::new(),
        });
        self.definition_spans.push(full);
        text
    }

    /// Takes the base symbols of `[Base other.Base :metaclass M]` as written (keywords and their values are excluded).
    fn base_names(&self, bases: &[Form]) -> Vec<String> {
        let mut names = Vec::new();
        let mut index = 0;
        while index < bases.len() {
            match bases[index].node {
                Node::Keyword => index += 2,
                Node::Symbol => {
                    names.push(self.text(bases[index].span).to_string());
                    index += 1;
                }
                _ => index += 1,
            }
        }
        names
    }

    /// Reads `(defn [decorators]? :tp [T]? #^ Ret? name [params] "doc"? body…)` (also defk, deff, defmacro).
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
        let decorators = self.decorators(&items[1..]);
        self.push_def(name, kind, form.span, container, docstring, params);
        // The `#^ T name` annotation is the answer type (if the contract dictionary has :post, that wins — read_contract overwrites it)
        let answer = match rest.first().map(|first| &first.node) {
            Some(Node::Annotated { annotation: Some(annotation), .. }) => Some(self.type_note(annotation)),
            Some(_) | None => None,
        };
        if let Some(last) = self.definitions.last_mut() {
            last.answer_type = answer;
            last.decorators = decorators;
        }
    }

    /// The spelling of each element of the definition's head decorator `[…]` (what `skip_decorators` skips) — so the reader side can show it as badges.
    /// For call forms, the outer parentheses are removed and runs of whitespace outside strings are collapsed to one. If there is no `[…]`, an empty list.
    fn decorators(&self, rest: &[Form]) -> Vec<String> {
        if skip_decorators(rest).len() == rest.len() {
            return Vec::new();
        }
        let Some(items) = rest.first().and_then(Form::bracket_items) else {
            return Vec::new();
        };
        items
            .iter()
            .filter(|item| !matches!(item.node, Node::Discarded))
            .map(|item| {
                let written = self.text(item.span);
                let text = match item.paren_items() {
                    // `(dataclass :frozen True)` -> `dataclass :frozen True` (for an unclosed parenthesis only the opening one is removed)
                    Some(_) => {
                        let inner = written.strip_prefix('(').unwrap_or(written);
                        inner.strip_suffix(')').unwrap_or(inner)
                    }
                    None => written,
                };
                collapse_spaces(text)
            })
            .filter(|text| !text.is_empty())
            .collect()
    }

    /// Accumulates the field `#^ type` annotations into `param_types` of definition `index`, in the written order (same reading for defrecord and defclass).
    fn push_field_types(&mut self, index: usize, targets: &[super::fields::FieldTarget]) {
        for target in targets {
            if let (Some(annotation), false) = (target.annotation, is_operator(self.text(target.name))) {
                let type_note = self.type_note_of_span(annotation);
                let param = ParamType { name: self.text(target.name).to_string(), type_note };
                self.definitions[index].param_types.push(param);
            }
        }
    }

    /// Reads `(defclass [decorators]? Name [bases]? "doc"? body…)` and accumulates methods and fields as nested items.
    fn class_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let rest = skip_type_params(self, skip_decorators(&items[1..]));
        let Some(name) = rest.first().and_then(|first| self.def_name(first)) else {
            return;
        };
        let (bases, body) = match rest.get(1).and_then(Form::bracket_items) {
            Some(bases) => (self.base_names(bases), rest.get(2..).unwrap_or_default()),
            None => (Vec::new(), rest.get(1..).unwrap_or_default()),
        };
        let (docstring, members) = self.body_docstring(body);
        let kind = DefinitionKind::Defclass;
        let decorators = self.decorators(&items[1..]);
        let class_name = self.push_def_with_bases(name, kind, form.span, container, docstring, Vec::new(), bases);
        let class_index = self.definitions.len() - 1;
        self.definitions[class_index].decorators = decorators;
        // Field types are read by the same reader as defrecord (fields.rs) (v9: defclass cards also show types on fields). The field definitions themselves
        // (kind field) are still accumulated by class_member as before.
        let fields: Vec<&Form> = members.iter().collect();
        let targets = super::fields::record_field_targets(self.src, &fields);
        self.push_field_types(class_index, &targets);
        for member in members {
            self.class_member(member, &class_name);
        }
    }

    /// Looks at one item of a class body: `(defn …)` is a method; `#^ T x` / `(#^ T x …)` / `(setv x …)` is a field.
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
                let head = self.head(items);
                if matches!(head, Some("defn" | "defn/a" | "defk" | "deff" | "defmacro")) {
                    self.function_def(member, items, DefinitionKind::Method, Some(class_name));
                } else if head == Some("setv") {
                    for pair in items[1..].chunks(2) {
                        self.binding_targets(member, &pair[0], DefinitionKind::Field, Some(class_name));
                    }
                } else if matches!(items.first().map(|first| &first.node), Some(Node::Annotated { .. })) {
                    if let Some(name) = self.def_name(&items[0]) {
                        self.push_def(name, DefinitionKind::Field, member.span, Some(class_name), None, Vec::new());
                    }
                }
            }
            // Bare forms such as symbols, keywords, strings and numbers are not class fields
            Node::Symbol
            | Node::Keyword
            | Node::Str { .. }
            | Node::Number
            | Node::Prefixed { .. }
            | Node::Discarded
            | Node::Tagged { .. } => {}
        }
    }

    /// Reads `(defrecord Name [bases]? "doc"? {:tags {…} :check […]}? #^ T field …)` (a field may be any of a bare symbol, a parenthesized form,
    /// or an unannotated symbol). The current defrecord macro only has the form without bases, so bases is normally empty.
    /// :tags of the head dictionary (doeff-hy's defrecord) are the definition's tags (read the same way as defk / defeffect),
    /// and each :check expression goes into checks with its spelling as written (if there is no head dictionary, checks is null).
    fn record_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let (bases, rest) = match items.get(2).and_then(Form::bracket_items) {
            Some(bases) => (self.base_names(bases), items.get(3..).unwrap_or_default()),
            None => (Vec::new(), items.get(2..).unwrap_or_default()),
        };
        let (docstring, rest) = self.body_docstring(rest);
        let (header, fields) = match rest.first() {
            Some(first) if first.is_brace() => (Some(first), &rest[1..]),
            _ => (None, rest),
        };
        let kind = DefinitionKind::Defrecord;
        let record = self.push_def_with_bases(name, kind, form.span, container, docstring, Vec::new(), bases);
        if let Some(header) = header {
            let tags = self.dict_value(header, ":tags").filter(|v| v.is_brace()).and_then(|v| self.tags_of_dict(v));
            let checks: Vec<String> = self
                .dict_value(header, ":check")
                .and_then(Form::bracket_items)
                .map(|checks| {
                    checks.iter().filter(|c| !matches!(c.node, Node::Discarded)).map(|c| self.text(c.span).to_string()).collect()
                })
                .unwrap_or_default();
            if let Some(last) = self.definitions.last_mut() {
                last.tags = tags;
                last.checks = Some(checks);
            }
        }
        // The way fields are read follows the canonical sources of defrecord / defwire (fields.rs; on the Hy side doeff_hy.declarations/field-targets).
        let members: Vec<&Form> = fields.iter().collect();
        let targets = super::fields::record_field_targets(self.src, &members);
        let record_index = self.definitions.len() - 1;
        self.push_field_types(record_index, &targets);
        for target in targets {
            if !is_operator(self.text(target.name)) {
                self.declared_names.insert(target.name.start);
                self.push_def(target.name, DefinitionKind::Field, target.member, Some(&record), None, Vec::new());
            }
        }
        // Body forms that are not fields (methods and so on) are read as the class body, as before.
        for field in fields.iter().filter(|f| matches!(f.node, Node::Seq { .. })) {
            if let Some(items) = field.paren_items() {
                if matches!(self.head(items), Some("defn" | "defn/a" | "defk" | "deff" | "defmacro")) {
                    self.class_member(field, &record);
                }
            }
        }
    }

    /// Reads `(defenum Name A B (C "value") …)`.
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

    /// From a dictionary form, reads the set of keyword keys that have string values (None if there are none — an empty dictionary does not claim anything).
    fn tags_of_dict(&self, dict: &Form) -> Option<BTreeMap<String, String>> {
        let Node::Seq { delim: Delim::Brace, items } = &dict.node else {
            return None;
        };
        let items: Vec<&Form> = items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect();
        let tags: BTreeMap<String, String> = items
            .chunks(2)
            .filter_map(|pair| match pair {
                [key, value] if matches!(key.node, Node::Keyword) => {
                    let text = match &value.node {
                        Node::Str { kind: StrKind::Plain, body } => Some(unescape(&self.src[body.start..body.end])),
                        Node::Str { kind: StrKind::Raw | StrKind::Bracket, body } => Some(self.src[body.start..body.end].to_string()),
                        _ => None,
                    }?;
                    Some((self.text(key.span).trim_start_matches(':').to_string(), text))
                }
                _ => None,
            })
            .collect();
        (!tags.is_empty()).then_some(tags)
    }

    /// The value form for `key` in a dictionary form (None if absent). Read as pairs of key and value.
    fn dict_value<'f>(&self, dict: &'f Form, key: &str) -> Option<&'f Form> {
        let Node::Seq { delim: Delim::Brace, items } = &dict.node else {
            return None;
        };
        let items: Vec<&Form> = items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect();
        let mut found = None;
        for pair in items.chunks(2) {
            if let [k, v] = pair {
                if matches!(k.node, Node::Keyword) && self.text(k.span) == key {
                    found = Some(*v);
                }
            }
        }
        found
    }

    /// The contract dictionary — the first dictionary after skipping the argument list and docstring that follow the name (up to the 4th item from the name).
    fn contract_dict<'f>(&self, items: &'f [Form]) -> Option<&'f Form> {
        let items: Vec<&'f Form> = items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect();
        for part in items.iter().skip(2).take(4) {
            if part.is_brace() {
                return Some(*part);
            }
            let skippable = matches!(part.node, Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, .. })
                || part.bracket_items().is_some();
            if !skippable {
                break;
            }
        }
        None
    }

    /// Records :effects, :pre and :post of the contract dictionary on definition `index`. `(: arg type)` is an argument type, `(: % type)` is the answer type,
    /// and any other predicate (including type annotations about names that are not arguments) is kept as a non-type contract as written.
    fn read_contract(&mut self, index: usize, dict: &Form) {
        let effects = self.dict_value(dict, ":effects").and_then(Form::bracket_items).map(|names| {
            names
                .iter()
                .filter(|n| matches!(n.node, Node::Symbol) && !is_operator(self.text(n.span)))
                .map(|n| NameRef { name: self.text(n.span).to_string(), target: None })
                .collect()
        });
        let params = self.definitions[index].params.clone();
        let mut param_types: Vec<ParamType> = Vec::new();
        let mut answer_type = None;
        let mut contracts = Vec::new();
        for (key, side) in [(":pre", ContractSide::Pre), (":post", ContractSide::Post)] {
            let Some(clauses) = self.dict_value(dict, key).and_then(Form::bracket_items) else {
                continue;
            };
            for clause in clauses.iter().filter(|c| !matches!(c.node, Node::Discarded)) {
                let placed = match (self.annotation_of(clause), side) {
                    (Some((name, type_form)), ContractSide::Pre)
                        if params.iter().any(|p| p == name) && !param_types.iter().any(|t| t.name == name) =>
                    {
                        param_types.push(ParamType { name: name.to_string(), type_note: self.type_note(type_form) });
                        true
                    }
                    (Some(("%", type_form)), ContractSide::Post) if answer_type.is_none() => {
                        answer_type = Some(self.type_note(type_form));
                        true
                    }
                    (Some(_), ContractSide::Pre | ContractSide::Post) | (None, ContractSide::Pre | ContractSide::Post) => false,
                };
                if !placed {
                    contracts.push(ContractClause { side, text: self.text(clause.span).to_string() });
                }
            }
        }
        // Types are ordered by argument order (not the order written in :pre)
        param_types.sort_by_key(|t| params.iter().position(|p| *p == t.name));
        let definition = &mut self.definitions[index];
        definition.effects = effects;
        definition.param_types = param_types;
        if answer_type.is_some() {
            definition.answer_type = answer_type;
        }
        definition.contracts = contracts;
    }

    /// If the form is `(: name type)`, returns (spelling of name, type form).
    fn annotation_of<'f>(&self, clause: &'f Form) -> Option<(&'a str, &'f Form)> {
        let items: Vec<&Form> = clause.paren_items()?.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
        match items.as_slice() {
            [head, name, type_form]
                if matches!(head.node, Node::Symbol)
                    && self.text(head.span) == ":"
                    && matches!(name.node, Node::Symbol) =>
            {
                Some((self.text(name.span), *type_form))
            }
            _ => None,
        }
    }

    /// The annotation of a type form (the spelling as written, and the names inside it — fully qualified names are filled in by qualify::link).
    fn type_note(&self, form: &Form) -> TypeNote {
        let mut names: Vec<NameRef> = Vec::new();
        self.type_names(form, &mut names);
        TypeNote { text: self.text(form.span).to_string(), names }
    }

    /// Builds an annotation from the range of a type annotation (the span returned by fields.rs).
    fn type_note_of_span(&self, span: Span) -> TypeNote {
        let mut reader = Reader::new(self.src, span.start, span.end);
        match reader.read_form() {
            Some(form) => self.type_note(&form),
            None => TypeNote { text: self.text(span).to_string(), names: Vec::new() },
        }
    }

    /// Collects the names in a type form in written order (syntax symbols, keywords, strings and numbers are excluded; the same name once).
    fn type_names(&self, form: &Form, names: &mut Vec<NameRef>) {
        match &form.node {
            Node::Symbol => {
                let text = self.text(form.span);
                if !TYPE_SYNTAX.contains(&text) && !is_operator(text) && !names.iter().any(|n| n.name == text) {
                    names.push(NameRef { name: text.to_string(), target: None });
                }
            }
            Node::Seq { items, .. } => {
                for item in items {
                    self.type_names(item, names);
                }
            }
            Node::Annotated { annotation, target } => {
                for inner in [annotation, target].into_iter().flatten() {
                    self.type_names(inner, names);
                }
            }
            Node::Prefixed { inner: Some(inner), .. } => self.type_names(inner, names),
            Node::Prefixed { inner: None, .. }
            | Node::Keyword
            | Node::Str { .. }
            | Node::Number
            | Node::Discarded
            | Node::Tagged { .. } => {}
        }
    }

    /// Reads `(defeffect Name "doc"? {:fields [a b] :answer T :tags {…}})` (doeff-hy's effect type — a frozen dataclass that always inherits EffectBase).
    /// params are the names in :fields, bases is EffectBase, and tags are :tags of the dictionary.
    fn effect_def(&mut self, form: &Form, items: &[Form], container: Option<&str>) {
        let Some(name) = items.get(1).and_then(|first| self.def_name(first)) else {
            return;
        };
        let live: Vec<&Form> = items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect();
        let mut docstring = None;
        let mut params = Vec::new();
        let mut tags = None;
        let mut param_types = Vec::new();
        let mut answer_type = None;
        for part in live.iter().skip(2).take(2) {
            if part.is_brace() {
                if let Some(fields) = self.dict_value(part, ":fields").and_then(Form::bracket_items) {
                    params = self.param_names(fields);
                    // A field `(: field type)` is typed (a field that is a bare symbol is untyped)
                    param_types = fields
                        .iter()
                        .filter_map(|field| self.annotation_of(field))
                        .map(|(name, type_form)| ParamType { name: name.to_string(), type_note: self.type_note(type_form) })
                        .collect();
                }
                answer_type = self.dict_value(part, ":answer").map(|answer| self.type_note(answer));
                tags = self.dict_value(part, ":tags").filter(|v| v.is_brace()).and_then(|v| self.tags_of_dict(v));
                break;
            }
            match string_value(self.src)(part) {
                Some(doc) => docstring = Some(doc),
                None => break,
            }
        }
        let bases = vec!["EffectBase".to_string()];
        self.push_def_with_bases(name, DefinitionKind::Defeffect, form.span, container, docstring, params, bases);
        if let Some(last) = self.definitions.last_mut() {
            last.tags = tags;
            last.param_types = param_types;
            last.answer_type = answer_type;
        }
    }

    /// Reads `(defhandler name "doc"? [params]? "doc"? (session var x init)… (Effect [fields] body…)…)`.
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
                        let effect = self.push_def(effect_span, kind, clause.span, Some(&handler), None, fields);
                        if let Some(last) = self.definitions.last_mut() {
                            last.handles = Some(NameRef { name: effect, target: None });
                        }
                    }
                }
                _ => {}
            }
        }
    }

    /// Accumulates a definition whose name comes second (such as `(deftest name …)`) and returns the name.
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

    /// Reads `(defmain [args] body…)`. It is a form without a name, so the leading `defmain` is used as the name.
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

    /// Reads `(defmcp-tool name "description" [params] body…)` (description becomes the docstring).
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

    /// Searches inside a container definition and accumulates definitions of the `heads` forms as nested items (such as law in defadr).
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

    /// Accumulates each name of a binding target (symbol, annotated, `[a b]` destructuring) as a definition.
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

    /// Takes argument names from an argument list as written (including `#* args`, `[x default]` and `#^ T x`).
    fn param_names(&self, params: &[Form]) -> Vec<String> {
        let mut names = Vec::new();
        for param in params {
            self.param_name(param, &mut names);
        }
        names
    }

    /// Takes the name from one argument.
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

    /// Takes the docstring at the head of the body. It may follow `{:pre … :post …}`, and it is a docstring only when a form follows.
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

    /// Takes the string at the head of a class-type body as the docstring (nothing needs to follow) and returns the rest.
    fn body_docstring<'f>(&self, body: &'f [Form]) -> (Option<String>, &'f [Form]) {
        match body.first().and_then(string_value(self.src)) {
            Some(doc) => (Some(doc), &body[1..]),
            None => (None, body),
        }
    }

    /// Takes the value of `:key "value"` as the docstring (`:statement` of law, `:title` of defadr).
    fn keyword_value_docstring(&self, body: &[Form], key: &str) -> Option<String> {
        body.windows(2).find_map(|pair| match pair[0].node {
            Node::Keyword if self.text(pair[0].span) == key => string_value(self.src)(&pair[1]),
            _ => None,
        })
    }

    // --- Imports and references ---------------------------------------------------------------------

    /// Walks a form, accumulating symbol occurrences as references and `(import …)` / `(require …)` as imports.
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
            Node::Seq { items, .. } if form.paren_items().is_some() => self.walk_list(items, quoting, false),
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
                // `(of list int)` and the like inside type annotations are not calls.
                if let Some(annotation) = annotation {
                    self.call_suppression += 1;
                    self.walk(annotation, quoting);
                    self.call_suppression -= 1;
                }
                if let Some(target) = target {
                    self.walk(target, quoting);
                }
            }
            Node::Tagged { inner } => {
                if let Some(inner) = inner {
                    self.walk(inner, quoting);
                }
            }
        }
    }

    /// Walks `( … )`. A leading reserved word enters neither references nor calls, and import / quote / `<-` / handler clauses /
    /// `.` / match forms are each read in their own way. `performed` = this list is fired by `<-` / `yield`.
    fn walk_list(&mut self, items: &[Form], quoting: Quoting, performed: bool) {
        if quoting != Quoting::None {
            for item in items {
                self.walk(item, quoting);
            }
            return;
        }
        // doeff type annotation `(: value type)`. A type name is not a call of the value.
        if let [colon, value, note] = items {
            if self.text(colon.span) == ":" {
                self.walk(value, Quoting::None);
                self.walk_without_calls(note);
                return;
            }
        }
        let Some(head) = self.head(items) else {
            for item in items {
                self.walk(item, quoting);
            }
            return;
        };
        let mut rest = &items[1..];
        let mut performed_index = None;
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
            // (<- (X …)) / (<- name (X …)) / (<- name T (X …)) — the last form is the one fired.
            "<-" if (1..=3).contains(&rest.len()) => performed_index = Some(rest.len() - 1),
            // `(! (X …))` is an effect bind in argument position in doeff-hy (ADR-DOE-HY-003), and is a firing form like `<-`.
            "yield" | "yield-from" | "!" if !rest.is_empty() => performed_index = Some(0),
            "defhandler" => {
                self.walk_handler_clauses(rest);
                return;
            }
            "handle" if self.is_keyword_head("handle") => {
                if let Some((body, clauses)) = rest.split_first() {
                    self.walk(body, Quoting::None);
                    self.walk_handler_clauses(clauses);
                }
                return;
            }
            "." => {
                self.walk_attribute_access(rest);
                return;
            }
            "match" => {
                if let Some((subject, clauses)) = rest.split_first() {
                    self.walk(subject, Quoting::None);
                    self.walk_match_clauses(clauses);
                }
                return;
            }
            comparison if COMPARISONS.contains(&comparison) => {
                for operand in rest {
                    self.walk_inspected(operand);
                }
                return;
            }
            _ => {}
        }
        if !self.is_keyword_head(head) {
            let pushed = self.references.len();
            self.reference(items[0].span);
            let keywords = items[1..].iter().filter(|item| matches!(item.node, Node::Keyword)).map(|item| self.text(item.span).to_string()).collect();
            let arguments = self.call_arguments(&items[1..]);
            // If the head is a method (`(.m receiver …)` or `(x.m …)`), attach to the last segment m the number of arguments excluding the receiver (model's call_arity).
            let method_head = self.text(items[0].span).starts_with('.');
            if let Some(last) = self.references[pushed..].last_mut().filter(|r| r.member) {
                last.call_arity = Some(if method_head { arguments.len().saturating_sub(1) } else { arguments.len() });
            }
            self.record_call(items[0].span, items.last().map_or(items[0].span.end, |last| last.span.end), keywords, arguments, performed);
        }
        for (index, item) in rest.iter().enumerate() {
            match item.paren_items() {
                Some(inner) if performed_index == Some(index) => self.walk_list(inner, Quoting::None, true),
                _ => self.walk(item, Quoting::None),
            }
        }
    }

    /// Walks the operands of a comparison form: symbols in a list of symbols and literals, dictionary, tuple or set are mere mentions that only inspect the value
    /// (`Reference::inspected`). If an operand is a call `( … )`, its inside is walked normally (a form that compares the result of a call —
    /// the call itself is still reported).
    fn walk_inspected(&mut self, operand: &Form) {
        match &operand.node {
            Node::Symbol => {
                self.inspecting += 1;
                self.reference(operand.span);
                self.inspecting -= 1;
            }
            Node::Seq { delim, items } if *delim != Delim::Paren => {
                for item in items {
                    self.walk_inspected(item);
                }
            }
            _ => self.walk(operand, Quoting::None),
        }
    }

    /// Walks the clauses of a handler body: the head of `(Effect [fields] body…)` is a reference but not a call.
    /// Everything else (name, docstring, arguments, `(session …)`) is walked normally.
    fn walk_handler_clauses(&mut self, clauses: &[Form]) {
        for clause in clauses {
            match clause.paren_items() {
                Some([effect, fields, body @ ..])
                    if matches!(effect.node, Node::Symbol)
                        && fields.bracket_items().is_some()
                        && !self.is_keyword_head(self.text(effect.span)) =>
                {
                    self.reference(effect.span);
                    self.walk(fields, Quoting::None);
                    for form in body {
                        self.walk(form, Quoting::None);
                    }
                }
                _ => self.walk(clause, Quoting::None),
            }
        }
    }

    /// Walks `(. obj attr (method args))`: attribute and method names are references but not calls.
    fn walk_attribute_access(&mut self, rest: &[Form]) {
        let Some((object, accessors)) = rest.split_first() else {
            return;
        };
        self.walk(object, Quoting::None);
        for accessor in accessors {
            match accessor.paren_items() {
                Some([method, args @ ..]) if matches!(method.node, Node::Symbol) => {
                    self.member_reference(method.span);
                    for arg in args {
                        self.walk(arg, Quoting::None);
                    }
                }
                _ if matches!(accessor.node, Node::Symbol) => self.member_reference(accessor.span),
                _ => self.walk(accessor, Quoting::None),
            }
        }
    }

    /// Walks a match clause `pattern [:as name] [:if guard] result`. `(Point …)` inside the pattern is
    /// a class type check and not a call, so it is not put into calls (it is put into references).
    fn walk_match_clauses(&mut self, forms: &[Form]) {
        let mut index = 0;
        while index < forms.len() {
            self.walk_without_calls(&forms[index]);
            index += 1;
            loop {
                match forms.get(index) {
                    Some(keyword) if matches!(keyword.node, Node::Keyword) && self.text(keyword.span) == ":as" => {
                        if let Some(name) = forms.get(index + 1) {
                            self.walk_without_calls(name);
                        }
                        index += 2;
                    }
                    Some(keyword) if matches!(keyword.node, Node::Keyword) && self.text(keyword.span) == ":if" => {
                        if let Some(guard) = forms.get(index + 1) {
                            self.walk(guard, Quoting::None);
                        }
                        index += 2;
                    }
                    _ => break,
                }
            }
            if let Some(result) = forms.get(index) {
                self.walk(result, Quoting::None);
            }
            index += 1;
        }
    }

    /// Walks a form accumulating references but not calls (for type annotations and match patterns).
    fn walk_without_calls(&mut self, form: &Form) {
        self.call_suppression += 1;
        self.walk(form, Quoting::None);
        self.call_suppression -= 1;
    }

    /// Accumulates the head symbol of a call as a call (`.method` forms, operators and constants are excluded). The caller is
    /// the narrowest definition whose whole-form range contains the call position.
    fn record_call(&mut self, head: Span, form_end: usize, keywords: Vec<String>, arguments: Vec<CallArgument>, performed: bool) {
        let text = self.text(head);
        if self.call_suppression > 0
            || text.is_empty()
            || text.starts_with('.')
            || text == "_"
            || CONSTANTS.contains(&text)
            || BINDING_SYNTAX.contains(&text)
            || is_operator(text)
        {
            return;
        }
        let mut offset = 0;
        let mut segments = Vec::new();
        for part in text.split('.') {
            if !part.is_empty() {
                segments.push((part, head.start + offset));
            }
            offset += part.len() + 1;
        }
        let Some(&(callee, start)) = segments.last() else {
            return;
        };
        let qualifier = match segments.len() {
            0 | 1 => None,
            count => Some(segments[..count - 1].iter().map(|(part, _)| *part).collect::<Vec<_>>().join(".")),
        };
        let caller = self
            .definition_spans
            .iter()
            .enumerate()
            .filter(|(_, span)| span.start <= start && start < span.end)
            .min_by_key(|(_, span)| span.end - span.start)
            .map(|(index, _)| index);
        self.calls.push(Call {
            callee: callee.to_string(),
            mangled: mangle(callee),
            qualifier,
            range: self.lines.range(start, start + callee.len()),
            form_range: self.lines.range(head.start, form_end.max(start + callee.len())),
            keywords,
            arguments,
            caller,
            performed,
            // qualify::link fills it in after all imports and definitions are in place
            target: None,
        });
    }

    fn call_arguments(&self, forms: &[Form]) -> Vec<CallArgument> {
        let mut arguments = Vec::new();
        let mut remaining = forms.iter();
        while let Some(form) = remaining.next() {
            let (keyword, value) = if matches!(form.node, Node::Keyword) {
                let Some(value) = remaining.next() else { break };
                (Some(self.text(form.span).trim_start_matches(':').to_string()), value)
            } else {
                (None, form)
            };
            let value = if matches!(value.node, Node::Symbol) && self.text(value.span) == "None" {
                ArgumentValue::LiteralNone
            } else if matches!(value.node, Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. }) {
                ArgumentValue::Unpacked
            } else {
                ArgumentValue::Explicit
            };
            arguments.push(CallArgument { keyword, value });
        }
        arguments
    }

    /// Reads and walks inside `{form}` of an f-string (the format of `{x:>10}` is removed from the name).
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

    /// Accumulates occurrences of a symbol, split on `.`, as references (operators, constants and `_` are excluded). The `.m` at the head of `(.m x)` and the second
    /// and later segments of a dotted name are attribute / method names (member).
    fn reference(&mut self, span: Span) {
        self.push_reference(span, false);
    }

    /// Accumulates the attribute and method names of `(. obj attr (method args))` as references (even if written as bare symbols, they are members on a value).
    fn member_reference(&mut self, span: Span) {
        self.push_reference(span, true);
    }

    /// Accumulates occurrences of a symbol, split on `.`, as references. member = every segment of the symbol is an attribute / method name on a value.
    fn push_reference(&mut self, span: Span, member: bool) {
        let text = self.text(span);
        if text.is_empty() || text == "_" || CONSTANTS.contains(&text) || is_operator(text) || self.declared_names.contains(&span.start) {
            return;
        }
        let method_head = text.starts_with('.');
        let inspected = self.inspecting > 0;
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
                member: member || method_head || qualifier.is_some(),
                target: None,
                type_only: self.call_suppression > 0,
                inspected,
                call_arity: None,
            });
            qualifier = Some(match qualifier {
                Some(prefix) => format!("{}.{}", prefix, part),
                None => part.to_string(),
            });
        }
    }

    /// Reads the arguments of `(import …)` / `(require …)`: `mod`, `mod :as m`, `mod [a b :as c]`, `mod *`,
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

    /// Accumulates the names of `[a b :as c *]` as imports.
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

    /// Accumulates one import (`range` is whichever of name, module and alias the contract decided).
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

/// Collapses runs of whitespace outside strings (including newlines) into a single space and trims both ends (to make a decorator's spelling one line).
fn collapse_spaces(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_string = false;
    let mut escaped = false;
    let mut pending_space = false;
    for c in text.chars() {
        if in_string {
            out.push(c);
            match (escaped, c) {
                (true, _) => escaped = false,
                (false, '\\') => escaped = true,
                (false, '"') => in_string = false,
                (false, _) => {}
            }
            continue;
        }
        if c.is_whitespace() {
            pending_space = !out.is_empty();
            continue;
        }
        if pending_space {
            out.push(' ');
            pending_space = false;
        }
        if c == '"' {
            in_string = true;
        }
        out.push(c);
    }
    out
}

/// Skips the decorator list of `(defn [decorators] name …)` (it is a decorator only when a name form follows).
fn skip_decorators(rest: &[Form]) -> &[Form] {
    match (rest.first().and_then(Form::bracket_items), rest.get(1).map(|next| &next.node)) {
        (Some(_), Some(Node::Symbol | Node::Annotated { .. } | Node::Keyword)) => &rest[1..],
        _ => rest,
    }
}

/// Skips `:tp [T …]` (type parameters).
fn skip_type_params<'f>(analyzer: &Analyzer, rest: &'f [Form]) -> &'f [Form] {
    match rest.first() {
        Some(form) if matches!(form.node, Node::Keyword) && analyzer.text(form.span) == ":tp" => {
            rest.get(2..).unwrap_or_default()
        }
        _ => rest,
    }
}

/// Returns a function that takes the contents from a string form that can be a docstring (ordinary strings have escapes undone,
/// bracket strings are kept as is. Both align indentation like Python's inspect.cleandoc).
fn string_value(src: &str) -> impl Fn(&Form) -> Option<String> + '_ {
    move |form: &Form| match &form.node {
        Node::Str { kind: StrKind::Plain, body } => Some(clean_doc(&unescape(&src[body.start..body.end]))),
        Node::Str { kind: StrKind::Bracket, body } => Some(clean_doc(&src[body.start..body.end])),
        _ => None,
    }
}

/// Undoes Hy string escapes (`\n`, `\t`, `\"`, `\\`, and a trailing `\` at end of line). Unknown escapes are left as is.
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

/// Aligns the indentation of a docstring (same as Python's inspect.cleandoc: remove the common indentation from the 2nd line on, and drop leading and trailing blank lines).
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

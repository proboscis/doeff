//! Extracts, from one layer module, the facts the layer rules read (tags, import targets, function definitions).
//!
//! Hy is turned into a tree of forms with doeff-indexer's reader (`doeff_indexer::hy_index::reader`) and then read -- the reader part of Hy is
//! not copied here. Python is read from rustpython's syntax tree. Both keep positions as byte ranges and convert them to UTF-16 at output time.
//!
//! The reading is aligned with agora-controllers' `scripts/module_tags.hy` (`read-modules`, `hy-definitions`, `hy-imports`, `py-imports`) so that it gives
//! the same result (so the registry keys line up).

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};
use rustpython_ast::{Constant, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use super::names::{absolute_module, hy_mangle};
use super::layers::TagReading;

/// A byte range `[start, end)` in the source.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct ByteSpan {
    pub start: usize,
    pub end: usize,
}

/// A name and its position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NamedSpan {
    pub name: String,
    pub span: ByteSpan,
}

/// One set of declared tags (made only when there is at least one key with a string value in the dict).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TagSet {
    pub context: Option<String>,
    pub role: Option<String>,
    /// All keys that have non-empty string values (for checking required keys).
    pub keys: std::collections::BTreeSet<String>,
    /// Position of the dict.
    pub span: ByteSpan,
}

/// One import target (a per-module import is the module's spelling; an import that lists names is `module.name`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportTarget {
    pub target: String,
    pub span: ByteSpan,
}

/// The language of a module.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Hy,
    Python,
}

/// The tags of one definition (the definition's name and the declared tags).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaggedDefinition {
    pub name: NamedSpan,
    pub tags: TagSet,
}

/// The facts of one module that the layer rules read.
#[derive(Debug, Clone)]
pub struct ModuleFacts {
    pub language: Language,
    /// The module's head tags (Hy's `(val MODULE-TAGS {…})`, Python's `MODULE_TAGS = {…}`).
    pub module_tags: Option<TagSet>,
    /// The definitions' :tags (for definitions of the same name, a later one overwrites the value of an earlier one, and the order is the position of the first -- the same as a Python dict).
    pub tagged: Vec<TaggedDefinition>,
    /// Definitions without tags (always empty for a Python module).
    pub untagged: Vec<NamedSpan>,
    pub imports: Vec<ImportTarget>,
    /// Top-level function and handler definitions (for the type-only layer rules).
    pub functions: Vec<NamedSpan>,
    /// The reason it could not be read (the facts that could be read are kept).
    pub errors: Vec<String>,
    /// Definitions read by the definition rules (prohibition of defn, reasons for deff, required tags) (Hy only; also inside `do` and compile sections).
    pub definitions: Vec<DefinitionFact>,
    /// Shapes of defclass (for DOEFF119; Hy only; also inside `do` and compile sections).
    pub classes: Vec<ClassFact>,
    /// Names bound by import -> module spelling (absolute). `(import m [A :as B])` is B -> m, `(import m :as n)` is n -> m, `(import m)` is m -> m.
    pub bindings: std::collections::BTreeMap<String, String>,
}

/// The shape of one defclass (to judge whether the base is an outside library and whether there is a method that does work).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassFact {
    pub name: NamedSpan,
    /// The spelling of the bases as written (keywords and their values are excluded).
    pub bases: Vec<String>,
    /// The spelling of the decorators' heads (`(dataclass :frozen True)` is `dataclass`).
    pub decorators: Vec<String>,
    pub methods: Vec<MethodFact>,
    /// Field declarations (`#^ T x` is `x: T`; `(setv x …)` without an annotation is `x`). Material passed to Jev.
    pub fields: Vec<String>,
    /// The range of the whole defclass form (the source passed to Jev).
    pub span: ByteSpan,
    /// Whether it is inside `eval-and-compile` / `eval-when-compile`.
    pub compile_time: bool,
}

/// One method of a class body (the name, and whether the body does any work).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MethodFact {
    pub name: String,
    /// Whether the body has one or more expressions other than a docstring, `...`, `pass`, and `None`.
    pub has_body: bool,
    /// Names of the self fields the method rewrites (`(setv self.x …)`, `(+= self.x …)`, `(.append self.x …)`, and so on).
    pub mutates: Vec<String>,
}

/// One Hy definition read by the definition rules.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DefinitionFact {
    /// The definition's head (defn, defk, deff, ...).
    pub head: String,
    pub name: NamedSpan,
    /// The start of the definition's form (for the line where the reason comment is looked for).
    pub start: usize,
    /// The :tags of the contract dict (for defeffect, the dict's :tags).
    pub tags: Option<TagSet>,
    /// Whether it is inside `eval-and-compile` / `eval-when-compile` (a function at macro expansion time).
    pub compile_time: bool,
}

/// The definition heads the definition rules look at.
const DEFINITION_HEADS: &[&str] = &["defn", "defn/a", "defk", "deff", "defp", "defpp", "defhandler", "defeffect", "defrecord", "defwire"];

impl ModuleFacts {
    /// All of the module's effective tags (the definitions' :tags, and the module's head tags that apply to definitions without tags).
    pub fn tag_sets(&self) -> Vec<&TagSet> {
        let mut sets: Vec<&TagSet> = self.tagged.iter().map(|t| &t.tags).collect();
        if let Some(module_tags) = &self.module_tags {
            if !self.untagged.is_empty() || self.tagged.is_empty() {
                sets.push(module_tags);
            }
        }
        sets
    }

    /// One set of tags shown on the map (the module's head tags, or if none, the first definition's tags).
    pub fn summary_tags(&self) -> Option<&TagSet> {
        self.module_tags.as_ref().or_else(|| self.tagged.first().map(|t| &t.tags))
    }
}

/// Reads only the import bindings (name -> module) of Hy source.
pub fn hy_bindings(source: &str, module: &str) -> std::collections::BTreeMap<String, String> {
    // So that, when resolving the call heads of all files in the repo (following the arguments of DOEFF126), only the import bindings are taken cheaply without reading tags or definitions.
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = HySource { src: source };
    let mut bindings = std::collections::BTreeMap::new();
    for form in &forms {
        hy.collect_bindings(form, module, &mut bindings);
    }
    bindings
}

/// Reads source with the reading appropriate to each language. module is the spelling that is the base for resolving relative imports.
pub fn read_facts(language: Language, source: &str, module: &str, reading: &TagReading) -> ModuleFacts {
    match language {
        Language::Hy => hy_facts(source, module, reading),
        Language::Python => python_facts(source, module, reading),
    }
}

/// The kind of one thing a module depends on -- `Import` is a runtime import, `Require` is a dependency of a Hy macro (at expansion time).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum DependencyKind {
    Import,
    Require,
}

/// One thing a module depends on (the import target has the same spelling as the `import` reading -- an import that lists names is `module.name`).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub struct Dependency {
    pub target: String,
    pub kind: DependencyKind,
}

/// The dependency reading of one module (imports and Hy requires). Even if there is a reason it could not be read, the dependencies that could be read are kept.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ModuleDependencies {
    pub dependencies: Vec<Dependency>,
    pub error: Option<String>,
}

/// Reads only the source's imports (read the same as the layer rules -- `collect_imports`, `python_imports`) and the modules of Hy's `(require …)`
/// (for the reverse-dependency index -- tags and definitions are not read). module is the spelling that is the base for resolving relative imports.
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
                error: (!reader.issues.is_empty()).then(|| format!("there are {} places where a parenthesis or string is not closed", reader.issues.len())),
            }
        }
        Language::Python => match parse(source, Mode::Module, "<module>") {
            Ok(Mod::Module(parsed)) => {
                let mut imports = Vec::new();
                python_imports(&parsed.body, module, &mut imports);
                ModuleDependencies { dependencies: as_dependencies(imports.into_iter().map(|i| i.target).collect(), DependencyKind::Import).collect(), error: None }
            }
            Ok(_) => ModuleDependencies { dependencies: Vec::new(), error: None },
            Err(error) => ModuleDependencies { dependencies: Vec::new(), error: Some(format!("cannot be read as Python syntax: {}", error)) },
        },
    }
}

/// From the forms Hy read, extracts only the import bindings (name -> module spelling) (to tie the types in a defk heading to definitions -- other facts are not read).
pub fn form_bindings(forms: &[Form], source: &str, module: &str) -> std::collections::BTreeMap<String, String> {
    let hy = HySource { src: source };
    let mut out = std::collections::BTreeMap::new();
    for form in forms {
        hy.collect_bindings(form, module, &mut out);
    }
    out
}

// --- Hy ---------------------------------------------------------------------------------

/// Reads Hy source and extracts tags, imports, and function definitions.
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
        errors: if reader.issues.is_empty() { Vec::new() } else { vec![format!("there are {} places where a parenthesis or string is not closed", reader.issues.len())] },
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

/// The tools for reading Hy source and the spelling of forms.
struct HySource<'a> {
    src: &'a str,
}

/// Turns the range of a form into a ByteSpan.
fn span_of(form: &Form) -> ByteSpan {
    ByteSpan { start: form.span.start, end: form.span.end }
}

/// Removes discards (`#_`) from the contents of a list -- Hy's reader does not leave discards in the list.
fn live_items(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// The contents of `( … )` (discards removed).
fn paren(form: &Form) -> Option<Vec<&Form>> {
    form.paren_items().map(live_items)
}

impl<'a> HySource<'a> {
    /// The spelling of the form.
    fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// The spelling of a symbol (None if not a symbol).
    fn symbol(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol => Some(self.text(form)),
            _ => None,
        }
    }

    /// The name of a keyword (`:tags` -> `tags`). None if not a keyword.
    fn keyword(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Keyword => Some(self.text(form).trim_start_matches(':')),
            _ => None,
        }
    }

    /// The value of a Hy String (None for f-strings and bytes, which are not String).
    fn string_value(&self, form: &Form) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(str::to_string),
            _ => None,
        }
    }

    /// Reads a Hy dict `{:context "…" :role "…"}` (None if there is no key with a string value -- an empty dict has not declared anything).
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

    /// Reads the module's top-level `(setv|val MODULE-TAGS {…})` (the first one).
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

    /// The form of the name of a definition expression (for `#^ T name`, the name side).
    fn defined_name(&self, form: &Form) -> NamedSpan {
        let target = match &form.node {
            Node::Annotated { target: Some(target), .. } => target.as_ref(),
            _ => form,
        };
        NamedSpan { name: hy_mangle(self.text(target)), span: span_of(target) }
    }

    /// Collects the definitions the definition rules read (also treats the inside of `do` and `eval-and-compile` / `eval-when-compile` as top level).
    fn definition_facts(&self, forms: &[Form], compile_time: bool, reading: &TagReading, out: &mut Vec<DefinitionFact>) {
        for form in forms {
            let Some(items) = paren(form) else { continue };
            let Some(head) = items.first().and_then(|h| self.symbol(h)) else { continue };
            match head {
                "do" => self.definition_facts_refs(&items[1..], compile_time, reading, out),
                "eval-and-compile" | "eval-when-compile" => self.definition_facts_refs(&items[1..], true, reading, out),
                _ if DEFINITION_HEADS.contains(&head) && items.len() >= 2 => {
                    // `(defn [decorators] name …)` skips the decorator list and reads the name.
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
                // `(setv name (fn …))` / `(val name (fn …))` are counted as a definition with head = "fn" (for DOEFF118, the rule on test functions).
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

    /// Collects the shapes of defclass (also treats the inside of `do` and `eval-and-compile` / `eval-when-compile` as top level).
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

    /// Reads the sequence of `(defclass [decorators]? Name [bases]? "doc"? body…)` with the head removed (None if there is no name).
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
        // How fields are read follows the source of truth of defrecord / defwire (doeff-indexer's hy_index::fields -- on the Hy side doeff_hy.declarations/field-targets).
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

    /// The spelling of the heads of the decorator list (symbols as they are; `(f …)` is f).
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

    /// The symbols in the list of bases (keywords and their values are excluded).
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

    /// If one item of the class body is a method (such as `(defn [decorators]? name [params] "doc"? body…)`), reads the name and whether the body has content.
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
            // The contract dict ({:pre …}) and expressions that do nothing (`...`, pass, None) are not counted as work.
            !form.is_brace() && !matches!(self.symbol(form), Some("..." | "pass" | "None"))
        });
        let mut mutates = Vec::new();
        for form in &body {
            self.self_mutations(form, &mut mutates);
        }
        Some(MethodFact { name, has_body, mutates })
    }

    /// Finds, in an expression, the places that rewrite a self field and pushes the field names (to tell stateful classes apart).
    fn self_mutations(&self, form: &Form, out: &mut Vec<String>) {
        /// Names of methods that rewrite their contents when called (list, dict, set, deque).
        const MUTATORS: &[&str] = &["append", "extend", "insert", "pop", "popleft", "appendleft", "remove", "clear", "update", "add", "discard", "setdefault", "sort", "reverse"];
        let push = |out: &mut Vec<String>, field: &str| {
            let field = field.split('.').next().unwrap_or(field).to_string();
            if !field.is_empty() && !out.contains(&field) {
                out.push(field);
            }
        };
        // The field name of `self.x` or `self.x.y` (None if not self).
        let self_field = |target: &Form| -> Option<String> {
            match paren(target) {
                // `(get self.x k)` and `(. self x)` are also writes to a self field.
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

    /// Collects import bindings (name -> module). Reads the same forms as `import_targets` and also records `:as` aliases.
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

    /// The shape of `definition_facts` that receives the list of references of forms.
    fn definition_facts_refs(&self, forms: &[&Form], compile_time: bool, reading: &TagReading, out: &mut Vec<DefinitionFact>) {
        for form in forms {
            self.definition_facts(std::slice::from_ref(*form), compile_time, reading, out);
        }
    }

    /// Reads the tags of each top-level definition and the function definitions.
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

    /// Reads the :tags value of a dict form as pairs of key and value (if the same key appears twice, the later one).
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

    /// Looks at the elements after the name in order, and reads the :tags of the first dict after the skippable items (limit = how many to look at after the name).
    /// If something unskippable comes before the dict, there are no tags.
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

    /// Reads the :tags of the contract dict -- the first dict after skipping the argument list and docstring after the name (up to the 4th from the name).
    fn contract_tags(&self, items: &[&Form]) -> Option<TagSet> {
        self.first_dict_tags(items, 4, |part| self.string_value(part).is_some() || part.bracket_items().is_some())
    }

    /// Reads the :tags of the leading dict after the name (up to the 2nd from the name; skips only a docstring) -- `(defeffect name "doc"? {:fields […] :answer type :tags {…}})`
    /// and `(defrecord name "doc"? {:tags {…} :check […]} fields …)` (a defrecord without a leading dict has fields coming next, so no tags).
    fn header_tags(&self, items: &[&Form]) -> Option<TagSet> {
        self.first_dict_tags(items, 2, |part| self.string_value(part).is_some())
    }

    /// Reads all `(import …)` expressions in a form (also counts imports inside functions and inside entry-point clauses).
    fn collect_imports(&self, form: &Form, module: &str, out: &mut Vec<ImportTarget>) {
        self.visit_headed(form, "import", &mut |args| self.import_targets(args, module, out));
    }

    /// Visits all expressions in a form tree whose head is `head` and passes the argument list (also inside functions, entry-point clauses, and quote -- the range of import reading).
    /// Does not descend into a visited expression.
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

    /// Reads module spellings from the arguments of a `(require …)` expression: `mod [a b]`, `mod *`, `mod :as m`, `mod :macros [..] :readers [..]`.
    /// Lists of names, `*`, and keywords with their values are not modules.
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

    /// Reads the arguments of an import expression: `mod`, `mod :as m`, `mod [a b]` (an import that lists names is per name).
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

/// Pushes the tags of a definition (the same name overwrites the value while keeping its position -- the same as assignment into a Python dict).
fn record_tagged(tagged: &mut Vec<TaggedDefinition>, name: NamedSpan, tags: TagSet) {
    match tagged.iter_mut().find(|t| t.name.name == name.name) {
        Some(existing) => existing.tags = tags,
        None => tagged.push(TaggedDefinition { name, tags }),
    }
}

// --- Python -----------------------------------------------------------------------------

/// Reads Python source and extracts tags, imports, and function definitions (syntax errors are pushed into errors and empty facts are returned).
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
            facts.errors.push(format!("cannot be read as Python syntax: {}", error));
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

/// If the statement is `MODULE_TAGS = {…}`, reads the tags (only pairs whose keys and values are both constants).
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

/// Turns a constant into the spelling of Python's `str()`.
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

/// Finds the position of the name in `def name` (the start of the statement if not found).
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

/// Reads all imports in a list of statements (also inside functions, classes, if, try, and so on -- the same range as `ast.walk`).
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

// --- Use of names (DOEFF120 -- where JsonValue is used)---------------------------------------------

/// Collects all positions of uses of the names `names` in the source (in position order).
///
/// - Hy: reader symbols whose last `.` segment is the name (import, `(setv name …)`, `#^ name x`, `(: x name)`, `#(str name)` ...).
///   Strings, comments, docstrings, and forms discarded with `#_` are not counted. The position is the last segment of the symbol.
/// - Python: lexical names (names, attribute names, import names), and words inside the strings of annotations (arguments, return values, `x: T`) and type aliases (`X: TypeAlias = "…"`, `type X = …`)
///   (those not adjacent to identifier characters). Docstrings and comments are not counted.
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
            // Stop where the lexing can no longer read (names up to the readable point are counted).
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

/// From a Hy form tree, collects the symbols whose last segment is the name.
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

/// From a list of Python statements, finds the strings inside annotation and type-alias expressions and collects the words of names in them (also inside functions, classes, if, try, and so on).
fn python_type_strings(source: &str, body: &[Stmt], names: &[&str], out: &mut Vec<ByteSpan>) {
    let in_expr = |expr: &Expr, out: &mut Vec<ByteSpan>| python_expr_strings(source, expr, names, out);
    for stmt in body {
        match stmt {
            Stmt::AnnAssign(node) => {
                in_expr(&node.annotation, out);
                // The value of `X: TypeAlias = "…"` is a type expression (a forward reference written as a string).
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

/// Collects the words in the strings inside a function argument's annotation.
fn python_arguments_strings(source: &str, args: &rustpython_ast::Arguments, names: &[&str], out: &mut Vec<ByteSpan>) {
    let with_default = args.posonlyargs.iter().chain(args.args.iter()).chain(args.kwonlyargs.iter()).map(|a| &a.def);
    let bare = args.vararg.iter().chain(args.kwarg.iter()).map(|a| a.as_ref());
    for arg in with_default.chain(bare) {
        if let Some(annotation) = &arg.annotation {
            python_expr_strings(source, annotation, names, out);
        }
    }
}

/// Finds the strings inside a type expression (`"JsonValue"`, `dict[str, "JsonValue"]`, `"A | B"`) and collects the positions of the words of names inside that literal.
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

/// The positions (byte ranges inside text) of the words of names `names` in text (those not adjacent to identifier characters).
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

/// Turns rustpython's range into a ByteSpan.
fn text_span(range: rustpython_parser::text_size::TextRange) -> ByteSpan {
    ByteSpan { start: usize::from(range.start()), end: usize::from(range.end()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::layers::tag_reading;

    /// The default tag reading (the value when the config section is empty).
    fn reading() -> TagReading {
        tag_reading(None, None)
    }

    #[test]
    fn hy_tags_from_contract_dict_module_tags_and_defeffect() {
        let src = r#"
(defk plan [x] "plan" {:pre [] :tags {:context "billing" :role "program"}} x)
(defk #^ int typed [x] {:tags {}} x)
(defhandler h #_ ignored {:tags {:context "billing" :role "protocol"}} (E [e k] (k 1)))
(defeffect Charge "charge" {:fields [amount] :answer int :tags {:context "billing" :role "intent"}})
(defeffect Old :fields [amount] :tags {:context "billing" :role "intent"})
(defrecord Row "row" {:tags {:context "billing" :role "type"} :check [(> n 0)]} #^ int n)
(defrecord Bare #^ str key)
(defn helper [] 1)
(val MODULE-TAGS {:context "billing" :role "judgment"})
(defn [do] decorated [x] x)
"#;
        let facts = read_facts(Language::Hy, src, "app.core.m", &reading());
        let tagged: Vec<(&str, Option<&str>)> = facts.tagged.iter().map(|t| (t.name.name.as_str(), t.tags.role.as_deref())).collect();
        assert_eq!(tagged, vec![("plan", Some("program")), ("h", Some("protocol")), ("Charge", Some("intent")), ("Row", Some("type"))]);
        // An empty :tags has not declared anything. Reads the name after the #^ type annotation.
        let untagged: Vec<&str> = facts.untagged.iter().map(|n| n.name.as_str()).collect();
        // A shape of key-and-value sequence that doeff-hy does not accept (Old) is not read as tags (same as module_tags.hy).
        // A defrecord without a leading dict is a definition that wrote no tags.
        assert_eq!(untagged[..4], ["typed", "Old", "Bare", "helper"]);
        assert_eq!(facts.module_tags.as_ref().and_then(|t| t.role.as_deref()), Some("judgment"));
        // The module's head tags apply to definitions without tags.
        assert_eq!(facts.tag_sets().len(), 5);
        assert_eq!(facts.functions.len(), 5);
    }

    #[test]
    fn hy_imports_nested_relative_and_aliased() {
        let src = "(import app.intent.charge [Charge :as C])\n(import .sibling)\n(defn f [] (import subprocess) '(import quoted))\n(import os.path :as p)\n";
        let facts = read_facts(Language::Hy, src, "app.core.m", &reading());
        let targets: Vec<&str> = facts.imports.iter().map(|i| i.target.as_str()).collect();
        // The alias of `[a :as b]` is also counted in the spelling (same as hy-imports of module_tags.hy).
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
        // The name f points to the name's position, not a character inside `def`.
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
        // Hy: imports, annotations, the last segment of dotted names, and inside quote are counted; strings, comments, #_, and other names (JsonValues) are not.
        let hy = "(import m [JsonValue])\n; JsonValue\n(setv x #^ wire.JsonObject y)\n\"JsonValue\"\n#_ JsonValue\n(setv JsonValues 1 q '(JsonValue))\n";
        assert_eq!(text(hy, name_occurrences(Language::Hy, hy, &names)), vec!["JsonValue", "JsonObject", "JsonValue"]);
        // Python: names, attributes, import names, and words inside the strings of annotations and type aliases. Docstrings, comments, f-strings, and parts of words are not counted.
        let py = "\"\"\"JsonValue\"\"\"\nfrom m import JsonValue\n# JsonValue\ndef f(a: \"MyJsonValue\", b: \"list[JsonObject]\") -> \"JsonValue\":\n    return f\"{a} JsonValue\"\nx = m.JsonObject\n";
        assert_eq!(text(py, name_occurrences(Language::Python, py, &names)), vec!["JsonValue", "JsonObject", "JsonValue", "JsonObject"]);
    }

    #[test]
    fn class_fields_for_doeff204_match_the_shared_case_table() {
        // The list of a class's fields passed to DOEFF204 is read with the same reader as defrecord / defwire (doeff-indexer's hy_index::fields).
        // The test of the Hy-side source of truth (doeff_hy.declarations/field-targets) also reads the same table.
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

//! Smell rules (DOEFF121 to 125) — pick up forms in business Hy definitions that could be written with types and effects but were written by hand.
//!
//! Adds the smell rules. The subject matter is decide-tag in agora-controllers' controllers/kanban/core/tag_judgment.hy.
//! All of them compare deterministic shapes, and the default severity is warning (ADR-DOE-HY-007 R9 — raised from info once Absent / Raise entered the main line).
//!
//! - DOEFF121: a definition in a judgment layer (the setting `smells.shape_check_layers`) checks the shape of its input with `(.get x "field")` using a string key and `isinstance` on that field
//!   (a JSON shape check has crept into core).
//! - DOEFF122: a match arm receives a failure type (taken from declarations — defrecord's `:failure True` and defeffect's `:failure` / `:absent`) and
//!   merely returns the received value as it is or re-wrapped (a hand-written re-raise of an exception).
//! - DOEFF123: `(return x)` comes right after `(<- x T (f …))`, and x is used nowhere else.
//! - DOEFF124: two or more fields of the same value are joined with strings into one string by `+` (or an f-string).
//! - DOEFF125: `(:= xs (+ xs #(…)))` or `(setv xs (+ xs […]))` inside for / while (an accumulator rebuilt each time).
//!
//! Hy is turned into a tree of forms with doeff-indexer's reader before being read (the Hy reading part is not copied).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};

use super::facts::{read_facts, ByteSpan, Language};
use super::layers::TagReading;
use super::names::{hy_mangle, module_of};

/// The kinds of smell, and the material inserted into the explanation text (a closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SmellKind {
    /// DOEFF121: isinstance checks a field read with a string key.
    ShapeCheck { field: String, holder: String },
    /// DOEFF122: an arm for a failure type merely returns the received value again.
    FailureRethrow { failure_type: String, subject: String },
    /// DOEFF123: a `<-` that merely binds and returns immediately.
    BindThenReturn { name: String },
    /// DOEFF124: fields of the same value joined into a string.
    FieldsJoined { value: String, fields: Vec<String> },
    /// DOEFF125: an accumulator rebuilt on each pass of a loop.
    RebuiltAccumulator { name: String },
}

/// One smell (in which definition, and where).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Smell {
    /// The definition's name (already mangled).
    pub definition: String,
    pub span: ByteSpan,
    pub kind: SmellKind,
}

impl Smell {
    /// Detail of the registry key (`<definition>::<name>`) — smells of the same name in the same definition count as one.
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

/// The types that an effect declared in defeffect as absent (`:absent`) or failure (`:failure`) (names including the module, already mangled).
/// `<-` and `!` do not bind these types as answer values but let them escape as Absent / Raise (ADR-DOE-CORE-EFFECTS-003 R5). DOEFF175 consults this.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct EffectOutcomes {
    pub absent: BTreeSet<String>,
    pub failure: BTreeSet<String>,
}

impl EffectOutcomes {
    /// `Absent` if the type (name including the module) is declared absent, `Failure` if declared failure (None if not declared).
    pub fn declared(&self, qualified: &str) -> Option<DeclaredAs> {
        if self.absent.contains(qualified) {
            Some(DeclaredAs::Absent)
        } else if self.failure.contains(qualified) {
            Some(DeclaredAs::Failure)
        } else {
            None
        }
    }
}

/// The kind of declaration (defeffect's `:absent` or `:failure`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeclaredAs {
    Absent,
    Failure,
}

/// The set of failure types (names including the module, already mangled — `controllers.core.kanban_write_rules.Refusal`) — collected from declarations.
/// They are not inferred from type names. Even if a type of the same name exists in another module, only the declared one becomes a failure type.
/// `effects` holds the declarations per defeffect (effect name including the module → absent and failure types) — used by DOEFF175.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct FailureTypes {
    names: BTreeSet<String>,
    #[serde(default)]
    effects: BTreeMap<String, EffectOutcomes>,
}

/// Material for resolving names — the file's module spelling and import bindings (name → module).
#[derive(Debug, Clone, Copy)]
pub struct Scope<'a> {
    pub module: &'a str,
    pub bindings: &'a BTreeMap<String, String>,
}

impl Scope<'_> {
    /// Resolve a type's spelling to a name including the module — T in `(import m [T])` is `m.T`, `n.T` in `(import m :as n)` is `m.T`,
    /// and a bare name with no binding is a definition in this file (`<module>.T`).
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

/// Something that can answer whether a name is a failure type — either a set collected from the whole repo (`FailureTypes`), or one collected, when asked, only from the declaration files that spell the type's name (`SpelledFailures`; single-file run).
pub trait FailureLookup {
    /// Whether the name including the module is a failure type.
    fn is_failure(&self, qualified: &str) -> bool;

    /// The types that the effect with the name including the module declared absent or failure in defeffect (None for an effect with no declaration or a name that is not an effect).
    fn effect_outcomes(&self, qualified_effect: &str) -> Option<EffectOutcomes>;
}

impl FailureLookup for FailureTypes {
    fn is_failure(&self, qualified: &str) -> bool {
        self.contains(qualified)
    }

    fn effect_outcomes(&self, qualified_effect: &str) -> Option<EffectOutcomes> {
        self.effects.get(qualified_effect).cloned()
    }
}

impl FailureTypes {
    /// Build from a list of names including the module (for tests).
    pub fn of(names: &[&str]) -> Self {
        FailureTypes { names: names.iter().map(|n| n.to_string()).collect(), effects: BTreeMap::new() }
    }

    /// Whether the name including the module is a failure type.
    pub fn contains(&self, qualified: &str) -> bool {
        self.names.contains(qualified)
    }

    /// Add another set.
    pub fn extend(&mut self, other: FailureTypes) {
        self.names.extend(other.names);
        for (effect, outcomes) in other.effects {
            let merged = self.effects.entry(effect).or_default();
            merged.absent.extend(outcomes.absent);
            merged.failure.extend(outcomes.failure);
        }
    }

    /// The number of declarations (for reporting).
    pub fn len(&self) -> usize {
        self.names.len()
    }

    /// Whether it is empty.
    pub fn is_empty(&self) -> bool {
        self.names.is_empty() && self.effects.is_empty()
    }
}

/// The kind of failure-type facts (the name of the per-file cache location) — the whole run (`failure_types_for`) and the single-file run (`spelled_failures`) use the same location.
pub const FAILURE_TYPES_KIND: &str = "failure-types";

/// A file's failure types are decided by its contents and the tag-reading method (settings) — the fingerprint of the reading method is the cache mark's `key`.
pub fn failure_types_key(reading: &TagReading) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(format!("{reading:?}").as_bytes()))
}

/// Whether the contents are of a file that may hold failure-type declarations (`defrecord`'s `:failure True` and `defeffect`'s `:failure` / `:absent`) — a file with neither spelling holds no declarations.
pub fn has_failure_declaration_spelling(source: &str) -> bool {
    source.contains(":failure") || source.contains(":absent")
}

/// The failure types declared by one Hy file whose contents were read (empty is possible — a file that has the spelling but is not a declaration).
pub fn failure_types_of_source(rel: &str, source: &str, reading: &TagReading) -> FailureTypes {
    let module = module_of(rel);
    let facts = read_facts(Language::Hy, source, &module, reading);
    failure_types_in(source, Scope { module: &module, bindings: &facts.bindings })
}

/// The failure types declared by one Hy file (None for a file that does not spell `:failure` or `:absent`, or has no declarations). The whole run and the single-file run use the same construction.
pub fn failure_types_of_file(rel: &str, path: &Path, reading: &TagReading) -> Option<FailureTypes> {
    let source = std::fs::read_to_string(path).ok()?;
    if !has_failure_declaration_spelling(&source) {
        return None;
    }
    let found = failure_types_of_source(rel, &source, reading);
    (!found.is_empty()).then_some(found)
}

/// The failure-type declarations of one source — the names of `(defrecord name "doc"? {… :failure True …} …)`, and
/// the types in the lists of `(defeffect name "doc"? {… :failure [A B] :absent [C] …})` (resolved by scope to names including the module).
pub fn failure_types_in(source: &str, scope: Scope<'_>) -> FailureTypes {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut names = BTreeSet::new();
    let mut effects: BTreeMap<String, EffectOutcomes> = BTreeMap::new();
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
                let mut outcomes = EffectOutcomes::default();
                for key in [":failure", ":absent"] {
                    let types = hy.dict_value(contract, key).and_then(|v| v.bracket_items()).map(live_items).unwrap_or_default();
                    let qualified: BTreeSet<String> = types.into_iter().filter_map(|t| hy.symbol(t)).map(|t| scope.qualify(t)).collect();
                    names.extend(qualified.iter().cloned());
                    if key == ":failure" { outcomes.failure = qualified } else { outcomes.absent = qualified }
                }
                // Per-effect declarations (DOEFF175 — which effect declared which types absent or failure).
                if let (Some(effect), false) = (items.get(1).and_then(|n| hy.symbol(n)), outcomes.absent.is_empty() && outcomes.failure.is_empty()) {
                    effects.insert(format!("{}.{}", scope.module, hy_mangle(effect)), outcomes);
                }
            }
            _ => {}
        }
    }
    FailureTypes { names, effects }
}

/// Pick up all smells of one source. `shape_checks` is whether to apply DOEFF121 (only files in judgment layers). scope is for resolving the types of match arms.
pub fn smells_in(source: &str, scope: Scope<'_>, failure: &dyn FailureLookup, shape_checks: bool) -> Vec<Smell> {
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
            // Only the first of the same detail in the same definition (so there is one key).
            if !found.iter().any(|f| f.kind_id() == smell.kind_id() && f.detail() == smell.detail()) {
                found.push(smell);
            }
        }
    }
    found
}

impl Smell {
    /// The kind's number (for judging overlap).
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

/// The contents of `( … )` (excluding discarded reads).
pub(super) fn live(form: &Form) -> Option<Vec<&Form>> {
    form.paren_items().map(live_items)
}

/// Remove discarded reads (`#_`) from a sequence's contents.
pub(super) fn live_items(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// The children of a form (the contents of a sequence, the target of an annotation, the contents of a prefix).
pub(super) fn children(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { items, .. } => live_items(items),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => vec![inner.as_ref()],
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().map(|b| b.as_ref()).collect(),
        _ => Vec::new(),
    }
}

/// Make a form's range a ByteSpan.
pub(super) fn span_of(form: &Form) -> ByteSpan {
    ByteSpan { start: form.span.start, end: form.span.end }
}

/// The head of a definition (the definitions to search for smells).
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler"];

/// The head of a loop (DOEFF125's "each time"). Comprehensions (lfor, gfor) do not rebuild an accumulator, so they are not counted.
const LOOP_HEADS: &[&str] = &["for", "while"];

/// The head of a binding (the forms DOEFF121 and DOEFF122 read as "put a value in a name").
const BINDING_HEADS: &[&str] = &["val", "var", "setv", "setx", ":=", "<-"];

/// Material for DOEFF122 — the set of failure types, and the scope that resolves type spellings.
struct Judge<'a> {
    failure: &'a dyn FailureLookup,
    scope: Scope<'a>,
}

/// A Hy source, and the tool that reads form spellings.
pub(super) struct Hy<'a> {
    pub(super) src: &'a str,
}

impl<'a> Hy<'a> {
    /// A form's spelling.
    pub(super) fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// A symbol's spelling (None if not a symbol).
    pub(super) fn symbol(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol => Some(self.text(form)),
            _ => None,
        }
    }

    /// The spelling of a sequence's head (a symbol or keyword — the `:=` of `(:= x v)` is read as a keyword).
    pub(super) fn head(&self, form: &Form) -> Option<&'a str> {
        let items = form.paren_items()?;
        let first = items.iter().find(|i| !matches!(i.node, Node::Discarded))?;
        match first.node {
            Node::Symbol | Node::Keyword => Some(self.text(first)),
            _ => None,
        }
    }

    /// The contents of a literal string (excluding f-strings and bytes).
    fn string_literal(&self, form: &Form) -> Option<&'a str> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end),
            _ => None,
        }
    }

    /// Whether the head dict comes right after the name (and docstring) (the position of defrecord's head dict).
    fn header_position(&self, rest: &[&Form]) -> bool {
        match rest {
            [first, ..] if first.is_brace() => true,
            [doc, second, ..] if self.string_literal(doc).is_some() => second.is_brace(),
            _ => false,
        }
    }

    /// The value of a dict's key.
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

    /// The names and forms of top-level definitions (including inside `do` and `eval-and-compile`).
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

    /// For `(.get x "field")`, return (x's spelling, field).
    fn string_key_get(&self, form: &Form) -> Option<(String, String)> {
        let items = live(form)?;
        match items.as_slice() {
            [head, holder, key, ..] if self.symbol(head) == Some(".get") => {
                Some((self.text(holder).to_string(), self.string_literal(key)?.to_string()))
            }
            _ => None,
        }
    }

    /// DOEFF121: pick up isinstance on a field read with a string key within a definition.
    fn shape_checks(&self, definition: &Form, name: &str, out: &mut Vec<Smell>) {
        // 1. A field put in a name: (val n (.get x "field")) and so on.
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
        // 2. (isinstance name T) or (isinstance (.get x "field") T).
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

    /// The spelling of a sequence's head (a symbol or keyword) from one form.
    pub(super) fn head_text(&self, form: &Form) -> Option<&'a str> {
        match form.node {
            Node::Symbol | Node::Keyword => Some(self.text(form)),
            _ => None,
        }
    }

    /// Visit a form and its descendants in order from the front.
    fn each(&self, form: &Form, visit: &mut dyn FnMut(&Form)) {
        visit(form);
        for child in children(form) {
            self.each(child, visit);
        }
    }

    /// Whether an expression refers to any of names (`name` or `name.field`).
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

    /// The number of times the symbol name (`name` or `name.field`) appears within a definition.
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

    /// Descend the tree while picking up DOEFF122 to 125. `in_loop` is whether we are inside for / while.
    fn walk(&self, form: &Form, name: &str, judge: &Judge<'_>, definition: &Form, in_loop: bool, out: &mut Vec<Smell>) {
        let head = self.head(form);
        if let Some(items) = live(form) {
            match head {
                Some("match") => self.failure_rethrows(&items, name, judge, out),
                Some("+") => self.joined_fields(form, &items, name, out),
                Some(":=" | "setv" | "setx") if in_loop => self.rebuilt_accumulator(form, &items, name, out),
                _ => {}
            }
            // DOEFF123: two adjacent children (<- x …) (return x).
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

    /// DOEFF122: the arm of `(match subject (failure-type …) arm …)` merely returns the received value (or a value made from it).
    fn failure_rethrows(&self, items: &[&Form], name: &str, judge: &Judge<'_>, out: &mut Vec<Smell>) {
        let Some(subject) = items.get(1) else { return };
        let subject_name = self.symbol(subject).map(str::to_string);
        let mut index = 2;
        while index < items.len() {
            let pattern = items[index];
            index += 1;
            let mut bound: BTreeSet<String> = subject_name.iter().cloned().collect();
            // Skip `:as name` and `:if condition` (the name of :as is an alias for the received value).
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
            // The question of whether it is a failure type is asked last, after it is known that the arm has the shape of merely returning the received value (the set of failure types is collected from declarations across the whole repo — a single-file run
            // collects it only when asked. Most arms, whose shape fails first, never consult the set).
            // A name bound in the pattern is also part of the received value.
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
            // A name made from the received value inside the arm (`(<- x T (rejected (+ said.reason …)))`) is also a re-wrapping of the received value.
            for form in before {
                let Some(parts) = live(form) else { continue };
                let is_binding = parts.first().and_then(|h| self.head_text(h)).is_some_and(|h| BINDING_HEADS.contains(&h));
                if let (true, Some(target), Some(value)) = (is_binding, parts.get(1).and_then(|t| self.symbol(t)), parts.last()) {
                    if self.refers(value, &bound) {
                        bound.insert(target.to_string());
                    }
                }
            }
            if self.refers(value, &bound) && judge.failure.is_failure(&judge.scope.qualify(type_name)) {
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

    /// DOEFF123: `(return x)` immediately after `(<- x …)`, with x used nowhere else in the definition.
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

    /// DOEFF124: `(+ …)` joins strings with two or more fields of the same value (`v.a`, `(str v.b)`).
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

    /// The shape of DOEFF124's f-string: `f"{v.a}: {v.b}"`.
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

    /// Push DOEFF124 if there are two or more fields of the same value.
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

    /// DOEFF125: `(:= xs (+ xs #(…)))` or `(setv xs (+ xs […]))` inside a loop.
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

/// Whether the path is a Hy file (the population from which failure-type declarations are collected).
pub fn is_hy(path: &Path) -> bool {
    matches!(path.extension().and_then(|e| e.to_str()), Some("hy" | "hyk" | "hyp"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Turn the smells into a list of (kind number, detail) (module m, no imports — failure types are passed as `m.<name>`).
    fn found(source: &str, failure: &[&str], shape: bool) -> Vec<(u8, String)> {
        let bindings = BTreeMap::new();
        smells_in(source, Scope { module: "m", bindings: &bindings }, &FailureTypes::of(failure), shape).iter().map(|s| (s.kind_id(), s.detail())).collect()
    }

    #[test]
    fn failure_types_come_from_declarations_not_names() {
        let source = r#"(defrecord Refusal "refusal" {:failure True} #^ str reason)
(defrecord Accepted {:tags {:context "c" :role "type"}} #^ str word)
(defrecord Rejection #^ str reason)
(defeffect ReadRow "reads a row" {:fields [key] :answer (| Row Missing Unreachable) :absent [Missing] :failure [store.Unreachable]})
"#;
        let bindings: BTreeMap<String, String> = [("store".to_string(), "app.store".to_string())].into_iter().collect();
        let types = failure_types_in(source, Scope { module: "app.rules", bindings: &bindings });
        // defrecord's declaration is a name in this module, while defeffect's list is names resolved by import.
        assert!(types.contains("app.rules.Refusal") && types.contains("app.rules.Missing") && types.contains("app.store.Unreachable"));
        // Even if a name looks like a failure, it is not a failure type without a declaration. The same name in another module is also a different type.
        assert!(!types.contains("app.rules.Rejection") && !types.contains("app.rules.Accepted") && !types.contains("app.webapp.Refusal"));
        assert_eq!(types.len(), 3);
        // Resolve the spelling of an arm's type: an imported name, a module of an alias, and a bare name (a definition in this file).
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
        // isinstance on a typed value, and get without a key, do not match.
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
    (Refusal) (do (log "refused") (return (Plan :why "fixed text")))
    (Accepted) None)
  (match (verdict x)
    (Refusal) :as r (return r)
    _ None))"#;
        let hits: Vec<(u8, String)> = found(source, &["m.Refusal"], false).into_iter().filter(|(k, _)| *k == 2).collect();
        // said (re-wrapped), again (a field bound in a pattern), and the alias of :as match. Arms that do not use the received value, and success arms, do not match.
        assert_eq!(hits, vec![(2, "decide::said".to_string()), (2, "decide::again".to_string()), (2, "decide::Refusal".to_string())]);
        // Without declarations (the set of failure types is empty), nothing is emitted.
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
        // Sums of numbers (a + with no strings) and fields of different values do not match.
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

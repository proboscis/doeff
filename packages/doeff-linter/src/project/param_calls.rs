//! The second shape of DOEFF126: a bare call of a function received as an argument — but picked up only when the caller passes a defk or fnk as that argument.
//!
//! The fact (fixed in agora's L1550): `rows-by-text` called its argument `field-of` bare as `(setv value (field-of row))`,
//! but the caller passed an fnk (which calls `payload-of`) there. The value is always a Program, so `isinstance value str` is always false and
//! the index was always empty, a real defect. The coordinator's request of 2026-09-28.
//!
//! How the flow is followed (the range that can be picked up):
//! 1. Among all calls `(g … argument …)` in the repo, collect those where the argument is the name of a repo defk or an `(fnk …)` (g is resolved to its module through imports and definition locations;
//!    for a positional argument, note which position it is, and for a keyword argument, note its name).
//! 2. Look up the argument's name in the parameter list of the callee g's top-level definition (defk, deff, defn, defn/a); if a call `(name …)` headed by that name in the body
//!    is outside the places that pass it on as a Program (the right side of `(<- …)`, `(! …)`, `(return …)`, `(yield …)`, an argument of a call that accepts a Program), make that call
//!    in the callee a violation (a bind of the name `(setv v (name …))` is also a violation — the caller passes a defk, so what is bound is a Program, not the answer).
//!
//! The range that cannot be picked up (written in ADR-DOE-HY-007 R14): a caller that puts a defk into a variable or field before passing it, or wraps it with partial or the like, a callee that
//! passes that argument on to yet another function that calls it bare there (only one level is followed), a callee that is a method, a nested function, or something that cannot be looked up by name, and a caller outside the repo.

use std::collections::{BTreeMap, BTreeSet};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};

use super::bare_calls::DefkNames;
use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, live_items, span_of, Hy, Scope};

/// Argument position (for a positional argument, which position; for a keyword argument, the mangled name).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum Slot {
    Position(usize),
    Keyword(String),
}

/// One fact that a caller passed a function returning a Program as an argument.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PassedProgram {
    pub slot: Slot,
    /// The spelling of what was passed (a defk name, or `fnk`).
    pub passed: String,
    /// Where it was passed (`<definition>` of `<path>`).
    pub caller: String,
}

/// Per callee (the name including the module), the arguments that receive a function returning a Program.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct ProgramParams {
    by_callee: BTreeMap<String, Vec<PassedProgram>>,
}

/// Among the arguments of a call, the forms that may be passing a Program — a symbol (as spelled) or `(fnk …)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Argument<'a> {
    Symbol(&'a str),
    Fnk,
}

/// For the calls of one source whose callee (the name resolved to its module) matches `wants`, hands all the symbol or `(fnk …)` arguments to `visit`
/// (callee, argument position, argument, description of where it was passed — rel is for the description). In call order, with one call's arguments in their order, and the calls descended from a call after it.
/// It does not ask whether the symbol is a defk — that is asked of the caller (the collection across the whole repo, or only the looked-up modules).
pub fn each_argument<'s>(source: &'s str, rel: &str, scope: Scope<'_>, wants: &dyn Fn(&str) -> bool, visit: &mut dyn FnMut(&str, Slot, Argument<'s>, &str)) {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    for (definition, form) in top_definitions(&hy, &forms) {
        let caller = format!("{} in {}", rel, definition);
        arguments_in(&hy, form, scope, wants, &caller, visit);
    }
}

/// Looks at a form and the calls descended from it.
fn arguments_in<'s>(
    hy: &Hy<'s>,
    form: &Form,
    scope: Scope<'_>,
    wants: &dyn Fn(&str) -> bool,
    caller: &str,
    visit: &mut dyn FnMut(&str, Slot, Argument<'s>, &str),
) {
    if let Some(items) = live(form) {
        let head = items.first().and_then(|h| hy.symbol(h)).filter(|h| !h.starts_with('.'));
        if let Some(head) = head {
            let callee = scope.qualify(head);
            if wants(&callee) {
                let mut position = 0;
                let mut index = 1;
                while index < items.len() {
                    let (slot, arg) = match items[index].node {
                        Node::Keyword => {
                            let name = hy.text(items[index]).trim_start_matches(':');
                            index += 1;
                            (Slot::Keyword(hy_mangle(name)), items.get(index).copied())
                        }
                        _ => {
                            position += 1;
                            (Slot::Position(position - 1), Some(items[index]))
                        }
                    };
                    index += 1;
                    let argument = arg.and_then(|a| match (hy.symbol(a), hy.head(a)) {
                        (Some(symbol), _) => Some(Argument::Symbol(symbol)),
                        (None, Some("fnk")) => Some(Argument::Fnk),
                        _ => None,
                    });
                    if let Some(argument) = argument {
                        visit(&callee, slot, argument, caller);
                    }
                }
            }
        }
    }
    for child in children(form) {
        arguments_in(hy, child, scope, wants, caller, visit);
    }
}

impl ProgramParams {
    /// From the calls of one source, accumulates those passing a defk or fnk as an argument (rel is for the description of where it was passed).
    pub fn collect(&mut self, source: &str, rel: &str, scope: Scope<'_>, defks: &DefkNames) {
        each_argument(source, rel, scope, &|_| true, &mut |callee, slot, argument, caller| {
            let passed = match argument {
                Argument::Symbol(symbol) if defks.contains(&scope.qualify(symbol)) => symbol.to_string(),
                Argument::Symbol(_) => return,
                Argument::Fnk => "fnk".to_string(),
            };
            self.by_callee.entry(callee.to_string()).or_default().push(PassedProgram { slot, passed, caller: caller.to_string() });
        });
    }

    /// Adds facts collected from another file (to collect them lined up per file).
    pub fn merge(&mut self, other: ProgramParams) {
        for (callee, facts) in other.by_callee {
            self.by_callee.entry(callee).or_default().extend(facts);
        }
    }

    /// The facts about a callee's arguments that receive a function returning a Program.
    pub fn of(&self, callee: &str) -> &[PassedProgram] {
        self.by_callee.get(callee).map(Vec::as_slice).unwrap_or(&[])
    }

    /// The callees that have facts (for tests).
    #[cfg(test)]
    pub fn callees(&self) -> Vec<String> {
        self.by_callee.keys().cloned().collect()
    }

    /// The number of facts (for the report).
    pub fn len(&self) -> usize {
        self.by_callee.values().map(Vec::len).sum()
    }

    /// Whether it is empty.
    pub fn is_empty(&self) -> bool {
        self.by_callee.is_empty()
    }
}

/// One symbol the caller passed as an argument — as spelled, and the name resolved to its module (the key for looking up the collection of defks).
#[derive(Debug, Clone, PartialEq, Eq)]
struct Spelled {
    text: String,
    qualified: String,
}

/// One candidate for what the caller passed as an argument — waiting for the decision of whether the symbol is a defk (the decision is made by the collection the caller holds, not by the file's contents alone).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    /// The callee (the name resolved to its module).
    pub callee: String,
    slot: Slot,
    /// The symbol passed. None for `(fnk …)`.
    symbol: Option<Spelled>,
    caller: String,
}

impl Candidate {
    /// The name of the passed symbol resolved to its module (the key for looking up whether it is a defk). None for `(fnk …)`.
    pub fn qualified(&self) -> Option<&str> {
        self.symbol.as_ref().map(|symbol| symbol.qualified.as_str())
    }

    /// Finishes the decision and makes it a fact. None if the symbol is not a defk (`(fnk …)` is always a fact). The same as what `ProgramParams::collect` accumulates.
    pub fn settle(self, is_defk: impl Fn(&str) -> bool) -> Option<PassedProgram> {
        let passed = match self.symbol {
            Some(symbol) if is_defk(&symbol.qualified) => symbol.text,
            Some(_) => return None,
            None => "fnk".to_string(),
        };
        Some(PassedProgram { slot: self.slot, passed, caller: self.caller })
    }
}

/// All candidates for calls in one source whose callee matches `wants`, in the order `ProgramParams::collect` accumulates them (without asking whether the symbol is a defk).
pub fn candidates_in(source: &str, rel: &str, scope: Scope<'_>, wants: &dyn Fn(&str) -> bool) -> Vec<Candidate> {
    let mut found = Vec::new();
    each_argument(source, rel, scope, wants, &mut |callee, slot, argument, caller| {
        let symbol = match argument {
            Argument::Symbol(text) => Some(Spelled { text: text.to_string(), qualified: scope.qualify(text) }),
            Argument::Fnk => None,
        };
        found.push(Candidate { callee: callee.to_string(), slot, symbol, caller: caller.to_string() });
    });
    found
}

/// Something that can look up, for a callee (the name including the module), the facts of the arguments for which the caller passes a Program — either one collected from the whole repo (`ProgramParams`), or
/// one collected, when looked up, only from the files that contain the callee's name literally (`SpelledCallers` — a single-file run; callers are also in files that the written file does not name).
pub trait ProgramParamsLookup {
    /// The facts for each of `callees` (in the same order — the collector knows the callees to look up together, and reads the files once).
    fn passed_to_all(&self, callees: &[String]) -> Vec<Vec<PassedProgram>>;
}

impl ProgramParamsLookup for ProgramParams {
    fn passed_to_all(&self, callees: &[String]) -> Vec<Vec<PassedProgram>> {
        callees.iter().map(|callee| self.of(callee).to_vec()).collect()
    }
}

/// One bare call of a function received as an argument.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParamCall {
    /// The name (mangled) and head of the callee's definition.
    pub definition: String,
    pub container: String,
    /// The name of the argument called bare (as written).
    pub param: String,
    /// What the caller passed (a defk name or fnk), and where it was passed (the first one).
    pub passed: String,
    pub caller: String,
    pub span: ByteSpan,
}

impl ParamCall {
    /// Detail of the registry key (`<definition>::<argument>`).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, hy_mangle(&self.param))
    }
}

/// The names and forms of top-level function definitions (including inside `do` and `eval-and-compile`).
fn top_definitions<'f>(hy: &Hy<'_>, forms: &'f [Form]) -> Vec<(String, &'f Form)> {
    let mut out = Vec::new();
    let mut pending: Vec<&Form> = forms.iter().rev().collect();
    while let Some(form) = pending.pop() {
        match hy.head(form) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(live(form).unwrap_or_default().into_iter().skip(1).rev()),
            Some(_) => out.push((definition_name(hy, form).map(hy_mangle).unwrap_or_else(|| "<module>".to_string()), form)),
            None => {}
        }
    }
    out
}

/// The name of a definition form (`(defk [decorators]? name …)`, `#^ T name`). None if it is not a definition.
fn definition_name<'a>(hy: &Hy<'a>, form: &Form) -> Option<&'a str> {
    let items = live(form)?;
    let head = items.first().and_then(|h| hy.symbol(h))?;
    if !matches!(head, "defk" | "deff" | "defn" | "defn/a" | "defp" | "defpp" | "defhandler" | "deftest") {
        return None;
    }
    let name = match items.get(1) {
        Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
        other => other.copied(),
    }?;
    Some(match &name.node {
        Node::Annotated { target: Some(target), .. } => hy.text(target),
        _ => hy.text(name),
    })
}

/// The names in the parameter list (in the order of positional arguments; also all keyword-only arguments, so they can be looked up by name). After `*` they are not looked up by position.
fn parameters<'a>(hy: &Hy<'a>, list: &Form) -> (Vec<&'a str>, Vec<&'a str>) {
    let mut positional = Vec::new();
    let mut all = Vec::new();
    let mut keyword_only = false;
    for item in list.bracket_items().map(live_items).unwrap_or_default() {
        let name = match &item.node {
            Node::Symbol if matches!(hy.text(item), "*" | "/") => {
                keyword_only |= hy.text(item) == "*";
                continue;
            }
            Node::Symbol => Some(hy.text(item)),
            Node::Annotated { target: Some(target), .. } => Some(hy.text(target)),
            Node::Seq { delim: Delim::Bracket, .. } => item.bracket_items().and_then(|b| b.first()).map(|f| hy.text(f)),
            Node::Prefixed { .. } => {
                keyword_only = true;
                None
            }
            _ => None,
        };
        if let Some(name) = name {
            all.push(name);
            if !keyword_only {
                positional.push(name);
            }
        }
    }
    (positional, all)
}

/// The forms that pass their contents as a Program, the doeff-hy forms that accept a Program, and the forms that carry a Program position into branches.
const PROGRAM_POSITIONS: &[&str] = &["!", "yield", "yield-from", "return", "await"];
const PROGRAM_TAKING_FORMS: &[&str] = &["with-handlers", "maybe", "result", "on-raise", "absent-as", "do!"];
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let", "match"];

/// One definition that calls some of its own arguments bare outside a Program position — a definition that looks up the caller's facts.
struct Asking<'a, 'f> {
    definition: String,
    container: String,
    items: Vec<&'f Form>,
    list_at: usize,
    positional: Vec<&'a str>,
    all: Vec<&'a str>,
}

/// Definitions that call some of their own arguments bare outside a Program position — definitions that look up the caller's facts.
fn asking_definitions<'a, 'f>(hy: &Hy<'a>, forms: &'f [Form], scope: Scope<'_>, defks: &DefkNames) -> Vec<Asking<'a, 'f>> {
    let mut asking = Vec::new();
    for (definition, form) in top_definitions(hy, forms) {
        let items = live(form).unwrap_or_default();
        let container = items.first().and_then(|h| hy.symbol(h)).unwrap_or("").to_string();
        let list_at = match items.get(1) {
            Some(first) if first.bracket_items().is_some() => 3,
            _ => 2,
        };
        let Some(list) = items.get(list_at).filter(|l| l.bracket_items().is_some()) else { continue };
        let (positional, all) = parameters(hy, list);
        // The caller's facts (`params` — collected from calls across the whole repo) are looked up only when this definition calls some of its own arguments bare outside a Program position.
        // If it does not, there is no hit whatever the caller passes (a hit needs both a "passed argument" and a "bare call").
        if !calls_a_parameter_bare(hy, scope, defks, &items[list_at + 1..], &all, &definition, &container) {
            continue;
        }
        asking.push(Asking { definition, container, items, list_at, positional, all });
    }
    asking
}

/// In one source, the callees (the name including the module) of the definitions that look up the caller's facts. If `defks` is an empty collection, it includes all definitions that would be looked up with the actual collection
/// (the larger the collection of defks, the more "positions where a Program is passed" there are, and the fewer bare calls — monotonic). An estimate for starting to collect the caller's facts before the collection of defks is decided.
pub fn asked_callees(source: &str, scope: Scope<'_>, defks: &DefkNames) -> Vec<String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    asking_definitions(&hy, &forms, scope, defks).iter().map(|asked| format!("{}.{}", scope.module, asked.definition)).collect()
}

/// In one source, picks up every place where the body calls bare an argument for which the caller passes a function returning a Program (one finding per argument of one definition).
pub fn param_calls_in(source: &str, scope: Scope<'_>, defks: &DefkNames, params: &dyn ProgramParamsLookup) -> Vec<ParamCall> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let asking = asking_definitions(&hy, &forms, scope, defks);
    if asking.is_empty() {
        return Vec::new();
    }
    // The callees to look up are passed together for the definitions (so the side searching for callers reads the repo's files only once).
    let callees: Vec<String> = asking.iter().map(|asked| format!("{}.{}", scope.module, asked.definition)).collect();
    let passed_all = params.passed_to_all(&callees);
    let mut found: Vec<ParamCall> = Vec::new();
    for (asked, passed) in asking.iter().zip(&passed_all) {
        if passed.is_empty() {
            continue;
        }
        let Asking { definition, container, items, list_at, positional, all } = asked;
        // Argument name -> what was passed (the first one).
        let mut targets: BTreeMap<String, &PassedProgram> = BTreeMap::new();
        for fact in passed {
            let name = match &fact.slot {
                Slot::Position(at) => positional.get(*at).copied(),
                Slot::Keyword(key) => all.iter().copied().find(|n| hy_mangle(n) == *key),
            };
            if let Some(name) = name {
                targets.entry(name.to_string()).or_insert(fact);
            }
        }
        if targets.is_empty() {
            continue;
        }
        let walker = Walker { hy: &hy, scope, defks, targets: &targets, definition, container };
        for body in items.iter().skip(list_at + 1) {
            walker.walk(body, false, &mut found);
        }
    }
    let mut seen = BTreeSet::new();
    found.retain(|call| seen.insert(call.detail()));
    found
}

/// Whether the body of a definition (`bodies`) calls any of the arguments (`parameters` — what was passed does not matter) bare outside a Program position. A necessary condition for a hit
/// (a hit is a place where the walker, in the same way as this decision, finds the name of an argument the caller passed), so if false there is no need to look up the caller's facts.
fn calls_a_parameter_bare(
    hy: &Hy<'_>,
    scope: Scope<'_>,
    defks: &DefkNames,
    bodies: &[&Form],
    parameters: &[&str],
    definition: &str,
    container: &str,
) -> bool {
    // What value was passed does not affect whether there is a hit (the Walker looks only at names), so every argument gets the same mark.
    let any = PassedProgram { slot: Slot::Position(0), passed: String::new(), caller: String::new() };
    let targets: BTreeMap<String, &PassedProgram> = parameters.iter().map(|name| (name.to_string(), &any)).collect();
    if targets.is_empty() {
        return false;
    }
    let walker = Walker { hy, scope, defks, targets: &targets, definition, container };
    let mut found = Vec::new();
    for body in bodies {
        walker.walk(body, false, &mut found);
    }
    !found.is_empty()
}

/// A tool that searches, outside the places that pass on as a Program, for a call headed by an argument's name.
struct Walker<'a, 'h> {
    hy: &'h Hy<'a>,
    scope: Scope<'a>,
    defks: &'a DefkNames,
    targets: &'h BTreeMap<String, &'h PassedProgram>,
    definition: &'h str,
    container: &'h str,
}

impl Walker<'_, '_> {
    /// Descends a form. `program` is whether this form is in a position passed as a Program.
    fn walk(&self, form: &Form, program: bool, out: &mut Vec<ParamCall>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.call(form, program, out),
            Node::Seq { delim: Delim::Bracket | Delim::Tuple, .. } => {
                for child in children(form) {
                    self.walk(child, program, out);
                }
            }
            _ => {
                for child in children(form) {
                    self.walk(child, false, out);
                }
            }
        }
    }

    /// One `( … )` — if a call headed by an argument's name is outside a Program position, accumulates it, and distributes the Program position to its children.
    fn call(&self, form: &Form, program: bool, out: &mut Vec<ParamCall>) {
        let items = live(form).unwrap_or_default();
        let head = items.first().and_then(|h| self.hy.symbol(h));
        if let (Some(name), false) = (head, program) {
            if let Some(fact) = self.targets.get(name) {
                out.push(ParamCall {
                    definition: self.definition.to_string(),
                    container: self.container.to_string(),
                    param: name.to_string(),
                    passed: fact.passed.clone(),
                    caller: fact.caller.clone(),
                    span: span_of(form),
                });
            }
        }
        let qualified = head.map(|h| self.scope.qualify(h));
        let takes_programs = match head {
            Some(h) if PROGRAM_POSITIONS.contains(&h) || PROGRAM_TAKING_FORMS.contains(&h) => true,
            Some(h) if BRANCHING_FORMS.contains(&h) => program,
            Some(_) => qualified.as_deref().is_some_and(|q| q.starts_with("doeff") || self.defks.contains(q)),
            None => false,
        };
        let binds = head == Some("<-");
        let last = items.len().saturating_sub(1);
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_program = match binds {
                true => index == last || items.get(index + 1).is_some_and(|next| matches!(next.node, Node::Keyword)),
                false => takes_programs && !matches!(child.node, Node::Keyword),
            };
            self.walk(child, child_program, out);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// For two sources (callee m.callee and caller m.caller), turns the callee's bare calls into a list of (definition::argument <- what was passed).
    fn found(callee: &str, caller: &str) -> Vec<String> {
        let bindings = BTreeMap::new();
        let defks = DefkNames::of(&["m.payload_of", "m.rows_by_text"]);
        let mut params = ProgramParams::default();
        params.collect(caller, "caller.hy", Scope { module: "m", bindings: &bindings }, &defks);
        param_calls_in(callee, Scope { module: "m", bindings: &bindings }, &defks, &params)
            .iter()
            .map(|c| format!("{} <- {}", c.detail(), c.passed))
            .collect()
    }

    #[test]
    fn a_function_argument_called_bare_is_found_when_a_caller_passes_a_defk_or_fnk() {
        let callee = r#"(defk rows-by-text [rows field-of]
  (for [row rows]
    (setv value (field-of row))
    (when (isinstance value str) (print value)))
  rows)
(deff pick [rows * key-of] (lfor r rows (key-of r)))
(defk safe [rows field-of] (<- v (field-of (get rows 0))) (return (! (field-of v))))"#;
        let caller = r#"(defk tag-rows-of [tags]
  (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p "subject")))))
(defk other [xs] (pick xs :key-of payload-of))
(defk calm [xs] (safe xs payload-of))"#;
        // rows-by-text and pick call bare (a bind, a comprehension). safe takes it with (<- …) and (! …), so it is not picked up.
        assert_eq!(found(callee, caller), vec!["rows_by_text::field_of <- fnk", "pick::key_of <- payload-of"]);
    }

    #[test]
    fn plain_functions_passed_as_arguments_are_not_traced() {
        let callee = "(defk rows-by-text [rows field-of] (for [row rows] (setv value (field-of row))) rows)\n";
        // If the caller passes a plain function (the name of an fn or deff), calling it bare is fine. A defk put into a variable before being passed cannot be followed (not picked up).
        let caller = "(defk a [t] (! (rows-by-text t (fn [row] (.get row \"s\")))))\n(defk b [t] (setv f payload-of) (! (rows-by-text t f)))\n";
        assert!(found(callee, caller).is_empty());
    }
}

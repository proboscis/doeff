//! DOEFF210: a Hy program does not sleep on the wall clock (doeff ADR-DOE-TIME-001 R1, R2).
//!
//! When a program (the body of defk or defp — an @do generator) waits for time, it emits doeff-time effects (Delay, WaitUntil, WaitWithin …), and
//! what one second means is decided by the time handler the caller installs. If a program calls `time.sleep` / `asyncio.sleep`, it waits on the wall clock
//! whichever handler is installed, and checks of that program cannot run on a virtual clock. The same rule for Python is held by the semgrep rule
//! doeff-no-sleep-in-programs — semgrep cannot read Hy, so the Hy side is held here. Where programs live (the population) exists only in the repo
//! setting `[tool.doeff-linter.program_sleep]` and is not written here.
//!
//! Forms matched (Hy form tree):
//!   * Inside the body of defk or defp (including fn, defn, deff nested lexically within), calls whose head is `time.sleep` / `asyncio.sleep`.
//!     Also the alias `t.sleep` from `(import time :as t)`, and calls to names bound by `(import asyncio [sleep])` or `(import time [sleep :as nap])`.
//!     For `(Await (asyncio.sleep seconds))`, the inner call is matched.
//! Forms not matched:
//!   * Calls whose seconds is the literal 0 (`(asyncio.sleep 0)`, `(time.sleep 0.0)` — they merely yield once without waiting for time).
//!   * Handler implementations (waiting on the wall clock is the handler's responsibility): the body of defhandler, definitions whose argument list is `[effect k]` (defk, deff,
//!     defn, fn), and defk/defp whose contract dict `:tags` has `:role` `foundation` (the generic I/O role). The last one is a declaration of a helper called
//!     only from handlers — it is read only when the definition writes it in its own dict, and a module-head tag does not exempt it.
//!   * Definitions that are not programs (top-level deff, defn, defclass methods) — a plain callable cannot emit effects. Whether deff is right is
//!     held by DOEFF111 and 203.
//!   * Comments, forms discarded with `#_`, and quoted forms.

use std::collections::BTreeMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};

/// The modules with a sleep function (the two in the Python standard library) and the function name. The test-sleep rule (DOEFF211, test_sleep.rs) reads the same two.
pub(super) const SLEEP_MODULES: &[&str] = &["time", "asyncio"];
pub(super) const SLEEP_NAME: &str = "sleep";
/// Heads that define a program (the body becomes an @do generator).
const PROGRAM_HEADS: &[&str] = &["defk", "defp"];
/// Heads read as a handler implementation when the argument list is `[effect k]`, with the position of the argument list for that head.
const CALLABLE_HEADS: &[(&str, usize)] = &[("defk", 2), ("deff", 2), ("defn", 2), ("defn/a", 2), ("fn", 1), ("fn/a", 1)];
/// The argument list of a handler implementation.
const HANDLER_PARAMS: &[&str] = &["effect", "k"];
/// The generic I/O role (a declaration of a helper called only from handlers).
const IO_ROLE: &str = "foundation";
/// The range in which to look for the contract dict (number of elements after the name — skipping the argument list and docstring).
const CONTRACT_REACH: usize = 4;

/// One hit (byte range of the sleep call form, name of the enclosing program, spelling of the sleep function).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgramSleepHit {
    pub start: usize,
    pub end: usize,
    /// Name of the enclosing program (defk, defp).
    pub definition: String,
    /// The sleep function (`time.sleep` or `asyncio.sleep` — the original spelling even when called by an alias).
    pub callee: String,
}

impl ProgramSleepHit {
    /// Key detail (`<program name>::<sleep function>`).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, self.callee)
    }
}

/// Judges one Hy content (hits in position order).
pub fn judge(source: &str) -> Vec<ProgramSleepHit> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let judge = Judge { source, sleeps: bound_names(source, &forms, SLEEP_MODULES, SLEEP_NAME) };
    let mut out = Vec::new();
    forms.iter().for_each(|form| judge.walk(form, None, &mut out));
    out.sort_by_key(|hit| hit.start);
    out
}

/// The spelling of a form.
pub(super) fn text<'a>(source: &'a str, form: &Form) -> &'a str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// Elements excluding discarded ones.
pub(super) fn live(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// The head symbol spelling of `( … )` and its elements excluding discarded ones.
pub(super) fn headed<'a, 'f>(source: &'a str, form: &'f Form) -> Option<(&'a str, Vec<&'f Form>)> {
    let items = live(form.paren_items()?);
    let head = items.first().filter(|first| matches!(first.node, Node::Symbol)).map(|first| text(source, first))?;
    Some((head, items))
}

/// Children of a form (quoted forms and discarded forms are not entered).
pub(super) fn children(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { items, .. } => live(items),
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => Vec::new(),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => vec![inner.as_ref()],
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().map(|part| part.as_ref()).collect(),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {
            Vec::new()
        }
    }
}

/// Within a file, maps the head spelling of a call that refers to name (e.g. sleep of time, asyncio) in any of the modules to the original spelling (`<module>.<name>`).
/// Collects the plain spelling `<module>.<name>` and the names bound by `(import …)` in the file (including those inside functions).
pub(super) fn bound_names(source: &str, forms: &[Form], modules: &[&str], name: &str) -> BTreeMap<String, String> {
    let mut bound: BTreeMap<String, String> = modules.iter().map(|module| (format!("{}.{}", module, name), format!("{}.{}", module, name))).collect();
    forms.iter().for_each(|form| bind_imports(source, form, modules, name, &mut bound));
    bound
}

/// Adds the names bound by one `(import …)` (or ones within its children). `(import time)` and `(import time :as t)` give `<name>.sleep`;
/// `(import time [sleep])`, `(import time [sleep :as nap])` and `(import time *)` give the bound name itself.
fn bind_imports(source: &str, form: &Form, modules: &[&str], name: &str, bound_names: &mut BTreeMap<String, String>) {
    match headed(source, form) {
        Some(("import", items)) => {
            let args = &items[1..];
            let mut at = 0;
            while at < args.len() {
                let module = text(source, args[at]);
                let original = modules.contains(&module).then(|| format!("{}.{}", module, name));
                let next = args.get(at + 1).copied();
                let (bound, used) = match next {
                    Some(names) if names.bracket_items().is_some() => (imported_name(source, names, name), 2),
                    Some(star) if matches!(star.node, Node::Symbol) && text(source, star) == "*" => (Some(name.to_string()), 2),
                    Some(keyword) if text(source, keyword) == ":as" => {
                        (args.get(at + 2).map(|alias| format!("{}.{}", text(source, alias), name)), 3)
                    }
                    _ => (Some(format!("{}.{}", module, name)), 1),
                };
                if let (Some(original), Some(bound)) = (original, bound) {
                    bound_names.insert(bound, original);
                }
                at += used;
            }
        }
        _ => children(form).into_iter().for_each(|child| bind_imports(source, child, modules, name, bound_names)),
    }
}

/// The name bound for name inside the `[…]` of an import listing names (`[sleep]` -> `sleep`, `[sleep :as nap]` -> `nap`, None if absent).
fn imported_name(source: &str, names: &Form, name: &str) -> Option<String> {
    let names = live(names.bracket_items()?);
    let at = names.iter().position(|bound| matches!(bound.node, Node::Symbol) && text(source, bound) == name)?;
    match (names.get(at + 1), names.get(at + 2)) {
        (Some(keyword), Some(alias)) if text(source, keyword) == ":as" => Some(text(source, alias).to_string()),
        _ => Some(name.to_string()),
    }
}

/// Whether the seconds is the literal 0 (`0`, `0.0`, `0.`).
pub(super) fn is_zero(source: &str, seconds: &Form) -> bool {
    matches!(seconds.node, Node::Number) && text(source, seconds).parse::<f64>().is_ok_and(|value| value == 0.0)
}

struct Judge<'a> {
    source: &'a str,
    /// Head spelling of a call referring to a sleep function -> original spelling (`time.sleep` / `asyncio.sleep`).
    sleeps: BTreeMap<String, String>,
}

impl Judge<'_> {
    /// Walks the form tree. `program` = name of the program (defk, defp) we are in (None outside a program or inside a handler implementation).
    fn walk(&self, form: &Form, program: Option<&str>, out: &mut Vec<ProgramSleepHit>) {
        let mut inside = program;
        if let Some((head, items)) = headed(self.source, form) {
            if head == "defhandler" || self.takes_effect_and_continuation(head, &items) {
                inside = None;
            } else if PROGRAM_HEADS.contains(&head) {
                inside = match self.names_io_role(&items) {
                    true => None,
                    false => items.get(1).map(|name| text(self.source, name)),
                };
            } else if let (Some(definition), Some(callee)) = (program, self.sleeps.get(head)) {
                if !items.get(1).is_some_and(|seconds| is_zero(self.source, seconds)) {
                    out.push(ProgramSleepHit {
                        start: form.span.start,
                        end: form.span.end,
                        definition: definition.to_string(),
                        callee: callee.clone(),
                    });
                }
            }
        }
        children(form).into_iter().for_each(|child| self.walk(child, inside, out));
    }

    /// Whether it is a definition whose argument list is `[effect k]` (a handler implementation — same recognition as the translating handler of DOEFF130).
    fn takes_effect_and_continuation(&self, head: &str, items: &[&Form]) -> bool {
        let Some((_, at)) = CALLABLE_HEADS.iter().find(|(callable, _)| *callable == head) else { return false };
        items
            .get(*at)
            .and_then(|params| params.bracket_items())
            .is_some_and(|params| live(params).iter().map(|param| text(self.source, param)).eq(HANDLER_PARAMS.iter().copied()))
    }

    /// Whether `:tags` of the contract dict declares the generic I/O role — reads the first dict after the name, skipping the argument list and docstring.
    fn names_io_role(&self, items: &[&Form]) -> bool {
        let contract = items
            .iter()
            .skip(2)
            .take(CONTRACT_REACH)
            .take_while(|part| part.is_brace() || part.bracket_items().is_some() || matches!(part.node, Node::Str { .. }))
            .find(|part| part.is_brace());
        contract
            .and_then(|contract| value_of(self.source, contract, ":tags"))
            .and_then(|tags| value_of(self.source, tags, ":role"))
            .is_some_and(|role| string(self.source, role) == Some(IO_ROLE))
    }
}

/// The value of key in a dict `{key value …}`.
pub(super) fn value_of<'f>(source: &str, dict: &'f Form, key: &str) -> Option<&'f Form> {
    let entries = match &dict.node {
        Node::Seq { delim: Delim::Brace, items } => live(items),
        _ => return None,
    };
    entries.chunks(2).find_map(|pair| match pair {
        [name, value] if matches!(name.node, Node::Keyword) && text(source, name) == key => Some(*value),
        _ => None,
    })
}

/// The content of a string literal.
pub(super) fn string<'a>(source: &'a str, form: &Form) -> Option<&'a str> {
    match &form.node {
        Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => source.get(body.start..body.end),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The list of key details of the hits.
    fn details(source: &str) -> Vec<String> {
        judge(source).iter().map(ProgramSleepHit::detail).collect()
    }

    #[test]
    fn a_program_that_sleeps_on_the_wall_clock_is_found() {
        assert_eq!(details("(import time)\n(defk wait-a-bit []\n  {:pre [] :post [(: % None)]}\n  (time.sleep 1.0))\n"), vec!["wait-a-bit::time.sleep"]);
        assert_eq!(details("(import asyncio)\n(defk nap [s] (<- (Await (asyncio.sleep s))))\n"), vec!["nap::asyncio.sleep"]);
        assert_eq!(details("(defp settle {:post [(: % None)]} (time.sleep SETTLE-SECONDS))\n"), vec!["settle::time.sleep"]);
    }

    #[test]
    fn the_hit_spans_the_call_and_names_the_program() {
        let source = "(defk poll []\n  (while True\n    (time.sleep 0.5)))\n";
        let hits = judge(source);
        assert_eq!(hits.len(), 1);
        assert_eq!(&source[hits[0].start..hits[0].end], "(time.sleep 0.5)");
        assert_eq!((hits[0].definition.as_str(), hits[0].callee.as_str()), ("poll", "time.sleep"));
    }

    #[test]
    fn aliases_and_imported_names_are_followed_to_the_module() {
        assert_eq!(details("(import time :as t)\n(defk a [] (t.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import asyncio [sleep])\n(defk a [] (<- (Await (sleep 1))))\n"), vec!["a::asyncio.sleep"]);
        assert_eq!(details("(import time [monotonic sleep :as nap])\n(defk a [] (nap 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import time *)\n(defk a [] (sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import os time :as t asyncio [sleep :as rest])\n(defk a [] (t.sleep 1) (rest 2))\n"), vec!["a::time.sleep", "a::asyncio.sleep"]);
        // Imports inside functions are read too.
        assert_eq!(details("(defk a [] (import time :as clock) (clock.sleep 1))\n"), vec!["a::time.sleep"]);
        // A sleep from a module that is not a sleep module is not counted (neither doeff-time's sleep nor one's own function).
        assert!(details("(import mylib [sleep])\n(defk a [] (sleep 1))\n").is_empty());
        assert!(details("(import mylib :as time2)\n(defk a [] (time2.sleep 1))\n").is_empty());
    }

    #[test]
    fn nested_callables_inside_a_program_count_as_the_program() {
        assert_eq!(details("(defk a [] (run-later (fn [] (time.sleep 1))))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] (defn helper [] (time.sleep 1)) (helper))\n"), vec!["a::time.sleep"]);
        // A program inside a plain definition is still a program.
        assert_eq!(details("(defclass Box []\n  (defn build [self]\n    (defk inner [] (time.sleep 1))\n    inner))\n"), vec!["inner::time.sleep"]);
    }

    #[test]
    fn a_yield_of_zero_seconds_is_not_a_wait() {
        assert!(details("(defk a [] (<- (Await (asyncio.sleep 0))) (time.sleep 0.0) (asyncio.sleep 0 result))\n").is_empty());
        // A non-zero literal and an expression of unknown value are waits.
        assert_eq!(details("(defk a [] (time.sleep 0.01) (time.sleep seconds) (time.sleep (- 1 1)))\n").len(), 3);
    }

    #[test]
    fn a_handler_implementation_may_sleep() {
        // A defhandler clause.
        assert!(details("(defhandler wall-clock\n  (Delay [seconds]\n    (time.sleep seconds)\n    (resume None)))\n").is_empty());
        // A defhandler clause written inside a program body is also a handler implementation (the nesting itself is stopped by another rule).
        assert!(details("(defk build []\n  (defhandler wall-clock\n    (Delay [seconds]\n      (time.sleep seconds)\n      (resume None)))\n  wall-clock)\n").is_empty());
        // A definition that takes (effect k).
        assert!(details("(defk raw-handler [effect k]\n  {:pre [] :post []}\n  (time.sleep 1)\n  (<- (Pass effect k)))\n").is_empty());
        assert!(details("(defk a [] (with-handler (fn [effect k] (time.sleep 1)) body))\n").is_empty());
        // A helper declaring the generic I/O role (called only from handlers).
        let helper = "(defk group-gone-by [child deadline]\n  {:pre [(: deadline float)] :post [(: % bool)] :tags {:context \"process\" :role \"foundation\"}}\n  \"docstring\"\n  (time.sleep 0.02)\n  True)\n";
        assert!(details(helper).is_empty());
        assert!(details("(defp settle {:post [(: % None)] :tags {:context \"x\" :role \"foundation\"}} (time.sleep 1))\n").is_empty());
        // Read even when a docstring comes before the dict.
        assert!(details("(defk a [] \"doc\" {:pre [] :post [] :tags {:context \"x\" :role \"foundation\"}} (time.sleep 1))\n").is_empty());
    }

    #[test]
    fn only_the_definitions_own_io_role_excuses_it() {
        // Other roles, :tags without a role, and contracts without :tags are programs.
        assert_eq!(details("(defk a [] {:pre [] :post [] :tags {:context \"x\" :role \"program\"}} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] {:pre [] :post [] :tags {:context \"x\"}} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] {:pre [] :post []} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // A module-head tag does not exempt (the definition declares it itself).
        assert_eq!(details("(val MODULE-TAGS {:context \"x\" :role \"foundation\"})\n(defk a [] {:pre [] :post []} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // A dict inside the body is not a contract dict.
        assert_eq!(details("(defk a [] (setv t {:tags {:role \"foundation\"}}) (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // A two-argument definition with different argument names is not a handler implementation.
        assert_eq!(details("(defk a [effect continuation] (time.sleep 1))\n"), vec!["a::time.sleep"]);
    }

    #[test]
    fn plain_callables_outside_a_program_are_not_programs() {
        assert!(details("(deff tmux-send-keys-io [pane] {:pre [] :post []} (time.sleep 1))\n").is_empty());
        assert!(details("(defn monitor-loop [config] (while True (time.sleep 1)))\n").is_empty());
        assert!(details("(defclass Listener []\n  (defn run [self] (time.sleep 1)))\n").is_empty());
        assert!(details("(time.sleep 1)\n").is_empty());
    }

    #[test]
    fn comments_discarded_and_quoted_forms_are_not_read() {
        assert!(details("(defk a []\n  ;; (time.sleep 1)\n  \"(time.sleep 1)\"\n  #_(time.sleep 1)\n  '(time.sleep 1)\n  `(time.sleep 1)\n  None)\n").is_empty());
    }

    #[test]
    fn a_program_that_waits_by_the_time_effect_is_clean() {
        assert!(details("(import doeff_time [Delay])\n(defk a [] (<- (Delay 1.0)) (<- now (clock-now)) now)\n").is_empty());
    }
}

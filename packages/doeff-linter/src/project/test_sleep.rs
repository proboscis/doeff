//! DOEFF211: Hy tests do not sleep on the wall clock unless they declare that real time is the subject (doeff ADR-DOE-TIME-001 R3).
//!
//! When a test waits with `time.sleep` / `asyncio.sleep`, what it waits for (the event) is not named, it is slower by the real time spent, and on a busy machine it goes
//! red by timeout. A test should have the program emit doeff-time effects (Delay etc.) and run it on a virtual clock (sim-time-handler), or
//! name the event it waits for and wait on that. Only tests whose subject is real time itself (real threads, a real asyncio loop, real external processes) declare realtime,
//! and sleeps inside them are not flagged. The same rule for Python is held by the semgrep rule doeff-no-sleep-in-tests — semgrep cannot read Hy, so
//! this covers the Hy side (using the same reading parts as DOEFF210, sleep in a program). Where tests live (the population) exists only in the repo setting
//! `[tool.doeff-linter.test_sleep]` and is not written here.
//!
//! Forms of the declaration (3 forms that pytest reads as a marker — the first 2 are the same as doeff-hy's reader pytest_items.py record_module_binding and macros.hy's
//! _extract-test-meta):
//!   * module: a top-level `(val pytestmark pytest.mark.realtime)` (`var` too). The value is one marker, or a list / tuple of markers
//!     (`[pytest.mark.slow pytest.mark.realtime]`). Sleeps in the whole file are not flagged.
//!   * deftest: `"realtime"` in `:marks` of the meta map (`(deftest test-x {:marks ["realtime"]} …)`). Sleeps inside that deftest are not flagged.
//!   * plain-function test: a marker in the defn's decorator list (`(defn [pytest.mark.realtime] test-x [] …)`). Sleeps inside that function are not flagged.
//! Forms flagged (where not declared; the tree of Hy forms):
//!   * a call whose head is `time.sleep` / `asyncio.sleep`. Also `t.sleep` through the alias `(import time :as t)`, calls of names bound by `(import asyncio [sleep])` or
//!     `(import time [sleep :as nap])`, and the method spelling `(.sleep time seconds)`. `(Await (asyncio.sleep seconds))` and
//!     `(await (asyncio.sleep seconds))` are flagged at the inner call.
//!   * a rephrased sleep `(.wait (threading.Event) seconds)` — waiting with seconds on an Event made on the spot (which nobody sets).
//! Forms not flagged:
//!   * a sleep whose seconds is the literal 0 (`(asyncio.sleep 0)`, `(time.sleep 0.0)` — it only yields once without waiting any time).
//!   * comments, forms discarded with `#_`, quoted forms, and inside strings (the body of a program passed to a child process).

use std::collections::BTreeMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};

use super::program_sleep::{bound_names, children, headed, is_zero, live, string, text, value_of, SLEEP_MODULES, SLEEP_NAME};

/// Name of the marker (pytest marker) for tests whose subject is real time.
const MARK: &str = "realtime";
/// Binding targets for module-level declarations, and the binding heads (the heads doeff-hy records as markers).
const MODULE_MARKS: &str = "pytestmark";
const MODULE_BINDERS: &[&str] = &["val", "var"];
/// The part before the marker expression `pytest.mark.<name>`.
const MARK_OWNER: &[&str] = &["pytest", "mark"];
/// Heads that define a test, and the key for the marker in its meta map.
const TEST_HEAD: &str = "deftest";
const MARKS_KEY: &str = ":marks";
/// Heads that define a plain function whose decorator list can declare a marker (`[…]` right after the head).
const FUNCTION_HEADS: &[&str] = &["defn", "defn/a"];
/// Rephrased sleep: waiting with seconds on an Event made on the spot.
const EVENT_MODULES: &[&str] = &["threading"];
const EVENT_NAME: &str = "Event";
const WAIT_METHOD: &str = ".wait";
/// Name of the enclosing definition for a hit outside any definition (a module top-level expression).
const MODULE_SCOPE: &str = "<module>";

/// One hit (byte range of the sleep call form, name of the enclosing top-level definition, spelling of the sleep function).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TestSleepHit {
    pub start: usize,
    pub end: usize,
    /// Name of the enclosing top-level definition (deftest, defn, defk …). `<module>` outside a definition.
    pub definition: String,
    /// The sleep function (`time.sleep`, `asyncio.sleep`, `threading.Event.wait` — the original spelling even when called through an alias).
    pub callee: String,
}

impl TestSleepHit {
    /// Detail of the key (`<enclosing definition name>::<sleep function>`).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, self.callee)
    }
}

/// Judge the contents of one Hy test (hits are in position order). If the module declares realtime, there are no hits.
pub fn judge(source: &str) -> Vec<TestSleepHit> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if forms.iter().any(|form| module_names_realtime(source, form)) {
        return Vec::new();
    }
    let judge = Judge {
        source,
        sleeps: bound_names(source, &forms, SLEEP_MODULES, SLEEP_NAME),
        events: bound_names(source, &forms, EVENT_MODULES, EVENT_NAME),
    };
    let mut out = Vec::new();
    forms.iter().for_each(|form| judge.walk(form, &definition_of(source, form), &mut out));
    out.sort_by_key(|hit| hit.start);
    out
}

/// The name a top-level form defines (the first symbol of a form whose head starts with `def` — `:async`, decorator lists, and type annotations are skipped). `<module>` if it is not a definition.
fn definition_of(source: &str, form: &Form) -> String {
    let named = headed(source, form).filter(|(head, _)| head.starts_with("def")).and_then(|(_, items)| {
        items.into_iter().skip(1).find_map(|part| match &part.node {
            Node::Symbol => Some(Some(text(source, part))),
            Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => Some(Some(text(source, target))),
            Node::Keyword | Node::Seq { delim: Delim::Bracket, .. } => None,
            Node::Annotated { .. }
            | Node::Seq { .. }
            | Node::Str { .. }
            | Node::Number
            | Node::Prefixed { .. }
            | Node::Discarded
            | Node::Tagged { .. } => Some(None),
        })
    });
    named.flatten().unwrap_or(MODULE_SCOPE).to_string()
}

/// Whether a top-level `(val pytestmark value)` / `(var pytestmark value)` declares realtime — the value (last element) is one marker, or a list / tuple of markers.
fn module_names_realtime(source: &str, form: &Form) -> bool {
    let Some((head, items)) = headed(source, form) else { return false };
    let binds_marks = MODULE_BINDERS.contains(&head) && items.len() >= 3 && matches!(items[1].node, Node::Symbol) && text(source, items[1]) == MODULE_MARKS;
    binds_marks && items.last().is_some_and(|value| elements(value).into_iter().any(|mark| is_realtime_mark(source, mark)))
}

/// The elements if it is a list / tuple literal, otherwise the form itself as one element.
fn elements(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { delim: Delim::Bracket | Delim::Tuple, items } => live(items),
        Node::Seq { delim: Delim::Paren | Delim::Brace | Delim::Set, .. }
        | Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => vec![form],
    }
}

/// Whether the marker expression is realtime — `pytest.mark.realtime`, `(. pytest mark realtime)`, or its call `(pytest.mark.realtime …)`.
fn is_realtime_mark(source: &str, form: &Form) -> bool {
    match &form.node {
        Node::Symbol => text(source, form).split('.').eq(MARK_OWNER.iter().copied().chain([MARK])),
        Node::Seq { delim: Delim::Paren, items } => {
            let items = live(items);
            items.iter().map(|item| text(source, item)).eq(["."].into_iter().chain(MARK_OWNER.iter().copied()).chain([MARK]))
                || items.first().is_some_and(|head| is_realtime_mark(source, head))
        }
        Node::Seq { delim: Delim::Bracket | Delim::Brace | Delim::Tuple | Delim::Set, .. }
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => false,
    }
}

/// Whether `:marks` in the meta of `(deftest name [args]? "docstring"… {meta} body…)` includes realtime — meta is only the first element, after skipping the arg list and
/// leading strings, when it is a map (same reading as doeff-hy's deftest). A marker is a string or a symbol.
fn test_names_realtime(source: &str, items: &[&Form]) -> bool {
    let after_name = items.get(2..).unwrap_or(&[]);
    let body = match after_name.first() {
        Some(params) if params.bracket_items().is_some() => &after_name[1..],
        _ => after_name,
    };
    body.iter()
        .find(|part| string(source, part).is_none())
        .filter(|part| part.is_brace())
        .and_then(|meta| value_of(source, meta, MARKS_KEY))
        .is_some_and(|marks| elements(marks).into_iter().any(|mark| string(source, mark) == Some(MARK) || (matches!(mark.node, Node::Symbol) && text(source, mark) == MARK)))
}

/// Whether the decorator list of `(defn [decorator …] name [args] body…)` includes the realtime marker — the list is the `[…]` right after the head (before the name).
fn function_names_realtime(source: &str, items: &[&Form]) -> bool {
    items.get(1).and_then(|decorators| decorators.bracket_items()).is_some_and(|decorators| live(decorators).into_iter().any(|mark| is_realtime_mark(source, mark)))
}

struct Judge<'a> {
    source: &'a str,
    /// Spelling of the head of a call that refers to a sleep function -> original spelling (`time.sleep` / `asyncio.sleep`).
    sleeps: BTreeMap<String, String>,
    /// Spelling of the head of a call that makes an Event -> original spelling (`threading.Event`).
    events: BTreeMap<String, String>,
}

impl Judge<'_> {
    /// Walk the form tree (does not enter a deftest or plain function that declares realtime). `definition` = name of the enclosing top-level definition.
    fn walk(&self, form: &Form, definition: &str, out: &mut Vec<TestSleepHit>) {
        if let Some((head, items)) = headed(self.source, form) {
            let declared = (head == TEST_HEAD && test_names_realtime(self.source, &items))
                || (FUNCTION_HEADS.contains(&head) && function_names_realtime(self.source, &items));
            if declared {
                return;
            }
            if let Some(callee) = self.sleeping_call(head, &items) {
                out.push(TestSleepHit { start: form.span.start, end: form.span.end, definition: definition.to_string(), callee });
            }
        }
        children(form).into_iter().for_each(|child| self.walk(child, definition, out));
    }

    /// If the call waits on the wall clock, the original spelling of the function it waits with.
    fn sleeping_call(&self, head: &str, items: &[&Form]) -> Option<String> {
        let waits = |seconds: Option<&&Form>| !seconds.is_some_and(|seconds| is_zero(self.source, seconds));
        if let Some(original) = self.sleeps.get(head) {
            // (time.sleep seconds)
            return waits(items.get(1)).then(|| original.clone());
        }
        if head.strip_prefix('.') == Some(SLEEP_NAME) {
            // (.sleep time seconds)
            let receiver = items.get(1).filter(|receiver| matches!(receiver.node, Node::Symbol))?;
            let original = self.sleeps.get(&format!("{}.{}", text(self.source, receiver), SLEEP_NAME))?;
            return waits(items.get(2)).then(|| original.clone());
        }
        if head == WAIT_METHOD && items.len() > 2 {
            // (.wait (threading.Event) seconds)
            let (maker, _) = headed(self.source, items[1])?;
            return self.events.get(maker).map(|original| format!("{}{}", original, WAIT_METHOD));
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// List of key details for the hits.
    fn details(source: &str) -> Vec<String> {
        judge(source).iter().map(TestSleepHit::detail).collect()
    }

    #[test]
    fn a_test_that_sleeps_on_the_wall_clock_is_found() {
        assert_eq!(details("(import time)\n(deftest test-waits\n  (time.sleep 0.3)\n  (assert True))\n"), vec!["test-waits::time.sleep"]);
        assert_eq!(details("(defn :async wait-up [session] (await (asyncio.sleep 0.05)))\n"), vec!["wait-up::asyncio.sleep"]);
        assert_eq!(details("(defk pause [gap] {:pre [] :post []} (<- (Await (asyncio.sleep gap))))\n"), vec!["pause::asyncio.sleep"]);
        // Outside definitions (a test script that runs top to bottom).
        assert_eq!(details("(import time)\n(time.sleep 1)\n(when ready (time.sleep 2))\n"), vec!["<module>::time.sleep", "<module>::time.sleep"]);
    }

    #[test]
    fn the_hit_spans_the_call_and_names_the_top_level_definition() {
        let source = "(defclass Peer []\n  (defn run [self]\n    (while True (time.sleep 0.5))))\n";
        let hits = judge(source);
        assert_eq!(hits.len(), 1);
        assert_eq!(&source[hits[0].start..hits[0].end], "(time.sleep 0.5)");
        assert_eq!((hits[0].definition.as_str(), hits[0].callee.as_str()), ("Peer", "time.sleep"));
        // Skip :async, decorator lists, and type annotations to read the name.
        assert_eq!(details("(defn :async [decorated] #^ bool all-closed [sessions] (await (asyncio.sleep 0.02)))\n"), vec!["all-closed::asyncio.sleep"]);
    }

    #[test]
    fn aliases_imported_names_and_the_method_spelling_are_followed() {
        assert_eq!(details("(import time :as t)\n(deftest a (t.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import asyncio [sleep])\n(deftest a (await (sleep 1)))\n"), vec!["a::asyncio.sleep"]);
        assert_eq!(details("(import time [monotonic sleep :as nap])\n(deftest a (nap 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import time)\n(deftest a (.sleep time 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import time :as t)\n(deftest a (.sleep t 1))\n"), vec!["a::time.sleep"]);
        // A sleep from a module that is not a sleep module is not counted.
        assert!(details("(import mylib [sleep])\n(deftest a (sleep 1))\n").is_empty());
        assert!(details("(deftest a (.sleep stub 1))\n").is_empty());
    }

    #[test]
    fn a_fresh_event_waited_with_seconds_is_a_sleep() {
        assert_eq!(details("(import threading)\n(defk counted [] (.wait (threading.Event) 0.1) 1)\n"), vec!["counted::threading.Event.wait"]);
        assert_eq!(details("(import threading [Event])\n(deftest a (.wait (Event) :timeout 0.1))\n"), vec!["a::threading.Event.wait"]);
        assert_eq!(details("(import threading :as th)\n(deftest a (.wait (th.Event) 0.1))\n"), vec!["a::threading.Event.wait"]);
        // A wait on an Event that someone sets, or a wait without seconds, is not a sleep.
        assert!(details("(deftest a (setv gate (threading.Event)) (.wait gate 5) (.wait (threading.Event)))\n").is_empty());
    }

    #[test]
    fn a_yield_of_zero_seconds_is_not_a_wait() {
        assert!(details("(deftest a (await (asyncio.sleep 0)) (time.sleep 0.0) (asyncio.sleep 0 1) (.sleep time 0))\n").is_empty());
        assert_eq!(details("(deftest a (time.sleep 0.01) (time.sleep seconds))\n").len(), 2);
    }

    #[test]
    fn a_module_that_declares_realtime_is_not_judged() {
        assert!(details("(import pytest time)\n(val pytestmark pytest.mark.realtime)\n(defn wait [] (time.sleep 1))\n(deftest a (time.sleep 1))\n").is_empty());
        assert!(details("(var pytestmark [pytest.mark.slow pytest.mark.realtime])\n(deftest a (time.sleep 1))\n").is_empty());
        assert!(details("(val pytestmark #(pytest.mark.realtime))\n(deftest a (time.sleep 1))\n").is_empty());
        assert!(details("(val pytestmark (. pytest mark realtime))\n(deftest a (time.sleep 1))\n").is_empty());
        assert!(details("(val pytestmark (pytest.mark.realtime))\n(deftest a (time.sleep 1))\n").is_empty());
    }

    #[test]
    fn only_the_recorded_module_form_declares() {
        // Other markers, setv bindings, non-top-level bindings, and other names are not declarations (only the forms doeff-hy records as markers are read).
        assert_eq!(details("(val pytestmark pytest.mark.slow)\n(deftest a (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(setv pytestmark pytest.mark.realtime)\n(deftest a (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(defn setup [] (val pytestmark pytest.mark.realtime))\n(deftest a (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(val marks pytest.mark.realtime)\n(deftest a (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(val pytestmark mylib.mark.realtime)\n(deftest a (time.sleep 1))\n").len(), 1);
        assert_eq!(details(";; (val pytestmark pytest.mark.realtime)\n#_(val pytestmark pytest.mark.realtime)\n(deftest a (time.sleep 1))\n").len(), 1);
    }

    #[test]
    fn a_deftest_that_declares_realtime_is_not_judged() {
        assert!(details("(deftest test-overlap {:marks [\"realtime\"]} (time.sleep 0.3))\n").is_empty());
        assert!(details("(deftest test-overlap [tmp-path] \"doc\" {:interpreters [\"x\"] :marks [\"slow\" \"realtime\"]} (time.sleep 0.3))\n").is_empty());
        assert!(details("(deftest test-overlap {:marks #(realtime)} (run (fn [] (time.sleep 0.3))))\n").is_empty());
        // Outside the declared deftest is flagged (neighbouring tests and helpers in the same file).
        let source = "(deftest quiet {:marks [\"realtime\"]} (time.sleep 1))\n(deftest loud (time.sleep 1))\n(defn helper [] (time.sleep 1))\n";
        assert_eq!(details(source), vec!["loud::time.sleep", "helper::time.sleep"]);
    }

    #[test]
    fn only_the_meta_map_of_a_deftest_declares() {
        // Other markers, a map inside the body, and a map of a non-deftest definition are not declarations.
        assert_eq!(details("(deftest a {:marks [\"slow\"]} (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(deftest a (setv meta {:marks [\"realtime\"]}) (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(deftest a (assert True) {:marks [\"realtime\"]} (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(defk a [] {:pre [] :post [] :marks [\"realtime\"]} (time.sleep 1))\n").len(), 1);
        assert_eq!(details("(deftest a {:tags {:marks [\"realtime\"]}} (time.sleep 1))\n").len(), 1);
    }

    #[test]
    fn a_plain_test_function_declares_by_its_decorator() {
        assert!(details("(defn [pytest.mark.realtime] #^ None test-overlap [] (time.sleep 0.3))\n").is_empty());
        assert!(details("(defn [pytest.mark.slow (pytest.mark.realtime)] :async test-overlap [] (await (asyncio.sleep 0.3)))\n").is_empty());
        // A sleep in a definition that appears literally inside a function (a program the test defines inside) is also inside the declared function.
        assert!(details("(defn [pytest.mark.realtime] test-overlap []\n  (defk counted [] (time.sleep 0.1) 1)\n  (run (counted)))\n").is_empty());
        // Other-marker decorators, the arg list (`[…]` after the name), and a `[…]` after a head that is neither deftest nor defn are not declarations.
        assert_eq!(details("(defn [pytest.mark.slow] test-a [] (time.sleep 1))\n"), vec!["test-a::time.sleep"]);
        assert_eq!(details("(defn test-a [pytest.mark.realtime] (time.sleep 1))\n"), vec!["test-a::time.sleep"]);
        assert_eq!(details("(defk [pytest.mark.realtime] helper [] (time.sleep 1))\n").len(), 1);
    }

    #[test]
    fn comments_discarded_quoted_forms_and_strings_are_not_read() {
        assert!(details("(deftest a\n  ;; (time.sleep 1)\n  \"(time.sleep 1)\"\n  #_(time.sleep 1)\n  '(time.sleep 1)\n  `(time.sleep 1)\n  (.write-text path \"(import time)\\n(time.sleep 30)\\n\"))\n").is_empty());
    }
}

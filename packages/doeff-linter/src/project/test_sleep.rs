//! DOEFF211: Hy の検は、実時間が対象だと名乗らない限り壁の時計で眠らない(doeff の ADR-DOE-TIME-001 R3 — card ki-bb3ee40d0af2)。
//!
//! 検が `time.sleep` / `asyncio.sleep` で待つと、待つ物(出来事)が名指されず、実時間の分だけ遅くなり、込んだ機体では時間切れで
//! 赤になる。検は program に doeff-time の効果(Delay など)を出させて仮想の時計(sim-time-handler)で走らせるか、待つ出来事を
//! 名指して待つ。実時間そのもの(本物の thread・本物の asyncio の loop・本物の外の process)が対象の検だけが realtime を名乗り、
//! その中の眠りは当てない。Python の同じ決まりは semgrep の規則 doeff-no-sleep-in-tests が持つ — semgrep は Hy を読めないので、
//! Hy の分はここが持つ(program の眠りの DOEFF210 と同じ読みの部品を使う)。検の置き場(母集団)は repo の設定
//! `[tool.doeff-linter.test_sleep]` にだけ在り、ここには書かない。
//!
//! 名乗りの形(pytest が印として読む 3 つの形 — 前の 2 つは doeff-hy の読み手 pytest_items.py の record_module_binding・macros.hy の
//! _extract-test-meta と同じ):
//!   * module: 最上位の `(val pytestmark pytest.mark.realtime)`(`var` も)。値は印 1 つか、印の list・tuple
//!     (`[pytest.mark.slow pytest.mark.realtime]`)。file の全部の眠りを当てない。
//!   * deftest: meta の map の `:marks` に `"realtime"`(`(deftest test-x {:marks ["realtime"]} …)`)。その deftest の中の眠りを当てない。
//!   * 素の関数の検: defn の decorator の並びに印(`(defn [pytest.mark.realtime] test-x [] …)`)。その関数の中の眠りを当てない。
//! 当てる形(名乗っていない所の、Hy の form の木):
//!   * 頭が `time.sleep` / `asyncio.sleep` の呼び。`(import time :as t)` の別名の `t.sleep`、`(import asyncio [sleep])`・
//!     `(import time [sleep :as nap])` で束ねた名の呼び、method の綴り `(.sleep time 秒)` も。`(Await (asyncio.sleep 秒))`・
//!     `(await (asyncio.sleep 秒))` は中の呼びが当たる。
//!   * 眠りの言い換え `(.wait (threading.Event) 秒)` — その場で作った Event(誰も set しない)を秒つきで待つ。
//! 当てない形:
//!   * 秒が literal の 0 の眠り(`(asyncio.sleep 0)`・`(time.sleep 0.0)` — 時間を待たずに 1 度譲るだけ)。
//!   * 註・`#_` で読み捨てた form・quote した form・文字列の中(子の process に渡す program の本文)。

use std::collections::BTreeMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};

use super::program_sleep::{bound_names, children, headed, is_zero, live, string, text, value_of, SLEEP_MODULES, SLEEP_NAME};

/// 実時間が対象の検の印の名(pytest の marker)。
const MARK: &str = "realtime";
/// module の段の名乗りの束ね先と、束ねる頭(doeff-hy が印として記録する頭)。
const MODULE_MARKS: &str = "pytestmark";
const MODULE_BINDERS: &[&str] = &["val", "var"];
/// 印の式 `pytest.mark.<名>` の前の部分。
const MARK_OWNER: &[&str] = &["pytest", "mark"];
/// 検を定める頭と、その meta の map の印の鍵。
const TEST_HEAD: &str = "deftest";
const MARKS_KEY: &str = ":marks";
/// decorator の並び(頭のすぐ後の `[…]`)で印を名乗れる、素の関数を定める頭。
const FUNCTION_HEADS: &[&str] = &["defn", "defn/a"];
/// 眠りの言い換え: その場で作った Event を秒つきで待つ。
const EVENT_MODULES: &[&str] = &["threading"];
const EVENT_NAME: &str = "Event";
const WAIT_METHOD: &str = ".wait";
/// 定義の外(module の最上位の式)の当たりの、包む定義の名。
const MODULE_SCOPE: &str = "<module>";

/// 当たり 1 つ(眠りの呼びの form の byte の範囲・包む最上位の定義の名・眠りの関数の綴り)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TestSleepHit {
    pub start: usize,
    pub end: usize,
    /// 包む最上位の定義(deftest・defn・defk …)の名。定義の外は `<module>`。
    pub definition: String,
    /// 眠りの関数(`time.sleep`・`asyncio.sleep`・`threading.Event.wait` — 別名で呼んでも元の綴り)。
    pub callee: String,
}

impl TestSleepHit {
    /// 鍵の細目(`<包む定義の名>::<眠りの関数>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, self.callee)
    }
}

/// Hy の検の中身 1 つを判じる(当たりは位置の順)。module が realtime を名乗っていれば当たりは無い。
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

/// 最上位の form が定める名(頭が `def` で始まる form の、最初の記号 — `:async` と decorator の並びと型の注記は飛ばす)。定義でなければ `<module>`。
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

/// 最上位の `(val pytestmark 値)` / `(var pytestmark 値)` が realtime を名乗るか — 値(最後の要素)は印 1 つか、印の list・tuple。
fn module_names_realtime(source: &str, form: &Form) -> bool {
    let Some((head, items)) = headed(source, form) else { return false };
    let binds_marks = MODULE_BINDERS.contains(&head) && items.len() >= 3 && matches!(items[1].node, Node::Symbol) && text(source, items[1]) == MODULE_MARKS;
    binds_marks && items.last().is_some_and(|value| elements(value).into_iter().any(|mark| is_realtime_mark(source, mark)))
}

/// list・tuple の literal ならその要素、そうでなければ form そのもの 1 つ。
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

/// 印の式が realtime か — `pytest.mark.realtime`・`(. pytest mark realtime)`・その呼び出し `(pytest.mark.realtime …)`。
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

/// `(deftest 名 [引数]? "docstring"… {meta} 本文…)` の meta の `:marks` が realtime を含むか — meta は、引数の並びと先頭の文字列を
/// 飛ばした最初の要素が map の時だけ(doeff-hy の deftest と同じ読み)。印は文字列か記号。
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

/// `(defn [decorator …] 名 [引数] 本文…)` の decorator の並びが realtime の印を含むか — 並びは頭のすぐ後の `[…]`(名より前)。
fn function_names_realtime(source: &str, items: &[&Form]) -> bool {
    items.get(1).and_then(|decorators| decorators.bracket_items()).is_some_and(|decorators| live(decorators).into_iter().any(|mark| is_realtime_mark(source, mark)))
}

struct Judge<'a> {
    source: &'a str,
    /// 眠りの関数を指す呼びの頭の綴り → 元の綴り(`time.sleep` / `asyncio.sleep`)。
    sleeps: BTreeMap<String, String>,
    /// Event を作る呼びの頭の綴り → 元の綴り(`threading.Event`)。
    events: BTreeMap<String, String>,
}

impl Judge<'_> {
    /// form の木を歩く(realtime を名乗る deftest・素の関数の中には入らない)。`definition` = 包む最上位の定義の名。
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

    /// 呼びが壁の時計で待つなら、待つ関数の元の綴り。
    fn sleeping_call(&self, head: &str, items: &[&Form]) -> Option<String> {
        let waits = |seconds: Option<&&Form>| !seconds.is_some_and(|seconds| is_zero(self.source, seconds));
        if let Some(original) = self.sleeps.get(head) {
            // (time.sleep 秒)
            return waits(items.get(1)).then(|| original.clone());
        }
        if head.strip_prefix('.') == Some(SLEEP_NAME) {
            // (.sleep time 秒)
            let receiver = items.get(1).filter(|receiver| matches!(receiver.node, Node::Symbol))?;
            let original = self.sleeps.get(&format!("{}.{}", text(self.source, receiver), SLEEP_NAME))?;
            return waits(items.get(2)).then(|| original.clone());
        }
        if head == WAIT_METHOD && items.len() > 2 {
            // (.wait (threading.Event) 秒)
            let (maker, _) = headed(self.source, items[1])?;
            return self.events.get(maker).map(|original| format!("{}{}", original, WAIT_METHOD));
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 当たりの鍵の細目の列。
    fn details(source: &str) -> Vec<String> {
        judge(source).iter().map(TestSleepHit::detail).collect()
    }

    #[test]
    fn a_test_that_sleeps_on_the_wall_clock_is_found() {
        assert_eq!(details("(import time)\n(deftest test-waits\n  (time.sleep 0.3)\n  (assert True))\n"), vec!["test-waits::time.sleep"]);
        assert_eq!(details("(defn :async wait-up [session] (await (asyncio.sleep 0.05)))\n"), vec!["wait-up::asyncio.sleep"]);
        assert_eq!(details("(defk pause [gap] {:pre [] :post []} (<- (Await (asyncio.sleep gap))))\n"), vec!["pause::asyncio.sleep"]);
        // 定義の外(上から順に走る検の script)。
        assert_eq!(details("(import time)\n(time.sleep 1)\n(when ready (time.sleep 2))\n"), vec!["<module>::time.sleep", "<module>::time.sleep"]);
    }

    #[test]
    fn the_hit_spans_the_call_and_names_the_top_level_definition() {
        let source = "(defclass Peer []\n  (defn run [self]\n    (while True (time.sleep 0.5))))\n";
        let hits = judge(source);
        assert_eq!(hits.len(), 1);
        assert_eq!(&source[hits[0].start..hits[0].end], "(time.sleep 0.5)");
        assert_eq!((hits[0].definition.as_str(), hits[0].callee.as_str()), ("Peer", "time.sleep"));
        // :async・decorator の並び・型の注記を飛ばして名を読む。
        assert_eq!(details("(defn :async [decorated] #^ bool all-closed [sessions] (await (asyncio.sleep 0.02)))\n"), vec!["all-closed::asyncio.sleep"]);
    }

    #[test]
    fn aliases_imported_names_and_the_method_spelling_are_followed() {
        assert_eq!(details("(import time :as t)\n(deftest a (t.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import asyncio [sleep])\n(deftest a (await (sleep 1)))\n"), vec!["a::asyncio.sleep"]);
        assert_eq!(details("(import time [monotonic sleep :as nap])\n(deftest a (nap 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import time)\n(deftest a (.sleep time 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(import time :as t)\n(deftest a (.sleep t 1))\n"), vec!["a::time.sleep"]);
        // 眠りの module でない物の sleep は数えない。
        assert!(details("(import mylib [sleep])\n(deftest a (sleep 1))\n").is_empty());
        assert!(details("(deftest a (.sleep stub 1))\n").is_empty());
    }

    #[test]
    fn a_fresh_event_waited_with_seconds_is_a_sleep() {
        assert_eq!(details("(import threading)\n(defk counted [] (.wait (threading.Event) 0.1) 1)\n"), vec!["counted::threading.Event.wait"]);
        assert_eq!(details("(import threading [Event])\n(deftest a (.wait (Event) :timeout 0.1))\n"), vec!["a::threading.Event.wait"]);
        assert_eq!(details("(import threading :as th)\n(deftest a (.wait (th.Event) 0.1))\n"), vec!["a::threading.Event.wait"]);
        // 誰かが set する Event の待ち・秒の無い待ちは眠りではない。
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
        // 他の印・setv の束ね・最上位でない束ね・別の名は名乗りではない(doeff-hy が印として記録する形だけを読む)。
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
        // 名乗った deftest の外は当たる(同じ file の隣の検・helper)。
        let source = "(deftest quiet {:marks [\"realtime\"]} (time.sleep 1))\n(deftest loud (time.sleep 1))\n(defn helper [] (time.sleep 1))\n";
        assert_eq!(details(source), vec!["loud::time.sleep", "helper::time.sleep"]);
    }

    #[test]
    fn only_the_meta_map_of_a_deftest_declares() {
        // 他の印・本文の中の map・deftest でない定義の map は名乗りではない。
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
        // 関数の中に字面で在る定義(検が中で定める program)の眠りも、名乗った関数の中。
        assert!(details("(defn [pytest.mark.realtime] test-overlap []\n  (defk counted [] (time.sleep 0.1) 1)\n  (run (counted)))\n").is_empty());
        // 他の印の decorator・引数の並び(名の後の `[…]`)・deftest でも defn でもない頭の `[…]` は名乗りではない。
        assert_eq!(details("(defn [pytest.mark.slow] test-a [] (time.sleep 1))\n"), vec!["test-a::time.sleep"]);
        assert_eq!(details("(defn test-a [pytest.mark.realtime] (time.sleep 1))\n"), vec!["test-a::time.sleep"]);
        assert_eq!(details("(defk [pytest.mark.realtime] helper [] (time.sleep 1))\n").len(), 1);
    }

    #[test]
    fn comments_discarded_quoted_forms_and_strings_are_not_read() {
        assert!(details("(deftest a\n  ;; (time.sleep 1)\n  \"(time.sleep 1)\"\n  #_(time.sleep 1)\n  '(time.sleep 1)\n  `(time.sleep 1)\n  (.write-text path \"(import time)\\n(time.sleep 30)\\n\"))\n").is_empty());
    }
}

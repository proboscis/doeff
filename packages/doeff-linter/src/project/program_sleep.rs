//! DOEFF210: Hy の program は壁の時計で眠らない(doeff の ADR-DOE-TIME-001 R1・R2 — card ki-3c3d85253c2c)。
//!
//! program(defk・defp の本体 — @do の generator)が時間を待つ時は doeff-time の効果(Delay・WaitUntil・WaitWithin …)を出し、
//! 1 秒が何であるかは呼び手が置く時間の handler が決める。program が `time.sleep` / `asyncio.sleep` を呼ぶと、どの handler を
//! 置いても壁の時計で待ち、その program の検は仮想の時計で走れない。Python の同じ決まりは semgrep の規則
//! doeff-no-sleep-in-programs が持つ — semgrep は Hy を読めないので、Hy の分はここが持つ。program の置き場(母集団)は repo の
//! 設定 `[tool.doeff-linter.program_sleep]` にだけ在り、ここには書かない。
//!
//! 当てる形(Hy の form の木):
//!   * defk・defp の本体の中(字面で内側に在る fn・defn・deff の中も)の、頭が `time.sleep` / `asyncio.sleep` の呼び。
//!     `(import time :as t)` の別名の `t.sleep`、`(import asyncio [sleep])`・`(import time [sleep :as nap])` で束ねた名の呼びも。
//!     `(Await (asyncio.sleep 秒))` は中の呼びが当たる。
//! 当てない形:
//!   * 秒が literal の 0 の呼び(`(asyncio.sleep 0)`・`(time.sleep 0.0)` — 時間を待たずに 1 度譲るだけ)。
//!   * handler の実装(壁の時計で待つのは handler の責務): defhandler の本体・引数の並びが `[effect k]` の定義(defk・deff・
//!     defn・fn)・契約の辞書の `:tags` の `:role` が `foundation`(汎用の I/O の役)の defk・defp。最後の 1 つは handler から
//!     だけ呼ばれる helper の名乗り — 定義が自分の辞書に書いた時だけ読み、module の頭のタグでは外さない。
//!   * program でない定義(最上位の deff・defn・defclass の method)— 素の callable は効果を出せない。deff の是非は
//!     DOEFF111・203 が持つ。
//!   * 註・`#_` で読み捨てた form・quote した form。

use std::collections::BTreeMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};

/// 眠りの関数を持つ module(Python の標準の 2 つ)と、その関数の名。検の眠りの規則(DOEFF211・test_sleep.rs)も同じ 2 つを読む。
pub(super) const SLEEP_MODULES: &[&str] = &["time", "asyncio"];
pub(super) const SLEEP_NAME: &str = "sleep";
/// program を定める頭(本体が @do の generator になる)。
const PROGRAM_HEADS: &[&str] = &["defk", "defp"];
/// 引数の並びが `[effect k]` なら handler の実装として読む頭と、その頭の引数の並びの位置。
const CALLABLE_HEADS: &[(&str, usize)] = &[("defk", 2), ("deff", 2), ("defn", 2), ("defn/a", 2), ("fn", 1), ("fn/a", 1)];
/// handler の実装の引数の並び。
const HANDLER_PARAMS: &[&str] = &["effect", "k"];
/// 汎用の I/O の役(handler からだけ呼ばれる helper の名乗り)。
const IO_ROLE: &str = "foundation";
/// 契約の辞書を探す範囲(名の後の要素の数 — 引数の並びと docstring を飛ばす)。
const CONTRACT_REACH: usize = 4;

/// 当たり 1 つ(眠りの呼びの form の byte の範囲・包む program の名・眠りの関数の綴り)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgramSleepHit {
    pub start: usize,
    pub end: usize,
    /// 包む program(defk・defp)の名。
    pub definition: String,
    /// 眠りの関数(`time.sleep` か `asyncio.sleep` — 別名で呼んでも元の綴り)。
    pub callee: String,
}

impl ProgramSleepHit {
    /// 鍵の細目(`<program の名>::<眠りの関数>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, self.callee)
    }
}

/// Hy の中身 1 つを判じる(当たりは位置の順)。
pub fn judge(source: &str) -> Vec<ProgramSleepHit> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let judge = Judge { source, sleeps: bound_names(source, &forms, SLEEP_MODULES, SLEEP_NAME) };
    let mut out = Vec::new();
    forms.iter().for_each(|form| judge.walk(form, None, &mut out));
    out.sort_by_key(|hit| hit.start);
    out
}

/// form の綴り。
pub(super) fn text<'a>(source: &'a str, form: &Form) -> &'a str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// 読み捨てを除いた要素。
pub(super) fn live(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// `( … )` の頭の記号の綴りと、読み捨てを除いた要素。
pub(super) fn headed<'a, 'f>(source: &'a str, form: &'f Form) -> Option<(&'a str, Vec<&'f Form>)> {
    let items = live(form.paren_items()?);
    let head = items.first().filter(|first| matches!(first.node, Node::Symbol)).map(|first| text(source, first))?;
    Some((head, items))
}

/// form の子(quote した form と読み捨てた form には入らない)。
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

/// file の中で、module の列 modules のどれかの名 name(例 time・asyncio の sleep)を指す呼びの頭の綴り → 元の綴り(`<module>.<name>`)。
/// 素の綴り `<module>.<name>` と、file の中の `(import …)`(関数の中の物も)が束ねた名を集める。
pub(super) fn bound_names(source: &str, forms: &[Form], modules: &[&str], name: &str) -> BTreeMap<String, String> {
    let mut bound: BTreeMap<String, String> = modules.iter().map(|module| (format!("{}.{}", module, name), format!("{}.{}", module, name))).collect();
    forms.iter().for_each(|form| bind_imports(source, form, modules, name, &mut bound));
    bound
}

/// `(import …)` 1 つ(か、その子の中の物)が束ねた名を足す。`(import time)`・`(import time :as t)` は `<名>.sleep`、
/// `(import time [sleep])`・`(import time [sleep :as nap])`・`(import time *)` は束ねた名そのもの。
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

/// 名を並べた import の `[…]` の中で name を束ねた名(`[sleep]` → `sleep`・`[sleep :as nap]` → `nap`・無ければ None)。
fn imported_name(source: &str, names: &Form, name: &str) -> Option<String> {
    let names = live(names.bracket_items()?);
    let at = names.iter().position(|bound| matches!(bound.node, Node::Symbol) && text(source, bound) == name)?;
    match (names.get(at + 1), names.get(at + 2)) {
        (Some(keyword), Some(alias)) if text(source, keyword) == ":as" => Some(text(source, alias).to_string()),
        _ => Some(name.to_string()),
    }
}

/// 秒が literal の 0 か(`0`・`0.0`・`0.`)。
pub(super) fn is_zero(source: &str, seconds: &Form) -> bool {
    matches!(seconds.node, Node::Number) && text(source, seconds).parse::<f64>().is_ok_and(|value| value == 0.0)
}

struct Judge<'a> {
    source: &'a str,
    /// 眠りの関数を指す呼びの頭の綴り → 元の綴り(`time.sleep` / `asyncio.sleep`)。
    sleeps: BTreeMap<String, String>,
}

impl Judge<'_> {
    /// form の木を歩く。`program` = 今いる program(defk・defp)の名(program の外・handler の実装の中は None)。
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

    /// 引数の並びが `[effect k]` の定義か(handler の実装 — DOEFF130 の翻訳の handler と同じ見分け)。
    fn takes_effect_and_continuation(&self, head: &str, items: &[&Form]) -> bool {
        let Some((_, at)) = CALLABLE_HEADS.iter().find(|(callable, _)| *callable == head) else { return false };
        items
            .get(*at)
            .and_then(|params| params.bracket_items())
            .is_some_and(|params| live(params).iter().map(|param| text(self.source, param)).eq(HANDLER_PARAMS.iter().copied()))
    }

    /// 契約の辞書の `:tags` が汎用の I/O の役を名乗るか — 名の後の引数の並びと docstring を飛ばした最初の辞書を読む。
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

/// 辞書 `{鍵 値 …}` の鍵 key の値。
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

/// 文字列の literal の中身。
pub(super) fn string<'a>(source: &'a str, form: &Form) -> Option<&'a str> {
    match &form.node {
        Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => source.get(body.start..body.end),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 当たりの鍵の細目の列。
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
        // 関数の中の import も読む。
        assert_eq!(details("(defk a [] (import time :as clock) (clock.sleep 1))\n"), vec!["a::time.sleep"]);
        // 眠りの module でない物の sleep は数えない(doeff-time の sleep でも、自前の関数でも)。
        assert!(details("(import mylib [sleep])\n(defk a [] (sleep 1))\n").is_empty());
        assert!(details("(import mylib :as time2)\n(defk a [] (time2.sleep 1))\n").is_empty());
    }

    #[test]
    fn nested_callables_inside_a_program_count_as_the_program() {
        assert_eq!(details("(defk a [] (run-later (fn [] (time.sleep 1))))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] (defn helper [] (time.sleep 1)) (helper))\n"), vec!["a::time.sleep"]);
        // 素の定義の中の program も program。
        assert_eq!(details("(defclass Box []\n  (defn build [self]\n    (defk inner [] (time.sleep 1))\n    inner))\n"), vec!["inner::time.sleep"]);
    }

    #[test]
    fn a_yield_of_zero_seconds_is_not_a_wait() {
        assert!(details("(defk a [] (<- (Await (asyncio.sleep 0))) (time.sleep 0.0) (asyncio.sleep 0 result))\n").is_empty());
        // 0 でない literal と、値の分からない式は待ち。
        assert_eq!(details("(defk a [] (time.sleep 0.01) (time.sleep seconds) (time.sleep (- 1 1)))\n").len(), 3);
    }

    #[test]
    fn a_handler_implementation_may_sleep() {
        // defhandler の節。
        assert!(details("(defhandler wall-clock\n  (Delay [seconds]\n    (time.sleep seconds)\n    (resume None)))\n").is_empty());
        // program の本体の中に書いた defhandler の節も handler の実装(入れ子そのものは別の規則が止める)。
        assert!(details("(defk build []\n  (defhandler wall-clock\n    (Delay [seconds]\n      (time.sleep seconds)\n      (resume None)))\n  wall-clock)\n").is_empty());
        // (effect k) を受ける定義。
        assert!(details("(defk raw-handler [effect k]\n  {:pre [] :post []}\n  (time.sleep 1)\n  (<- (Pass effect k)))\n").is_empty());
        assert!(details("(defk a [] (with-handler (fn [effect k] (time.sleep 1)) body))\n").is_empty());
        // 汎用の I/O の役を名乗る helper(handler からだけ呼ばれる)。
        let helper = "(defk group-gone-by [child deadline]\n  {:pre [(: deadline float)] :post [(: % bool)] :tags {:context \"process\" :role \"foundation\"}}\n  \"docstring\"\n  (time.sleep 0.02)\n  True)\n";
        assert!(details(helper).is_empty());
        assert!(details("(defp settle {:post [(: % None)] :tags {:context \"x\" :role \"foundation\"}} (time.sleep 1))\n").is_empty());
        // docstring が辞書の前でも読む。
        assert!(details("(defk a [] \"doc\" {:pre [] :post [] :tags {:context \"x\" :role \"foundation\"}} (time.sleep 1))\n").is_empty());
    }

    #[test]
    fn only_the_definitions_own_io_role_excuses_it() {
        // 他の役・役の無い :tags・:tags の無い契約は program。
        assert_eq!(details("(defk a [] {:pre [] :post [] :tags {:context \"x\" :role \"program\"}} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] {:pre [] :post [] :tags {:context \"x\"}} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        assert_eq!(details("(defk a [] {:pre [] :post []} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // module の頭のタグは外さない(定義が自分で名乗る)。
        assert_eq!(details("(val MODULE-TAGS {:context \"x\" :role \"foundation\"})\n(defk a [] {:pre [] :post []} (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // 本体の中の辞書は契約の辞書ではない。
        assert_eq!(details("(defk a [] (setv t {:tags {:role \"foundation\"}}) (time.sleep 1))\n"), vec!["a::time.sleep"]);
        // 引数の名が違う 2 引数の定義は handler の実装ではない。
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

//! DOEFF126 の 2 つ目の形: 引数で受けた関数を素で呼ぶ所 — ただし呼び手がその引数に defk か fnk を渡している時だけ拾う。
//!
//! 事実(agora-redesign #798・agora の L1550 で直した): `rows-by-text` が引数 `field-of` を `(setv value (field-of row))` と素で
//! 呼んでいたが、呼び手はそこに fnk(`payload-of` を呼ぶ)を渡していた。値は常に Program になり、`isinstance value str` が常に偽で
//! 索引が常に空になる本物の欠陥だった。coordinator の依頼 2026-09-28。
//!
//! 流れの追い方(拾える範囲):
//! 1. repo の全部の呼び `(g … 引数 …)` のうち、引数が repo の defk の名か `(fnk …)` の物を集める(g は import と定義の場所で module まで
//!    解く・位置の引数は何番目か、keyword の引数は名で控える)。
//! 2. 呼び先 g の最上位の定義(defk・deff・defn・defn/a)の引数の並びでその引数の名を引き、本体の中でその名を頭にした呼び `(名 …)` が
//!    Program として渡す所(`(<- …)` の右辺・`(! …)`・`(return …)`・`(yield …)`・Program を受ける呼びの引数)の外に在れば、呼び先の
//!    その呼びを違反にする(名への束ね `(setv v (名 …))` も違反 — 呼び手が defk を渡しているので、答えではなく Program が束なる)。
//!
//! 拾えない範囲(ADR-DOE-HY-007 R14 に書く): 呼び手が defk を変数や欄に入れてから渡す形・partial などで包んで渡す形、呼び先がその引数を
//! さらに別の関数へ渡してそこで素で呼ぶ形(1 段だけ追う)、呼び先が method・入れ子の関数・名で引けない物、repo の外の呼び手。

use std::collections::{BTreeMap, BTreeSet};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};

use super::bare_calls::DefkNames;
use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, live_items, span_of, Hy, Scope};

/// 引数の位置(位置の引数は何番目か・keyword の引数は mangle した名)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum Slot {
    Position(usize),
    Keyword(String),
}

/// 呼び手が引数に Program を返す関数を渡した事実 1 つ。
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PassedProgram {
    pub slot: Slot,
    /// 渡した物の綴り(defk の名か `fnk`)。
    pub passed: String,
    /// 渡した所(`<path>` の `<定義>`)。
    pub caller: String,
}

/// 呼び先(module まで含めた名)ごとの、Program を返す関数を受ける引数。
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct ProgramParams {
    by_callee: BTreeMap<String, Vec<PassedProgram>>,
}

impl ProgramParams {
    /// 1 つの source の呼びから、引数に defk か fnk を渡している物を積む(rel は渡した所の説明のため)。
    pub fn collect(&mut self, source: &str, rel: &str, scope: Scope<'_>, defks: &DefkNames) {
        let mut reader = Reader::new(source, 0, source.len());
        let forms = reader.read_all();
        let hy = Hy { src: source };
        for (definition, form) in top_definitions(&hy, &forms) {
            let caller = format!("{} の {}", rel, definition);
            self.collect_in(&hy, form, scope, defks, &caller);
        }
    }

    /// form とその子孫の呼びを見る。
    fn collect_in(&mut self, hy: &Hy<'_>, form: &Form, scope: Scope<'_>, defks: &DefkNames, caller: &str) {
        if let Some(items) = live(form) {
            let head = items.first().and_then(|h| hy.symbol(h)).filter(|h| !h.starts_with('.'));
            if let Some(head) = head {
                let callee = scope.qualify(head);
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
                    let passed = arg.and_then(|a| match (hy.symbol(a), hy.head(a)) {
                        (Some(symbol), _) if defks.contains(&scope.qualify(symbol)) => Some(symbol.to_string()),
                        (None, Some("fnk")) => Some("fnk".to_string()),
                        _ => None,
                    });
                    if let Some(passed) = passed {
                        self.by_callee.entry(callee.clone()).or_default().push(PassedProgram { slot, passed, caller: caller.to_string() });
                    }
                }
            }
        }
        for child in children(form) {
            self.collect_in(hy, child, scope, defks, caller);
        }
    }

    /// 別の file で集めた事実を足す(file ごとに並べて集めるため)。
    pub fn merge(&mut self, other: ProgramParams) {
        for (callee, facts) in other.by_callee {
            self.by_callee.entry(callee).or_default().extend(facts);
        }
    }

    /// 呼び先の、Program を返す関数を受ける引数の事実。
    fn of(&self, callee: &str) -> &[PassedProgram] {
        self.by_callee.get(callee).map(Vec::as_slice).unwrap_or(&[])
    }

    /// 事実の数(報告のため)。
    pub fn len(&self) -> usize {
        self.by_callee.values().map(Vec::len).sum()
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.by_callee.is_empty()
    }
}

/// 引数で受けた関数の素の呼び 1 件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParamCall {
    /// 呼び先の定義の名(mangle 済み)と頭。
    pub definition: String,
    pub container: String,
    /// 素で呼んだ引数の名(書かれたとおり)。
    pub param: String,
    /// 呼び手が渡した物(defk の名か fnk)と、渡した所(最初の 1 つ)。
    pub passed: String,
    pub caller: String,
    pub span: ByteSpan,
}

impl ParamCall {
    /// 登録簿の鍵の細目(`<定義>::<引数>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, hy_mangle(&self.param))
    }
}

/// 最上位の関数の定義(`do` と `eval-and-compile` の中も)の名と form。
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

/// 定義の form の名(`(defk [decorators]? 名 …)`・`#^ T 名`)。定義でなければ None。
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

/// 引数の並びの名(位置の引数の順・keyword だけの引数も名で引けるように全部)。`*` の後ろは位置では引かない。
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

/// 中身を Program として渡す形・Program を受ける doeff-hy の形・Program の位置を枝へ受け継ぐ形。
const PROGRAM_POSITIONS: &[&str] = &["!", "yield", "yield-from", "return", "await"];
const PROGRAM_TAKING_FORMS: &[&str] = &["with-handlers", "maybe", "result", "on-raise", "absent-as", "do!"];
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let", "match"];

/// 1 つの source の、呼び手が Program を返す関数を渡している引数を、本体が素で呼んでいる所を全部拾う(同じ定義の同じ引数は 1 件)。
pub fn param_calls_in(source: &str, scope: Scope<'_>, defks: &DefkNames, params: &ProgramParams) -> Vec<ParamCall> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found: Vec<ParamCall> = Vec::new();
    for (definition, form) in top_definitions(&hy, &forms) {
        let passed = params.of(&format!("{}.{}", scope.module, definition));
        if passed.is_empty() {
            continue;
        }
        let items = live(form).unwrap_or_default();
        let container = items.first().and_then(|h| hy.symbol(h)).unwrap_or("").to_string();
        let list_at = match items.get(1) {
            Some(first) if first.bracket_items().is_some() => 3,
            _ => 2,
        };
        let Some(list) = items.get(list_at).filter(|l| l.bracket_items().is_some()) else { continue };
        let (positional, all) = parameters(&hy, list);
        // 引数の名 → 渡した物(最初の 1 つ)。
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
        let walker = Walker { hy: &hy, scope, defks, targets: &targets, definition: &definition, container: &container };
        for body in items.iter().skip(list_at + 1) {
            walker.walk(body, false, &mut found);
        }
    }
    let mut seen = BTreeSet::new();
    found.retain(|call| seen.insert(call.detail()));
    found
}

/// Program として渡す所の外で、引数の名を頭にした呼びを探す道具。
struct Walker<'a, 'h> {
    hy: &'h Hy<'a>,
    scope: Scope<'a>,
    defks: &'a DefkNames,
    targets: &'h BTreeMap<String, &'h PassedProgram>,
    definition: &'h str,
    container: &'h str,
}

impl Walker<'_, '_> {
    /// form を下る。`program` はこの form が Program として渡される位置か。
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

    /// `( … )` 1 つ — 引数の名を頭にした呼びが Program の位置の外なら積み、子へ Program の位置を配る。
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

    /// 2 つの source(呼び先 m.callee と呼び手 m.caller)で、呼び先の素の呼びを (定義::引数 ← 渡した物) の列にする。
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
        // rows-by-text と pick は素で呼ぶ(束ね・内包表記)。safe は (<- …) と (! …) で受けるので拾わない。
        assert_eq!(found(callee, caller), vec!["rows_by_text::field_of <- fnk", "pick::key_of <- payload-of"]);
    }

    #[test]
    fn plain_functions_passed_as_arguments_are_not_traced() {
        let callee = "(defk rows-by-text [rows field-of] (for [row rows] (setv value (field-of row))) rows)\n";
        // 呼び手が素の関数(fn・deff の名)を渡すなら素で呼んでよい。defk を変数に入れてから渡す形は追えない(拾わない)。
        let caller = "(defk a [t] (! (rows-by-text t (fn [row] (.get row \"s\")))))\n(defk b [t] (setv f payload-of) (! (rows-by-text t f)))\n";
        assert!(found(callee, caller).is_empty());
    }
}

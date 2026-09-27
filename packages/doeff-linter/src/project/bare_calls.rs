//! DOEFF126: defk の定義を素で呼んでいる所を拾う。
//!
//! defk を素で呼ぶと、答えではなく Program が返る。型の誤りで落ちずに、静かに間違った値として流れる(agora-redesign #798 の直しの便で、
//! defk に改めた `latest-by-ref` を deff と検 4 file が素のまま呼んでいた — 検で見つかった)。defn / deff を defk に改める便が何百と
//! 続くので、呼び手の取り残しを見張る。coordinator の決定 2026-09-28(戻せる・#798 に記録)。
//!
//! defk と分かる定義 = repo の Hy の file の最上位の `(defk 名 …)`(`do` と `eval-and-compile` の中も)。呼びの頭の綴りは file の import と
//! 定義の場所で module まで解き(`smells::Scope`)、defk の集合と比べる。追えない呼び(引数で受けた関数・method)は拾わない。
//!
//! 拾うのは、呼びの答えを値として使う所だけ(Program が値の代わりに流れて静かに間違う所): 比べ・演算・真偽の組み合わせ
//! (`=`・`+`・`in`・`not`・`and` …)、答えを読む組み込みの関数(`len`・`str`・`get`・`sorted`・`isinstance` …)、method の的と引数
//! (`(.get (f …) "欄")`・`(.append out (f …))`)と属性(`(. (f …) 欄)`)、条件(`if`・`when`・`while` の頭・`cond` の条件)、繰り返しの元(`for` の束ねと
//! 内包表記の元)、record の欄(頭が大文字の型を作る呼び — doeff の package の effect は除く)。その位置の中の `if`・`when`・`cond`・
//! `do`・`let` の枝も答えとして使う所のまま。
//! 拾わない: `(<- …)` の右辺・`(! …)`・`(return …)`、Program を受ける呼びの引数(repo の関数に渡す形も — Program を受けて走らせる
//! 関数(run-on など)かもしれず、追えない)、名への束ね(後で Program として渡すかもしれない)。

use std::collections::BTreeSet;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};

use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, span_of, Hy, Scope};

/// defk の集合(module まで含めた名・mangle 済み)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DefkNames {
    names: BTreeSet<String>,
}

impl DefkNames {
    /// module まで含めた名の列から作る(検のため)。
    pub fn of(names: &[&str]) -> Self {
        DefkNames { names: names.iter().map(|n| n.to_string()).collect() }
    }

    /// 別の集合を足す。
    pub fn extend(&mut self, other: DefkNames) {
        self.names.extend(other.names);
    }

    /// module まで含めた名が defk か。
    pub fn contains(&self, qualified: &str) -> bool {
        self.names.contains(qualified)
    }

    /// 数(報告のため)。
    pub fn len(&self) -> usize {
        self.names.len()
    }

    /// 空か。
    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }
}

/// 1 つの source の最上位の defk の名(`<module>.<名>`)。
pub fn defk_names_in(source: &str, module: &str) -> DefkNames {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut names = BTreeSet::new();
    let mut pending: Vec<&Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().copied()),
            Some("defk") => {
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form.map(|f| match &f.node {
                    Node::Annotated { target: Some(target), .. } => hy.text(target),
                    _ => hy.text(f),
                });
                if let Some(name) = name.filter(|n| !n.is_empty()) {
                    names.insert(format!("{}.{}", module, hy_mangle(name)));
                }
            }
            _ => {}
        }
    }
    DefkNames { names }
}

/// 素の呼び 1 件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BareCall {
    /// 呼びを含む最上位の定義の名(mangle 済み — 定義の外なら `<module>`)。
    pub definition: String,
    /// 呼びを含む定義の頭(defk・deff・defn・deftest …)— 説明の文のため。
    pub container: String,
    /// 呼んだ defk の綴り(書かれたとおり)。
    pub callee: String,
    pub span: ByteSpan,
}

impl BareCall {
    /// 登録簿の鍵の細目(`<定義>::<呼んだ defk>`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.definition, hy_mangle(&self.callee))
    }
}

/// 答えを値として読む Python の演算子と組み込みの関数(引数の全部が答えとして使う所)。
const VALUE_HEADS: &[&str] = &[
    "=", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "//", "%", "**", "in", "not-in", "not", "and", "or", "is", "is-not", "len", "str",
    "int", "float", "bool", "tuple", "list", "dict", "set", "frozenset", "sorted", "reversed", "any", "all", "sum", "min", "max", "repr",
    "get", "isinstance", "print", "enumerate", "zip", "iter", "next", "hash", "format", "abs", "round",
];

/// 答えとして使う位置を中の枝へ受け継ぐ形(条件の式は別に答えとして使う所)。
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let"];

/// 内包表記(元は items[2])。
const COMPREHENSIONS: &[&str] = &["lfor", "sfor", "gfor", "dfor"];

/// 定義の頭(呼びを含む定義の名を取るため)。
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler", "deftest", "defeffect"];

/// 1 つの source の、答えとして使う所で呼んだ defk を全部拾う(同じ定義の同じ呼び先は最初の 1 件だけ)。
pub fn bare_calls_in(source: &str, scope: Scope<'_>, defks: &DefkNames) -> Vec<BareCall> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let walker = Walker { hy: Hy { src: source }, scope, defks };
    let mut found: Vec<BareCall> = Vec::new();
    for form in &forms {
        walker.top(form, &mut found);
    }
    let mut seen = BTreeSet::new();
    found.retain(|call| seen.insert(call.detail()));
    found
}

/// 素の呼びを探して木を下る道具。
struct Walker<'a> {
    hy: Hy<'a>,
    scope: Scope<'a>,
    defks: &'a DefkNames,
}

impl Walker<'_> {
    /// 最上位の form 1 つ — 定義なら名と頭を控えて本体を下り、`do` などは中へ入る。
    fn top(&self, form: &Form, out: &mut Vec<BareCall>) {
        let head = self.hy.head(form);
        match head {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                for child in live(form).unwrap_or_default().into_iter().skip(1) {
                    self.top(child, out);
                }
            }
            Some(h) if DEFINITION_HEADS.contains(&h) => {
                let items = live(form).unwrap_or_default();
                let name_form = match items.get(1) {
                    Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                    other => other.copied(),
                };
                let name = name_form
                    .map(|f| match &f.node {
                        Node::Annotated { target: Some(target), .. } => self.hy.text(target),
                        _ => self.hy.text(f),
                    })
                    .unwrap_or("");
                let context = Context { definition: hy_mangle(name), container: h.to_string() };
                for child in items.into_iter().skip(1) {
                    self.walk(child, false, &context, out);
                }
            }
            _ => self.walk(form, false, &Context { definition: format!("<{}>", self.scope.module), container: "module".to_string() }, out),
        }
    }

    /// form を下る。`value` はこの form の答えが値として使われる位置か。
    fn walk(&self, form: &Form, value: bool, context: &Context, out: &mut Vec<BareCall>) {
        self.walk_at(form, value, false, context, out);
    }

    /// form を下る。`yielded` はこの form が effect として出される位置(`(<- … form)` の右辺・`(! form)`・`(yield form)`)か。
    fn walk_at(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.call(form, value, yielded, context, out),
            _ => {
                for child in children(form) {
                    self.walk(child, false, context, out);
                }
            }
        }
    }

    /// `( … )` 1 つ — 自分が defk の呼びで答えとして使われているなら積み、子へ「答えとして使う位置か」を配る。
    fn call(&self, form: &Form, value: bool, yielded: bool, context: &Context, out: &mut Vec<BareCall>) {
        let items = live(form).unwrap_or_default();
        let head = items.first().and_then(|h| self.hy.head_text(h));
        let qualified = head.filter(|h| !h.starts_with(':')).map(|h| self.scope.qualify(h));
        let is_defk = qualified.as_deref().is_some_and(|q| self.defks.contains(q));
        if let (true, true, Some(callee)) = (is_defk, value, head) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.to_string(), span: span_of(form) });
        }
        let doeff = qualified.as_deref().is_some_and(|q| q.starts_with("doeff"));
        // record の欄は答えとして使う所 — ただし effect として出す型(`(<- (AnswerLater :answer (f …)))`)の欄は Program を運ぶことがあるので除く。
        let constructor = head.is_some_and(|h| h.chars().next().is_some_and(|c| c.is_ascii_uppercase())) && !doeff && !is_defk && !yielded;
        let binds = head == Some("<-");
        let last = items.len().saturating_sub(1);
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_value = match head {
                Some(h) if VALUE_HEADS.contains(&h) => true,
                Some(".") => index == 1,
                // method の的も引数も答えとして使う所(`(.get (f …) "欄")`・`(.append out (f …))`)。
                Some(h) if h.starts_with('.') && h.len() > 1 => true,
                Some("if" | "when" | "unless" | "while") if index == 1 => true,
                Some("cond") => index % 2 == 1 || value,
                Some(h) if BRANCHING_FORMS.contains(&h) => value,
                Some(h) if COMPREHENSIONS.contains(&h) => index == 2,
                Some("for") if index == 1 => {
                    // `(for [x (f …)] …)` — 束ねの列の元(奇数の位置)が繰り返しの元。
                    let sources = child.bracket_items().map(super::smells::live_items).unwrap_or_default();
                    for (at, source) in sources.iter().enumerate() {
                        self.walk(source, at % 2 == 1, context, out);
                    }
                    continue;
                }
                _ => constructor && !matches!(child.node, Node::Keyword),
            };
            let child_yielded = match head {
                Some("!" | "yield" | "yield-from") => true,
                _ => binds && (index == last || items.get(index + 1).is_some_and(|next| matches!(next.node, Node::Keyword))),
            };
            self.walk_at(child, child_value, child_yielded, context, out);
        }
    }
}

/// 下っている所の定義(鍵と説明のため)。
struct Context {
    definition: String,
    container: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// module m・import の束縛 bindings で素の呼びを (定義::呼び先) の列にする。defk は `m.fetch` と `lib.load`。
    fn found(source: &str, bindings: &[(&str, &str)]) -> Vec<String> {
        let bindings: BTreeMap<String, String> = bindings.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let defks = DefkNames::of(&["m.fetch", "lib.load"]);
        bare_calls_in(source, Scope { module: "m", bindings: &bindings }, &defks).iter().map(|c| c.detail()).collect()
    }

    #[test]
    fn defk_names_come_from_top_level_defk_definitions() {
        let names = defk_names_in("(defk fetch [x] x)\n(deff plain [x] x)\n(do (defk inner [] 1))\n(defk #^ int typed [] 1)\n", "app.core.x");
        assert!(names.contains("app.core.x.fetch") && names.contains("app.core.x.inner") && names.contains("app.core.x.typed"));
        assert!(!names.contains("app.core.x.plain"));
        assert_eq!(names.len(), 3);
    }

    #[test]
    fn bare_calls_are_found_where_the_answer_is_used_as_a_value() {
        let source = r#"(deff text-at [ref] (.get (fetch ref) "text"))
(defn count-rows [] (len (load "a")))
(deff collect [out ref] (.append out (fetch ref)))
(defk checks [ref]
  (when (fetch ref) (return 1))
  (val same (= (fetch ref) ref))
  (val field (. (fetch ref) text))
  (val row (Row :text (fetch ref)))
  (<- later (AnswerLater :answer (fetch ref)))
  (for [x (load "b")] (print x))
  (val xs (lfor y (fetch ref) y))
  (return (+ 1 (if same (fetch 1) 2))))
(val MODULE-LEVEL (str (fetch 0)))"#;
        assert_eq!(found(source, &[("load", "lib")]), vec!["text_at::fetch", "count_rows::load", "collect::fetch", "checks::fetch", "checks::load", "<m>::fetch"]);
    }

    #[test]
    fn programs_that_are_passed_bound_or_returned_are_not_bare() {
        let source = r#"(defk top []
  (<- out (run (with_handlers [h] (fetch 1))))
  (<- many (Gather [(fetch 1) (fetch 2)]))
  (<- task (Spawn (fetch 3)))
  (<- r (result (maybe (fetch 4))))
  (<- chosen (if flag (fetch 6) (load 7)))
  (<- nested (fetch (load 8)))
  (val later (fetch 9))
  (return out))
(deff runner [x] (run-on (fetch x) handlers))
(deff builder [x] (fetch x))
(deff callback [x] (helper x) (len (other.fetch x)) (len (f x)))"#;
        // Program を受ける呼びの引数・名への束ね・関数の答えとして返す形は拾わない(Program として渡すかもしれない)。
        // 追えない呼び(helper・method・引数の f)も拾わない。
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("Gather", "doeff"), ("Spawn", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }
}

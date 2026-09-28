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
//!
//! 定義の外(module の最上位の式 — `(val TABLE [(entry "a" (f …)) …])`・`(setv PAIRS #(…))`)は、位置を問わず答えとして使う所として拾う。
//! 定義の外には Program を走らせる所が無く、表の行や名に束ねた Program は値の代わりに流れる(実弾 = agora-controllers c6271008a が
//! scripts/land_focus_gate.hy の最上位の表に defk の tests-of を run 無しで足し、`--all` が起動で落ちた)。ただし `run`・`<-`・`!`・
//! doeff の package の呼びの中、defk の呼びの引数、関数の本体(`fn`・`fnk`・`defmacro`・`defclass` …)、quote / quasiquote の中は定義の中と同じ判定で下る。

use std::collections::{BTreeMap, BTreeSet};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};

use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, span_of, Hy, Scope};

/// defk の集合(module まで含めた名・mangle 済み)。
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
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

    /// 集まりの指紋(file ごとの事実の cache の印に使う — この集まりに依る事実は、集まりが変われば作り直す)。
    pub fn digest(&self) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        for name in &self.names {
            hasher.update(name.as_bytes());
            hasher.update([0u8]);
        }
        format!("{:x}", hasher.finalize())
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
    "get", "isinstance", "print", "enumerate", "zip", "iter", "next", "hash", "format", "abs", "round", "assert",
];

/// 名に束ねる形(`(setv 名 式 …)` の組・`(val 名 式)`・`(var 名 式)`・`(:= 名 式)`)— 素の呼びを名に束ねて後で答えとして使う形を追うため。
const PLAIN_BINDING_HEADS: &[&str] = &["setv", "setx", "val", "var", ":="];

/// 答えとして使う位置を中の枝へ受け継ぐ形(条件の式は別に答えとして使う所)。
const BRANCHING_FORMS: &[&str] = &["if", "when", "unless", "cond", "do", "let"];

/// 内包表記(元は items[2])。
const COMPREHENSIONS: &[&str] = &["lfor", "sfor", "gfor", "dfor"];

/// 定義の頭(呼びを含む定義の名を取るため)。
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "defhandler", "deftest", "defeffect"];

/// 定義の外で、中の Program を受ける形(`run` と effect として出す形)— 中は定義の中と同じ判定で下る。doeff の package の呼びも同じ扱い。
const MODULE_LEVEL_PROGRAM_HEADS: &[&str] = &["run", "<-", "!", "yield", "yield-from"];

/// 定義の外に書いた関数の本体・macro・class・import — 中は定義の中と同じ判定で下る(呼ばれた時に答えを返す所で、定義の外ではない)。
const MODULE_LEVEL_BODY_HEADS: &[&str] = &[
    "fn", "fn/a", "fnk", "defmacro", "defmacro/g!", "defreader", "defclass", "defrecord", "defmain", "import", "require", "quote", "quasiquote",
];

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
                let context =
                    Context { definition: hy_mangle(name), container: h.to_string(), bound: self.bare_bindings(form), module_level: false };
                for child in items.into_iter().skip(1) {
                    self.walk(child, false, &context, out);
                }
            }
            _ => self.walk(
                form,
                false,
                &Context {
                    definition: format!("<{}>", self.scope.module),
                    container: "module".to_string(),
                    bound: BTreeMap::new(),
                    module_level: true,
                },
                out,
            ),
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
            // defk の素の呼びを束ねた名(`all`・`all.x`)を答えとして使う所 — 束ねた呼びを違反にする。
            Node::Symbol if value => {
                let text = self.hy.text(form);
                let name = text.split('.').next().unwrap_or(text);
                if let Some((callee, span)) = context.bound.get(name) {
                    out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
                }
            }
            // 定義の外の quote / quasiquote(`'(f …)`・`` `(f …) ``)と reader の tag の中は呼びではない — 定義の中と同じ判定で下る。
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } | Node::Tagged { .. } if context.module_level => {
                let inner = context.inside_definition();
                for child in children(form) {
                    self.walk(child, false, &inner, out);
                }
            }
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
        // `(all.get k)` — 束ねた名の method を呼ぶのも、その名を答えとして使う所。
        if let Some((callee, span)) = head.and_then(|h| h.split_once('.')).and_then(|(name, _)| context.bound.get(name)) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.clone(), span: *span });
        }
        if let (true, true, Some(callee)) = (is_defk, value || context.module_level, head) {
            out.push(BareCall { definition: context.definition.clone(), container: context.container.clone(), callee: callee.to_string(), span: span_of(form) });
        }
        let doeff = qualified.as_deref().is_some_and(|q| q.starts_with("doeff"));
        // 定義の外で、中が Program を受ける所・関数の本体・defk の呼びの引数(その defk が Program を受けるかもしれない — 呼び自体は積んだ)
        // なら、中は定義の中と同じ判定で下る。
        // `run` を値として受け取る呼び(`(map run #((f …) …))`)も、中の Program を走らせる所。
        let passes_runner =
            items.iter().skip(1).filter_map(|item| self.hy.symbol(item)).any(|s| s == "run" || self.scope.qualify(s) == "doeff.run");
        let leaves_module_level = context.module_level
            && (doeff
                || is_defk
                || passes_runner
                || head.is_some_and(|h| {
                    MODULE_LEVEL_PROGRAM_HEADS.contains(&h) || MODULE_LEVEL_BODY_HEADS.contains(&h) || DEFINITION_HEADS.contains(&h)
                }));
        let inner;
        let context = if leaves_module_level {
            inner = context.inside_definition();
            &inner
        } else {
            context
        };
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

/// 下っている所の定義(鍵と説明のため)と、その定義の中で defk の素の呼びを束ねた名(名 → 呼んだ defk・束ねた呼びの範囲)。
struct Context {
    definition: String,
    container: String,
    bound: BTreeMap<String, (String, ByteSpan)>,
    /// 定義の外(module の最上位の式)を下っているか。定義の外には Program を走らせる所が無いので、`run` などの Program を受ける形
    /// (`MODULE_LEVEL_PROGRAM_HEADS`・doeff の package の呼び)と関数の本体(`MODULE_LEVEL_BODY_HEADS`)と quote の外にある defk の呼びは、
    /// 位置を問わず答えとして使う所(表の行・`+` の引数・repo の関数の引数・名への束ね)。
    module_level: bool,
}

impl Context {
    /// 定義の外の判定を外した写し(Program を受ける形・関数の本体・quote の中を、定義の中と同じ判定で下るため)。
    fn inside_definition(&self) -> Context {
        Context { definition: self.definition.clone(), container: self.container.clone(), bound: self.bound.clone(), module_level: false }
    }
}

impl Walker<'_> {
    /// 定義の中で、defk の素の呼びを `setv`・`val`・`var`・`:=`・`let` で名に束ねた所を集める。同じ名を `(<- 名 …)` でも束ねる定義では
    /// その名を追わない(どちらの値か決まらない)。追うのは 1 つの定義の中だけ。
    fn bare_bindings(&self, definition: &Form) -> BTreeMap<String, (String, ByteSpan)> {
        let mut bound = BTreeMap::new();
        let mut effect_bound = BTreeSet::new();
        let mut pending = vec![definition];
        while let Some(form) = pending.pop() {
            if let Some(items) = live(form) {
                let head = items.first().and_then(|h| self.hy.head_text(h));
                let pairs: Vec<(&Form, &Form)> = match head {
                    Some(h) if PLAIN_BINDING_HEADS.contains(&h) => items[1..].chunks(2).filter_map(|p| Some((*p.first()?, *p.get(1)?))).collect(),
                    Some("let") => items
                        .get(1)
                        .and_then(|b| b.bracket_items())
                        .map(super::smells::live_items)
                        .unwrap_or_default()
                        .chunks(2)
                        .filter_map(|p| Some((*p.first()?, *p.get(1)?)))
                        .collect(),
                    Some("<-") => {
                        if let Some(name) = items.get(1).and_then(|t| self.hy.symbol(t)) {
                            effect_bound.insert(name.to_string());
                        }
                        Vec::new()
                    }
                    _ => Vec::new(),
                };
                for (target, value) in pairs {
                    let (Some(name), Some(callee)) = (self.hy.symbol(target), self.hy.head(value)) else { continue };
                    if self.defks.contains(&self.scope.qualify(callee)) {
                        bound.entry(name.to_string()).or_insert((callee.to_string(), span_of(value)));
                    }
                }
            }
            pending.extend(children(form));
        }
        bound.retain(|name, _| !effect_bound.contains(name));
        bound
    }
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

    #[test]
    fn bound_bare_calls_used_as_values_and_assert_messages_are_found() {
        // agora の incident_placement.hy(直す前 9e2574a6^)の形: (.items (histories run)) は method の的、
        // (setv … all (histories run)) の後の (get all job) は束ねた名を答えとして使う所。assert の文 (describe …) も答えとして使う。
        let source = r#"(deff turn-survives-roll [run]
  (for [[job history] (.items (fetch run))] (print job)))
(deff waits-of-run [run]
  (setv out [] all (fetch run))
  (for [job (.keys all)] (.append out (get all job)))
  out)
(deff via-let [run] (let [rows (fetch run)] (len rows)))
(deftest test-laws (assert (in "TI1" laws) (fetch out.breaches)))
(defk fine [run]
  (setv program (fetch run))
  (<- got (run-on program))
  (val later (fetch run))
  (return later))
(defk rebound [run] (setv x (fetch run)) (<- x (fetch run)) (len x))"#;
        assert_eq!(found(source, &[]), vec!["turn_survives_roll::fetch", "waits_of_run::fetch", "via_let::fetch", "test_laws::fetch"]);
    }

    #[test]
    fn bare_calls_inside_module_level_tables_are_found() {
        // agora-controllers c6271008a の scripts/land_focus_gate.hy を縮めた形: module の最上位の表の行に defk の tests-of を run 無しで足し、
        // 表に Program が混ざって `--all` が起動で落ちた。表の行の組み立て(`+`・repo の関数 entry の引数)の中でも、定義の外では答えとして使う所。
        let source = r#"(defn entry [path cmds] [path cmds])
(setv SIM-TESTS [(run (fetch "sim"))])
(val TABLE [(entry "a" [(run (fetch "x"))])
            ["b" (+ [(fetch "y") (run (load "z"))] SIM-TESTS)]])
(setv PAIRS #((entry "c" (load "w"))))"#;
        assert_eq!(found(source, &[("load", "lib"), ("run", "doeff")]), vec!["<m>::fetch", "<m>::load"]);
    }

    #[test]
    fn module_level_programs_that_are_run_passed_or_quoted_are_not_bare() {
        // 定義の外でも拾わない: run・doeff の Program を受ける呼び・`<-`・`!` の中、defk を値として渡す所(呼んでいない)、
        // 関数の本体(fn・fnk・defmacro・defclass の method)、quote / quasiquote の中。
        let source = r#"(setv ANSWER (run (with_handlers [h] (fetch 1))))
(setv DIRECT (doeff.run (fetch 2)))
(setv REGISTRY {"fetch" fetch "load" load})
(setv LATER (fn [x] (fetch x)))
(setv LATER-K (fnk [x] (<- got (fetch x)) (return got)))
(defmacro fetch-all [#* xs] `(lfor x ~xs (fetch x)))
(defclass Holder [] (defn method [self] (fetch 3)))
(setv QUOTED '(fetch 4))
(val REQUESTS (tuple (map run #((fetch 6) (fetch 7)))))
(when (= __name__ "__main__") (run (fetch 5)))"#;
        let bindings = [("run", "doeff"), ("with_handlers", "doeff"), ("load", "lib")];
        assert!(found(source, &bindings).is_empty(), "{:?}", found(source, &bindings));
    }
}


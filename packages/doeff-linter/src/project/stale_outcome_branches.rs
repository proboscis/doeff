//! DOEFF175 — 宣言を切り替えた effect の古い分岐(ADR-DOE-CORE-EFFECTS-003 R14 (4) — 段階 4 の道具・law declarations-switch-one-effect-at-a-time)。
//!
//! defeffect が `:absent` / `:failure` で宣言した型は、`<-` と `!` が答えの値として束ねない(不在は Absent、失敗は Raise として逃がす — R5)。
//! だから、その effect の答えを束ねた変数に対して、宣言した型を値として分ける枝(`match` の腕・`isinstance`)は決して通らない。effect を
//! 1 つ切り替えた版の後に、呼び手に残る古い分岐を、この規則が拾う(当たりが 0 になるまで、その版を着地させない)。
//!
//! 拾う形(1 つの定義の中だけ。名は `<-` か `val`・`var`・`setv`・`setx`・`:=` で effect の答えに束ねた物):
//! - `(<- x (E …))`・`(<- x T (E …))`・`(<- x (E …) :absent F)`・`(val x (! (E …)))` で束ねた x に対する
//!   `(match x (T …) …)` の腕(`(| (A) (B))` の選択肢も)と `(isinstance x T)`(`#(A B)`・`(| A B)`・`(tuple [A B])` の型の列も)。
//! - `(match (! (E …)) (T …) …)` と `(isinstance (! (E …)) T)`(束ねずに直に分ける形)。
//! - cond・if・when・and・not の中の isinstance も、木を歩いて拾う。
//! T が E の `:absent` / `:failure` の列に在る時だけ当たる(型の名は import と module で module まで解いて比べる — DOEFF122 と同じ)。
//!
//! 拾わない物: `:value` と宣言した型の枝・成功の型の枝・宣言の無い effect の枝・defeffect の宣言そのもの・defhandler の節(effect を受ける側で、
//! 答えを束ねる側ではない — 定義ごと読まない)・quote の中・註と `#_` で読み捨てた form・`maybe` / `on-raise` / `absent-as` で包んだ答え
//! (束ねるのは Maybe の値か畳んだ業務の答えで、effect の答えではない)。defk を経由した答え(`(<- x (read-typed …))`)は追わない —
//! 経由する defk 自身の effect が切り替わる版で、その defk の戻りの型の枝として直す(この規則が見るのは effect の頭を直に束ねる形だけ)。
//!
//! 名の束縛は、定義の中を前から順に読んで覚える。別の値に束ね直した名・関数の引数・comprehension / for / with / let / except / match の
//! pattern で束ねた名は、effect の答えでなくなる(覚えを捨てる — 誤って当てるより、見逃す方に倒す)。

use std::collections::HashMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};

use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, span_of, DeclaredAs, FailureLookup, Hy, Scope};

/// 古い分岐の形。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BranchForm {
    /// `(match 主 … (T …) 腕 …)` の腕。
    MatchArm,
    /// `(isinstance 主 T)`。
    Isinstance,
}

impl BranchForm {
    /// 説明の文の語。
    pub fn word(self) -> &'static str {
        match self {
            BranchForm::MatchArm => "match の腕",
            BranchForm::Isinstance => "isinstance",
        }
    }
}

/// 古い分岐 1 件(どの定義の、どこで、どの effect の宣言に反するか)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StaleBranch {
    /// 定義の名(mangle 済み)。
    pub definition: String,
    pub span: ByteSpan,
    /// 答えを束ねた effect の綴り(`(<- x (E …))` の E)。
    pub effect: String,
    /// 分けられている主(束ねた名。`(! (E …))` を直に分ける形は effect の綴り)。
    pub subject: String,
    /// 分ける型の綴り。
    pub ty: String,
    /// effect が宣言した種類。
    pub declared: DeclaredAs,
    pub form: BranchForm,
}

impl StaleBranch {
    /// 鍵の細目(`<定義>::<主>::<型>` — 同じ定義の同じ主・型は 1 件に数える)。
    pub fn detail(&self) -> String {
        format!("{}::{}::{}", self.definition, hy_mangle(&self.subject), hy_mangle(&self.ty))
    }

    /// 宣言の種類の語。
    pub fn declared_word(&self) -> &'static str {
        match self.declared {
            DeclaredAs::Absent => "不在",
            DeclaredAs::Failure => "失敗",
        }
    }
}

/// 読む定義の頭。defhandler は入れない(節は effect を受ける側で、答えを束ねる側ではない)。
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "deftest"];

/// 名を束ねる頭(`(頭 名 値 …)` — 値が `(! (E …))` なら effect の答え)。
const VALUE_BINDING_HEADS: &[&str] = &["val", "var", "setv", "setx", ":="];

/// 名を新しく束ねる頭(中の名は別の値になる)。
const SCOPING_HEADS: &[&str] = &[
    "fn", "fn*", "defk", "deff", "defn", "defn/a", "defp", "defpp", "deftest", "defmacro", "lfor", "gfor", "sfor", "dfor", "for", "with", "let", "except",
];

/// 名の覚え: 名 → 答えを束ねた effect の頭の綴り(None = 別の値に束ね直した)。
type Env = HashMap<String, Option<String>>;

/// 1 つの source の古い分岐を全部拾う。scope は effect と型の綴りを解くため、lookup は effect の宣言を引くため。
pub fn stale_branches_in(source: &str, scope: Scope<'_>, lookup: &dyn FailureLookup) -> Vec<StaleBranch> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut walker = Walker { hy: &hy, scope, lookup, definition: String::new(), found: Vec::new() };
    walker.definitions(&forms.iter().collect::<Vec<_>>());
    walker.found
}

struct Walker<'a> {
    hy: &'a Hy<'a>,
    scope: Scope<'a>,
    lookup: &'a dyn FailureLookup,
    /// 今読んでいる定義の名。
    definition: String,
    found: Vec<StaleBranch>,
}

impl Walker<'_> {
    /// 最上位の定義(`do` と `eval-and-compile` の中も)を 1 つずつ読む。
    fn definitions(&mut self, forms: &[&Form]) {
        for form in forms {
            let Some(items) = live(form) else { continue };
            match items.first().and_then(|h| self.hy.symbol(h)) {
                Some("do" | "eval-and-compile" | "eval-when-compile") => self.definitions(&items[1..]),
                Some(head) if DEFINITION_HEADS.contains(&head) => {
                    let name_form = match items.get(1) {
                        Some(first) if first.bracket_items().is_some() => items.get(2).copied(),
                        other => other.copied(),
                    };
                    let name = name_form.map(|f| match &f.node {
                        Node::Annotated { target: Some(target), .. } => self.hy.text(target),
                        _ => self.hy.text(f),
                    });
                    if let Some(name) = name.filter(|n| !n.is_empty()) {
                        self.definition = hy_mangle(name);
                        let mut env = Env::new();
                        self.walk(form, &mut env);
                    }
                }
                _ => {}
            }
        }
    }

    /// 形を前から順に読む。束ねは env に覚え、分岐は覚えた名に対して照らす。
    fn walk(&mut self, form: &Form, env: &mut Env) {
        // quote の中は読まない。
        if matches!(&form.node, Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. }) {
            return;
        }
        let Some(items) = live(form) else {
            for child in children(form) {
                self.walk(child, env);
            }
            return;
        };
        let head = self.hy.head(form);
        match head {
            Some("<-") => return self.walk_arrow(&items, env),
            Some(h) if VALUE_BINDING_HEADS.contains(&h) => return self.walk_value_binding(&items, env),
            Some("match") => self.check_match(&items, env),
            Some("isinstance") => self.check_isinstance(&items, env),
            _ => {}
        }
        if let Some(scoping) = head.filter(|h| SCOPING_HEADS.contains(h)) {
            let (mut inner, leaked) = self.scoped_env(scoping, &items, env);
            for name in leaked {
                env.insert(name, None);
            }
            for child in items.iter().skip(1) {
                self.walk(child, &mut inner);
            }
            return;
        }
        if head == Some("match") {
            self.walk_match_arms(&items, env);
            return;
        }
        for child in items.iter().skip(if head.is_some() { 1 } else { 0 }) {
            self.walk(child, env);
        }
    }

    /// `(<- x e)`・`(<- x T e)`・`(<- x e :absent F)`・`(<- (E …))`。右辺の中を先に読み、束ねた名を覚える。
    fn walk_arrow(&mut self, items: &[&Form], env: &mut Env) {
        let (core, absent) = self.split_absent(items);
        for part in core.iter().skip(1) {
            self.walk(part, env);
        }
        let (target, value) = match core {
            [_, target, value] => (Some(*target), Some(*value)),
            [_, target, _ty, value] => (Some(*target), Some(*value)),
            _ => (None, None),
        };
        if let (Some(target), Some(value)) = (target, value) {
            let effect = self.effect_head_of(value, false);
            self.remember(target, effect, env);
        }
        // `:absent F` の F は不在の時だけ評価する値の式(中の分岐も読む)。
        if let Some(absent) = absent {
            self.walk(absent, env);
        }
    }

    /// 末尾の `:absent 失敗` を除いた形(と、失敗の式)。
    fn split_absent<'f>(&self, items: &'f [&'f Form]) -> (&'f [&'f Form], Option<&'f Form>) {
        let count = items.len();
        if count >= 4 && matches!(items[count - 2].node, Node::Keyword) && self.hy.text(items[count - 2]) == ":absent" {
            (&items[..count - 2], Some(items[count - 1]))
        } else {
            (items, None)
        }
    }

    /// `(val x v)`・`(setv a 1 b 2)` など。値が `(! (E …))` なら effect の答え。
    fn walk_value_binding(&mut self, items: &[&Form], env: &mut Env) {
        for pair in items[1..].chunks(2) {
            if let [target, value] = pair {
                self.walk(value, env);
                let effect = self.effect_head_of(value, true);
                self.remember(target, effect, env);
            } else {
                for part in pair {
                    self.walk(part, env);
                }
            }
        }
    }

    /// 束ねた名を覚える(記号 1 つなら effect か None、組み立ての pattern なら中の名を全部 None)。
    fn remember(&self, target: &Form, effect: Option<String>, env: &mut Env) {
        match self.hy.symbol(target) {
            Some(name) if !name.contains('.') => {
                env.insert(name.to_string(), effect);
            }
            Some(_) => {}
            None => {
                for name in self.symbols_in(target) {
                    env.insert(name, None);
                }
            }
        }
    }

    /// 右辺 `value` が effect の答え(の頭の綴り)か。`<-` は右辺が `(E …)`、`val` などは `(! (E …))`。
    fn effect_head_of(&self, value: &Form, through_bang: bool) -> Option<String> {
        let call = if through_bang { self.bang_argument(value)? } else { value };
        let items = live(call)?;
        let head = self.hy.symbol(items.first()?)?;
        // effect は class なので名の最後の段が大文字で始まる(`ReadRow`・`effects.ReadRow`)。defk の呼び(`read-typed`)・method の呼び(`.get`)・
        // `!` は effect でない — 宣言を引く前に外す(1 file の実行は、宣言を引くたびに repo の宣言の file を読むので、引く数を減らす)。
        let is_effect_name = head.rsplit('.').next().and_then(|last| last.chars().next()).is_some_and(char::is_uppercase);
        is_effect_name.then(|| head.to_string())
    }

    /// `(! e)` の e。
    fn bang_argument<'f>(&self, form: &'f Form) -> Option<&'f Form> {
        let items = live(form)?;
        match items.as_slice() {
            [bang, inner] if self.hy.symbol(bang) == Some("!") => Some(*inner),
            _ => None,
        }
    }

    /// 主(`x` か `(! (E …))`)が effect の答えなら (主の綴り・effect の頭の綴り)。
    fn subject_of(&self, subject: &Form, env: &Env) -> Option<(String, String)> {
        if let Some(name) = self.hy.symbol(subject) {
            return env.get(name)?.as_ref().map(|effect| (name.to_string(), effect.clone()));
        }
        let effect = self.effect_head_of(subject, true)?;
        Some((effect.clone(), effect))
    }

    /// effect の頭の綴りを解いて、宣言した型を引く(宣言の無い名・defk の名は None)。
    fn outcomes_of(&self, effect: &str) -> Option<super::smells::EffectOutcomes> {
        self.lookup.effect_outcomes(&self.scope.qualify(effect))
    }

    /// `(match 主 pattern 腕 …)` の腕の型が、主の effect の宣言した不在・失敗の型か。
    fn check_match(&mut self, items: &[&Form], env: &Env) {
        let Some(subject) = items.get(1) else { return };
        let Some((name, effect)) = self.subject_of(subject, env) else { return };
        let mut index = 2;
        while index < items.len() {
            let pattern = items[index];
            index += 1;
            // `:as 名` と `:if 条件` を飛ばす。
            while index + 1 < items.len() && matches!(items[index].node, Node::Keyword) {
                index += 2;
            }
            index += 1; // 腕の本体
            for ty in self.pattern_types(pattern) {
                self.report(&effect, &name, &ty, BranchForm::MatchArm, span_of(pattern));
            }
        }
    }

    /// 腕の pattern の型の綴り(`(T …)` の T と、`(| (A) (B))` の選択肢の頭)。pattern の欄の中の型は、答えの型ではないので辿らない。
    fn pattern_types(&self, pattern: &Form) -> Vec<String> {
        let Some(items) = live(pattern) else { return Vec::new() };
        match items.first() {
            Some(head) if self.hy.symbol(head) == Some("|") => items[1..].iter().flat_map(|alt| self.pattern_types(alt)).collect(),
            Some(head) => self.hy.symbol(head).map(|t| vec![t.to_string()]).unwrap_or_default(),
            None => Vec::new(),
        }
    }

    /// `(isinstance 主 型)` の型が、主の effect の宣言した不在・失敗の型か。
    fn check_isinstance(&mut self, items: &[&Form], env: &Env) {
        let [_, subject, types, ..] = items else { return };
        let Some((name, effect)) = self.subject_of(subject, env) else { return };
        for (ty, span) in self.type_symbols(types) {
            self.report(&effect, &name, &ty, BranchForm::Isinstance, span);
        }
    }

    /// isinstance の第 2 引数の型の綴りと位置(記号 1 つ・`#(A B)`・`[A B]`・`(| A B)`・`(tuple [A B])`)。
    fn type_symbols(&self, types: &Form) -> Vec<(String, ByteSpan)> {
        if let Some(symbol) = self.hy.symbol(types) {
            return vec![(symbol.to_string(), span_of(types))];
        }
        match &types.node {
            Node::Seq { delim: Delim::Tuple | Delim::Bracket, .. } => children(types).into_iter().flat_map(|t| self.type_symbols(t)).collect(),
            Node::Seq { delim: Delim::Paren, .. } => {
                let items = live(types).unwrap_or_default();
                match items.first().and_then(|h| self.hy.symbol(h)) {
                    Some("|" | "tuple") => items[1..].iter().flat_map(|t| self.type_symbols(t)).collect(),
                    _ => Vec::new(),
                }
            }
            _ => Vec::new(),
        }
    }

    /// 型 `spelled` が effect の宣言した不在・失敗なら、古い分岐として積む。
    fn report(&mut self, effect: &str, subject: &str, spelled: &str, form: BranchForm, span: ByteSpan) {
        let Some(outcomes) = self.outcomes_of(effect) else { return };
        let Some(declared) = outcomes.declared(&self.scope.qualify(spelled)) else { return };
        let branch = StaleBranch {
            definition: self.definition.clone(),
            span,
            effect: effect.to_string(),
            subject: subject.to_string(),
            ty: spelled.to_string(),
            declared,
            form,
        };
        // 同じ定義の同じ主・型は最初の 1 件だけ(鍵を 1 つにする)。
        if !self.found.iter().any(|f| f.detail() == branch.detail()) {
            self.found.push(branch);
        }
    }

    /// match の主と腕の本体を読む(pattern は読まない — pattern で束ねた名は覚えを捨てる)。
    fn walk_match_arms(&mut self, items: &[&Form], env: &mut Env) {
        if let Some(subject) = items.get(1) {
            self.walk(subject, env);
        }
        let mut index = 2;
        while index < items.len() {
            let pattern = items[index];
            index += 1;
            for name in self.symbols_in(pattern) {
                env.insert(name, None);
            }
            while index + 1 < items.len() && matches!(items[index].node, Node::Keyword) {
                let value = items[index + 1];
                if self.hy.text(items[index]) == ":as" {
                    if let Some(alias) = self.hy.symbol(value) {
                        env.insert(alias.to_string(), None);
                    }
                } else {
                    self.walk(value, env);
                }
                index += 2;
            }
            if let Some(body) = items.get(index) {
                self.walk(body, env);
            }
            index += 1;
        }
    }

    /// 形の中の記号の綴り(名の束ねを数えるため。keyword・`.` を含む名・`_` は除く)。
    fn symbols_in(&self, form: &Form) -> Vec<String> {
        let mut out = Vec::new();
        let mut pending = vec![form];
        while let Some(next) = pending.pop() {
            if let Some(symbol) = self.hy.symbol(next).filter(|s| *s != "_" && !s.contains('.')) {
                out.push(symbol.to_string());
            }
            pending.extend(children(next));
        }
        out
    }

    /// scoping の form の中で読む名の覚え(と、form の外でも覚えを捨てる名)。引数・繰り返しの名・with の名・let の名・except の名は別の値に
    /// なる(多めに数えて、覚えを捨てる側に倒す)。comprehension の中は何も覚えない(外の名は読めるが、内側の束ねを追わない)。
    /// for と with が束ねる名は、後ろの文でも生きる。
    fn scoped_env(&self, head: &str, items: &[&Form], env: &Env) -> (Env, Vec<String>) {
        let symbols = |index: usize| items.get(index).map(|f| self.symbols_in(f)).unwrap_or_default();
        match head {
            "lfor" | "gfor" | "sfor" | "dfor" => (Env::new(), Vec::new()),
            "for" | "with" => {
                let names = symbols(1);
                let mut inner = env.clone();
                inner.extend(names.iter().map(|n| (n.clone(), None)));
                (inner, names)
            }
            "fn" | "fn*" | "let" | "except" => {
                let mut inner = env.clone();
                inner.extend(symbols(1).into_iter().map(|n| (n, None)));
                (inner, Vec::new())
            }
            // defk・deff・defn …・deftest・defmacro: 名(飾りの vector も)と引数の vector。外の覚えは持ち込まない(別の定義)。
            _ => {
                let mut inner = Env::new();
                inner.extend(symbols(1).into_iter().chain(symbols(2)).map(|n| (n, None)));
                (inner, Vec::new())
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::super::smells::{failure_types_in, Scope};
    use super::*;

    /// 宣言(module m)— ReadRow は Missing を不在・Unreachable を失敗・Conflict を値と宣言し、Plain は何も宣言しない。
    const DECLARATIONS: &str = r#"(defeffect ReadRow "行を読む" {:fields [key] :answer (| Row Missing Unreachable Conflict) :absent [Missing] :failure [Unreachable] :value [Conflict]})
(defeffect Plain "宣言なし" {:fields [key] :answer (| Row Missing Unreachable)})
"#;

    /// 使う側の source の古い分岐を `<定義>::<主>::<型>` の列にする(同じ module m・import なし — 型も effect も `m.<名>`)。
    fn found(uses: &str) -> Vec<String> {
        let bindings = BTreeMap::new();
        let scope = Scope { module: "m", bindings: &bindings };
        let declared = failure_types_in(DECLARATIONS, scope);
        stale_branches_in(uses, scope, &declared).iter().map(StaleBranch::detail).collect()
    }

    #[test]
    fn declared_absent_and_failure_types_are_found_in_every_branch_form() {
        let source = r#"(defk a [k]
  (<- r (ReadRow k))
  (match r (Missing) 0 (| (Unreachable) (Row)) 1))
(defk b [k]
  (<- r (| Row Missing) (ReadRow k))
  (cond (isinstance r #(Row Unreachable)) 1 (isinstance r Missing) 2 True 3))
(defk c [k]
  (val r (! (ReadRow k)))
  (not (isinstance r (| Missing Conflict))))
(defk d [k]
  (match (! (ReadRow k)) (Unreachable :detail why) why _ None))
(defk e [k]
  (<- r (ReadRow k) :absent (Conflict "none"))
  (isinstance r (tuple [Missing])))"#;
        assert_eq!(
            found(source),
            vec!["a::r::Missing", "a::r::Unreachable", "b::r::Unreachable", "b::r::Missing", "c::r::Missing", "d::ReadRow::Unreachable", "e::r::Missing"]
        );
    }

    #[test]
    fn value_success_undeclared_and_handler_branches_are_not_found() {
        let source = r#"(defk a [k]
  (<- r (ReadRow k))
  (match r (Conflict) 0 (Row) 1)
  (isinstance r Row))
(defk b [k]
  (<- r (Plain k))
  (match r (Missing) 0 (Unreachable) 1)
  (isinstance r Missing))
(defhandler h
  (ReadRow [k]
    (<- inner (ReadRow k))
    (match inner (Missing) (resume 0) _ (resume 1))))
(defk c [k]
  (<- r (ReadRow k))
  (match r (Row :value (Missing)) 0 _ 1)
  (match (ReadRow k) (Missing) 0 _ 1))"#;
        assert_eq!(found(source), Vec::<String>::new());
    }

    #[test]
    fn a_name_that_is_bound_again_or_hidden_is_not_an_effect_answer_any_more() {
        let source = r#"(defk a [k]
  (<- r (ReadRow k))
  (setv r (fallback))
  (match r (Missing) 0 _ 1))
(defk b [k]
  (<- r (ReadRow k))
  (for [r (rows)] (isinstance r Missing)))
(defk c [k]
  (<- r (ReadRow k))
  (lfor r (rows) (isinstance r Missing)))
(defk d [k]
  (<- r (ReadRow k))
  (let [r 1] (isinstance r Missing)))
(defk e [k]
  (<- r (ReadRow k))
  (match (other) (Row :value r) (isinstance r Missing) _ 1))
(defk f [r]
  (isinstance r Missing))
(defk h [k]
  (<- r (read-typed k))
  (isinstance r Missing))
(defk g [k]
  (<- r (ReadRow k))
  (val s (fn [r] (isinstance r Missing)))
  (isinstance r Missing))"#;
        // g の最後の isinstance だけ(引数 r で隠れるのは fn の中だけ — 外の r は effect の答えのまま)。
        assert_eq!(found(source), vec!["g::r::Missing"]);
    }
}

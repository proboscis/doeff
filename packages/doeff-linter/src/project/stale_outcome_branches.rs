//! DOEFF175 -- stale branches on an effect whose declaration was switched (ADR-DOE-CORE-EFFECTS-003 R14 (4) -- a stage-4 tool; law declarations-switch-one-effect-at-a-time).
//!
//! The types that defeffect declares with `:absent` / `:failure` are not bound as the answer value by `<-` and `!` (absence escapes as Absent, failure as Raise -- R5).
//! So a branch that splits on a declared type as a value (a `match` arm, `isinstance`) against a variable bound to that effect's answer is never taken. After a version that
//! switched one effect, this rule catches the stale branches left in callers (the version is not landed until the hits reach 0).
//!
//! Shapes caught (within a single definition only. A name is bound to an effect's answer with `<-`, `val`, `var`, `setv`, `setx`, or `:=`):
//! - Arms of `(match x (T …) …)` (also the alternatives of `(| (A) (B))`) and `(isinstance x T)` (also type lists such as `#(A B)`, `(| A B)`, `(tuple [A B])`) against an x bound with
//!   `(<- x (E …))`, `(<- x T (E …))`, `(<- x (E …) :absent F)`, or `(val x (! (E …)))`.
//! - `(match (! (E …)) (T …) …)` and `(isinstance (! (E …)) T)` (splitting directly without binding).
//! - isinstance inside cond, if, when, and, and not is also caught by walking the tree.
//! It hits only when T is in the `:absent` / `:failure` list of E (type names are resolved up to the module through imports and modules before comparing -- the same as DOEFF122).
//!
//! Not caught: branches on types declared `:value`, branches on success types, branches on effects with no declaration, the defeffect declaration itself, defhandler clauses (the side that receives the effect,
//! not the side that binds the answer -- the whole definition is not read), inside quote, forms discarded by comments and `#_`, and answers wrapped in `maybe` / `on-raise` / `absent-as`
//! (what is bound is a Maybe value or a folded business answer, not the effect's answer). Answers passed through a defk (`(<- x (read-typed …))`) are not followed --
//! in the version where the defk's own effect is switched, fix it as a branch on that defk's return type (this rule looks only at the shape that binds an effect head directly).
//!
//! Name bindings are learned by reading a definition in order from the front. A name rebound to another value, a function argument, or a name bound by the pattern of comprehension / for / with / let / except / match
//! is no longer an effect's answer (the memory is dropped -- erring toward missing rather than wrongly hitting).

use std::collections::HashMap;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};

use super::facts::ByteSpan;
use super::names::hy_mangle;
use super::smells::{children, live, span_of, DeclaredAs, FailureLookup, Hy, Scope};

/// The shape of a stale branch.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BranchForm {
    /// An arm of `(match subject … (T …) arm …)`.
    MatchArm,
    /// `(isinstance subject T)`.
    Isinstance,
}

impl BranchForm {
    /// Words of the explanation sentence.
    pub fn word(self) -> &'static str {
        match self {
            BranchForm::MatchArm => "match arm",
            BranchForm::Isinstance => "isinstance",
        }
    }
}

/// One stale branch (in which definition, where, and against which effect's declaration it violates).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StaleBranch {
    /// The definition's name (mangled).
    pub definition: String,
    pub span: ByteSpan,
    /// The spelling of the effect whose answer was bound (the E of `(<- x (E …))`).
    pub effect: String,
    /// The subject being split (the bound name. For the shape that splits `(! (E …))` directly, the effect's spelling).
    pub subject: String,
    /// The spelling of the type being split on.
    pub ty: String,
    /// The kind the effect declared.
    pub declared: DeclaredAs,
    pub form: BranchForm,
}

impl StaleBranch {
    /// Key detail (`<definition>::<subject>::<type>` -- the same subject and type in the same definition count as one).
    pub fn detail(&self) -> String {
        format!("{}::{}::{}", self.definition, hy_mangle(&self.subject), hy_mangle(&self.ty))
    }

    /// Words for the kind of declaration.
    pub fn declared_word(&self) -> &'static str {
        match self.declared {
            DeclaredAs::Absent => "absent",
            DeclaredAs::Failure => "failure",
        }
    }
}

/// Heads of definitions to read. defhandler is not included (its clauses are the side that receives the effect, not the side that binds the answer).
const DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defn/a", "defp", "defpp", "deftest"];

/// Heads that bind a name (`(head name value …)` -- if the value is `(! (E …))`, it is an effect's answer).
const VALUE_BINDING_HEADS: &[&str] = &["val", "var", "setv", "setx", ":="];

/// Heads that newly bind names (the names inside become other values).
const SCOPING_HEADS: &[&str] = &[
    "fn", "fn*", "defk", "deff", "defn", "defn/a", "defp", "defpp", "deftest", "defmacro", "lfor", "gfor", "sfor", "dfor", "for", "with", "let", "except",
];

/// Memory of names: name -> spelling of the head of the effect whose answer was bound (None = rebound to another value).
type Env = HashMap<String, Option<String>>;

/// Catches all stale branches of one source. scope is for resolving the spellings of effects and types, and lookup is for looking up effect declarations.
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
    /// The name of the definition being read now.
    definition: String,
    found: Vec<StaleBranch>,
}

impl Walker<'_> {
    /// Reads top-level definitions (also those inside `do` and `eval-and-compile`) one by one.
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

    /// Reads forms in order from the front. Bindings are remembered in env, and branches are checked against the remembered names.
    fn walk(&mut self, form: &Form, env: &mut Env) {
        // Does not read inside quote.
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

    /// `(<- x e)`, `(<- x T e)`, `(<- x e :absent F)`, `(<- (E …))`. Reads the right-hand side first and remembers the bound name.
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
        // The F of `:absent F` is a value expression evaluated only on absence (branches inside are also read).
        if let Some(absent) = absent {
            self.walk(absent, env);
        }
    }

    /// The form with the trailing `:absent failure` removed (and the failure expression).
    fn split_absent<'f>(&self, items: &'f [&'f Form]) -> (&'f [&'f Form], Option<&'f Form>) {
        let count = items.len();
        if count >= 4 && matches!(items[count - 2].node, Node::Keyword) && self.hy.text(items[count - 2]) == ":absent" {
            (&items[..count - 2], Some(items[count - 1]))
        } else {
            (items, None)
        }
    }

    /// `(val x v)`, `(setv a 1 b 2)`, and so on. If the value is `(! (E …))`, it is an effect's answer.
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

    /// Remembers bound names (for a single symbol, the effect or None; for a destructuring pattern, all names inside become None).
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

    /// Whether the right-hand side `value` is an effect's answer (the spelling of its head). For `<-` the right-hand side is `(E …)`; for `val` and the like, `(! (E …))`.
    fn effect_head_of(&self, value: &Form, through_bang: bool) -> Option<String> {
        let call = if through_bang { self.bang_argument(value)? } else { value };
        let items = live(call)?;
        let head = self.hy.symbol(items.first()?)?;
        // An effect is a class, so the last segment of the name starts with an uppercase letter (`ReadRow`, `effects.ReadRow`). Defk calls (`read-typed`), method calls (`.get`), and
        // `!` are not effects -- exclude them before looking up declarations (a single-file run reads the repo's declaration files on each declaration lookup, so reduce the number of lookups).
        let is_effect_name = head.rsplit('.').next().and_then(|last| last.chars().next()).is_some_and(char::is_uppercase);
        is_effect_name.then(|| head.to_string())
    }

    /// The e of `(! e)`.
    fn bang_argument<'f>(&self, form: &'f Form) -> Option<&'f Form> {
        let items = live(form)?;
        match items.as_slice() {
            [bang, inner] if self.hy.symbol(bang) == Some("!") => Some(*inner),
            _ => None,
        }
    }

    /// If the subject (`x` or `(! (E …))`) is an effect's answer, (the subject's spelling, the effect head's spelling).
    fn subject_of(&self, subject: &Form, env: &Env) -> Option<(String, String)> {
        if let Some(name) = self.hy.symbol(subject) {
            return env.get(name)?.as_ref().map(|effect| (name.to_string(), effect.clone()));
        }
        let effect = self.effect_head_of(subject, true)?;
        Some((effect.clone(), effect))
    }

    /// Resolves the spelling of the effect head and looks up the declared types (None for an undeclared name or a defk's name).
    fn outcomes_of(&self, effect: &str) -> Option<super::smells::EffectOutcomes> {
        self.lookup.effect_outcomes(&self.scope.qualify(effect))
    }

    /// Whether the type of an arm of `(match subject pattern arm …)` is an absent or failure type declared by the subject's effect.
    fn check_match(&mut self, items: &[&Form], env: &Env) {
        let Some(subject) = items.get(1) else { return };
        let Some((name, effect)) = self.subject_of(subject, env) else { return };
        let mut index = 2;
        while index < items.len() {
            let pattern = items[index];
            index += 1;
            // Skip `:as name` and `:if condition`.
            while index + 1 < items.len() && matches!(items[index].node, Node::Keyword) {
                index += 2;
            }
            index += 1; // the arm's body
            for ty in self.pattern_types(pattern) {
                self.report(&effect, &name, &ty, BranchForm::MatchArm, span_of(pattern));
            }
        }
    }

    /// The spelling of the type in an arm's pattern (the T of `(T …)`, and the heads of the alternatives of `(| (A) (B))`). Types inside the pattern's fields are not the answer's type, so they are not followed.
    fn pattern_types(&self, pattern: &Form) -> Vec<String> {
        let Some(items) = live(pattern) else { return Vec::new() };
        match items.first() {
            Some(head) if self.hy.symbol(head) == Some("|") => items[1..].iter().flat_map(|alt| self.pattern_types(alt)).collect(),
            Some(head) => self.hy.symbol(head).map(|t| vec![t.to_string()]).unwrap_or_default(),
            None => Vec::new(),
        }
    }

    /// Whether the type of `(isinstance subject type)` is an absent or failure type declared by the subject's effect.
    fn check_isinstance(&mut self, items: &[&Form], env: &Env) {
        let [_, subject, types, ..] = items else { return };
        let Some((name, effect)) = self.subject_of(subject, env) else { return };
        for (ty, span) in self.type_symbols(types) {
            self.report(&effect, &name, &ty, BranchForm::Isinstance, span);
        }
    }

    /// The spelling and position of the type in the second argument of isinstance (a single symbol, `#(A B)`, `[A B]`, `(| A B)`, `(tuple [A B])`).
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

    /// If the type `spelled` is an absent or failure declared by the effect, pushes it as a stale branch.
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
        // Only the first one of the same subject and type in the same definition (to make the key unique).
        if !self.found.iter().any(|f| f.detail() == branch.detail()) {
            self.found.push(branch);
        }
    }

    /// Reads the match subject and the arm bodies (the pattern is not read -- names bound by the pattern drop their memory).
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

    /// The spelling of symbols in a form (to count name bindings. Keywords, names containing `.`, and `_` are excluded).
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

    /// The memory of names read inside a scoping form (and names whose memory is dropped even outside the form). Arguments, loop names, with names, let names, and except names become other
    /// values (counting generously and erring toward dropping memory). Nothing is remembered inside a comprehension (outer names can be read, but bindings inside are not followed).
    /// Names bound by for and with live on in later statements as well.
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
            // defk, deff, defn ..., deftest, defmacro: the name (and decorator vector) and the argument vector. The outer memory is not carried in (a different definition).
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

    /// Declarations (module m) -- ReadRow declares Missing as absent, Unreachable as failure, and Conflict as a value; Plain declares nothing.
    const DECLARATIONS: &str = r#"(defeffect ReadRow "read a row" {:fields [key] :answer (| Row Missing Unreachable Conflict) :absent [Missing] :failure [Unreachable] :value [Conflict]})
(defeffect Plain "no declaration" {:fields [key] :answer (| Row Missing Unreachable)})
"#;

    /// Turns the stale branches of the user-side source into a list of `<definition>::<subject>::<type>` (the same module m, no imports -- both types and effects are `m.<name>`).
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
        // Only g's last isinstance (hiding by the argument r is only inside the fn -- the outer r remains an effect's answer).
        assert_eq!(found(source), vec!["g::r::Missing"]);
    }
}

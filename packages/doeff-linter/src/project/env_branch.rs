//! DOEFF168: business layers do not branch on environment-name values or dry-run flags (law business-code-has-no-environment-branch).
//!
//! The spellings of environment-name values and dry-run flags, and the layers they apply to, live only in the repo's architecture.hy `:environment-branches` and are not written here.
//! Business layers do not know the environment — differences between environments (production, emulated, dry-run) are expressed only by swapping the handler set. The shapes that hit (the Hy form tree):
//!   * An argument of a comparison form (`=`, `!=`, `is`, `is-not`, `in`, `not-in`) contains a string literal that is an environment-name value (:values)
//!     (elements of collection literals `#{…}`, `[…]`, `#(…)` are counted too) — `(if (= mode "emulated") 0 total)`.
//!   * The pattern of a `match` clause is a string literal that is an environment-name value — `(match mode "emulated" 0 _ total)`.
//!   * Inside a branching form (the condition of `if`, `when`, `unless`, each condition of `cond`, the subject of `match`) there is a symbol named by a dry-run flag (:flags)
//!     (compared after mangling the last dot-separated segment — `dry-run`, `self.dry-run`, `args.dry_run`) — `(when dry-run (return 0))`.
//! Comments, forms discarded with `#_`, and quoted forms are not counted. Comparisons with values that are not environment names (`(= kind "message")`) do not hit.

use doeff_indexer::hy_index::mangle;
use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};

use super::architecture::EnvironmentBranches;

/// Heads of comparison forms.
const COMPARISONS: &[&str] = &["=", "!=", "is", "is-not", "in", "not-in"];
/// Heads of branching forms that hold one condition (the form after the head).
const SINGLE_TEST_BRANCHES: &[&str] = &["if", "when", "unless"];

/// The kind of spelling that hit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BranchHit {
    /// Compared with a string literal that is an environment-name value (a comparison form or a match clause pattern).
    Value(String),
    /// Branched on a dry-run flag (a symbol in a branch condition or a match subject).
    Flag(String),
}

impl BranchHit {
    /// Spelling used in the key detail and the explanation (`value:<spelling>` for a value, `flag:<spelling>` for a flag).
    pub fn detail(&self) -> String {
        match self {
            BranchHit::Value(value) => format!("value:{}", value),
            BranchHit::Flag(flag) => format!("flag:{}", flag),
        }
    }
}

/// One hit (the byte range of the literal or symbol that hit).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnvironmentBranchHit {
    pub start: usize,
    pub end: usize,
    pub hit: BranchHit,
}

/// Applies one Hy body to the declaration (hits are in position order).
pub fn judge(source: &str, decl: &EnvironmentBranches) -> Vec<EnvironmentBranchHit> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let judge = Judge { source, decl, flags: decl.flags.iter().map(|flag| mangle(flag)).collect() };
    let mut out = Vec::new();
    for form in &forms {
        judge.walk(form, &mut out);
    }
    out.sort_by_key(|hit| hit.start);
    out.dedup();
    out
}

struct Judge<'a> {
    source: &'a str,
    decl: &'a EnvironmentBranches,
    /// Names of dry-run flags (already mangled).
    flags: Vec<String>,
}

impl Judge<'_> {
    /// Walks the form tree and judges comparison forms, match, and branching forms (does not enter quoted forms or discarded forms).
    fn walk(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        if let Some(items) = form.paren_items() {
            let head = items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| self.text(f));
            match head {
                Some(head) if COMPARISONS.contains(&head) => items[1..].iter().for_each(|arg| self.values_in(arg, out)),
                Some("match") => self.judge_match(&items[1..], out),
                Some(head) if SINGLE_TEST_BRANCHES.contains(&head) => {
                    if let Some(test) = items.get(1) {
                        self.flags_in(test, out);
                    }
                }
                Some("cond") => items[1..].iter().step_by(2).for_each(|test| self.flags_in(test, out)),
                _ => {}
            }
        }
        match &form.node {
            Node::Seq { items, .. } => items.iter().for_each(|item| self.walk(item, out)),
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
            Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => self.walk(inner, out),
            Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| self.walk(part, out)),
            Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
        }
    }

    /// `(match SUBJECT PATTERN BODY PATTERN BODY …)` — hits flags in the subject and environment-name values in clause patterns (an `:if` guard after a pattern is not counted as a pattern).
    fn judge_match(&self, rest: &[Form], out: &mut Vec<EnvironmentBranchHit>) {
        let Some((subject, clauses)) = rest.split_first() else { return };
        self.flags_in(subject, out);
        let mut at = 0;
        while at < clauses.len() {
            self.value_literal(&clauses[at], out);
            // Clause = pattern, (:if guard), body.
            at += if clauses.get(at + 1).is_some_and(|f| matches!(f.node, Node::Keyword) && self.text(f) == ":if") { 4 } else { 2 };
        }
    }

    /// One argument of a comparison form — hits if it is a string literal, or a string literal element of a collection literal, that is an environment-name value.
    fn values_in(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        match &form.node {
            Node::Seq { delim: Delim::Bracket | Delim::Tuple | Delim::Set, items } => items.iter().for_each(|item| self.value_literal(item, out)),
            _ => self.value_literal(form, out),
        }
    }

    /// Hits if the form is a string literal that is an environment-name value.
    fn value_literal(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        if let Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } = &form.node {
            let text = self.source.get(body.start..body.end).unwrap_or("");
            if let Some(value) = self.decl.values.iter().find(|value| value.as_str() == text) {
                out.push(EnvironmentBranchHit { start: form.span.start, end: form.span.end, hit: BranchHit::Value(value.clone()) });
            }
        }
    }

    /// Hits a symbol named by a dry-run flag inside a branch condition expression (compared after mangling the last dot-separated segment; quotes are not looked into).
    fn flags_in(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        match &form.node {
            Node::Symbol => {
                let spelled = self.text(form);
                let last = spelled.rsplit('.').next().unwrap_or(spelled);
                if let Some(at) = self.flags.iter().position(|flag| *flag == mangle(last)) {
                    out.push(EnvironmentBranchHit { start: form.span.start, end: form.span.end, hit: BranchHit::Flag(self.decl.flags[at].clone()) });
                }
            }
            Node::Seq { items, .. } => items.iter().for_each(|item| self.flags_in(item, out)),
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
            Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => self.flags_in(inner, out),
            Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| self.flags_in(part, out)),
            Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
        }
    }

    fn text(&self, form: &Form) -> &str {
        self.source.get(form.span.start..form.span.end).unwrap_or("")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decl() -> EnvironmentBranches {
        EnvironmentBranches {
            values: vec!["production".into(), "emulated".into(), "sim".into()],
            flags: vec!["dry-run".into()],
            layers: vec!["core".into()],
        }
    }

    fn details(source: &str) -> Vec<String> {
        judge(source, &decl()).iter().map(|hit| hit.hit.detail()).collect()
    }

    #[test]
    fn comparisons_and_match_patterns_with_environment_values_hit() {
        assert_eq!(details("(defk total-of [mode total] (if (= mode \"emulated\") 0 total))\n"), vec!["value:emulated"]);
        assert_eq!(details("(defk f [env] (when (in env #{\"sim\" \"production\"}) 1))\n"), vec!["value:sim", "value:production"]);
        assert_eq!(details("(defk g [mode] (match mode \"emulated\" 0 _ 1))\n"), vec!["value:emulated"]);
        assert_eq!(details("(defk h [mode] (match mode s :if (> (len s) 3) 0 \"production\" 1))\n"), vec!["value:production"]);
    }

    #[test]
    fn dry_run_flags_in_branch_tests_hit() {
        assert_eq!(details("(defk write [dry-run] (when dry-run (return 0)) 1)\n"), vec!["flag:dry-run"]);
        assert_eq!(details("(defk write [self] (if (and self.dry-run ok) 0 1))\n"), vec!["flag:dry-run"]);
        assert_eq!(details("(defk write [args] (cond args.dry_run 0 True 1))\n"), vec!["flag:dry-run"]);
        assert_eq!(details("(defk write [x] (match x.dry-run True 0 _ 1))\n"), vec!["flag:dry-run"]);
    }

    #[test]
    fn other_values_comments_strings_and_quotes_do_not_hit() {
        assert!(details("(defk f [kind] (if (= kind \"message\") 0 1))\n").is_empty(), "comparison with a value that is not an environment name does not hit");
        assert!(details(";; do not write (= mode \"emulated\")\n(defk f [] \"when emulated …\")\n").is_empty(), "comments and strings not being compared do not hit");
        assert!(details("(defk f [] '(= mode \"emulated\"))\n#_(when dry-run 0)\n").is_empty(), "quoted and discarded forms do not hit");
        assert!(details("(defk f [dry-run] (setv x dry-run) (print \"dry-run\"))\n").is_empty(), "a flag outside a branch condition does not hit");
    }
}

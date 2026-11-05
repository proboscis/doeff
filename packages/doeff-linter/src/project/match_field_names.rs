//! DOEFF169: a `-` in a keyword field name of a `match` class pattern (a pattern that can never match).
//!
//! Hy 1.3.1's `match` emits class-pattern keywords to Python's `case` without mangling them to attribute names:
//! `(match r (Rec :ended-reason None) …)` becomes `case Rec(ended-reason=None):`, and no value has an attribute `ended-reason`, so
//! that clause never matches whatever the value is (it silently falls to the next clause — often the default `_`). Writing the field name as `:ended_reason` with `_` makes it match.
//! The shape it flags (a Hy form tree):
//!   * in the pattern of each clause of `(match subject pattern body …)` (an `:if` guard does not count as a pattern), a class pattern — a parenthesized form whose head is a symbol (other than `|`) —
//!     with a keyword argument whose name (after the `:`) contains `-`. Nesting inside the pattern (`[…]`, `#(…)`, `{…}`, class-pattern arguments) is also followed.
//! Positional-argument patterns, keywords written with `_`, and keywords outside patterns (body, guard, calls) are not flagged. Comments, forms discarded with `#_`, and quoted
//! forms are not counted either.
//! Inside a `(defk …)` form (arguments, contract, body) it is not flagged either — at expansion time defk rewrites the field names of class patterns of the inner match to
//! attribute names with `hy.mangle` (doeff-hy's doeff_hy/match_fields.py — a workaround for Hy not doing it; ADR-DOE-HY-008), so there `:ended-reason` is
//! the correct spelling. This rule guards outside defk (deff, defn, deftest, defhandler, module top level, and so on).

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};

/// One hit (the byte range of the hit keyword and its spelling — the name without the `:`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HyphenFieldHit {
    pub start: usize,
    pub end: usize,
    /// Spelling of the head class (`Rec`, `mod.Rec`).
    pub class: String,
    /// Name of the keyword (`ended-reason`).
    pub field: String,
}

/// Judge one Hy source (hits are in position order).
pub fn judge(source: &str) -> Vec<HyphenFieldHit> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let judge = Judge { source };
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
}

impl Judge<'_> {
    /// Walk the form tree and judge the patterns of `match` clauses (does not enter quoted forms, discarded forms, or defk forms).
    fn walk(&self, form: &Form, out: &mut Vec<HyphenFieldHit>) {
        if let Some(items) = form.paren_items() {
            let head = items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| self.text(f));
            match head {
                // Inside defk, doeff-hy rewrites field names to attribute names (doeff_hy/match_fields.py), so the `-` spelling is accepted there.
                Some("defk") => return,
                Some("match") => self.judge_match(&items[1..], out),
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

    /// `(match subject pattern body pattern body …)` — follow class patterns for each clause's pattern (an `:if` guard after a pattern does not count as a pattern).
    fn judge_match(&self, rest: &[Form], out: &mut Vec<HyphenFieldHit>) {
        let Some((_, clauses)) = rest.split_first() else { return };
        let mut at = 0;
        while at < clauses.len() {
            self.pattern(&clauses[at], out);
            // Clause = pattern, (:if guard), body.
            at += if clauses.get(at + 1).is_some_and(|f| matches!(f.node, Node::Keyword) && self.text(f) == ":if") { 4 } else { 2 };
        }
    }

    /// One pattern — judge the keyword names of a class pattern (a parenthesized form whose head is a symbol other than `|`), and follow arguments and nested patterns.
    fn pattern(&self, form: &Form, out: &mut Vec<HyphenFieldHit>) {
        if let Some(items) = form.paren_items() {
            let head = items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| self.text(f));
            if let Some(class) = head.filter(|head| *head != "|") {
                items[1..]
                    .iter()
                    .filter(|item| matches!(item.node, Node::Keyword))
                    .map(|item| (item, self.text(item).trim_start_matches(':')))
                    .filter(|(_, name)| name.contains('-'))
                    .for_each(|(item, name)| {
                        out.push(HyphenFieldHit { start: item.span.start, end: item.span.end, class: class.to_string(), field: name.to_string() })
                    });
            }
        }
        match &form.node {
            Node::Seq { items, .. } => items.iter().skip(usize::from(form.paren_items().is_some())).for_each(|item| self.pattern(item, out)),
            Node::Prefixed { inner: Some(inner), prefix } if !matches!(prefix, Prefix::Quote | Prefix::Quasiquote) => self.pattern(inner, out),
            _ => {}
        }
    }

    fn text(&self, form: &Form) -> &str {
        self.source.get(form.span.start..form.span.end).unwrap_or("")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fields(source: &str) -> Vec<String> {
        judge(source).iter().map(|hit| format!("{}:{}", hit.class, hit.field)).collect()
    }

    #[test]
    fn hyphenated_keyword_fields_in_class_patterns_hit() {
        assert_eq!(fields("(deff f [r] (match r (Rec :ended-reason None :usage None) 0 _ 1))\n"), vec!["Rec:ended-reason"]);
        assert_eq!(fields("(defn f [r] (match r [(Rec :a-b 1) x] 0 _ 1))\n"), vec!["Rec:a-b"], "nested patterns are also flagged");
        assert_eq!(fields("(deftest t (match r (Outer :inner (Inner :x-y 2)) 0 _ 1))\n"), vec!["Inner:x-y"], "inside class-pattern arguments is also flagged");
        assert_eq!(fields("(defhandler h (E [r] (match r x :if (> x 1) 0 (mod.Rec :a-b 1) 1)))\n"), vec!["mod.Rec:a-b"], "the pattern of a clause after an :if guard is also flagged");
        assert_eq!(fields("(setv kind (match r (| (A :a-b 1) (B :c 2)) 0 _ 1))\n"), vec!["A:a-b"], "a class pattern inside | is also flagged");
    }

    #[test]
    fn underscore_fields_positional_patterns_and_keywords_outside_patterns_do_not_hit() {
        assert!(fields("(deff f [r] (match r (Rec :ended_reason None) 0 _ 1))\n").is_empty(), "a field written with _ is not flagged");
        assert!(fields("(deff f [r] (match r (Rec None 1) 0 _ 1))\n").is_empty(), "a positional-argument pattern is not flagged");
        assert!(fields("(deff f [r] (match r _ (Rec :a-b 1)))\n").is_empty(), "a keyword in a clause body (a call) is not flagged");
        assert!(fields("(deff f [r] (match r x :if (g :a-b 1) 0 _ 1))\n").is_empty(), "a keyword in a guard is not flagged");
        assert!(fields("(deff f [] (Rec :a-b 1))\n;; (match r (Rec :a-b 1) 0)\n'(match r (Rec :a-b 1) 0)\n#_(match r (Rec :a-b 1) 0)\n").is_empty(), "outside match, comments, quotes, and discarded forms are not flagged");
    }

    /// Inside a defk form it is not flagged because doeff-hy rewrites field names to attribute names (including a nested defk and an fnk inside defk). Outside defk it is flagged.
    #[test]
    fn match_inside_defk_is_left_to_the_doeff_hy_workaround() {
        assert!(fields("(defk f [r] (match r (Rec :ended-reason None :usage None) 0 _ 1))\n").is_empty(), "a match in a defk body is not flagged");
        assert!(fields("(defk f [r] {:pre [(match r (Rec :a-b 1) True _ False)] :post [(: % int)]} 0)\n").is_empty(), "a match in a defk contract is not flagged either");
        assert!(fields("(defn outer [] (defk inner [r] (fnk [x] (match x (Rec :a-b 1) 0 _ 1))))\n").is_empty(), "an fnk inside a defk inside a defn is not flagged either");
        assert_eq!(
            fields("(defk f [r] (match r (Rec :a-b 1) 0 _ 1))\n(deff g [r] (match r (Rec :c-d 1) 0 _ 1))\n"),
            vec!["Rec:c-d"],
            "a match outside defk in the same file is flagged"
        );
    }
}

//! DOEFF169: `match` の class pattern の keyword の欄の名に `-` が在る(決して当たらない pattern — agora-redesign #2036)。
//!
//! Hy 1.3.1 の `match` は class pattern の keyword を属性名へ mangle しないまま Python の `case` に出す:
//! `(match r (Rec :ended-reason None) …)` は `case Rec(ended-reason=None):` になり、属性 `ended-reason` はどの値にも無いので、
//! その節は defk の中でも外でも、値が何でも当たらない(黙って次の節 — 多くは既定の `_` — に倒れる)。欄の名を `:ended_reason` と
//! `_` で書けば当たる。当てる形(Hy の form の木):
//!   * `(match 主語 型 本体 …)` の各節の型(`:if` の守りは型に数えない)の中の class pattern — 頭が記号(`|` を除く)の丸括弧の form —
//!     の keyword の引数で、名(`:` の後)に `-` を含む物。型の中の入れ子(`[…]`・`#(…)`・`{…}`・class pattern の引数)も辿る。
//! 位置引数の pattern・`_` で書いた keyword・型の外の keyword(本体・守り・呼び)は当たらない。註・`#_` で読み捨てた form・quote した
//! form も数えない。

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};

/// 当たり 1 つ(当たった keyword の byte の範囲と、その綴り — `:` を除いた名)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HyphenFieldHit {
    pub start: usize,
    pub end: usize,
    /// 頭の class の綴り(`Rec`・`mod.Rec`)。
    pub class: String,
    /// keyword の名(`ended-reason`)。
    pub field: String,
}

/// Hy の中身 1 つを判じる(当たりは位置の順)。
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
    /// form の木を歩き、`match` の節の型を判じる(quote した form と読み捨てた form には入らない)。
    fn walk(&self, form: &Form, out: &mut Vec<HyphenFieldHit>) {
        if let Some(items) = form.paren_items() {
            let head = items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| self.text(f));
            if head == Some("match") {
                self.judge_match(&items[1..], out);
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

    /// `(match 主語 型 本体 型 本体 …)` — 節の型ごとに class pattern を辿る(型の後の `:if` の守りは型に数えない)。
    fn judge_match(&self, rest: &[Form], out: &mut Vec<HyphenFieldHit>) {
        let Some((_, clauses)) = rest.split_first() else { return };
        let mut at = 0;
        while at < clauses.len() {
            self.pattern(&clauses[at], out);
            // 節 = 型・(:if 守り)・本体。
            at += if clauses.get(at + 1).is_some_and(|f| matches!(f.node, Node::Keyword) && self.text(f) == ":if") { 4 } else { 2 };
        }
    }

    /// 型 1 つ — class pattern(頭が `|` でない記号の丸括弧)の keyword の名を判じ、引数と入れ子の型を辿る。
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
        assert_eq!(fields("(defk f [r] (match r (Rec :ended-reason None :usage None) 0 _ 1))\n"), vec!["Rec:ended-reason"]);
        assert_eq!(fields("(defk f [r] (match r [(Rec :a-b 1) x] 0 _ 1))\n"), vec!["Rec:a-b"], "入れ子の型の中も当たる");
        assert_eq!(fields("(defk f [r] (match r (Outer :inner (Inner :x-y 2)) 0 _ 1))\n"), vec!["Inner:x-y"], "class pattern の引数の中も当たる");
        assert_eq!(fields("(defk f [r] (match r x :if (> x 1) 0 (mod.Rec :a-b 1) 1))\n"), vec!["mod.Rec:a-b"], ":if の守りの後の節の型も当たる");
        assert_eq!(fields("(defk f [r] (match r (| (A :a-b 1) (B :c 2)) 0 _ 1))\n"), vec!["A:a-b"], "| の中の class pattern も当たる");
    }

    #[test]
    fn underscore_fields_positional_patterns_and_keywords_outside_patterns_do_not_hit() {
        assert!(fields("(defk f [r] (match r (Rec :ended_reason None) 0 _ 1))\n").is_empty(), "_ で書いた欄は当たらない");
        assert!(fields("(defk f [r] (match r (Rec None 1) 0 _ 1))\n").is_empty(), "位置引数の pattern は当たらない");
        assert!(fields("(defk f [r] (match r _ (Rec :a-b 1)))\n").is_empty(), "節の本体の keyword(呼び)は当たらない");
        assert!(fields("(defk f [r] (match r x :if (g :a-b 1) 0 _ 1))\n").is_empty(), "守りの keyword は当たらない");
        assert!(fields("(defk f [] (Rec :a-b 1))\n;; (match r (Rec :a-b 1) 0)\n'(match r (Rec :a-b 1) 0)\n#_(match r (Rec :a-b 1) 0)\n").is_empty(), "match の外・註・quote・読み捨ては当たらない");
    }
}

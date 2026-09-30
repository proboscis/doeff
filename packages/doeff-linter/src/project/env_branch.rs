//! DOEFF168: 業務の層が環境の名の値や dry-run の印で分岐しない(agora-redesign #1906 — law business-code-has-no-environment-branch)。
//!
//! 環境の名の値・dry-run の印の綴りと、当てる層は repo の architecture.hy の `:environment-branches` にだけ在り、ここには書かない。
//! 業務の層は環境を知らない — 環境の違い(本番・模擬・dry-run)は handler の組の差し替えだけで表す。当てる形(Hy の form の木):
//!   * 比べる form(`=`・`!=`・`is`・`is-not`・`in`・`not-in`)の引数に、環境の名の値(:values)の文字列の literal が在る
//!     (集まりの literal `#{…}`・`[…]`・`#(…)` の要素も数える)— `(if (= mode "emulated") 0 total)`。
//!   * `match` の節の型が環境の名の値の文字列の literal — `(match mode "emulated" 0 _ total)`。
//!   * 分岐の form(`if`・`when`・`unless` の条件・`cond` の各条件・`match` の主語)の中に、dry-run の印(:flags)の名の記号が在る
//!     (点で区切った最後の段を mangle して比べる — `dry-run`・`self.dry-run`・`args.dry_run`)— `(when dry-run (return 0))`。
//! 註・`#_` で読み捨てた form・quote した form は数えない。環境の名でない値との比較(`(= kind "message")`)は当たらない。

use doeff_indexer::hy_index::mangle;
use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader, StrKind};

use super::architecture::EnvironmentBranches;

/// 比べる form の頭。
const COMPARISONS: &[&str] = &["=", "!=", "is", "is-not", "in", "not-in"];
/// 条件を 1 つ(頭の次の form)持つ分岐の form の頭。
const SINGLE_TEST_BRANCHES: &[&str] = &["if", "when", "unless"];

/// 当たった綴りの種類。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BranchHit {
    /// 環境の名の値の文字列の literal と比べた(比べる form か match の節の型)。
    Value(String),
    /// dry-run の印で分岐した(分岐の条件か match の主語の中の記号)。
    Flag(String),
}

impl BranchHit {
    /// 鍵の細目と説明に使う綴り(値は `value:<綴り>`・印は `flag:<綴り>`)。
    pub fn detail(&self) -> String {
        match self {
            BranchHit::Value(value) => format!("value:{}", value),
            BranchHit::Flag(flag) => format!("flag:{}", flag),
        }
    }
}

/// 当たり 1 つ(当たった literal か記号の byte の範囲)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnvironmentBranchHit {
    pub start: usize,
    pub end: usize,
    pub hit: BranchHit,
}

/// Hy の中身 1 つを宣言に当てる(当たりは位置の順)。
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
    /// dry-run の印の名(mangle 済み)。
    flags: Vec<String>,
}

impl Judge<'_> {
    /// form の木を歩き、比べる form・match・分岐の form を判じる(quote した form と読み捨てた form には入らない)。
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

    /// `(match 主語 型 本体 型 本体 …)` — 主語の中の印と、節の型の環境の名の値を当てる(型の後の `:if` の守りは型に数えない)。
    fn judge_match(&self, rest: &[Form], out: &mut Vec<EnvironmentBranchHit>) {
        let Some((subject, clauses)) = rest.split_first() else { return };
        self.flags_in(subject, out);
        let mut at = 0;
        while at < clauses.len() {
            self.value_literal(&clauses[at], out);
            // 節 = 型・(:if 守り)・本体。
            at += if clauses.get(at + 1).is_some_and(|f| matches!(f.node, Node::Keyword) && self.text(f) == ":if") { 4 } else { 2 };
        }
    }

    /// 比べる form の引数 1 つ — 文字列の literal か、集まりの literal の要素の文字列の literal が環境の名の値なら当てる。
    fn values_in(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        match &form.node {
            Node::Seq { delim: Delim::Bracket | Delim::Tuple | Delim::Set, items } => items.iter().for_each(|item| self.value_literal(item, out)),
            _ => self.value_literal(form, out),
        }
    }

    /// form が環境の名の値の文字列の literal なら当てる。
    fn value_literal(&self, form: &Form, out: &mut Vec<EnvironmentBranchHit>) {
        if let Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } = &form.node {
            let text = self.source.get(body.start..body.end).unwrap_or("");
            if let Some(value) = self.decl.values.iter().find(|value| value.as_str() == text) {
                out.push(EnvironmentBranchHit { start: form.span.start, end: form.span.end, hit: BranchHit::Value(value.clone()) });
            }
        }
    }

    /// 分岐の条件の式の中の、dry-run の印の名の記号を当てる(点で区切った最後の段を mangle して比べる・quote の中は見ない)。
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
        assert!(details("(defk f [kind] (if (= kind \"message\") 0 1))\n").is_empty(), "環境の名でない値との比較は当たらない");
        assert!(details(";; (= mode \"emulated\") は書かない\n(defk f [] \"emulated の時は …\")\n").is_empty(), "註と比べない文字列は当たらない");
        assert!(details("(defk f [] '(= mode \"emulated\"))\n#_(when dry-run 0)\n").is_empty(), "quote と読み捨てた form は当たらない");
        assert!(details("(defk f [dry-run] (setv x dry-run) (print \"dry-run\"))\n").is_empty(), "分岐の条件の外の印は当たらない");
    }
}

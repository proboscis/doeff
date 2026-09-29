//! DOEFF147: 呼んでよい頭を決めた定義(agora-redesign #1372・#1413 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/fault_boundary_rules.hy の (e)(g))。
//!
//! architecture.hy の `:allowed-heads [(allowed-heads "module:名" :heads [..] :why "…")]` の定義ごとに:
//!   * 定義の form(入れ子の定義を含む)を読み取り器で読み、`( … )` の頭が記号か keyword の物の綴りを集める。文字列・註・`#_` で
//!     読み捨てた form は読まない。:heads に無い頭ごとに 1 件(鍵の細目 = 頭の綴り・位置 = 定義の中で最初に現れた所)。
//!   * 宣言した定義が見つからない(module の Hy の file が無い・定義が無い)なら architecture.hy の位置で 1 件(`missing`)。
//! 頭は関数の呼び出しだけでなく特殊形式(`when`・`setv`)と macro(`defk`・`<-`)も含む — 例外を上げうるかは linter には分からない
//! ので、宣言が綴りで決める。`#( … )` の tuple と `[ … ]`・`{ … }` の要素は呼び出しでないので頭に数えない(元の判定の正規表現は
//! tuple の 1 つ目も頭に数えていた — 数えない分だけ当たりが少ない)。
//! 読むのは宣言した定義の module の file だけ(repo 全体は読まない)。定義は file の top level の form のうち、頭が `def` で始まる記号
//! (defk・defn・defhandler …)で 2 つ目が宣言の名の物 — 索引は組まない(定義の範囲が要るだけで、索引の解析は 2,000 行の file 1 つで
//! 秒のけたを使う)。

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::{mangle, Range};

use super::architecture::AllowedHeads;
use crate::position::LineIndex;

/// 当たりの種類(閉じた 2 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HeadProblem {
    /// 定義の中に一覧の外の頭 head が在る。
    Unlisted { head: String },
    /// 宣言した定義が見つからない。
    Missing { reason: String },
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct HeadFinding {
    /// 宣言の綴り(`module:名`)。
    pub declared: String,
    pub why: String,
    /// 当たりの file(根からの path)と、その中の位置。Missing は architecture.hy。
    pub rel: String,
    pub range: Range,
    pub problem: HeadProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

/// form の木の中の `( … )` の頭(記号と keyword)の byte の範囲を、現れた順に積む。
fn heads_of(form: &Form, out: &mut Vec<(usize, usize)>) {
    match &form.node {
        Node::Seq { delim, items } => {
            if let (Delim::Paren, Some(first)) = (delim, items.first()) {
                if matches!(first.node, Node::Symbol | Node::Keyword) {
                    out.push((first.span.start, first.span.end));
                }
            }
            for item in items {
                heads_of(item, out);
            }
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => heads_of(inner, out),
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                heads_of(part, out);
            }
        }
        Node::Prefixed { inner: None, .. }
        | Node::Tagged { inner: None }
        | Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Discarded => {}
    }
}

/// 宣言 1 つを判じる。読めなかった file は errors へ積む。
fn judge_one(root: &Path, declared: &AllowedHeads, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<HeadFinding> {
    let spelling = declared.definition.spelling();
    let finding = |rel: String, range: Range, problem: HeadProblem, detail: String| HeadFinding {
        declared: spelling.clone(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail,
    };
    let missing = |reason: String| vec![finding(architecture_rel.to_string(), declared.range, HeadProblem::Missing { reason }, "missing".to_string())];
    let module = declared.definition.mangled_module();
    let rel = format!("{}.hy", module.replace('.', "/"));
    let path = root.join(&rel);
    let source = match std::fs::read_to_string(&path) {
        Ok(source) => source,
        Err(error) if path.exists() => {
            errors.push(format!("{}: 読めない: {}", rel, error));
            return Vec::new();
        }
        Err(_) => return missing(format!("module {} の Hy の file({})が repo に無い", declared.definition.module, rel)),
    };
    let top = Reader::new(&source, 0, source.len()).read_all();
    let wanted = mangle(&declared.definition.name);
    let symbol = |form: &Form| matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end]);
    let defines = |form: &&Form| match form.paren_items() {
        Some([head, name, ..]) => symbol(head).is_some_and(|h| h.starts_with("def")) && symbol(name).is_some_and(|n| mangle(n) == wanted),
        _ => false,
    };
    let Some(definition) = top.iter().find(defines) else {
        return missing(format!("{} に定義 {} が無い", rel, declared.definition.name));
    };
    let mut spans = Vec::new();
    heads_of(definition, &mut spans);
    // 頭ごとに最初の位置 1 つ(綴りの順 — 当たりの並びを決める)。
    let mut first_at: BTreeMap<&str, (usize, usize)> = BTreeMap::new();
    for (s, e) in spans {
        first_at.entry(&source[s..e]).or_insert((s, e));
    }
    let lines = LineIndex::new(&source);
    first_at
        .into_iter()
        .filter(|(head, _)| !declared.heads.iter().any(|allowed| allowed == head))
        .map(|(head, (s, e))| finding(rel.clone(), lines.range(s, e), HeadProblem::Unlisted { head: head.to_string() }, head.to_string()))
        .collect()
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(見つからない宣言の位置)。
pub fn find(root: &Path, declarations: &[AllowedHeads], architecture_rel: &str) -> (Vec<HeadFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found: Vec<HeadFinding> = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn heads(text: &str) -> Vec<String> {
        let mut spans = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            heads_of(&form, &mut spans);
        }
        spans.into_iter().map(|(s, e)| text[s..e].to_string()).collect()
    }

    #[test]
    fn heads_are_the_first_symbol_of_each_paren_form_outside_strings_and_comments() {
        let text = "(defk f [x] {:pre [(: x str)]} ; (decode x) は註\n  \"(parse x)\" (when (.get x 1) (Send x)) #_(boom) #(tuple 1))";
        assert_eq!(heads(text), vec!["defk", ":", "when", ".get", "Send"]);
    }
}

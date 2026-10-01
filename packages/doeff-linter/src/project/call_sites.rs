//! DOEFF159: 頭を呼んでよい場所と回数を決めた綴り(agora-redesign #1372・#1414 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/fault_boundary_rules.hy の (a)(b)(c)(f)(h))。
//!
//! architecture.hy の `:call-sites [(call-site "頭" :files [..] :except [..] :sites [(site "module:名" :count N :parent "頭" :branch "名")]
//! :why "…")]` の宣言ごとに、:files の glob に当たる Hy の file(:except を除く)を読み取り器の木で歩き、頭がその綴りの `( … )` の呼びを集める
//! (文字列・註・`#_` の中は呼びでない)。そのうえで:
//!   * どの :sites の定義の中にも無い呼び → file ごとに 1 件(`<頭>:outside`・位置 = 最初の呼び)。
//!   * :count を書いた場所の中の呼びの数が違う → 場所ごとに 1 件(`<頭>:count:<名>`)。
//!   * :parent を書いた場所の中の呼びのうち、直ぐ外の form の頭がその綴りでない物 → 場所ごとに 1 件(`<頭>:parent:<名>`)。
//!   * :branch を書いた場所の中の呼びのうち、cond・when・if・unless の分岐で条件の form にその記号が在る枝の中に無い物
//!     → 場所ごとに 1 件(`<頭>:branch:<名>`)。
//!   * 場所の定義が無い → architecture.hy の位置で 1 件(`<頭>:missing:<名>`)。:files に当たる file が 0 → 1 件(`<頭>:empty`)。
//! 母集団 0 と消えた定義を緑にしない。場所の定義は file の top level の form のうち頭が `def` で始まり 2 つ目が宣言の名の物(DOEFF147 と同じ)。
//! 歩くのは :files の glob の字義どおりの頭の dir だけ(repo 全体は歩かない)。

use std::collections::BTreeSet;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::Range;

use super::architecture::{CallSite, CallSiteSite};
use super::paths::{glob_matches, relative_path};
use super::top_level;
use crate::position::LineIndex;

/// 当たりの種類(閉じた 6 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallSiteProblem {
    /// 場所の外の呼び(file の中の数)。
    Outside { count: usize },
    /// 場所の中の呼びの数が宣言と違う。
    Count { site: String, expected: usize, actual: usize },
    /// 場所の中の呼びの直ぐ外の form の頭が宣言と違う。
    Parent { site: String, parent: String },
    /// 場所の中の呼びが宣言した分岐の外。
    Branch { site: String, branch: String },
    /// 場所の定義が無い。
    Missing { site: String, reason: String },
    /// :files に当たる file が 0。
    Empty,
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct CallSiteFinding {
    pub head: String,
    pub why: String,
    /// 当たりの file(根からの path)と、その中の位置。Missing と Empty は architecture.hy。
    pub rel: String,
    pub range: Range,
    pub problem: CallSiteProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

/// 見つけた呼び 1 つ。
struct Call {
    rel: String,
    start: usize,
    end: usize,
    /// 直ぐ外の `( … )` の頭(記号でなければ None)。
    parent: Option<String>,
    /// 呼びを含む分岐の条件の form の記号(外の分岐から順に全部)。
    branch_symbols: BTreeSet<String>,
}

/// 場所 1 つの在りか。
struct Located<'s> {
    site: &'s CallSiteSite,
    rel: String,
    start: usize,
    end: usize,
    /// 定義の名の位置(回数などの当たりの位置)。
    name_at: (usize, usize),
    source: String,
}

/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

fn symbol_text<'t>(source: &'t str, form: &Form) -> Option<&'t str> {
    matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end])
}

/// form の木の中の記号を全部集める。
fn symbols_of(source: &str, form: &Form, out: &mut BTreeSet<String>) {
    match &form.node {
        Node::Symbol => {
            out.insert(source[form.span.start..form.span.end].to_string());
        }
        Node::Seq { items, .. } => items.iter().for_each(|item| symbols_of(source, item, out)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => symbols_of(source, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| symbols_of(source, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 木を歩き、頭が head の呼びを集める。parent は直ぐ外の `( … )` の頭・branch は外の分岐の条件の記号。
fn collect(source: &str, rel: &str, form: &Form, head: &str, parent: Option<&str>, branch: &BTreeSet<String>, out: &mut Vec<Call>) {
    match &form.node {
        Node::Seq { delim, items } => {
            let own = match (delim, items.first()) {
                (Delim::Paren, Some(first)) => symbol_text(source, first),
                _ => None,
            };
            if own == Some(head) {
                out.push(Call { rel: rel.to_string(), start: form.span.start, end: form.span.end, parent: parent.map(str::to_string), branch_symbols: branch.clone() });
            }
            let child_parent = if matches!(delim, Delim::Paren) { own } else { parent };
            for (index, item) in items.iter().enumerate() {
                // 分岐の枝: cond は 2 つ目から 1 つおきが枝(直前が条件)・when / if / unless は条件の後ろが枝。
                let test = match own {
                    Some("cond") if index >= 2 && index % 2 == 0 => items.get(index - 1),
                    Some("when" | "if" | "unless") if index >= 2 => items.get(1),
                    _ => None,
                };
                match test {
                    Some(test) => {
                        let mut inner = branch.clone();
                        symbols_of(source, test, &mut inner);
                        collect(source, rel, item, head, child_parent, &inner, out);
                    }
                    None => collect(source, rel, item, head, child_parent, branch, out),
                }
            }
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => collect(source, rel, inner, head, parent, branch, out),
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                collect(source, rel, part, head, parent, branch, out);
            }
        }
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// glob の字義どおりの頭の段(`*` を含む段の手前まで)— 歩き始める所。`/` の無い型は根。
fn literal_prefix(pattern: &str) -> String {
    if !pattern.contains('/') {
        return String::new();
    }
    pattern.split('/').take_while(|s| !s.contains(['*', '?', '['])).collect::<Vec<_>>().join("/")
}

/// :files に当たり :except に当たらない Hy の file(根からの path の順)。
fn files_of(root: &Path, declared: &CallSite) -> Vec<String> {
    let mut rels: BTreeSet<String> = BTreeSet::new();
    for pattern in &declared.files {
        let start = root.join(literal_prefix(pattern));
        let walker = walkdir::WalkDir::new(&start).follow_links(false).into_iter().filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        });
        rels.extend(
            walker
                .filter_map(Result::ok)
                .filter(|entry| entry.file_type().is_file() && entry.path().extension().is_some_and(|e| e == "hy"))
                .filter_map(|entry| relative_path(root, entry.path()))
                .filter(|rel| glob_matches(pattern, rel) && !declared.except.iter().any(|p| glob_matches(p, rel))),
        );
    }
    rels.into_iter().collect()
}

/// 場所の定義を探す(file の top level の、頭が def で始まり 2 つ目が名の form)。
fn locate<'s>(root: &Path, site: &'s CallSiteSite) -> Result<Located<'s>, String> {
    let rel = format!("{}.hy", site.definition.mangled_module().replace('.', "/"));
    let source = std::fs::read_to_string(root.join(&rel)).map_err(|_| format!("module {} の Hy の file({})が読めない", site.definition.module, rel))?;
    let top = Reader::new(&source, 0, source.len()).read_all();
    let found = top_level::definition(&source, &top, &site.definition.name).and_then(|form| match form.paren_items() {
        Some([_, name, ..]) => Some((form.span.start, form.span.end, (name.span.start, name.span.end))),
        _ => None,
    });
    let (start, end, name_at) = found.ok_or_else(|| format!("{} に定義 {} が無い", rel, site.definition.name))?;
    Ok(Located { site, rel, start, end, name_at, source })
}

/// 宣言 1 つを判じる。
fn judge_one(root: &Path, declared: &CallSite, architecture_rel: &str) -> Vec<CallSiteFinding> {
    let head = declared.head.as_str();
    let finding = |rel: String, range: Range, problem: CallSiteProblem, detail: String| CallSiteFinding {
        head: head.to_string(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail: format!("{}:{}", head, detail),
    };
    let mut out = Vec::new();
    let mut located: Vec<Located> = Vec::new();
    for site in &declared.sites {
        match locate(root, site) {
            Ok(found) => located.push(found),
            Err(reason) => {
                let name = site.definition.name.clone();
                out.push(finding(architecture_rel.to_string(), site.range, CallSiteProblem::Missing { site: site.definition.spelling(), reason }, format!("missing:{}", name)));
            }
        }
    }
    let rels = files_of(root, declared);
    if rels.is_empty() {
        out.push(finding(architecture_rel.to_string(), declared.range, CallSiteProblem::Empty, "empty".to_string()));
        return out;
    }
    let mut calls: Vec<Call> = Vec::new();
    for rel in &rels {
        let Ok(source) = std::fs::read_to_string(root.join(rel)) else { continue };
        for form in Reader::new(&source, 0, source.len()).read_all() {
            collect(&source, rel, &form, head, None, &BTreeSet::new(), &mut calls);
        }
    }
    let inside = |call: &Call, place: &Located| call.rel == place.rel && place.start <= call.start && call.end <= place.end;
    // 場所の外の呼び(file ごとに 1 件)。
    let mut outside_by_file: Vec<(String, Vec<&Call>)> = Vec::new();
    for call in calls.iter().filter(|call| !located.iter().any(|place| inside(call, place))) {
        match outside_by_file.iter_mut().find(|(rel, _)| rel == &call.rel) {
            Some((_, list)) => list.push(call),
            None => outside_by_file.push((call.rel.clone(), vec![call])),
        }
    }
    for (rel, list) in outside_by_file {
        let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
        let range = LineIndex::new(&source).range(list[0].start, list[0].end.min(list[0].start + 1 + head.len()));
        out.push(finding(rel, range, CallSiteProblem::Outside { count: list.len() }, "outside".to_string()));
    }
    // 場所ごとの回数・外の form・分岐。
    for place in &located {
        let lines = LineIndex::new(&place.source);
        let name = place.site.definition.name.clone();
        let spelling = place.site.definition.spelling();
        let here: Vec<&Call> = calls.iter().filter(|call| inside(call, place)).collect();
        let at = |call: Option<&&Call>| match call {
            Some(call) => lines.range(call.start, call.end.min(call.start + 1 + head.len())),
            None => lines.range(place.name_at.0, place.name_at.1),
        };
        if let Some(expected) = place.site.count.filter(|expected| *expected != here.len()) {
            out.push(finding(
                place.rel.clone(),
                at(None),
                CallSiteProblem::Count { site: spelling.clone(), expected, actual: here.len() },
                format!("count:{}", name),
            ));
        }
        if let Some(parent) = &place.site.parent {
            let wrong: Vec<&&Call> = here.iter().filter(|call| call.parent.as_deref() != Some(parent.as_str())).collect();
            if !wrong.is_empty() {
                out.push(finding(place.rel.clone(), at(wrong.first().copied()), CallSiteProblem::Parent { site: spelling.clone(), parent: parent.clone() }, format!("parent:{}", name)));
            }
        }
        if let Some(branch) = &place.site.branch {
            let wrong: Vec<&&Call> = here.iter().filter(|call| !call.branch_symbols.contains(branch)).collect();
            if !wrong.is_empty() {
                out.push(finding(place.rel.clone(), at(wrong.first().copied()), CallSiteProblem::Branch { site: spelling.clone(), branch: branch.clone() }, format!("branch:{}", name)));
            }
        }
    }
    out
}

/// 当たりの 1 行の文(知らせの message と説明の「これは」が同じ文を使う)。
pub fn describe(head: &str, problem: &CallSiteProblem) -> String {
    match problem {
        CallSiteProblem::Outside { count } => format!("({} …) を宣言した場所の外で {} 回呼ぶ", head, count),
        CallSiteProblem::Count { site, expected, actual } => format!("{} の中の ({} …) が {} 回(宣言は {} 回ちょうど)", site, head, actual, expected),
        CallSiteProblem::Parent { site, parent } => format!("{} の中の ({} …) の直ぐ外の form が ({} …) でない", site, head, parent),
        CallSiteProblem::Branch { site, branch } => format!("{} の中の ({} …) が条件に {} を持つ分岐の外に在る", site, head, branch),
        CallSiteProblem::Missing { site, reason } => format!("({} …) を呼んでよい場所 {} が無い — {}", head, site, reason),
        CallSiteProblem::Empty => format!("({} …) を探す :files に当たる Hy の file が 0", head),
    }
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(見つからない場所と空の母集団の位置)。
pub fn find(root: &Path, declarations: &[CallSite], architecture_rel: &str) -> Vec<CallSiteFinding> {
    declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn calls(text: &str, head: &str) -> Vec<Call> {
        let mut out = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            collect(text, "x.hy", &form, head, None, &BTreeSet::new(), &mut out);
        }
        out
    }

    #[test]
    fn calls_carry_their_parent_head_and_branch_tests() {
        let text = "(defk f [a]\n  (cond (isinstance a Done) (x (go a)) (isinstance a Failed) (go a))\n  ;; (go a) は註\n  \"(go a)\" #_(go a) (when (ok a) (go a)))";
        let found = calls(text, "go");
        assert_eq!(found.len(), 3, "註・文字列・#_ の中は呼びでない");
        assert_eq!(found[0].parent.as_deref(), Some("x"));
        assert!(found[0].branch_symbols.contains("Done"));
        assert!(!found[0].branch_symbols.contains("Failed"));
        assert!(found[1].branch_symbols.contains("Failed"));
        assert_eq!(found[1].parent.as_deref(), Some("cond"));
        assert!(found[2].branch_symbols.contains("ok"));
    }

    #[test]
    fn walking_starts_at_the_literal_head_of_the_glob() {
        assert_eq!(literal_prefix("controllers/screen/**/*.hy"), "controllers/screen");
        assert_eq!(literal_prefix("controllers/screen/entry/server.hy"), "controllers/screen/entry/server.hy");
        assert_eq!(literal_prefix("*.hy"), "");
    }
}

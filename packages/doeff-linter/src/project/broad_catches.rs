//! DOEFF160: 広い例外の捕捉は運搬の境界だけ(agora-redesign #1372・#1415 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/fault_boundary_rules.hy の (d))。
//!
//! architecture.hy の `:broad-catches [(broad-catch "名" :files [..] :except [..] :carriers [(carrier "module:名" :event "出来事")] :why "…")]`
//! の群ごとに、:files の glob に当たる Hy・Python の file(:except を除く)の広い捕捉を集める。広い捕捉とは:
//!   * Hy: `(except [] …)` と、捕まえる型の form に BROAD_NAMES の記号(`.` で区切った最後の段)が在る `(except [型] …)`・`(except [名 型] …)`。
//!     文字列・註・`#_` の中は捕捉でない(読み取り器の木で歩く)。
//!   * Python: 型の無い `except:` と、型が BROAD_NAMES の名・属性・その組の `except …:`(rustpython の構文木で歩く)。
//! 許すのは :carriers の定義(Hy の file の top level の form)の中の Hy の捕捉で、捕捉が例外を名で束縛し(`(except [名 型] …)`)、
//! その捕捉の form の中の `(出来事 …)` の呼びが、その名を引数に直に渡す物だけ(握りつぶさずに列へ運ぶ境界)。そのうえで:
//!   * 運搬の境界の外の広い捕捉 → file ごとに 1 件(`<名>:outside`・位置 = 最初の捕捉)。
//!   * 境界の中の広い捕捉が名を束縛しない・その名を出来事へ渡さない → 境界ごとに 1 件(`<名>:unbound:<定義の名>`)。
//!   * 境界の定義が無い → architecture.hy の位置で 1 件(`<名>:missing:<定義の名>`)。
//!   * :files に当たる file が 0 → 1 件(`<名>:empty`)。
//! 母集団 0 と消えた定義を緑にしない。Python の file は構文木が読めなければ errors へ積む(黙って 0 件にしない)。

use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::Range;
use rustpython_ast::{ExceptHandler, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::{BroadCatch, BroadCatchCarrier};
use super::spelling_scope::selected_files;
use super::top_level;
use crate::position::LineIndex;

/// 捕まえると広い捕捉になる型の名(契約の破れ〔AssertionError〕とその上の型)。
const BROAD_NAMES: &[&str] = &["Exception", "BaseException", "AssertionError"];

/// 当たりの種類(閉じた 4 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BroadCatchProblem {
    /// 運搬の境界の外の広い捕捉(file の中の数)。
    Outside { count: usize },
    /// 境界の中の広い捕捉が、例外を名で束縛して出来事へ渡していない。
    Unbound { carrier: String, event: String },
    /// 境界の定義が無い。
    Missing { carrier: String, reason: String },
    /// :files に当たる file が 0。
    Empty,
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct BroadCatchFinding {
    /// 群の名。
    pub group: String,
    pub why: String,
    /// 当たりの file(根からの path)と、その中の位置。Missing と Empty は architecture.hy。
    pub rel: String,
    pub range: Range,
    pub problem: BroadCatchProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

/// 見つけた広い捕捉 1 つ(byte の範囲)。
struct Catch {
    rel: String,
    start: usize,
    end: usize,
    /// Hy の捕捉が例外を束縛した名(Python と束縛の無い捕捉は None)。
    bound: Option<String>,
}

/// 境界 1 つの在りか。
struct Located<'c> {
    carrier: &'c BroadCatchCarrier,
    rel: String,
    start: usize,
    end: usize,
}

fn symbol_text<'t>(source: &'t str, form: &Form) -> Option<&'t str> {
    matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end])
}

fn is_broad_name(name: &str) -> bool {
    BROAD_NAMES.contains(&name.rsplit('.').next().unwrap_or(name))
}

/// form の木の中に広い型の記号が在るか。
fn names_broad(source: &str, form: &Form) -> bool {
    match &form.node {
        Node::Symbol => is_broad_name(&source[form.span.start..form.span.end]),
        Node::Seq { items, .. } => items.iter().any(|item| names_broad(source, item)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => names_broad(source, inner),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().any(|part| names_broad(source, part)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => false,
    }
}

/// `(except [..] …)` の form なら、広いかと束縛した名。広くない捕捉と捕捉でない form は None。
fn broad_hy_catch(source: &str, form: &Form) -> Option<Option<String>> {
    let items = form.paren_items()?;
    let [head, spec, ..] = items else { return None };
    if symbol_text(source, head) != Some("except") {
        return None;
    }
    let Node::Seq { delim: Delim::Bracket, items: spec } = &spec.node else { return None };
    match spec.as_slice() {
        [] => Some(None),
        [types] => names_broad(source, types).then_some(None),
        [name, types, ..] => names_broad(source, types).then(|| symbol_text(source, name).map(str::to_string)),
    }
}

/// Hy の木を歩き、広い捕捉を集める。
fn collect_hy(source: &str, rel: &str, form: &Form, out: &mut Vec<Catch>) {
    match &form.node {
        Node::Seq { items, .. } => {
            if let Some(bound) = broad_hy_catch(source, form) {
                out.push(Catch { rel: rel.to_string(), start: form.span.start, end: form.span.end, bound });
            }
            items.iter().for_each(|item| collect_hy(source, rel, item, out));
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => collect_hy(source, rel, inner, out),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().for_each(|part| collect_hy(source, rel, part, out)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

/// 型の式が広いか(名・属性・その組)。
fn expr_is_broad(expr: &Expr) -> bool {
    // 型の位置に来うる式は名・属性・組だけを見る(呼びや添字で型を作る捕捉は広さを静的に決められないので数えない)。
    if let Expr::Name(name) = expr {
        is_broad_name(name.id.as_str())
    } else if let Expr::Attribute(attribute) = expr {
        is_broad_name(attribute.attr.as_str())
    } else if let Expr::Tuple(tuple) = expr {
        tuple.elts.iter().any(expr_is_broad)
    } else {
        false
    }
}

fn python_handlers(rel: &str, handlers: &[ExceptHandler], out: &mut Vec<Catch>) {
    for handler in handlers {
        let ExceptHandler::ExceptHandler(h) = handler;
        if h.type_.as_deref().is_none_or(expr_is_broad) {
            let start = h.range.start().to_usize();
            out.push(Catch { rel: rel.to_string(), start, end: start + "except".len(), bound: None });
        }
        collect_python(rel, &h.body, out);
    }
}

/// Python の文の列を歩き、広い捕捉を集める(式の中に捕捉は無いので、文の入れ子だけを歩く)。
fn collect_python(rel: &str, body: &[Stmt], out: &mut Vec<Catch>) {
    for stmt in body {
        match stmt {
            Stmt::FunctionDef(node) => collect_python(rel, &node.body, out),
            Stmt::AsyncFunctionDef(node) => collect_python(rel, &node.body, out),
            Stmt::ClassDef(node) => collect_python(rel, &node.body, out),
            Stmt::For(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::AsyncFor(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::While(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::If(node) => {
                collect_python(rel, &node.body, out);
                collect_python(rel, &node.orelse, out);
            }
            Stmt::With(node) => collect_python(rel, &node.body, out),
            Stmt::AsyncWith(node) => collect_python(rel, &node.body, out),
            Stmt::Match(node) => node.cases.iter().for_each(|case| collect_python(rel, &case.body, out)),
            Stmt::Try(node) => {
                collect_python(rel, &node.body, out);
                python_handlers(rel, &node.handlers, out);
                collect_python(rel, &node.orelse, out);
                collect_python(rel, &node.finalbody, out);
            }
            Stmt::TryStar(node) => {
                collect_python(rel, &node.body, out);
                python_handlers(rel, &node.handlers, out);
                collect_python(rel, &node.orelse, out);
                collect_python(rel, &node.finalbody, out);
            }
            Stmt::Return(_)
            | Stmt::Delete(_)
            | Stmt::Assign(_)
            | Stmt::TypeAlias(_)
            | Stmt::AugAssign(_)
            | Stmt::AnnAssign(_)
            | Stmt::Raise(_)
            | Stmt::Assert(_)
            | Stmt::Import(_)
            | Stmt::ImportFrom(_)
            | Stmt::Global(_)
            | Stmt::Nonlocal(_)
            | Stmt::Expr(_)
            | Stmt::Pass(_)
            | Stmt::Break(_)
            | Stmt::Continue(_) => {}
        }
    }
}

/// form の木の中に、頭が event で、名 bound を直に引数に持つ呼びが在るか。
fn passes_to(source: &str, form: &Form, event: &str, bound: &str) -> bool {
    match &form.node {
        Node::Seq { delim, items } => {
            let here = matches!(delim, Delim::Paren)
                && items.first().and_then(|head| symbol_text(source, head)) == Some(event)
                && items.iter().skip(1).any(|item| symbol_text(source, item) == Some(bound));
            here || items.iter().any(|item| passes_to(source, item, event, bound))
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => passes_to(source, inner, event, bound),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().any(|part| passes_to(source, part, event, bound)),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => false,
    }
}

/// 境界の中の捕捉が、束縛した名を出来事へ渡しているか(捕捉の form を読み直して確かめる)。
fn carries(source: &str, catch: &Catch, event: &str) -> bool {
    let Some(bound) = &catch.bound else { return false };
    Reader::new(source, catch.start, catch.end).read_all().iter().any(|form| passes_to(source, form, event, bound))
}

/// 境界の定義を探す(Hy の file の top level の定義)。
fn locate<'c>(root: &Path, carrier: &'c BroadCatchCarrier) -> Result<Located<'c>, String> {
    let rel = format!("{}.hy", carrier.definition.mangled_module().replace('.', "/"));
    let source = std::fs::read_to_string(root.join(&rel)).map_err(|_| format!("module {} の Hy の file({})が読めない", carrier.definition.module, rel))?;
    let top = Reader::new(&source, 0, source.len()).read_all();
    let form = top_level::definition(&source, &top, &carrier.definition.name).ok_or_else(|| format!("{} に定義 {} が無い", rel, carrier.definition.name))?;
    Ok(Located { carrier, rel, start: form.span.start, end: form.span.end })
}

/// 群 1 つを判じる。読めなかった file は errors へ積む。
fn judge_one(root: &Path, declared: &BroadCatch, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<BroadCatchFinding> {
    let finding = |rel: String, range: Range, problem: BroadCatchProblem, detail: String| BroadCatchFinding {
        group: declared.name.clone(),
        why: declared.why.clone(),
        rel,
        range,
        problem,
        detail: format!("{}:{}", declared.name, detail),
    };
    let mut out = Vec::new();
    let mut located: Vec<Located> = Vec::new();
    for carrier in &declared.carriers {
        match locate(root, carrier) {
            Ok(found) => located.push(found),
            Err(reason) => out.push(finding(
                architecture_rel.to_string(),
                carrier.range,
                BroadCatchProblem::Missing { carrier: carrier.definition.spelling(), reason },
                format!("missing:{}", carrier.definition.name),
            )),
        }
    }
    let rels = selected_files(root, &declared.files, &declared.except);
    if rels.is_empty() {
        out.push(finding(architecture_rel.to_string(), declared.range, BroadCatchProblem::Empty, "empty".to_string()));
        return out;
    }
    for rel in &rels {
        let source = match std::fs::read_to_string(root.join(rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: 読めない: {}", rel, error));
                continue;
            }
        };
        let mut catches: Vec<Catch> = Vec::new();
        if rel.ends_with(".hy") {
            for form in Reader::new(&source, 0, source.len()).read_all() {
                collect_hy(&source, rel, &form, &mut catches);
            }
        } else {
            match parse(&source, Mode::Module, rel) {
                Ok(Mod::Module(module)) => collect_python(rel, &module.body, &mut catches),
                Ok(Mod::Interactive(_) | Mod::Expression(_) | Mod::FunctionType(_)) => {}
                Err(error) => {
                    errors.push(format!("{}: Python の構文木が読めない(広い捕捉を数えられない): {}", rel, error));
                    continue;
                }
            }
        }
        let lines = LineIndex::new(&source);
        let within = |catch: &Catch| located.iter().find(|place| place.rel == catch.rel && place.start <= catch.start && catch.end <= place.end);
        let outside: Vec<&Catch> = catches.iter().filter(|catch| within(catch).is_none()).collect();
        if let Some(first) = outside.first() {
            out.push(finding(rel.clone(), lines.range(first.start, first.start + 1), BroadCatchProblem::Outside { count: outside.len() }, "outside".to_string()));
        }
        // 境界ごとに、名を出来事へ渡さない捕捉(境界ごとに 1 件・位置 = 最初の物)。
        for place in &located {
            let event = &place.carrier.event;
            if let Some(first) = catches.iter().filter(|catch| within(catch).is_some_and(|p| std::ptr::eq(p, place))).find(|catch| !carries(&source, catch, event)) {
                out.push(finding(
                    rel.clone(),
                    lines.range(first.start, first.start + 1),
                    BroadCatchProblem::Unbound { carrier: place.carrier.definition.spelling(), event: event.clone() },
                    format!("unbound:{}", place.carrier.definition.name),
                ));
            }
        }
    }
    out
}

/// 当たりの 1 行の文(知らせの message と説明の「これは」が同じ文を使う)。
pub fn describe(group: &str, problem: &BroadCatchProblem) -> String {
    match problem {
        BroadCatchProblem::Outside { count } => format!("広い例外の捕捉が運搬の境界の外に {} 件在る(群 {})", count, group),
        BroadCatchProblem::Unbound { carrier, event } => {
            format!("運搬の境界 {} の広い捕捉が、例外を名で束縛して ({} …) へ渡していない(群 {})", carrier, event, group)
        }
        BroadCatchProblem::Missing { carrier, reason } => format!("群 {} の運搬の境界 {} が無い — {}", group, carrier, reason),
        BroadCatchProblem::Empty => format!("群 {} の :files に当たる Hy・Python の file が 0", group),
    }
}

/// 群の全部を判じる。architecture_rel は architecture.hy の根からの path(見つからない境界と空の母集団の位置)。
pub fn find(root: &Path, declarations: &[BroadCatch], architecture_rel: &str) -> (Vec<BroadCatchFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hy_catches(text: &str) -> Vec<Catch> {
        let mut out = Vec::new();
        for form in Reader::new(text, 0, text.len()).read_all() {
            collect_hy(text, "x.hy", &form, &mut out);
        }
        out
    }

    fn py_catches(text: &str) -> Vec<Catch> {
        let mut out = Vec::new();
        if let Ok(Mod::Module(module)) = parse(text, Mode::Module, "x.py") {
            collect_python("x.py", &module.body, &mut out);
        }
        out
    }

    #[test]
    fn broad_hy_catches_are_found_outside_strings_and_comments() {
        let text = "(try (a) (except [] 1) (except [ValueError] 2) (except [e Exception] 3) (except [e [KeyError builtins.BaseException]] 4)\n\
                    (except [AssertionError] 5))\n;; (except [Exception] x)\n\"(except [] y)\" #_(except [] z)";
        let found = hy_catches(text);
        assert_eq!(found.len(), 4, "狭い捕捉・註・文字列・#_ の中は数えない");
        assert_eq!(found[0].bound, None);
        assert_eq!(found[1].bound.as_deref(), Some("e"));
        assert_eq!(found[3].bound, None);
    }

    #[test]
    fn broad_python_catches_are_found_in_nested_statements() {
        let text = "def f():\n    try:\n        a()\n    except ValueError:\n        pass\n    except:\n        pass\n\nclass C:\n    def g(self):\n        \
                    for x in y:\n            try:\n                b()\n            except (KeyError, Exception) as error:\n                raise\n\n\
                    # except Exception:\ntry:\n    c()\nexcept builtins.AssertionError:\n    pass\n";
        assert_eq!(py_catches(text).len(), 3);
    }

    #[test]
    fn a_carrier_passes_the_bound_name_to_its_event() {
        let good = "(except [error Exception] (<- (PutChannel inbox (Queued :work w :failure error))))";
        let swallow = "(except [error Exception] (<- (PutChannel inbox (Queued :work w :failure None))))";
        let unbound = "(except [Exception] (<- (PutChannel inbox (Queued :work w))))";
        for (text, expected) in [(good, true), (swallow, false), (unbound, false)] {
            let found = hy_catches(text);
            assert_eq!(found.len(), 1);
            assert_eq!(carries(text, &found[0], "Queued"), expected, "{}", text);
        }
    }
}

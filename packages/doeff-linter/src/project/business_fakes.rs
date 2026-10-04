//! DOEFF143: 業務の効果に答える偽の handler を作らない(agora-redesign #1375 / #1367 / #1189 — 元は agora-controllers の一時の検
//! business_fakes_rules.hy の判定 A と D・#682・#715・#780)。
//!
//! 偽物 = 模擬の根から届き、本番の入口から届かない定義の中の effect の節。その節が業務の効果(定義元の module が
//! `:business-modules` に当たる)に tap でなく答え、外の世界の効果の表(`:external-effects`)にも反例の表(`:counterexamples`)にも
//! 無ければ critical。表の腐りも出す: 反例の表の行がもう当たらない・外の世界の表の行にどの偽物も答えない・外の世界の表の行に本番の
//! 入口から届く答え手が無い(`:unserved` に理由つきで載せた物を除く)。偽物が下の層の効果(`:lower-layer-modules`)に tap でなく
//! 答えるのも DOEFF143(下の層の偽物は repo の外の正典 1 つだけ — 元の判定 C)。
//!
//! DOEFF157(agora-redesign #1377 — 元の判定 B): 検だけの偽物 = 検の file の定義から届き、本番の入口からも模擬の根からも届かない
//! 定義の節。業務の効果か下の層の効果に tap でなく答え、外の世界の表にも反例の表にも無ければ critical。
//!
//! DOEFF158(#1377 — 元の判定 G): 本番の入口から届く節のうち、intent の層(`:assembly-shape :intent-layer`)の効果に tap でなく
//! 答えるのは、翻訳の層(`:translation-layer`)の handler 1 つだけ。翻訳の層の外の答え手と、同じ効果に答える翻訳の handler の 2 つ目以降を出す。
//!
//! DOEFF206(agora-redesign #3405・#3407・#3406): intent の層の効果には検証環境(`:verification-environment`)でも本番の翻訳の handler が
//! 答える。検証環境の dir の節が intent の効果に狭い tap(出し直しの答えをそのまま resume)でなく答える所と、違反を通す表(外の世界の表・
//! 下の層を通す表・検だけの偽物の表)の行が intent の効果を通す所を出す。登録簿で下げない(利用者 2026-10-04 "so this kind of violation,
//! must be detected by doeff linter")。
//!
//! 届く先は DOEFF133・136 と同じ定義の辺の図(呼び出し・参照・入れ子)を根から前向きに辿る。全体の実行だけ(repo 全体の図が要る)。
//! 模擬の根・本番の入口・業務の module・表の置き場は repo の宣言 `:business-fakes` から読み、ここには repo の名前を置かない。
//!
//! tap = 節の本体が同じ効果を出し直す節(節の頭の名の呼び・`(<- 答え effect)`・`(yield effect)` — 観測・障害の注入)。出し直した上で答えを変える節も tap に見える(読みの限界)。

use std::collections::{BTreeMap, HashMap};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use regex::Regex;

use super::architecture::BusinessFakes;
use super::paths::glob_matches;
use super::names::hy_mangle;

/// file の役(宣言の綴りの型から決める)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileRole {
    /// 読まない(:skip)。
    Skipped,
    /// 検の file(模擬の根にも本番の code にも数えない)。
    Test,
    /// 模擬の環境(模擬の根・本番の code ではない)。
    Simulation,
    /// 組み立ての層(模擬の根で、本番の code でもある)。
    Assembly,
    /// 本番の code。
    Production,
}

pub fn role_of(rel: &str, decl: &BusinessFakes) -> FileRole {
    let any = |patterns: &[String]| patterns.iter().any(|p| glob_matches(p, rel));
    if any(&decl.skip) {
        FileRole::Skipped
    } else if any(&decl.tests) {
        FileRole::Test
    } else if any(&decl.simulation) {
        FileRole::Simulation
    } else if any(&decl.assembly) {
        FileRole::Assembly
    } else {
        FileRole::Production
    }
}

/// 本番の code の file か(本番の code か組み立ての層で、:production を書けばそれに当たる物だけ)。
pub fn production_code(rel: &str, decl: &BusinessFakes) -> bool {
    matches!(role_of(rel, decl), FileRole::Production | FileRole::Assembly)
        && (decl.production.is_empty() || decl.production.iter().any(|p| glob_matches(p, rel)))
}

/// 組の file の定義か(:sets に当たる file の、名が prefix で始まる定義)。
pub fn set_member(rel: &str, name: &str, prefix: Option<&str>, decl: &BusinessFakes) -> bool {
    prefix.is_some_and(|p| !p.is_empty() && name.starts_with(p)) && decl.sets.iter().any(|s| glob_matches(s, rel))
}

/// module が業務の module か(`:business-modules` の綴り — `a.b` は a.b とその下・末尾 `*` は前方一致)。
pub fn business_module(module: &str, decl: &BusinessFakes) -> bool {
    listed(module, &decl.business_modules)
}

/// module が下の層の module か(`:lower-layer-modules` — 綴りは `:business-modules` と同じ)。
pub fn lower_layer_module(module: &str, decl: &BusinessFakes) -> bool {
    listed(module, &decl.lower_layer_modules)
}

fn listed(module: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| match p.strip_suffix('*') {
        Some(prefix) => module.starts_with(prefix),
        None => module == p || module.starts_with(&format!("{}.", p)),
    })
}

/// 効果が業務の効果か — 定義元の module が業務の module で、その file が検の file でも模擬の環境でもない(検や模擬の筋書きが
/// 自分で定義した効果は業務の語彙ではない)。
pub fn business_effect(effect: &str, decl: &BusinessFakes) -> bool {
    let module = module_of_effect(effect);
    business_module(module, decl) && !matches!(role_of(&format!("{}.hy", module.replace('.', "/")), decl), FileRole::Test | FileRole::Simulation)
}

/// 効果の完全名 → 定義元の module。
pub fn module_of_effect(effect: &str) -> &str {
    effect.rsplit_once('.').map(|(m, _)| m).unwrap_or("")
}

fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    &source[form.span.start..form.span.end]
}

fn head_symbol<'s>(source: &'s str, items: &[Form]) -> Option<&'s str> {
    items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| text(source, f))
}

/// form の中に同じ効果の出し直しが在るか — `(name …)` の呼び・受けた effect をそのまま外へ出す `(<- 答え effect)`・`(yield effect)`。
fn reissues(source: &str, form: &Form, name: &str) -> bool {
    match &form.node {
        Node::Seq { delim, items } => {
            let last_is_effect = items.last().is_some_and(|f| matches!(f.node, Node::Symbol) && text(source, f) == "effect");
            let here = *delim == Delim::Paren
                && match head_symbol(source, items) {
                    Some(head) if head == name => true,
                    Some("<-") => items.len() >= 3 && last_is_effect,
                    Some("yield") => items.len() == 2 && last_is_effect,
                    _ => false,
                };
            here || items.iter().any(|i| reissues(source, i, name))
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => reissues(source, inner, name),
        Node::Annotated { target: Some(target), .. } => reissues(source, target, name),
        _ => false,
    }
}

fn read_forms(source: &str) -> Vec<Form> {
    Reader::new(source, 0, source.len()).read_all()
}

/// file の defhandler の節ごとの tap(鍵 = (handler の名, 節の頭の名) — 同じ handler の同じ頭は 1 つに数え、どれかが tap なら tap)。
pub fn taps_in(source: &str) -> HashMap<(String, String), bool> {
    fn visit(source: &str, forms: &[Form], out: &mut HashMap<(String, String), bool>) {
        for form in forms {
            let Node::Seq { delim, items } = &form.node else { continue };
            if *delim == Delim::Paren && head_symbol(source, items) == Some("defhandler") && items.len() >= 2 {
                let handler = text(source, &items[1]).to_string();
                for clause in &items[2..] {
                    let Some(parts) = clause.paren_items() else { continue };
                    let Some(head) = head_symbol(source, parts) else { continue };
                    if parts.get(1).and_then(Form::bracket_items).is_none() {
                        continue;
                    }
                    let tap = parts[2..].iter().any(|p| reissues(source, p, head));
                    *out.entry((handler.clone(), head.to_string())).or_insert(false) |= tap;
                }
            }
            visit(source, items, out);
        }
    }
    let mut out = HashMap::new();
    visit(source, &read_forms(source), &mut out);
    out
}

/// DOEFF206 の狭い tap: 節の本体が同じ効果を出し直し(`(<- 名 [型] effect)`・`(<- 名 [型] (頭 受けた引数 …))`・`(setv 名 (yield effect))`)、
/// 本体の `(resume …)` の全部が、その出し直しで束ねた名**そのもの**を渡す節だけ。受けた effect の別名(`(val 名 effect)`・`(setv 名 effect)`)の
/// 出し直しも同じに数える。出し直した答えの欄を書き換える・別の値を組む・枝の 1 つで自前の値を渡す・引数を変えて出し直す節は tap でない
/// (DOEFF143 の `taps_in` は字面の出し直しだけを見る — そちらは変えない)。
fn passes_reissue_through(source: &str, params: &[&str], body: &[Form], head: &str) -> bool {
    /// 受けた effect の名と、その別名(`(val 名 X)`・`(setv 名 X)` の X が effect か別名 — 本体の全部から集める)。
    fn aliases<'s>(source: &'s str, form: &Form, out: &mut Vec<&'s str>) -> bool {
        let Node::Seq { items, .. } = &form.node else { return false };
        let mut grew = false;
        if let (Some("val" | "setv"), [_, name, value]) = (head_symbol(source, items), items.as_slice()) {
            let (name, value) = (text(source, name), text(source, value));
            if matches!(items[1].node, Node::Symbol) && matches!(items[2].node, Node::Symbol) && out.contains(&value) && !out.contains(&name) {
                out.push(name);
                grew = true;
            }
        }
        items.iter().fold(grew, |grew, item| aliases(source, item, out) || grew)
    }
    let mut effect_names: Vec<&str> = vec!["effect"];
    while body.iter().fold(false, |grew, form| aliases(source, form, &mut effect_names) || grew) {}
    /// 出し直しの式か(effect の名か別名・節が受けた引数をそのまま並べた `(頭 …)` の呼び(`:欄` の語は数えない)・`(yield 出し直し)`)。
    fn reissue(source: &str, form: &Form, head: &str, params: &[&str], effect_names: &[&str]) -> bool {
        match &form.node {
            Node::Symbol => effect_names.contains(&text(source, form)),
            Node::Seq { delim: Delim::Paren, items } => match head_symbol(source, items) {
                Some(h) if h == head => {
                    let args: Vec<&str> = items[1..].iter().map(|f| text(source, f)).filter(|t| !t.starts_with(':')).collect();
                    args == params
                }
                Some("yield") => items.len() == 2 && reissue(source, &items[1], head, params, effect_names),
                _ => false,
            },
            _ => false,
        }
    }
    let reissue = |form: &Form| reissue(source, form, head, params, &effect_names);
    fn walk<'s>(source: &'s str, form: &Form, reissue: &dyn Fn(&Form) -> bool, bound: &mut Vec<&'s str>, resumed: &mut Vec<Option<&'s str>>) {
        let Node::Seq { delim, items } = &form.node else { return };
        if *delim == Delim::Paren {
            match head_symbol(source, items) {
                // (<- 名 [型] 出し直し)
                Some("<-") if items.len() >= 3 && matches!(items[1].node, Node::Symbol) && reissue(&items[items.len() - 1]) => {
                    bound.push(text(source, &items[1]));
                }
                // (setv 名 (yield 出し直し))
                Some("setv") if items.len() == 3 && matches!(items[1].node, Node::Symbol) => {
                    if let Some(inner) = items[2].paren_items() {
                        if head_symbol(source, inner) == Some("yield") && inner.len() == 2 && reissue(&inner[1]) {
                            bound.push(text(source, &items[1]));
                        }
                    }
                }
                Some("resume") => {
                    resumed.push(match items.as_slice() {
                        [_, value] if matches!(value.node, Node::Symbol) => Some(text(source, value)),
                        _ => None,
                    });
                    return;
                }
                _ => {}
            }
        }
        items.iter().for_each(|item| walk(source, item, reissue, bound, resumed));
    }
    let mut bound = Vec::new();
    let mut resumed = Vec::new();
    body.iter().for_each(|form| walk(source, form, &reissue, &mut bound, &mut resumed));
    !resumed.is_empty() && resumed.iter().all(|value| value.is_some_and(|name| bound.contains(&name)))
}

/// file の defhandler の節ごとの DOEFF206 の狭い tap(鍵 = (handler の名, 節の頭の名) — 同じ handler の同じ頭が 2 つ在れば、両方が tap の時だけ
/// tap)。`taps_in` と同じ節の読み。
pub fn pass_through_taps_in(source: &str) -> HashMap<(String, String), bool> {
    fn visit(source: &str, forms: &[Form], out: &mut HashMap<(String, String), bool>) {
        for form in forms {
            let Node::Seq { delim, items } = &form.node else { continue };
            if *delim == Delim::Paren && head_symbol(source, items) == Some("defhandler") && items.len() >= 2 {
                let handler = text(source, &items[1]).to_string();
                for clause in &items[2..] {
                    let Some(parts) = clause.paren_items() else { continue };
                    let Some(head) = head_symbol(source, parts) else { continue };
                    let Some(params) = parts.get(1).and_then(Form::bracket_items) else { continue };
                    let params: Vec<&str> = params.iter().map(|p| text(source, p)).collect();
                    let tap = passes_reissue_through(source, &params, &parts[2..], head);
                    *out.entry((handler.clone(), head.to_string())).or_insert(true) &= tap;
                }
            }
            visit(source, items, out);
        }
    }
    let mut out = HashMap::new();
    visit(source, &read_forms(source), &mut out);
    out
}

/// process の入口の節 `(when (= __name__ "__main__") …)` の行の範囲(0 始まり・両端を含む)。
pub fn main_guard_lines(source: &str) -> Vec<(u32, u32)> {
    let line_of = |at: usize| source[..at].bytes().filter(|b| *b == b'\n').count() as u32;
    read_forms(source)
        .iter()
        .filter(|form| {
            let Some(items) = form.paren_items() else { return false };
            if head_symbol(source, items) != Some("when") {
                return false;
            }
            let Some(test) = items.get(1).and_then(Form::paren_items) else { return false };
            let mut words: Vec<&str> = test.iter().map(|f| text(source, f).trim_matches('"')).collect();
            words.sort();
            words == ["=", "__main__", "__name__"]
        })
        .map(|form| (line_of(form.span.start), line_of(form.span.end)))
        .collect()
}

/// 本文の中の入口の文字列(`"<module>:<名>"` — module の頭は `:entry-string-modules`)→ 定義の完全名(module.名 の mangle)。
pub fn entry_names(source: &str, decl: &BusinessFakes) -> Vec<String> {
    if decl.entry_string_modules.is_empty() {
        return Vec::new();
    }
    let heads = decl.entry_string_modules.iter().map(|m| regex::escape(m)).collect::<Vec<_>>().join("|");
    let Ok(pattern) = Regex::new(&format!(r"\b((?:{})(?:\.[A-Za-z_][A-Za-z0-9_]*)+):([A-Za-z_][A-Za-z0-9_-]*)", heads)) else {
        return Vec::new();
    };
    pattern.captures_iter(source).map(|c| format!("{}.{}", &c[1], hy_mangle(&c[2]))).collect()
}

/// Python の source の中の `isinstance(<何か>, X)` の X(import で完全名へ解いた物 — `from m import X` と `import m` の `m.X`)。
pub fn python_isinstance_effects(source: &str, importer: &str) -> Vec<String> {
    use rustpython_ast::{Expr, Mod, Stmt};
    let Ok(Mod::Module(module)) = rustpython_parser::parse(source, rustpython_parser::Mode::Module, "<business-fakes>") else { return Vec::new() };
    let mut names: HashMap<String, String> = HashMap::new();
    for stmt in &module.body {
        match stmt {
            Stmt::ImportFrom(i) => {
                let level = i.level.map(|l| l.to_usize()).unwrap_or(0);
                let m = super::names::absolute_module(importer, &format!("{}{}", ".".repeat(level), i.module.as_ref().map(|m| m.as_str()).unwrap_or("")));
                for alias in &i.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    names.insert(local, format!("{}.{}", m, alias.name));
                }
            }
            Stmt::Import(i) => {
                for alias in &i.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    names.insert(local, alias.name.to_string());
                }
            }
            _ => {}
        }
    }
    fn spelling(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(n) => Some(n.id.to_string()),
            Expr::Attribute(a) => spelling(&a.value).map(|head| format!("{}.{}", head, a.attr)),
            _ => None,
        }
    }
    let mut out = Vec::new();
    let mut resolve = |expr: &Expr| {
        let targets: Vec<&Expr> = match expr {
            Expr::Tuple(t) => t.elts.iter().collect(),
            other => vec![other],
        };
        for target in targets {
            let Some(text) = spelling(target) else { continue };
            let (head, rest) = text.split_once('.').map(|(h, r)| (h.to_string(), Some(r.to_string()))).unwrap_or((text.clone(), None));
            if let Some(full) = names.get(&head) {
                out.push(match rest {
                    Some(rest) => format!("{}.{}", full, rest),
                    None => full.clone(),
                });
            }
        }
    };
    let mut stack: Vec<&Expr> = Vec::new();
    fn exprs_of<'a>(stmts: &'a [Stmt], out: &mut Vec<&'a Expr>) {
        for stmt in stmts {
            match stmt {
                Stmt::If(s) => {
                    out.push(&s.test);
                    exprs_of(&s.body, out);
                    exprs_of(&s.orelse, out);
                }
                Stmt::While(s) => {
                    out.push(&s.test);
                    exprs_of(&s.body, out);
                }
                Stmt::FunctionDef(f) => exprs_of(&f.body, out),
                Stmt::AsyncFunctionDef(f) => exprs_of(&f.body, out),
                Stmt::ClassDef(c) => exprs_of(&c.body, out),
                Stmt::For(s) => exprs_of(&s.body, out),
                Stmt::With(s) => exprs_of(&s.body, out),
                Stmt::Try(s) => {
                    exprs_of(&s.body, out);
                    exprs_of(&s.orelse, out);
                    exprs_of(&s.finalbody, out);
                }
                Stmt::Return(r) => out.extend(r.value.as_deref()),
                Stmt::Expr(e) => out.push(&e.value),
                Stmt::Assign(a) => out.push(&a.value),
                _ => {}
            }
        }
    }
    exprs_of(&module.body, &mut stack);
    while let Some(expr) = stack.pop() {
        match expr {
            Expr::Call(call) => {
                if matches!(call.func.as_ref(), Expr::Name(n) if n.id.as_str() == "isinstance") && call.args.len() == 2 {
                    resolve(&call.args[1]);
                }
                stack.extend(call.args.iter());
            }
            Expr::BoolOp(b) => stack.extend(b.values.iter()),
            Expr::UnaryOp(u) => stack.push(&u.operand),
            Expr::IfExp(i) => stack.extend([i.test.as_ref(), i.body.as_ref(), i.orelse.as_ref()]),
            _ => {}
        }
    }
    out.sort();
    out.dedup();
    out
}

/// 効果に答える節 1 つ(索引の effect の節と、その tap)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Clause {
    pub node: usize,
    pub rel: String,
    pub handler: String,
    pub effect: String,
    pub tap: bool,
}

impl Clause {
    /// 反例の表の鍵(`<path>::<handler>::<効果>`)。
    pub fn key(&self) -> String {
        format!("{}::{}::{}", self.rel, self.handler, self.effect)
    }
}

/// 判定の材料(図から求めた集合)と、判定の結果。
pub struct Inputs<'a> {
    pub clauses: &'a [Clause],
    /// 模擬の根から届く節か(添字は Clause の順)。
    pub simulated: &'a [bool],
    /// 本番の入口から届く節か。
    pub produced: &'a [bool],
    /// 検の file の定義から届く節か。
    pub tested: &'a [bool],
    /// 節の効果が intent の層の効果か。
    pub intent_effect: &'a [bool],
    /// 節が翻訳の層の file に在るか。
    pub translation_file: &'a [bool],
    pub external: &'a BTreeMap<String, String>,
    pub counterexamples: &'a BTreeMap<String, String>,
    pub unserved: &'a BTreeMap<String, String>,
    /// 本番の code の Python の handler が答える効果(`isinstance(effect, X)` — 索引の図の外なので届く先を問わず本番の答え手に数える)。
    pub python_answered: &'a std::collections::BTreeSet<String>,
}

/// 判定 1 件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// 業務の効果の偽物(Clause の添字)。
    Fake(usize),
    /// 下の層の効果の第 2 の偽物(DOEFF143)。
    LowerLayerFake(usize),
    /// 検だけから届く偽物(DOEFF157)。
    TestOnlyFake(usize),
    /// 翻訳の層の外の handler が intent の効果に答える(DOEFF158)。
    IntentAnsweredOutside(usize),
    /// 同じ intent の効果に翻訳の handler が 2 つ以上答える(DOEFF158 — 節の添字と、答える handler の数)。
    IntentAnsweredTwice(usize, usize),
    /// 反例の表の行がもう当たらない。
    StaleCounterexample(String),
    /// 外の世界の表の行にどの偽物も答えない。
    UnusedExternal(String),
    /// 外の世界の表の行に本番の答え手が無い。
    UnservedExternal(String),
    /// DOEFF206: 違反を通す表(外の世界の表・下の層を通す表・検だけの偽物の表)の行が intent の層の効果を通す(添字は `PassRow` の順)。
    IntentPassedByTable(usize),
    /// DOEFF206: 検証環境の dir の中の handler の節が intent の層の効果に、出し直しの答えをそのまま渡す tap でなく答えを作る(Clause の添字)。
    IntentAnsweredInVerification(usize),
}

/// 違反を通す表の行 1 つ(DOEFF206 の材料)。鍵は外の世界の表なら効果の完全名、ほかは `<path>::<handler>::<効果>`。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PassRow {
    /// 表の dir(宣言の綴り)。
    pub table: String,
    pub key: String,
    /// 行の file(root からの綴り)。
    pub origin: String,
}

impl PassRow {
    /// 行が通す効果(鍵の最後の `::` の後 — `::` の無い鍵はそのまま効果の完全名)。
    pub fn effect(&self) -> &str {
        self.key.rsplit_once("::").map(|(_, effect)| effect).unwrap_or(&self.key)
    }
}

/// DOEFF206 の判定の材料。
pub struct IntentFakeInputs<'a> {
    pub clauses: &'a [Clause],
    /// 節の効果が intent の層の効果か(Clause の順)。
    pub intent_effect: &'a [bool],
    /// 節が検証環境の dir の中の file に在るか。
    pub in_verification: &'a [bool],
    /// 節が狭い tap(同じ効果を出し直し、その答えをそのまま resume する)か。
    pub pass_through: &'a [bool],
    pub counterexamples: &'a BTreeMap<String, String>,
    pub rows: &'a [PassRow],
    /// 行の効果が intent の層の効果か(PassRow の順)。
    pub row_intent: &'a [bool],
}

/// DOEFF206: intent の層の効果は、模擬でも本番の翻訳の handler が答える — 検証環境が自前で答える節と、それを通す表の行を出す。
/// 外すのは狭い tap と、反例の表に鍵(`<path>::<handler>::<効果>`)の在る節だけ(登録簿では下げない — 規則の側で決める)。
pub fn judge_intent_fakes(inputs: &IntentFakeInputs) -> Vec<Verdict> {
    let IntentFakeInputs { clauses, intent_effect, in_verification, pass_through, counterexamples, rows, row_intent } = inputs;
    let rows = rows.iter().enumerate().filter(|(i, _)| row_intent[*i]).map(|(i, _)| Verdict::IntentPassedByTable(i));
    let answers = clauses
        .iter()
        .enumerate()
        .filter(|(i, clause)| in_verification[*i] && intent_effect[*i] && !pass_through[*i] && !counterexamples.contains_key(&clause.key()))
        .map(|(i, _)| Verdict::IntentAnsweredInVerification(i));
    rows.chain(answers).collect()
}

pub fn judge(inputs: &Inputs, decl: &BusinessFakes) -> Vec<Verdict> {
    let Inputs { clauses, simulated, produced, tested, intent_effect, translation_file, external, counterexamples, unserved, python_answered } = inputs;
    let fake = |i: usize| simulated[i] && !produced[i];
    let test_only = |i: usize| tested[i] && !simulated[i] && !produced[i];
    let mut out = Vec::new();
    let mut hit_keys = std::collections::BTreeSet::new();
    let mut answered = std::collections::BTreeSet::new();
    let mut served: std::collections::BTreeSet<&str> = python_answered.iter().map(String::as_str).collect();
    // intent の効果 → それに答える翻訳の handler(`<path>::<handler>`)と、その最初の節。
    let mut translators: BTreeMap<&str, BTreeMap<(&str, &str), usize>> = BTreeMap::new();
    for (i, clause) in clauses.iter().enumerate() {
        if clause.tap {
            continue;
        }
        // 本番から届かない節の鍵が反例の表に在れば、どの効果に答える節でも表の当たりに数える — 土台の効果(記録・時計・外の相手)に
        // 答える壊した handler も反例の表に載せられる(agora-redesign #1560 の定義 3)。
        if !produced[i] && counterexamples.contains_key(&clause.key()) {
            hit_keys.insert(clause.key());
        }
        if produced[i] {
            served.insert(clause.effect.as_str());
            if intent_effect[i] {
                if translation_file[i] {
                    translators.entry(clause.effect.as_str()).or_default().entry((clause.rel.as_str(), clause.handler.as_str())).or_insert(i);
                } else {
                    out.push(Verdict::IntentAnsweredOutside(i));
                }
            }
        }
        let outside = !external.contains_key(&clause.effect);
        let business = outside && business_effect(&clause.effect, decl);
        let lower = lower_layer_module(module_of_effect(&clause.effect), decl);
        if test_only(i) {
            // わざと壊した反例の handler(下の層の置き場の代役を含む)は反例の表で数え、それ以外は検だけの偽物。
            if business || (outside && lower) {
                let key = clause.key();
                if counterexamples.contains_key(&key) {
                    hit_keys.insert(key);
                } else {
                    out.push(Verdict::TestOnlyFake(i));
                }
            }
            continue;
        }
        if !fake(i) {
            continue;
        }
        answered.insert(clause.effect.as_str());
        if lower {
            out.push(Verdict::LowerLayerFake(i));
        }
        if !business {
            continue;
        }
        let key = clause.key();
        hit_keys.insert(key.clone());
        if !counterexamples.contains_key(&key) {
            out.push(Verdict::Fake(i));
        }
    }
    for handlers in translators.values().filter(|h| h.len() > 1) {
        out.extend(handlers.values().map(|&i| Verdict::IntentAnsweredTwice(i, handlers.len())));
    }
    for key in counterexamples.keys() {
        if !hit_keys.contains(key) {
            out.push(Verdict::StaleCounterexample(key.clone()));
        }
    }
    for effect in external.keys() {
        if !answered.contains(effect.as_str()) {
            out.push(Verdict::UnusedExternal(effect.clone()));
        }
        if !served.contains(effect.as_str()) && !unserved.contains_key(effect) {
            out.push(Verdict::UnservedExternal(effect.clone()));
        }
    }
    out
}

/// 反例の表の節 1 つ(DOEFF164 の材料): 節の効果の定義元の service(土台の効果なら None)と、節に届く deftest(図の節の添字)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CounterexampleCase {
    pub owner: Option<String>,
    pub tests: std::collections::BTreeSet<usize>,
}

/// entry の層を持つ service 1 つ(DOEFF164 の材料): 名と、その entry の層の定義に届く deftest(図の節の添字)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceCase {
    pub name: String,
    pub entry_tests: std::collections::BTreeSet<usize>,
}

/// 反例の無い service 1 つ(DOEFF164 の判定)。candidates = その service の効果か土台の効果に答える反例の節の数。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissingCounterexample {
    pub service: usize,
    pub candidates: usize,
}

/// DOEFF164: service ごとに、反例の節のうち効果の持ち主がその service か土台の物(候補)を選び、候補に届く deftest の 1 本でも
/// その service の entry に届けば「反例が有る」。1 本も無い service を返す(候補が 0 の service も返す)。
pub fn services_without_counterexample(cases: &[CounterexampleCase], services: &[ServiceCase]) -> Vec<MissingCounterexample> {
    services
        .iter()
        .enumerate()
        .filter_map(|(index, service)| {
            let candidates: Vec<&CounterexampleCase> =
                cases.iter().filter(|case| case.owner.as_deref().is_none_or(|owner| owner == service.name)).collect();
            let covered = candidates.iter().any(|case| !case.tests.is_disjoint(&service.entry_tests));
            (!covered).then_some(MissingCounterexample { service: index, candidates: candidates.len() })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decl() -> BusinessFakes {
        BusinessFakes {
            simulation: vec!["app/sim/**".into()],
            assembly: vec!["app/*/entry/**".into()],
            tests: vec!["**/tests/**".into()],
            skip: vec!["**/adr/**".into()],
            production: vec![],
            sets: vec!["handler_sets.hy".into()],
            simulation_prefix: Some("emulated".into()),
            production_prefix: Some("production".into()),
            entry_string_modules: vec!["app".into()],
            entry_string_files: vec!["deploy/**".into()],
            business_modules: vec!["app.orders".into(), "app.intent*".into()],
            lower_layer_modules: vec!["lib.records.effects".into()],
            external_effects: None,
            counterexamples: None,
            unserved: None,
            lower_layer_passages: None,
            test_only_fakes: None,
        }
    }

    fn clause(i: usize, effect: &str, tap: bool) -> Clause {
        Clause { node: i, rel: "app/sim/fake.hy".into(), handler: "fake".into(), effect: effect.into(), tap }
    }

    #[test]
    fn roles_follow_the_declaration() {
        let d = decl();
        assert_eq!(role_of("app/sim/x.hy", &d), FileRole::Simulation);
        assert_eq!(role_of("app/orders/entry/x.hy", &d), FileRole::Assembly);
        assert_eq!(role_of("app/orders/tests/x.hy", &d), FileRole::Test);
        assert_eq!(role_of("app/orders/adr/x.hy", &d), FileRole::Skipped);
        assert_eq!(role_of("app/orders/core/x.hy", &d), FileRole::Production);
        assert!(set_member("app/orders/handler_sets.hy", "emulated-orders", d.simulation_prefix.as_deref(), &d));
        assert!(!set_member("app/orders/core.hy", "emulated-orders", d.simulation_prefix.as_deref(), &d));
        assert!(business_module("app.orders.intent.rows", &d));
        assert!(business_module("app.intent_rows", &d));
        assert!(!business_module("app.ordersx", &d));
        assert!(business_effect("app.orders.intent.rows.ReadRow", &d));
        assert!(!business_effect("app.orders.tests.world.ReadWorld", &d)); // 検が自分で定義した効果
        assert!(!business_effect("app.sim.script.Step", &d)); // 模擬の筋書きの効果(業務の module でもない)
    }

    #[test]
    fn taps_and_entries_are_read() {
        let source = r#"
(defhandler fake
  (ReadRow [key] (resume k (ReadRow key)))
  (WriteRow [row] (resume k None))
  (Tick [n] (<- seen effect) (resume k seen))
  (Log [line] (yield effect))
  (Drop [n] (<- other effect2) (yield effect n)))
(defhandler fake2
  (Stop [] (resume k None)))
(when (= __name__ "__main__")
  (main))
"#;
        let taps = taps_in(source);
        assert_eq!(taps.get(&("fake".to_string(), "ReadRow".to_string())), Some(&true));
        assert_eq!(taps.get(&("fake".to_string(), "WriteRow".to_string())), Some(&false));
        assert_eq!(taps.get(&("fake".to_string(), "Tick".to_string())), Some(&true)); // (<- 答え effect)
        assert_eq!(taps.get(&("fake".to_string(), "Log".to_string())), Some(&true)); // (yield effect)
        assert_eq!(taps.get(&("fake".to_string(), "Drop".to_string())), Some(&false)); // 別の値の出し直しは tap でない
        assert_eq!(main_guard_lines(source), vec![(9, 10)]);
        assert_eq!(entry_names("env = \"app.orders.envs:make-env\"", &decl()), vec!["app.orders.envs.make_env".to_string()]);
        let py = "from app.screen.effects import Log\nimport app.clock as c\ndef dispatch(effect, k):\n    if isinstance(effect, Log):\n        return 1\n    if isinstance(effect, (c.Now, str)):\n        return 2\n";
        assert_eq!(python_isinstance_effects(py, "app.screen.entry.values"), vec!["app.clock.Now".to_string(), "app.screen.effects.Log".to_string()]);
        let relative = "from ..effects import Log\ndef d(effect):\n    return isinstance(effect, Log)\n";
        assert_eq!(python_isinstance_effects(relative, "app.screen.entry.values"), vec!["app.screen.effects.Log".to_string()]);
    }

    /// DOEFF206 の狭い tap: 出し直した答えをそのまま resume する節だけ(DOEFF143 の taps_in より狭い)。
    #[test]
    fn pass_through_taps_resume_the_reissued_answer_unchanged() {
        let source = r#"
(defhandler h
  (ReadRow [key] (<- row (ReadRow key)) (resume row))
  (Tick [n] (<- seen int effect) (resume seen))
  (Log [line] (setv answer (yield effect)) (resume answer))
  (Bend [n] (<- seen effect) (resume (+ seen 1)))
  (Swap [n] (<- seen effect) (<- other (Swap 2)) (resume other))
  (Keyed [key n] (<- row (Keyed :key key :n n)) (resume row))
  (Aliased [n] (val request effect) (<- seen datetime (GetTime)) (<- answer request) (resume answer))
  (Split [n] (if n (resume 0) (do (<- seen effect) (resume seen))))
  (Drop [n] (yield effect))
  (Own [n] (resume n)))
"#;
        let taps = pass_through_taps_in(source);
        let tap = |head: &str| taps.get(&("h".to_string(), head.to_string())).copied();
        assert_eq!(tap("ReadRow"), Some(true));
        assert_eq!(tap("Tick"), Some(true));
        assert_eq!(tap("Log"), Some(true));
        assert_eq!(tap("Bend"), Some(false)); // 答えを変えて resume
        assert_eq!(tap("Swap"), Some(false)); // 引数を変えて出し直した答え(同じ効果の出し直しではない)
        assert_eq!(tap("Aliased"), Some(true)); // 受けた effect の別名の出し直し
        assert_eq!(tap("Keyed"), Some(true)); // :欄 の語つきでも、受けた引数をそのまま並べた出し直し
        assert_eq!(tap("Split"), Some(false)); // 枝の 1 つで自前の値
        assert_eq!(tap("Drop"), Some(false)); // resume が無い
        assert_eq!(tap("Own"), Some(false));
        // DOEFF143 の読み(taps_in)は字面の出し直しで tap に数えるまま。
        let wide = taps_in(source);
        assert_eq!(wide.get(&("h".to_string(), "Bend".to_string())), Some(&true));
    }

    #[test]
    fn fakes_of_business_effects_are_red_and_the_tables_do_not_rot() {
        let clauses = vec![
            clause(0, "app.orders.intent.WriteRow", false),       // 業務の効果の偽物 → 赤
            clause(1, "app.orders.intent.ReadRow", true),         // tap → 数えない
            clause(2, "app.clock.Now", false),                    // 業務でない
            clause(3, "app.orders.intent.Send", false),           // 外の世界の表に在る
            clause(4, "app.orders.intent.Broken", false),         // 反例の表に在る
            clause(5, "app.orders.intent.Shared", false),         // 本番からも届く → 偽物でない
            // 検の file のわざと壊した下の層の代役 → 反例の表の行に当たる(偽物ではない)
            clause(6, "lib.records.effects.ReadRow", false),
            clause(7, "lib.records.effects.WriteRow", false),     // 模擬の偽物が下の層に答える → DOEFF143
            clause(8, "app.orders.intent.Ship", false),           // 検だけから届く業務の偽物 → DOEFF157
            clause(9, "lib.records.effects.Scan", false),         // 検だけから届く下の層の偽物 → DOEFF157
            clause(10, "app.clock.Now", false),                   // 検だけから届くが業務でも下の層でもない
            clause(11, "app.orders.intent.Ship", true),           // 検だけから届く tap
        ];
        let n = clauses.len();
        let mut simulated = vec![true; n];
        let mut produced = vec![false; n];
        let mut tested = vec![false; n];
        simulated[8..].iter_mut().for_each(|s| *s = false);
        tested[8..].iter_mut().for_each(|t| *t = true);
        (simulated[6], tested[6]) = (false, true);
        tested[5] = true; // 本番からも届けば検だけの偽物でない
        produced[5] = true;
        let intent_effect = vec![false; n];
        let translation_file = vec![false; n];
        let external: BTreeMap<String, String> =
            [("app.orders.intent.Send".to_string(), "外の相手".to_string()), ("app.gone.Old".to_string(), "古い".to_string())].into();
        let counterexamples: BTreeMap<String, String> = [
            ("app/sim/fake.hy::fake::app.orders.intent.Broken".to_string(), "反例".to_string()),
            ("app/sim/fake.hy::fake::app.orders.intent.Gone".to_string(), "古い反例".to_string()),
            ("app/sim/fake.hy::fake::lib.records.effects.ReadRow".to_string(), "下の層の反例".to_string()),
        ]
        .into();
        let unserved: BTreeMap<String, String> = [("app.orders.intent.Send".to_string(), "#1 で書く".to_string())].into();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &intent_effect,
                translation_file: &translation_file,
                external: &external,
                counterexamples: &counterexamples,
                unserved: &unserved,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(
            verdicts,
            vec![
                Verdict::Fake(0),
                Verdict::LowerLayerFake(7),
                Verdict::TestOnlyFake(8),
                Verdict::TestOnlyFake(9),
                Verdict::StaleCounterexample("app/sim/fake.hy::fake::app.orders.intent.Gone".into()),
                Verdict::UnusedExternal("app.gone.Old".into()),
                Verdict::UnservedExternal("app.gone.Old".into()),
            ]
        );
    }

    #[test]
    fn intent_effects_are_answered_by_one_translation_handler() {
        let at = |i: usize, rel: &str, handler: &str, tap: bool| Clause {
            node: i,
            rel: rel.into(),
            handler: handler.into(),
            effect: "app.orders.intent.Place".into(),
            tap,
        };
        let clauses = vec![
            at(0, "app/orders/protocol/a.hy", "translate", false), // 翻訳の handler 1 つ目
            at(1, "app/orders/protocol/b.hy", "translate2", false), // 同じ効果に 2 つ目 → 2 つとも出す
            at(2, "app/orders/entry/f.hy", "foundation", false),   // 翻訳の層の外 → 出す
            at(3, "app/orders/entry/f.hy", "observe", true),       // tap は数えない
            at(4, "app/sim/fake.hy", "fake", false),               // 本番から届かない → DOEFF158 の外
            at(5, "app/orders/protocol/a.hy", "translate", false), // 同じ handler の 2 つ目の節は 1 つに数える
        ];
        let n = clauses.len();
        let produced = vec![true, true, true, true, false, true];
        let simulated = vec![false, false, false, false, true, false];
        let tested = vec![false; n];
        let intent_effect = vec![true; n];
        let translation_file = vec![true, true, false, false, false, true];
        let external = [("app.orders.intent.Place".to_string(), "外".to_string())].into();
        let empty = BTreeMap::new();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &intent_effect,
                translation_file: &translation_file,
                external: &external,
                counterexamples: &empty,
                unserved: &empty,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(verdicts, vec![Verdict::IntentAnsweredOutside(2), Verdict::IntentAnsweredTwice(0, 2), Verdict::IntentAnsweredTwice(1, 2)]);
    }

    /// agora-redesign #1560 の定義 3: 土台の効果(業務でも下の層でもない)に答える壊した handler も反例の表に載せられる。
    /// 本番から届く節は反例ではないので、表に在っても当たらない(腐り)。
    #[test]
    fn counterexamples_of_foundation_effects_are_hits() {
        let clauses = vec![
            clause(0, "app.clock.Now", false),   // 模擬の土台の壊した handler(turn_counterexamples の型)
            clause(1, "app.clock.Tick", false),  // 検だけから届く土台の壊した handler
            clause(2, "app.clock.Sleep", false), // 本番からも届く → 反例でない
        ];
        let simulated = vec![true, false, true];
        let tested = vec![false, true, false];
        let produced = vec![false, false, true];
        let flags = vec![false; clauses.len()];
        let key = |effect: &str| format!("app/sim/fake.hy::fake::{}", effect);
        let counterexamples: BTreeMap<String, String> =
            ["app.clock.Now", "app.clock.Tick", "app.clock.Sleep"].iter().map(|e| (key(e), "土台の反例".to_string())).collect();
        let empty = BTreeMap::new();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &flags,
                translation_file: &flags,
                external: &empty,
                counterexamples: &counterexamples,
                unserved: &empty,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(verdicts, vec![Verdict::StaleCounterexample(key("app.clock.Sleep"))]);
    }

    /// DOEFF164: 候補(効果の持ち主がその service か土台)に届く deftest の 1 本でも entry に届けば有り。
    #[test]
    fn services_without_counterexample_are_found() {
        let tests = |nodes: &[usize]| nodes.iter().copied().collect::<std::collections::BTreeSet<usize>>();
        let case = |owner: Option<&str>, nodes: &[usize]| CounterexampleCase { owner: owner.map(str::to_string), tests: tests(nodes) };
        let service = |name: &str, nodes: &[usize]| ServiceCase { name: name.into(), entry_tests: tests(nodes) };
        let cases = vec![
            case(Some("orders"), &[2]), // orders の反例 — orders の entry に届く
            case(Some("orders"), &[3]), // orders の効果の反例が billing の entry に届いても billing の反例に数えない
            case(None, &[5]),           // 土台の効果の反例 — 届いた service(stock)の反例に数える
            case(Some("billing"), &[9]), // billing の効果の反例だが、その検は billing の entry に届かない
        ];
        let services = vec![service("orders", &[1, 2]), service("billing", &[3]), service("empty", &[]), service("stock", &[5])];
        assert_eq!(
            services_without_counterexample(&cases, &services),
            vec![MissingCounterexample { service: 1, candidates: 2 }, MissingCounterexample { service: 2, candidates: 1 }]
        );
    }
}

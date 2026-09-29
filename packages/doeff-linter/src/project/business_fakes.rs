//! DOEFF143: 業務の効果に答える偽の handler を作らない(agora-redesign #1375 / #1367 / #1189 — 元は agora-controllers の一時の検
//! business_fakes_rules.hy の判定 A と D・#682・#715・#780)。
//!
//! 偽物 = 模擬の根から届き、本番の入口から届かない定義の中の effect の節。その節が業務の効果(定義元の module が
//! `:business-modules` に当たる)に tap でなく答え、外の世界の効果の表(`:external-effects`)にも反例の表(`:counterexamples`)にも
//! 無ければ critical。表の腐りも出す: 反例の表の行がもう当たらない・外の世界の表の行にどの偽物も答えない・外の世界の表の行に本番の
//! 入口から届く答え手が無い(`:unserved` に理由つきで載せた物を除く)。
//!
//! 届く先は DOEFF133・136 と同じ定義の辺の図(呼び出し・参照・入れ子)を根から前向きに辿る。全体の実行だけ(repo 全体の図が要る)。
//! 模擬の根・本番の入口・業務の module・表の置き場は repo の宣言 `:business-fakes` から読み、ここには repo の名前を置かない。
//!
//! tap = 節の本体が同じ効果を出し直す節(節の頭の名の呼び — 観測・障害の注入)。出し直した上で答えを変える節も tap に見える(読みの限界)。

use std::collections::{BTreeMap, HashMap};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use regex::Regex;

use super::architecture::BusinessFakes;
use super::glob_matches;
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

/// form の中に `(name …)` の呼びが在るか。
fn calls(source: &str, form: &Form, name: &str) -> bool {
    match &form.node {
        Node::Seq { delim, items } => {
            (*delim == Delim::Paren && head_symbol(source, items) == Some(name)) || items.iter().any(|i| calls(source, i, name))
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => calls(source, inner, name),
        Node::Annotated { target: Some(target), .. } => calls(source, target, name),
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
                    let tap = parts[2..].iter().any(|p| calls(source, p, head));
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
    /// 検の file の節か(模擬の根から届けば偽物・届かなければ反例の表の照らしにだけ使う)。
    pub test: bool,
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
    /// 反例の表の行がもう当たらない。
    StaleCounterexample(String),
    /// 外の世界の表の行にどの偽物も答えない。
    UnusedExternal(String),
    /// 外の世界の表の行に本番の答え手が無い。
    UnservedExternal(String),
}

pub fn judge(inputs: &Inputs, decl: &BusinessFakes) -> Vec<Verdict> {
    let Inputs { clauses, simulated, produced, external, counterexamples, unserved, python_answered } = inputs;
    let fake = |i: usize| simulated[i] && !produced[i];
    let mut out = Vec::new();
    let mut hit_keys = std::collections::BTreeSet::new();
    let mut answered = std::collections::BTreeSet::new();
    let mut served: std::collections::BTreeSet<&str> = python_answered.iter().map(String::as_str).collect();
    for (i, clause) in clauses.iter().enumerate() {
        if clause.tap {
            continue;
        }
        if produced[i] {
            served.insert(clause.effect.as_str());
        }
        let business = !external.contains_key(&clause.effect) && business_module(module_of_effect(&clause.effect), decl);
        if !fake(i) {
            // 検だけから届く節(C8b-3 の持ち分)も、わざと壊した反例の表の照らしには数える — 業務の効果か下の層の効果に答える節
            // (下の層の置き場のわざと壊した代役も反例 — 元の検の test-answers と同じ)。
            let lower = !external.contains_key(&clause.effect) && lower_layer_module(module_of_effect(&clause.effect), decl);
            if clause.test && (business || lower) {
                hit_keys.insert(clause.key());
            }
            continue;
        }
        answered.insert(clause.effect.as_str());
        if !business {
            continue;
        }
        let key = clause.key();
        hit_keys.insert(key.clone());
        if !counterexamples.contains_key(&key) {
            out.push(Verdict::Fake(i));
        }
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
        }
    }

    fn clause(i: usize, effect: &str, tap: bool) -> Clause {
        Clause { node: i, rel: "app/sim/fake.hy".into(), handler: "fake".into(), effect: effect.into(), tap, test: false }
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
    }

    #[test]
    fn taps_and_entries_are_read() {
        let source = r#"
(defhandler fake
  (ReadRow [key] (resume k (ReadRow key)))
  (WriteRow [row] (resume k None)))
(when (= __name__ "__main__")
  (main))
"#;
        let taps = taps_in(source);
        assert_eq!(taps.get(&("fake".to_string(), "ReadRow".to_string())), Some(&true));
        assert_eq!(taps.get(&("fake".to_string(), "WriteRow".to_string())), Some(&false));
        assert_eq!(main_guard_lines(source), vec![(4, 5)]);
        assert_eq!(entry_names("env = \"app.orders.envs:make-env\"", &decl()), vec!["app.orders.envs.make_env".to_string()]);
        let py = "from app.screen.effects import Log\nimport app.clock as c\ndef dispatch(effect, k):\n    if isinstance(effect, Log):\n        return 1\n    if isinstance(effect, (c.Now, str)):\n        return 2\n";
        assert_eq!(python_isinstance_effects(py, "app.screen.entry.values"), vec!["app.clock.Now".to_string(), "app.screen.effects.Log".to_string()]);
        let relative = "from ..effects import Log\ndef d(effect):\n    return isinstance(effect, Log)\n";
        assert_eq!(python_isinstance_effects(relative, "app.screen.entry.values"), vec!["app.screen.effects.Log".to_string()]);
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
            Clause { test: true, ..clause(6, "lib.records.effects.ReadRow", false) },
        ];
        let simulated = vec![true, true, true, true, true, true, false];
        let produced = vec![false, false, false, false, false, true, false];
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
            &Inputs { clauses: &clauses, simulated: &simulated, produced: &produced, external: &external, counterexamples: &counterexamples, unserved: &unserved, python_answered: &python_answered },
            &decl(),
        );
        assert_eq!(
            verdicts,
            vec![
                Verdict::Fake(0),
                Verdict::StaleCounterexample("app/sim/fake.hy::fake::app.orders.intent.Gone".into()),
                Verdict::UnusedExternal("app.gone.Old".into()),
                Verdict::UnservedExternal("app.gone.Old".into()),
            ]
        );
    }
}

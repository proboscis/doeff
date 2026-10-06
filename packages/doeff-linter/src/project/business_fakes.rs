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
//! 答える。検証環境の dir の節が intent の効果に自前で答える(どれかの終わりが自前の値を返す — 片方の枝だけでも)所と、違反を通す表(外の世界の表・
//! 下の層を通す表・検だけの偽物の表)の行が intent の効果を通す所を出す。登録簿で下げない(利用者 2026-10-04 "so this kind of violation,
//! must be detected by doeff linter")。
//!
//! 届く先は DOEFF133・136 と同じ定義の辺の図(呼び出し・参照・入れ子)を根から前向きに辿る。全体の実行だけ(repo 全体の図が要る)。
//! 模擬の根・本番の入口・業務の module・表の置き場は repo の宣言 `:business-fakes` から読み、ここには repo の名前を置かない。
//!
//! 節の判じは `clause_shapes_in` の 1 か所だけ(agora-redesign #3834 — それまで DOEFF143 の広い読みと DOEFF206 の狭い読みが同じ節を
//! 別に判じていた)。節の形 `ClauseShape` は 2 つの欄を持つ: forwards = どれかの終わりが受けた効果をそのまま外へ渡す・answers = どれかの
//! 終わりが自前の値を返す。この file の「tap」は forwards の在る節(観測・障害の注入 — 片方の枝でだけ出し直す節も含む)を指し、
//! 偽物の判じ(DOEFF143・155・156・157・158)はそれを外す。反例の表の当たり(DOEFF143・157 の表の照らし・164・167)と DOEFF206 は
//! answers の在る節に表の行を求める。

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

fn read_forms(source: &str) -> Vec<Form> {
    Reader::new(source, 0, source.len()).read_all()
}

/// 効果の節 1 つの形 — DOEFF143・155・156・157・158・164・167・206 が共に使う、ただ 1 つの判じ(agora-redesign #3834)。
/// 節の終わり(`resume`・`transfer`・`finish`・`reperform`・`pass`・`:when` の条件が偽の時の自動の reperform)を全部集め、2 つの欄に分ける。
/// 1 つの節が両方を持つ事がある(片方の枝でだけ出し直す障害の注入・観測)。扱いは規則の側で決める: 表の行を要るかは `answers` で、
/// 障害の注入・観測として偽物の判じから外すかは `forwards` で見る。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClauseShape {
    /// どれかの終わりが受けた効果をそのまま外へ渡す — 出し直し(`(<- 名 [型] 出し直し)`・`(setv 名 (yield 出し直し))`)で束ねた名
    /// (`(:= 名 束ねた名)` などで入れ直した名も)をそのまま `resume` / `transfer` する・同じ効果を出し直した節の `(resume None)`(答えを
    /// 捨てる観測)・`(reperform effect の名か別名)`・`(pass)`・`:when` の節。出し直しには、別の handler に答えさせる
    /// `(with_handlers [...] 出し直し)` も数える。`(resume (if 条件 A B))` は枝ごとの終わりに分ける。
    pub forwards: bool,
    /// どれかの終わりが自前の値を返す — 束ねた名でない値の `resume` / `transfer`・`finish`・別の効果の `reperform`・別の handler の下の
    /// 出し直しの答え(答えを作る handler を節が選ぶ)。forwards の終わりが 1 つも無い節も自前で答える節に数える。`raise` は終わりに数えない
    /// (捕まえた例外を投げ直す観測と、自前の故障を区別できない)。
    pub answers: bool,
}

impl ClauseShape {
    /// 読めなかった節(file が読めない・節の頭が索引と合わない)の形 — 自前で答える節に数える(外す側に倒さない)。
    pub const ANSWERING: ClauseShape = ClauseShape { forwards: false, answers: true };
}

/// 節の本体(節の頭の後ろ・引数の並びの後ろの form の並び)の形。
fn clause_shape(source: &str, params: &[&str], body: &[Form], head: &str) -> ClauseShape {
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
    // 受けた効果は名 `effect` で束ねられる。節の欄に `effect` の名が在れば(`(RecordAs [principal effect] …)` など)、その名は欄の値で、
    // 受けた効果ではない。
    let mut effect_names: Vec<&str> = if params.contains(&"effect") { Vec::new() } else { vec!["effect"] };
    while body.iter().fold(false, |grew, form| aliases(source, form, &mut effect_names) || grew) {}
    /// 出し直しの式か — effect の名か別名・節が受けた引数をそのまま並べた `(頭 …)` の呼び(`:欄` の語は数えない)・`(yield 出し直し)`・
    /// 別の handler の下での出し直し `(with_handlers [...] 出し直し)`(綴り `with-handlers`・`with-handler` も同じ)。
    /// 出し直しなら Some(別の handler の下か)。
    fn reissue(source: &str, form: &Form, head: &str, params: &[&str], effect_names: &[&str]) -> Option<bool> {
        match &form.node {
            Node::Symbol => effect_names.contains(&text(source, form)).then_some(false),
            Node::Seq { delim: Delim::Paren, items } => match head_symbol(source, items) {
                Some(h) if h == head => {
                    let args: Vec<&str> = items[1..].iter().map(|f| text(source, f)).filter(|t| !t.starts_with(':')).collect();
                    (args == params).then_some(false)
                }
                Some("yield") if items.len() == 2 => reissue(source, &items[1], head, params, effect_names),
                Some("with_handlers" | "with-handlers" | "with_handler" | "with-handler") if items.len() == 3 => {
                    reissue(source, &items[2], head, params, effect_names).map(|_| true)
                }
                _ => None,
            },
            _ => None,
        }
    }
    /// 節の終わり 1 つ。
    enum Ending<'s> {
        /// 受けた効果をそのまま外へ渡す(`(reperform effect)`・`(pass)`)。
        Forward,
        /// 自前の値。
        Own,
        /// 記号の値の `resume` / `transfer`(出し直しで束ねた名なら渡す・そうでなければ自前 — 束ねの全部を集めた後で決める)。
        Value(&'s str),
        /// `(resume None)`(同じ効果を出し直した節なら、答えを捨てて None で返す観測 — 渡す終わりに数える)。
        Nothing,
    }
    /// 本体を歩いて集める物。
    #[derive(Default)]
    struct Walked<'s> {
        /// 出し直しの答えを束ねた名と、別の handler の下の出し直しか。
        bound: Vec<(&'s str, bool)>,
        /// 名から名への入れ直し `(:= 名 名)`・`(setv 名 名)`・`(val 名 名)`。
        assigned: Vec<(&'s str, &'s str)>,
        /// 本体のどこかで同じ効果を出し直したか(名に束ねない `(<- 出し直し)` も含む)。
        reissued: bool,
        endings: Vec<Ending<'s>>,
    }
    /// `resume` / `transfer` の値の終わり — `(if 条件 A B)` は枝ごとの終わりに分ける。
    fn value_endings<'s>(source: &'s str, form: &Form, out: &mut Vec<Ending<'s>>) {
        match &form.node {
            Node::Symbol if text(source, form) == "None" => out.push(Ending::Nothing),
            Node::Symbol => out.push(Ending::Value(text(source, form))),
            Node::Seq { delim: Delim::Paren, items } if head_symbol(source, items) == Some("if") && items.len() == 4 => {
                value_endings(source, &items[2], out);
                value_endings(source, &items[3], out);
            }
            _ => out.push(Ending::Own),
        }
    }
    fn walk<'s>(source: &'s str, form: &Form, reissue: &dyn Fn(&Form) -> Option<bool>, walked: &mut Walked<'s>) {
        let Node::Seq { delim, items } = &form.node else { return };
        if *delim == Delim::Paren {
            // `:=` は読みの上で記号でなく keyword の形なので、頭の綴りで見る。
            let word = head_symbol(source, items).or_else(|| items.first().map(|f| text(source, f)).filter(|t| *t == ":="));
            match (word, items.as_slice()) {
                // (<- 名 [型] 出し直し)・(<- 出し直し)
                (Some("<-"), [_, .., last]) => {
                    if let Some(chosen) = reissue(last) {
                        walked.reissued = true;
                        if items.len() >= 3 && matches!(items[1].node, Node::Symbol) {
                            walked.bound.push((text(source, &items[1]), chosen));
                        }
                    }
                }
                (Some(":=" | "setv" | "val"), [_, name, value]) if matches!(name.node, Node::Symbol) => {
                    if matches!(value.node, Node::Symbol) {
                        walked.assigned.push((text(source, name), text(source, value)));
                    }
                    // (setv 名 (yield 出し直し))
                    if let Some(inner) = value.paren_items() {
                        if head_symbol(source, inner) == Some("yield") {
                            if let Some(chosen) = reissue(value) {
                                walked.reissued = true;
                                walked.bound.push((text(source, name), chosen));
                            }
                        }
                    }
                }
                (Some("resume" | "transfer"), [_, value]) => {
                    value_endings(source, value, &mut walked.endings);
                    return;
                }
                (Some("resume" | "transfer" | "finish"), _) => {
                    walked.endings.push(Ending::Own);
                    return;
                }
                (Some("reperform"), _) => {
                    walked.endings.push(if items.len() == 2 && reissue(&items[1]).is_some() { Ending::Forward } else { Ending::Own });
                    return;
                }
                (Some("pass"), [_]) => {
                    walked.endings.push(Ending::Forward);
                    return;
                }
                _ => {}
            }
        }
        items.iter().for_each(|item| walk(source, item, reissue, walked));
    }
    let reissue = |form: &Form| reissue(source, form, head, params, &effect_names);
    let mut walked = Walked::default();
    body.iter().for_each(|form| walk(source, form, &reissue, &mut walked));
    // 束ねた名を入れ直した名も束ねた名に数える(`(:= answer closed)` など)。
    while let Some(grown) = walked.assigned.iter().find_map(|&(name, value)| {
        let known = |n: &str| walked.bound.iter().find(|(bound, _)| *bound == n).map(|&(_, chosen)| chosen);
        known(name).is_none().then(|| known(value).map(|chosen| (name, chosen))).flatten()
    }) {
        walked.bound.push(grown);
    }
    let Walked { bound, reissued, endings, .. } = walked;
    // 終わりごとに (渡すか, 自前で答えるか)。別の handler の下の出し直しの答えは、受けた効果を外へ渡しつつ、答えを作る handler を節が選ぶので両方。
    let judged = |ending: &Ending| match ending {
        Ending::Forward => (true, false),
        Ending::Own => (false, true),
        Ending::Nothing => (reissued, !reissued),
        Ending::Value(name) => match bound.iter().find(|(bound, _)| bound == name) {
            Some(&(_, chosen)) => (true, chosen),
            None => (false, true),
        },
    };
    // `:when 条件` の節は、条件が偽の時に doeff-hy の handle が自動で reperform する(doeff-hy handle.hy の Clause guards)。
    let guarded = body.first().is_some_and(|form| text(source, form) == ":when");
    let forwards = guarded || endings.iter().any(|ending| judged(ending).0);
    let answers = !forwards || endings.iter().any(|ending| judged(ending).1);
    ClauseShape { forwards, answers }
}

/// file の defhandler の節ごとの形(鍵 = (handler の名, 節の頭の名))。同じ handler の同じ頭の節が 2 つ在れば、どちらの欄も OR で合わせる
/// (どちらかが渡せば forwards・どちらかが答えれば answers)。
pub fn clause_shapes_in(source: &str) -> HashMap<(String, String), ClauseShape> {
    fn visit(source: &str, forms: &[Form], out: &mut HashMap<(String, String), ClauseShape>) {
        for form in forms {
            let Node::Seq { delim, items } = &form.node else { continue };
            if *delim == Delim::Paren && head_symbol(source, items) == Some("defhandler") && items.len() >= 2 {
                let handler = text(source, &items[1]).to_string();
                for clause in &items[2..] {
                    let Some(parts) = clause.paren_items() else { continue };
                    let Some(head) = head_symbol(source, parts) else { continue };
                    let Some(params) = parts.get(1).and_then(Form::bracket_items) else { continue };
                    let params: Vec<&str> = params.iter().map(|p| text(source, p)).collect();
                    let shape = clause_shape(source, &params, &parts[2..], head);
                    let merged = out.entry((handler.clone(), head.to_string())).or_insert(ClauseShape { forwards: false, answers: false });
                    merged.forwards |= shape.forwards;
                    merged.answers |= shape.answers;
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

/// 効果に答える節 1 つ(索引の effect の節と、その形)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Clause {
    pub node: usize,
    pub rel: String,
    pub handler: String,
    pub effect: String,
    /// 節の形(`clause_shapes_in` で 1 度だけ求める — 規則の全部が同じ判じを読む)。
    pub shape: ClauseShape,
}

impl Clause {
    /// 反例の表の鍵(`<path>::<handler>::<効果>`)。
    pub fn key(&self) -> String {
        format!("{}::{}::{}", self.rel, self.handler, self.effect)
    }

    /// 反例の表の当たりに数える節か — 本番の入口から届かず、自前で答える終わりを持ち(片方の枝だけでも)、鍵が表に在る。
    /// DOEFF143・157 の表の照らしと DOEFF164・167 の反例の節が同じ条件を使う(agora-redesign #3834)。
    pub fn counterexample_in(&self, produced: bool, counterexamples: &BTreeMap<String, String>) -> bool {
        self.shape.answers && !produced && counterexamples.contains_key(&self.key())
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
    /// DOEFF206: 検証環境の dir の中の handler の節が intent の層の効果に自前の値を返す終わりを持ち、反例の表に行が無い(Clause の添字)。
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
    pub counterexamples: &'a BTreeMap<String, String>,
    pub rows: &'a [PassRow],
    /// 行の効果が intent の層の効果か(PassRow の順)。
    pub row_intent: &'a [bool],
}

/// DOEFF206: intent の層の効果は、模擬でも本番の翻訳の handler が答える — 検証環境が自前で答える節と、それを通す表の行を出す。
/// 自前で答える終わりを 1 つでも持つ節(`ClauseShape::answers` — 一部の枝でだけ出し直す節も含む)は、反例の表に鍵
/// (`<path>::<handler>::<効果>`)が在る時だけ外す(登録簿では下げない — 規則の側で決める)。
pub fn judge_intent_fakes(inputs: &IntentFakeInputs) -> Vec<Verdict> {
    let IntentFakeInputs { clauses, intent_effect, in_verification, counterexamples, rows, row_intent } = inputs;
    let rows = rows.iter().enumerate().filter(|(i, _)| row_intent[*i]).map(|(i, _)| Verdict::IntentPassedByTable(i));
    let answers = clauses
        .iter()
        .enumerate()
        .filter(|(i, clause)| in_verification[*i] && intent_effect[*i] && clause.shape.answers && !counterexamples.contains_key(&clause.key()))
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
        // 本番から届かない節が自前で答える終わりを持ち、その鍵が反例の表に在れば、どの効果に答える節でも表の当たりに数える — 土台の効果
        // (記録・時計・外の相手)に答える壊した handler も反例の表に載せられる(agora-redesign #1560 の定義 3)。片方の枝でだけ出し直す
        // 節も当たりに数える(下の偽物の判じからは外れるが、表の行は要る — DOEFF206・164・167 と同じ読み・agora-redesign #3834)。
        if clause.counterexample_in(produced[i], counterexamples) {
            hit_keys.insert(clause.key());
        }
        // 受けた効果をそのまま外へ渡す終わりを持つ節は、障害の注入・観測として偽物の判じから外す。
        if clause.shape.forwards {
            continue;
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

    /// 判じの検で使う節の形 — forwards の節は受けた効果をそのまま渡すだけ(答えない)・そうでない節は自前で答えるだけ。
    fn shape(forwards: bool) -> ClauseShape {
        ClauseShape { forwards, answers: !forwards }
    }

    fn clause(i: usize, effect: &str, forwards: bool) -> Clause {
        Clause { node: i, rel: "app/sim/fake.hy".into(), handler: "fake".into(), effect: effect.into(), shape: shape(forwards) }
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
    fn entries_are_read() {
        let source = r#"
(defhandler fake
  (ReadRow [key] (<- row (ReadRow key)) (resume row))
  (WriteRow [row] (resume None))
  (Tick [n] (<- seen effect) (resume seen))
  (Log [line] (reperform effect))
  (Drop [n] (<- other effect2) (resume other)))
(defhandler fake2
  (Stop [] (resume None)))
(when (= __name__ "__main__")
  (main))
"#;
        assert_eq!(main_guard_lines(source), vec![(9, 10)]);
        assert_eq!(entry_names("env = \"app.orders.envs:make-env\"", &decl()), vec!["app.orders.envs.make_env".to_string()]);
        let py = "from app.screen.effects import Log\nimport app.clock as c\ndef dispatch(effect, k):\n    if isinstance(effect, Log):\n        return 1\n    if isinstance(effect, (c.Now, str)):\n        return 2\n";
        assert_eq!(python_isinstance_effects(py, "app.screen.entry.values"), vec!["app.clock.Now".to_string(), "app.screen.effects.Log".to_string()]);
        let relative = "from ..effects import Log\ndef d(effect):\n    return isinstance(effect, Log)\n";
        assert_eq!(python_isinstance_effects(relative, "app.screen.entry.values"), vec!["app.screen.effects.Log".to_string()]);
    }

    /// 節の形の判じ(agora-redesign #3834): 終わりごとに「受けた効果をそのまま渡す」か「自前の値」かを分け、節の 2 つの欄にする。
    #[test]
    fn clause_shapes_split_forwarding_and_answering_endings() {
        let source = r#"
(defhandler h
  (ReadRow [key] (<- row (ReadRow key)) (resume row))
  (Tick [n] (<- seen int effect) (resume seen))
  (Log [line] (setv answer (yield effect)) (resume answer))
  (Bend [n] (<- seen effect) (resume (+ seen 1)))
  (Swap [n] (<- seen effect) (<- other (Swap 2)) (resume other))
  (Keyed [key n] (<- row (Keyed :key key :n n)) (resume row))
  (Aliased [n] (val request effect) (<- seen datetime (GetTime)) (<- answer request) (resume answer))
  (Again [n] (reperform effect))
  (Renamed [n] (setv asked effect) (reperform asked))
  (Other [n] (reperform (Other 2)))
  (Old [n] (pass))
  (Guarded [n] :when (> n 0) (<- seen effect) (resume seen))
  (Elsewhere [key] (<- found (handler-for key)) (<- answer object (with_handlers [found] (Elsewhere key))) (resume answer))
  (Handed [key] (<- answer (with-handlers [found] effect)) (transfer answer))
  (Ended [n] (finish n))
  (Raised [n] (raise (ValueError n)))
  (Seen [n] (<- _seen effect) (<- (Note n)) (resume None))
  (Told [n] (<- (Told n)) (resume None))
  (Silent [n] (resume None))
  (Kept [n] (var answer None) (try (<- got effect) (:= answer got) (except [e Exception] (raise e))) (resume answer))
  (Wrapped [principal effect] (<- answer (with_handlers [found] effect)) (resume answer))
  (Trimmed [n] (<- answer effect) (resume (if n (trim answer) answer)))
  (Twice [n] (resume 0))
  (Twice [n] (reperform effect))
  (Own [n] (resume n)))
"#;
        let shapes = clause_shapes_in(source);
        let at = |head: &str| shapes.get(&("h".to_string(), head.to_string())).map(|s| (s.forwards, s.answers));
        let passes = Some((true, false));
        let answers = Some((false, true));
        let both = Some((true, true));
        assert_eq!(at("ReadRow"), passes);
        assert_eq!(at("Tick"), passes);
        assert_eq!(at("Log"), passes);
        assert_eq!(at("Bend"), answers); // 答えを変えて resume
        assert_eq!(at("Swap"), answers); // 引数を変えて出し直した答え(同じ効果の出し直しではない)
        assert_eq!(at("Keyed"), passes); // :欄 の語つきでも、受けた引数をそのまま並べた出し直し
        assert_eq!(at("Aliased"), passes); // 受けた effect の別名の出し直し
        assert_eq!(at("Again"), passes); // (reperform effect)
        assert_eq!(at("Renamed"), passes); // 別名の reperform
        assert_eq!(at("Other"), answers); // 別の値の reperform は自前の答え
        assert_eq!(at("Old"), passes); // (pass)
        assert_eq!(at("Guarded"), passes); // :when の偽の時の自動の reperform と、出し直しの答えの resume
        assert_eq!(at("Elsewhere"), both); // 別の handler に答えさせる — 効果は外へ渡るが、答えを作る handler は節が選ぶ
        assert_eq!(at("Handed"), both); // 別の handler の下の出し直しの答えを transfer
        assert_eq!(at("Ended"), answers); // finish
        assert_eq!(at("Raised"), answers); // raise は終わりに数えず、渡す終わりも無い
        assert_eq!(at("Seen"), passes); // 出し直して答えを捨てる観測の (resume None)
        assert_eq!(at("Told"), passes); // 名に束ねない出し直しの後の (resume None)
        assert_eq!(at("Silent"), answers); // 出し直さない (resume None) は自前の答え
        assert_eq!(at("Kept"), passes); // 出し直しの答えを (:= 名 束ねた名) で入れ直して resume
        assert_eq!(at("Wrapped"), answers); // 欄の名 effect は受けた効果ではない
        assert_eq!(at("Trimmed"), both); // (resume (if 条件 A B)) の枝の 1 つが自前の値
        assert_eq!(at("Twice"), both); // 同じ頭の 2 つの節は欄ごとに OR
        assert_eq!(at("Own"), answers);
    }

    /// 片方の枝でだけ出し直す壊した handler(agora-redesign #3834 — kn-w37 の 7 件目の書き直す前の形)は、書き方が 3 つ在っても
    /// 同じ形 {forwards・answers} に判じ、DOEFF143・164・206 が同じ答えを出す: 反例の表に行が有れば どれも当たらず、無ければ 206 だけが当たる。
    #[test]
    fn one_branch_forwarding_is_judged_alike_by_every_rule() {
        let source = r#"
(defhandler a (Place [n] (if n (resume 0) (do (<- got effect) (resume got)))))
(defhandler b (Place [n] :when n (resume 0)))
(defhandler c (Place [n] (if n (resume 0) (reperform effect))))
"#;
        let shapes = clause_shapes_in(source);
        for handler in ["a", "b", "c"] {
            let shape = shapes[&(handler.to_string(), "Place".to_string())];
            assert_eq!(shape, ClauseShape { forwards: true, answers: true }, "{} の形", handler);
            let at = Clause { node: 0, rel: "app/sim/fake.hy".into(), handler: handler.into(), effect: "app.orders.intent.Place".into(), shape };
            let row: BTreeMap<String, String> = [(at.key(), "反例".to_string())].into();
            let clauses = vec![at];
            let (yes, no, empty) = (vec![true], vec![false], BTreeMap::new());
            let python_answered = std::collections::BTreeSet::new();
            for (table, expected_206) in [(&row, vec![]), (&empty, vec![Verdict::IntentAnsweredInVerification(0)])] {
                let verdicts = judge(
                    &Inputs {
                        clauses: &clauses,
                        simulated: &yes,
                        produced: &no,
                        tested: &no,
                        intent_effect: &yes,
                        translation_file: &no,
                        external: &empty,
                        counterexamples: table,
                        unserved: &empty,
                        python_answered: &python_answered,
                    },
                    &decl(),
                );
                assert_eq!(verdicts, vec![], "DOEFF143 が {} を当てた(表の行 {} 件)", handler, table.len());
                let intent = judge_intent_fakes(&IntentFakeInputs {
                    clauses: &clauses,
                    intent_effect: &yes,
                    in_verification: &yes,
                    counterexamples: table,
                    rows: &[],
                    row_intent: &[],
                });
                assert_eq!(intent, expected_206, "DOEFF206 の {} の判じ(表の行 {} 件)", handler, table.len());
                // DOEFF164・167 の反例の節の条件(mod.rs が同じ Clause::counterexample_in を読む)。
                let counted = clauses.iter().filter(|c| c.counterexample_in(false, table)).count();
                assert_eq!(counted, table.len(), "DOEFF164 の反例の節の数 {}", handler);
            }
        }
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
        let at = |i: usize, rel: &str, handler: &str, forwards: bool| Clause {
            node: i,
            rel: rel.into(),
            handler: handler.into(),
            effect: "app.orders.intent.Place".into(),
            shape: shape(forwards),
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

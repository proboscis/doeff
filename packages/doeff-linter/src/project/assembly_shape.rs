//! DOEFF155・156: 組み立ての形(agora-redesign #1376 / #1367 / #1189 — 元は agora-controllers の一時の検
//! controllers/agora_sim/tests/business_fakes_rules.hy の assembly-breaches の 5 法・ADR R8・R9・#780・#827・#832・#893・#1340)。
//!
//! 翻訳の列の 1 点は組み立ての層の defk(名の型 `:translation-point` — 例 `with-*-translation`)で、本体の
//! `(with-handlers [#* 列 …] 本体)` が翻訳の層(`:translation-layer`)の翻訳の列の定数(`:translations`)を並べる。土台の handler は
//! 別の関数が本体を包む字面に置き、翻訳の列に混ぜない。
//!
//! DOEFF155(形):
//! * 組の file(`:business-fakes` の `:sets` に当たる本番の code の file)が残る
//! * 組み立ての層に退役した組み立ての関数(`:retired-function`)が残る
//! * 翻訳の列の 1 点が `#*` で翻訳の列でない import した名を並べる
//! * 列の並び: 列が出し直す別の service の intent の効果(`:intent-layer`)に答える列が、それより外側(前)に無い
//! * 翻訳の列の定数が別の service の翻訳の層の定義を並べる
//!
//! DOEFF156(答えの置き場):
//! * 翻訳の handler が業務の効果を出し直す — intent の層の効果は常に、それ以外は同じ列の handler が答えない物(自分が答える効果は除く)・
//!   出し直した効果に答える同じ列の handler がどれも内側(列の後ろ)に在る
//! * 組の file の組の関数が並べる土台の handler(翻訳の列の外)が業務の効果に答える
//!
//! 業務の効果・外の世界の表・組み立ての層・組の file は `:business-fakes`(DOEFF143)の宣言から、名前は `:assembly-shape` から読み、
//! ここには repo の名前を置かない。届く先は DOEFF143 と同じ定義の辺の図を最上位の定義へ畳んで辿る。全体の実行だけ。
//! 読みの限界: 翻訳の先は定義の本体が名指す業務の効果の名で判じる(呼ばない参照も出し直しに数える)。

use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};
use doeff_indexer::hy_index::{HyFileIndex, Range};

use super::architecture::{Architecture, AssemblyShape, BusinessFakes};
use super::business_fakes::{self, FileRole};
use super::names::{absolute_module, hy_mangle, module_of};
use super::paths::glob_matches;
use super::spans::innermost_definition;

/// 判定の材料のうち、全体の定義の図から組む物(組むのは呼び手 — project/mod.rs の judge_assembly_shape)。図の型と組み立てを
/// mod.rs に置いたまま、この module が mod.rs を読み戻さない形で受け取るため(読み戻すと依存の輪になる — agora-redesign #2121)。
pub struct GraphInputs<'h> {
    /// 図の file の並び(定義の節の順)。
    pub rels: Vec<&'h String>,
    /// file → その file の最初の定義の節の番号。
    pub base: HashMap<&'h str, usize>,
    /// 定義の節の数。
    pub nodes: usize,
    /// callees[n] = 節 n から届く節(辺の順向き)。
    pub callees: Vec<Vec<usize>>,
    /// effect の条(業務の偽物の宣言から組んだ物)。
    pub clauses: Vec<business_fakes::Clause>,
    /// file → (層の名・service の名)。層の置き場の外の file は載らない。
    pub layers: HashMap<String, (String, Option<String>)>,
}

/// 最上位の定義 1 つ(判定の材料)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Top {
    pub rel: String,
    pub qualified: String,
    /// 定義の名(完全名の最後の段 — mangle した綴り)。
    pub name: String,
    /// 本体が名指す名の完全名。
    pub refs: BTreeSet<String>,
    /// tap でなく答える業務の効果の節(handler, 効果)— 外の世界の表の効果を除く。
    pub answers: Vec<(String, String)>,
    /// 本番の code か。
    pub production: bool,
    /// file が当たる層の名と、その置き場の service の dir(層が先の置き場は None)。
    pub layer: Option<(String, Option<String>)>,
    /// root の次の段が宣言した service の dir ならその dir(層の置き場の外の旧い置き場も含む)。
    pub service_dir: Option<String>,
    /// 組の file の組の関数か(本番か模擬の頭の語で始まる)。
    pub set_function: bool,
}

/// 判定の材料の全体。
#[derive(Debug, Clone, Default)]
pub struct Model {
    pub tops: Vec<Top>,
    pub by_name: HashMap<String, usize>,
    /// edges[t] = t の本体から届く最上位の定義。
    pub edges: Vec<Vec<usize>>,
    /// 組の file(:sets に当たる本番の code の file)。
    pub set_files: BTreeSet<String>,
    /// 共有の置き場の dir(その下の intent は別の service の物に数えない)。
    pub shared: Option<String>,
}

/// 翻訳の列の 1 点 1 つの読み。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationPoint {
    pub rel: String,
    /// 1 点の defk の完全名。
    pub function: String,
    /// 並べた翻訳の列の完全名(並びの順・先頭が外側)。
    pub sequence: Vec<String>,
    /// `#*` で並べたのに翻訳の列でない import した名(書いた綴り)。
    pub strays: Vec<String>,
}

/// 破れの種類(閉じた型 — 規則と鍵の細目と文はここから決まる)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Law {
    /// 組の file が残る。
    SetFile,
    /// 退役した組み立ての関数が組み立ての層に残る。
    Retired,
    /// 翻訳の列の 1 点が翻訳の列でない名を並べる。
    Stray { name: String },
    /// 列 list が出し直す別の service の intent の効果 effect に答える列が外側に無い。
    Order { list: String, effect: String },
    /// 翻訳の列が別の service の翻訳 reference を並べる。
    OwnTranslations { reference: String, owner: String },
    /// 翻訳の handler が業務の効果を出し直す。
    GenericTarget { handler: String, effect: String },
    /// 出し直した効果に答える同じ列の handler がどれも内側に在る。
    InnerAnswerer { handler: String, effect: String, answerers: Vec<String> },
    /// 組の関数が並べる土台の handler が業務の効果に答える。
    FoundationAnswer { function: String, handler: String, effect: String },
}

/// 破れ 1 件(rel = 当たりの file・at = 位置を取る定義の完全名)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Breach {
    pub rel: String,
    pub at: Option<String>,
    pub law: Law,
}

impl Breach {
    /// DOEFF155(形)か DOEFF156(答えの置き場)か。
    pub fn is_shape(&self) -> bool {
        !matches!(self.law, Law::GenericTarget { .. } | Law::InnerAnswerer { .. } | Law::FoundationAnswer { .. })
    }

    /// 登録簿の鍵の細目。
    pub fn detail(&self) -> String {
        match &self.law {
            Law::SetFile => "set-file".to_string(),
            Law::Retired => "retired".to_string(),
            Law::Stray { name } => format!("stray:{}", name),
            Law::Order { list, effect } => format!("order:{}:{}", list, effect),
            Law::OwnTranslations { reference, .. } => format!("own:{}", reference),
            Law::GenericTarget { handler, effect } => format!("target:{}:{}", handler, effect),
            Law::InnerAnswerer { handler, effect, .. } => format!("inner:{}:{}", handler, effect),
            Law::FoundationAnswer { function, handler, effect } => format!("foundation:{}:{}:{}", function, handler, effect),
        }
    }

    /// 知らせの主体(これは何か)と理由(なぜ違反か)。
    pub fn explain(&self, shape: &AssemblyShape) -> (String, String) {
        match &self.law {
            Law::SetFile => (
                format!("組の file {}", self.rel),
                format!(
                    "組み立てが組み立ての層の翻訳の列の 1 点 {}(翻訳の層の {} を並べる)と、本体を包む土台へ移っていない — 本番と模擬の違いは本体を包む土台だけ",
                    shape.translation_point, shape.translations
                ),
            ),
            Law::Retired => (
                format!("{} の {}", self.rel, shape.retired_function.as_deref().unwrap_or("")),
                format!("退役した組み立ての関数が残る — 翻訳の列は defk {} [… 本体] に、土台は本体を包む字面に置く", shape.translation_point),
            ),
            Law::Stray { name } => (
                format!("{} の翻訳の列の 1 点が並べる {}", self.rel, name),
                format!("翻訳の列でない — #* で並べるのは翻訳の層から import した {}(か置き場を持たない service の持ち主の列)だけ。土台の handler は本体を包む字面に置く", shape.translations),
            ),
            Law::Order { list, effect } => (
                format!("{} の翻訳の列の 1 点が並べる列 {}", self.rel, list),
                format!("別の service の intent の効果 {} を出し直すが、それに答える列がそれより外側(前)に無い — 効果は土台まで抜けて答え手が無い", effect),
            ),
            Law::OwnTranslations { reference, owner } => (
                format!("{} の {}", self.rel, shape.translations),
                format!("別の service {} の翻訳 {} を並べる — 翻訳の列は自分の service の翻訳だけ。別の service の列は組み立ての層が並べる", owner, reference),
            ),
            Law::GenericTarget { handler, effect } => (
                format!("{} の翻訳の handler {}", self.rel, handler),
                format!(
                    "業務の効果 {} を出し直す — 翻訳の先は業務を知らない汎用の効果だけ。intent の効果を出す業務の流れは core の program に置く(intent でない旧い置き場の効果は同じ列の handler が答えるなら出し直してよい)",
                    effect
                ),
            ),
            Law::InnerAnswerer { handler, effect, answerers } => (
                format!("{} の翻訳の handler {}", self.rel, handler),
                format!("出し直す {} に答える同じ列の handler {} がどれも内側(列の後ろ)に在る — 出し直した効果に答えられるのは外側の handler だけ", effect, answerers.join("・")),
            ),
            Law::FoundationAnswer { function, handler, effect } => (
                format!("{} の {} の土台の handler {}", self.rel, function, handler),
                format!("業務の効果 {} に答える — 土台の handler は外の世界の効果にだけ答える。業務の効果の答えは {} の翻訳の handler に置く", effect, shape.translations),
            ),
        }
    }
}

/// roots から届く最上位の定義(roots を含む)。
fn reach(model: &Model, roots: &[usize]) -> BTreeSet<usize> {
    let mut seen: BTreeSet<usize> = roots.iter().copied().collect();
    let mut queue: VecDeque<usize> = roots.iter().copied().collect();
    while let Some(top) = queue.pop_front() {
        for &next in &model.edges[top] {
            if seen.insert(next) {
                queue.push_back(next);
            }
        }
    }
    seen
}

/// 翻訳の層の置き場の service(層が先の置き場・他の層は None)。
fn translation_owner<'m>(top: &'m Top, shape: &AssemblyShape) -> Option<&'m str> {
    match &top.layer {
        Some((layer, Some(service))) if *layer == shape.translation_layer => Some(service.as_str()),
        _ => None,
    }
}

/// 最上位の定義が、層の置き場の外(宣言した service の dir の下で、どの層にも当たらない旧い置き場)の本番の code か。
fn unplaced(top: &Top) -> bool {
    top.production && top.layer.is_none() && top.service_dir.is_some()
}

/// 効果 effect を intent の層に定めたか(service を問わない)。
fn intent_effect(model: &Model, shape: &AssemblyShape, effect: &str) -> bool {
    model.by_name.get(effect).is_some_and(|&at| matches!(&model.tops[at].layer, Some((layer, _)) if *layer == shape.intent_layer))
}

/// 効果 effect が owner と別の service の intent の効果(共有の置き場・層が先の置き場を除く)か。
fn other_service_intent(model: &Model, shape: &AssemblyShape, effect: &str, owner: &str) -> bool {
    let Some(&at) = model.by_name.get(effect) else { return false };
    match &model.tops[at].layer {
        Some((layer, Some(service))) => *layer == shape.intent_layer && service != owner && model.shared.as_deref() != Some(service.as_str()),
        _ => false,
    }
}

/// 翻訳の列の 1 点が並べてよい翻訳の列か: 翻訳の層の module の翻訳の列の定数か、置き場を持たない service の持ち主の module の列で、
/// 要素が全部、業務の効果に答える置き場の外の handler の物(過渡の条 — 移しが済めば消える・#827)。
pub fn translation_list(model: &Model, shape: &AssemblyShape, target: &str) -> bool {
    let Some(&at) = model.by_name.get(target) else { return false };
    let top = &model.tops[at];
    if top.name == hy_mangle(&shape.translations) && matches!(&top.layer, Some((layer, _)) if *layer == shape.translation_layer) {
        return true;
    }
    let elements: Vec<&String> = top.refs.iter().filter(|r| *r != target).collect();
    unplaced(top)
        && !elements.is_empty()
        && elements.iter().all(|r| model.by_name.get(r.as_str()).is_some_and(|&e| !model.tops[e].answers.is_empty() && unplaced(&model.tops[e])))
}

/// 判じる。retired = 組み立ての層に残る退役した関数の (file, 完全名)・points = 翻訳の列の 1 点の読み・
/// orders = 翻訳の列の定数の完全名 → 要素の名 → 位置。
pub fn judge(
    model: &Model,
    shape: &AssemblyShape,
    retired: &[(String, String)],
    points: &[TranslationPoint],
    orders: &HashMap<String, HashMap<String, usize>>,
) -> Vec<Breach> {
    let mut out = Vec::new();
    let business_answered: BTreeSet<&str> = model.tops.iter().flat_map(|t| t.answers.iter().map(|(_, e)| e.as_str())).collect();
    let answers_of = |tops: &BTreeSet<usize>| -> BTreeSet<&str> { tops.iter().flat_map(|&t| model.tops[t].answers.iter().map(|(_, e)| e.as_str())).collect() };
    // 組の file は、それだけで破れ(移し終えれば file ごと消える)。
    for rel in &model.set_files {
        out.push(Breach { rel: rel.clone(), at: None, law: Law::SetFile });
    }
    for (rel, function) in retired {
        out.push(Breach { rel: rel.clone(), at: Some(function.clone()), law: Law::Retired });
    }
    // 翻訳の列の 1 点: 翻訳の列でない名と、列どうしの並び(同じ鍵は 2 度出さない)。
    let mut emitted: BTreeSet<(String, String)> = BTreeSet::new();
    for point in points {
        for name in &point.strays {
            let breach = Breach { rel: point.rel.clone(), at: Some(point.function.clone()), law: Law::Stray { name: name.clone() } };
            if emitted.insert((breach.rel.clone(), breach.detail())) {
                out.push(breach);
            }
        }
        let mut outside: BTreeSet<&str> = BTreeSet::new();
        for list in &point.sequence {
            let Some(&at) = model.by_name.get(list) else { continue };
            let reached = reach(model, &[at]);
            let own = answers_of(&reached);
            if let Some(owner) = model.tops[at].service_dir.as_deref() {
                let reyields: BTreeSet<&str> = reached
                    .iter()
                    .filter(|&&t| !model.tops[t].answers.is_empty())
                    .flat_map(|&t| model.tops[t].refs.iter().map(String::as_str))
                    .filter(|r| business_answered.contains(r) && !own.contains(r) && other_service_intent(model, shape, r, owner))
                    .collect();
                for effect in reyields.difference(&outside) {
                    let breach = Breach {
                        rel: point.rel.clone(),
                        at: Some(point.function.clone()),
                        law: Law::Order { list: list.clone(), effect: effect.to_string() },
                    };
                    if emitted.insert((breach.rel.clone(), breach.detail())) {
                        out.push(breach);
                    }
                }
            }
            outside.extend(own);
        }
    }
    let constant = hy_mangle(&shape.translations);
    let mut constants: Vec<usize> = (0..model.tops.len()).filter(|&t| model.tops[t].production && model.tops[t].name == constant).collect();
    constants.sort_by(|a, b| model.tops[*a].qualified.cmp(&model.tops[*b].qualified));
    // 翻訳の列は自分の service の翻訳だけ。
    for &q in &constants {
        let Some(owner) = translation_owner(&model.tops[q], shape) else { continue };
        for reference in &model.tops[q].refs {
            let Some(&other) = model.by_name.get(reference) else { continue };
            if let Some(other_owner) = translation_owner(&model.tops[other], shape).filter(|o| *o != owner) {
                out.push(Breach {
                    rel: model.tops[q].rel.clone(),
                    at: Some(model.tops[q].qualified.clone()),
                    law: Law::OwnTranslations { reference: reference.clone(), owner: other_owner.to_string() },
                });
            }
        }
    }
    // 翻訳の handler の出し直しの先と並び。
    for &q in &constants {
        let list = &model.tops[q];
        let handlers: Vec<usize> = reach(model, &[q]).into_iter().filter(|&t| !model.tops[t].answers.is_empty()).collect();
        let translated: BTreeSet<&str> = handlers.iter().flat_map(|&t| model.tops[t].answers.iter().map(|(_, e)| e.as_str())).collect();
        let order = orders.get(&list.qualified);
        for &t in &handlers {
            let top = &model.tops[t];
            let own: BTreeSet<&str> = top.answers.iter().map(|(_, e)| e.as_str()).collect();
            let position = order.and_then(|o| o.get(&top.name)).copied();
            for reference in top.refs.iter().map(String::as_str).filter(|r| business_answered.contains(r) && !own.contains(r)) {
                if intent_effect(model, shape, reference) || !translated.contains(reference) {
                    out.push(Breach {
                        rel: list.rel.clone(),
                        at: Some(top.qualified.clone()),
                        law: Law::GenericTarget { handler: top.name.clone(), effect: reference.to_string() },
                    });
                }
                let (Some(position), Some(order)) = (position, order) else { continue };
                if !translated.contains(reference) {
                    continue;
                }
                let answerers: Vec<&Top> =
                    handlers.iter().map(|&h| &model.tops[h]).filter(|h| h.qualified != top.qualified && h.answers.iter().any(|(_, e)| e == reference)).collect();
                let positions: Vec<usize> = answerers.iter().filter_map(|h| order.get(&h.name).copied()).collect();
                if !positions.is_empty() && positions.len() == answerers.len() && positions.iter().all(|p| *p >= position) {
                    out.push(Breach {
                        rel: list.rel.clone(),
                        at: Some(top.qualified.clone()),
                        law: Law::InnerAnswerer {
                            handler: top.name.clone(),
                            effect: reference.to_string(),
                            answerers: answerers.iter().map(|h| h.name.clone()).collect(),
                        },
                    });
                }
            }
        }
    }
    // 組の file の組の関数が並べる土台の handler(翻訳の列の外)は業務の効果に答えない。
    for (t, function) in model.tops.iter().enumerate().filter(|(_, t)| t.production && t.set_function) {
        let module = function.qualified.rsplit_once('.').map(|(m, _)| m).unwrap_or("");
        let translation = model.by_name.get(&format!("{}.{}", module, constant)).map(|&c| reach(model, &[c])).unwrap_or_default();
        let foundation = reach(model, &model.edges[t]);
        let mut found: Vec<(&str, &str)> = foundation
            .difference(&translation)
            .flat_map(|&f| model.tops[f].answers.iter().map(|(h, e)| (h.as_str(), e.as_str())))
            .collect();
        found.sort();
        found.dedup();
        for (handler, effect) in found {
            out.push(Breach {
                rel: function.rel.clone(),
                at: Some(function.qualified.clone()),
                law: Law::FoundationAnswer { function: function.name.clone(), handler: handler.to_string(), effect: effect.to_string() },
            });
        }
    }
    out
}

fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    &source[form.span.start..form.span.end]
}

/// 型の注記を剥がした form(`#^ T 名` の名)。
fn bare(form: &Form) -> &Form {
    match &form.node {
        Node::Annotated { target: Some(target), .. } => bare(target),
        _ => form,
    }
}

fn symbol<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    matches!(bare(form).node, Node::Symbol).then(|| text(source, bare(form)))
}

/// dotted の綴りを段ごとに mangle する(`p.TRANSLATION-HANDLERS` → `p.TRANSLATION_HANDLERS`)。
fn mangle_dotted(spelled: &str) -> String {
    spelled.split('.').map(hy_mangle).collect::<Vec<_>>().join(".")
}

/// 名が名の型 pattern(`*` は 1 つ)に当たるか(mangle した綴りどうしで比べる)。
fn name_matches(pattern: &str, name: &str) -> bool {
    let Some((head, tail)) = pattern.split_once('*') else { return hy_mangle(pattern) == name };
    // 断片は名の途中なので、頭や尻の `-` を mangle の特別な綴りにせず `_` へ写す。
    let (head, tail) = (head.replace('-', "_"), tail.replace('-', "_"));
    name.len() > head.len() + tail.len() && name.starts_with(&head) && name.ends_with(&tail)
}

/// form の中で最初に現れる `(with-handlers [..] ..)` の handler の列の literal。
fn handler_list<'f>(source: &str, form: &'f Form) -> Option<&'f [Form]> {
    let Node::Seq { items, .. } = &form.node else { return None };
    if let (Some(head), Some(list)) = (form.paren_items().and_then(|i| i.first()), items.get(1)) {
        if matches!(symbol(source, head), Some("with-handlers" | "with_handlers")) {
            if let Some(listed) = list.bracket_items() {
                return Some(listed);
            }
        }
    }
    items.iter().find_map(|item| match &item.node {
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => handler_list(source, inner),
        _ => handler_list(source, item),
    })
}

/// file の翻訳の列の 1 点を読む。resolve = import した名(mangle した dotted の綴り)→ 名指す先の完全名、
/// accepts = 翻訳の列として受けるか。答え = (1 点の名, 並べた翻訳の列, 翻訳の列でない import した名) の列。
pub fn read_points(
    source: &str,
    shape: &AssemblyShape,
    resolve: &dyn Fn(&str) -> Option<String>,
    accepts: &dyn Fn(&str) -> bool,
) -> Vec<(String, Vec<String>, Vec<String>)> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    // 同じ module の直下で列を並べた val / setv(`#*` で開く)。
    let locals: HashMap<String, &[Form]> = forms
        .iter()
        .filter_map(|form| {
            let [head, name, value] = form.paren_items()? else { return None };
            matches!(symbol(source, head)?, "val" | "setv").then_some(())?;
            Some((hy_mangle(symbol(source, name)?), value.bracket_items()?))
        })
        .collect();
    let mut points = Vec::new();
    for form in &forms {
        let Some(items) = form.paren_items() else { continue };
        let (Some("defk"), Some(name)) = (items.first().and_then(|h| symbol(source, h)), items.get(1).and_then(|n| symbol(source, n))) else { continue };
        let name = hy_mangle(name);
        if !name_matches(&shape.translation_point, &name) {
            continue;
        }
        let Some(listed) = items[2..].iter().find_map(|item| handler_list(source, item)) else { continue };
        let (mut sequence, mut strays) = (Vec::new(), Vec::new());
        let mut pending: VecDeque<&Form> = listed.iter().collect();
        let mut opened: BTreeSet<String> = BTreeSet::new();
        while let Some(item) = pending.pop_front() {
            let Node::Prefixed { prefix: Prefix::Unpack, inner: Some(inner) } = &item.node else { continue };
            let Some(spelled) = symbol(source, inner).map(mangle_dotted) else { continue };
            match (resolve(&spelled), locals.get(&spelled)) {
                (Some(target), _) if accepts(&target) => sequence.push(target),
                (_, Some(local)) => {
                    if opened.insert(spelled.clone()) {
                        for element in local.iter().rev() {
                            pending.push_front(element);
                        }
                    }
                }
                (Some(_), None) => strays.push(spelled),
                (None, None) => {}
            }
        }
        points.push((name, sequence, strays));
    }
    points
}

/// 翻訳の列の定数の要素の並び(要素の名(記号か、handler を作る呼び出しの頭)→ 最初の位置)。literal でなければ None。
pub fn list_order(source: &str, constant: &str) -> Option<HashMap<String, usize>> {
    let wanted = hy_mangle(constant);
    Reader::new(source, 0, source.len()).read_all().iter().find_map(|form| {
        let [head, name, value] = form.paren_items()? else { return None };
        if !matches!(symbol(source, head)?, "val" | "setv") || hy_mangle(symbol(source, name)?) != wanted {
            return None;
        }
        let Node::Seq { delim: Delim::Bracket | Delim::Tuple, items } = &value.node else { return None };
        let mut order = HashMap::new();
        for (i, item) in items.iter().enumerate() {
            let name = symbol(source, item).or_else(|| item.paren_items().and_then(|p| p.first()).and_then(|h| symbol(source, h)));
            if let Some(name) = name {
                order.entry(hy_mangle(name)).or_insert(i);
            }
        }
        Some(order)
    })
}

/// file の import が束ねる名(mangle した綴り)→ 名指す先の完全名(`(import m [a :as b])` の b → m.a・`(import m :as p)` の p → m)。
fn import_names(file: &HyFileIndex) -> HashMap<String, String> {
    let mut names = HashMap::new();
    for import in file.imports.iter().filter(|i| !i.is_require) {
        let module = absolute_module(&file.module, &import.module);
        match (&import.name, &import.alias) {
            (Some(name), alias) => {
                names.insert(hy_mangle(alias.as_deref().unwrap_or(name)), format!("{}.{}", module, hy_mangle(name)));
            }
            (None, Some(alias)) => {
                names.insert(hy_mangle(alias), module.clone());
            }
            (None, None) => {
                names.insert(module.clone(), module.clone());
            }
        }
    }
    names
}

/// 索引と、呼び手が定義の図から組んだ材料 graph から判じる(全体の実行だけ)。読めない表の理由は 2 つ目に返す。
pub fn find(
    root: &Path,
    architecture: &Architecture,
    decl: &BusinessFakes,
    shape: &AssemblyShape,
    hy: &HashMap<String, HyFileIndex>,
    graph: GraphInputs,
) -> (Vec<Breach>, Vec<String>) {
    let mut problems = Vec::new();
    let external: BTreeMap<String, String> = match &decl.external_effects {
        Some(dir) => {
            let judged = super::registry::JudgedKeys::load(root, std::slice::from_ref(dir), super::registry::Absent::Unreadable);
            problems.extend(judged.problems);
            judged.reasons
        }
        None => BTreeMap::new(),
    };
    let GraphInputs { rels, base, nodes, callees, clauses, layers } = graph;
    let arch_root = super::layers::normalize_dir(&architecture.root);
    // 節ごとに最上位の定義(同じ file の、範囲がほかの定義に含まれない定義)へ畳む。
    let mut top_of: Vec<usize> = vec![0; nodes];
    let mut model = Model { shared: architecture.shared.clone(), ..Model::default() };
    for rel in &rels {
        let file = &hy[rel.as_str()];
        let first = base[rel.as_str()];
        let mut order: Vec<usize> = (0..file.definitions.len()).collect();
        order.sort_by(|a, b| {
            let (a, b) = (&file.definitions[*a].full_range, &file.definitions[*b].full_range);
            a.start.cmp(&b.start).then(b.end.cmp(&a.end))
        });
        let production = business_fakes::role_of(rel, decl) != FileRole::Skipped && business_fakes::production_code(rel, decl);
        let layer = layers.get(rel.as_str()).cloned();
        let service_dir = rel
            .strip_prefix(&format!("{}/", arch_root))
            .and_then(|rest| rest.split_once('/'))
            .map(|(dir, _)| dir)
            .filter(|dir| architecture.service_by_dir(dir).is_some())
            .map(str::to_string);
        // 始まりの順に並べたので、今の最上位の定義の範囲に入る定義はその中の定義。
        let mut current: Option<(usize, Range)> = None;
        for index in order {
            let d = &file.definitions[index];
            let enclosing = current.filter(|(_, range)| range.start <= d.full_range.start && d.full_range.end <= range.end).map(|(c, _)| c);
            let top = match enclosing {
                Some(c) => c,
                None => {
                    model.by_name.entry(d.qualified_name.clone()).or_insert(model.tops.len());
                    model.tops.push(Top {
                        rel: rel.to_string(),
                        qualified: d.qualified_name.clone(),
                        name: d.qualified_name.rsplit('.').next().unwrap_or("").to_string(),
                        production,
                        layer: layer.clone(),
                        service_dir: service_dir.clone(),
                        set_function: business_fakes::set_member(rel, &d.name, decl.production_prefix.as_deref(), decl)
                            || business_fakes::set_member(rel, &d.name, decl.simulation_prefix.as_deref(), decl),
                        ..Top::default()
                    });
                    current = Some((model.tops.len() - 1, d.full_range));
                    model.tops.len() - 1
                }
            };
            top_of[first + index] = top;
        }
        // 本体が名指す名(参照と呼び出し)を最上位の定義へ。
        for reference in &file.references {
            let Some(target) = reference.target.as_ref() else { continue };
            if let Some(owner) = innermost_definition(&file.definitions, &reference.range) {
                model.tops[top_of[first + owner]].refs.insert(target.clone());
            }
        }
        for call in &file.calls {
            let (Some(target), Some(owner)) = (call.target.as_ref(), call.caller) else { continue };
            model.tops[top_of[first + owner]].refs.insert(target.clone());
        }
        if production && decl.sets.iter().any(|s| glob_matches(s, rel)) {
            model.set_files.insert(rel.to_string());
        }
    }
    model.edges = vec![Vec::new(); model.tops.len()];
    for (node, next) in callees.iter().enumerate() {
        for &n in next {
            let (from, to) = (top_of[node], top_of[n]);
            if from != to && !model.edges[from].contains(&to) {
                model.edges[from].push(to);
            }
        }
    }
    for clause in clauses.iter().filter(|c| !c.tap && !external.contains_key(&c.effect)) {
        if business_fakes::business_module(business_fakes::module_of_effect(&clause.effect), decl) {
            model.tops[top_of[clause.node]].answers.push((clause.handler.clone(), clause.effect.clone()));
        }
    }
    // 組み立ての層の file(検を除く)の退役した関数と翻訳の列の 1 点。
    let mut retired = Vec::new();
    let mut points = Vec::new();
    let assembly: Vec<&String> = rels.iter().copied().filter(|r| business_fakes::role_of(r, decl) == FileRole::Assembly).collect();
    for rel in &assembly {
        if let Some(function) = &shape.retired_function {
            let qualified = format!("{}.{}", module_of(rel), hy_mangle(function));
            if model.by_name.contains_key(&qualified) {
                retired.push((rel.to_string(), qualified));
            }
        }
    }
    for rel in assembly.iter().filter(|r| r.ends_with(".hy")) {
        let file = &hy[rel.as_str()];
        if !file.definitions.iter().any(|d| d.container.is_none() && name_matches(&shape.translation_point, &d.mangled)) {
            continue;
        }
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let names = import_names(file);
        let resolve = |spelled: &str| -> Option<String> {
            names.get(spelled).cloned().or_else(|| {
                let (head, rest) = spelled.split_once('.')?;
                names.get(head).map(|module| format!("{}.{}", module, rest))
            })
        };
        let accepts = |target: &str| translation_list(&model, shape, target);
        for (name, sequence, strays) in read_points(&source, shape, &resolve, &accepts) {
            points.push(TranslationPoint { rel: rel.to_string(), function: format!("{}.{}", module_of(rel), name), sequence, strays });
        }
    }
    // 翻訳の列の定数の要素の並び。
    let constant = hy_mangle(&shape.translations);
    let mut orders = HashMap::new();
    for top in model.tops.iter().filter(|t| t.production && t.name == constant && t.rel.ends_with(".hy")) {
        let Ok(source) = std::fs::read_to_string(root.join(&top.rel)) else { continue };
        if let Some(order) = list_order(&source, &shape.translations) {
            orders.insert(top.qualified.clone(), order);
        }
    }
    (judge(&model, shape, &retired, &points, &orders), problems)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape() -> AssemblyShape {
        AssemblyShape {
            translation_point: "with-*-translation".into(),
            retired_function: Some("handlers-of".into()),
            translations: "TRANSLATION-HANDLERS".into(),
            translation_layer: "protocol".into(),
            intent_layer: "intent".into(),
        }
    }

    fn top(rel: &str, qualified: &str, layer: Option<(&str, &str)>, answers: &[&str], refs: &[&str]) -> Top {
        let name = qualified.rsplit('.').next().unwrap().to_string();
        Top {
            rel: rel.into(),
            qualified: qualified.into(),
            refs: refs.iter().map(|r| r.to_string()).collect(),
            answers: answers.iter().map(|e| (name.clone(), e.to_string())).collect(),
            name,
            production: true,
            layer: layer.map(|(l, s)| (l.to_string(), Some(s.to_string()))),
            service_dir: rel.split('/').nth(1).map(str::to_string),
            set_function: false,
        }
    }

    /// 翻訳の列 A(h1・h2)と B(bi)・別の service の intent の効果 I・旧い置き場の効果 E1〜E3・組の file の土台の handler。
    fn model() -> Model {
        let a = "app/a/protocol/translations.hy";
        let b = "app/b/protocol/translations.hy";
        let mut tops = vec![
            top(a, "app.a.protocol.translations.TRANSLATION_HANDLERS", Some(("protocol", "a")), &[], &[
                "app.a.protocol.translations.h1",
                "app.a.protocol.translations.h2",
                "app.b.protocol.translations.bi",
            ]),
            top(a, "app.a.protocol.translations.h1", Some(("protocol", "a")), &["app.old.E1"], &["app.old.E1", "app.b.intent.I", "app.old.E2", "app.old.E3"]),
            top(a, "app.a.protocol.translations.h2", Some(("protocol", "a")), &["app.old.E2"], &[]),
            top(b, "app.b.protocol.translations.TRANSLATION_HANDLERS", Some(("protocol", "b")), &[], &["app.b.protocol.translations.bi"]),
            top(b, "app.b.protocol.translations.bi", Some(("protocol", "b")), &["app.b.intent.I"], &[]),
            top("app/b/intent/effects.hy", "app.b.intent.I", Some(("intent", "b")), &[], &[]),
            top("app/c/core/other.hy", "app.c.core.other.other", Some(("core", "c")), &["app.old.E3"], &[]),
            top("app/a/handler_sets.hy", "app.a.handler_sets.production_handlers", None, &[], &[]),
            top("app/a/handler_sets.hy", "app.a.handler_sets.fh", None, &["app.old.E1"], &[]),
        ];
        tops[7].set_function = true;
        let by_name = tops.iter().enumerate().map(|(i, t)| (t.qualified.clone(), i)).collect();
        let mut edges = vec![Vec::new(); tops.len()];
        edges[0] = vec![1, 2];
        edges[3] = vec![4];
        edges[7] = vec![8];
        Model { tops, by_name, edges, set_files: ["app/a/handler_sets.hy".to_string()].into(), shared: Some("shared".into()) }
    }

    #[test]
    fn names_and_lists_are_read_from_the_source() {
        assert!(name_matches("with-*-translation", "with_land_notice_translation"));
        assert!(!name_matches("with-*-translation", "with_translation"));
        assert!(!name_matches("with-*-translation", "land_notice_translation"));
        let source = "(val LOCAL [#* ORDERS-LIST])\n\
                      (defk with-orders-translation [body]\n  \"doc\"\n  {:pre []}\n  (<- answer (with-handlers [#* p.TRANSLATION-HANDLERS #* LOCAL #* POSTING-HANDLERS (reader) #* unknown] body))\n  answer)\n\
                      (defk other [body] (with-handlers [#* POSTING-HANDLERS] body))\n";
        let resolve = |spelled: &str| match spelled {
            "p.TRANSLATION_HANDLERS" => Some("app.a.protocol.t.TRANSLATION_HANDLERS".to_string()),
            "ORDERS_LIST" => Some("app.orders.handlers.ORDERS_LIST".to_string()),
            "POSTING_HANDLERS" => Some("app.m.protocol.t.POSTING_HANDLERS".to_string()),
            _ => None,
        };
        let accepts = |target: &str| target.ends_with("TRANSLATION_HANDLERS") || target.ends_with("ORDERS_LIST");
        let points = read_points(source, &shape(), &resolve, &accepts);
        assert_eq!(
            points,
            vec![(
                "with_orders_translation".to_string(),
                vec!["app.a.protocol.t.TRANSLATION_HANDLERS".to_string(), "app.orders.handlers.ORDERS_LIST".to_string()],
                vec!["POSTING_HANDLERS".to_string()],
            )]
        );
        let order = list_order("(val TRANSLATION-HANDLERS [h-one (h-two x) h-one])", "TRANSLATION-HANDLERS").unwrap();
        assert_eq!((order["h_one"], order["h_two"]), (0, 1));
        assert!(list_order("(val TRANSLATION-HANDLERS (make))", "TRANSLATION-HANDLERS").is_none());
    }

    #[test]
    fn every_law_is_judged() {
        let model = model();
        let a = "app.a.protocol.translations.TRANSLATION_HANDLERS";
        let b = "app.b.protocol.translations.TRANSLATION_HANDLERS";
        let retired = vec![("app/a/entry/old.hy".to_string(), "app.a.entry.old.handlers_of".to_string())];
        let points = vec![
            // A を B より外に並べると、A が出し直す b の intent I に答える列が外側に無い。
            TranslationPoint { rel: "app/a/entry/x.hy".into(), function: "app.a.entry.x.with_a_translation".into(), sequence: vec![a.into(), b.into()], strays: vec!["POSTING_HANDLERS".into()] },
            // B を外に並べれば並びは合う。
            TranslationPoint { rel: "app/a/entry/y.hy".into(), function: "app.a.entry.y.with_a_translation".into(), sequence: vec![b.into(), a.into()], strays: vec![] },
        ];
        let orders: HashMap<String, HashMap<String, usize>> = [(a.to_string(), [("h1".to_string(), 0), ("h2".to_string(), 1)].into())].into();
        let mut found: Vec<(String, String, bool)> =
            judge(&model, &shape(), &retired, &points, &orders).iter().map(|b| (b.rel.clone(), b.detail(), b.is_shape())).collect();
        found.sort();
        let t = "app/a/protocol/translations.hy";
        let mut wanted: Vec<(String, String, bool)> = vec![
            ("app/a/handler_sets.hy".into(), "set-file".into(), true),
            ("app/a/handler_sets.hy".into(), "foundation:production_handlers:fh:app.old.E1".into(), false),
            ("app/a/entry/old.hy".into(), "retired".into(), true),
            ("app/a/entry/x.hy".into(), "stray:POSTING_HANDLERS".into(), true),
            ("app/a/entry/x.hy".into(), format!("order:{}:app.b.intent.I", a), true),
            (t.into(), "own:app.b.protocol.translations.bi".into(), true),
            // intent の効果は常に・同じ列が答えない E3 も・旧い置き場の E2 は同じ列の h2 が答えるので出し直してよいが h2 は内側。
            (t.into(), "target:h1:app.b.intent.I".into(), false),
            (t.into(), "target:h1:app.old.E3".into(), false),
            (t.into(), "inner:h1:app.old.E2".into(), false),
        ];
        wanted.sort();
        assert_eq!(found, wanted);
        let intent_list = model.by_name[b];
        assert!(translation_list(&model, &shape(), b) && model.tops[intent_list].layer.is_some());
    }
}

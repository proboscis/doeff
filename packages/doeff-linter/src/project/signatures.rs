//! defk / deff の見出し(signature)と、束縛の型 — editor-json の `signatures` と `bindings`(契約 版 2・agora-redesign #849)。
//!
//! エディタ(doeff-runner)は defk の型・effect・tags を見出しとして描き、束縛の名の前に型を描く。型の読み方はここ 1 か所に置き、
//! エディタは写しを持たない(読むだけの表示 — source は書き換えない。operator 2026-09-28 "I will never edit the source manually")。
//!
//! 読む物:
//! - 引数の型 = 契約の辞書の `:pre` の `(: 名 型)`、答えの型 = `:post` の `(: % 型)`、宣言した effect = `:effects [E …]`(#800)、
//!   tags = `:tags` の文字列の値。
//! - 推論した effect = 本体で撃った(`(<- …)` / `(! …)`)呼びの頭が defeffect ならその effect、defk ならその defk の推論を足す
//!   (repo の Hy の file 全部から集めた表の上の不動点)。handler で受けた effect は引かない(上から見積もった集合)。
//! - Absent の包み(答えが Maybe)= `:absent` を宣言した effect(か、答えが Maybe の defk)を、`:absent F` も `absent-as` も
//!   付けずに撃った時。`:absent F` は F の型の Raise に替わる。Raise = 撃った effect の `:failure` と、撃った defk の Raise
//!   (`on-raise` の program の中は引く)。
//! - 束縛の型 = `(<- x T …)` の注釈 / 撃った effect の値の答え(`:answer` から `:absent` と `:failure` を除いた物)/ 撃った defk の
//!   答え / 字面(文字列・数・真偽・None・辞書・列)/ 型の名の呼び(record を作る)/ deff の答え。`(:= x v)` は同じ定義の中の
//!   `(var x …)` の型。**分からない物は型を null にし、別の型で埋めない**。
//!
//! 型の名は file の import と定義の場所で module まで解き(`smells::Scope`)、repo の定義(defrecord・defwire・defenum・defclass・
//! defeffect・頭が大文字の val)に当たれば定義の位置を添える(エディタが押して定義へ飛ぶため)。

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use rayon::prelude::*;
use serde::Serialize;

use crate::position::{LineIndex, Range};

use super::facts::form_bindings;
use super::names::module_of;
use super::smells::{live, Hy, Scope};

// --- 契約の形 ------------------------------------------------------------------------------

/// 定義の位置(エディタが押して飛ぶ先)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Location {
    pub path: String,
    pub range: Range,
}

/// 型の式(契約の閉じた集合)。`name` は書かれた綴り、`definition` は repo の中の定義(組み込みと解けない名は null)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum TypeRef {
    Name {
        name: String,
        definition: Option<Location>,
    },
    Union {
        members: Vec<TypeRef>,
    },
    Apply {
        head: Box<TypeRef>,
        args: Vec<TypeRef>,
    },
    /// 型として読めない式(書かれた綴りそのまま)。
    Unknown {
        text: String,
    },
}

/// effect 1 つ(宣言か推論)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EffectRef {
    pub name: String,
    pub definition: Option<Location>,
    /// defeffect の `:answer`(無ければ null)。
    pub answer: Option<TypeRef>,
    pub absent: Vec<TypeRef>,
    pub failure: Vec<TypeRef>,
}

/// 見出しの effect — 宣言(`:effects` が無ければ null)と推論。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SignatureEffects {
    pub declared: Option<Vec<EffectRef>>,
    pub inferred: Vec<EffectRef>,
}

/// 引数 1 つ(`:pre` に型が無ければ type は null)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SignatureParam {
    pub name: String,
    #[serde(rename = "type")]
    pub type_ref: Option<TypeRef>,
}

/// 見出しを持つ定義の種類。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum SignatureKind {
    Defk,
    Deff,
}

/// defk / deff 1 つの見出し。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Signature {
    pub kind: SignatureKind,
    pub name: String,
    pub path: String,
    /// 名の範囲。
    pub range: Range,
    pub full_range: Range,
    /// 契約の辞書 `{:pre … :post … :effects … :tags …}` の範囲(無ければ null)。
    pub contract_range: Option<Range>,
    pub params: Vec<SignatureParam>,
    /// `:post` の型(無ければ null)。Absent の包みは `absent` で別に持つ。
    pub answer: Option<TypeRef>,
    /// 答えが Maybe か(Absent が呼び手へ抜けうる)。
    pub absent: bool,
    pub raises: Vec<TypeRef>,
    pub effects: SignatureEffects,
    pub tags: BTreeMap<String, String>,
}

/// 束縛の形。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum BindingForm {
    #[serde(rename = "<-")]
    Bind,
    #[serde(rename = "val")]
    Val,
    #[serde(rename = "var")]
    Var,
    #[serde(rename = "setv")]
    Setv,
    #[serde(rename = ":=")]
    Assign,
}

/// 束縛の型をどこから読んだか。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BindingOrigin {
    /// `(<- x T …)` の注釈。
    Annotation,
    /// 撃った effect の値の答え。
    Effect,
    /// 撃った defk の答え・呼んだ deff の答え。
    Call,
    /// 字面(文字列・数・真偽・None・辞書・列)。
    Literal,
    /// 型の名の呼び(record を作る)。
    Constructor,
    /// `(:= x v)` の x を宣言した `(var x …)` の型。
    Var,
    /// 分からない(type は null)。
    Unknown,
}

/// 束縛 1 つ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Binding {
    pub form: BindingForm,
    pub name: String,
    pub path: String,
    /// 名の範囲。
    pub range: Range,
    /// 束縛の form 全体(括弧を含む)。
    pub form_range: Range,
    /// 頭の記号(`<-`・`val` …)の範囲。
    pub head_range: Range,
    /// `(<- x T …)` の T の範囲(無ければ null)。
    pub annotation_range: Option<Range>,
    /// 束ねる式の範囲(`(val x (! e))` は `(! e)` 全体)。
    pub value_range: Option<Range>,
    #[serde(rename = "type")]
    pub type_ref: Option<TypeRef>,
    pub origin: BindingOrigin,
    pub absent: bool,
    pub raises: Vec<TypeRef>,
}

/// 1 file の見出しと束縛。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileSignatures {
    pub signatures: Vec<Signature>,
    pub bindings: Vec<Binding>,
}

// --- repo の表 ---------------------------------------------------------------------------

/// 型の式(解く前 — 書かれた綴りと module まで含めた名)。
#[derive(Debug, Clone, PartialEq, Eq)]
enum TypeSyntax {
    Name { spelled: String, qualified: String },
    Union(Vec<TypeSyntax>),
    Apply(Box<TypeSyntax>, Vec<TypeSyntax>),
    Unknown(String),
}

impl TypeSyntax {
    /// 名の型なら module まで含めた名。
    fn qualified(&self) -> Option<&str> {
        match self {
            TypeSyntax::Name { qualified, .. } => Some(qualified),
            _ => None,
        }
    }

    /// 和なら要素、それ以外は自分 1 つ。
    fn members(&self) -> Vec<&TypeSyntax> {
        match self {
            TypeSyntax::Union(members) => members.iter().collect(),
            other => vec![other],
        }
    }
}

/// defeffect 1 つ。
#[derive(Debug, Clone)]
struct EffectFacts {
    location: Location,
    answer: Option<TypeSyntax>,
    absent: Vec<TypeSyntax>,
    failure: Vec<TypeSyntax>,
}

/// 撃った呼び 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
struct Site {
    /// 呼びの頭(module まで含めた名)。
    callee: String,
    /// `(<- … :absent F)` の F の型(Absent を Raise(F) に替える)。
    absent_as_raise: Option<TypeSyntax>,
    /// `absent-as` の中(Absent を受けている)。
    absent_handled: bool,
    /// `on-raise` の program の中(Raise を受けている)。
    raise_handled: bool,
}

/// defk / deff 1 つ(推論の材料)。
#[derive(Debug, Clone)]
struct DefinitionFacts {
    kind: SignatureKind,
    answer: Option<TypeSyntax>,
    sites: Vec<Site>,
}

/// defk の推論の結果。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct Summary {
    effects: BTreeSet<String>,
    absent: bool,
    /// module まで含めた名 → 書かれた綴り。
    raises: BTreeMap<String, String>,
}

/// repo の Hy の file 全部から集めた、型・effect・defk の表と、defk の推論。
#[derive(Debug, Default)]
pub struct World {
    types: HashMap<String, Location>,
    effects: HashMap<String, EffectFacts>,
    definitions: HashMap<String, DefinitionFacts>,
    summaries: HashMap<String, Summary>,
}

/// 1 file から集めた表の材料。
#[derive(Default)]
struct FileFacts {
    types: Vec<(String, Location)>,
    effects: Vec<(String, EffectFacts)>,
    definitions: Vec<(String, DefinitionFacts)>,
}

/// 型の名として定義を持つ頭。
const TYPE_HEADS: &[&str] = &[
    "defrecord",
    "defwire",
    "defenum",
    "defclass",
    "defeffect",
    "deftype",
];

impl World {
    /// repo の Hy の file 全部から表を作る。`overlay` = (根からの path, 中身)— 保存前の中身をその file の代わりに読む。
    pub fn build(root: &Path, overlay: Option<(&str, &str)>) -> World {
        let files: Vec<(String, std::path::PathBuf)> =
            doeff_indexer::hy_index::collect_hy_files(root)
                .into_iter()
                .filter_map(|path| super::relative_path(root, &path).map(|rel| (rel, path)))
                .collect();
        let mut facts: Vec<FileFacts> = files
            .par_iter()
            .filter(|(rel, _)| overlay.is_none_or(|(o, _)| o != rel))
            .filter_map(|(rel, path)| {
                let source = std::fs::read_to_string(path).ok()?;
                source
                    .contains("(def")
                    .then(|| file_facts(root, rel, &source))
            })
            .collect();
        if let Some((rel, source)) = overlay {
            facts.push(file_facts(root, rel, source));
        }
        let mut world = World::default();
        for file in facts {
            world.types.extend(file.types);
            world.effects.extend(file.effects);
            world.definitions.extend(file.definitions);
        }
        world.infer();
        world
    }

    /// defk の推論を不動点まで回す(呼びの輪は同じ集合に落ちる — 回数は defk の数で抑える)。
    fn infer(&mut self) {
        let defks: Vec<String> = self
            .definitions
            .iter()
            .filter(|(_, d)| d.kind == SignatureKind::Defk)
            .map(|(q, _)| q.clone())
            .collect();
        for _ in 0..=defks.len().min(64) {
            let mut changed = false;
            for name in &defks {
                let next = self.summary_of_sites(&self.definitions[name].sites);
                if self.summaries.get(name) != Some(&next) {
                    self.summaries.insert(name.clone(), next);
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
    }

    /// 撃った呼びの頭が repo の外の effect と見なせるか(頭が大文字で、repo の型でも定義でもない — 撃つ大文字の呼びは effect を作る呼び)。
    /// 宣言した `:effects [Delay]` を推論も拾えるようにするため(拾えないと「宣言だけ」の食い違いに見える)。
    fn is_foreign_effect(&self, callee: &str) -> bool {
        last_segment(callee).starts_with(|c: char| c.is_ascii_uppercase())
            && !self.types.contains_key(callee)
            && !self.definitions.contains_key(callee)
    }

    /// 撃った呼びの列から、effect・Absent・Raise を集める。
    fn summary_of_sites(&self, sites: &[Site]) -> Summary {
        let mut summary = Summary::default();
        for site in sites {
            let outcome = self.site_outcome(site);
            summary.effects.extend(outcome.effects);
            summary.absent |= outcome.absent;
            summary.raises.extend(outcome.raises);
        }
        summary
    }

    /// 撃った呼び 1 つの結末(呼びの頭が effect でも defk でもなければ空)。
    fn site_outcome(&self, site: &Site) -> Summary {
        let mut out = Summary::default();
        let (absent, raises) = if let Some(effect) = self.effects.get(&site.callee) {
            out.effects.insert(site.callee.clone());
            let raises: BTreeMap<String, String> = effect
                .failure
                .iter()
                .filter_map(|t| t.qualified().map(|q| (q.to_string(), spelled_of(t))))
                .collect();
            (!effect.absent.is_empty(), raises)
        } else if let Some(summary) = self.summaries.get(&site.callee) {
            out.effects.extend(summary.effects.iter().cloned());
            (summary.absent, summary.raises.clone())
        } else if self.is_foreign_effect(&site.callee) {
            // repo の外の effect(doeff 本体の Delay・Ask など)— 撃っているので effect として数える。答えの分け方は分からない
            out.effects.insert(site.callee.clone());
            (false, BTreeMap::new())
        } else {
            return out;
        };
        if !site.raise_handled {
            out.raises.extend(raises);
        }
        if absent && !site.absent_handled {
            match &site.absent_as_raise {
                Some(raise) if !site.raise_handled => {
                    if let Some(q) = raise.qualified() {
                        out.raises.insert(q.to_string(), spelled_of(raise));
                    }
                }
                Some(_) => {}
                None => out.absent = true,
            }
        }
        out
    }

    /// 解く前の型を契約の型にする(repo の定義に当たれば位置を添える)。
    fn resolve(&self, syntax: &TypeSyntax) -> TypeRef {
        match syntax {
            TypeSyntax::Name { spelled, qualified } => TypeRef::Name {
                name: spelled.clone(),
                definition: self.types.get(qualified).cloned(),
            },
            TypeSyntax::Union(members) => TypeRef::Union {
                members: members.iter().map(|m| self.resolve(m)).collect(),
            },
            TypeSyntax::Apply(head, args) => TypeRef::Apply {
                head: Box::new(self.resolve(head)),
                args: args.iter().map(|a| self.resolve(a)).collect(),
            },
            TypeSyntax::Unknown(text) => TypeRef::Unknown { text: text.clone() },
        }
    }

    /// module まで含めた名の型(綴りは最後の区切り)。
    fn resolve_qualified(&self, qualified: &str, spelled: &str) -> TypeRef {
        TypeRef::Name {
            name: spelled.to_string(),
            definition: self.types.get(qualified).cloned(),
        }
    }

    /// effect の参照(表に無ければ位置も答えも null)。
    fn effect_ref(&self, qualified: &str, spelled: &str) -> EffectRef {
        match self.effects.get(qualified) {
            Some(effect) => EffectRef {
                name: spelled.to_string(),
                definition: Some(effect.location.clone()),
                answer: effect.answer.as_ref().map(|a| self.resolve(a)),
                absent: effect.absent.iter().map(|t| self.resolve(t)).collect(),
                failure: effect.failure.iter().map(|t| self.resolve(t)).collect(),
            },
            None => EffectRef {
                name: spelled.to_string(),
                definition: None,
                answer: None,
                absent: Vec::new(),
                failure: Vec::new(),
            },
        }
    }

    /// effect の値の答え(`:answer` の要素から `:absent` と `:failure` を除いた物 — 1 つなら名、複数なら和)。
    fn effect_value(&self, effect: &EffectFacts) -> Option<TypeRef> {
        let answer = effect.answer.as_ref()?;
        let excluded: BTreeSet<&str> = effect
            .absent
            .iter()
            .chain(&effect.failure)
            .filter_map(|t| t.qualified())
            .collect();
        let kept: Vec<&TypeSyntax> = answer
            .members()
            .into_iter()
            .filter(|m| m.qualified().is_none_or(|q| !excluded.contains(q)))
            .collect();
        match kept.as_slice() {
            [] => None,
            [one] => Some(self.resolve(one)),
            many => Some(TypeRef::Union {
                members: many.iter().map(|m| self.resolve(m)).collect(),
            }),
        }
    }
}

/// 型の綴り(名ならその綴り・それ以外は空)。
fn spelled_of(syntax: &TypeSyntax) -> String {
    match syntax {
        TypeSyntax::Name { spelled, .. } => spelled.clone(),
        _ => String::new(),
    }
}

/// module まで含めた名の最後の区切り。
fn last_segment(qualified: &str) -> &str {
    qualified.rsplit('.').next().unwrap_or(qualified)
}

// --- 読み ---------------------------------------------------------------------------------

/// 1 file を読む道具(source・行の表・名の解き方)。
struct FileReader<'a> {
    hy: Hy<'a>,
    lines: LineIndex<'a>,
    scope: Scope<'a>,
    path: String,
}

impl<'a> FileReader<'a> {
    fn location(&self, form: &Form) -> Location {
        Location {
            path: self.path.clone(),
            range: self.lines.range(form.span.start, form.span.end),
        }
    }

    fn range(&self, form: &Form) -> Range {
        self.lines.range(form.span.start, form.span.end)
    }

    /// 型の式を読む(`(| a b)` は和・`(of H a …)` は当て・記号は名・それ以外は読めない式)。
    fn type_syntax(&self, form: &Form) -> TypeSyntax {
        match &form.node {
            Node::Symbol => {
                let spelled = self.hy.text(form);
                TypeSyntax::Name {
                    spelled: spelled.to_string(),
                    qualified: self.scope.qualify(spelled),
                }
            }
            Node::Seq {
                delim: Delim::Paren,
                ..
            } => {
                let items = live(form).unwrap_or_default();
                match items.first().and_then(|h| self.hy.symbol(h)) {
                    Some("|") => {
                        let mut members = Vec::new();
                        for item in &items[1..] {
                            match self.type_syntax(item) {
                                TypeSyntax::Union(inner) => members.extend(inner),
                                other => members.push(other),
                            }
                        }
                        TypeSyntax::Union(members)
                    }
                    Some("of") if items.len() >= 2 => TypeSyntax::Apply(
                        Box::new(self.type_syntax(items[1])),
                        items[2..].iter().map(|a| self.type_syntax(a)).collect(),
                    ),
                    _ => TypeSyntax::Unknown(self.hy.text(form).to_string()),
                }
            }
            _ => TypeSyntax::Unknown(self.hy.text(form).to_string()),
        }
    }

    /// 辞書の key の値。
    fn dict_value<'f>(&self, dict: &'f Form, key: &str) -> Option<&'f Form> {
        let Node::Seq {
            delim: Delim::Brace,
            items,
        } = &dict.node
        else {
            return None;
        };
        let items: Vec<&Form> = items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect();
        items.chunks(2).find_map(|pair| match pair {
            [k, v] if matches!(k.node, Node::Keyword) && self.hy.text(k) == key => Some(*v),
            _ => None,
        })
    }

    /// `[(: 名 型) …]` を 名 → 型の form の対にする(`(: …)` でない条件は飛ばす)。
    fn annotations<'f>(&self, list: Option<&'f Form>) -> Vec<(&'a str, &'f Form)> {
        let Some(items) = list.and_then(|l| l.bracket_items()) else {
            return Vec::new();
        };
        items
            .iter()
            .filter_map(|item| {
                let parts = live(item)?;
                match parts.as_slice() {
                    [head, name, ty] if self.hy.symbol(head) == Some(":") => {
                        Some((self.hy.text(name), *ty))
                    }
                    _ => None,
                }
            })
            .collect()
    }

    /// 型の名の列 `[A B]` を読む。
    fn type_list(&self, form: Option<&Form>) -> Vec<TypeSyntax> {
        form.and_then(|f| f.bracket_items())
            .map(|items| {
                items
                    .iter()
                    .filter(|i| !matches!(i.node, Node::Discarded))
                    .map(|i| self.type_syntax(i))
                    .collect()
            })
            .unwrap_or_default()
    }
}

/// 最上位の定義(`do` と `eval-and-compile` の中も)の form。
fn top_definitions<'f>(hy: &Hy, forms: &'f [Form]) -> Vec<&'f Form> {
    let mut out = Vec::new();
    let mut pending: Vec<&Form> = forms.iter().rev().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                pending.extend(items[1..].iter().rev().copied())
            }
            Some(_) => out.push(form),
            None => {}
        }
    }
    out
}

/// 定義の名の form(`#^ T 名` の注記つきなら的)と、注記の型の form。
fn definition_name(form: &Form) -> (Option<&Form>, Option<&Form>) {
    match &form.node {
        Node::Annotated { annotation, target } => (target.as_deref(), annotation.as_deref()),
        _ => (Some(form), None),
    }
}

/// defk / deff の形を読んだ物。
struct DefinitionShape<'f> {
    kind: SignatureKind,
    name: &'f Form,
    name_annotation: Option<&'f Form>,
    params: Option<&'f Form>,
    contract: Option<&'f Form>,
    body: Vec<&'f Form>,
}

/// `(defk 名 [引数] {契約}? "doc"? 本体…)` を読む(契約は doc の前でも後でもよい)。
fn definition_shape<'f>(hy: &Hy, form: &'f Form) -> Option<DefinitionShape<'f>> {
    let items = live(form)?;
    let kind = match items.first().and_then(|h| hy.symbol(h))? {
        "defk" => SignatureKind::Defk,
        "deff" => SignatureKind::Deff,
        _ => return None,
    };
    let (name, name_annotation) = definition_name(items.get(1)?);
    let name = name.filter(|n| matches!(n.node, Node::Symbol))?;
    let params = items
        .get(2)
        .copied()
        .filter(|p| p.bracket_items().is_some());
    let rest = &items[3.min(items.len())..];
    let contract = match rest {
        [first, ..] if first.is_brace() => Some(*first),
        [doc, second, ..] if matches!(doc.node, Node::Str { .. }) && second.is_brace() => {
            Some(*second)
        }
        _ => None,
    };
    let body = rest
        .iter()
        .copied()
        .filter(|f| contract.is_none_or(|c| !std::ptr::eq(*f, c)))
        .collect();
    Some(DefinitionShape {
        kind,
        name,
        name_annotation,
        params,
        contract,
        body,
    })
}

/// 1 file から表の材料を集める。
fn file_facts(root: &Path, rel: &str, source: &str) -> FileFacts {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: root.join(rel).to_string_lossy().into_owned(),
    };
    let mut out = FileFacts::default();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(items) = live(form) else { continue };
        let head = items
            .first()
            .and_then(|h| reader.hy.symbol(h))
            .unwrap_or("");
        let named = items
            .get(1)
            .and_then(|n| definition_name(n).0)
            .filter(|n| matches!(n.node, Node::Symbol));
        if TYPE_HEADS.contains(&head)
            || (head == "val"
                && named.is_some_and(|n| {
                    reader
                        .hy
                        .text(n)
                        .starts_with(|c: char| c.is_ascii_uppercase())
                }))
        {
            if let Some(name) = named {
                let qualified = reader.scope.qualify(reader.hy.text(name));
                out.types.push((qualified.clone(), reader.location(name)));
                if head == "defeffect" {
                    let contract = items[2..].iter().take(2).find(|f| f.is_brace()).copied();
                    let effect = EffectFacts {
                        location: reader.location(name),
                        answer: contract
                            .and_then(|c| reader.dict_value(c, ":answer"))
                            .map(|a| reader.type_syntax(a)),
                        absent: reader
                            .type_list(contract.and_then(|c| reader.dict_value(c, ":absent"))),
                        failure: reader
                            .type_list(contract.and_then(|c| reader.dict_value(c, ":failure"))),
                    };
                    out.effects.push((qualified, effect));
                }
            }
            continue;
        }
        if let Some(shape) = definition_shape(&reader.hy, form) {
            let qualified = reader.scope.qualify(reader.hy.text(shape.name));
            let answer = answer_syntax(&reader, &shape);
            let mut sites = Vec::new();
            for item in &shape.body {
                collect_sites(&reader, item, Flags::default(), &mut sites);
            }
            out.definitions.push((
                qualified,
                DefinitionFacts {
                    kind: shape.kind,
                    answer,
                    sites,
                },
            ));
        }
    }
    out
}

/// 答えの型(`:post` の `(: % T)`、無ければ名の注記 `#^ T`)。
fn answer_syntax(reader: &FileReader, shape: &DefinitionShape) -> Option<TypeSyntax> {
    let post = shape.contract.and_then(|c| reader.dict_value(c, ":post"));
    reader
        .annotations(post)
        .into_iter()
        .find(|(name, _)| *name == "%")
        .map(|(_, ty)| reader.type_syntax(ty))
        .or_else(|| shape.name_annotation.map(|a| reader.type_syntax(a)))
}

/// 撃った呼びの受け方(外側の `absent-as` と `on-raise`)。
#[derive(Debug, Clone, Copy, Default)]
struct Flags {
    absent_handled: bool,
    raise_handled: bool,
}

/// `(<- …)` を読んだ物(名・注記・式・`:absent F`)。
struct BindShape<'f> {
    name: Option<&'f Form>,
    annotation: Option<&'f Form>,
    value: &'f Form,
    absent_as_raise: Option<&'f Form>,
}

/// `(<- x e)` / `(<- x T e)` / `(<- e)`、末尾に `:absent F` があってもよい。
fn bind_shape<'f>(hy: &Hy, items: &[&'f Form]) -> Option<BindShape<'f>> {
    let mut args = &items[1..];
    let mut absent_as_raise = None;
    if args.len() >= 3
        && matches!(args[args.len() - 2].node, Node::Keyword)
        && hy.text(args[args.len() - 2]) == ":absent"
    {
        absent_as_raise = Some(args[args.len() - 1]);
        args = &args[..args.len() - 2];
    }
    match args {
        [value] => Some(BindShape {
            name: None,
            annotation: None,
            value,
            absent_as_raise,
        }),
        [name, value] => Some(BindShape {
            name: Some(name),
            annotation: None,
            value,
            absent_as_raise,
        }),
        [name, annotation, value] => Some(BindShape {
            name: Some(name),
            annotation: Some(annotation),
            value,
            absent_as_raise,
        }),
        _ => None,
    }
}

/// 呼びの頭の綴り(`(f …)` の f)。
fn call_head<'a>(hy: &Hy<'a>, form: &Form) -> Option<&'a str> {
    live(form)?.first().and_then(|h| hy.symbol(h))
}

/// `:absent F` の F の型(`(Conflict "…")` の頭か、記号そのもの)。
fn raise_type(reader: &FileReader, form: &Form) -> TypeSyntax {
    match call_head(&reader.hy, form) {
        Some(head) => TypeSyntax::Name {
            spelled: head.to_string(),
            qualified: reader.scope.qualify(head),
        },
        None => reader.type_syntax(form),
    }
}

/// 撃った呼びを集める(quote の中は読まない)。
fn collect_sites(reader: &FileReader, form: &Form, flags: Flags, out: &mut Vec<Site>) {
    let children: Vec<&Form> = match &form.node {
        Node::Seq { items, .. } => items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect(),
        Node::Prefixed {
            prefix,
            inner: Some(inner),
        } => {
            if matches!(
                prefix,
                doeff_indexer::hy_index::reader::Prefix::Quote
                    | doeff_indexer::hy_index::reader::Prefix::Quasiquote
            ) {
                return;
            }
            vec![inner.as_ref()]
        }
        Node::Annotated {
            annotation: _,
            target: Some(target),
        } => vec![target.as_ref()],
        _ => return,
    };
    let head = match &form.node {
        Node::Seq {
            delim: Delim::Paren,
            ..
        } => children.first().and_then(|h| reader.hy.symbol(h)),
        _ => None,
    };
    match head {
        Some("quote" | "quasiquote") => {}
        Some("<-") => {
            if let Some(bind) = bind_shape(&reader.hy, &children) {
                push_site(
                    reader,
                    bind.value,
                    bind.absent_as_raise.map(|f| raise_type(reader, f)),
                    flags,
                    out,
                );
                collect_sites(reader, bind.value, flags, out);
            }
        }
        Some("!") => {
            if let Some(value) = children.get(1) {
                push_site(reader, value, None, flags, out);
                collect_sites(reader, value, flags, out);
            }
        }
        Some("absent-as") => {
            for child in &children[1..] {
                collect_sites(
                    reader,
                    child,
                    Flags {
                        absent_handled: true,
                        ..flags
                    },
                    out,
                );
            }
        }
        Some("on-raise") => {
            if let Some(program) = children.get(1) {
                collect_sites(
                    reader,
                    program,
                    Flags {
                        raise_handled: true,
                        ..flags
                    },
                    out,
                );
            }
            for child in children.iter().skip(2) {
                collect_sites(reader, child, flags, out);
            }
        }
        _ => {
            for child in children {
                collect_sites(reader, child, flags, out);
            }
        }
    }
}

/// 撃った式の頭を呼びとして控える。
fn push_site(
    reader: &FileReader,
    value: &Form,
    absent_as_raise: Option<TypeSyntax>,
    flags: Flags,
    out: &mut Vec<Site>,
) {
    if let Some(head) = call_head(&reader.hy, value) {
        out.push(Site {
            callee: reader.scope.qualify(head),
            absent_as_raise,
            absent_handled: flags.absent_handled,
            raise_handled: flags.raise_handled,
        });
    }
}

// --- 1 file の見出しと束縛 -----------------------------------------------------------------

/// 1 file の見出しと束縛を読む(表は `World::build` で、この file の同じ中身を overlay にして作った物)。
pub fn file_signatures(world: &World, root: &Path, rel: &str, source: &str) -> FileSignatures {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: root.join(rel).to_string_lossy().into_owned(),
    };
    let mut out = FileSignatures::default();
    for form in top_definitions(&reader.hy, &forms) {
        if let Some(shape) = definition_shape(&reader.hy, form) {
            out.signatures
                .push(signature_of(world, &reader, form, &shape));
        }
        let mut vars: HashMap<String, Option<TypeRef>> = HashMap::new();
        collect_bindings(
            world,
            &reader,
            form,
            Flags::default(),
            &mut vars,
            &mut out.bindings,
        );
    }
    out
}

/// defk / deff 1 つの見出し。
fn signature_of(
    world: &World,
    reader: &FileReader,
    form: &Form,
    shape: &DefinitionShape,
) -> Signature {
    let pre = shape.contract.and_then(|c| reader.dict_value(c, ":pre"));
    let annotated: BTreeMap<&str, TypeRef> = reader
        .annotations(pre)
        .into_iter()
        .map(|(name, ty)| (name, world.resolve(&reader.type_syntax(ty))))
        .collect();
    let params = shape
        .params
        .and_then(|p| p.bracket_items())
        .map(|items| {
            items
                .iter()
                .filter(|i| matches!(i.node, Node::Symbol) && !reader.hy.text(i).starts_with('&'))
                .map(|i| {
                    let name = reader.hy.text(i);
                    SignatureParam {
                        name: name.to_string(),
                        type_ref: annotated.get(name).cloned(),
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    let qualified = reader.scope.qualify(reader.hy.text(shape.name));
    let summary = world.summaries.get(&qualified).cloned().unwrap_or_default();
    let declared = shape
        .contract
        .and_then(|c| reader.dict_value(c, ":effects"))
        .and_then(|list| list.bracket_items())
        .map(|items| {
            items
                .iter()
                .filter_map(|i| reader.hy.symbol(i))
                .map(|spelled| world.effect_ref(&reader.scope.qualify(spelled), spelled))
                .collect()
        });
    let inferred = summary
        .effects
        .iter()
        .map(|q| world.effect_ref(q, last_segment(q)))
        .collect();
    let tags = shape
        .contract
        .and_then(|c| reader.dict_value(c, ":tags"))
        .map(|dict| string_entries(reader, dict))
        .unwrap_or_default();
    Signature {
        kind: shape.kind,
        name: reader.hy.text(shape.name).to_string(),
        path: reader.path.clone(),
        range: reader.range(shape.name),
        full_range: reader.range(form),
        contract_range: shape.contract.map(|c| reader.range(c)),
        params,
        answer: answer_syntax(reader, shape).map(|a| world.resolve(&a)),
        absent: summary.absent,
        raises: summary
            .raises
            .iter()
            .map(|(q, spelled)| world.resolve_qualified(q, spelled))
            .collect(),
        effects: SignatureEffects { declared, inferred },
        tags,
    }
}

/// 辞書の文字列の値の鍵(`:context "x"` → context = x)。
fn string_entries(reader: &FileReader, dict: &Form) -> BTreeMap<String, String> {
    let Node::Seq {
        delim: Delim::Brace,
        items,
    } = &dict.node
    else {
        return BTreeMap::new();
    };
    let items: Vec<&Form> = items
        .iter()
        .filter(|i| !matches!(i.node, Node::Discarded))
        .collect();
    items
        .chunks(2)
        .filter_map(|pair| match pair {
            [k, v] if matches!(k.node, Node::Keyword) => match &v.node {
                Node::Str { body, .. } => Some((
                    reader.hy.text(k).trim_start_matches(':').to_string(),
                    reader
                        .hy
                        .src
                        .get(body.start..body.end)
                        .unwrap_or("")
                        .to_string(),
                )),
                _ => None,
            },
            _ => None,
        })
        .collect()
}

/// 束縛の型を読んだ結果。
struct Typed {
    type_ref: Option<TypeRef>,
    origin: BindingOrigin,
    absent: bool,
    raises: Vec<TypeRef>,
}

impl Typed {
    fn unknown() -> Typed {
        Typed {
            type_ref: None,
            origin: BindingOrigin::Unknown,
            absent: false,
            raises: Vec::new(),
        }
    }
}

/// 撃った式(`(<- …)` の右辺・`(! …)` の中身)の型と Absent / Raise。
fn performed_type(
    world: &World,
    reader: &FileReader,
    value: &Form,
    absent_as_raise: Option<&Form>,
    flags: Flags,
) -> Typed {
    let Some(head) = call_head(&reader.hy, value) else {
        return Typed::unknown();
    };
    let callee = reader.scope.qualify(head);
    let site = Site {
        callee: callee.clone(),
        absent_as_raise: absent_as_raise.map(|f| raise_type(reader, f)),
        absent_handled: flags.absent_handled,
        raise_handled: flags.raise_handled,
    };
    let outcome = world.site_outcome(&site);
    let raises = outcome
        .raises
        .iter()
        .map(|(q, spelled)| world.resolve_qualified(q, spelled))
        .collect();
    if let Some(effect) = world.effects.get(&callee) {
        let type_ref = world.effect_value(effect);
        let origin = if type_ref.is_some() {
            BindingOrigin::Effect
        } else {
            BindingOrigin::Unknown
        };
        return Typed {
            type_ref,
            origin,
            absent: outcome.absent,
            raises,
        };
    }
    match world.definitions.get(&callee) {
        Some(definition) if definition.kind == SignatureKind::Defk => {
            let type_ref = definition.answer.as_ref().map(|a| world.resolve(a));
            let origin = if type_ref.is_some() {
                BindingOrigin::Call
            } else {
                BindingOrigin::Unknown
            };
            Typed {
                type_ref,
                origin,
                absent: outcome.absent,
                raises,
            }
        }
        _ => Typed::unknown(),
    }
}

/// 撃たない式の型(字面・型の名の呼び・deff の呼び・`(! …)`)。
fn value_type(world: &World, reader: &FileReader, value: &Form, flags: Flags) -> Typed {
    let literal = |name: &str| Typed {
        type_ref: Some(TypeRef::Name {
            name: name.to_string(),
            definition: None,
        }),
        origin: BindingOrigin::Literal,
        absent: false,
        raises: Vec::new(),
    };
    match &value.node {
        Node::Str { .. } => literal("str"),
        Node::Number => literal(if reader.hy.text(value).contains('.') {
            "float"
        } else {
            "int"
        }),
        Node::Symbol => match reader.hy.text(value) {
            "True" | "False" => literal("bool"),
            "None" => literal("None"),
            _ => Typed::unknown(),
        },
        Node::Seq {
            delim: Delim::Brace,
            ..
        } => literal("dict"),
        Node::Seq {
            delim: Delim::Bracket,
            ..
        } => literal("list"),
        Node::Seq {
            delim: Delim::Paren,
            ..
        } => {
            let items = live(value).unwrap_or_default();
            let Some(head) = items.first().and_then(|h| reader.hy.symbol(h)) else {
                return Typed::unknown();
            };
            if head == "!" {
                return match items.get(1) {
                    Some(inner) => performed_type(world, reader, inner, None, flags),
                    None => Typed::unknown(),
                };
            }
            let qualified = reader.scope.qualify(head);
            if head.starts_with(|c: char| c.is_ascii_uppercase())
                && world.types.contains_key(&qualified)
                && !world.effects.contains_key(&qualified)
            {
                return Typed {
                    type_ref: Some(TypeRef::Name {
                        name: head.to_string(),
                        definition: world.types.get(&qualified).cloned(),
                    }),
                    origin: BindingOrigin::Constructor,
                    absent: false,
                    raises: Vec::new(),
                };
            }
            match world.definitions.get(&qualified) {
                Some(definition) if definition.kind == SignatureKind::Deff => {
                    match &definition.answer {
                        Some(answer) => Typed {
                            type_ref: Some(world.resolve(answer)),
                            origin: BindingOrigin::Call,
                            absent: false,
                            raises: Vec::new(),
                        },
                        None => Typed::unknown(),
                    }
                }
                _ => Typed::unknown(),
            }
        }
        _ => Typed::unknown(),
    }
}

/// 束縛を集める(quote の中は読まない)。`vars` は同じ最上位の定義の中の `var` の型(`:=` のため)。
fn collect_bindings(
    world: &World,
    reader: &FileReader,
    form: &Form,
    flags: Flags,
    vars: &mut HashMap<String, Option<TypeRef>>,
    out: &mut Vec<Binding>,
) {
    let children: Vec<&Form> = match &form.node {
        Node::Seq { items, .. } => items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect(),
        Node::Prefixed {
            prefix,
            inner: Some(inner),
        } => {
            if matches!(
                prefix,
                doeff_indexer::hy_index::reader::Prefix::Quote
                    | doeff_indexer::hy_index::reader::Prefix::Quasiquote
            ) {
                return;
            }
            vec![inner.as_ref()]
        }
        Node::Annotated {
            target: Some(target),
            ..
        } => vec![target.as_ref()],
        _ => return,
    };
    let is_paren = matches!(
        &form.node,
        Node::Seq {
            delim: Delim::Paren,
            ..
        }
    );
    let head_form = children.first().copied().filter(|_| is_paren);
    let head = head_form.and_then(|h| match h.node {
        Node::Symbol | Node::Keyword => Some(reader.hy.text(h)),
        _ => None,
    });
    let mut inner_flags = flags;
    match head {
        Some("quote" | "quasiquote") => return,
        Some("<-") => {
            if let Some(bind) = bind_shape(&reader.hy, &children) {
                if let Some(name) = bind.name.filter(|n| matches!(n.node, Node::Symbol)) {
                    let performed =
                        performed_type(world, reader, bind.value, bind.absent_as_raise, flags);
                    let typed = match bind.annotation {
                        Some(annotation) => Typed {
                            type_ref: Some(world.resolve(&reader.type_syntax(annotation))),
                            origin: BindingOrigin::Annotation,
                            absent: performed.absent,
                            raises: performed.raises,
                        },
                        None => performed,
                    };
                    out.push(binding(
                        reader,
                        BindingForm::Bind,
                        form,
                        head_form,
                        name,
                        bind.annotation,
                        Some(bind.value),
                        typed,
                    ));
                }
            }
        }
        Some(word @ ("val" | "var" | "setv")) => {
            let kind = match word {
                "val" => BindingForm::Val,
                "var" => BindingForm::Var,
                _ => BindingForm::Setv,
            };
            for pair in children[1..].chunks(2) {
                if let [name, value] = pair {
                    if matches!(name.node, Node::Symbol) {
                        let typed = match value_type(world, reader, value, flags) {
                            // `(var x None)` は後で別の型の値を入れる置き場 — None を型と読まない(分からないにする)。
                            Typed {
                                type_ref: Some(TypeRef::Name { name, .. }),
                                origin: BindingOrigin::Literal,
                                ..
                            } if kind == BindingForm::Var && name == "None" => Typed::unknown(),
                            other => other,
                        };
                        if kind == BindingForm::Var {
                            vars.insert(reader.hy.text(name).to_string(), typed.type_ref.clone());
                        }
                        out.push(binding(
                            reader,
                            kind,
                            form,
                            head_form,
                            name,
                            None,
                            Some(value),
                            typed,
                        ));
                    }
                }
            }
        }
        Some(":=") => {
            if let [_, name, value, ..] = children.as_slice() {
                if matches!(name.node, Node::Symbol) {
                    let typed = match vars.get(reader.hy.text(name)) {
                        Some(Some(type_ref)) => Typed {
                            type_ref: Some(type_ref.clone()),
                            origin: BindingOrigin::Var,
                            absent: false,
                            raises: Vec::new(),
                        },
                        _ => Typed::unknown(),
                    };
                    out.push(binding(
                        reader,
                        BindingForm::Assign,
                        form,
                        head_form,
                        name,
                        None,
                        Some(value),
                        typed,
                    ));
                }
            }
        }
        Some("absent-as") => inner_flags.absent_handled = true,
        Some("on-raise") => {
            if let Some(program) = children.get(1) {
                collect_bindings(
                    world,
                    reader,
                    program,
                    Flags {
                        raise_handled: true,
                        ..flags
                    },
                    vars,
                    out,
                );
            }
            for child in children.iter().skip(2) {
                collect_bindings(world, reader, child, flags, vars, out);
            }
            return;
        }
        _ => {}
    }
    for child in children {
        collect_bindings(world, reader, child, inner_flags, vars, out);
    }
}

/// 束縛 1 つを契約の形にする。
#[allow(clippy::too_many_arguments)]
fn binding(
    reader: &FileReader,
    form: BindingForm,
    whole: &Form,
    head: Option<&Form>,
    name: &Form,
    annotation: Option<&Form>,
    value: Option<&Form>,
    typed: Typed,
) -> Binding {
    Binding {
        form,
        name: reader.hy.text(name).to_string(),
        path: reader.path.clone(),
        range: reader.range(name),
        form_range: reader.range(whole),
        head_range: head
            .map(|h| reader.range(h))
            .unwrap_or_else(|| reader.range(whole)),
        annotation_range: annotation.map(|a| reader.range(a)),
        value_range: value.map(|v| reader.range(v)),
        type_ref: typed.type_ref,
        origin: typed.origin,
        absent: typed.absent,
        raises: typed.raises,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 根に file を並べて表を作り、1 file の見出しと束縛を読む。
    fn read(files: &[(&str, &str)], target: &str) -> FileSignatures {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        file_signatures(&world, &root, target, source)
    }

    fn name_of(t: &TypeRef) -> String {
        match t {
            TypeRef::Name { name, .. } => name.clone(),
            TypeRef::Union { members } => {
                members.iter().map(name_of).collect::<Vec<_>>().join(" | ")
            }
            TypeRef::Apply { head, args } => format!(
                "{}[{}]",
                name_of(head),
                args.iter().map(name_of).collect::<Vec<_>>().join(", ")
            ),
            TypeRef::Unknown { text } => format!("?{}", text),
        }
    }

    const INTENT: &str = r#"
(defrecord Row "行" (#^ str id))
(defrecord Missing "無い" (#^ str id))
(defrecord Unreadable "読めない" (#^ str id))
(defeffect ReadRow
  "行を読む"
  {:fields [(: id str)]
   :answer (| Row Missing Unreadable)
   :absent [Missing] :failure [Unreadable]
   :tags {:context "demo" :role "intent"}})
(defeffect PutRow
  "行を書く"
  {:fields [(: row Row)] :answer bool :tags {:context "demo" :role "intent"}})
"#;

    #[test]
    fn pure_defk_signature_reads_pre_post_and_tags() {
        let core = r#"
(defk body-version-line [n document]
  {:tags {:context "kanban" :role "judgment"}
   :pre [(: n int) (: document str)]
   :post [(: % str)]}
  "doc"
  f"{n}{document}")
"#;
        let got = read(&[("core/body.hy", core)], "core/body.hy");
        let sig = &got.signatures[0];
        assert_eq!(sig.kind, SignatureKind::Defk);
        assert_eq!(sig.name, "body-version-line");
        let params: Vec<(String, String)> = sig
            .params
            .iter()
            .map(|p| {
                (
                    p.name.clone(),
                    p.type_ref.as_ref().map(name_of).unwrap_or_default(),
                )
            })
            .collect();
        assert_eq!(
            params,
            vec![
                ("n".into(), "int".into()),
                ("document".into(), "str".into())
            ]
        );
        assert_eq!(sig.answer.as_ref().map(name_of).as_deref(), Some("str"));
        assert!(!sig.absent);
        assert!(sig.effects.declared.is_none());
        assert!(sig.effects.inferred.is_empty());
        assert_eq!(sig.tags.get("role").map(String::as_str), Some("judgment"));
        assert_eq!(sig.contract_range.unwrap().start.line, 2);
    }

    #[test]
    fn effects_are_inferred_transitively_with_absent_and_raise() {
        let core = r#"
(import intent.rows [ReadRow PutRow Row])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row Row (ReadRow id))
  row)
(defk store [id]
  {:pre [(: id str)] :post [(: % bool)] :effects [PutRow]}
  (val row (! (fetch id)))
  (<- ok (PutRow row))
  ok)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let fetch = &got.signatures[0];
        assert!(fetch.absent, "ReadRow の :absent は呼び手へ抜ける");
        assert_eq!(
            fetch.raises.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Unreadable"]
        );
        let store = &got.signatures[1];
        let inferred: Vec<String> = store
            .effects
            .inferred
            .iter()
            .map(|e| e.name.clone())
            .collect();
        assert_eq!(inferred, vec!["PutRow", "ReadRow"]);
        let declared: Vec<String> = store
            .effects
            .declared
            .as_ref()
            .unwrap()
            .iter()
            .map(|e| e.name.clone())
            .collect();
        assert_eq!(
            declared,
            vec!["PutRow"],
            "宣言と推論の食い違いはそのまま出す(エディタが見せる)"
        );
        assert!(store.absent);
        let read_row = &fetch.effects.declared.as_ref().unwrap()[0];
        assert!(read_row
            .definition
            .as_ref()
            .unwrap()
            .path
            .ends_with("intent/rows.hy"));
        assert_eq!(
            read_row.absent.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Missing"]
        );
    }

    #[test]
    fn absent_suffix_and_absent_as_do_not_wrap_in_maybe() {
        let core = r#"
(import intent.rows [ReadRow Row])
(defrecord Conflict "競合" (#^ str why))
(defk strict [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- row (ReadRow id) :absent (Conflict "無い"))
  row)
(defk lenient [id]
  {:pre [(: id str)] :post [(: % (| Row None))]}
  (absent-as None (ReadRow id)))
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let strict = &got.signatures[0];
        assert!(!strict.absent);
        let mut raises: Vec<String> = strict.raises.iter().map(name_of).collect();
        raises.sort();
        assert_eq!(raises, vec!["Conflict", "Unreadable"]);
        assert!(!got.signatures[1].absent);
    }

    #[test]
    fn bindings_carry_types_or_null_when_unknown() {
        let core = r#"
(import intent.rows [ReadRow Row])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- a str (helper id))
  (<- row (ReadRow id))
  (val made (Row :id id))
  (var count 0)
  (:= count (+ count 1))
  (var slot None)
  (:= slot row)
  (setv mystery (compute id))
  row)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let seen: Vec<(BindingForm, String, Option<String>, BindingOrigin)> = got
            .bindings
            .iter()
            .map(|b| {
                (
                    b.form,
                    b.name.clone(),
                    b.type_ref.as_ref().map(name_of),
                    b.origin,
                )
            })
            .collect();
        assert_eq!(
            seen,
            vec![
                (
                    BindingForm::Bind,
                    "a".into(),
                    Some("str".into()),
                    BindingOrigin::Annotation
                ),
                (
                    BindingForm::Bind,
                    "row".into(),
                    Some("Row".into()),
                    BindingOrigin::Effect
                ),
                (
                    BindingForm::Val,
                    "made".into(),
                    Some("Row".into()),
                    BindingOrigin::Constructor
                ),
                (
                    BindingForm::Var,
                    "count".into(),
                    Some("int".into()),
                    BindingOrigin::Literal
                ),
                (
                    BindingForm::Assign,
                    "count".into(),
                    Some("int".into()),
                    BindingOrigin::Var
                ),
                (
                    BindingForm::Var,
                    "slot".into(),
                    None,
                    BindingOrigin::Unknown
                ),
                (
                    BindingForm::Assign,
                    "slot".into(),
                    None,
                    BindingOrigin::Unknown
                ),
                (
                    BindingForm::Setv,
                    "mystery".into(),
                    None,
                    BindingOrigin::Unknown
                ),
            ]
        );
        let row = &got.bindings[1];
        assert!(row.absent);
        assert_eq!(
            row.raises.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Unreadable"]
        );
        assert!(got.bindings[0].annotation_range.is_some());
        let TypeRef::Name { definition, .. } = got.bindings[2].type_ref.as_ref().unwrap() else {
            panic!("名の型")
        };
        assert!(
            definition
                .as_ref()
                .unwrap()
                .path
                .ends_with("intent/rows.hy"),
            "型の名は定義へ結ぶ"
        );
    }

    #[test]
    fn quoted_forms_are_not_read() {
        let core = r#"
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val q '(val hidden 1))
  x)
"#;
        let got = read(&[("core/q.hy", core)], "core/q.hy");
        assert!(got.bindings.iter().all(|b| b.name != "hidden"));
    }

    #[test]
    fn foreign_effects_are_inferred_so_declarations_match() {
        let core = r#"
(import doeff_core_effects [Delay])
(import intent.rows [Row])
(defk wait [seconds]
  {:pre [(: seconds int)] :post [(: % int)] :effects [Delay]}
  (<- (Delay seconds))
  (val made (! (Row :id "x")))
  seconds)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/wait.hy", core)],
            "core/wait.hy",
        );
        let sig = &got.signatures[0];
        let inferred: Vec<&str> = sig
            .effects
            .inferred
            .iter()
            .map(|e| e.name.as_str())
            .collect();
        assert_eq!(
            inferred,
            vec!["Delay"],
            "repo の外の effect も推論に入る・repo の record を撃つ形は effect にしない"
        );
        assert!(sig.effects.inferred[0].definition.is_none());
        assert_eq!(sig.effects.declared.as_ref().unwrap()[0].name, "Delay");
    }
}

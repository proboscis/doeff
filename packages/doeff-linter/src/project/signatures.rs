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
    /// 推論が追いきれたか — 追えない呼び(repo の外の関数・deff・method)を撃っていれば false で、`inferred` は見えた分だけ。
    /// エディタが「effect なし」と「推論しきれない」を分けて描くため(版 2 への欄の追加)。
    pub complete: bool,
    /// 追えなかった呼びの頭の名(書かれた綴りの最後の節・重複なし・並びは名の順)。`complete` が false の時だけ空でない。
    /// エディタが「inference partial: with_handlers, …」と何が追えなかったかを出すため(版 2 への欄の追加・無い出力は空と読む)。
    pub opaque: Vec<String>,
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

/// `val` / `var` の前に付く語(ADR-DOE-HY-006: `(lazy val x e)` = 初めて使った時に評価・`(session val x e)` = defhandler の
/// セッションで共有)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BindingModifier {
    Lazy,
    Session,
}

impl BindingModifier {
    /// 頭の語から読む(`lazy` / `session` でなければ None)。
    pub(super) fn of(word: &str) -> Option<BindingModifier> {
        [("lazy", BindingModifier::Lazy), ("session", BindingModifier::Session)]
            .into_iter()
            .find(|(spelled, _)| *spelled == word)
            .map(|(_, modifier)| modifier)
    }
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
    /// `(lazy val …)`・`(session var …)` の前の語(無ければ null。form は val / var)。
    pub modifier: Option<BindingModifier>,
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
    /// 定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え(`call_view.rs`)。
    pub rewrites: Vec<super::call_view::Rewrite>,
    /// 定義ごとの本体の文字の行(`body_view.rs` — 読む面が描く)。
    pub bodies: Vec<super::body_view::Body>,
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
pub(super) struct EffectFacts {
    pub(super) location: Location,
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
    /// 呼びの頭の記号の byte の範囲(DOEFF127 が違反の場所に使う — 表の比べには使わない)。
    head: (usize, usize),
}

/// defk / deff 1 つ(推論の材料)。
#[derive(Debug, Clone)]
pub(super) struct DefinitionFacts {
    pub(super) kind: SignatureKind,
    /// 名の位置(表示の置き換えの部品が押して飛ぶ先)。
    pub(super) location: Location,
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
    /// 追えない呼び(repo の外の関数・deff・method)を撃っている(推論の effect の集合が欠けうる — DOEFF127 が
    /// 「宣言したのに起こしていない」を判じない)。
    opaque: bool,
    /// 追えない呼びの頭(module まで含めた名)— エディタが「何が追えなかったか」を出すため(呼び先の defk の分も畳んで持つ)。
    opaque_calls: BTreeSet<String>,
}

/// repo の Hy の file 全部から集めた、型・effect・defk の表と、defk の推論。
#[derive(Debug, Default)]
pub struct World {
    pub(super) types: HashMap<String, Location>,
    pub(super) effects: HashMap<String, EffectFacts>,
    pub(super) definitions: HashMap<String, DefinitionFacts>,
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
    pub(super) fn is_foreign_effect(&self, callee: &str) -> bool {
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
            summary.opaque |= outcome.opaque;
            summary.opaque_calls.extend(outcome.opaque_calls);
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
            out.opaque = summary.opaque;
            out.opaque_calls.extend(summary.opaque_calls.iter().cloned());
            (summary.absent, summary.raises.clone())
        } else if self.is_foreign_effect(&site.callee) {
            // repo の外の effect(doeff 本体の Delay・Ask など)— 撃っているので effect として数える。答えの分け方は分からない
            out.effects.insert(site.callee.clone());
            (false, BTreeMap::new())
        } else {
            // 追えない呼び(repo の外の関数・deff・method)を撃っている — その先の effect は分からない
            out.opaque = true;
            out.opaque_calls.insert(site.callee.clone());
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

    /// defk / deff の答えの型(`:post` の型か名の注記。書いていなければ None)。
    pub(super) fn answer_of(&self, qualified: &str) -> Option<TypeRef> {
        self.definitions.get(qualified)?.answer.as_ref().map(|a| self.resolve(a))
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
    pub(super) fn effect_value(&self, effect: &EffectFacts) -> Option<TypeRef> {
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
pub(super) struct FileReader<'a> {
    pub(super) hy: Hy<'a>,
    pub(super) lines: LineIndex<'a>,
    pub(super) scope: Scope<'a>,
    pub(super) path: String,
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
pub(super) fn top_definitions<'f>(hy: &Hy, forms: &'f [Form]) -> Vec<&'f Form> {
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
pub(super) struct DefinitionShape<'f> {
    pub(super) kind: SignatureKind,
    pub(super) name: &'f Form,
    name_annotation: Option<&'f Form>,
    pub(super) params: Option<&'f Form>,
    pub(super) contract: Option<&'f Form>,
    pub(super) body: Vec<&'f Form>,
}

/// `(defk 名 [引数] {契約}? "doc"? 本体…)` を読む(契約は doc の前でも後でもよい)。
pub(super) fn definition_shape<'f>(hy: &Hy, form: &'f Form) -> Option<DefinitionShape<'f>> {
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
                    location: reader.location(shape.name),
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
pub(super) struct BindShape<'f> {
    pub(super) name: Option<&'f Form>,
    pub(super) annotation: Option<&'f Form>,
    pub(super) value: &'f Form,
    pub(super) absent_as_raise: Option<&'f Form>,
}

/// 宣言の名と値の組 1 つ。`bang` は値の前の `!`(`(val x ! (f a))` — Hy の reader は `!(f a)` も 2 つの要素に読む)。
pub(super) struct BindingPair<'f> {
    pub(super) name: &'f Form,
    pub(super) bang: Option<&'f Form>,
    /// 値(`(val x)` のように欠けていれば None)。
    pub(super) value: Option<&'f Form>,
}

/// 宣言(`val`・`var`・`lazy val` … と `:=`)の頭の後ろの並びを名と値の組に読む。値の前の `! 式` の 2 つ組は撃つ値として
/// 1 つに畳む(ADR-DOE-HY-006 §3 — 宣言と `:=` の値の部分に限る。setv は畳まない)。
pub(super) fn binding_pairs<'f>(hy: &Hy, items: &[&'f Form]) -> Vec<BindingPair<'f>> {
    let mut pairs = Vec::new();
    let mut i = 0;
    while i < items.len() {
        let name = items[i];
        match (items.get(i + 1), items.get(i + 2)) {
            (Some(bang), Some(value)) if hy.symbol(bang) == Some("!") => {
                pairs.push(BindingPair { name, bang: Some(bang), value: Some(value) });
                i += 3;
            }
            (value, _) => {
                pairs.push(BindingPair { name, bang: None, value: value.copied() });
                i += 2;
            }
        }
    }
    pairs
}

/// `(<- x e)` / `(<- x T e)` / `(<- e)`、末尾に `:absent F` があってもよい。
pub(super) fn bind_shape<'f>(hy: &Hy, items: &[&'f Form]) -> Option<BindShape<'f>> {
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

/// 呼びの頭の記号の byte の範囲(記号でなければ式の頭の位置)。
fn call_head_span(form: &Form) -> (usize, usize) {
    live(form)
        .and_then(|items| items.first().map(|h| (h.span.start, h.span.end)))
        .unwrap_or((form.span.start, form.span.start))
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
    match call_head(&reader.hy, value) {
        // 撃つ位置に分岐を置いた形(`(<- x (if c (A …) (B …)))`)— 撃つのは枝の式なので、枝ごとに呼びとして控える
        Some("if" | "when" | "unless" | "cond" | "do" | "match" | "let") => {
            let items = live(value).unwrap_or_default();
            let branches = match items.first().and_then(|h| reader.hy.symbol(h)) {
                Some("if" | "when" | "unless" | "let") => items.get(2..).unwrap_or_default(),
                Some("match") => items.get(2..).unwrap_or_default(),
                _ => items.get(1..).unwrap_or_default(),
            };
            for branch in branches {
                if branch.paren_items().is_some() {
                    push_site(reader, branch, absent_as_raise.clone(), flags, out);
                }
            }
        }
        Some(head) => out.push(Site {
            callee: reader.scope.qualify(head),
            absent_as_raise,
            absent_handled: flags.absent_handled,
            raise_handled: flags.raise_handled,
            head: call_head_span(value),
        }),
        None => {}
    }
}

// --- DOEFF127: :effects の宣言と推論の食い違い ------------------------------------------------

/// DOEFF127 の食い違い 1 つ(`:effects` を宣言した defk だけ — 宣言は任意(#800)なので、宣言の無い defk は対象外)。
/// 推論は見出しと同じ `World` の読み(1 か所)を使う。範囲は byte。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EffectMismatch {
    /// 推論で起こしているのに `:effects` に無い effect。場所 = その effect に至る撃った呼びの頭(本体の最初の 1 つ)。
    /// via = その呼びが defk を経由する時の、呼んだ定義の綴り(effect を直に撃っていれば None)。
    Undeclared {
        definition: String,
        effect: String,
        via: Option<String>,
        start: usize,
        end: usize,
    },
    /// `:effects` に在るのに推論では起こしていない effect。場所 = `:effects` の中のその名。
    Unused {
        definition: String,
        effect: String,
        start: usize,
        end: usize,
    },
}

impl EffectMismatch {
    /// 違反の場所の byte の範囲。
    pub fn span(&self) -> (usize, usize) {
        match self {
            EffectMismatch::Undeclared { start, end, .. }
            | EffectMismatch::Unused { start, end, .. } => (*start, *end),
        }
    }

    /// 定義の綴り。
    pub fn definition(&self) -> &str {
        match self {
            EffectMismatch::Undeclared { definition, .. }
            | EffectMismatch::Unused { definition, .. } => definition,
        }
    }

    /// effect の綴り(module を外した最後の区切り)。
    pub fn effect(&self) -> &str {
        match self {
            EffectMismatch::Undeclared { effect, .. } | EffectMismatch::Unused { effect, .. } => {
                effect
            }
        }
    }
}

/// 1 file の defk の `:effects` の宣言と推論の食い違いを判じる(表は `World::build` で作った物 — 1 file の実行では
/// この file の同じ中身を overlay にした物)。
pub fn effect_mismatches(world: &World, rel: &str, source: &str) -> Vec<EffectMismatch> {
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
        path: rel.to_string(),
    };
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(shape) = definition_shape(&reader.hy, form) else {
            continue;
        };
        if shape.kind != SignatureKind::Defk {
            continue;
        }
        let Some(list) = shape
            .contract
            .and_then(|c| reader.dict_value(c, ":effects"))
            .and_then(|l| l.bracket_items())
        else {
            continue;
        };
        let definition = reader.hy.text(shape.name).to_string();
        let declared: Vec<(String, &Form)> = list
            .iter()
            .filter(|i| matches!(i.node, Node::Symbol))
            .map(|i| (reader.scope.qualify(reader.hy.text(i)), i))
            .collect();
        let (inferred, opaque) = inferred_sites(world, &reader, &shape);
        for (effect, site) in &inferred {
            if declared.iter().any(|(q, _)| q == effect) {
                continue;
            }
            let via = (site.callee != *effect).then(|| {
                source
                    .get(site.head.0..site.head.1)
                    .unwrap_or("")
                    .to_string()
            });
            out.push(EffectMismatch::Undeclared {
                definition: definition.clone(),
                effect: last_segment(effect).to_string(),
                via,
                start: site.head.0,
                end: site.head.1,
            });
        }
        // 追えない呼びを撃っていれば推論の集合が欠けうるので、「宣言したのに起こしていない」は判じない
        for (qualified, symbol) in declared.iter().filter(|_| !opaque) {
            if !inferred.iter().any(|(e, _)| e == qualified) {
                out.push(EffectMismatch::Unused {
                    definition: definition.clone(),
                    effect: reader.hy.text(symbol).to_string(),
                    start: symbol.span.start,
                    end: symbol.span.end,
                });
            }
        }
    }
    out
}

/// defk 1 つの本体から推論した effect と、それぞれに至る最初の撃った呼び(本文の順)、追えない呼びを撃っているか。
/// DOEFF127 と DOEFF129 が同じ読みを使う(推論の読みを 2 か所に置かない)。
fn inferred_sites(world: &World, reader: &FileReader, shape: &DefinitionShape) -> (Vec<(String, Site)>, bool) {
    let mut sites = Vec::new();
    for item in &shape.body {
        collect_sites(reader, item, Flags::default(), &mut sites);
    }
    let mut inferred: Vec<(String, Site)> = Vec::new();
    let mut opaque = false;
    for site in sites {
        let outcome = world.site_outcome(&site);
        opaque |= outcome.opaque;
        for effect in outcome.effects {
            if !inferred.iter().any(|(e, _)| *e == effect) {
                inferred.push((effect, site.clone()));
            }
        }
    }
    (inferred, opaque)
}

// --- DOEFF129: 判断(judgment)は effect を起こさない ------------------------------------------------

/// DOEFF129 の破れ 1 つ — `:tags` で役 judgment を名乗った defk が起こす effect 1 つと、それに至る最初の撃った呼び。範囲は byte。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JudgmentEffect {
    pub definition: String,
    /// module まで含めた名。
    pub qualified: String,
    /// 撃った呼びが defk を経由する時の、呼んだ定義の綴り(effect を直に撃っていれば None)。
    pub via: Option<String>,
    pub start: usize,
    pub end: usize,
}

impl JudgmentEffect {
    /// effect の綴り(module を外した最後の区切り)。
    pub fn effect(&self) -> &str {
        last_segment(&self.qualified)
    }
}

/// 1 file の、`:tags` で役 judgment を名乗った defk が起こす effect を読む(表は `World::build` で作った物)。
/// 追えない呼びの先の effect は数えない(見えた effect だけで判じる — 無いと言い切らない)。
pub fn judgment_effects(world: &World, rel: &str, source: &str) -> Vec<JudgmentEffect> {
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
        path: rel.to_string(),
    };
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(shape) = definition_shape(&reader.hy, form) else {
            continue;
        };
        if shape.kind != SignatureKind::Defk {
            continue;
        }
        let role = shape
            .contract
            .and_then(|c| reader.dict_value(c, ":tags"))
            .and_then(|tags| string_entries(&reader, tags).remove("role"));
        if role.as_deref() != Some("judgment") {
            continue;
        }
        let definition = reader.hy.text(shape.name).to_string();
        let (inferred, _) = inferred_sites(world, &reader, &shape);
        for (qualified, site) in inferred {
            let via = (site.callee != qualified).then(|| source.get(site.head.0..site.head.1).unwrap_or("").to_string());
            out.push(JudgmentEffect {
                definition: definition.clone(),
                qualified,
                via,
                start: site.head.0,
                end: site.head.1,
            });
        }
    }
    out
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
    out.rewrites = super::call_view::file_rewrites(world, &reader, &forms);
    out.bodies = super::body_view::file_bodies(world, &reader, &forms, &out.bindings);
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
        effects: SignatureEffects {
            declared,
            inferred,
            complete: !summary.opaque,
            opaque: summary
                .opaque_calls
                .iter()
                .map(|q| last_segment(q).to_string())
                .collect::<BTreeSet<String>>()
                .into_iter()
                .collect(),
        },
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
        head: call_head_span(value),
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
                        None,
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
        Some("setv") => {
            for pair in children[1..].chunks(2) {
                if let [name, value] = pair {
                    if matches!(name.node, Node::Symbol) {
                        let typed = value_type(world, reader, value, flags);
                        out.push(binding(
                            reader,
                            BindingForm::Setv,
                            None,
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
        Some(word @ ("val" | "var" | "lazy" | "session")) => {
            // `(lazy val x e)`・`(session var x e)` は前の語の次が val / var
            let (modifier, word, start) = match BindingModifier::of(word) {
                Some(modifier) => (Some(modifier), children.get(1).and_then(|w| reader.hy.symbol(w)).unwrap_or(""), 2),
                None => (None, word, 1),
            };
            let kind = [("val", BindingForm::Val), ("var", BindingForm::Var)]
                .into_iter()
                .find(|(spelled, _)| *spelled == word)
                .map(|(_, kind)| kind);
            if let Some(kind) = kind {
                for pair in binding_pairs(&reader.hy, &children[start.min(children.len())..]) {
                    let (name, Some(value)) = (pair.name, pair.value) else { continue };
                    if !matches!(name.node, Node::Symbol) {
                        continue;
                    }
                    let typed = match pair.bang {
                        Some(_) => performed_type(world, reader, value, None, flags),
                        None => value_type(world, reader, value, flags),
                    };
                    let typed = match typed {
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
                    out.push(binding(reader, kind, modifier, form, head_form, name, None, Some(value), typed));
                }
            }
        }
        Some(":=") => {
            let pair = binding_pairs(&reader.hy, &children[1..]).into_iter().next();
            if let Some(BindingPair { name, value: Some(value), .. }) = pair {
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
                        None,
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
    modifier: Option<BindingModifier>,
    whole: &Form,
    head: Option<&Form>,
    name: &Form,
    annotation: Option<&Form>,
    value: Option<&Form>,
    typed: Typed,
) -> Binding {
    Binding {
        form,
        modifier,
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
        assert!(sig.effects.complete, "撃つ呼びが無い defk は推論が追いきれている");
    }

    #[test]
    fn docstring_before_contract_and_untrackable_calls_are_read() {
        // agora-controllers の sim-part(controllers/agora_sim/full_system.hy)と run-emulated-with の形
        let core = r#"
(defk sim-part [services handlers]
  "部品の値。"
  {:pre [(: services list) (: handlers (| Callable None))]
   :post [(: % dict)]
   :tags {:context "turn" :role "judgment"}}
  {"services" services "handlers" handlers})
(defk run-emulated-with [agora parts]
  {:pre [(: agora int) (: parts list)] :post [(: % list)] :tags {:context "turn" :role "entry"}}
  (<- results list (with_handlers (! (helper parts)) (run agora)))
  results)
"#;
        let got = read(&[("core/sim.hy", core)], "core/sim.hy");
        let sim = &got.signatures[0];
        let contract = sim.contract_range.unwrap();
        assert_eq!((contract.start.line, contract.end.line), (3, 5), "docstring が先でも契約の辞書を見つける");
        assert_eq!(sim.params[1].type_ref.as_ref().map(name_of).as_deref(), Some("Callable | None"));
        assert_eq!(sim.tags.get("role").map(String::as_str), Some("judgment"));
        assert!(sim.effects.complete);
        assert!(sim.effects.opaque.is_empty(), "追えない呼びが無ければ名の列は空");
        let run = &got.signatures[1];
        assert!(run.effects.inferred.is_empty());
        assert!(!run.effects.complete, "追えない呼び with_handlers を撃つので、推論が空でも「effect なし」とは言えない");
        assert_eq!(
            run.effects.opaque,
            vec!["helper".to_string(), "with_handlers".to_string()],
            "何が追えなかったか(撃った呼びの頭の名・名の順・重複なし)をエディタへ出す"
        );
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

    /// 根に file を並べて表を作り、1 file の DOEFF127 の食い違いを読む。
    fn mismatches(files: &[(&str, &str)], target: &str) -> Vec<EffectMismatch> {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        effect_mismatches(&world, target, source)
    }

    #[test]
    fn effect_mismatches_point_at_the_call_and_the_declaration() {
        let core = r#"
(import intent.rows [ReadRow PutRow Row])
(import doeff_core_effects [Delay])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row (ReadRow id))
  row)
(defk store [id]
  {:pre [(: id str)] :post [(: % bool)] :effects [PutRow Delay]}
  (val row (! (fetch id)))
  (<- ok (PutRow row))
  ok)
(defk undeclared-ok [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- row (ReadRow id))
  row)
(defk waits [s]
  {:pre [(: s int)] :post [(: % int)] :effects [Delay]}
  (<- (Delay s))
  s)
(defk branches [id flag]
  {:pre [(: id str) (: flag bool)] :post [(: % Row)] :effects [ReadRow PutRow]}
  (<- got (if flag (ReadRow id) (PutRow id)))
  got)
(defk through-helper [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row (read-typed id))
  row)
"#;
        let got = mismatches(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let at = |m: &EffectMismatch| &core[m.span().0..m.span().1];
        // 出ない: 宣言なしの undeclared-ok・一致の fetch と waits(外の effect Delay)・枝の両方を撃つ branches・
        // 追えない呼び read-typed を撃つ through-helper(起こしていないと言えない)
        assert_eq!(got.len(), 2, "{:?}", got);
        match &got[0] {
            EffectMismatch::Undeclared {
                definition,
                effect,
                via,
                ..
            } => {
                assert_eq!(
                    (definition.as_str(), effect.as_str(), via.as_deref()),
                    ("store", "ReadRow", Some("fetch"))
                );
                assert_eq!(at(&got[0]), "fetch", "場所は defk を経由した呼びの頭");
            }
            other => panic!("{:?}", other),
        }
        match &got[1] {
            EffectMismatch::Unused {
                definition, effect, ..
            } => {
                assert_eq!((definition.as_str(), effect.as_str()), ("store", "Delay"));
                assert_eq!(at(&got[1]), "Delay", "場所は :effects の中の名");
            }
            other => panic!("{:?}", other),
        }
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

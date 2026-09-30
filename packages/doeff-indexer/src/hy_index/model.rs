//! `hy-index` の出力の型 — 契約 `hy-index-contract.md`(版 1)・`hy-index-contract-v2.md`(版 2)・`hy-index-contract-v3.md`(版 3 = 生の副作用の証拠)
//! と版 4(完全修飾名 = 定義の `qualified_name`・呼び出しの `target`)・版 5(宣言した effect・引数と答えの型・型でない契約・
//! effect 節が解く effect)・版 6(定義の decorator)— どれも SPECIFICATION.md の Hy Index の節 — の JSON の形そのもの。
//! JSON への変換は CLI の出力の 1 か所(`main.rs`)だけが行う。

use serde::{Deserialize, Serialize};

pub use super::position::{Position, Range};
pub use super::raw_catalog::RawCategory;

/// 契約の版。形を変える時は契約と一緒に上げる。
pub const CONTRACT_VERSION: u32 = 6;

/// `hy-index` の出力の全体。
#[derive(Debug, Clone, Serialize)]
pub struct HyIndex {
    pub version: u32,
    pub root: String,
    pub files: Vec<HyFileIndex>,
    /// 経由の証拠を計算したか(`--root` の全体の実行だけが計算する。1 file や `--file` の実行は "not-computed")。
    pub raw_via: RawViaScope,
    /// `--raw-catalog-extra` の中で読めなかった値の理由。
    pub raw_catalog_problems: Vec<String>,
}

/// 経由の証拠の計算の範囲。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RawViaScope {
    #[serde(rename = "computed")]
    Computed,
    #[serde(rename = "not-computed")]
    NotComputed,
}

/// 証拠の強さ — 強い = import を通した名前・組み込み、弱い = method 名だけ。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RawStrength {
    #[serde(rename = "strong")]
    Strong,
    #[serde(rename = "weak")]
    Weak,
}

/// 何で見つけたか。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RawEvidenceKind {
    #[serde(rename = "name")]
    Name,
    #[serde(rename = "builtin")]
    Builtin,
    #[serde(rename = "method")]
    Method,
}

/// 生の副作用の証拠 1 件(参照 1 つの位置)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawEvidence {
    pub category: RawCategory,
    /// import を通した完全な名前(`httpx.post`)か、組み込み・method(`.read_text`)の名前。
    pub name: String,
    pub kind: RawEvidenceKind,
    pub strength: RawStrength,
    /// 証拠の在る file(直接なら定義と同じ file)。
    pub path: String,
    pub range: Range,
}

/// 経路の 1 段 — 呼んだ定義。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawStep {
    pub path: String,
    /// その file の `definitions` の添字。
    pub index: usize,
    pub name: String,
}

/// 呼ぶ定義を通した証拠。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawVia {
    pub through: Vec<RawStep>,
    pub evidence: RawEvidence,
}

/// 定義 1 つの判定。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawMark {
    pub direct: Vec<RawEvidence>,
    pub via: Vec<RawVia>,
}


/// 1 つの file の索引。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HyFileIndex {
    pub path: String,
    pub module: String,
    pub definitions: Vec<Definition>,
    pub imports: Vec<Import>,
    pub references: Vec<Reference>,
    pub calls: Vec<Call>,
    pub errors: Vec<String>,
}

/// top level の定義と、その直下の入れ子の定義。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Definition {
    pub name: String,
    pub mangled: String,
    /// 完全修飾名 — module + 入れ物(在れば)+ 名、どの区切りも mangle した綴り(版 4)。呼び出しの `target` と
    /// 文字列で一致させて呼び先・呼び手を引く鍵。module は file の索引が決めるので `qualify::link` が埋める。
    pub qualified_name: String,
    pub kind: DefinitionKind,
    pub range: Range,
    pub full_range: Range,
    pub container: Option<String>,
    pub docstring: Option<String>,
    pub params: Vec<String>,
    /// defclass / defrecord の基底の記号(書かれたとおり、dotted も 1 つ)。それ以外の kind は常に空。
    pub bases: Vec<String>,
    /// 生の副作用の証拠(版 3 — 規則の判定ではなく事実。判定の正本は linter)。
    pub raw: RawMark,
    /// 定義が名乗ったタグ(版 3 への追加 — 契約の辞書の :tags と defeffect の :tags。文字列の値の鍵だけ。無ければ null)。
    pub tags: Option<std::collections::BTreeMap<String, String>>,
    /// defrecord の頭の辞書の :check の式(書かれたとおりの綴り・書いた順)。頭の辞書を持つ defrecord だけが持ち
    /// (:check が無ければ空の列)、それ以外の定義は欄ごと出さない(版 3 への追加)。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub checks: Option<Vec<String>>,
    /// 宣言した effect(版 5)— 契約の辞書の `:effects [E …]` の名を書いた順に。`:effects` を書いていなければ null
    /// (`[]` は「effect を起こさない」の宣言)。推論した effect は持たない(正本は doeff-linter の signatures・DOEFF127 が
    /// 宣言と推論の一致を検める)。
    pub effects: Option<Vec<NameRef>>,
    /// 引数の型(版 5)— defk 等は `:pre` の `(: 引数 型)`、defeffect は `:fields` の `(: 欄 型)`、defrecord は欄の `#^ 型`。
    /// 型を書いた引数だけ・引数の順。
    pub param_types: Vec<ParamType>,
    /// 答えの型(版 5)— `:post` の `(: % 型)`、無ければ名の `#^ 型`。defeffect は `:answer`。無ければ null。
    pub answer_type: Option<TypeNote>,
    /// 型でない契約の述語(版 5)— `:pre` / `:post` のうち `(: 引数 型)` / `(: % 型)` でない物を書かれたとおりに。
    pub contracts: Vec<ContractClause>,
    /// effect 節が解く effect(版 5)— kind が `effect-clause` の定義だけが持ち、他は null。`name` は節の頭の綴り、
    /// `target` は effect の完全修飾名(解く handler は、この target が一致する effect 節の container の定義)。
    pub handles: Option<NameRef>,
    /// 定義の decorator(版 6)— `(defclass [d …] Name …)` / `(defn [d …] name …)` の `[…]` の各要素を書いた順に。
    /// 綴りは書かれたとおりで、呼びの形は外側の括弧を外し(`(dataclass :frozen True)` → `dataclass :frozen True`)、
    /// 文字列の外の空白の連なりは 1 つに詰める。decorator の無い定義は空の列。
    pub decorators: Vec<String>,
}

/// 書かれた名と、その完全修飾名(版 5 — 呼び出しの `target` と同じ名前の解決。解けなければ null)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NameRef {
    pub name: String,
    pub target: Option<String>,
}

/// 型の注記 1 つ(版 5)— 書かれた綴りと、その中の名(`|`・`of`・`get` の構文を除く記号を書いた順・重ねない)。
/// 型の意味の読み方(Union・Maybe・Raise)は doeff-linter の signatures が正本で、索引は書かれた事実だけを持つ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypeNote {
    pub text: String,
    pub names: Vec<NameRef>,
}

/// 引数 1 つの型(版 5)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParamType {
    pub name: String,
    #[serde(rename = "type")]
    pub type_note: TypeNote,
}

/// 契約の述語がどちらの側か。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContractSide {
    #[serde(rename = "pre")]
    Pre,
    #[serde(rename = "post")]
    Post,
}

/// 型でない契約の述語 1 つ(版 5)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractClause {
    pub side: ContractSide,
    pub text: String,
}

/// 定義の種類(契約の kind の一覧ちょうど)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DefinitionKind {
    #[serde(rename = "defn")]
    Defn,
    #[serde(rename = "defn/a")]
    DefnAsync,
    #[serde(rename = "defmacro")]
    Defmacro,
    #[serde(rename = "defk")]
    Defk,
    #[serde(rename = "deff")]
    Deff,
    #[serde(rename = "defp")]
    Defp,
    #[serde(rename = "defpp")]
    Defpp,
    #[serde(rename = "fnk-binding")]
    FnkBinding,
    #[serde(rename = "defclass")]
    Defclass,
    #[serde(rename = "defrecord")]
    Defrecord,
    #[serde(rename = "defenum")]
    Defenum,
    #[serde(rename = "enum-member")]
    EnumMember,
    #[serde(rename = "field")]
    Field,
    #[serde(rename = "method")]
    Method,
    #[serde(rename = "defhandler")]
    Defhandler,
    #[serde(rename = "defeffect")]
    Defeffect,
    #[serde(rename = "effect-clause")]
    EffectClause,
    #[serde(rename = "deftest")]
    Deftest,
    #[serde(rename = "defadr")]
    Defadr,
    #[serde(rename = "defsemgrep")]
    Defsemgrep,
    #[serde(rename = "law")]
    Law,
    /// doeff-cluster の系の宣言(`(defsystem 名 [引数] 本体)` — 中の呼び出しの持ち主。agora-redesign #1143)。
    #[serde(rename = "defsystem")]
    Defsystem,
    #[serde(rename = "defpipeline")]
    Defpipeline,
    #[serde(rename = "defworkflow")]
    Defworkflow,
    #[serde(rename = "defphase")]
    Defphase,
    #[serde(rename = "defmcp-tool")]
    DefmcpTool,
    #[serde(rename = "deftype")]
    Deftype,
    #[serde(rename = "defmain")]
    Defmain,
    #[serde(rename = "variable")]
    Variable,
}

impl DefinitionKind {
    /// 契約の綴り(JSON の値と同じ)を返す — テストと照合の道具のため。
    pub fn as_str(self) -> &'static str {
        match self {
            DefinitionKind::Defn => "defn",
            DefinitionKind::DefnAsync => "defn/a",
            DefinitionKind::Defmacro => "defmacro",
            DefinitionKind::Defk => "defk",
            DefinitionKind::Deff => "deff",
            DefinitionKind::Defp => "defp",
            DefinitionKind::Defpp => "defpp",
            DefinitionKind::FnkBinding => "fnk-binding",
            DefinitionKind::Defclass => "defclass",
            DefinitionKind::Defrecord => "defrecord",
            DefinitionKind::Defenum => "defenum",
            DefinitionKind::EnumMember => "enum-member",
            DefinitionKind::Field => "field",
            DefinitionKind::Method => "method",
            DefinitionKind::Defhandler => "defhandler",
            DefinitionKind::Defeffect => "defeffect",
            DefinitionKind::EffectClause => "effect-clause",
            DefinitionKind::Deftest => "deftest",
            DefinitionKind::Defadr => "defadr",
            DefinitionKind::Defsemgrep => "defsemgrep",
            DefinitionKind::Law => "law",
            DefinitionKind::Defsystem => "defsystem",
            DefinitionKind::Defpipeline => "defpipeline",
            DefinitionKind::Defworkflow => "defworkflow",
            DefinitionKind::Defphase => "defphase",
            DefinitionKind::DefmcpTool => "defmcp-tool",
            DefinitionKind::Deftype => "deftype",
            DefinitionKind::Defmain => "defmain",
            DefinitionKind::Variable => "variable",
        }
    }
}

/// `(import …)` / `(require …)` の 1 つの名前(または module だけの import)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Import {
    pub module: String,
    pub name: Option<String>,
    pub alias: Option<String>,
    pub range: Range,
    pub is_require: bool,
}

/// 記号の出現の 1 区切り(dotted の `a.b.c` は 3 件)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Reference {
    pub name: String,
    pub mangled: String,
    pub qualifier: Option<String>,
    pub range: Range,
    /// 値の上の属性・method の名前として書かれた(`(.m x)` の `m`・`x.m` の `m`・`(. obj m)` / `(. obj (m …))` の `m`)。
    /// false は名前の引き(局所の束縛・定義・import・組み込み)。生の副作用の method の証拠はこの区切りだけから取る —
    /// 局所の束縛の名 `stat` を `.stat` と読まないため(agora-redesign #798)。索引の JSON の契約(版 1)には出さない。
    #[serde(skip)]
    pub member: bool,
    /// 名指した先の完全修飾名 — 呼び出しの `target` と同じ名前の解決(`qualify.rs`)。handler を値として渡す所
    /// (`(with-handlers [os-file-handler] …)`)は呼び出しにならないので、名指しの先はここで引く(agora-redesign #1140)。
    /// 解決できなければ None。索引の JSON の契約(版 1)には出さない。
    #[serde(skip)]
    pub target: Option<String>,
    /// 型注釈・pattern の中の参照。値の実行としては数えない。
    #[serde(skip)]
    pub type_only: bool,
    /// 値を検めるだけの名指し — 比べの form(`is`・`is-not`・`=`・`!=`・`in`・`not-in`)と `assert` の直接の被演算子(literal の
    /// 列・辞書・組・集合の中と、被演算子の dotted の属性の読み `(= f.__doeff_needs__ …)` を含む)。名指した値を呼ばず・被せず・
    /// 他の定義へ渡さないので、届く辺(doeff-linter の定義の図)にしない(agora-redesign #1581)。索引の JSON の契約(版 1)には出さない。
    #[serde(skip)]
    pub inspected: bool,
}

/// 呼び出しへ渡された値の構文上の形。式の評価結果は推測しない。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ArgumentValue {
    LiteralNone,
    Explicit,
    Unpacked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CallArgument {
    pub keyword: Option<String>,
    pub value: ArgumentValue,
}

/// 呼び出しの 1 つ(`(` の直後の記号)。effect・handler・defk の間を行き来するためのもの。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Call {
    /// 頭の記号の最後の区切り(書かれたとおり)。
    pub callee: String,
    pub mangled: String,
    /// dotted の前の区切りを `.` で繋いだもの。無ければ null。
    pub qualifier: Option<String>,
    /// 頭の記号の最後の区切りの位置。
    pub range: Range,
    /// 呼び出しの頭から最後の引数の終わりまで(引数の中の参照を引くため — agora-redesign #1279)。索引の JSON の契約には出さない。
    #[serde(skip)]
    pub form_range: Range,
    /// 直接の引数として渡した keyword の綴り(`(f a :k v)` の `:k`)。索引の JSON の契約には出さない。
    #[serde(skip)]
    pub keywords: Vec<String>,
    /// 直接の引数の形。生の I/O の判定で明示された引数と省略を区別する。
    #[serde(skip)]
    pub arguments: Vec<CallArgument>,
    /// この呼び出しを含む最も内側の定義の、同じ file の `definitions` の添字。top level の式なら null。
    pub caller: Option<usize>,
    /// `<-` で撃たれている(または `yield` / `yield-from` の直下)なら true。
    pub performed: bool,
    /// 呼び先の完全修飾名(版 4)— この file の定義と import だけで決める名前の解決の結果(`qualify.rs`)。
    /// 解決できない(組み込み・special form・局所の束縛・引数・値の上の属性・module そのもの)なら null。
    /// Hy の定義とは限らない — 索引の `qualified_name` に一致すれば Hy の定義。
    pub target: Option<String>,
}

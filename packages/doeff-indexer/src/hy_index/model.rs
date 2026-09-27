//! `hy-index` の出力の型 — 契約 `hy-index-contract.md`(版 1)・`hy-index-contract-v2.md`(版 2)・`hy-index-contract-v3.md`(版 3 = 生の副作用の証拠)の JSON の形そのもの。
//! JSON への変換は CLI の出力の 1 か所(`main.rs`)だけが行う。

use serde::Serialize;

pub use super::position::{Position, Range};
pub use super::raw_catalog::RawCategory;

/// 契約の版。形を変える時は契約と一緒に上げる。
pub const CONTRACT_VERSION: u32 = 3;

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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RawStrength {
    #[serde(rename = "strong")]
    Strong,
    #[serde(rename = "weak")]
    Weak,
}

/// 何で見つけたか。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RawEvidenceKind {
    #[serde(rename = "name")]
    Name,
    #[serde(rename = "builtin")]
    Builtin,
    #[serde(rename = "method")]
    Method,
}

/// 生の副作用の証拠 1 件(参照 1 つの位置)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
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
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RawStep {
    pub path: String,
    /// その file の `definitions` の添字。
    pub index: usize,
    pub name: String,
}

/// 呼ぶ定義を通した証拠。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RawVia {
    pub through: Vec<RawStep>,
    pub evidence: RawEvidence,
}

/// 定義 1 つの判定。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct RawMark {
    pub direct: Vec<RawEvidence>,
    pub via: Vec<RawVia>,
}


/// 1 つの file の索引。
#[derive(Debug, Clone, Serialize)]
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
#[derive(Debug, Clone, Serialize)]
pub struct Definition {
    pub name: String,
    pub mangled: String,
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
}

/// 定義の種類(契約の kind の一覧ちょうど)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
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
#[derive(Debug, Clone, Serialize)]
pub struct Import {
    pub module: String,
    pub name: Option<String>,
    pub alias: Option<String>,
    pub range: Range,
    pub is_require: bool,
}

/// 記号の出現の 1 区切り(dotted の `a.b.c` は 3 件)。
#[derive(Debug, Clone, Serialize)]
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
}

/// 呼び出しの 1 つ(`(` の直後の記号)。effect・handler・defk の間を行き来するためのもの。
#[derive(Debug, Clone, Serialize)]
pub struct Call {
    /// 頭の記号の最後の区切り(書かれたとおり)。
    pub callee: String,
    pub mangled: String,
    /// dotted の前の区切りを `.` で繋いだもの。無ければ null。
    pub qualifier: Option<String>,
    /// 頭の記号の最後の区切りの位置。
    pub range: Range,
    /// この呼び出しを含む最も内側の定義の、同じ file の `definitions` の添字。top level の式なら null。
    pub caller: Option<usize>,
    /// `<-` で撃たれている(または `yield` / `yield-from` の直下)なら true。
    pub performed: bool,
}

//! `hy-index` の出力の型 — 契約 `hy-index-contract.md`(版 1)と `hy-index-contract-v2.md`(版 2 の追加分)の JSON の形そのもの。
//! JSON への変換は CLI の出力の 1 か所(`main.rs`)だけが行う。

use serde::Serialize;

pub use super::position::{Position, Range};

/// 契約の版。形を変える時は契約と一緒に上げる。
pub const CONTRACT_VERSION: u32 = 2;

/// `hy-index` の出力の全体。
#[derive(Debug, Clone, Serialize)]
pub struct HyIndex {
    pub version: u32,
    pub root: String,
    pub files: Vec<HyFileIndex>,
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

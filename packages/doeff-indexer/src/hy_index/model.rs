//! Output types of `hy-index` — the exact JSON shape of contract `hy-index-contract.md` (version 1), `hy-index-contract-v2.md` (version 2), `hy-index-contract-v3.md` (version 3 = raw side-effect evidence)
//! and version 4 (fully qualified names = a definition's `qualified_name` and a call's `target`), version 5 (declared effects, argument and answer types, non-type contracts,
//! effects resolved by effect clauses) and version 6 (definition decorators), all in the Hy Index section of SPECIFICATION.md.
//! Conversion to JSON happens in exactly one place, the CLI output (`main.rs`).

use serde::{Deserialize, Serialize};

pub use super::position::{Position, Range};
pub use super::raw_catalog::RawCategory;

/// Contract version. Bump it together with the contract when the shape changes.
pub const CONTRACT_VERSION: u32 = 6;

/// The whole output of `hy-index`.
#[derive(Debug, Clone, Serialize)]
pub struct HyIndex {
    pub version: u32,
    pub root: String,
    pub files: Vec<HyFileIndex>,
    /// Whether via-evidence was computed (only a full `--root` run computes it; a single-file or `--file` run reports "not-computed").
    pub raw_via: RawViaScope,
    /// Reasons for values that could not be read inside `--raw-catalog-extra`.
    pub raw_catalog_problems: Vec<String>,
}

/// Scope of the via-evidence computation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RawViaScope {
    #[serde(rename = "computed")]
    Computed,
    #[serde(rename = "not-computed")]
    NotComputed,
}

/// Strength of evidence — strong = a name resolved through an import, or a builtin; weak = a method name only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RawStrength {
    #[serde(rename = "strong")]
    Strong,
    #[serde(rename = "weak")]
    Weak,
}

/// How it was found.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RawEvidenceKind {
    #[serde(rename = "name")]
    Name,
    #[serde(rename = "builtin")]
    Builtin,
    #[serde(rename = "method")]
    Method,
}

/// One piece of raw side-effect evidence (the position of one reference).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawEvidence {
    pub category: RawCategory,
    /// The full name resolved through an import (`httpx.post`), or the name of a builtin or method (`.read_text`).
    pub name: String,
    pub kind: RawEvidenceKind,
    pub strength: RawStrength,
    /// The file holding the evidence (the same file as the definition when direct).
    pub path: String,
    pub range: Range,
}

/// One step of a path — the called definition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawStep {
    pub path: String,
    /// Index into that file's `definitions`.
    pub index: usize,
    pub name: String,
}

/// Evidence reached through a called definition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawVia {
    pub through: Vec<RawStep>,
    pub evidence: RawEvidence,
}

/// Verdict for one definition.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawMark {
    pub direct: Vec<RawEvidence>,
    pub via: Vec<RawVia>,
}


/// Index of one file.
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

/// Top-level definitions, and the nested definitions directly beneath them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Definition {
    pub name: String,
    pub mangled: String,
    /// Fully qualified name — module + container (if any) + name, every separator mangled (version 4). The key for finding callees and callers
    /// by string equality with a call's `target`. The module is decided by the file's index, so `qualify::link` fills it in.
    pub qualified_name: String,
    pub kind: DefinitionKind,
    pub range: Range,
    pub full_range: Range,
    pub container: Option<String>,
    pub docstring: Option<String>,
    pub params: Vec<String>,
    /// Base symbols of a defclass / defrecord (as written; a dotted one counts as one). Always empty for other kinds.
    pub bases: Vec<String>,
    /// Raw side-effect evidence (version 3 — a fact, not a rule verdict. The source of truth for verdicts is the linter).
    pub raw: RawMark,
    /// Tags the definition claims (added in version 3 — `:tags` in the contract dict and `:tags` in defeffect; only keys with string values. null if absent).
    pub tags: Option<std::collections::BTreeMap<String, String>>,
    /// The `:check` expressions in a defrecord's head dict (spelled as written, in written order). Only a defrecord that has a head dict carries this
    /// (empty list if there is no `:check`); other definitions omit the field entirely (added in version 3).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub checks: Option<Vec<String>>,
    /// Declared effects (version 5) — the names in the contract dict's `:effects [E …]`, in written order. null if `:effects` is not written
    /// (`[]` is the declaration "raises no effect"). Inferred effects are not held (the source of truth is doeff-linter's signatures; DOEFF127 checks
    /// that declaration and inference agree).
    pub effects: Option<Vec<NameRef>>,
    /// Argument types (version 5) — for defk and similar, `(: arg type)` in `:pre`; for defeffect, `(: field type)` in `:fields`; for defrecord, `#^ type` on a field.
    /// Only arguments with a written type, in argument order.
    pub param_types: Vec<ParamType>,
    /// Answer type (version 5) — `(: % type)` in `:post`, or else `#^ type` on the name. For defeffect, `:answer`. null if absent.
    pub answer_type: Option<TypeNote>,
    /// Predicates of non-type contracts (version 5) — entries of `:pre` / `:post` that are not `(: arg type)` / `(: % type)`, as written.
    pub contracts: Vec<ContractClause>,
    /// The effect resolved by an effect clause (version 5) — held only by definitions whose kind is `effect-clause`, null for others. `name` is the spelling at the head of the clause,
    /// `target` is the effect's fully qualified name (the handler that resolves it is the definition containing the effect clause whose target matches).
    pub handles: Option<NameRef>,
    /// Decorators of the definition (version 6) — each element of the `[…]` in `(defclass [d …] Name …)` / `(defn [d …] name …)`, in written order.
    /// Spelling is as written; a call form drops its outer parentheses (`(dataclass :frozen True)` -> `dataclass :frozen True`),
    /// and runs of whitespace outside strings are collapsed to one. A definition without decorators gets an empty list.
    pub decorators: Vec<String>,
}

/// A written name and its fully qualified name (version 5 — the same name resolution as a call's `target`; null if unresolved).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NameRef {
    pub name: String,
    pub target: Option<String>,
}

/// One type annotation (version 5) — the spelling as written, and the names inside it (symbols other than the `|`, `of` and `get` syntax, in written order, without duplicates).
/// How to read the meaning of types (Union, Maybe, Raise) is owned by doeff-linter's signatures; the index holds only the facts as written.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypeNote {
    pub text: String,
    pub names: Vec<NameRef>,
}

/// The type of one argument (version 5).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParamType {
    pub name: String,
    #[serde(rename = "type")]
    pub type_note: TypeNote,
}

/// Which side a contract predicate belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContractSide {
    #[serde(rename = "pre")]
    Pre,
    #[serde(rename = "post")]
    Post,
}

/// One predicate of a non-type contract (version 5).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractClause {
    pub side: ContractSide,
    pub text: String,
}

/// Kind of definition (exactly the list of kinds in the contract).
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
    /// Declaration of a doeff-cluster system (`(defsystem name [args] body)` — the owner of the calls inside).
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
    /// Returns the contract spelling (same as the JSON value) — a tool for tests and matching.
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

/// One name of `(import …)` / `(require …)` (or an import of a module alone).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Import {
    pub module: String,
    pub name: Option<String>,
    pub alias: Option<String>,
    pub range: Range,
    pub is_require: bool,
}

/// One separator-delimited segment of a symbol occurrence (a dotted `a.b.c` yields 3).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Reference {
    pub name: String,
    pub mangled: String,
    pub qualifier: Option<String>,
    pub range: Range,
    /// Written as an attribute or method name on a value (`m` in `(.m x)`, `m` in `x.m`, `m` in `(. obj m)` / `(. obj (m …))`).
    /// false means a name lookup (local binding, definition, import, builtin). Method evidence for raw side effects is taken only from this segment —
    /// so that a local binding named `stat` is not read as `.stat`. Not emitted in the index JSON contract (version 1).
    #[serde(skip)]
    pub member: bool,
    /// Fully qualified name of the target named — the same name resolution as a call's `target` (`qualify.rs`). Where a handler is passed as a value
    /// (`(with-handlers [os-file-handler] …)`) it is not a call, so the named target is looked up here.
    /// None if unresolved. Not emitted in the index JSON contract (version 1).
    #[serde(skip)]
    pub target: Option<String>,
    /// A reference inside a type annotation or pattern. Not counted as running a value.
    #[serde(skip)]
    pub type_only: bool,
    /// A reference that merely inspects a value — comparison forms (`is`, `is-not`, `=`, `!=`, `in`, `not-in`) and the direct operands of `assert` (including those inside literal
    /// lists, dicts, tuples and sets, and dotted attribute reads of operands `(= f.__doeff_needs__ …)`). The named value is not called, wrapped, or
    /// passed to another definition, so it does not become a reaching edge (in doeff-linter's definition graph). Not emitted in the index JSON contract (version 1).
    #[serde(skip)]
    pub inspected: bool,
    /// When called as a method (`m` at the head of `(.m receiver …)`, `m` at the head of `(x.m …)`), the number of arguments excluding the receiver (a keyword and its value count as one).
    /// None unless at the head of a call. Used so that method evidence for raw side effects can be told apart from a same-named method of another type by the catalog's argument limit
    /// (string `(.replace s "/" "_")` versus `Path.replace(target)`). Not emitted in the index JSON contract (version 1).
    #[serde(skip)]
    pub call_arity: Option<usize>,
}

/// The syntactic shape of a value passed to a call. The result of evaluating the expression is not guessed.
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

/// One call (the symbol right after `(`). Used to move among effects, handlers and defk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Call {
    /// The last segment of the head symbol (as written).
    pub callee: String,
    pub mangled: String,
    /// The preceding segments of a dotted name joined with `.`. null if none.
    pub qualifier: Option<String>,
    /// Position of the last segment of the head symbol.
    pub range: Range,
    /// From the head of the call to the end of the last argument (for looking up references inside the arguments). Not emitted in the index JSON contract.
    #[serde(skip)]
    pub form_range: Range,
    /// Spelling of keywords passed as direct arguments (`:k` in `(f a :k v)`). Not emitted in the index JSON contract.
    #[serde(skip)]
    pub keywords: Vec<String>,
    /// Shapes of the direct arguments. Distinguishes explicit arguments from omitted ones in raw I/O verdicts.
    #[serde(skip)]
    pub arguments: Vec<CallArgument>,
    /// Index, in the same file's `definitions`, of the innermost definition containing this call. null for a top-level expression.
    pub caller: Option<usize>,
    /// true if fired by `<-` (or directly under `yield` / `yield-from`).
    pub performed: bool,
    /// Fully qualified name of the callee (version 4) — the result of name resolution decided from this file's definitions and imports only (`qualify.rs`).
    /// null if unresolvable (builtin, special form, local binding, argument, attribute on a value, the module itself).
    /// Not necessarily a Hy definition — it is a Hy definition if it matches a `qualified_name` in the index.
    pub target: Option<String>,
}

//! `doeff-linter fix defn-to-defk` — a transform that rewrites Hy defn into defk and rewrites callers within the repo to `<-` / `!`.
//!
//! Origin: linter fixes were supposed to be simple but were taking far too long.
//! An LLM worker was hand-rewriting callers file by file, so the mechanical rewriting moves into this transform, and the worker fixes only the
//! "needs judgement" items the transform could not fix (a reversible decision by the coordinator).
//!
//! It reads with the same doeff-indexer reader as the linter (form tree and byte ranges), and rewrites only by replacing byte ranges
//! (the author's line breaks, comments and indentation are kept). After the transform, it runs the DOEFF126 (bare defk call) check with the same reader and reports what remains.
//!
//! Rewriting definitions:
//! - `(defn #^ R name [#^ T x …] "doc" body)` → `(defk name [x …] {:pre [(: x T) …] :post [(: % R)]} "doc" body)`. defk requires the types of all arguments in :pre and the
//!   return type in :post (the doeff-hy macro checks this), so they are built from the existing `#^` annotations. Items with no annotation, `object` or `Any`
//!   cannot be built (needs judgement). A parametrized type such as `(get list str)` is not accepted by isinstance, so the contract gets the base type (`list`)
//!   and the argument's `#^` annotation is kept (the annotation carries the static type). An argument whose default is None becomes `(| T None)`, and `float` becomes `(| float int)`
//!   (callers commonly pass an int — the annotation was never checked).
//! - If a `{:pre … :post …}` dict already exists it is kept and only the missing keys are added. `:tags` is not added (another rule, DOEFF112, looks at it).
//!
//! Rewriting callers (all Hy files in the repo):
//! - `(setv x (f a))`, `(val x (f a))` (binding to a single name) → `(<- x (f a))`.
//! - Any other call in the middle or at the end of an expression → `(! (f a))` (also at the end — a defk body returning a Program fails on the :post type. Measured 2026-09-28).
//! - Places that cannot be rewritten (comprehensions, inside plain functions (defn, deff, fn), methods, module top level, inside macros, inside `->`, places that pass the function itself as a value,
//!   references from Python, import aliases, files under excluded paths) are not rewritten and are listed under "needs judgement" (the worker fixes them by hand — coordinator
//!   instruction 2026-09-28). With `--strict`, a function that has even one such place is not converted (so callers are not left broken). A caller inside a function can be
//!   rewritten if that function is also converted (decided by a fixed point).
//! - A defn that is already called in the form `(<- x (f a))`, `(! (f a))` or `(run (f a))` is an assembly that returns a Program, so it is not converted (needs judgement).
//!
//! Forms not targeted (the report's "skipped" list): dunder names, `:async`, items with a deff reason marker (the setting
//! `definitions.deff_reason_marker`) or "defn のまま" on the definition line or the comment line just before it, anything inside `eval-and-compile`, class methods, excluded paths (default
//! `clients/hy/acp_client`, `docs/design-checks` — not shown in the report either).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::{
    self,
    reader::{Delim, Form, Node, Prefix, Reader, StrKind},
};
use serde::Serialize;

use super::bare_calls;
use super::facts::{hy_bindings, ByteSpan};
use super::names::{absolute_module, hy_mangle, module_of};
use super::paths::relative_path;
use super::smells::{children, live, live_items, span_of, Hy, Scope};

/// Paths excluded by default (prefix match from the repo root) — a copy of the ACP client, and design-verification samples.
pub const DEFAULT_EXCLUDES: &[&str] = &["clients/hy/acp_client", "docs/design-checks"];

/// The deff reason marker when the settings give none (same as the default in settings.rs).
pub const DEFAULT_REASON_MARKER: &str = "defk にできない:";

/// Other spellings of the marker for the comment giving the reason to leave a defn as is (existing comments in agora).
const KEEP_MARKERS: &[&str] = &["defn のまま"];

/// Input of the transform.
#[derive(Debug, Clone)]
pub struct FixOptions {
    /// Repo root (the range in which callers are searched; the base for module spellings).
    pub root: PathBuf,
    /// File or directory (absolute path) in which to look for definitions to convert.
    pub targets: Vec<PathBuf>,
    /// Excluded paths (prefix match from the root).
    pub excludes: Vec<String>,
    /// The deff reason marker.
    pub reason_marker: String,
    /// Whether to rewrite files (if false, only the plan is reported).
    pub write: bool,
    /// Convert only functions whose callers can all be rewritten (if false, convert and list the callers that cannot be rewritten under needs judgement).
    pub strict: bool,
}

// --- Report -------------------------------------------------------------------------------

/// Position in a file (1-based).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Location {
    pub file: String,
    pub line: usize,
    pub column: usize,
}

impl std::fmt::Display for Location {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}:{}:{}", self.file, self.line, self.column)
    }
}

/// One definition rewritten to defk.
#[derive(Debug, Clone, Serialize)]
pub struct ConvertedDefinition {
    pub location: Location,
    pub name: String,
    /// The contract dict that was added (empty if one already existed and nothing was added).
    pub contract: String,
}

/// Kind of call rewrite.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum CallRewrite {
    /// `(setv x (f a))` / `(val x (f a))` → `(<- x (f a))`。
    Bind { from: String },
    /// `(f a)` → `(! (f a))`。
    Bang,
}

/// One call that was rewritten.
#[derive(Debug, Clone, Serialize)]
pub struct RewrittenCall {
    pub location: Location,
    pub callee: String,
    /// Name of the definition containing the call (empty at module top level).
    pub container: String,
    pub rewrite: CallRewrite,
}

/// Why a caller cannot be rewritten.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum CallerBlock {
    /// Inside a comprehension (lfor, gfor, dfor, sfor) — a yield cannot be placed.
    Comprehension { head: String },
    /// Inside a plain function that is not converted (defn, deff, fn, defclass).
    PlainFunction { head: String, name: String },
    /// Module top level (runs at import time).
    ModuleLevel,
    /// Definition header (default values, contract dict) — runs at definition time or as a plain check.
    Signature,
    /// Inside a macro (defmacro).
    Macro,
    /// A step of `->`, `->>`, `as->`, `doto` — the call form differs from the written form.
    ThreadingMacro { head: String },
    /// The function itself is passed as a value (`:key` of `sorted`, an argument of `map`, a table value, etc.).
    PassedAsValue,
    /// Already bound as a Program (right side of `<-`, `!`, `yield`) — an assembly that returns a Program.
    AlreadyBound,
    /// Passed to a function that takes a Program (run, Gather …) — an assembly that returns a Program.
    ProgramArgument { head: String },
    /// A reference from a Python file.
    Python,
    /// A reference from a file other than Hy and Python (e.g. a pyproject entry point).
    OtherFile,
    /// An alias in `(import m [f :as g])` — calls through the alias cannot be traced.
    ImportAlias { alias: String },
    /// Inside a file under an excluded path.
    ExcludedFile,
}

impl std::fmt::Display for CallerBlock {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallerBlock::Comprehension { head } => write!(f, "called inside comprehension {}", head),
            CallerBlock::PlainFunction { head, name } => write!(f, "called inside a plain function that is not converted ({} {})", head, name),
            CallerBlock::ModuleLevel => write!(f, "called at module top level"),
            CallerBlock::Signature => write!(f, "called in a definition header (default value or contract)"),
            CallerBlock::Macro => write!(f, "used inside a macro"),
            CallerBlock::ThreadingMacro { head } => write!(f, "called as a step of {}", head),
            CallerBlock::PassedAsValue => write!(f, "the function itself is passed as a value"),
            CallerBlock::AlreadyBound => write!(f, "already bound with <- / ! (an assembly that returns a Program)"),
            CallerBlock::ProgramArgument { head } => write!(f, "passed to {}, which takes a Program (an assembly that returns a Program)", head),
            CallerBlock::Python => write!(f, "referenced from a Python file"),
            CallerBlock::OtherFile => write!(f, "referenced from a file other than Hy and Python"),
            CallerBlock::ImportAlias { alias } => write!(f, "imported under the alias {}", alias),
            CallerBlock::ExcludedFile => write!(f, "used inside a file under an excluded path"),
        }
    }
}

/// Reason an item needs judgement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum JudgementReason {
    /// An argument has no type annotation (the type to write in defk's :pre cannot be decided).
    MissingParamType { param: String },
    /// No return type annotation (the type to write in :post cannot be decided).
    MissingReturnType,
    /// `object`, `Any` — types too broad, which the defk contract rejects.
    BroadType { target: String, written: String },
    /// A type that cannot be mapped to isinstance (`Literal`, a string description, etc.).
    UnconvertibleType { target: String, written: String },
    /// Has decorators (`(defn [decorator] …)`).
    Decorated { decorators: String },
    /// The body has a yield (generator).
    Generator,
    /// An argument name looks like an effect handler (effect, eff, k) — defk rejects it.
    HandlerLikeParams { params: Vec<String> },
    /// A check function (starts with test-) — it should become a deftest, not a defk.
    TestFunction,
    /// A defn inside a function (not a class method).
    NestedDefn { outer: String },
    /// There is a caller or reference that cannot be rewritten (this place).
    BlockedCaller { callee: String, why: CallerBlock },
    /// The function became a defk — this caller or reference cannot be rewritten, so fix it by hand.
    CallerLeft { callee: String, why: CallerBlock },
}

impl std::fmt::Display for JudgementReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JudgementReason::MissingParamType { param } => write!(f, "argument {} has no type annotation", param),
            JudgementReason::MissingReturnType => write!(f, "no return type annotation"),
            JudgementReason::BroadType { target, written } => write!(f, "type {1} of {0} is too broad; the defk contract rejects it", target, written),
            JudgementReason::UnconvertibleType { target, written } => write!(f, "type {1} of {0} cannot be mapped to an isinstance type", target, written),
            JudgementReason::Decorated { decorators } => write!(f, "has decorators {}", decorators),
            JudgementReason::Generator => write!(f, "the body has a yield (generator)"),
            JudgementReason::HandlerLikeParams { params } => write!(f, "argument names {} look like a handler", params.join(", ")),
            JudgementReason::TestFunction => write!(f, "a check function — make it a deftest"),
            JudgementReason::NestedDefn { outer } => write!(f, "a defn inside {}", outer),
            JudgementReason::BlockedCaller { callee, why } => write!(f, "callers of {}: {}", callee, why),
            JudgementReason::CallerLeft { callee, why } => write!(f, "{} became a defk — fix this caller by hand: {}", callee, why),
        }
    }
}

/// One item that needs judgement.
#[derive(Debug, Clone, Serialize)]
pub struct NeedsJudgement {
    pub location: Location,
    /// Name of the definition that was not converted.
    pub definition: String,
    pub reason: JudgementReason,
}

/// Why an item was skipped (outside the fix targets).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum SkipReason {
    Dunder,
    Async,
    ReasonComment { comment: String },
    CompileTime,
    Method,
}

impl std::fmt::Display for SkipReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SkipReason::Dunder => write!(f, "dunder name"),
            SkipReason::Async => write!(f, ":async"),
            SkipReason::ReasonComment { comment } => write!(f, "reason comment: {}", comment),
            SkipReason::CompileTime => write!(f, "inside eval-and-compile"),
            SkipReason::Method => write!(f, "class method"),
        }
    }
}

/// One definition that was skipped.
#[derive(Debug, Clone, Serialize)]
pub struct SkippedDefinition {
    pub location: Location,
    pub name: String,
    pub reason: SkipReason,
}

/// One bare call (DOEFF126) remaining after the transform. The position is a line of the rewritten source (positions in the other reports are lines before rewriting).
#[derive(Debug, Clone, Serialize)]
pub struct ResidualBareCall {
    pub location: Location,
    pub definition: String,
    pub callee: String,
}

/// Report of one run.
#[derive(Debug, Clone, Default, Serialize)]
pub struct FixReport {
    pub root: String,
    /// Whether files were rewritten (false = plan only).
    pub written: bool,
    pub converted: Vec<ConvertedDefinition>,
    pub rewritten_calls: Vec<RewrittenCall>,
    pub needs_judgement: Vec<NeedsJudgement>,
    pub skipped: Vec<SkippedDefinition>,
    pub residual_bare_calls: Vec<ResidualBareCall>,
    pub changed_files: Vec<String>,
}

impl FixReport {
    /// Human-readable table (Markdown table).
    pub fn table(&self) -> String {
        let mut out = String::new();
        let binds = self.rewritten_calls.iter().filter(|c| matches!(c.rewrite, CallRewrite::Bind { .. })).count();
        out.push_str(&format!(
            "defn → defk({}): {} converted, {} calls rewritten (<- {}, ! {}), {} needing judgement, {} skipped, {} bare calls remaining, {} files rewritten\n",
            if self.written { "rewritten" } else { "plan only" },
            self.converted.len(),
            self.rewritten_calls.len(),
            binds,
            self.rewritten_calls.len() - binds,
            self.needs_judgement.len(),
            self.skipped.len(),
            self.residual_bare_calls.len(),
            self.changed_files.len()
        ));
        if !self.converted.is_empty() {
            out.push_str("\n## Converted definitions\n\n| Location (line before rewriting) | Name | Contract added |\n|---|---|---|\n");
            for c in &self.converted {
                out.push_str(&format!("| {} | {} | `{}` |\n", c.location, c.name, c.contract));
            }
        }
        if !self.rewritten_calls.is_empty() {
            out.push_str("\n## Rewritten calls\n\n| Location (line before rewriting) | Callee | Enclosing definition | Form |\n|---|---|---|---|\n");
            for c in &self.rewritten_calls {
                let form = match &c.rewrite {
                    CallRewrite::Bind { from } => format!("{} → <-", from),
                    CallRewrite::Bang => "!".to_string(),
                };
                out.push_str(&format!("| {} | {} | {} | {} |\n", c.location, c.callee, c.container, form));
            }
        }
        if !self.needs_judgement.is_empty() {
            out.push_str("\n## Needs judgement\n\n| Location (line before rewriting) | Definition | Reason |\n|---|---|---|\n");
            for j in &self.needs_judgement {
                out.push_str(&format!("| {} | {} | {} |\n", j.location, j.definition, j.reason));
            }
        }
        if !self.skipped.is_empty() {
            out.push_str("\n## Skipped\n\n| Location (line before rewriting) | Name | Reason |\n|---|---|---|\n");
            for s in &self.skipped {
                out.push_str(&format!("| {} | {} | {} |\n", s.location, s.name, s.reason));
            }
        }
        if !self.residual_bare_calls.is_empty() {
            out.push_str("\n## Remaining bare calls (DOEFF126)\n\n| Location (line after rewriting) | Definition | Callee |\n|---|---|---|\n");
            for r in &self.residual_bare_calls {
                out.push_str(&format!("| {} | {} | {} |\n", r.location, r.definition, r.callee));
            }
        }
        out
    }
}

// --- Files read ------------------------------------------------------------------------------

/// One Hy file that was read.
struct HyFile {
    rel: String,
    path: PathBuf,
    module: String,
    source: String,
    forms: Vec<Form>,
    bindings: BTreeMap<String, String>,
    lines: Vec<usize>,
    /// Whether this is a file in which to look for definitions to convert.
    target: bool,
    /// Whether this is a file under an excluded path.
    excluded: bool,
}

impl HyFile {
    fn read(root: &Path, path: &Path, source: String, targets: &[PathBuf], excludes: &[String]) -> Option<HyFile> {
        let rel = relative_path(root, path)?;
        let module = module_of(&rel);
        let excluded = excludes.iter().any(|e| rel == *e || rel.starts_with(&format!("{}/", e.trim_end_matches('/'))));
        let target = !excluded && targets.iter().any(|t| path.starts_with(t));
        let forms = Reader::new(&source, 0, source.len()).read_all();
        let bindings = hy_bindings(&source, &module);
        let lines = line_starts(&source);
        Some(HyFile { rel, path: path.to_path_buf(), module, source, forms, bindings, lines, target, excluded })
    }

    fn location(&self, offset: usize) -> Location {
        location_in(&self.rel, &self.lines, &self.source, offset)
    }
}

fn line_starts(source: &str) -> Vec<usize> {
    let mut starts = vec![0];
    starts.extend(source.match_indices('\n').map(|(at, _)| at + 1));
    starts
}

fn location_in(rel: &str, lines: &[usize], source: &str, offset: usize) -> Location {
    let line = lines.partition_point(|&start| start <= offset).max(1);
    let start = lines[line - 1];
    let column = source.get(start..offset).map(|s| s.chars().count()).unwrap_or(0) + 1;
    Location { file: rel.to_string(), line, column }
}

// --- Reading definitions ------------------------------------------------------------------------------

/// One replacement of a byte range.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Edit {
    start: usize,
    end: usize,
    text: String,
}

/// One candidate definition for conversion.
struct Candidate {
    file: usize,
    name: String,
    qualified: String,
    start: usize,
    edits: Vec<Edit>,
    contract: String,
}

/// A type annotation mapped to a contract type.
struct ContractType {
    text: String,
    /// Type arguments were dropped (the annotation is kept).
    reduced: bool,
}

/// Why a type cannot be mapped.
enum TypeProblem {
    Broad(String),
    Unconvertible(String),
}

/// Types too broad, which the defk contract rejects.
const BROAD_TYPES: &[&str] = &["object", "Any", "typing.Any"];

/// Turn a type annotation form into a spelling of a contract type that can be passed to isinstance.
fn contract_type(hy: &Hy<'_>, form: &Form) -> Result<ContractType, TypeProblem> {
    let written = hy.text(form).to_string();
    match &form.node {
        Node::Symbol if BROAD_TYPES.contains(&written.as_str()) => Err(TypeProblem::Broad(written)),
        Node::Symbol if written == "float" => Ok(ContractType { text: "(| float int)".to_string(), reduced: false }),
        Node::Symbol => Ok(ContractType { text: written, reduced: false }),
        Node::Str { kind: StrKind::Plain, body } => {
            let inner = hy.src.get(body.start..body.end).unwrap_or("");
            let identifier = !inner.is_empty()
                && inner.split('.').all(|part| part.chars().next().is_some_and(|c| c.is_alphabetic() || c == '_') && part.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '-'));
            if identifier {
                Ok(ContractType { text: inner.to_string(), reduced: false })
            } else {
                Err(TypeProblem::Unconvertible(written))
            }
        }
        Node::Seq { delim: Delim::Tuple, .. } => Ok(ContractType { text: written, reduced: false }),
        Node::Seq { delim: Delim::Paren, .. } => {
            let items = live(form).unwrap_or_default();
            match items.first().and_then(|h| hy.symbol(h)) {
                Some("|") => {
                    let parts: Vec<ContractType> = items[1..].iter().map(|p| contract_type(hy, p)).collect::<Result<_, _>>()?;
                    let reduced = parts.iter().any(|p| p.reduced);
                    Ok(ContractType { text: format!("(| {})", parts.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join(" ")), reduced })
                }
                Some("get" | "of") => {
                    let origin = items.get(1).ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                    let args: Vec<&Form> = match items.get(2) {
                        Some(tuple) if matches!(tuple.node, Node::Seq { delim: Delim::Tuple, .. }) => children(tuple),
                        Some(one) => vec![*one],
                        None => Vec::new(),
                    };
                    let origin_text = hy.text(origin);
                    match origin_text.rsplit('.').next().unwrap_or(origin_text) {
                        "Optional" => {
                            let inner = args.first().ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                            let inner = contract_type(hy, inner)?;
                            Ok(ContractType { text: format!("(| {} None)", inner.text), reduced: true })
                        }
                        "Union" => {
                            let parts: Vec<ContractType> = args.iter().map(|p| contract_type(hy, p)).collect::<Result<_, _>>()?;
                            Ok(ContractType { text: format!("(| {})", parts.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join(" ")), reduced: true })
                        }
                        "Annotated" => {
                            let inner = args.first().ok_or_else(|| TypeProblem::Unconvertible(written.clone()))?;
                            let inner = contract_type(hy, inner)?;
                            Ok(ContractType { text: inner.text, reduced: true })
                        }
                        "Literal" | "ClassVar" | "Final" => Err(TypeProblem::Unconvertible(written)),
                        "Type" => Ok(ContractType { text: "type".to_string(), reduced: true }),
                        _ => {
                            let inner = contract_type(hy, origin)?;
                            Ok(ContractType { text: inner.text, reduced: true })
                        }
                    }
                }
                _ => Ok(ContractType { text: written, reduced: false }),
            }
        }
        _ => Err(TypeProblem::Unconvertible(written)),
    }
}

/// Whether the contract type accepts None (to decide whether to add `(| T None)` for an argument whose default is None).
fn accepts_none(text: &str) -> bool {
    text == "None" || text.split(|c: char| c.is_whitespace() || c == '(' || c == ')').any(|w| w == "None")
}

/// Parts of a definition form.
struct DefinitionParts<'f> {
    decorators: Option<&'f Form>,
    is_async: bool,
    name_form: &'f Form,
    params: &'f Form,
    body: Vec<&'f Form>,
}

/// Split `(defn [decorator]? :async? #^ R? name [args] body…)` into parts (None if the shape differs).
fn definition_parts<'f>(hy: &Hy<'_>, items: &[&'f Form]) -> Option<DefinitionParts<'f>> {
    let mut at = 1;
    let mut decorators = None;
    let mut is_async = hy.head_text(items[0]) == Some("defn/a");
    while at < items.len() {
        let item = items[at];
        match &item.node {
            Node::Keyword if hy.text(item) == ":async" => {
                is_async = true;
                at += 1;
            }
            Node::Keyword if hy.text(item) == ":tp" => at += 2,
            Node::Seq { delim: Delim::Bracket, .. }
                if decorators.is_none() && items.get(at + 1).is_some_and(|n| matches!(n.node, Node::Symbol | Node::Annotated { .. } | Node::Keyword)) =>
            {
                decorators = Some(item);
                at += 1;
            }
            _ => break,
        }
    }
    let name_form = *items.get(at)?;
    let params = *items.get(at + 1)?;
    params.bracket_items()?;
    Some(DefinitionParts { decorators, is_async, name_form, params, body: items[at + 2..].to_vec() })
}

/// The definition's name (spelling without the annotation) and the return annotation.
fn name_and_return<'f>(hy: &Hy<'_>, name_form: &'f Form) -> (String, Option<&'f Form>) {
    match &name_form.node {
        Node::Annotated { annotation, target: Some(target) } => (hy.text(target).to_string(), annotation.as_deref()),
        _ => (hy.text(name_form).to_string(), None),
    }
}

/// Result of examining a candidate.
enum Examined {
    Candidate(Candidate),
    Skipped(SkipReason),
    Judgement(Vec<JudgementReason>),
}

/// Examine one top-level defn.
fn examine(file: &HyFile, index: usize, form: &Form, items: &[&Form], compile_time: bool, marker: &str) -> Option<(String, Examined)> {
    let hy = Hy { src: &file.source };
    let parts = definition_parts(&hy, items)?;
    let (name, return_form) = name_and_return(&hy, parts.name_form);
    if name.starts_with("__") && name.ends_with("__") {
        return Some((name, Examined::Skipped(SkipReason::Dunder)));
    }
    if parts.is_async {
        return Some((name, Examined::Skipped(SkipReason::Async)));
    }
    if compile_time {
        return Some((name, Examined::Skipped(SkipReason::CompileTime)));
    }
    if let Some(comment) = reason_comment(&file.source, form.span.start, parts.params.span.end, marker) {
        return Some((name, Examined::Skipped(SkipReason::ReasonComment { comment })));
    }
    let mut problems = Vec::new();
    if let Some(decorators) = parts.decorators.filter(|d| d.bracket_items().is_some_and(|i| !live_items(i).is_empty())) {
        problems.push(JudgementReason::Decorated { decorators: hy.text(decorators).to_string() });
    }
    if name.starts_with("test-") || name.starts_with("test_") {
        problems.push(JudgementReason::TestFunction);
    }
    if parts.body.iter().any(|b| has_yield(&hy, b)) {
        problems.push(JudgementReason::Generator);
    }
    let mut edits = vec![Edit { start: items[0].span.start, end: items[0].span.end, text: "defk".to_string() }];
    if let (Node::Annotated { target: Some(target), .. }, true) = (&parts.name_form.node, return_form.is_some()) {
        edits.push(Edit { start: parts.name_form.span.start, end: parts.name_form.span.end, text: hy.text(target).to_string() });
    }
    let mut pre = Vec::new();
    let mut handler_like = Vec::new();
    for param in parts.params.bracket_items().map(live_items).unwrap_or_default() {
        let (annotation, target) = match &param.node {
            Node::Annotated { annotation, target: Some(target) } => (annotation.as_deref(), target.as_ref()),
            _ => (None, param),
        };
        let (param_name, default) = match &target.node {
            Node::Symbol if matches!(hy.text(target), "*" | "/") => continue,
            Node::Symbol => (hy.text(target).to_string(), None),
            Node::Seq { delim: Delim::Bracket, .. } => {
                let pair = target.bracket_items().map(live_items).unwrap_or_default();
                match pair.first() {
                    Some(first) => (hy.text(first).to_string(), pair.get(1).map(|d| hy.text(d))),
                    None => continue,
                }
            }
            // `#* args`, `#** kw` — no contract needed (the annotation is kept as is).
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. } => continue,
            _ => (hy.text(target).to_string(), None),
        };
        if matches!(param_name.as_str(), "effect" | "eff" | "k") {
            handler_like.push(param_name.clone());
        }
        let Some(annotation) = annotation else {
            problems.push(JudgementReason::MissingParamType { param: param_name });
            continue;
        };
        match contract_type(&hy, annotation) {
            Ok(ty) => {
                let text = if default == Some("None") && !accepts_none(&ty.text) { format!("(| {} None)", ty.text) } else { ty.text };
                pre.push(format!("(: {} {})", param_name, text));
                if !ty.reduced {
                    edits.push(Edit { start: param.span.start, end: param.span.end, text: hy.text(target).to_string() });
                }
            }
            Err(TypeProblem::Broad(written)) => problems.push(JudgementReason::BroadType { target: param_name, written }),
            Err(TypeProblem::Unconvertible(written)) => problems.push(JudgementReason::UnconvertibleType { target: param_name, written }),
        }
    }
    if !handler_like.is_empty() {
        problems.push(JudgementReason::HandlerLikeParams { params: handler_like });
    }
    let post = match return_form.map(|r| contract_type(&hy, r)) {
        None => {
            problems.push(JudgementReason::MissingReturnType);
            None
        }
        Some(Ok(ty)) => Some(format!("(: % {})", ty.text)),
        Some(Err(TypeProblem::Broad(written))) => {
            problems.push(JudgementReason::BroadType { target: "return value".to_string(), written });
            None
        }
        Some(Err(TypeProblem::Unconvertible(written))) => {
            problems.push(JudgementReason::UnconvertibleType { target: "return value".to_string(), written });
            None
        }
    };
    if !problems.is_empty() {
        return Some((name, Examined::Judgement(problems)));
    }
    let pre_text = format!(":pre [{}]", pre.join(" "));
    let post_text = format!(":post [{}]", post.unwrap_or_default());
    let existing = existing_contract(&hy, &parts.body);
    let contract = match existing {
        Some(dict) => {
            let keys = contract_keys(&hy, dict);
            let mut missing = Vec::new();
            if !keys.contains(":pre") {
                missing.push(pre_text);
            }
            if !keys.contains(":post") {
                missing.push(post_text);
            }
            if !missing.is_empty() {
                let text = missing.join(" ");
                edits.push(Edit { start: dict.span.start + 1, end: dict.span.start + 1, text: format!("{} ", text) });
                text
            } else {
                String::new()
            }
        }
        None => {
            let text = format!("{{{} {}}}", pre_text, post_text);
            edits.push(contract_insertion(&file.source, form.span.start, parts.params.span.end, &text));
            text
        }
    };
    let qualified = format!("{}.{}", file.module, hy_mangle(&name));
    Some((name.clone(), Examined::Candidate(Candidate { file: index, name, qualified, start: form.span.start, edits, contract })))
}

/// The contract dict at the head of the body (after the docstring — one whose key starts with any of :pre, :post, :tags, :effects).
fn existing_contract<'f>(hy: &Hy<'_>, body: &[&'f Form]) -> Option<&'f Form> {
    let mut rest = body.iter();
    let mut first = rest.next()?;
    if matches!(first.node, Node::Str { .. }) {
        first = rest.next()?;
    }
    let keys = contract_keys(hy, first);
    (first.is_brace() && keys.iter().next().is_some_and(|k| matches!(k.as_str(), ":pre" | ":post" | ":tags" | ":effects"))).then_some(*first)
}

/// Keys of a dict (keywords only, at even positions).
fn contract_keys(hy: &Hy<'_>, dict: &Form) -> BTreeSet<String> {
    match &dict.node {
        Node::Seq { delim: Delim::Brace, items } => live_items(items).iter().step_by(2).filter(|k| matches!(k.node, Node::Keyword)).map(|k| hy.text(k).to_string()).collect(),
        _ => BTreeSet::new(),
    }
}

/// Where to put the contract dict — if what follows the argument `]` is the end of the line (or a comment), put it on the next line, indented; if the body continues on the same line, put it right after `]`.
fn contract_insertion(source: &str, form_start: usize, params_end: usize, contract: &str) -> Edit {
    let line_end = source[params_end..].find('\n').map(|at| params_end + at).unwrap_or(source.len());
    let rest = source[params_end..line_end].trim();
    let line_start = source[..form_start].rfind('\n').map(|at| at + 1).unwrap_or(0);
    let column = source[line_start..form_start].chars().count();
    if rest.is_empty() || rest.starts_with(';') {
        Edit { start: line_end, end: line_end, text: format!("\n{}{}", " ".repeat(column + 2), contract) }
    } else {
        Edit { start: params_end, end: params_end, text: format!(" {}", contract) }
    }
}

/// If the definition line (from the head to the end of the arguments) or a comment-only line just before it has a reason comment, that comment's text.
fn reason_comment(source: &str, form_start: usize, params_end: usize, marker: &str) -> Option<String> {
    let line_start = source[..form_start].rfind('\n').map(|at| at + 1).unwrap_or(0);
    let line_end = source[params_end..].find('\n').map(|at| params_end + at).unwrap_or(source.len());
    let own = &source[line_start..line_end];
    let previous = source[..line_start.saturating_sub(1)].rsplit('\n').next().filter(|l| l.trim_start().starts_with(';')).unwrap_or("");
    [own, previous].into_iter().filter_map(|text| {
        let comment = text.find(';').map(|at| text[at..].trim_start_matches(';').trim())?;
        (comment.contains(marker) || KEEP_MARKERS.iter().any(|m| comment.contains(m))).then(|| comment.to_string())
    }).next()
}

/// Whether the form contains a yield (inside a nested fn is not counted).
fn has_yield(hy: &Hy<'_>, form: &Form) -> bool {
    match hy.head(form) {
        Some("yield" | "yield-from") => true,
        Some("fn" | "fn/a" | "defn" | "defn/a" | "defk" | "deff" | "fnk" | "defclass") => false,
        _ => children(form).into_iter().any(|c| has_yield(hy, c)),
    }
}

// --- Callers ------------------------------------------------------------------------------

/// The enclosing context of a call site (can it be rewritten?).
#[derive(Debug, Clone)]
enum Frame {
    /// Body of a Program (defk, deftest, defhandler, fnk, defp, do! …).
    Program,
    /// Body of a conversion candidate (a Program if the candidate is converted).
    Candidate { candidate: usize, name: String },
    /// A place that cannot be rewritten.
    Blocked(CallerBlock),
}

/// Shape of a call.
#[derive(Debug, Clone)]
enum Shape {
    /// A call — if Some, a binding to a single name (the range of the head of setv / val).
    Call { bind: Option<ByteSpan> },
    /// Already bound with <- / ! / yield.
    Yielded,
    /// An argument of a function that takes a Program.
    ProgramArgument { head: String },
    /// A step of `->`.
    Threaded { head: String },
    /// A reference that is not the head of a call.
    Reference,
    /// A reference from a place that cannot be read (Python, an alias, another file).
    Foreign(CallerBlock),
}

/// One place that uses a candidate function.
struct Site {
    /// A Hy file (None for references from Python etc.).
    file: Option<usize>,
    location: Location,
    span: ByteSpan,
    callee: usize,
    frame: Frame,
    container: String,
    shape: Shape,
}

/// Kind of position of a call's child.
#[derive(Debug, Clone)]
enum Slot {
    Value,
    Yielded,
    Bind(ByteSpan),
    ProgramArgument(String),
    Threaded(String),
}

/// Functions that take a Program and run or combine it (if a candidate is passed here, it is an assembly that returns a Program).
const PROGRAM_TAKERS: &[&str] = &[
    "run", "run-on", "run_on", "async-run", "async_run", "Gather", "Spawn", "Race", "Safe", "Try", "Local", "Listen", "Intercept", "WithHandler",
    "with-handler", "with_handler", "with-handlers", "with_handlers", "maybe", "result", "on-raise", "absent-as",
];

const COMPREHENSIONS: &[&str] = &["lfor", "gfor", "dfor", "sfor"];
const THREADING: &[&str] = &["->", "->>", "as->", "doto"];
const PROGRAM_DEFINITIONS: &[&str] = &["defk", "deftest", "defp", "defpp", "defhandler", "defmcp-tool"];
const PROGRAM_BLOCKS: &[&str] = &["do!", "for/do", "traverse", "handle", "fnk"];
const PLAIN_DEFINITIONS: &[&str] = &["deff", "defclass"];
const PLAIN_FUNCTIONS: &[&str] = &["fn", "fn/a"];
const MACROS: &[&str] = &["defmacro", "defmacro/g!", "defreader"];

/// Tool that searches for callers and descends through one file.
struct SiteWalker<'a> {
    hy: Hy<'a>,
    file: &'a HyFile,
    index: usize,
    scope: Scope<'a>,
    candidates: &'a BTreeMap<String, usize>,
    /// Top-level candidates of this file (form start → candidate index).
    tops: &'a BTreeMap<usize, usize>,
}

impl SiteWalker<'_> {
    fn resolve(&self, spelled: &str) -> Option<usize> {
        if spelled.starts_with(':') || spelled.starts_with('.') || !spelled.chars().any(|c| c.is_alphabetic()) {
            return None;
        }
        self.candidates.get(&self.scope.qualify(spelled)).copied()
    }

    fn push(&self, span: ByteSpan, callee: usize, frame: &Frame, container: &str, shape: Shape, out: &mut Vec<Site>) {
        let frame = if self.file.excluded { Frame::Blocked(CallerBlock::ExcludedFile) } else { frame.clone() };
        out.push(Site { file: Some(self.index), location: self.file.location(span.start), span, callee, frame, container: container.to_string(), shape });
    }

    fn walk(&self, form: &Form, frame: &Frame, container: &str, slot: Slot, top: bool, out: &mut Vec<Site>) {
        match &form.node {
            Node::Seq { delim: Delim::Paren, .. } => self.list(form, frame, container, slot, top, out),
            Node::Symbol => {
                if let Some(callee) = self.resolve(self.hy.text(form)) {
                    self.push(span_of(form), callee, frame, container, Shape::Reference, out);
                }
            }
            Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => {}
            Node::Annotated { target: Some(target), .. } => self.walk(target, frame, container, Slot::Value, false, out),
            _ => {
                for child in children(form) {
                    self.walk(child, frame, container, Slot::Value, false, out);
                }
            }
        }
    }

    fn list(&self, form: &Form, frame: &Frame, container: &str, slot: Slot, top: bool, out: &mut Vec<Site>) {
        let items = live(form).unwrap_or_default();
        let Some(first) = items.first() else { return };
        let head = self.hy.head_text(first);
        match head {
            Some("import" | "require") => return self.import_aliases(&items, frame, container, out),
            Some("do" | "eval-and-compile" | "eval-when-compile") if top => {
                for child in &items[1..] {
                    self.walk(child, frame, container, Slot::Value, true, out);
                }
                return;
            }
            Some(h @ ("defn" | "defn/a")) => {
                let (name, body_frame) = match (top, self.tops.get(&form.span.start)) {
                    (true, Some(&candidate)) => {
                        let name = self.definition_name(&items);
                        (name.clone(), Frame::Candidate { candidate, name })
                    }
                    _ => {
                        let name = self.definition_name(&items);
                        (name.clone(), Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name }))
                    }
                };
                return self.definition(&items, frame, container, &name, body_frame, out);
            }
            Some(h) if PROGRAM_DEFINITIONS.contains(&h) => {
                let name = self.definition_name(&items);
                return self.definition(&items, frame, container, &name, Frame::Program, out);
            }
            Some(h) if PLAIN_DEFINITIONS.contains(&h) => {
                let name = self.definition_name(&items);
                let blocked = Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name: name.clone() });
                return self.definition(&items, frame, container, &name, blocked, out);
            }
            Some(h) if MACROS.contains(&h) => {
                for child in &items[1..] {
                    self.walk(child, &Frame::Blocked(CallerBlock::Macro), container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if PLAIN_FUNCTIONS.contains(&h) => {
                let blocked = Frame::Blocked(CallerBlock::PlainFunction { head: h.to_string(), name: container.to_string() });
                for child in &items[1..] {
                    self.walk(child, &blocked, container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if PROGRAM_BLOCKS.contains(&h) => {
                for child in &items[1..] {
                    self.walk(child, &Frame::Program, container, Slot::Value, false, out);
                }
                return;
            }
            Some(h) if COMPREHENSIONS.contains(&h) => {
                let blocked = Frame::Blocked(CallerBlock::Comprehension { head: h.to_string() });
                for child in &items[1..] {
                    self.walk(child, &blocked, container, Slot::Value, false, out);
                }
                return;
            }
            _ => {}
        }
        // If the call head is a candidate, record this call.
        match (&first.node, head) {
            (Node::Symbol, Some(spelled)) => {
                if let Some(callee) = self.resolve(spelled) {
                    let shape = match &slot {
                        Slot::Value => Shape::Call { bind: None },
                        Slot::Bind(head) => Shape::Call { bind: Some(*head) },
                        Slot::Yielded => Shape::Yielded,
                        Slot::ProgramArgument(head) => Shape::ProgramArgument { head: head.clone() },
                        Slot::Threaded(head) => Shape::Threaded { head: head.clone() },
                    };
                    self.push(span_of(form), callee, frame, container, shape, out);
                }
            }
            (Node::Keyword, _) => {}
            _ => self.walk(first, frame, container, Slot::Value, false, out),
        }
        let last = items.len() - 1;
        for (index, child) in items.iter().enumerate().skip(1) {
            let child_slot = match head {
                Some("<-") if index == last => Slot::Yielded,
                Some("!" | "yield" | "yield-from" | "await") => Slot::Yielded,
                Some("setv" | "val") if items.len() == 3 && index == 2 && self.plain_name(items[1]) => Slot::Bind(span_of(first)),
                Some(h) if PROGRAM_TAKERS.contains(&h) => Slot::ProgramArgument(h.to_string()),
                Some(h) if THREADING.contains(&h) && index >= 2 => Slot::Threaded(h.to_string()),
                _ => Slot::Value,
            };
            self.walk(child, frame, container, child_slot, false, out);
        }
    }

    /// Whether this is a name that can be fixed to `(<- x …)` (a symbol without a dot).
    fn plain_name(&self, form: &Form) -> bool {
        self.hy.symbol(form).is_some_and(|s| !s.contains('.') && !s.starts_with(':'))
    }

    fn definition_name(&self, items: &[&Form]) -> String {
        match definition_parts(&self.hy, items) {
            Some(parts) => name_and_return(&self.hy, parts.name_form).0,
            None => items.get(1).map(|n| name_and_return(&self.hy, n).0).unwrap_or_default(),
        }
    }

    /// A definition form — the header (decorators, argument defaults, contract dict) is in the outer context; the body is descended through body_frame.
    fn definition(&self, items: &[&Form], outer: &Frame, outer_container: &str, name: &str, body_frame: Frame, out: &mut Vec<Site>) {
        let head = self.hy.head_text(items[0]).unwrap_or("");
        let (header, body): (Vec<&Form>, Vec<&Form>) = match head {
            "defn" | "defn/a" | "defk" | "deff" | "defmacro" => match definition_parts(&self.hy, items) {
                Some(parts) => (parts.decorators.into_iter().chain([parts.params]).collect(), parts.body),
                None => (Vec::new(), items[1..].to_vec()),
            },
            // Everything after the name is body (deftest, defhandler, defp, defclass …).
            _ => (Vec::new(), items.get(2..).map(|r| r.to_vec()).unwrap_or_default()),
        };
        let header_frame = match outer {
            Frame::Blocked(_) => outer.clone(),
            _ => Frame::Blocked(CallerBlock::Signature),
        };
        for form in header {
            self.walk(form, &header_frame, outer_container, Slot::Value, false, out);
        }
        let contract = existing_contract(&self.hy, &body).map(|c| c.span.start);
        for form in body {
            let frame = if Some(form.span.start) == contract { &header_frame } else { &body_frame };
            self.walk(form, frame, name, Slot::Value, false, out);
        }
    }

    /// A place that imports a candidate via an alias in `(import m [f :as g])`.
    fn import_aliases(&self, items: &[&Form], frame: &Frame, container: &str, out: &mut Vec<Site>) {
        if self.hy.head_text(items[0]) != Some("import") {
            return;
        }
        let mut index = 1;
        while index < items.len() {
            let module = absolute_module(&self.file.module, &hy_mangle(self.hy.text(items[index])));
            match items.get(index + 1).and_then(|n| n.bracket_items()) {
                Some(names) => {
                    let names = live_items(names);
                    for at in 0..names.len() {
                        let is_alias = names.get(at + 1).is_some_and(|k| self.hy.text(k) == ":as");
                        let qualified = format!("{}.{}", module, hy_mangle(self.hy.text(names[at])));
                        if let (true, Some(&callee)) = (is_alias, self.candidates.get(&qualified)) {
                            let alias = names.get(at + 2).map(|a| self.hy.text(a).to_string()).unwrap_or_default();
                            self.push(span_of(names[at]), callee, frame, container, Shape::Foreign(CallerBlock::ImportAlias { alias }), out);
                        }
                    }
                    index += 2;
                }
                None => index += 1,
            }
        }
    }
}

/// References from Python and toml files (files where the name appears as a spelling and the module spelling also appears).
fn foreign_sites(root: &Path, candidates: &[Candidate], files: &[HyFile]) -> Vec<Site> {
    let mut by_name: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, candidate) in candidates.iter().enumerate() {
        by_name.entry(hy_mangle(&candidate.name)).or_default().push(index);
    }
    let skipped = [".venv", "node_modules", "target", ".git", "__pycache__"];
    let walker = walkdir::WalkDir::new(root).follow_links(false).into_iter().filter_entry(|e| e.depth() == 0 || !e.file_type().is_dir() || !skipped.contains(&e.file_name().to_string_lossy().as_ref()));
    let mut out = Vec::new();
    for entry in walker.filter_map(Result::ok).filter(|e| e.file_type().is_file()) {
        let path = entry.path();
        let block = match path.extension().and_then(|e| e.to_str()) {
            Some("py" | "pyi") => CallerBlock::Python,
            Some("toml") => CallerBlock::OtherFile,
            _ => continue,
        };
        let Ok(source) = std::fs::read_to_string(path) else { continue };
        let Some(rel) = relative_path(root, path) else { continue };
        let lines = line_starts(&source);
        let words = identifier_offsets(&source);
        for (word, offsets) in &words {
            let Some(indexes) = by_name.get(*word) else { continue };
            for &index in indexes {
                let candidate = &candidates[index];
                let module = &files[candidate.file].module;
                let last = module.rsplit('.').next().unwrap_or(module);
                if source.contains(module.as_str()) || source.contains(last) {
                    let offset = offsets[0];
                    out.push(Site {
                        file: None,
                        location: location_in(&rel, &lines, &source, offset),
                        span: ByteSpan { start: offset, end: offset + word.len() },
                        callee: index,
                        frame: Frame::Blocked(block.clone()),
                        container: String::new(),
                        shape: Shape::Foreign(block.clone()),
                    });
                }
            }
        }
    }
    out
}

/// Spelling of identifiers in source → byte positions where they appear.
fn identifier_offsets(source: &str) -> BTreeMap<&str, Vec<usize>> {
    let mut out: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    let bytes = source.as_bytes();
    let mut at = 0;
    while at < bytes.len() {
        let c = bytes[at];
        if c.is_ascii_alphabetic() || c == b'_' {
            let start = at;
            while at < bytes.len() && (bytes[at].is_ascii_alphanumeric() || bytes[at] == b'_') {
                at += 1;
            }
            out.entry(&source[start..at]).or_default().push(start);
        } else {
            at += 1;
        }
    }
    out
}

/// Whether a use blocks converting the function — in strict mode, every place that cannot be rewritten; otherwise only the marks of an assembly that returns a Program (already bound,
/// passed to a function that takes a Program) (converting would change the meaning of the caller).
fn blocks(site: &Site, alive: &BTreeSet<usize>, strict: bool) -> bool {
    match verdict(site, alive) {
        Ok(_) => false,
        Err(CallerBlock::AlreadyBound | CallerBlock::ProgramArgument { .. }) => true,
        Err(_) => strict,
    }
}

/// Verdict for one use — a rewrite shape, or a reason it cannot be rewritten.
fn verdict(site: &Site, alive: &BTreeSet<usize>) -> Result<Option<ByteSpan>, CallerBlock> {
    match &site.shape {
        Shape::Reference => Err(match &site.frame {
            Frame::Blocked(CallerBlock::ExcludedFile) => CallerBlock::ExcludedFile,
            _ => CallerBlock::PassedAsValue,
        }),
        Shape::Foreign(block) => Err(block.clone()),
        Shape::Yielded => Err(CallerBlock::AlreadyBound),
        Shape::ProgramArgument { head } => Err(CallerBlock::ProgramArgument { head: head.clone() }),
        Shape::Threaded { head } => Err(CallerBlock::ThreadingMacro { head: head.clone() }),
        Shape::Call { bind } => match &site.frame {
            Frame::Program => Ok(*bind),
            Frame::Candidate { candidate, .. } if alive.contains(candidate) => Ok(*bind),
            Frame::Candidate { name, .. } => Err(CallerBlock::PlainFunction { head: "defn".to_string(), name: name.clone() }),
            Frame::Blocked(block) => Err(block.clone()),
        },
    }
}

// --- Execution ------------------------------------------------------------------------------------

/// Run the transform once (if options.write is false, return only the plan without writing files).
pub fn run(options: &FixOptions) -> Result<FixReport, String> {
    let root = &options.root;
    let mut files: Vec<HyFile> = Vec::new();
    for path in super::hy_files::collect(root) {
        let source = std::fs::read_to_string(&path).map_err(|e| format!("cannot read {}: {}", path.display(), e))?;
        if let Some(file) = HyFile::read(root, &path, source, &options.targets, &options.excludes) {
            files.push(file);
        }
    }
    let mut report = FixReport { root: root.display().to_string(), written: options.write, ..FixReport::default() };

    // 1. Collect candidate definitions.
    let mut candidates: Vec<Candidate> = Vec::new();
    for (index, file) in files.iter().enumerate() {
        if !file.target {
            continue;
        }
        let hy = Hy { src: &file.source };
        let mut pending: Vec<(&Form, bool)> = file.forms.iter().map(|f| (f, false)).collect();
        pending.reverse();
        while let Some((form, compile_time)) = pending.pop() {
            let Some(items) = live(form) else { continue };
            match items.first().and_then(|h| hy.head_text(h)) {
                Some("do") => pending.extend(items[1..].iter().rev().map(|f| (*f, compile_time))),
                Some("eval-and-compile" | "eval-when-compile") => pending.extend(items[1..].iter().rev().map(|f| (*f, true))),
                Some("defn" | "defn/a") => match examine(file, index, form, &items, compile_time, &options.reason_marker) {
                    Some((_, Examined::Candidate(candidate))) => candidates.push(candidate),
                    Some((name, Examined::Skipped(reason))) => report.skipped.push(SkippedDefinition { location: file.location(form.span.start), name, reason }),
                    Some((name, Examined::Judgement(reasons))) => {
                        for reason in reasons {
                            report.needs_judgement.push(NeedsJudgement { location: file.location(form.span.start), definition: name.clone(), reason });
                        }
                    }
                    None => {}
                },
                _ => {}
            }
        }
        nested_definitions(&hy, file, &mut report);
    }

    // 2. Collect the places that use candidates from all Hy files in the repo, plus Python and toml.
    let by_qualified: BTreeMap<String, usize> = candidates.iter().enumerate().map(|(i, c)| (c.qualified.clone(), i)).collect();
    let mut sites: Vec<Site> = Vec::new();
    for (index, file) in files.iter().enumerate() {
        if !file.source.contains('(') {
            continue;
        }
        let tops: BTreeMap<usize, usize> = candidates.iter().enumerate().filter(|(_, c)| c.file == index).map(|(i, c)| (c.start, i)).collect();
        let walker = SiteWalker {
            hy: Hy { src: &file.source },
            file,
            index,
            scope: Scope { module: &file.module, bindings: &file.bindings },
            candidates: &by_qualified,
            tops: &tops,
        };
        for form in &file.forms {
            walker.walk(form, &Frame::Blocked(CallerBlock::ModuleLevel), "", Slot::Value, true, &mut sites);
        }
    }
    if !candidates.is_empty() {
        sites.extend(foreign_sites(root, &candidates, &files));
    }

    // 3. Fixed point — drop candidates that have places that cannot be rewritten; the calls in the bodies of dropped candidates also become unrewritable.
    let mut alive: BTreeSet<usize> = (0..candidates.len()).collect();
    loop {
        let dropped: BTreeSet<usize> = sites.iter().filter(|s| alive.contains(&s.callee) && blocks(s, &alive, options.strict)).map(|s| s.callee).collect();
        if dropped.is_empty() {
            break;
        }
        alive.retain(|c| !dropped.contains(c));
    }
    for site in &sites {
        if let Err(why) = verdict(site, &alive) {
            let candidate = &candidates[site.callee];
            let callee = candidate.qualified.clone();
            let reason = if alive.contains(&site.callee) { JudgementReason::CallerLeft { callee, why } } else { JudgementReason::BlockedCaller { callee, why } };
            report.needs_judgement.push(NeedsJudgement { location: site.location.clone(), definition: candidate.name.clone(), reason });
        }
    }

    // 4. Collect rewrites per file.
    let mut edits: BTreeMap<usize, Vec<Edit>> = BTreeMap::new();
    let mut needs: BTreeMap<usize, BTreeSet<&str>> = BTreeMap::new();
    for &index in &alive {
        let candidate = &candidates[index];
        edits.entry(candidate.file).or_default().extend(candidate.edits.iter().cloned());
        needs.entry(candidate.file).or_default().insert("defk");
        let file = &files[candidate.file];
        report.converted.push(ConvertedDefinition { location: file.location(candidate.start), name: candidate.name.clone(), contract: candidate.contract.clone() });
    }
    for site in &sites {
        let (true, Some(file)) = (alive.contains(&site.callee), site.file) else { continue };
        let Ok(bind) = verdict(site, &alive) else { continue };
        let source = &files[file].source;
        let rewrite = match bind {
            Some(head) => {
                edits.entry(file).or_default().push(Edit { start: head.start, end: head.end, text: "<-".to_string() });
                needs.entry(file).or_default().insert("<-");
                CallRewrite::Bind { from: source[head.start..head.end].to_string() }
            }
            None => {
                let list = edits.entry(file).or_default();
                list.push(Edit { start: site.span.start, end: site.span.start, text: "(! ".to_string() });
                list.push(Edit { start: site.span.end, end: site.span.end, text: ")".to_string() });
                CallRewrite::Bang
            }
        };
        report.rewritten_calls.push(RewrittenCall {
            location: site.location.clone(),
            callee: candidates[site.callee].name.clone(),
            container: site.container.clone(),
            rewrite,
        });
    }
    for (file, names) in &needs {
        if let Some(edit) = require_edit(&files[*file], names) {
            edits.entry(*file).or_default().push(edit);
        }
    }

    // 5. Build the rewritten source and run DOEFF126.
    let mut rewritten: BTreeMap<usize, String> = BTreeMap::new();
    for (file, list) in &edits {
        rewritten.insert(*file, apply_edits(&files[*file].source, list)?);
    }
    report.changed_files = rewritten.keys().map(|i| files[*i].rel.clone()).collect();
    report.residual_bare_calls = residual_bare_calls(&files, &rewritten, &sites);
    if options.write {
        for (file, text) in &rewritten {
            std::fs::write(&files[*file].path, text).map_err(|e| format!("cannot write {}: {}", files[*file].path.display(), e))?;
        }
    }
    report.converted.sort_by(|a, b| a.location.cmp(&b.location));
    report.rewritten_calls.sort_by(|a, b| a.location.cmp(&b.location));
    report.needs_judgement.sort_by(|a, b| a.location.cmp(&b.location));
    report.skipped.sort_by(|a, b| a.location.cmp(&b.location));
    Ok(report)
}

/// The outer definition, when examining nested definitions.
struct Outer {
    name: String,
    /// Whether the outer is a class (a defn inside is a method).
    class: bool,
}

/// A defn inside a function (a method is skipped; anything else needs judgement).
fn nested_definitions(hy: &Hy<'_>, file: &HyFile, report: &mut FixReport) {
    fn visit(hy: &Hy<'_>, file: &HyFile, form: &Form, outer: Option<&Outer>, top: bool, report: &mut FixReport) {
        let Some(items) = live(form) else {
            for child in children(form) {
                visit(hy, file, child, outer, false, report);
            }
            return;
        };
        let head = items.first().and_then(|h| hy.head_text(h));
        let inner = match head {
            Some("do" | "eval-and-compile" | "eval-when-compile") if top => {
                for child in &items[1..] {
                    visit(hy, file, child, outer, true, report);
                }
                return;
            }
            Some("defclass") => Outer { name: items.get(1).map(|n| hy.text(n)).unwrap_or("").to_string(), class: true },
            Some("defn" | "defn/a") => {
                let name = definition_parts(hy, &items).map(|p| name_and_return(hy, p.name_form).0).unwrap_or_default();
                let location = file.location(form.span.start);
                match outer {
                    Some(Outer { class: true, .. }) => report.skipped.push(SkippedDefinition { location, name: name.clone(), reason: SkipReason::Method }),
                    Some(Outer { name: outer_name, class: false }) => report.needs_judgement.push(NeedsJudgement {
                        location,
                        definition: name.clone(),
                        reason: JudgementReason::NestedDefn { outer: outer_name.clone() },
                    }),
                    None => {}
                }
                Outer { name, class: false }
            }
            Some(h) if DEFINITION_LIKE.contains(&h) => Outer { name: items.get(1).map(|n| name_and_return(hy, n).0).unwrap_or_default(), class: false },
            _ => {
                for child in &items {
                    visit(hy, file, child, outer, false, report);
                }
                return;
            }
        };
        for child in &items[1..] {
            visit(hy, file, child, Some(&inner), false, report);
        }
    }
    for form in &file.forms {
        visit(hy, file, form, None, true, report);
    }
}

/// A definition with a name (to take the outer name of a nested defn).
const DEFINITION_LIKE: &[&str] = &["defk", "deff", "deftest", "defhandler", "defp", "defpp", "defmacro"];

/// A rewrite that adds the macros a file's require needs (None if already present).
fn require_edit(file: &HyFile, needed: &BTreeSet<&str>) -> Option<Edit> {
    let hy = Hy { src: &file.source };
    let mut first_import: Option<usize> = None;
    for form in &file.forms {
        let Some(items) = live(form) else { continue };
        let head = items.first().and_then(|h| hy.head_text(h));
        if matches!(head, Some("require" | "import")) && first_import.is_none() {
            first_import = Some(form.span.start);
        }
        if head != Some("require") {
            continue;
        }
        let mut index = 1;
        while index < items.len() {
            let module = hy.text(items[index]);
            let Some(names) = items.get(index + 1).filter(|n| n.bracket_items().is_some()) else {
                index += 1;
                continue;
            };
            if module == "doeff-hy.macros" || module == "doeff_hy.macros" {
                let have: BTreeSet<&str> = live_items(names.bracket_items().unwrap_or_default()).iter().map(|n| hy.text(n)).collect();
                let missing: Vec<&str> = needed.iter().filter(|n| !have.contains(**n)).copied().collect();
                if missing.is_empty() {
                    return None;
                }
                let close = names.span.end - 1;
                let text = if have.is_empty() { missing.join(" ") } else { format!(" {}", missing.join(" ")) };
                return Some(Edit { start: close, end: close, text });
            }
            index += 2;
        }
    }
    let at = first_import.or_else(|| file.forms.first().map(|f| f.span.start)).unwrap_or(0);
    let names: Vec<&str> = needed.iter().copied().collect();
    Some(Edit { start: at, end: at, text: format!("(require doeff-hy.macros [{}])\n", names.join(" ")) })
}

/// Apply the rewrites from the back (overlapping rewrites are an error).
fn apply_edits(source: &str, edits: &[Edit]) -> Result<String, String> {
    let mut sorted: Vec<&Edit> = edits.iter().collect();
    sorted.sort_by(|a, b| b.start.cmp(&a.start).then(b.end.cmp(&a.end)));
    sorted.dedup_by(|a, b| a == b);
    let mut out = source.to_string();
    let mut limit = usize::MAX;
    for edit in sorted {
        if edit.end > limit {
            return Err(format!("rewrites overlap ({}..{})", edit.start, edit.end));
        }
        out.replace_range(edit.start..edit.end, &edit.text);
        limit = edit.start;
    }
    Ok(out)
}

/// Run DOEFF126 on the rewritten source (the rewritten files, and files that have a place using a candidate).
fn residual_bare_calls(files: &[HyFile], rewritten: &BTreeMap<usize, String>, sites: &[Site]) -> Vec<ResidualBareCall> {
    let text_of = |index: usize| rewritten.get(&index).map(String::as_str).unwrap_or(&files[index].source);
    let mut defks = bare_calls::DefkNames::default();
    for (index, file) in files.iter().enumerate() {
        let text = text_of(index);
        if text.contains("(defk") {
            defks.extend(bare_calls::defk_names_in(text, &file.module));
        }
    }
    let mut checked: BTreeSet<usize> = rewritten.keys().copied().collect();
    checked.extend(sites.iter().filter_map(|s| s.file));
    let mut out = Vec::new();
    for index in checked {
        let file = &files[index];
        if file.excluded {
            continue;
        }
        let text = text_of(index);
        let bindings = hy_bindings(text, &file.module);
        let lines = line_starts(text);
        for call in bare_calls::bare_calls_in(text, Scope { module: &file.module, bindings: &bindings }, &defks) {
            out.push(ResidualBareCall { location: location_in(&file.rel, &lines, text, call.span.start), definition: call.definition, callee: call.callee });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Put files in a temporary repo, run the transform, and return (report, rewritten contents).
    struct Fixture {
        dir: tempfile::TempDir,
    }

    impl Fixture {
        fn new(files: &[(&str, &str)]) -> Fixture {
            let dir = tempfile::tempdir().expect("tempdir");
            for (rel, text) in files {
                let path = dir.path().join(rel);
                std::fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
                std::fs::write(path, text).expect("write");
            }
            Fixture { dir }
        }

        fn run(&self, target: &str) -> FixReport {
            self.run_with(target, false)
        }

        fn run_strict(&self, target: &str) -> FixReport {
            self.run_with(target, true)
        }

        fn run_with(&self, target: &str, strict: bool) -> FixReport {
            let root = self.dir.path().canonicalize().expect("root");
            run(&FixOptions {
                root: root.clone(),
                targets: vec![root.join(target)],
                excludes: DEFAULT_EXCLUDES.iter().map(|s| s.to_string()).collect(),
                reason_marker: DEFAULT_REASON_MARKER.to_string(),
                write: true,
                strict,
            })
            .expect("run")
        }

        fn read(&self, rel: &str) -> String {
            std::fs::read_to_string(self.dir.path().join(rel)).expect("read")
        }
    }

    fn reasons(report: &FixReport) -> Vec<String> {
        report.needs_judgement.iter().map(|j| format!("{}:{}", j.definition, j.reason)).collect()
    }

    #[test]
    fn definition_gets_contract_from_annotations_and_callers_are_rewritten() {
        let fx = Fixture::new(&[
            (
                "app/core.hy",
                r#"(require doeff-hy.macros [val])

(defn #^ int add-one [#^ int x #^ (| str None) [label None]]
  "Add 1."
  (+ x 1))

(defn #^ (get list int) twice [#^ (get list int) xs #^ float scale]
  (lfor x xs (* x scale)))
"#,
            ),
            (
                "app/use.hy",
                r#"(require doeff-hy.macros [defk <- val])
(import app.core [add-one twice])

(defk total [n]
  {:pre [(: n int)] :post [(: % int)]}
  (setv a (add-one n))
  (val b (twice [n] 2.0))
  (+ a (add-one (len b))))
"#,
            ),
        ]);
        let report = fx.run("app/core.hy");
        assert!(report.needs_judgement.is_empty(), "{:?}", reasons(&report));
        assert_eq!(report.converted.len(), 2);
        let core = fx.read("app/core.hy");
        assert!(core.contains("(require doeff-hy.macros [val defk])"), "{}", core);
        assert!(core.contains("(defk add-one [x [label None]]\n  {:pre [(: x int) (: label (| str None))] :post [(: % int)]}\n  \"Add 1.\""), "{}", core);
        // A parametrized type gets the base type in the contract, and the annotation is kept. float also accepts int.
        assert!(core.contains("(defk twice [#^ (get list int) xs scale]\n  {:pre [(: xs list) (: scale (| float int))] :post [(: % list)]}"), "{}", core);
        let used = fx.read("app/use.hy");
        assert!(used.contains("(<- a (add-one n))"), "{}", used);
        assert!(used.contains("(<- b (twice [n] 2.0))"), "{}", used);
        // A call in the middle of an expression becomes !; an existing <- in require is not added again.
        assert!(used.contains("(+ a (! (add-one (len b))))"), "{}", used);
        assert!(used.starts_with("(require doeff-hy.macros [defk <- val])"), "{}", used);
        assert!(report.residual_bare_calls.is_empty(), "{:?}", report.residual_bare_calls);
    }

    #[test]
    fn tail_calls_and_recursion_inside_converted_functions_are_banged() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int leaf [#^ int x] x)
(defn #^ int branch [#^ int x] (if (> x 0) (branch (- x 1)) (leaf x)))
(deftest test-branch (assert (= (branch 2) 0)))
"#,
        )]);
        let report = fx.run("m.hy");
        assert_eq!(report.converted.len(), 2, "{:?}", reasons(&report));
        let text = fx.read("m.hy");
        assert!(text.starts_with("(require doeff-hy.macros [defk])\n(defk leaf [x] {:pre [(: x int)] :post [(: % int)]} x)"), "{}", text);
        assert!(text.contains("(if (> x 0) (! (branch (- x 1))) (! (leaf x)))"), "{}", text);
        assert!(text.contains("(assert (= (! (branch 2)) 0))"), "{}", text);
        assert!(report.residual_bare_calls.is_empty(), "{:?}", report.residual_bare_calls);
    }

    #[test]
    fn functions_with_callers_that_cannot_be_rewritten_are_left_and_listed() {
        let fx = Fixture::new(&[
            (
                "m.hy",
                r#"(defn #^ int in-comprehension [#^ int x] x)
(defn #^ int in-plain [#^ int x] x)
(defn #^ int as-key [#^ int x] x)
(defn #^ int at-module [#^ int x] x)
(defn #^ int in-fn [#^ int x] x)
(defn #^ int builder [#^ int x] x)
(defn #^ int from-python [#^ int x] x)
(defn #^ int caller-of-blocked [#^ int x] (in-plain x))
(deff helper [x] {:pre [(: x int)] :post [(: % int)]} (in-plain x))
(defk k [xs]
  {:pre [(: xs list)] :post [(: % list)]}
  (val ys (lfor x xs (in-comprehension x)))
  (val zs (sorted xs :key as-key))
  (val f (fn [x] (in-fn x)))
  (<- b (builder 1))
  ys)
(setv CONSTANT (at-module 1))
"#,
            ),
            ("py_user.py", "from m import from_python\n"),
        ]);
        let report = fx.run_strict("m.hy");
        // caller-of-blocked calls in-plain, but it has no caller of its own, so it is converted (in-plain's call is no longer inside a plain defn… but
        // in-plain is also called from inside a deff, so it is dropped).
        let converted: Vec<&str> = report.converted.iter().map(|c| c.name.as_str()).collect();
        assert_eq!(converted, vec!["caller-of-blocked"]);
        let why: BTreeSet<String> = reasons(&report).into_iter().collect();
        for expected in [
            "in-comprehension:callers of m.in_comprehension: called inside comprehension lfor",
            "in-plain:callers of m.in_plain: called inside a plain function that is not converted (deff helper)",
            "as-key:callers of m.as_key: the function itself is passed as a value",
            "at-module:callers of m.at_module: called at module top level",
            "in-fn:callers of m.in_fn: called inside a plain function that is not converted (fn k)",
            "builder:callers of m.builder: already bound with <- / ! (an assembly that returns a Program)",
            "from-python:callers of m.from_python: referenced from a Python file",
        ] {
            assert!(why.contains(expected), "missing {}: {:?}", expected, why);
        }
        let text = fx.read("m.hy");
        assert!(text.contains("(defn #^ int in-plain [#^ int x] x)"), "{}", text);
        // in-plain in the body of the converted caller-of-blocked stays a defn (calling a plain function from inside a defk is correct).
        assert!(text.contains("(defk caller-of-blocked [x] {:pre [(: x int)] :post [(: % int)]} (in-plain x))"), "{}", text);
    }

    #[test]
    fn a_blocked_caller_inside_a_candidate_cascades() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int inner [#^ int x] x)
(defn #^ int outer [#^ int x] (inner x))
(setv X (outer 1))
"#,
        )]);
        let report = fx.run_strict("m.hy");
        // outer is called at module top level so it is dropped; the call to inner inside outer ends up inside a plain defn, so inner is dropped too.
        assert!(report.converted.is_empty(), "{:?}", report.converted);
        assert!(reasons(&report).contains(&"inner:callers of m.inner: called inside a plain function that is not converted (defn outer)".to_string()), "{:?}", reasons(&report));
        assert!(report.changed_files.is_empty());
    }

    #[test]
    fn forms_that_are_not_targeted_are_skipped() {
        let fx = Fixture::new(&[
            (
                "m.hy",
                r#"(defn __getattr__ [name] name)
(defn :async fetch [url] url)
(defn #^ int kept [#^ int x] x)  ; defk にできない: called by an external framework as a plain function
(eval-and-compile (defn #^ int helper [#^ int x] x))
(defclass Box [] (defn #^ int size [self] 1))
"#,
            ),
            ("clients/hy/acp_client/a.hy", "(defn #^ int copied [#^ int x] x)\n"),
        ]);
        let report = fx.run(".");
        let skipped: Vec<String> = report.skipped.iter().map(|s| format!("{}:{}", s.name, s.reason)).collect();
        assert_eq!(
            skipped,
            vec![
                "__getattr__:dunder name",
                "fetch::async",
                "kept:reason comment: defk にできない: called by an external framework as a plain function",
                "helper:inside eval-and-compile",
                "size:class method",
            ]
        );
        // Definitions under an excluded path are neither counted nor rewritten.
        assert!(fx.read("clients/hy/acp_client/a.hy").starts_with("(defn"));
        assert!(report.converted.is_empty() && report.changed_files.is_empty());
    }

    #[test]
    fn definitions_that_need_judgement_are_listed_with_the_reason() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn untyped [x] x)
(defn #^ int no-param-type [x] x)
(defn no-return [#^ int x] x)
(defn #^ int broad [#^ object x] 1)
(defn #^ int literal [#^ (get Literal "a") x] 1)
(defn [cache] #^ int decorated [#^ int x] x)
(defn #^ int gen [#^ int x] (yield x))
(defn #^ None test-thing [] None)
(defn #^ int handler-like [#^ int effect #^ int k] 1)
(defn #^ int outer [#^ int x] (defn #^ int inner [#^ int y] y) (inner x))
"#,
        )]);
        let report = fx.run("m.hy");
        let why: Vec<String> = reasons(&report);
        for expected in [
            "untyped:argument x has no type annotation",
            "untyped:no return type annotation",
            "no-param-type:argument x has no type annotation",
            "no-return:no return type annotation",
            "broad:type object of x is too broad; the defk contract rejects it",
            "literal:type (get Literal \"a\") of x cannot be mapped to an isinstance type",
            "decorated:has decorators [cache]",
            "gen:the body has a yield (generator)",
            "test-thing:a check function — make it a deftest",
            "handler-like:argument names effect, k look like a handler",
            "inner:a defn inside outer",
        ] {
            assert!(why.contains(&expected.to_string()), "missing {}: {:?}", expected, why);
        }
        // outer itself is converted (the defn inside stays a plain function).
        assert_eq!(report.converted.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), vec!["outer"]);
    }

    #[test]
    fn existing_contract_dicts_are_kept_and_completed() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int tagged [#^ int x]
  "doc"
  {:tags {:context "c" :role "judgment"}}
  x)
(defn #^ dict literal-dict [] {"a" 1})
"#,
        )]);
        let report = fx.run("m.hy");
        assert_eq!(report.converted.len(), 2, "{:?}", reasons(&report));
        let text = fx.read("m.hy");
        assert!(text.contains("{:pre [(: x int)] :post [(: % int)] :tags {:context \"c\" :role \"judgment\"}}"), "{}", text);
        // A dict that is the answer is not a contract — put the contract before it.
        assert!(text.contains("(defk literal-dict [] {:pre [] :post [(: % dict)]} {\"a\" 1})"), "{}", text);
    }

    #[test]
    fn threading_and_import_aliases_block() {
        let fx = Fixture::new(&[
            ("a.hy", "(defn #^ int step [#^ int x #^ int y] (+ x y))\n(defn #^ int aliased [#^ int x] x)\n"),
            (
                "b.hy",
                "(import a [step aliased :as other])\n(defk go [n] {:pre [(: n int)] :post [(: % int)]} (-> n (step 1)))\n",
            ),
        ]);
        let report = fx.run_strict("a.hy");
        let why = reasons(&report);
        assert!(why.contains(&"step:callers of a.step: called as a step of ->".to_string()), "{:?}", why);
        assert!(why.contains(&"aliased:callers of a.aliased: imported under the alias other".to_string()), "{:?}", why);
        assert!(report.converted.is_empty());
    }

    #[test]
    fn by_default_functions_are_converted_and_callers_left_are_listed() {
        let fx = Fixture::new(&[(
            "m.hy",
            r#"(defn #^ int leaf [#^ int x] x)
(defn #^ int builder [#^ int x] x)
(defk k [xs]
  {:pre [(: xs list)] :post [(: % list)]}
  (val ys (lfor x xs (leaf x)))
  (<- b (builder 1))
  (+ ys [(leaf 1)]))
(setv CONSTANT (leaf 1))
"#,
        )]);
        let report = fx.run("m.hy");
        // leaf is converted; calls in a comprehension and at module top level are listed as things to fix by hand. builder is an assembly that returns a Program, so it is not converted.
        assert_eq!(report.converted.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), vec!["leaf"]);
        let why: BTreeSet<String> = reasons(&report).into_iter().collect();
        for expected in [
            "leaf:m.leaf became a defk — fix this caller by hand: called inside comprehension lfor",
            "leaf:m.leaf became a defk — fix this caller by hand: called at module top level",
            "builder:callers of m.builder: already bound with <- / ! (an assembly that returns a Program)",
        ] {
            assert!(why.contains(expected), "missing {}: {:?}", expected, why);
        }
        let text = fx.read("m.hy");
        assert!(text.contains("(+ ys [(! (leaf 1))])") && text.contains("(lfor x xs (leaf x))"), "{}", text);
        // The module-top-level binding of a call to fix by hand stays unrewritten and also appears in DOEFF126's remainder (a defk call outside a definition is a bare call regardless of position).
        // A call inside a comprehension (inside a defk body, not a place that uses the answer as a value) does not appear in the remainder.
        assert!(text.contains("(setv CONSTANT (leaf 1))"), "{}", text);
        let residual: Vec<(&str, &str)> = report.residual_bare_calls.iter().map(|r| (r.definition.as_str(), r.callee.as_str())).collect();
        assert_eq!(residual, vec![("<m>", "leaf")]);
    }

    #[test]
    fn dry_run_writes_nothing() {
        let fx = Fixture::new(&[("m.hy", "(defn #^ int one [] 1)\n")]);
        let root = fx.dir.path().canonicalize().expect("root");
        let report = run(&FixOptions {
            root: root.clone(),
            targets: vec![root.join("m.hy")],
            excludes: Vec::new(),
            reason_marker: DEFAULT_REASON_MARKER.to_string(),
            write: false,
            strict: false,
        })
        .expect("run");
        assert_eq!(report.converted.len(), 1);
        assert_eq!(report.changed_files, vec!["m.hy".to_string()]);
        assert_eq!(fx.read("m.hy"), "(defn #^ int one [] 1)\n");
        assert!(report.table().contains("plan only"));
    }
}

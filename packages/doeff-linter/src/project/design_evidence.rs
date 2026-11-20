//! DOEFF212: a type's shape changes only together with the design evidence for that shape.
//!
//! The evidence of a code element (round 1: a Hy `defrecord`) is the answers to the required design questions (responsibility, identity key, the fact each field states,
//! why a field may be absent, the parts it was split into, the rejected shapes and why, how many files a change touches, the id of the reviewer's verdict). It sits in the
//! module's sibling `<stem>.evidence.toml` (`foo.hy` → `foo.evidence.toml`), which no language runtime reads. The model is language-neutral: a Rust reader returns the same
//! `RecordShape`, and the comparison below does not change (docs/design/design-evidence).
//!
//! Hits, for each top-level defrecord of a .hy in `:design-evidence {:files [..] :except [..]}` of architecture.hy:
//! - the record has an evidence entry whose field names and types (in order) or bases differ from the source (ShapeDiffers);
//! - the record has an evidence entry with a missing answer (Incomplete);
//! - the record has no evidence entry, and the ledger of existing shapes (`:existing`) does not hold its current fingerprint — a new type or a reshaped one (Missing).
//!   An existing type whose shape did not change (the ledger holds the same fingerprint) is not a hit;
//! - an evidence entry of kind record names no defrecord of the module (Orphan), or the evidence file cannot be read (Unreadable).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::fields::record_field_targets;
use doeff_indexer::hy_index::reader::{Form, Node, Reader};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::architecture::DesignEvidenceDecl;
use super::retired::{judge_files, selected};

/// The suffix of an evidence file, replacing the module's extension.
pub const EVIDENCE_SUFFIX: &str = "evidence.toml";
/// The evidence schema this linter reads.
const SCHEMA: u32 = 1;

/// One field of a type as read from the source (name and type as spelled in the language; `optional` = the type admits an absent value — for Hy, `None` in the type).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldShape {
    pub name: String,
    pub type_text: Option<String>,
    pub optional: bool,
}

/// The shape of one type as read from the source: its name, bases, and fields in written order. `start..end` is the byte range from the form's start to the name's end.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordShape {
    pub name: String,
    pub bases: Vec<String>,
    pub fields: Vec<FieldShape>,
    pub start: usize,
    pub end: usize,
}

impl RecordShape {
    /// The canonical spelling of the shape (the fingerprint's input): `defrecord`, then `bases: a, b` if any, then one `name: type` (or `name`) line per field.
    /// Docstrings and `:check` invariants are not part of the shape.
    pub fn canonical(&self) -> String {
        let mut out = String::from("defrecord\n");
        if !self.bases.is_empty() {
            out.push_str(&format!("bases: {}\n", self.bases.join(", ")));
        }
        for field in &self.fields {
            match &field.type_text {
                Some(type_text) => out.push_str(&format!("{}: {}\n", field.name, type_text)),
                None => out.push_str(&format!("{}\n", field.name)),
            }
        }
        out
    }

    /// The first 16 hex digits of the sha256 of the canonical spelling (the ledger of existing shapes holds this).
    pub fn fingerprint(&self) -> String {
        let digest = Sha256::digest(self.canonical().as_bytes());
        digest.iter().take(8).map(|b| format!("{:02x}", b)).collect()
    }
}

/// Collapses runs of whitespace to one space (a type's spelling is compared without its layout).
fn squeeze(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Whether a Hy type spelling admits None (`(| T None)`, `(Optional T)`, `None`).
fn hy_optional(type_text: &str) -> bool {
    type_text.split(|c: char| c.is_whitespace() || "()[]|".contains(c)).any(|token| token == "None" || token == "Optional" || token.ends_with(".Optional"))
}

/// The contents of a list without discards (`#_`).
fn live(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect()
}

/// The top-level defrecords of a .hy source, in written order (Err if the Hy reader cannot read the source).
pub fn record_shapes(source: &str) -> Result<Vec<RecordShape>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("{} places the Hy reader cannot read (first = {:?})", reader.issues.len(), issue));
    }
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("").to_string();
    let mut out = Vec::new();
    for form in &forms {
        let Some(all) = form.paren_items() else { continue };
        let items = live(all);
        if items.len() < 2 || !matches!(items[0].node, Node::Symbol) || text(items[0]) != "defrecord" || !matches!(items[1].node, Node::Symbol) {
            continue;
        }
        let (bases, members) = match items.get(2).and_then(|f| f.bracket_items()) {
            Some(bases) => (live(bases).into_iter().map(text).collect(), &items[3..]),
            None => (Vec::new(), &items[2..]),
        };
        let fields = record_field_targets(source, members)
            .into_iter()
            .map(|target| {
                let type_text = target.annotation.map(|span| squeeze(source.get(span.start..span.end).unwrap_or("")));
                FieldShape {
                    name: source.get(target.name.start..target.name.end).unwrap_or("").to_string(),
                    optional: type_text.as_deref().is_some_and(hy_optional),
                    type_text,
                }
            })
            .collect();
        out.push(RecordShape { name: text(items[1]), bases, fields, start: form.span.start, end: items[1].span.end });
    }
    Ok(out)
}

/// The kind of a code element (round 1 judges only records; the other kinds are read and not judged).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ElementKind {
    Record,
    Function,
    Interface,
    Module,
}

/// One field's answers: the field's name and type as spelled in the source, the fact it states about the key, and when it is absent (required for a field that admits None).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FieldEvidence {
    pub name: String,
    #[serde(rename = "type")]
    pub type_text: Option<String>,
    #[serde(default)]
    pub fact: String,
    pub absent_when: Option<String>,
}

/// A shape that was considered and not chosen, and why.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RejectedShape {
    #[serde(default)]
    pub shape: String,
    #[serde(default)]
    pub why: String,
}

/// The evidence of one element: the considered design of element `name` of this module at the shape its fields spell. The required answers are Option or default here
/// so that a missing one is reported as a gap of that element (Incomplete), not as an unreadable file.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ElementEvidence {
    pub name: String,
    pub kind: ElementKind,
    #[serde(default)]
    pub concern: String,
    pub key: Option<Vec<String>>,
    #[serde(default)]
    pub field: Vec<FieldEvidence>,
    #[serde(default)]
    pub bases: Vec<String>,
    #[serde(default)]
    pub parts: Vec<String>,
    #[serde(default)]
    pub rejected: Vec<RejectedShape>,
    pub fan_out: Option<u32>,
    pub review: Option<String>,
}

/// One evidence file (`<stem>.evidence.toml`).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceFile {
    pub schema: u32,
    #[serde(default)]
    pub element: Vec<ElementEvidence>,
}

/// Reads an evidence file (Err with the reason if it is not TOML of this schema).
pub fn parse_evidence(text: &str) -> Result<EvidenceFile, String> {
    let file: EvidenceFile = toml::from_str(text).map_err(|error| error.to_string().trim().to_string())?;
    if file.schema != SCHEMA {
        return Err(format!("schema = {} (this linter reads schema = {})", file.schema, SCHEMA));
    }
    Ok(file)
}

/// The ledger of the shapes that existed when the rule was turned on: (path from the root, type name) → fingerprint. Looked up through `fingerprint_of`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExistingShapes {
    #[serde(default)]
    shapes: BTreeMap<String, String>,
}

impl ExistingShapes {
    pub fn parse(text: &str) -> Result<Self, String> {
        toml::from_str(text).map_err(|error| error.to_string().trim().to_string())
    }

    /// The ledger key of a type.
    fn key(rel: &str, name: &str) -> String {
        format!("{}::{}", rel, name)
    }

    pub fn fingerprint_of(&self, rel: &str, name: &str) -> Option<&str> {
        self.shapes.get(&Self::key(rel, name)).map(String::as_str)
    }

    /// The ledger's lines, key → fingerprint (for DOEFF214's comparison with the base).
    pub fn lines(&self) -> impl Iterator<Item = (&str, &str)> {
        self.shapes.iter().map(|(k, v)| (k.as_str(), v.as_str()))
    }

    /// The fingerprint of one ledger key.
    pub fn line(&self, key: &str) -> Option<&str> {
        self.shapes.get(key).map(String::as_str)
    }

    /// The ledger of the given shapes, as TOML (written once when the rule is turned on — `--print-existing-shapes`).
    pub fn spell<'a>(files: impl Iterator<Item = (&'a str, &'a [RecordShape])>) -> String {
        let shapes = files.flat_map(|(rel, shapes)| shapes.iter().map(move |s| (Self::key(rel, &s.name), s.fingerprint()))).collect();
        toml::to_string(&ExistingShapes { shapes }).unwrap_or_default()
    }
}

/// The kind of a hit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// No evidence entry, and the ledger does not hold the current fingerprint (`was` = the ledger's fingerprint if the type is in it — a reshaped existing type).
    Missing { fingerprint: String, was: Option<String> },
    /// The evidence entry's shape differs from the source (one line per difference).
    ShapeDiffers { differences: Vec<String> },
    /// The evidence entry lacks answers (one line per gap).
    Incomplete { gaps: Vec<String> },
    /// An evidence entry of kind record names no defrecord of the module.
    Orphan,
    /// The evidence file cannot be read.
    Unreadable { reason: String },
    /// A type of the shapes ledger at the base has fields its version at the base did not have, and no user approval at the base covers them — evidence alone does not let an
    /// existing type grow (v2 §8.3).
    Grew { fields: Vec<String> },
}

impl Problem {
    /// The detail of the hit's key after the type name (a type can have a shape hit and an answers hit at once — they are different keys).
    pub fn key_detail(&self, name: &str) -> String {
        match self {
            Problem::Missing { .. } => name.to_string(),
            Problem::ShapeDiffers { .. } => format!("{}:shape", name),
            Problem::Incomplete { .. } => format!("{}:answers", name),
            Problem::Orphan => format!("{}:orphan", name),
            Problem::Unreadable { .. } => name.to_string(),
            Problem::Grew { .. } => format!("{}:grew", name),
        }
    }

    /// The reason text of the hit (for the message and the explanation).
    pub fn describe(&self, name: &str, evidence_rel: &str) -> String {
        match self {
            Problem::Missing { fingerprint, was: None } => format!(
                "defrecord {} is a new type (not in the ledger of existing shapes) and has no entry in {} — write the answers to the required design questions there (shape fingerprint {})",
                name, evidence_rel, fingerprint
            ),
            Problem::Missing { fingerprint, was: Some(was) } => format!(
                "defrecord {} changed its shape (ledger {} → now {}) and has no entry in {} — a reshaped type needs the answers to the required design questions",
                name, was, fingerprint, evidence_rel
            ),
            Problem::ShapeDiffers { differences } => format!("defrecord {} does not have the shape its evidence in {} records: {}", name, evidence_rel, differences.join("; ")),
            Problem::Incomplete { gaps } => format!("the evidence of defrecord {} in {} lacks answers: {}", name, evidence_rel, gaps.join("; ")),
            Problem::Orphan => format!("{} has evidence for record {}, but the module has no top-level defrecord of that name", evidence_rel, name),
            Problem::Unreadable { reason } => format!("{} cannot be read as design evidence ({})", evidence_rel, reason),
            Problem::Grew { fields } => format!(
                "defrecord {} existed before design evidence (it is in the base's ledger of existing shapes) and gained fields {} — an existing type grows only with an approval of the user \
                 (an entry ledger = \"fields\" in the approvals file, landed before this change); put the new fact in a part type or a new type instead",
                name,
                fields.join(", ")
            ),
        }
    }
}

/// One hit (byte range in the .hy — the file's first byte for Orphan and Unreadable).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub name: String,
    pub start: usize,
    pub end: usize,
    pub problem: Problem,
}

/// The differences between the source shape and the evidence's fields and bases (empty = same shape).
fn shape_differences(shape: &RecordShape, evidence: &ElementEvidence) -> Vec<String> {
    let mut out = Vec::new();
    let written: Vec<(String, Option<String>)> = evidence.field.iter().map(|f| (f.name.clone(), f.type_text.as_deref().map(squeeze))).collect();
    let source: Vec<(String, Option<String>)> = shape.fields.iter().map(|f| (f.name.clone(), f.type_text.clone())).collect();
    let names = |fields: &[(String, Option<String>)]| fields.iter().map(|(n, _)| n.clone()).collect::<Vec<_>>();
    let added: Vec<String> = names(&source).into_iter().filter(|n| !written.iter().any(|(w, _)| w == n)).collect();
    let removed: Vec<String> = names(&written).into_iter().filter(|n| !source.iter().any(|(s, _)| s == n)).collect();
    if !added.is_empty() {
        out.push(format!("fields not in the evidence: {}", added.join(", ")));
    }
    if !removed.is_empty() {
        out.push(format!("fields in the evidence but not in the source: {}", removed.join(", ")));
    }
    for (name, type_text) in &source {
        if let Some((_, written_type)) = written.iter().find(|(w, _)| w == name).filter(|(_, w)| w != type_text) {
            let spell = |t: &Option<String>| t.clone().unwrap_or_else(|| "(no type)".to_string());
            out.push(format!("field {}: source type {}, evidence type {}", name, spell(type_text), spell(written_type)));
        }
    }
    if added.is_empty() && removed.is_empty() && names(&source) != names(&written) {
        out.push(format!("field order: source [{}], evidence [{}]", names(&source).join(" "), names(&written).join(" ")));
    }
    if shape.bases != evidence.bases.iter().map(|b| squeeze(b)).collect::<Vec<_>>() {
        out.push(format!("bases: source [{}], evidence [{}]", shape.bases.join(" "), evidence.bases.join(" ")));
    }
    out
}

/// The answers the evidence of a record lacks (empty = complete). `review` is checked against `review_pattern` if declared (only the spelling).
fn answer_gaps(shape: &RecordShape, evidence: &ElementEvidence, review_pattern: Option<&regex::Regex>) -> Vec<String> {
    let mut gaps = Vec::new();
    if evidence.concern.trim().is_empty() {
        gaps.push("concern (the one responsibility, in one sentence) is empty".to_string());
    }
    match &evidence.key {
        None => gaps.push("key is missing (the fields that decide identity; write key = [] for a value with no identity)".to_string()),
        Some(key) => {
            let unknown: Vec<&String> = key.iter().filter(|k| !shape.fields.iter().any(|f| &f.name == *k)).collect();
            if !unknown.is_empty() {
                gaps.push(format!("key names fields the record does not have: {}", unknown.iter().map(|k| k.as_str()).collect::<Vec<_>>().join(", ")));
            }
        }
    }
    let unstated: Vec<&str> = evidence.field.iter().filter(|f| f.fact.trim().is_empty()).map(|f| f.name.as_str()).collect();
    if !unstated.is_empty() {
        gaps.push(format!("fields without the fact they state about the key: {}", unstated.join(", ")));
    }
    let unexplained: Vec<&str> = shape
        .fields
        .iter()
        .filter(|f| f.optional)
        .filter(|f| !evidence.field.iter().any(|e| e.name == f.name && e.absent_when.as_deref().is_some_and(|w| !w.trim().is_empty())))
        .map(|f| f.name.as_str())
        .collect();
    if !unexplained.is_empty() {
        gaps.push(format!("fields that may be absent without absent_when (when, and why it is not a separate part): {}", unexplained.join(", ")));
    }
    if evidence.rejected.is_empty() {
        gaps.push("rejected is empty (at least one shape considered and not chosen, with why)".to_string());
    } else if evidence.rejected.iter().any(|r| r.shape.trim().is_empty() || r.why.trim().is_empty()) {
        gaps.push("a rejected shape lacks shape or why".to_string());
    }
    if evidence.fan_out.is_none() {
        gaps.push("fan_out is missing (how many files a change of this shape touches)".to_string());
    }
    match evidence.review.as_deref().map(str::trim) {
        None | Some("") => gaps.push("review is missing (the id of the reviewer's verdict)".to_string()),
        Some(review) if review_pattern.is_some_and(|p| !p.is_match(review)) => {
            gaps.push(format!("review {} does not have the declared spelling of a review id", review));
        }
        Some(_) => {}
    }
    gaps
}

/// The judgment of one module: the .hy source, its evidence file's contents (None = no evidence file), the ledger, and the declared review spelling.
/// Err only if the .hy cannot be read.
pub fn judge(
    rel: &str,
    hy_source: &str,
    evidence: Option<&str>,
    existing: &ExistingShapes,
    review_pattern: Option<&regex::Regex>,
) -> Result<Vec<Hit>, String> {
    let shapes = record_shapes(hy_source)?;
    let first_line_end = hy_source.find('\n').unwrap_or(hy_source.len());
    let file_hit = |name: String, problem: Problem| Hit { name, start: 0, end: first_line_end, problem };
    let entries: Vec<ElementEvidence> = match evidence.map(parse_evidence) {
        None => Vec::new(),
        Some(Ok(file)) => file.element.into_iter().filter(|e| e.kind == ElementKind::Record).collect(),
        Some(Err(reason)) => return Ok(vec![file_hit("evidence".to_string(), Problem::Unreadable { reason })]),
    };
    let mut hits = Vec::new();
    for shape in &shapes {
        let written: Vec<&ElementEvidence> = entries.iter().filter(|e| e.name == shape.name).collect();
        let hit = |problem: Problem| Hit { name: shape.name.clone(), start: shape.start, end: shape.end, problem };
        match written.as_slice() {
            [] => {
                let fingerprint = shape.fingerprint();
                let was = existing.fingerprint_of(rel, &shape.name);
                if was != Some(fingerprint.as_str()) {
                    hits.push(hit(Problem::Missing { was: was.map(str::to_string), fingerprint }));
                }
            }
            [one, rest @ ..] => {
                let differences = shape_differences(shape, one);
                if !differences.is_empty() {
                    hits.push(hit(Problem::ShapeDiffers { differences }));
                }
                let mut gaps = answer_gaps(shape, one, review_pattern);
                if !rest.is_empty() {
                    gaps.push(format!("written {} times (one entry per type)", rest.len() + 1));
                }
                if !gaps.is_empty() {
                    hits.push(hit(Problem::Incomplete { gaps }));
                }
            }
        }
    }
    let mut orphans: Vec<&str> = entries.iter().map(|e| e.name.as_str()).filter(|n| !shapes.iter().any(|s| s.name == *n)).collect();
    orphans.dedup();
    hits.extend(orphans.into_iter().map(|name| file_hit(name.to_string(), Problem::Orphan)));
    Ok(hits)
}

/// The fields each type of the base's shapes ledger gained against its version at the base and no approval covers (v2 §8.3). `base_source` is read only when a type of
/// the ledger changed its fingerprint (the common case — no listed type changed — reads nothing). Err only if the .hy cannot be read.
pub fn grown_fields(
    rel: &str,
    hy_source: &str,
    base_ledger: &ExistingShapes,
    base_source: impl FnOnce() -> Option<String>,
    approvals: &super::ledger_guard::Approvals,
) -> Result<Vec<Hit>, String> {
    let shapes = record_shapes(hy_source)?;
    let changed: Vec<&RecordShape> =
        shapes.iter().filter(|s| base_ledger.fingerprint_of(rel, &s.name).is_some_and(|was| was != s.fingerprint().as_str())).collect();
    if changed.is_empty() {
        return Ok(Vec::new());
    }
    let Some(base_shapes) = base_source().and_then(|source| record_shapes(&source).ok()) else { return Ok(Vec::new()) };
    let mut hits = Vec::new();
    for shape in changed {
        let Some(was) = base_shapes.iter().find(|b| b.name == shape.name) else { continue };
        let key = ExistingShapes::key(rel, &shape.name);
        let fields: Vec<String> = shape
            .fields
            .iter()
            .filter(|f| !was.fields.iter().any(|w| w.name == f.name))
            .filter(|f| !approvals.covers("fields", &key, &f.name))
            .map(|f| f.name.clone())
            .collect();
        if !fields.is_empty() {
            hits.push(Hit { name: shape.name.clone(), start: shape.start, end: shape.end, problem: Problem::Grew { fields } });
        }
    }
    Ok(hits)
}

/// The sibling evidence file of a module (`foo.hy` → `foo.evidence.toml`).
pub fn evidence_of(module: &Path) -> PathBuf {
    module.with_extension(EVIDENCE_SUFFIX)
}

/// The path from the root of the sibling evidence file (for messages).
pub fn evidence_rel(rel: &str) -> String {
    match rel.rsplit_once('.') {
        Some((stem, _)) => format!("{}.{}", stem, EVIDENCE_SUFFIX),
        None => format!("{}.{}", rel, EVIDENCE_SUFFIX),
    }
}

/// The module next to an evidence file (`foo.evidence.toml` → `foo.hy`; None if the path is not an evidence file).
pub fn module_of(evidence_rel: &str, evidence_path: &Path) -> Option<(String, PathBuf)> {
    let suffix = format!(".{}", EVIDENCE_SUFFIX);
    let stem = evidence_rel.strip_suffix(&suffix)?;
    let file_stem = evidence_path.file_name()?.to_str()?.strip_suffix(&suffix)?;
    Some((format!("{}.hy", stem), evidence_path.with_file_name(format!("{}.hy", file_stem))))
}

/// Whether a .hy is in the population.
pub fn wants(rel: &str, decl: &DesignEvidenceDecl) -> bool {
    rel.ends_with(".hy") && selected(rel, &decl.selection.files, &decl.selection.except)
}

/// The settings of one run: the ledger read once and the compiled review spelling (Err with the reason if the declared ledger cannot be read — the run then judges with an
/// empty ledger, so every type without evidence is a hit, and the reason goes to the report's errors).
pub struct Judging {
    pub existing: ExistingShapes,
    pub review_pattern: Option<regex::Regex>,
    /// The base of the declared guard, with its ledger and approvals (None = no :guard, or the base cannot be read — then growth is not judged and DOEFF214 reports why).
    pub growth: Option<Growth>,
}

/// What judging a type's growth reads at the base (v2 §8.3).
pub struct Growth {
    pub base: super::ledger_guard::Base,
    pub ledger: ExistingShapes,
    pub approvals: super::ledger_guard::Approvals,
}

impl Growth {
    fn load(root: &Path, decl: &DesignEvidenceDecl) -> Option<Growth> {
        let guard = decl.guard.as_ref()?;
        let base = super::ledger_guard::Base::resolve(root, &guard.base).ok()?;
        let ledger = decl.existing.as_deref().and_then(|rel| base.read(rel)).and_then(|text| ExistingShapes::parse(&text).ok()).unwrap_or_default();
        let approvals = super::ledger_guard::base_approvals(guard, &base).ok()?;
        Some(Growth { base, ledger, approvals })
    }
}

impl Judging {
    /// The hits of one module's types that grew (empty without a readable base).
    fn grown(&self, rel: &str, hy_source: &str) -> Result<Vec<Hit>, String> {
        match &self.growth {
            None => Ok(Vec::new()),
            Some(growth) => grown_fields(rel, hy_source, &growth.ledger, || growth.base.read(rel), &growth.approvals),
        }
    }

    pub fn load(root: &Path, decl: &DesignEvidenceDecl) -> (Self, Option<String>) {
        let growth = Growth::load(root, decl);
        let review_pattern = decl.review_pattern.as_deref().and_then(|p| regex::Regex::new(p).ok());
        let (existing, error) = match &decl.existing {
            None => (ExistingShapes::default(), None),
            Some(path) => match std::fs::read_to_string(root.join(path)).map_err(|e| e.to_string()).and_then(|text| ExistingShapes::parse(&text)) {
                Ok(existing) => (existing, None),
                Err(reason) => (ExistingShapes::default(), Some(format!("{}: cannot read the DOEFF212 ledger of existing shapes ({})", path, reason))),
            },
        };
        (Judging { existing, review_pattern, growth }, error)
    }

    /// Judges one module whose evidence file is read from disk (`hy_source` is the module's contents as passed in).
    pub fn judge_module(&self, rel: &str, path: &Path, hy_source: &str) -> Result<Vec<Hit>, String> {
        let evidence = std::fs::read_to_string(evidence_of(path)).ok();
        let mut hits = judge(rel, hy_source, evidence.as_deref(), &self.existing, self.review_pattern.as_ref())?;
        hits.extend(self.grown(rel, hy_source)?);
        Ok(hits)
    }

    /// Judges one evidence file's contents as passed in, against its sibling module read from disk (the hits are reported at the module).
    pub fn judge_evidence(&self, module_rel: &str, module_path: &Path, evidence: &str) -> Result<(String, Vec<Hit>), String> {
        let hy_source = std::fs::read_to_string(module_path).map_err(|error| format!("cannot read the sibling module: {}", error))?;
        let mut hits = judge(module_rel, &hy_source, Some(evidence), &self.existing, self.review_pattern.as_ref())?;
        hits.extend(self.grown(module_rel, &hy_source)?);
        Ok((hy_source, hits))
    }
}

/// The hits of one module.
pub struct FileHits {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub hits: Vec<Hit>,
}

/// Reads and judges the declared .hy files (only files under the focus if there is one). Unreadable files and an unreadable ledger return reasons.
pub fn find(root: &Path, decl: &DesignEvidenceDecl, focus: Option<&[PathBuf]>) -> (Vec<FileHits>, Vec<String>) {
    let (judging, ledger_error) = Judging::load(root, decl);
    let (found, mut errors) = judge_files(
        root,
        decl.selection.files.iter(),
        focus,
        |rel, _| wants(rel, decl),
        |rel, path, source| {
            let hits = judging.judge_module(&rel, &path, &source).map_err(|reason| format!("{}: cannot read the DOEFF212 judgment ({})", rel, reason))?;
            Ok((!hits.is_empty()).then(|| FileHits { rel, path, source, hits }))
        },
    );
    errors.extend(ledger_error);
    (found, errors)
}

/// The ledger of the current shapes of every defrecord in the population, as TOML (`--print-existing-shapes`; written once when the rule is turned on).
pub fn existing_shapes(root: &Path, decl: &DesignEvidenceDecl) -> (String, Vec<String>) {
    let (mut found, errors) = judge_files(
        root,
        decl.selection.files.iter(),
        None,
        |rel, _| wants(rel, decl),
        |rel, _, source| record_shapes(&source).map(|shapes| Some((rel.clone(), shapes))).map_err(|reason| format!("{}: {}", rel, reason)),
    );
    found.sort_by(|a, b| a.0.cmp(&b.0));
    (ExistingShapes::spell(found.iter().map(|(rel, shapes)| (rel.as_str(), shapes.as_slice()))), errors)
}

/// Adds, to the named paths, the module next to each named evidence file — so that when only the evidence is edited, its module is judged (focus) and its hits are kept.
pub fn with_sibling_modules(paths: Vec<PathBuf>) -> Vec<PathBuf> {
    let suffix = format!(".{}", EVIDENCE_SUFFIX);
    let modules: Vec<PathBuf> = paths
        .iter()
        .filter_map(|p| p.file_name().and_then(|n| n.to_str()).and_then(|n| n.strip_suffix(&suffix)).map(|stem| p.with_file_name(format!("{}.hy", stem))))
        .filter(|p| p.is_file())
        .collect();
    let mut out = paths;
    for module in modules {
        if !out.contains(&module) {
            out.push(module);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const HY: &str = "(defrecord Done\n  \"doc\"\n  {:tags {:role \"type\"}}\n  (#^ str card)\n  (#^ (| str  None) merged-at))\n";

    fn evidence(fields: &str, extra: &str) -> String {
        format!(
            "schema = 1\n[[element]]\nname = \"Done\"\nkind = \"record\"\nconcern = \"one done card\"\nkey = [\"card\"]\nfan_out = 2\nreview = \"r-1\"\n{}\n{}\n\
             [[element.rejected]]\nshape = \"a field on another type\"\nwhy = \"no owner\"\n",
            extra, fields
        )
    }

    const FIELDS: &str = "[[element.field]]\nname = \"card\"\ntype = \"str\"\nfact = \"the card\"\n\
                          [[element.field]]\nname = \"merged-at\"\ntype = \"(| str None)\"\nfact = \"when merged\"\nabsent_when = \"not merged\"\n";

    fn problems(hy: &str, evidence: Option<&str>, existing: &ExistingShapes) -> Vec<(String, Problem)> {
        judge("m.hy", hy, evidence, existing, None).expect("readable").into_iter().map(|h| (h.name, h.problem)).collect()
    }

    #[test]
    fn shapes_are_read_in_order_with_optional_fields() {
        let shapes = record_shapes(HY).expect("readable");
        assert_eq!(shapes.len(), 1);
        assert_eq!(shapes[0].name, "Done");
        let fields: Vec<(&str, Option<&str>, bool)> = shapes[0].fields.iter().map(|f| (f.name.as_str(), f.type_text.as_deref(), f.optional)).collect();
        assert_eq!(fields, vec![("card", Some("str"), false), ("merged-at", Some("(| str None)"), true)]);
        assert_eq!(shapes[0].canonical(), "defrecord\ncard: str\nmerged-at: (| str None)\n");
    }

    #[test]
    fn matching_complete_evidence_is_green() {
        assert_eq!(problems(HY, Some(&evidence(FIELDS, "")), &ExistingShapes::default()), vec![]);
    }

    #[test]
    fn a_new_type_without_evidence_is_missing() {
        let found = problems(HY, None, &ExistingShapes::default());
        assert!(matches!(found.as_slice(), [(name, Problem::Missing { was: None, .. })] if name == "Done"), "{:?}", found);
    }

    #[test]
    fn an_existing_unchanged_type_is_green_and_a_reshaped_one_is_missing() {
        let shapes = record_shapes(HY).expect("readable");
        let ledger = ExistingShapes::parse(&ExistingShapes::spell(std::iter::once(("m.hy", shapes.as_slice())))).expect("ledger");
        assert_eq!(problems(HY, None, &ledger), vec![]);
        let reshaped = HY.replace("merged-at))", "merged-at)\n  (#^ str extra))");
        let found = problems(&reshaped, None, &ledger);
        assert!(matches!(found.as_slice(), [(_, Problem::Missing { was: Some(_), .. })]), "{:?}", found);
    }

    #[test]
    fn a_shape_that_differs_from_the_evidence_is_a_hit() {
        let added = HY.replace("merged-at))", "merged-at)\n  (#^ int extra))");
        let found = problems(&added, Some(&evidence(FIELDS, "")), &ExistingShapes::default());
        assert!(matches!(found.as_slice(), [(_, Problem::ShapeDiffers { differences })] if differences[0].contains("extra")), "{:?}", found);
        let retyped = HY.replace("(#^ str card)", "(#^ int card)");
        let found = problems(&retyped, Some(&evidence(FIELDS, "")), &ExistingShapes::default());
        assert!(matches!(found.as_slice(), [(_, Problem::ShapeDiffers { differences })] if differences[0].contains("field card")), "{:?}", found);
    }

    #[test]
    fn missing_answers_are_gaps() {
        let bare = "schema = 1\n[[element]]\nname = \"Done\"\nkind = \"record\"\n\
                    [[element.field]]\nname = \"card\"\ntype = \"str\"\n[[element.field]]\nname = \"merged-at\"\ntype = \"(| str None)\"\n";
        let found = problems(HY, Some(bare), &ExistingShapes::default());
        let [(_, Problem::Incomplete { gaps })] = found.as_slice() else { panic!("{:?}", found) };
        for word in ["concern", "key", "fact", "absent_when", "rejected", "fan_out", "review"] {
            assert!(gaps.iter().any(|g| g.contains(word)), "no gap about {}: {:?}", word, gaps);
        }
    }

    #[test]
    fn review_spelling_follows_the_declared_pattern() {
        let pattern = regex::Regex::new("^r-[0-9]+$").unwrap();
        let ok = judge("m.hy", HY, Some(&evidence(FIELDS, "")), &ExistingShapes::default(), Some(&pattern)).unwrap();
        assert!(ok.is_empty(), "{:?}", ok);
        let bad = evidence(FIELDS, "").replace("r-1", "someone");
        let found = judge("m.hy", HY, Some(&bad), &ExistingShapes::default(), Some(&pattern)).unwrap();
        assert!(matches!(&found[0].problem, Problem::Incomplete { gaps } if gaps[0].contains("review")), "{:?}", found);
    }

    #[test]
    fn orphan_and_unreadable_evidence_are_hits() {
        let found = problems("(defrecord Other (#^ str a))\n", Some(&evidence(FIELDS, "")), &ExistingShapes { shapes: BTreeMap::new() });
        assert!(found.iter().any(|(name, p)| name == "Done" && *p == Problem::Orphan), "{:?}", found);
        let found = problems(HY, Some("schema = 1\n[[element]]\nname = 3\n"), &ExistingShapes::default());
        assert!(matches!(found.as_slice(), [(_, Problem::Unreadable { .. })]), "{:?}", found);
        let found = problems(HY, Some("schema = 2\n"), &ExistingShapes::default());
        assert!(matches!(found.as_slice(), [(_, Problem::Unreadable { .. })]), "{:?}", found);
    }

    #[test]
    fn a_ledger_type_that_gains_a_field_grew_even_with_evidence_unless_approved() {
        let shapes = record_shapes(HY).expect("readable");
        let ledger = ExistingShapes::parse(&ExistingShapes::spell(std::iter::once(("m.hy", shapes.as_slice())))).expect("ledger");
        let none = super::super::ledger_guard::Approvals::default();
        assert!(grown_fields("m.hy", HY, &ledger, || panic!("an unchanged type reads no base"), &none).unwrap().is_empty());
        let added = HY.replace("merged-at))", "merged-at)\n  (#^ (| str None) gap))");
        let found = grown_fields("m.hy", &added, &ledger, || Some(HY.to_string()), &none).unwrap();
        assert!(matches!(found.as_slice(), [Hit { problem: Problem::Grew { fields }, .. }] if fields == &vec!["gap".to_string()]), "{:?}", found);
        let approved = super::super::ledger_guard::Approvals::parse(
            "[[approval]]\nledger = \"fields\"\nkey = \"m.hy::Done\"\nvalue = \"gap\"\nreview = \"req-1\"\nwhy = \"a fact of the key\"\n",
        )
        .unwrap();
        assert!(grown_fields("m.hy", &added, &ledger, || Some(HY.to_string()), &approved).unwrap().is_empty());
        // A retyped field is a reshape (evidence answers it), not growth.
        let retyped = HY.replace("(#^ str card)", "(#^ int card)");
        assert!(grown_fields("m.hy", &retyped, &ledger, || Some(HY.to_string()), &none).unwrap().is_empty());
    }

    #[test]
    fn evidence_paths_pair_with_modules() {
        assert_eq!(evidence_of(Path::new("a/b.hy")), PathBuf::from("a/b.evidence.toml"));
        assert_eq!(evidence_rel("a/b.hy"), "a/b.evidence.toml");
        assert_eq!(module_of("a/b.evidence.toml", Path::new("/r/a/b.evidence.toml")), Some(("a/b.hy".to_string(), PathBuf::from("/r/a/b.hy"))));
        assert_eq!(module_of("a/b.toml", Path::new("/r/a/b.toml")), None);
    }
}

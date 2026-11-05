//! Result types of the layer rules (a violation, its standing, its origin, a module summary, the whole result). Readers (editor.rs, notice.rs, unreadable.rs)
//! read from here directly: reading back from the assembler project/mod.rs would form a dependency cycle with the child modules that mod.rs declares.

use std::collections::BTreeSet;
use std::path::PathBuf;

use crate::models::Severity;
use crate::position::Range;

use super::explain::Explanation;
use super::rule::ProjectRule;
use super::{semantic, signatures};

/// One violation (after the law and the registry have been applied).
#[derive(Debug, Clone)]
pub struct Finding {
    pub rule: ProjectRule,
    pub law: Option<String>,
    pub adr: Option<String>,
    pub severity: Severity,
    /// Absolute path of the file.
    pub path: PathBuf,
    /// Path from the repo root (separator `/`).
    pub rel: String,
    pub range: Range,
    pub message: String,
    pub hint: String,
    /// Registry key `<path>::<law or rule ID>[::<detail>]`.
    pub key: String,
    /// Whether the key is listed in the registry (true even for a rule under reconciliation, if listed).
    pub registered: bool,
    /// The rule's own severity before the registry and reconciliation are applied (`severity` is the severity after lowering this).
    pub base_severity: Severity,
    /// Whether it is a new violation, a known violation listed in the registry, or lowered because of reconciliation.
    pub standing: Standing,
    /// What this is, why it is a violation, and the text of the law (built by explain.rs).
    pub explanation: Explanation,
    /// Origin of the judgment (deterministic rule or Jev).
    pub origin: FindingOrigin,
    /// Probability of the Jev judgment (only for Jev violations).
    pub probability: Option<f64>,
}

/// Standing of a violation: a closed set of the reasons the severity was lowered (so the editor can count "untouched heavy violations").
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Standing {
    /// A new violation not in the registry (severity is the rule's own).
    New,
    /// A known violation listed in the registry (severity has been lowered).
    Registered,
    /// A violation of a rule under reconciliation (`registry.reconciling`: lowered to info regardless of whether it is in the registry).
    Reconciling,
}

impl Standing {
    /// Decides the standing from whether it is in the registry and whether it is under reconciliation (reconciliation first, since that is the reason the severity was lowered to info).
    pub fn of(registered: bool, reconciling: bool) -> Standing {
        match (reconciling, registered) {
            (true, _) => Standing::Reconciling,
            (false, true) => Standing::Registered,
            (false, false) => Standing::New,
        }
    }
}

/// Origin of a violation's judgment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum FindingOrigin {
    /// A deterministic rule.
    Linter,
    /// A semantic judgment by Jev.
    Jev,
}

/// Material for the map: one module that the layer rules read.
#[derive(Debug, Clone)]
pub struct ModuleSummary {
    pub path: PathBuf,
    pub rel: String,
    pub layer: Option<String>,
    pub context: Option<String>,
    pub role: Option<String>,
    /// What decided the layer (the path's location, a tag, or a mismatch between the two).
    pub layer_reason: Option<String>,
    /// Name of the service that matched the `*` segment of the location (None if none).
    pub service: Option<String>,
}

/// The whole result of the layer rules.
#[derive(Debug, Clone, Default)]
pub struct ProjectReport {
    pub findings: Vec<Finding>,
    pub modules: Vec<ModuleSummary>,
    /// Reasons for files, registries, and catalogs that could not be read.
    pub errors: Vec<String>,
    /// Notices that are not errors (a missing registry dir was read as empty).
    pub notes: Vec<String>,
    /// Summary of the semantic rules (None if there is no configuration).
    pub semantic: Option<semantic::SemanticSummary>,
    /// The effect-inference table built in a one-file run (only when built; the written file's contents used as an overlay).
    pub world: Option<signatures::World>,
    /// The layer rules this run judged: rules that finished emitting their findings, including rules with 0 findings. A whole run covers all enabled rules,
    /// and a one-file run covers only the rules with `ProjectRule::judged_on_one_file`. Every rule in findings is in this set. Material for editor-json's
    /// `judged_rules` (the editor replaces only the violations of judged rules with the one-file result).
    pub judged: BTreeSet<ProjectRule>,
}

//! Validated form of a law mapping — the law's name, text, the layers it applies to, and the rule IDs written in the law (layer rules or existing Python rules).
//! Read by the explanation (`explain`) and the settings assembly (`settings`) (split out of settings.rs).

use std::collections::BTreeSet;

use super::layers::LayerId;
use super::rule::ProjectRule;

/// One law mapping (after validation).
#[derive(Debug, Clone)]
pub struct LawSpec {
    pub name: String,
    pub adr: Option<String>,
    pub statement: String,
    pub rules: Vec<ProjectRuleOrExternal>,
    /// Layers it applies to (all layers if empty).
    pub layers: BTreeSet<LayerId>,
}

/// A rule ID written in a law — a layer rule (closed list) or the ID of an existing Python rule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProjectRuleOrExternal {
    Project(ProjectRule),
    External(String),
}

impl ProjectRuleOrExternal {
    /// Returns the spelling of the rule ID (for output and matching).
    pub fn id(&self) -> &str {
        match self {
            ProjectRuleOrExternal::Project(rule) => rule.id(),
            ProjectRuleOrExternal::External(id) => id,
        }
    }
}

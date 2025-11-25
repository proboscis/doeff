//! Base trait for all lint rules

use crate::models::{RuleContext, Violation};

/// Base trait that all lint rules must implement
pub trait LintRule: Send + Sync {
    /// The unique identifier for this rule (e.g., "DOEFF001")
    fn rule_id(&self) -> &str;

    /// Short description of what the rule checks
    fn description(&self) -> &str;

    /// Check if this rule is enabled (default: true)
    fn is_enabled(&self) -> bool {
        true
    }

    /// Perform the lint check on a statement
    fn check(&self, context: &RuleContext) -> Vec<Violation>;
}




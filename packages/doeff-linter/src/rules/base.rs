//! Base trait for all lint rules

use crate::models::{RuleContext, Violation};

/// The unit a rule looks at in one pass. The driver (lib's check_stmt_recursive) passes statements exactly per this unit.
/// If the unit is not part of the contract between rule and driver, a rule that descends into nested statements itself or one that views the whole module counts
/// the same hit as many times as the number of statements the driver passes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleReach {
    /// Looks only at the one statement passed (its own expressions). Nested statements are passed one at a time by the driver — applied to every statement.
    Statement,
    /// Walks the passed statement and its nested statements itself (for rules that need context such as an enclosing try or a class field). The driver passes only the module's
    /// top-level statements.
    Subtree,
    /// Looks at the whole module. The driver calls once per file, passing the module's first statement as `stmt`.
    Module,
}

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

    /// The unit this rule looks at in one pass (default = one statement). The driver calls `check` accordingly.
    fn reach(&self) -> RuleReach {
        RuleReach::Statement
    }

    /// Perform the lint check on a statement
    fn check(&self, context: &RuleContext) -> Vec<Violation>;
}




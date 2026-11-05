//! DOEFF032: Violation of the rule population declaration
//!
//! A layer declaration may exempt rules from the population only for named modules (`(layer NAME :modules [..] :exempt [(rule RULE-ID "reason")]
//! :forbid-modules [..])`). What is exempted is only code outside a Program (the watcher entered via `.pth` at Python startup, and the like); if business code
//! gets in there, the exempted rules no longer apply to that code either. This rule makes the following two cases errors:
//!   - A module of an exempted layer imports a module in that layer's `:forbid-modules` (business code entered the exempted layer)
//!   - The architecture.hy of a package that has files cannot be read, so the population of those files cannot be decided (name the unreadable file instead of exempting)
//!
//! The hits are emitted at a single point that looks up the nearest architecture.hy for each file (crate::population — lib::lint_source_at). The per-statement context (RuleContext) has
//! no layer for the file, so this rule's check returns nothing and only holds the rule name and the enable / disable switch (enable / disable in the settings).

use crate::models::{RuleContext, Violation};
use crate::rules::base::LintRule;

/// The ID of this rule. crate::population, which emits the hits, borrows it from here (the rule side does not know the population's single point — this keeps the dependency in one direction).
pub const RULE_ID: &str = "DOEFF032";

pub struct RulePopulationDeclarationRule;

impl RulePopulationDeclarationRule {
    pub fn new() -> Self {
        Self
    }
}

impl Default for RulePopulationDeclarationRule {
    fn default() -> Self {
        Self::new()
    }
}

impl LintRule for RulePopulationDeclarationRule {
    fn rule_id(&self) -> &str {
        RULE_ID
    }

    fn description(&self) -> &str {
        "Business code is in a layer exempted from the rule population, or the declaration that decides the population cannot be read"
    }

    fn check(&self, _context: &RuleContext) -> Vec<Violation> {
        // Hits are emitted per file by crate::population (see the header note above).
        Vec::new()
    }
}

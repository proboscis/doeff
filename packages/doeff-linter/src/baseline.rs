//! Comparison with the baseline (`--baseline-report <file>`, spec section 1 "Comparison with the baseline").
//!
//! The pre-merge check and the commit hook keep no saved list of known violations; they run the linter on both the baseline (main's tip) and the commit,
//! and pass if the set of the commit's critical identifiers is contained in the baseline's set (decision A of #1762). Since the comparison is by identifier rather than count,
//! a commit that fixes 3 and adds 1 turns red. Running the baseline side is the caller's job; this module only compares the two sets.

use std::collections::BTreeSet;

use serde_json::Value;

use crate::editor::EditorViolation;
use crate::project::rule::{ProjectRule, RuleLevel};

/// Violation identifier — `<path>::<rule>::<name>` (line numbers not included). A violation with a key (layer rules etc.) uses the key itself; a violation without a key
/// (Python per-statement rules) uses the message instead of the name.
pub fn identity(violation: &EditorViolation) -> String {
    match &violation.key {
        Some(key) => key.clone(),
        None => format!("{}::{}::{}", violation.path, violation.rule, violation.message),
    }
}

/// The set of critical identifiers of this run.
pub fn critical_identities(violations: &[EditorViolation]) -> BTreeSet<String> {
    violations.iter().filter(|v| v.level == RuleLevel::Critical).map(identity).collect()
}

/// Reads the set of critical identifiers from the editor-json output run on the baseline. Returns the reason if the shape is wrong (the caller exits with code 2).
pub fn read_baseline(text: &str) -> Result<BTreeSet<String>, String> {
    let report: Value = serde_json::from_str(text).map_err(|e| format!("baseline output is not JSON: {}", e))?;
    baseline_identities(&report)
}

/// The set of critical identifiers in the baseline's editor-json output (an already-parsed value) — read_baseline and the commit hook (commit_hook::judge), which separates whether a blocked hit also exists in the HEAD version,
/// read it with the same spelling.
pub fn baseline_identities(report: &Value) -> Result<BTreeSet<String>, String> {
    let violations = report
        .get("violations")
        .and_then(Value::as_array)
        .ok_or_else(|| "baseline output has no violations array (pass editor-json output)".to_string())?;
    let mut identities = BTreeSet::new();
    for violation in violations {
        if violation.get("level").and_then(Value::as_str) != Some("critical") {
            continue;
        }
        let field = |name: &str| {
            violation.get(name).and_then(Value::as_str).ok_or_else(|| format!("baseline violation has no field {}: {}", name, violation))
        };
        let identity = match violation.get("key").and_then(Value::as_str) {
            Some(key) => key.to_string(),
            None => format!("{}::{}::{}", field("path")?, field("rule")?, field("message")?),
        };
        identities.insert(identity);
    }
    Ok(identities)
}

/// Whether a violation is one to block without lowering by the baseline diff — rule `rule` has no known list (a rule the registry does not lower —
/// `ProjectRule::lowered_by_registry` is false = DOEFF206-209, 211) and the severity is critical. The single place for the decision to block if it is in a changed file —
/// used by the named run's new_critical (blocking_criticals — the commit hook also runs a child linter on staged paths and reads this).
pub fn blocks_regardless_of_baseline(rule: &str, level: RuleLevel) -> bool {
    level == RuleLevel::Critical && ProjectRule::parse(rule).is_some_and(|rule| !rule.lowered_by_registry())
}

/// Of this run's violations, the identifiers of hits to block without lowering by the baseline diff.
fn unlisted_rule_criticals(violations: &[EditorViolation]) -> impl Iterator<Item = String> + '_ {
    violations.iter().filter(|v| blocks_regardless_of_baseline(&v.rule, v.level)).map(identity)
}

/// Identifiers of critical violations to block (sorted) — in addition to criticals absent from the baseline (new_criticals), in a run with named paths (`named` — the commit hook naming changed files and the check before registration),
/// all criticals of rules without a known list (cisco-c8 approved).
/// Such rules let neither the baseline diff nor the registry pass existing hits, and block if a changed file has a hit (granularity is per file). violations is
/// the list after narrowing by the named paths. A run without named paths (whole) uses only the baseline diff — the whole count is reported daily.
pub fn blocking_criticals(baseline: &BTreeSet<String>, violations: &[EditorViolation], named: bool) -> Vec<String> {
    let fresh = new_criticals(baseline, &critical_identities(violations));
    if !named {
        return fresh;
    }
    fresh.into_iter().chain(unlisted_rule_criticals(violations)).collect::<BTreeSet<String>>().into_iter().collect()
}

/// The part of an identifier excluding the path (`<rule>::<name>`).
fn without_path(identity: &str) -> &str {
    identity.split_once("::").map_or(identity, |(_, rest)| rest)
}

/// Identifiers of criticals absent from the baseline (sorted). A file move/rename, where rule and name are the same and only the path differs and the baseline's identifier is now
/// gone, is regarded as the same violation 1:1 (if the baseline identifier remains and the same name appears under another path, it is a new violation).
pub fn new_criticals(baseline: &BTreeSet<String>, current: &BTreeSet<String>) -> Vec<String> {
    let mut vanished: Vec<&String> = baseline.difference(current).collect();
    let mut fresh = Vec::new();
    for identity in current.difference(baseline) {
        match vanished.iter().position(|old| without_path(old) == without_path(identity)) {
            Some(index) => {
                vanished.remove(index);
            }
            None => fresh.push(identity.clone()),
        }
    }
    fresh
}

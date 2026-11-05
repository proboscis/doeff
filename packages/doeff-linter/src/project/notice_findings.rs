//! Turns a config notice (DOEFF100) into a violation (Finding) — the side that reads the Explanation and the violation type is split off from the notice type and the key-location reading
//! (`notice`). `notice` is read from below by `architecture` (which reads declarations) and `settings` (which builds settings), so it must not read violations or explanations.

use std::path::Path;

use super::explain::Explanation;
use super::notice::{ConfigNotice, NoticeKind};
use super::paths::relative_path;
use super::report::{Finding, FindingOrigin};
use super::rule::ProjectRule;
use crate::models::Severity;

/// Turns notices into DOEFF100 violations (warning, outside the registry, a deterministic rule).
pub fn findings(notices: &[ConfigNotice], root: &Path) -> Vec<Finding> {
    let rule = ProjectRule::UnknownConfigKey;
    notices
        .iter()
        .map(|notice| {
            let rel = relative_path(root, &notice.file).unwrap_or_else(|| notice.file.to_string_lossy().into_owned());
            let (message, subject, what) = match &notice.kind {
                NoticeKind::Key => (
                    format!("This linter does not know the config key {} — the remaining rules ran without reading it", notice.key),
                    format!("This is the key {1} in {0}", rel, notice.key),
                    "this key",
                ),
                NoticeKind::RuleId(id) => (
                    format!("This linter does not know a rule referenced by config {}: {} — the remaining rules ran without reading only that reference", notice.key, id),
                    format!("This is, in {}, the rule {} referenced by {}", rel, id, notice.key),
                    "this rule",
                ),
            };
            let detail = match &notice.kind {
                NoticeKind::Key => notice.key.clone(),
                NoticeKind::RuleId(id) => format!("{}::{}", notice.key, id),
            };
            Finding {
                rule,
                law: None,
                adr: None,
                severity: Severity::Warning,
                path: notice.file.clone(),
                key: format!("{}::{}::{}", rel, rule.id(), detail),
                rel,
                range: notice.range,
                message,
                hint: rule.hint().to_string(),
                registered: false,
                base_severity: Severity::Warning,
                standing: super::report::Standing::New,
                explanation: Explanation {
                    subject,
                    reason: format!(
                        "This linter (built from doeff {}) does not know {}. Either the linter is older than the config (the binary in the dev-build location is rebuilt automatically from doeff's mainline — wait for that) or it is a typo",
                        crate::build_info::BUILD_COMMIT,
                        what
                    ),
                    law_statement: None,
                },
                origin: FindingOrigin::Linter,
                probability: None,
            }
        })
        .collect()
}

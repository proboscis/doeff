//! Notice for unreadable Hy files (DOEFF128).
//!
//! A file where the reader finds an unclosed bracket or string gets empty rule verdicts (facts exist only up to the readable part). Previously the reason
//! was only pushed onto the global `errors`, so the editor's violation pane for that file was empty and the agent hook said nothing (2026-09-28 — agora's
//! controllers/durable/protocol/contract.hy could not be read because of a reader bug, and violations silently came out empty). So an unreadable file is
//! reported, regardless of the enabled rule set, as an error violation DOEFF128 at the file's first unreadable place (shown in the editor pane, the hook and text output alike).

use std::path::Path;

use doeff_indexer::hy_index::reader::{ReadIssue, Reader};

use super::explain::Explanation;
use super::rule::ProjectRule;
use super::report::{Finding, FindingOrigin};
use crate::models::Severity;
use crate::position::LineIndex;

/// Byte position of the broken spot in reading, with a short description.
fn describe(issue: &ReadIssue) -> (usize, &'static str) {
    match issue {
        ReadIssue::Unclosed { open, .. } => (*open, "opening bracket is not closed before the end of the file"),
        ReadIssue::Mismatched { at, .. } => (*at, "closing bracket does not match the opening bracket"),
        ReadIssue::StrayCloser { at } => (*at, "stray closing bracket"),
        ReadIssue::UnterminatedString { start } => (*start, "string is not closed before the end of the file"),
    }
}

/// Reads one Hy file and returns one DOEFF128 violation if any part is unreadable (positioned at the first unreadable place).
pub fn finding(rel: &str, path: &Path, source: &str) -> Option<Finding> {
    let mut reader = Reader::new(source, 0, source.len());
    reader.read_all();
    let mut issues: Vec<(usize, &'static str)> = reader.issues.iter().map(describe).collect();
    issues.sort_by_key(|(at, _)| *at);
    let (first, what) = *issues.first()?;
    let lines = LineIndex::new(source);
    let range = lines.range(first, (first + 1).min(source.len()));
    let rule = ProjectRule::UnreadableFile;
    let line = range.start.line + 1;
    Some(Finding {
        rule,
        law: None,
        adr: None,
        severity: Severity::Error,
        path: path.to_path_buf(),
        key: format!("{}::{}", rel, rule.id()),
        rel: rel.to_string(),
        range,
        message: format!(
            "cannot read {} — {} unreadable place(s) (the first is at line {}: {}). Rule verdicts for this file cover only the readable part, so violations are missing",
            rel,
            issues.len(),
            line,
            what
        ),
        hint: rule.hint().to_string(),
        registered: false,
        base_severity: Severity::Error,
        standing: super::report::Standing::New,
        explanation: Explanation {
            subject: format!("{} line {} — {}", rel, line, what),
            reason: "The Hy reader of doeff-linter could not read this file to the end. Rules are judged only on the readable part, so violations in this file are missing (an empty result does not mean it passes). If Hy itself can read it, this is a reader bug, so report it to doeff-linter; if Hy cannot read it either, fix the brackets or strings.".to_string(),
            law_statement: None,
        },
        origin: FindingOrigin::Linter,
        probability: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unreadable_file_is_an_error_at_its_first_broken_place() {
        let found = finding("app/x.hy", Path::new("/r/app/x.hy"), "(defk ok [] 1)\n(defk broken [x]\n  (print \"no end)\n").expect("unreadable");
        assert_eq!(found.rule, ProjectRule::UnreadableFile);
        assert_eq!(found.severity, Severity::Error);
        assert_eq!(found.key, "app/x.hy::DOEFF128");
        assert_eq!(found.range.start.line, 1);
        assert!(found.message.contains("unreadable place(s)"), "{}", found.message);
        // A readable file yields nothing (strings inside an f-string field are readable too).
        assert!(finding("app/y.hy", Path::new("/r/app/y.hy"), "(print f\"{(.join \"; \" xs)}\")\n").is_none());
    }
}

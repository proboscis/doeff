//! noqa comment parsing and handling
//!
//! Supports inline comments to suppress lint rules:
//! - `# noqa` - suppress all rules on this line
//! - `# noqa: DOEFF001` - suppress specific rule (space after colon)
//! - `# noqa:DOEFF001` - suppress specific rule (no space after colon)
//! - `# noqa: DOEFF001, DOEFF002` - suppress multiple rules
//! - `# noqa:DOEFF001,DOEFF002` - suppress multiple rules (no spaces)
//! - `# noqa: doeff001` - rule IDs are case-insensitive
//! - `#noqa: DOEFF001` - no space after hash is also supported
//! - `# noqa: DOEFF001 - explanation` - trailing comment after ` - ` is allowed
//!
//! File-level noqa (suppress rules for entire file):
//! - `# noqa: file` or `# noqa: FILE` - suppress all rules for entire file
//! - `# noqa: file=DOEFF001` - suppress specific rule for entire file
//! - `# noqa: file=DOEFF001,DOEFF002` - suppress multiple rules for entire file
//! - Must appear before any code (only comments, docstrings, and blank lines allowed before)
//!
//! Supports various error code formats:
//! - `DOEFF001`, `E501`, `W503` - letter+digit codes
//! - `error_code`, `my_rule` - underscore-separated codes
//! - `some-rule`, `type-arg` - hyphen-separated codes (e.g., mypy style)

use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::{HashMap, HashSet};

static NOQA_REGEX: Lazy<Regex> = Lazy::new(|| {
    // Match noqa comments with optional rule IDs
    // Supports: # noqa, # noqa: DOEFF001, # noqa:DOEFF001,DOEFF002
    // Supports any error code format: E501, error_code, some-rule, etc.
    // Case-insensitive for rule IDs (doeff001 == DOEFF001)
    Regex::new(r"(?i)#\s*noqa(?:\s*:\s*([A-Za-z0-9_\-,\s=]+))?").unwrap()
});

static FILE_NOQA_REGEX: Lazy<Regex> = Lazy::new(|| {
    // Match file-level noqa: # noqa: file or # noqa: file=DOEFF001,DOEFF002
    // The regex captures the part after "file=" if present
    Regex::new(r"(?i)^file(?:\s*=\s*([A-Za-z0-9_\-,\s]+))?$").unwrap()
});

/// A warning from noqa parsing
#[derive(Debug, Clone)]
pub struct NoqaWarning {
    /// Line number (1-indexed)
    pub line: usize,
    /// Warning message
    pub message: String,
    /// Suggestion for fix
    pub suggestion: String,
}

/// Parsed noqa directives for a file
#[derive(Debug, Default)]
pub struct NoqaDirectives {
    /// Lines where all rules are suppressed
    pub suppress_all: HashSet<usize>,
    /// Lines where specific rules are suppressed: line -> set of rule IDs
    pub suppress_rules: HashMap<usize, HashSet<String>>,
    /// File-level: suppress all rules for entire file
    pub file_suppress_all: bool,
    /// File-level: suppress specific rules for entire file
    pub file_suppress_rules: HashSet<String>,
    /// Warnings from parsing noqa comments
    pub warnings: Vec<NoqaWarning>,
}

impl NoqaDirectives {
    /// Parse noqa directives from source code
    pub fn parse(source: &str) -> Self {
        let mut directives = NoqaDirectives::default();
        let mut in_file_header = true;
        let mut in_docstring = false;
        let mut docstring_delimiter: Option<&str> = None;

        for (line_num, line) in source.lines().enumerate() {
            let line_number = line_num + 1; // 1-indexed
            let trimmed = line.trim();

            // Track if we're still in the file header (before any code)
            if in_file_header {
                // Check for docstring start/end
                if !in_docstring {
                    if trimmed.starts_with("\"\"\"") || trimmed.starts_with("'''") {
                        in_docstring = true;
                        docstring_delimiter = Some(if trimmed.starts_with("\"\"\"") { "\"\"\"" } else { "'''" });
                        // Check if docstring ends on same line
                        let delimiter = docstring_delimiter.unwrap();
                        if trimmed.len() > 3 && trimmed[3..].contains(delimiter) {
                            in_docstring = false;
                            docstring_delimiter = None;
                        }
                    } else if !trimmed.is_empty() && !trimmed.starts_with('#') {
                        // Non-empty, non-comment, non-docstring line = end of file header
                        in_file_header = false;
                    }
                } else {
                    // Check for docstring end
                    if let Some(delimiter) = docstring_delimiter {
                        if trimmed.ends_with(delimiter) && !trimmed.starts_with(delimiter) {
                            in_docstring = false;
                        } else if trimmed == delimiter {
                            in_docstring = false;
                        }
                    }
                }
            }

            if let Some(caps) = NOQA_REGEX.captures(line) {
                if let Some(rules_match) = caps.get(1) {
                    // Strip trailing comment after " - " (e.g., "DOEFF001 - explanation")
                    let rules_str_raw = rules_match.as_str().trim();
                    
                    // Check for common mistakes and add warnings
                    directives.check_noqa_format(line_number, rules_str_raw);
                    
                    let rules_str = if let Some(pos) = rules_str_raw.find(" - ") {
                        rules_str_raw[..pos].trim()
                    } else if let Some(pos) = rules_str_raw.find(" -- ") {
                        rules_str_raw[..pos].trim()
                    } else {
                        rules_str_raw
                    };
                    
                    // Check for file-level noqa: # noqa: file or # noqa: file=DOEFF001
                    if let Some(file_caps) = FILE_NOQA_REGEX.captures(rules_str) {
                        if in_file_header {
                            if let Some(specific_rules) = file_caps.get(1) {
                                // File-level with specific rules: # noqa: file=DOEFF001
                                let rules: HashSet<String> = specific_rules
                                    .as_str()
                                    .split(',')
                                    .map(|s| s.trim().to_uppercase())
                                    .filter(|s| !s.is_empty())
                                    .collect();
                                directives.file_suppress_rules.extend(rules);
                            } else {
                                // File-level suppress all: # noqa: file
                                directives.file_suppress_all = true;
                            }
                        }
                        // Also treat as line-level suppression for the comment line itself
                        continue;
                    }
                    
                    // Regular line-level specific rules: # noqa: DOEFF001, DOEFF002
                    let rules: HashSet<String> = rules_str
                        .split(',')
                        .map(|s| s.trim().to_uppercase())
                        .filter(|s| !s.is_empty())
                        .collect();

                    if !rules.is_empty() {
                        directives
                            .suppress_rules
                            .entry(line_number)
                            .or_default()
                            .extend(rules);
                    }
                } else {
                    // Suppress all: # noqa
                    directives.suppress_all.insert(line_number);
                }
            }
        }

        directives
    }

    /// Check if a rule is suppressed at a given line
    pub fn is_suppressed(&self, line: usize, rule_id: &str) -> bool {
        // Check file-level suppressions first
        if self.file_suppress_all {
            return true;
        }

        if self.file_suppress_rules.contains(rule_id) {
            return true;
        }

        // Check line-level suppressions
        if self.suppress_all.contains(&line) {
            return true;
        }

        if let Some(rules) = self.suppress_rules.get(&line) {
            if rules.contains(rule_id) {
                return true;
            }
        }

        false
    }

    /// Check if file-level suppression is active
    pub fn has_file_level_suppression(&self) -> bool {
        self.file_suppress_all || !self.file_suppress_rules.is_empty()
    }

    /// Check noqa format and add warnings for common mistakes
    fn check_noqa_format(&mut self, line_number: usize, rules_str: &str) {
        // Skip file-level noqa checks
        if rules_str.to_lowercase().starts_with("file") {
            return;
        }

        // Pattern: "DOEFF001- comment" (missing space before dash)
        // This looks like "DOEFF001-" followed by more text without proper spacing
        let re_missing_space = Lazy::new(|| {
            Regex::new(r"([A-Z]+\d+)-[a-zA-Z]").unwrap()
        });
        if re_missing_space.is_match(rules_str) {
            self.warnings.push(NoqaWarning {
                line: line_number,
                message: format!(
                    "Possible malformed noqa comment: `{}`",
                    rules_str
                ),
                suggestion: "Use ` - ` (space-dash-space) to separate the rule ID from the comment. Example: `# noqa: DOEFF001 - explanation`".to_string(),
            });
            return;
        }

        // Pattern: rule ID looks like it contains prose (e.g., "DOEFF001 this is wrong")
        // After splitting by comma, each part should look like a rule ID
        for part in rules_str.split(',') {
            let part = part.trim();
            if part.is_empty() {
                continue;
            }
            
            // Check if part looks like "RULEID followed by prose" without proper separator
            // e.g., "DOEFF001 this comment" instead of "DOEFF001 - this comment"
            let re_rule_with_prose = Lazy::new(|| {
                Regex::new(r"^([A-Za-z0-9_-]+)\s+[a-zA-Z]{2,}").unwrap()
            });
            if re_rule_with_prose.is_match(part) {
                // Check it's not a valid pattern like "file=DOEFF001"
                if !part.contains('=') {
                    self.warnings.push(NoqaWarning {
                        line: line_number,
                        message: format!(
                            "Possible malformed noqa comment: `{}`",
                            part
                        ),
                        suggestion: "Use ` - ` (space-dash-space) to separate the rule ID from the comment. Example: `# noqa: DOEFF001 - explanation`".to_string(),
                    });
                    return;
                }
            }
        }
    }
}

/// Convert byte offset to line number (1-indexed)
pub fn offset_to_line(source: &str, offset: usize) -> usize {
    source[..offset.min(source.len())]
        .chars()
        .filter(|&c| c == '\n')
        .count()
        + 1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_noqa_all() {
        let source = r#"
def foo():  # noqa
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_all.contains(&2));
    }

    #[test]
    fn test_parse_noqa_specific() {
        let source = r#"
def dict():  # noqa: DOEFF001
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(!directives.suppress_all.contains(&2));
        assert!(directives.suppress_rules.get(&2).unwrap().contains("DOEFF001"));
    }

    #[test]
    fn test_parse_noqa_multiple() {
        let source = r#"
data["key"] = value  # noqa: DOEFF007, DOEFF008
"#;
        let directives = NoqaDirectives::parse(source);
        let rules = directives.suppress_rules.get(&2).unwrap();
        assert!(rules.contains("DOEFF007"));
        assert!(rules.contains("DOEFF008"));
    }

    #[test]
    fn test_is_suppressed() {
        let source = r#"
line1  # noqa
line2  # noqa: DOEFF001
line3
"#;
        let directives = NoqaDirectives::parse(source);

        // Line 2: suppress all
        assert!(directives.is_suppressed(2, "DOEFF001"));
        assert!(directives.is_suppressed(2, "DOEFF002"));

        // Line 3: suppress specific
        assert!(directives.is_suppressed(3, "DOEFF001"));
        assert!(!directives.is_suppressed(3, "DOEFF002"));

        // Line 4: no suppression
        assert!(!directives.is_suppressed(4, "DOEFF001"));
    }

    #[test]
    fn test_offset_to_line() {
        let source = "line1\nline2\nline3";
        assert_eq!(offset_to_line(source, 0), 1);
        assert_eq!(offset_to_line(source, 5), 1);
        assert_eq!(offset_to_line(source, 6), 2);
        assert_eq!(offset_to_line(source, 12), 3);
    }

    #[test]
    fn test_parse_noqa_no_space_after_colon() {
        // Support # noqa:DOEFF004 syntax (no space after colon)
        let source = r#"
x = os.environ["KEY"]  # noqa:DOEFF004
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(!directives.suppress_all.contains(&2));
        assert!(directives.suppress_rules.get(&2).unwrap().contains("DOEFF004"));
    }

    #[test]
    fn test_parse_noqa_no_space_before_colon() {
        // Support # noqa: DOEFF004 and #noqa:DOEFF004 variants
        let source = r#"
x = 1  #noqa:DOEFF001
y = 2  #noqa: DOEFF002
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_rules.get(&2).unwrap().contains("DOEFF001"));
        assert!(directives.suppress_rules.get(&3).unwrap().contains("DOEFF002"));
    }

    #[test]
    fn test_parse_noqa_multiple_no_spaces() {
        // Support # noqa:DOEFF001,DOEFF002 (no spaces around comma)
        let source = r#"
data["key"] = value  # noqa:DOEFF007,DOEFF008
"#;
        let directives = NoqaDirectives::parse(source);
        let rules = directives.suppress_rules.get(&2).unwrap();
        assert!(rules.contains("DOEFF007"));
        assert!(rules.contains("DOEFF008"));
    }

    #[test]
    fn test_parse_noqa_lowercase() {
        // Rule IDs should be case-insensitive
        let source = r#"
x = 1  # noqa: doeff001
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_rules.get(&2).unwrap().contains("DOEFF001"));
    }

    #[test]
    fn test_parse_noqa_underscore_codes() {
        // Support error codes with underscores (e.g., error_code, my_rule)
        let source = r#"
x = 1  # noqa: error_code
y = 2  # noqa:my_custom_rule
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_rules.get(&2).unwrap().contains("ERROR_CODE"));
        assert!(directives.suppress_rules.get(&3).unwrap().contains("MY_CUSTOM_RULE"));
    }

    #[test]
    fn test_parse_noqa_hyphen_codes() {
        // Support error codes with hyphens (e.g., mypy's type-arg, no-untyped-def)
        let source = r#"
x = 1  # noqa: type-arg
y = 2  # noqa:no-untyped-def
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_rules.get(&2).unwrap().contains("TYPE-ARG"));
        assert!(directives.suppress_rules.get(&3).unwrap().contains("NO-UNTYPED-DEF"));
    }

    #[test]
    fn test_parse_noqa_mixed_code_formats() {
        // Support mixing different code formats
        let source = r#"
x = 1  # noqa: DOEFF001, E501, error_code, type-arg
"#;
        let directives = NoqaDirectives::parse(source);
        let rules = directives.suppress_rules.get(&2).unwrap();
        assert!(rules.contains("DOEFF001"));
        assert!(rules.contains("E501"));
        assert!(rules.contains("ERROR_CODE"));
        assert!(rules.contains("TYPE-ARG"));
    }

    #[test]
    fn test_parse_noqa_flake8_style() {
        // Support flake8/ruff style codes
        let source = r#"
import os  # noqa: F401
x = 1  # noqa: E501, W503
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.suppress_rules.get(&2).unwrap().contains("F401"));
        let rules = directives.suppress_rules.get(&3).unwrap();
        assert!(rules.contains("E501"));
        assert!(rules.contains("W503"));
    }

    #[test]
    fn test_parse_noqa_with_trailing_comment() {
        // Support trailing comment after " - " (e.g., "# noqa: DOEFF001 - reason")
        let source = r#"
x = 1  # noqa: DOEFF022 - some comment explaining why
y = 2  # noqa: DOEFF001, DOEFF002 - multiple rules with explanation
z = 3  # noqa: DOEFF003 -- double dash comment
"#;
        let directives = NoqaDirectives::parse(source);
        
        // Line 2: single rule with comment
        let rules2 = directives.suppress_rules.get(&2).unwrap();
        assert!(rules2.contains("DOEFF022"));
        assert!(!rules2.contains("SOME"));  // "some" should not be treated as rule
        assert_eq!(rules2.len(), 1);
        
        // Line 3: multiple rules with comment
        let rules3 = directives.suppress_rules.get(&3).unwrap();
        assert!(rules3.contains("DOEFF001"));
        assert!(rules3.contains("DOEFF002"));
        assert!(!rules3.contains("MULTIPLE"));
        assert_eq!(rules3.len(), 2);
        
        // Line 4: double dash comment
        let rules4 = directives.suppress_rules.get(&4).unwrap();
        assert!(rules4.contains("DOEFF003"));
        assert_eq!(rules4.len(), 1);
    }

    #[test]
    fn test_parse_noqa_comment_with_hyphenated_code() {
        // Make sure codes with hyphens like "type-arg" still work
        // The key is " - " (space-dash-space) vs "-" (no spaces)
        let source = r#"
x = 1  # noqa: type-arg - explanation
y = 2  # noqa: no-untyped-def - this is a mypy code
"#;
        let directives = NoqaDirectives::parse(source);
        
        let rules2 = directives.suppress_rules.get(&2).unwrap();
        assert!(rules2.contains("TYPE-ARG"));
        assert_eq!(rules2.len(), 1);
        
        let rules3 = directives.suppress_rules.get(&3).unwrap();
        assert!(rules3.contains("NO-UNTYPED-DEF"));
        assert_eq!(rules3.len(), 1);
    }

    // ==================== File-level noqa tests ====================

    #[test]
    fn test_parse_file_noqa_suppress_all() {
        // # noqa: file suppresses all rules for entire file
        let source = r#"# noqa: file
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.file_suppress_all);
        assert!(directives.is_suppressed(2, "DOEFF001"));
        assert!(directives.is_suppressed(3, "ANY_RULE"));
    }

    #[test]
    fn test_parse_file_noqa_specific_rule() {
        // # noqa: file=DOEFF001 suppresses specific rule for entire file
        let source = r#"# noqa: file=DOEFF001
def dict():
    pass

def list():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(!directives.file_suppress_all);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
        assert!(directives.is_suppressed(2, "DOEFF001"));
        assert!(directives.is_suppressed(5, "DOEFF001"));
        assert!(!directives.is_suppressed(2, "DOEFF002"));
    }

    #[test]
    fn test_parse_file_noqa_multiple_rules() {
        // # noqa: file=DOEFF001,DOEFF002 suppresses multiple rules
        let source = r#"# noqa: file=DOEFF001, DOEFF002
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(!directives.file_suppress_all);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
        assert!(directives.file_suppress_rules.contains("DOEFF002"));
        assert!(directives.is_suppressed(2, "DOEFF001"));
        assert!(directives.is_suppressed(2, "DOEFF002"));
        assert!(!directives.is_suppressed(2, "DOEFF003"));
    }

    #[test]
    fn test_parse_file_noqa_case_insensitive() {
        // file keyword is case-insensitive
        let source = r#"# noqa: FILE=doeff001
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
    }

    #[test]
    fn test_parse_file_noqa_after_docstring() {
        // File-level noqa can appear after module docstring
        let source = r#""""Module docstring."""
# noqa: file=DOEFF001
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
        assert!(directives.is_suppressed(3, "DOEFF001"));
    }

    #[test]
    fn test_parse_file_noqa_multiline_docstring() {
        // File-level noqa can appear after multiline docstring
        let source = r#""""
Module docstring
spanning multiple lines.
"""
# noqa: file=DOEFF001
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
    }

    #[test]
    fn test_parse_file_noqa_with_comments() {
        // File-level noqa can have preceding comments
        let source = r#"# -*- coding: utf-8 -*-
# Copyright 2024 Some Company
# noqa: file=DOEFF001
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.file_suppress_rules.contains("DOEFF001"));
    }

    #[test]
    fn test_parse_file_noqa_ignored_after_code() {
        // File-level noqa is ignored if it appears after code
        let source = r#"import os
# noqa: file=DOEFF001
def dict():
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(!directives.file_suppress_all);
        assert!(directives.file_suppress_rules.is_empty());
        // Should NOT suppress DOEFF001
        assert!(!directives.is_suppressed(3, "DOEFF001"));
    }

    #[test]
    fn test_file_noqa_combined_with_line_noqa() {
        // File-level and line-level noqa work together
        let source = r#"# noqa: file=DOEFF001
def dict():  # noqa: DOEFF002
    pass
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(directives.is_suppressed(2, "DOEFF001")); // file-level
        assert!(directives.is_suppressed(2, "DOEFF002")); // line-level
        assert!(directives.is_suppressed(3, "DOEFF001")); // file-level
        assert!(!directives.is_suppressed(3, "DOEFF002")); // line 3 doesn't have DOEFF002 suppression
    }

    #[test]
    fn test_has_file_level_suppression() {
        let source1 = "# noqa: file\ndef foo(): pass";
        let directives1 = NoqaDirectives::parse(source1);
        assert!(directives1.has_file_level_suppression());

        let source2 = "# noqa: file=DOEFF001\ndef foo(): pass";
        let directives2 = NoqaDirectives::parse(source2);
        assert!(directives2.has_file_level_suppression());

        let source3 = "def foo():  # noqa: DOEFF001\n    pass";
        let directives3 = NoqaDirectives::parse(source3);
        assert!(!directives3.has_file_level_suppression());
    }

    // ==================== Noqa warning tests ====================

    #[test]
    fn test_warning_missing_space_before_dash() {
        // "DOEFF001-comment" should warn (missing space before dash)
        let source = r#"
x = 1  # noqa: DOEFF001-this is wrong
"#;
        let directives = NoqaDirectives::parse(source);
        assert_eq!(directives.warnings.len(), 1);
        assert!(directives.warnings[0].message.contains("malformed"));
        assert!(directives.warnings[0].suggestion.contains(" - "));
    }

    #[test]
    fn test_warning_prose_without_separator() {
        // "DOEFF001 this is wrong" should warn (prose without - separator)
        let source = r#"
x = 1  # noqa: DOEFF001 this is wrong
"#;
        let directives = NoqaDirectives::parse(source);
        assert_eq!(directives.warnings.len(), 1);
        assert!(directives.warnings[0].message.contains("malformed"));
    }

    #[test]
    fn test_no_warning_correct_format() {
        // Correct formats should not produce warnings
        let source = r#"
x = 1  # noqa: DOEFF001
y = 2  # noqa: DOEFF001 - this is correct
z = 3  # noqa: DOEFF001, DOEFF002 - multiple rules
a = 4  # noqa
b = 5  # noqa: type-arg - hyphenated code is ok
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(
            directives.warnings.is_empty(),
            "Should not have warnings for correct format. Got: {:?}",
            directives.warnings
        );
    }

    #[test]
    fn test_no_warning_hyphenated_rule_id() {
        // Hyphenated rule IDs like "type-arg" should not trigger warning
        let source = r#"
x = 1  # noqa: type-arg
y = 2  # noqa: no-untyped-def
"#;
        let directives = NoqaDirectives::parse(source);
        assert!(
            directives.warnings.is_empty(),
            "Hyphenated rule IDs should not produce warnings. Got: {:?}",
            directives.warnings
        );
    }

    #[test]
    fn test_warning_line_number() {
        let source = r#"line1
line2
x = 1  # noqa: DOEFF001-wrong
line4
"#;
        let directives = NoqaDirectives::parse(source);
        assert_eq!(directives.warnings.len(), 1);
        assert_eq!(directives.warnings[0].line, 3);
    }
}




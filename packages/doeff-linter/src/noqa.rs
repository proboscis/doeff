//! noqa comment parsing and handling
//!
//! Supports inline comments to suppress lint rules:
//! - `# noqa` - suppress all rules on this line
//! - `# noqa: DOEFF001` - suppress specific rule
//! - `# noqa: DOEFF001, DOEFF002` - suppress multiple rules

use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::{HashMap, HashSet};

static NOQA_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"#\s*noqa(?:\s*:\s*([A-Z0-9,\s]+))?").unwrap()
});

/// Parsed noqa directives for a file
#[derive(Debug, Default)]
pub struct NoqaDirectives {
    /// Lines where all rules are suppressed
    pub suppress_all: HashSet<usize>,
    /// Lines where specific rules are suppressed: line -> set of rule IDs
    pub suppress_rules: HashMap<usize, HashSet<String>>,
}

impl NoqaDirectives {
    /// Parse noqa directives from source code
    pub fn parse(source: &str) -> Self {
        let mut directives = NoqaDirectives::default();

        for (line_num, line) in source.lines().enumerate() {
            let line_number = line_num + 1; // 1-indexed

            if let Some(caps) = NOQA_REGEX.captures(line) {
                if let Some(rules_match) = caps.get(1) {
                    // Specific rules: # noqa: DOEFF001, DOEFF002
                    let rules: HashSet<String> = rules_match
                        .as_str()
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
}




//! Notices for config keys and rule IDs the linter does not know (DOEFF100).
//!
//! The config (every level of pyproject's `[tool.doeff-linter]`, architecture.hy) can get ahead of the linter -- after agora-controllers
//! writes a new key, until the binary at its location is rebuilt. If during that time the whole lint stopped with a config error (exit code 2),
//! the editor's violation pane would be empty and the hook would go silent (4 times on 2026-09-28). So unknown keys and rule IDs not in this binary
//! (the form `DOEFF` plus 3 digits) are handled by skipping only that key, running the remaining rules, and reporting them as warning violations
//! DOEFF100 at that line of the config file instead of silently dropping them. Typos are visible the same way (malformed IDs, values of the wrong type, and nonexistent layer names are errors as before).

use std::path::{Path, PathBuf};

use crate::position::{line_range, Range};

/// Kind of the thing that was not known.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NoticeKind {
    /// A config key (`semantic.proxy_url`, `:dependency-layers` of architecture.hy, etc.).
    Key,
    /// A rule ID the config references (a `DOEFF` plus 3-digit ID not in this binary).
    RuleId(String),
}

/// One notice -- which key of which file was not read, and where.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigNotice {
    pub file: PathBuf,
    /// Key path (`semantic.proxy_url`, `laws.<name>.rules`, `defarchitecture :layers[0] :dependency-layers`).
    pub key: String,
    pub kind: NoticeKind,
    pub range: Range,
}

/// A rule ID referenced by the config but not in this binary (the file and position are decided where the config file is read).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownRuleRef {
    /// Path of the referencing key (`laws.<name>.rules`, `registry.reconciling`, `rules.<ID>`).
    pub key: String,
    pub id: String,
}

/// Whether this is the shape of a rule ID (`DOEFF` plus 3 digits, case-insensitive) -- an ID of this shape that is unknown is treated as "a rule newer than this binary" and
/// goes to a notice, while a malformed ID (such as `DOEFF1O1`) is treated as a typo and an error as before.
pub fn is_rule_id_shape(id: &str) -> bool {
    let upper = id.to_uppercase();
    upper.len() == 8 && upper.starts_with("DOEFF") && upper[5..].chars().all(|c| c.is_ascii_digit())
}

/// Byte offset of the start of the line in the TOML body where the key path (separated by `.`, array indices as numeric segments) is written.
/// Searches section headings (`[tool.doeff-linter.<level>]`, `[[…]]`, and `[<level>]` in a file that holds only a section's content) from longest, and finds the first key line under the heading.
/// If not found, the line of the `[tool.doeff-linter]` heading; if that is absent too, 0.
pub fn locate_toml_key(text: &str, key: &str) -> usize {
    let segments: Vec<&str> = key.split('.').filter(|s| !s.is_empty() && !s.chars().all(|c| c.is_ascii_digit())).collect();
    let lines: Vec<(usize, &str)> = line_starts(text);
    for n in (1..=segments.len()).rev() {
        let prefix = segments[..n].join(".");
        let headers = [
            format!("[tool.doeff-linter.{}]", prefix),
            format!("[[tool.doeff-linter.{}]]", prefix),
            format!("[{}]", prefix),
            format!("[[{}]]", prefix),
        ];
        let Some(index) = lines.iter().position(|(_, line)| headers.iter().any(|h| line.trim_start().starts_with(h.as_str()))) else {
            continue;
        };
        if n == segments.len() {
            return lines[index].0;
        }
        return key_line_in_section(&lines[index + 1..], segments[n]).unwrap_or(lines[index].0);
    }
    let top = lines.iter().position(|(_, line)| line.trim_start().starts_with("[tool.doeff-linter]"));
    let body = top.map(|i| &lines[i + 1..]).unwrap_or(&lines[..]);
    match segments.first().and_then(|first| key_line_in_section(body, first)) {
        Some(at) => at,
        None => top.map(|i| lines[i].0).unwrap_or(0),
    }
}

/// Finds the `<key> =` line in a section body (up to just before the next heading) (the key is bare or quoted).
fn key_line_in_section(lines: &[(usize, &str)], key: &str) -> Option<usize> {
    for (start, line) in lines {
        let trimmed = line.trim_start();
        if trimmed.starts_with('[') {
            return None;
        }
        let rest = trimmed
            .strip_prefix(key)
            .or_else(|| trimmed.strip_prefix(&format!("\"{}\"", key)))
            .or_else(|| trimmed.strip_prefix(&format!("'{}'", key)));
        if rest.is_some_and(|r| r.trim_start().starts_with('=') || r.starts_with('.')) {
            return Some(*start);
        }
    }
    None
}

/// Byte offset of the line start and the line body's columns.
fn line_starts(text: &str) -> Vec<(usize, &str)> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for line in text.split_inclusive('\n') {
        out.push((start, line.trim_end_matches(['\n', '\r'])));
        start += line.len();
    }
    out
}

/// Notices for unknown keys inside a TOML config file (the position is found from the body).
pub fn key_notice(file: &Path, text: &str, key: &str) -> ConfigNotice {
    ConfigNotice { file: file.to_path_buf(), key: key.to_string(), kind: NoticeKind::Key, range: line_range(text, locate_toml_key(text, key)) }
}

/// Notices for unknown rule IDs inside a config file (position is the line where the ID's spelling first appears; if none, the key's line).
pub fn rule_notice(file: &Path, text: &str, unknown: &UnknownRuleRef) -> ConfigNotice {
    let at = text.find(unknown.id.as_str()).unwrap_or_else(|| locate_toml_key(text, &unknown.key));
    ConfigNotice { file: file.to_path_buf(), key: unknown.key.clone(), kind: NoticeKind::RuleId(unknown.id.clone()), range: line_range(text, at) }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PYPROJECT: &str = "[project]\nname = \"x\"\n\n[tool.doeff-linter]\nenable = [\"ALL\"]\nnew_top = 1\n\n[tool.doeff-linter.semantic]\nmodel = \"m\"\nproxy_url = \"http://x\"\n\n[tool.doeff-linter.smells]\nshape_check_layers = []\n";

    /// Returns the line number (0-based).
    fn line_of(text: &str, offset: usize) -> usize {
        text[..offset].matches('\n').count()
    }

    #[test]
    fn locates_nested_top_level_and_section_keys() {
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "semantic.proxy_url")), 9);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "smells")), 11);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "new_top")), 5);
        assert_eq!(line_of(PYPROJECT, locate_toml_key(PYPROJECT, "nowhere")), 3, "a key not found maps to the [tool.doeff-linter] line");
    }

    #[test]
    fn rule_id_shape_separates_newer_rules_from_typos() {
        assert!(is_rule_id_shape("DOEFF999"));
        assert!(is_rule_id_shape("doeff127"));
        assert!(!is_rule_id_shape("DOEFF1O1"));
        assert!(!is_rule_id_shape("DOEFF12"));
    }
}

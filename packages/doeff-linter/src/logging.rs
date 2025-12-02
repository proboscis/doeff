//! Logging module for doeff-linter
//!
//! Provides structured logging of lint violations to a file in JSON Lines format
//! for later analysis and statistics.

use crate::models::{LintResult, Severity};
use crate::noqa::offset_to_line;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// A single log entry representing one lint run
#[derive(Debug, Serialize, Deserialize)]
pub struct LintLogEntry {
    /// Unix timestamp of when the lint was run
    pub timestamp: u64,
    /// ISO 8601 formatted date string
    pub datetime: String,
    /// Total number of files scanned
    pub files_scanned: usize,
    /// Total number of violations found
    pub total_violations: usize,
    /// Number of errors
    pub error_count: usize,
    /// Number of warnings
    pub warning_count: usize,
    /// Number of info messages
    pub info_count: usize,
    /// Individual violations
    pub violations: Vec<ViolationLogEntry>,
    /// Run mode (normal, hook, modified)
    pub run_mode: String,
    /// Enabled rules for this run
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled_rules: Option<Vec<String>>,
}

/// Log entry for a single violation
#[derive(Debug, Serialize, Deserialize)]
pub struct ViolationLogEntry {
    /// Rule ID (e.g., DOEFF001)
    pub rule_id: String,
    /// File path where the violation was found
    pub file_path: String,
    /// Line number
    pub line: usize,
    /// Severity level
    pub severity: String,
    /// Violation message
    pub message: String,
    /// Source line content (truncated if too long)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_line: Option<String>,
}

impl LintLogEntry {
    /// Create a new log entry from lint results
    pub fn from_results(
        results: &[LintResult],
        run_mode: &str,
        enabled_rules: Option<Vec<String>>,
    ) -> Self {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        let timestamp = now.as_secs();

        // Format datetime in ISO 8601
        let datetime = format_datetime(timestamp);

        let mut violations = Vec::new();
        let mut error_count = 0;
        let mut warning_count = 0;
        let mut info_count = 0;

        for result in results {
            for v in &result.violations {
                let line = get_line_from_offset(&result.file_path, v.offset);
                let source_line = read_source_line(&v.file_path, line);

                match v.severity {
                    Severity::Error => error_count += 1,
                    Severity::Warning => warning_count += 1,
                    Severity::Info => info_count += 1,
                }

                violations.push(ViolationLogEntry {
                    rule_id: v.rule_id.clone(),
                    file_path: v.file_path.clone(),
                    line,
                    severity: format!("{}", v.severity),
                    message: v.message.clone(),
                    source_line: if source_line.is_empty() {
                        None
                    } else {
                        Some(truncate_source_line(&source_line, 200))
                    },
                });
            }
        }

        let files_scanned = results.len();
        let total_violations = violations.len();

        Self {
            timestamp,
            datetime,
            files_scanned,
            total_violations,
            error_count,
            warning_count,
            info_count,
            violations,
            run_mode: run_mode.to_string(),
            enabled_rules,
        }
    }
}

/// Logger that writes lint results to a file
pub struct LintLogger {
    writer: Option<BufWriter<File>>,
    log_path: String,
}

impl LintLogger {
    /// Create a new logger that writes to the specified file
    /// If the file exists, it will be appended to; otherwise created
    pub fn new(log_path: &str) -> std::io::Result<Self> {
        let path = Path::new(log_path);

        // Create parent directories if they don't exist
        if let Some(parent) = path.parent() {
            if !parent.exists() {
                std::fs::create_dir_all(parent)?;
            }
        }

        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;

        Ok(Self {
            writer: Some(BufWriter::new(file)),
            log_path: log_path.to_string(),
        })
    }

    /// Log a lint run to the file
    pub fn log(&mut self, entry: &LintLogEntry) -> std::io::Result<()> {
        if let Some(ref mut writer) = self.writer {
            let json = serde_json::to_string(entry)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            writeln!(writer, "{}", json)?;
            writer.flush()?;
        }
        Ok(())
    }

    /// Get the path of the log file
    pub fn log_path(&self) -> &str {
        &self.log_path
    }
}

/// Format a unix timestamp as ISO 8601 datetime string
fn format_datetime(timestamp: u64) -> String {
    use std::time::{Duration, UNIX_EPOCH};
    let d = UNIX_EPOCH + Duration::from_secs(timestamp);
    let datetime: chrono::DateTime<chrono::Utc> =
        chrono::DateTime::from(d);
    datetime.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Get line number from byte offset
fn get_line_from_offset(file_path: &str, offset: usize) -> usize {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        offset_to_line(&content, offset)
    } else {
        1
    }
}

/// Read a specific line from a file
fn read_source_line(file_path: &str, line_num: usize) -> String {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        content
            .lines()
            .nth(line_num.saturating_sub(1))
            .map(|s| s.trim().to_string())
            .unwrap_or_default()
    } else {
        String::new()
    }
}

/// Truncate source line if too long
fn truncate_source_line(line: &str, max_len: usize) -> String {
    if line.len() > max_len {
        format!("{}...", &line[..max_len])
    } else {
        line.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{LintResult, Severity, Violation};
    use tempfile::TempDir;

    #[test]
    fn test_lint_log_entry_creation() {
        let violations = vec![Violation::new(
            "DOEFF001".to_string(),
            "Test message".to_string(),
            0,
            "test.py".to_string(),
            Severity::Error,
        )];

        let result = LintResult {
            file_path: "test.py".to_string(),
            violations,
            error: None,
        };

        let entry = LintLogEntry::from_results(&[result], "normal", None);

        assert_eq!(entry.files_scanned, 1);
        assert_eq!(entry.total_violations, 1);
        assert_eq!(entry.error_count, 1);
        assert_eq!(entry.warning_count, 0);
        assert_eq!(entry.run_mode, "normal");
    }

    #[test]
    fn test_logger_creation_and_write() {
        let dir = TempDir::new().unwrap();
        let log_path = dir.path().join("lint.jsonl");
        let log_path_str = log_path.to_string_lossy().to_string();

        let mut logger = LintLogger::new(&log_path_str).unwrap();

        let entry = LintLogEntry::from_results(&[], "test", None);
        logger.log(&entry).unwrap();

        // Verify file was created and contains content
        let content = std::fs::read_to_string(&log_path).unwrap();
        assert!(!content.is_empty());

        // Verify it's valid JSON
        let parsed: LintLogEntry = serde_json::from_str(content.trim()).unwrap();
        assert_eq!(parsed.run_mode, "test");
    }

    #[test]
    fn test_truncate_source_line() {
        let short = "short line";
        assert_eq!(truncate_source_line(short, 100), "short line");

        let long = "a".repeat(250);
        let truncated = truncate_source_line(&long, 200);
        assert_eq!(truncated.len(), 203); // 200 chars + "..."
        assert!(truncated.ends_with("..."));
    }
}


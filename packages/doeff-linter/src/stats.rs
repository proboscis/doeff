//! Statistics module for analyzing lint logs
//!
//! Provides CLI-based statistics and analysis of lint violations.

use crate::logging::{LintLogEntry, ViolationLogEntry};
use colored::*;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

/// Statistics summary
#[derive(Debug, Default)]
pub struct LogStats {
    pub total_runs: usize,
    pub total_violations: usize,
    pub total_errors: usize,
    pub total_warnings: usize,
    pub total_info: usize,
    pub total_files_scanned: usize,
    pub violations_by_rule: HashMap<String, usize>,
    pub violations_by_file: HashMap<String, usize>,
    pub violations_by_severity: HashMap<String, usize>,
    pub runs_by_date: HashMap<String, usize>,
    pub first_run: Option<String>,
    pub last_run: Option<String>,
}

impl LogStats {
    /// Load and analyze a log file
    pub fn from_log_file(path: &Path) -> std::io::Result<Self> {
        let file = File::open(path)?;
        let reader = BufReader::new(file);
        let mut stats = LogStats::default();

        for line in reader.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }

            if let Ok(entry) = serde_json::from_str::<LintLogEntry>(&line) {
                stats.add_entry(&entry);
            }
        }

        Ok(stats)
    }

    fn add_entry(&mut self, entry: &LintLogEntry) {
        self.total_runs += 1;
        self.total_violations += entry.total_violations;
        self.total_errors += entry.error_count;
        self.total_warnings += entry.warning_count;
        self.total_info += entry.info_count;
        self.total_files_scanned += entry.files_scanned;

        // Track date (extract YYYY-MM-DD from datetime)
        let date = entry.datetime.split('T').next().unwrap_or(&entry.datetime);
        *self.runs_by_date.entry(date.to_string()).or_insert(0) += 1;

        // Track first and last run
        if self.first_run.is_none() {
            self.first_run = Some(entry.datetime.clone());
        }
        self.last_run = Some(entry.datetime.clone());

        // Aggregate violations
        for v in &entry.violations {
            *self.violations_by_rule.entry(v.rule_id.clone()).or_insert(0) += 1;
            *self.violations_by_file.entry(v.file_path.clone()).or_insert(0) += 1;
            *self.violations_by_severity.entry(v.severity.clone()).or_insert(0) += 1;
        }
    }

    /// Get top N files by violation count
    pub fn top_files(&self, n: usize) -> Vec<(&String, &usize)> {
        let mut files: Vec<_> = self.violations_by_file.iter().collect();
        files.sort_by(|a, b| b.1.cmp(a.1));
        files.into_iter().take(n).collect()
    }

    /// Get rules sorted by violation count
    pub fn rules_sorted(&self) -> Vec<(&String, &usize)> {
        let mut rules: Vec<_> = self.violations_by_rule.iter().collect();
        rules.sort_by(|a, b| b.1.cmp(a.1));
        rules
    }
}

/// Print statistics to console
pub fn print_stats(stats: &LogStats) {
    println!("\n{}", "═".repeat(60).cyan());
    println!("{}", " DOEFF-LINTER STATISTICS ".bold().cyan());
    println!("{}\n", "═".repeat(60).cyan());

    // Overview
    println!("{}", "📊 Overview".bold().white());
    println!("  Total lint runs:      {}", stats.total_runs.to_string().yellow());
    println!("  Total files scanned:  {}", stats.total_files_scanned.to_string().yellow());
    println!("  Total violations:     {}", stats.total_violations.to_string().yellow());
    println!();

    // Severity breakdown
    println!("{}", "🎯 By Severity".bold().white());
    println!(
        "  Errors:   {} ({})",
        stats.total_errors.to_string().red().bold(),
        format_percent(stats.total_errors, stats.total_violations)
    );
    println!(
        "  Warnings: {} ({})",
        stats.total_warnings.to_string().yellow(),
        format_percent(stats.total_warnings, stats.total_violations)
    );
    println!(
        "  Info:     {} ({})",
        stats.total_info.to_string().blue(),
        format_percent(stats.total_info, stats.total_violations)
    );
    println!();

    // By rule
    println!("{}", "📋 By Rule".bold().white());
    let rules = stats.rules_sorted();
    if rules.is_empty() {
        println!("  No violations recorded");
    } else {
        let max_count = rules.first().map(|(_, c)| **c).unwrap_or(1);
        for (rule, count) in &rules {
            let bar_len = (**count as f64 / max_count as f64 * 20.0) as usize;
            let bar = "█".repeat(bar_len);
            println!(
                "  {} {:>5}  {}",
                rule.cyan(),
                count.to_string().yellow(),
                bar.green()
            );
        }
    }
    println!();

    // Top files
    println!("{}", "📁 Top 10 Files by Violations".bold().white());
    let top_files = stats.top_files(10);
    if top_files.is_empty() {
        println!("  No violations recorded");
    } else {
        for (i, (file, count)) in top_files.iter().enumerate() {
            let file_display = if file.len() > 50 {
                format!("...{}", &file[file.len() - 47..])
            } else {
                file.to_string()
            };
            println!(
                "  {:>2}. {} {}",
                (i + 1).to_string().dimmed(),
                count.to_string().yellow(),
                file_display.dimmed()
            );
        }
    }
    println!();

    // Time range
    if let (Some(first), Some(last)) = (&stats.first_run, &stats.last_run) {
        println!("{}", "📅 Time Range".bold().white());
        println!("  First run: {}", first.dimmed());
        println!("  Last run:  {}", last.dimmed());
        println!("  Days with runs: {}", stats.runs_by_date.len().to_string().yellow());
    }

    println!("\n{}", "═".repeat(60).cyan());
}

fn format_percent(part: usize, total: usize) -> String {
    if total == 0 {
        "0%".to_string()
    } else {
        format!("{:.1}%", (part as f64 / total as f64) * 100.0)
    }
}

/// Print a trend summary (violations per day)
pub fn print_trend(stats: &LogStats) {
    println!("\n{}", "📈 Daily Trend".bold().white());

    let mut dates: Vec<_> = stats.runs_by_date.iter().collect();
    dates.sort_by(|a, b| a.0.cmp(b.0));

    if dates.is_empty() {
        println!("  No data available");
        return;
    }

    // Show last 14 days
    let dates: Vec<_> = dates.into_iter().rev().take(14).collect();
    let dates: Vec<_> = dates.into_iter().rev().collect();

    for (date, runs) in dates {
        println!("  {} - {} runs", date.dimmed(), runs.to_string().yellow());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::io::Write;

    #[test]
    fn test_log_stats_empty() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("empty.jsonl");
        std::fs::write(&path, "").unwrap();

        let stats = LogStats::from_log_file(&path).unwrap();
        assert_eq!(stats.total_runs, 0);
        assert_eq!(stats.total_violations, 0);
    }

    #[test]
    fn test_log_stats_with_entries() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.jsonl");

        let entry = r#"{"timestamp":1733126639,"datetime":"2025-12-02T07:23:59Z","files_scanned":5,"total_violations":3,"error_count":1,"warning_count":2,"info_count":0,"violations":[{"rule_id":"DOEFF001","file_path":"test.py","line":1,"severity":"error","message":"test"}],"run_mode":"normal"}"#;

        let mut file = File::create(&path).unwrap();
        writeln!(file, "{}", entry).unwrap();
        writeln!(file, "{}", entry).unwrap();

        let stats = LogStats::from_log_file(&path).unwrap();
        assert_eq!(stats.total_runs, 2);
        assert_eq!(stats.total_violations, 6);
        assert_eq!(stats.total_errors, 2);
        assert_eq!(stats.violations_by_rule.get("DOEFF001"), Some(&2));
    }
}



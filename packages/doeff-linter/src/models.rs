//! Core data models for the doeff-linter

use rustpython_ast::{Mod, Stmt};
use std::path::PathBuf;

/// A violation detected by a lint rule
#[derive(Debug, Clone)]
pub struct Violation {
    pub rule_id: String,
    pub message: String,
    pub offset: usize,
    pub file_path: String,
    pub severity: Severity,
    pub fix: Option<Fix>,
}

/// An automatic fix for a violation
#[derive(Debug, Clone)]
pub struct Fix {
    pub description: String,
    pub file_path: PathBuf,
    pub content: String,
}

/// Severity level of a violation
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Severity {
    Error,
    Warning,
    Info,
}

impl Violation {
    /// Create a new violation without a fix
    pub fn new(
        rule_id: String,
        message: String,
        offset: usize,
        file_path: String,
        severity: Severity,
    ) -> Self {
        Self {
            rule_id,
            message,
            offset,
            file_path,
            severity,
            fix: None,
        }
    }

    /// Create a new violation with a fix
    pub fn with_fix(
        rule_id: String,
        message: String,
        offset: usize,
        file_path: String,
        severity: Severity,
        fix: Fix,
    ) -> Self {
        Self {
            rule_id,
            message,
            offset,
            file_path,
            severity,
            fix: Some(fix),
        }
    }
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Severity::Error => write!(f, "error"),
            Severity::Warning => write!(f, "warning"),
            Severity::Info => write!(f, "info"),
        }
    }
}

/// Context passed to each rule for checking
pub struct RuleContext<'a> {
    pub stmt: &'a Stmt,
    pub file_path: &'a str,
    pub source: &'a str,
    pub ast: &'a Mod,
}

/// Result of linting a single file
#[derive(Debug, Default)]
pub struct LintResult {
    pub file_path: String,
    pub violations: Vec<Violation>,
    pub error: Option<String>,
}

impl LintResult {
    pub fn new(file_path: String) -> Self {
        Self {
            file_path,
            violations: Vec::new(),
            error: None,
        }
    }

    pub fn with_error(file_path: String, error: String) -> Self {
        Self {
            file_path,
            violations: Vec::new(),
            error: Some(error),
        }
    }
}




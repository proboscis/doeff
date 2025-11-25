//! doeff-linter: A linter for enforcing code quality and immutability patterns
//!
//! This crate provides lint rules for Python code, focusing on:
//! - Immutability patterns
//! - Type safety
//! - Code organization

pub mod config;
pub mod models;
pub mod noqa;
pub mod rules;
pub mod utils;

use models::{LintResult, RuleContext, Violation};
use noqa::{offset_to_line, NoqaDirectives};
use rayon::prelude::*;
use rules::base::LintRule;
use rustpython_ast::{Mod, Stmt};
use rustpython_parser::{parse, Mode};
use std::path::Path;
use walkdir::WalkDir;

/// Lint a single file and return the results
pub fn lint_file(
    file_path: &Path,
    rules: &[Box<dyn LintRule>],
) -> LintResult {
    let path_str = file_path.to_string_lossy().to_string();

    let source = match std::fs::read_to_string(file_path) {
        Ok(s) => s,
        Err(e) => return LintResult::with_error(path_str, format!("Failed to read file: {}", e)),
    };

    lint_source(&path_str, &source, rules)
}

/// Lint source code and return the results
pub fn lint_source(
    file_path: &str,
    source: &str,
    rules: &[Box<dyn LintRule>],
) -> LintResult {
    let ast = match parse(source, Mode::Module, file_path) {
        Ok(ast) => ast,
        Err(e) => return LintResult::with_error(file_path.to_string(), format!("Parse error: {}", e)),
    };

    let noqa = NoqaDirectives::parse(source);
    let mut result = LintResult::new(file_path.to_string());

    if let Mod::Module(module) = &ast {
        for stmt in &module.body {
            check_stmt_recursive(stmt, file_path, source, &ast, rules, &noqa, &mut result.violations);
        }
    }

    result
}

fn check_stmt_recursive(
    stmt: &Stmt,
    file_path: &str,
    source: &str,
    ast: &Mod,
    rules: &[Box<dyn LintRule>],
    noqa: &NoqaDirectives,
    violations: &mut Vec<Violation>,
) {
    let context = RuleContext {
        stmt,
        file_path,
        source,
        ast,
    };

    for rule in rules {
        let rule_violations = rule.check(&context);
        for v in rule_violations {
            let line = offset_to_line(source, v.offset);
            if !noqa.is_suppressed(line, &v.rule_id) {
                violations.push(v);
            }
        }
    }

    // Recursively check nested statements
    match stmt {
        Stmt::ClassDef(class_def) => {
            for s in &class_def.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::FunctionDef(func) => {
            for s in &func.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::AsyncFunctionDef(func) => {
            for s in &func.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::If(if_stmt) => {
            for s in &if_stmt.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
            for s in &if_stmt.orelse {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::While(while_stmt) => {
            for s in &while_stmt.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::For(for_stmt) => {
            for s in &for_stmt.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::With(with_stmt) => {
            for s in &with_stmt.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        Stmt::Try(try_stmt) => {
            for s in &try_stmt.body {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
            for handler in &try_stmt.handlers {
                if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                    for s in &h.body {
                        check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
                    }
                }
            }
            for s in &try_stmt.orelse {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
            for s in &try_stmt.finalbody {
                check_stmt_recursive(s, file_path, source, ast, rules, noqa, violations);
            }
        }
        _ => {}
    }
}

/// Collect Python files from paths
pub fn collect_python_files(paths: &[String], exclude_patterns: &[String]) -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();

    for path in paths {
        let p = Path::new(path);
        if p.is_file() {
            if p.extension().map_or(false, |e| e == "py") {
                files.push(p.to_path_buf());
            }
        } else if p.is_dir() {
            for entry in WalkDir::new(p)
                .into_iter()
                .filter_entry(|e| !should_exclude(e.path(), exclude_patterns))
                .filter_map(|e| e.ok())
            {
                let path = entry.path();
                if path.is_file() && path.extension().map_or(false, |e| e == "py") {
                    files.push(path.to_path_buf());
                }
            }
        }
    }

    files
}

fn should_exclude(path: &Path, patterns: &[String]) -> bool {
    for pattern in patterns {
        if let Some(name) = path.file_name() {
            if let Some(name_str) = name.to_str() {
                if name_str == pattern || name_str.contains(pattern) {
                    return true;
                }
            }
        }
        // Check if any path component matches
        for component in path.components() {
            if let Some(comp_str) = component.as_os_str().to_str() {
                if comp_str == pattern {
                    return true;
                }
            }
        }
    }
    false
}

/// Lint multiple files in parallel
pub fn lint_files_parallel(
    files: &[std::path::PathBuf],
    rules: &[Box<dyn LintRule>],
) -> Vec<LintResult> {
    files
        .par_iter()
        .map(|file| lint_file(file, rules))
        .collect()
}




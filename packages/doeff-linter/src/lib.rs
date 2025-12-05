//! doeff-linter: A linter for enforcing code quality and immutability patterns
//!
//! This crate provides lint rules for Python code, focusing on:
//! - Immutability patterns
//! - Type safety
//! - Code organization

pub mod config;
pub mod logging;
pub mod models;
pub mod noqa;
pub mod report;
pub mod rules;
pub mod stats;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::get_all_rules;

    /// Test case for noqa verification
    struct NoqaTestCase {
        rule_id: &'static str,
        /// Code that triggers the rule (without noqa)
        triggering_code: &'static str,
        /// Line number where the violation should occur (1-indexed)
        violation_line: usize,
    }

    /// Get test cases for all rules
    fn get_noqa_test_cases() -> Vec<NoqaTestCase> {
        vec![
            // DOEFF001: Builtin Shadowing
            NoqaTestCase {
                rule_id: "DOEFF001",
                triggering_code: "def dict():\n    return {}",
                violation_line: 1,
            },
            // DOEFF002: Mutable Attribute Naming
            NoqaTestCase {
                rule_id: "DOEFF002",
                triggering_code: r#"class Foo:
    def __init__(self):
        self.data = []
    def update(self):
        self.data = [1, 2, 3]"#,
                violation_line: 5,
            },
            // DOEFF003: Max Mutable Attributes
            NoqaTestCase {
                rule_id: "DOEFF003",
                triggering_code: r#"class Foo:
    def __init__(self):
        self.mut_a = 1
        self.mut_b = 2
        self.mut_c = 3
        self.mut_d = 4
        self.mut_e = 5
        self.mut_f = 6"#,
                violation_line: 1,
            },
            // DOEFF004: No os.environ Access
            NoqaTestCase {
                rule_id: "DOEFF004",
                triggering_code: "import os\nkey = os.environ[\"KEY\"]",
                violation_line: 2,
            },
            // DOEFF005: No Setter Methods
            NoqaTestCase {
                rule_id: "DOEFF005",
                triggering_code: r#"class Foo:
    def set_value(self, v):
        pass"#,
                violation_line: 2,
            },
            // DOEFF006: No Tuple Returns
            NoqaTestCase {
                rule_id: "DOEFF006",
                triggering_code: "def foo() -> tuple[int, str]:\n    return (1, \"a\")",
                violation_line: 1,
            },
            // DOEFF007: No Mutable Argument Mutations
            NoqaTestCase {
                rule_id: "DOEFF007",
                triggering_code: r#"def foo(items):
    items.append(1)"#,
                violation_line: 2,
            },
            // DOEFF008: No Dataclass Attribute Mutation
            NoqaTestCase {
                rule_id: "DOEFF008",
                triggering_code: r#"from dataclasses import dataclass
@dataclass
class User:
    name: str
user = User("test")
user.name = "new""#,
                violation_line: 6,
            },
            // DOEFF009: Missing Return Type Annotation
            NoqaTestCase {
                rule_id: "DOEFF009",
                triggering_code: "def foo():\n    return 1",
                violation_line: 1,
            },
            // DOEFF010: Test File Placement - uses file path detection
            // This rule checks file path, so we test it separately with a special file path
            NoqaTestCase {
                rule_id: "DOEFF010",
                triggering_code: "def test_foo():\n    pass",
                violation_line: 1,
            },
            // DOEFF011: No Flag Arguments
            NoqaTestCase {
                rule_id: "DOEFF011",
                triggering_code: "def foo(verbose: bool = False):\n    pass",
                violation_line: 1,
            },
            // DOEFF012: No Append Loop - violation is on the for loop line
            NoqaTestCase {
                rule_id: "DOEFF012",
                triggering_code: r#"items = [1, 2, 3]
result = []
for x in items:
    result.append(x)"#,
                violation_line: 3,
            },
            // DOEFF013: Prefer Maybe Monad
            NoqaTestCase {
                rule_id: "DOEFF013",
                triggering_code: "from typing import Optional\ndef foo(x: Optional[int]) -> int:\n    return x or 0",
                violation_line: 2,
            },
            // DOEFF014: No Try-Except
            NoqaTestCase {
                rule_id: "DOEFF014",
                triggering_code: r#"def foo():
    try:
        pass
    except:
        pass"#,
                violation_line: 2,
            },
            // DOEFF015: No Zero-Arg Program
            NoqaTestCase {
                rule_id: "DOEFF015",
                triggering_code: "p: Program = create_program()",
                violation_line: 1,
            },
            // DOEFF016: No Relative Import
            NoqaTestCase {
                rule_id: "DOEFF016",
                triggering_code: "from . import foo",
                violation_line: 1,
            },
            // DOEFF017: No Program Type Param
            NoqaTestCase {
                rule_id: "DOEFF017",
                triggering_code: "@do\ndef foo(p: Program[int]) -> int:\n    return 1",
                violation_line: 2,
            },
            // DOEFF018: No Ask in Try
            NoqaTestCase {
                rule_id: "DOEFF018",
                triggering_code: r#"@do
def foo():
    try:
        x = yield ask("key")
    except:
        pass"#,
                violation_line: 4,
            },
            // DOEFF019: No Ask with Fallback
            NoqaTestCase {
                rule_id: "DOEFF019",
                triggering_code: r#"@do
def foo(arg=None):
    x = arg or (yield ask("key"))"#,
                violation_line: 3,
            },
            // DOEFF020: Program Naming Convention
            NoqaTestCase {
                rule_id: "DOEFF020",
                triggering_code: "my_program: Program = get_program()",
                violation_line: 1,
            },
            // DOEFF021: No __all__
            NoqaTestCase {
                rule_id: "DOEFF021",
                triggering_code: "__all__ = [\"foo\", \"bar\"]",
                violation_line: 1,
            },
            // DOEFF022: Prefer @do Function
            NoqaTestCase {
                rule_id: "DOEFF022",
                triggering_code: "def foo() -> EffectGenerator[int]:\n    yield Log(\"test\")\n    return 1",
                violation_line: 1,
            },
            // DOEFF023: Pipeline Marker - requires @do function called to create Program variable
            // violation is reported on the `def` line, not the decorator
            NoqaTestCase {
                rule_id: "DOEFF023",
                triggering_code: r#"@do
def process():
    return 1

p: Program = process()"#,
                violation_line: 2,
            },
        ]
    }

    /// Helper to get a single rule by ID
    fn get_rule_by_id(rule_id: &str) -> Option<Box<dyn LintRule>> {
        get_all_rules()
            .into_iter()
            .find(|r| r.rule_id() == rule_id)
    }

    #[test]
    fn test_all_rules_respect_line_noqa() {
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            // Determine file path (DOEFF010 needs a test_ prefixed file not in tests/)
            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITHOUT noqa - should have violations
            let result_without_noqa = lint_source(file_path, test_case.triggering_code, &rules);
            assert!(
                !result_without_noqa.violations.is_empty(),
                "Rule {} should produce violations without noqa. Code:\n{}",
                test_case.rule_id,
                test_case.triggering_code
            );

            // Test WITH line-level noqa - should suppress violations
            let code_with_noqa = add_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
                test_case.rule_id,
            );

            let rule = get_rule_by_id(test_case.rule_id).unwrap();
            let rules: Vec<Box<dyn LintRule>> = vec![rule];
            let result_with_noqa = lint_source(file_path, &code_with_noqa, &rules);

            assert!(
                result_with_noqa.violations.is_empty(),
                "Rule {} should be suppressed by noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_noqa,
                result_with_noqa
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_blanket_noqa() {
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH blanket noqa (# noqa without rule ID) - should suppress
            let code_with_blanket_noqa = add_blanket_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
            );

            let result = lint_source(file_path, &code_with_blanket_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by blanket noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_blanket_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_blanket_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_file_level_noqa() {
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH file-level noqa - should suppress all violations
            let code_with_file_noqa = format!(
                "# noqa: file={}\n{}",
                test_case.rule_id, test_case.triggering_code
            );

            let result = lint_source(file_path, &code_with_file_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by file-level noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_file_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_file_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_file_level_blanket_noqa() {
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH file-level blanket noqa - should suppress all rules
            let code_with_file_noqa = format!(
                "# noqa: file\n{}",
                test_case.triggering_code
            );

            let result = lint_source(file_path, &code_with_file_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by file-level blanket noqa. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_file_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_file_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_noqa_for_different_rule_does_not_suppress() {
        // Test that noqa for a different rule doesn't suppress the violation
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Add noqa for a different rule (use DOEFF999 which doesn't exist)
            let code_with_wrong_noqa = add_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
                "DOEFF999",
            );

            let result = lint_source(file_path, &code_with_wrong_noqa, &rules);

            assert!(
                !result.violations.is_empty(),
                "Rule {} should NOT be suppressed by noqa for different rule. Code:\n{}",
                test_case.rule_id,
                code_with_wrong_noqa
            );
        }
    }

    #[test]
    fn test_noqa_case_insensitive() {
        // Test that noqa rule IDs are case-insensitive
        let code = "def dict():  # noqa: doeff001\n    return {}";
        let rule = get_rule_by_id("DOEFF001").unwrap();
        let rules: Vec<Box<dyn LintRule>> = vec![rule];

        let result = lint_source("test.py", code, &rules);

        assert!(
            result.violations.is_empty(),
            "noqa should be case-insensitive. Violations: {:?}",
            result.violations
        );
    }

    #[test]
    fn test_noqa_multiple_rules() {
        // Test that multiple rules can be suppressed on one line
        let code = "def dict() -> tuple[int, str]:  # noqa: DOEFF001, DOEFF006\n    return (1, \"a\")";
        let rules = get_all_rules();

        let result = lint_source("test.py", code, &rules);

        // Should not have DOEFF001 or DOEFF006 violations
        let remaining_violations: Vec<_> = result
            .violations
            .iter()
            .filter(|v| v.rule_id == "DOEFF001" || v.rule_id == "DOEFF006")
            .collect();

        assert!(
            remaining_violations.is_empty(),
            "Both DOEFF001 and DOEFF006 should be suppressed. Remaining: {:?}",
            remaining_violations
        );
    }

    /// Helper: Add noqa comment for specific rule to a specific line
    fn add_noqa_to_line(code: &str, line_num: usize, rule_id: &str) -> String {
        let lines: Vec<&str> = code.lines().collect();
        let mut result = Vec::new();

        for (i, line) in lines.iter().enumerate() {
            if i + 1 == line_num {
                result.push(format!("{}  # noqa: {}", line, rule_id));
            } else {
                result.push(line.to_string());
            }
        }

        result.join("\n")
    }

    /// Helper: Add blanket noqa comment (without rule ID) to a specific line
    fn add_blanket_noqa_to_line(code: &str, line_num: usize) -> String {
        let lines: Vec<&str> = code.lines().collect();
        let mut result = Vec::new();

        for (i, line) in lines.iter().enumerate() {
            if i + 1 == line_num {
                result.push(format!("{}  # noqa", line));
            } else {
                result.push(line.to_string());
            }
        }

        result.join("\n")
    }
}


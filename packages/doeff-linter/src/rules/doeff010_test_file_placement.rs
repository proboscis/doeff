//! DOEFF010: Test File Placement
//!
//! Test files must be placed under a 'tests' directory.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::Mod;
use std::path::Path;

pub struct TestFilePlacementRule;

impl TestFilePlacementRule {
    pub fn new() -> Self {
        Self
    }

    fn is_test_file(file_path: &str) -> bool {
        if let Some(file_name) = Path::new(file_path).file_name() {
            if let Some(name_str) = file_name.to_str() {
                return (name_str.starts_with("test_") || name_str == "test.py")
                    && name_str.ends_with(".py");
            }
        }
        false
    }

    fn is_in_tests_directory(file_path: &str) -> bool {
        let path = Path::new(file_path);
        for ancestor in path.ancestors() {
            if let Some(dir_name) = ancestor.file_name() {
                if let Some(name_str) = dir_name.to_str() {
                    if name_str == "tests" {
                        return true;
                    }
                }
            }
        }
        false
    }
}

impl LintRule for TestFilePlacementRule {
    fn rule_id(&self) -> &str {
        "DOEFF010"
    }

    fn description(&self) -> &str {
        "Test files must be placed under a 'tests' directory"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Only check on first statement to avoid duplicate violations
        if let Mod::Module(module) = context.ast {
            if !module.body.is_empty() && !std::ptr::eq(context.stmt, &module.body[0]) {
                return violations;
            }
        } else {
            return violations;
        }

        if Self::is_test_file(context.file_path) && !Self::is_in_tests_directory(context.file_path)
        {
            let file_name = Path::new(context.file_path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("test file");

            violations.push(Violation::new(
                self.rule_id().to_string(),
                format!(
                    "Test file '{}' must be placed under a 'tests' directory. \
                     Move to tests/{} or tests/unit/{}, etc.",
                    file_name, file_name, file_name
                ),
                0,
                context.file_path.to_string(),
                Severity::Error,
            ));
        }

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str, file_path: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, file_path).unwrap();
        let rule = TestFilePlacementRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            if let Some(first_stmt) = module.body.first() {
                let context = RuleContext {
                    stmt: first_stmt,
                    file_path,
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

    #[test]
    fn test_file_not_in_tests() {
        let code = "def test_something(): pass";
        let violations = check_code(code, "src/test_module.py");
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_file_in_tests() {
        let code = "def test_something(): pass";
        let violations = check_code(code, "tests/test_module.py");
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_file_in_nested_tests() {
        let code = "def test_something(): pass";
        let violations = check_code(code, "tests/unit/test_module.py");
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_non_test_file_allowed() {
        let code = "def something(): pass";
        let violations = check_code(code, "src/module.py");
        assert_eq!(violations.len(), 0);
    }
}




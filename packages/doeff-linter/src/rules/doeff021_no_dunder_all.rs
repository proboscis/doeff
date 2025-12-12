//! DOEFF021: No __all__ Declaration
//!
//! Forbid the use of `__all__` as this project defaults to exporting everything.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoDunderAllRule;

impl NoDunderAllRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if an expression is the name `__all__`
    fn is_dunder_all(expr: &Expr) -> bool {
        match expr {
            Expr::Name(name) => name.id.as_str() == "__all__",
            _ => false,
        }
    }
}

impl LintRule for NoDunderAllRule {
    fn rule_id(&self) -> &str {
        "DOEFF021"
    }

    fn description(&self) -> &str {
        "Forbid __all__ declaration; this project exports everything by default"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            // Handle: __all__ = [...]
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if Self::is_dunder_all(target) {
                        violations.push(self.create_violation(
                            assign.range.start().to_usize(),
                            context.file_path,
                        ));
                    }
                }
            }

            // Handle: __all__: list = [...]
            Stmt::AnnAssign(ann_assign) => {
                if Self::is_dunder_all(&ann_assign.target) {
                    violations.push(self.create_violation(
                        ann_assign.range.start().to_usize(),
                        context.file_path,
                    ));
                }
            }

            // Handle: __all__ += [...]
            Stmt::AugAssign(aug_assign) => {
                if Self::is_dunder_all(&aug_assign.target) {
                    violations.push(self.create_violation(
                        aug_assign.range.start().to_usize(),
                        context.file_path,
                    ));
                }
            }

            _ => {}
        }

        violations
    }
}

impl NoDunderAllRule {
    fn create_violation(&self, offset: usize, file_path: &str) -> Violation {
        let message = "'__all__' should not be used.\n\n\
            Policy: This project defaults to exporting everything from modules.\n\
            Using __all__ restricts exports and goes against the project convention.\n\n\
            Fix: Remove the __all__ declaration. If you need to limit exports for a\n\
            specific reason, add a comment explaining why and use # noqa: DOEFF021."
            .to_string();

        Violation::new(
            "DOEFF021".to_string(),
            message,
            offset,
            file_path.to_string(),
            Severity::Error,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoDunderAllRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            for stmt in &module.body {
                let context = RuleContext {
                    stmt,
                    file_path: "test.py",
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

    #[test]
    fn test_simple_all_assignment() {
        // __all__ = ["foo", "bar"]
        let code = r#"__all__ = ["foo", "bar"]"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'__all__' should not be used"));
    }

    #[test]
    fn test_annotated_all_assignment() {
        // __all__: list = ["foo", "bar"]
        let code = r#"__all__: list = ["foo", "bar"]"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'__all__' should not be used"));
    }

    #[test]
    fn test_annotated_all_with_list_str() {
        // __all__: list[str] = ["foo", "bar"]
        let code = r#"__all__: list[str] = ["foo", "bar"]"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_aug_assign_all() {
        // __all__ += ["baz"]
        let code = r#"__all__ += ["baz"]"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'__all__' should not be used"));
    }

    #[test]
    fn test_empty_all() {
        // __all__ = []
        let code = r#"__all__ = []"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_all_with_tuple() {
        // __all__ = ("foo", "bar")
        let code = r#"__all__ = ("foo", "bar")"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_regular_variable_allowed() {
        // Regular variables should not trigger the rule
        let code = r#"
exports = ["foo", "bar"]
my_list: list = [1, 2, 3]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_other_dunder_variables_allowed() {
        // Other dunder variables should not trigger
        let code = r#"
__version__ = "1.0.0"
__author__ = "Test"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_all_in_string_allowed() {
        // __all__ in strings should not trigger
        let code = r#"
doc = "Use __all__ to define exports"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_message_contains_fix_suggestion() {
        let code = r#"__all__ = ["foo"]"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Remove the __all__ declaration"));
        assert!(violations[0].message.contains("noqa: DOEFF021"));
    }

    #[test]
    fn test_multiple_all_declarations() {
        // Multiple __all__ declarations
        let code = r#"
__all__ = ["foo"]
__all__ += ["bar"]
__all__: list = ["baz"]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 3);
    }
}


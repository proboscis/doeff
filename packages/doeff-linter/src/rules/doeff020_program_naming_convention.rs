//! DOEFF020: Program Naming Convention
//!
//! Program type variables should use the `p_` prefix for consistency and brevity.
//! The `_program` suffix is deprecated.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct ProgramNamingConventionRule;

impl ProgramNamingConventionRule {
    pub fn new() -> Self {
        Self
    }

    /// Check if the type annotation contains "Program"
    fn is_program_type(expr: &Expr) -> bool {
        match expr {
            // Program or Program[T]
            Expr::Name(name) => name.id.as_str() == "Program",
            // Program[T], Program[int], etc.
            Expr::Subscript(subscript) => {
                if let Expr::Name(name) = &*subscript.value {
                    name.id.as_str() == "Program"
                } else {
                    false
                }
            }
            // Handle Union types like Program | None
            Expr::BinOp(binop) => {
                Self::is_program_type(&binop.left) || Self::is_program_type(&binop.right)
            }
            _ => false,
        }
    }

    /// Extract the variable name from the assignment target
    fn get_target_name(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(name) => Some(name.id.to_string()),
            _ => None,
        }
    }

    /// Suggest a corrected name with p_ prefix
    fn suggest_name(name: &str) -> String {
        if name.ends_with("_program") {
            // Remove _program suffix and add p_ prefix
            let base = &name[..name.len() - "_program".len()];
            format!("p_{}", base)
        } else {
            format!("p_{}", name)
        }
    }
}

impl LintRule for ProgramNamingConventionRule {
    fn rule_id(&self) -> &str {
        "DOEFF020"
    }

    fn description(&self) -> &str {
        "Program type variables should use 'p_' prefix"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Only check annotated assignments
        if let Stmt::AnnAssign(ann_assign) = context.stmt {
            // Check if the type annotation is Program or Program[T]
            if !Self::is_program_type(&ann_assign.annotation) {
                return violations;
            }

            // Get the variable name
            let Some(name) = Self::get_target_name(&ann_assign.target) else {
                return violations;
            };

            // p_ prefix is correct - no violation
            if name.starts_with("p_") {
                return violations;
            }

            let suggested_name = Self::suggest_name(&name);

            // _program suffix is deprecated (Warning level)
            if name.ends_with("_program") {
                let message = format!(
                    "Program variable '{}' uses deprecated '_program' suffix.\n\n\
                    Naming convention: Program type variables should be named with 'p_' prefix \
                    instead of '_program' suffix for consistency and brevity.\n\n\
                    Fix: Rename the variable:\n  \
                    # Before\n  \
                    {}: Program = ...\n  \n  \
                    # After\n  \
                    {}: Program = ...",
                    name, name, suggested_name
                );

                violations.push(Violation::new(
                    self.rule_id().to_string(),
                    message,
                    ann_assign.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Warning,
                ));
            } else {
                // Other naming patterns (Info level)
                let message = format!(
                    "Program variable '{}' should use 'p_' prefix.\n\n\
                    Naming convention: Program type variables should be named with 'p_' prefix \
                    for consistency and brevity.\n\n\
                    Fix: Rename the variable:\n  \
                    # Before\n  \
                    {}: Program = ...\n  \n  \
                    # After\n  \
                    {}: Program = ...",
                    name, name, suggested_name
                );

                violations.push(Violation::new(
                    self.rule_id().to_string(),
                    message,
                    ann_assign.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Info,
                ));
            }
        }

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = ProgramNamingConventionRule::new();
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
    fn test_program_suffix_deprecated() {
        let code = r#"
data_program: Program = load_data(path=Path("data.json"))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("data_program"));
        assert!(violations[0].message.contains("deprecated"));
        assert!(violations[0].message.contains("p_data"));
        assert_eq!(violations[0].severity, Severity::Warning);
    }

    #[test]
    fn test_program_generic_suffix_deprecated() {
        let code = r#"
some_program: Program[int] = compute()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("some_program"));
        assert_eq!(violations[0].severity, Severity::Warning);
    }

    #[test]
    fn test_p_prefix_allowed() {
        let code = r#"
p_data: Program = load_data(path=Path("data.json"))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_p_prefix_generic_allowed() {
        let code = r#"
p_result: Program[int] = compute()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_other_naming_info_level() {
        let code = r#"
my_task: Program = run_task()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("my_task"));
        assert!(violations[0].message.contains("p_my_task"));
        assert_eq!(violations[0].severity, Severity::Info);
    }

    #[test]
    fn test_non_program_type_ignored() {
        let code = r#"
data_program: int = 42
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_union_type_with_program() {
        let code = r#"
maybe_program: Program | None = get_program()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("maybe_program"));
    }

    #[test]
    fn test_suggestion_for_program_suffix() {
        let code = r#"
fetch_program: Program = fetch()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        // Should suggest p_fetch (removing _program and adding p_)
        assert!(violations[0].message.contains("p_fetch"));
    }

    #[test]
    fn test_no_annotation_ignored() {
        let code = r#"
data_program = load_data()
"#;
        let violations = check_code(code);
        // Without type annotation, we can't know if it's a Program
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_complex_generic_type() {
        let code = r#"
result_program: Program[list[dict[str, int]]] = process()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("result_program"));
    }
}


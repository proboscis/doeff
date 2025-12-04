//! DOEFF015: No Zero-Argument Program Entrypoints
//!
//! Program entrypoints should be created with explicit arguments to make their
//! configuration visible and reviewable. Zero-argument factory functions hide
//! configuration, making the Program's behavior opaque.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, Stmt};

pub struct NoZeroArgProgramRule;

impl NoZeroArgProgramRule {
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

    /// Extract the function name from a call expression
    fn get_func_name(call: &rustpython_ast::ExprCall) -> String {
        match &*call.func {
            Expr::Name(name) => name.id.to_string(),
            Expr::Attribute(attr) => {
                // Handle method calls like obj.method()
                let base = Self::expr_to_string(&attr.value);
                format!("{}.{}", base, attr.attr)
            }
            _ => "<unknown>".to_string(),
        }
    }

    /// Convert an expression to a string representation
    fn expr_to_string(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Attribute(attr) => {
                let base = Self::expr_to_string(&attr.value);
                format!("{}.{}", base, attr.attr)
            }
            _ => "<expr>".to_string(),
        }
    }

    /// Extract the variable name from the assignment target
    fn get_target_name(expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            _ => "<unknown>".to_string(),
        }
    }

    /// Check if this is a zero-argument call (no positional or keyword args)
    fn is_zero_arg_call(call: &rustpython_ast::ExprCall) -> bool {
        call.args.is_empty() && call.keywords.is_empty()
    }

    /// Check if this call is an allowed pattern (e.g., Program.pure(value))
    fn is_allowed_pattern(call: &rustpython_ast::ExprCall) -> bool {
        // Allow Program.pure(), Program.fail(), etc. - these are direct value constructors
        if let Expr::Attribute(attr) = &*call.func {
            if let Expr::Name(name) = &*attr.value {
                if name.id.as_str() == "Program" {
                    return true;
                }
            }
        }
        false
    }
}

impl LintRule for NoZeroArgProgramRule {
    fn rule_id(&self) -> &str {
        "DOEFF015"
    }

    fn description(&self) -> &str {
        "Program entrypoints should not be created by zero-argument function calls"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        // Only check annotated assignments at module level
        if let Stmt::AnnAssign(ann_assign) = context.stmt {
            // Check if the type annotation is Program or Program[T]
            if !Self::is_program_type(&ann_assign.annotation) {
                return violations;
            }

            // Check if there's a value assigned
            if let Some(value) = &ann_assign.value {
                // Check if the value is a function call
                if let Expr::Call(call) = &**value {
                    // Skip allowed patterns like Program.pure()
                    if Self::is_allowed_pattern(call) {
                        return violations;
                    }

                    // Check if it's a zero-argument call
                    if Self::is_zero_arg_call(call) {
                        let var_name = Self::get_target_name(&ann_assign.target);
                        let func_name = Self::get_func_name(call);

                        let message = format!(
                            "Program entrypoint '{}' is created by calling '{}()' with no arguments.\n\n\
                            Problem: Zero-argument factory functions hide configuration, making the Program's \
                            behavior opaque and difficult to review or vary.\n\n\
                            Fix: Pass explicit arguments to make the entrypoint's configuration visible:\n\
                            - Call a @do function with keyword arguments: process(data=input_data, threshold=0.5)\n\
                            - Use Program.pure(func)(arg=value) pattern for pure functions\n\
                            - If the function truly needs no parameters, consider using Program.pure(constant_value)",
                            var_name, func_name
                        );

                        violations.push(Violation::new(
                            self.rule_id().to_string(),
                            message,
                            ann_assign.range.start().to_usize(),
                            context.file_path.to_string(),
                            Severity::Warning,
                        ));
                    }
                }
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
        let rule = NoZeroArgProgramRule::new();
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
    fn test_zero_arg_program_call() {
        let code = r#"
p_data: Program = create_pipeline()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("p_data"));
        assert!(violations[0].message.contains("create_pipeline"));
        assert!(violations[0].message.contains("no arguments"));
    }

    #[test]
    fn test_zero_arg_program_generic_call() {
        let code = r#"
p_result: Program[int] = build_program()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("p_result"));
        assert!(violations[0].message.contains("build_program"));
    }

    #[test]
    fn test_zero_arg_program_dataframe() {
        let code = r#"
p_complex: Program[DataFrame] = _internal_factory()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("_internal_factory"));
    }

    #[test]
    fn test_program_with_args_allowed() {
        let code = r#"
p_data: Program = create_pipeline(config=Config())
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_program_with_positional_args_allowed() {
        let code = r#"
p_result: Program[int] = do_task(42)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_program_with_keyword_args_allowed() {
        let code = r#"
p_with_kwarg: Program = factory(name="test")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_non_program_type_ignored() {
        let code = r#"
regular_var: int = some_func()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_program_pure_allowed() {
        let code = r#"
p_pure: Program[int] = Program.pure(42)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_program_fail_allowed() {
        let code = r#"
p_fail: Program[int] = Program.fail(ValueError("error"))
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_underscore_prefix_factory() {
        let code = r#"
p_test_optimizer_lettering: Program = _create_sample_lettering_pipeline()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("p_test_optimizer_lettering"));
        assert!(violations[0].message.contains("_create_sample_lettering_pipeline"));
    }

    #[test]
    fn test_message_contains_fix_suggestions() {
        let code = r#"
p_data: Program = create_pipeline()
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        // Check that the message contains helpful fix suggestions
        assert!(violations[0].message.contains("@do function"));
        assert!(violations[0].message.contains("Program.pure"));
    }

    #[test]
    fn test_no_annotation_ignored() {
        let code = r#"
p_data = create_pipeline()
"#;
        let violations = check_code(code);
        // Without type annotation, we can't know if it's a Program
        assert_eq!(violations.len(), 0);
    }
}


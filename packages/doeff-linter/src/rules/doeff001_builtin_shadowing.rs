//! DOEFF001: Builtin Shadowing
//!
//! Functions should not shadow Python built-in names like dict, list, type, etc.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use crate::utils::PYTHON_BUILTINS;
use rustpython_ast::Stmt;

pub struct BuiltinShadowingRule;

impl BuiltinShadowingRule {
    pub fn new() -> Self {
        Self
    }

    fn is_builtin(name: &str) -> bool {
        PYTHON_BUILTINS.contains(&name)
    }
}

impl LintRule for BuiltinShadowingRule {
    fn rule_id(&self) -> &str {
        "DOEFF001"
    }

    fn description(&self) -> &str {
        "Functions should not shadow Python built-in names"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::FunctionDef(func) => {
                if Self::is_builtin(func.name.as_str()) {
                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "Function '{}' shadows Python built-in '{}'. \
                             Use a more descriptive name like '{}_impl' or 'create_{}'.",
                            func.name, func.name, func.name, func.name
                        ),
                        func.range.start().to_usize(),
                        context.file_path.to_string(),
                        Severity::Warning,
                    ));
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                if Self::is_builtin(func.name.as_str()) {
                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "Async function '{}' shadows Python built-in '{}'. \
                             Use a more descriptive name.",
                            func.name, func.name
                        ),
                        func.range.start().to_usize(),
                        context.file_path.to_string(),
                        Severity::Warning,
                    ));
                }
            }
            _ => {}
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
        let rule = BuiltinShadowingRule::new();
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
    fn test_builtin_shadowing_dict() {
        let code = r#"
def dict():
    return {}
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("dict"));
    }

    #[test]
    fn test_builtin_shadowing_list() {
        let code = r#"
def list():
    return []
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_no_shadowing() {
        let code = r#"
def create_dict():
    return {}

def my_list():
    return []
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_function_shadowing() {
        let code = r#"
async def open():
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}




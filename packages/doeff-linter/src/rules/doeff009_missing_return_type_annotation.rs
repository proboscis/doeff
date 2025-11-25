//! DOEFF009: Missing Return Type Annotation
//!
//! Functions and methods should have return type annotations.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Stmt, StmtAsyncFunctionDef, StmtFunctionDef};

pub struct MissingReturnTypeAnnotationRule {
    skip_private: bool,
    skip_test: bool,
}

impl MissingReturnTypeAnnotationRule {
    pub fn new() -> Self {
        Self {
            skip_private: false,
            skip_test: false,
        }
    }

    pub fn with_options(skip_private: bool, skip_test: bool) -> Self {
        Self {
            skip_private,
            skip_test,
        }
    }

    fn should_skip(name: &str, is_method: bool, skip_private: bool, skip_test: bool) -> bool {
        // Skip special methods
        if is_method {
            if matches!(
                name,
                "__init__"
                    | "__new__"
                    | "__del__"
                    | "__enter__"
                    | "__exit__"
                    | "__aenter__"
                    | "__aexit__"
                    | "__setattr__"
                    | "__delattr__"
                    | "__setitem__"
                    | "__delitem__"
            ) {
                return true;
            }
        }

        // Skip private functions if configured
        if skip_private && name.starts_with('_') && !name.starts_with("__") {
            return true;
        }

        // Skip test functions if configured
        if skip_test && name.starts_with("test_") {
            return true;
        }

        false
    }

    fn is_method(func: &StmtFunctionDef) -> bool {
        if let Some(first_arg) = func.args.args.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        if let Some(first_arg) = func.args.posonlyargs.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        false
    }

    fn is_async_method(func: &StmtAsyncFunctionDef) -> bool {
        if let Some(first_arg) = func.args.args.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        if let Some(first_arg) = func.args.posonlyargs.first() {
            let arg_name = first_arg.def.arg.as_str();
            return arg_name == "self" || arg_name == "cls";
        }
        false
    }
}

impl LintRule for MissingReturnTypeAnnotationRule {
    fn rule_id(&self) -> &str {
        "DOEFF009"
    }

    fn description(&self) -> &str {
        "Functions should have return type annotations"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        match context.stmt {
            Stmt::FunctionDef(func) => {
                let is_method = Self::is_method(func);
                if Self::should_skip(
                    func.name.as_str(),
                    is_method,
                    self.skip_private,
                    self.skip_test,
                ) {
                    return violations;
                }

                if func.returns.is_none() {
                    let func_type = if is_method { "Method" } else { "Function" };
                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "{} '{}' is missing a return type annotation. \
                             Add '-> ReturnType' for better type safety.",
                            func_type, func.name
                        ),
                        func.range.start().to_usize(),
                        context.file_path.to_string(),
                        Severity::Warning,
                    ));
                }
            }
            Stmt::AsyncFunctionDef(func) => {
                let is_method = Self::is_async_method(func);
                if Self::should_skip(
                    func.name.as_str(),
                    is_method,
                    self.skip_private,
                    self.skip_test,
                ) {
                    return violations;
                }

                if func.returns.is_none() {
                    let func_type = if is_method {
                        "Async method"
                    } else {
                        "Async function"
                    };
                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "{} '{}' is missing a return type annotation.",
                            func_type, func.name
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
        let rule = MissingReturnTypeAnnotationRule::new();
        let mut violations = Vec::new();

        fn check_stmts(
            stmts: &[Stmt],
            rule: &MissingReturnTypeAnnotationRule,
            violations: &mut Vec<Violation>,
            code: &str,
            ast: &Mod,
        ) {
            for stmt in stmts {
                let context = RuleContext {
                    stmt,
                    file_path: "test.py",
                    source: code,
                    ast,
                };
                violations.extend(rule.check(&context));

                match stmt {
                    Stmt::ClassDef(class) => {
                        check_stmts(&class.body, rule, violations, code, ast);
                    }
                    Stmt::FunctionDef(func) => {
                        check_stmts(&func.body, rule, violations, code, ast);
                    }
                    _ => {}
                }
            }
        }

        if let Mod::Module(module) = &ast {
            check_stmts(&module.body, &rule, &mut violations, code, &ast);
        }

        violations
    }

    #[test]
    fn test_missing_return_type() {
        let code = r#"
def add(a: int, b: int):
    return a + b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("add"));
    }

    #[test]
    fn test_has_return_type() {
        let code = r#"
def add(a: int, b: int) -> int:
    return a + b
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_init_skipped() {
        let code = r#"
class MyClass:
    def __init__(self, value: int):
        self.value = value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_async_function() {
        let code = r#"
async def fetch():
    pass
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Async function"));
    }
}




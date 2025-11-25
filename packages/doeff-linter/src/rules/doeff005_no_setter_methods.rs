//! DOEFF005: No Setter Methods
//!
//! Classes should not have setter methods. Prefer immutable patterns.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Stmt, StmtClassDef, StmtFunctionDef};

pub struct NoSetterMethodsRule;

impl NoSetterMethodsRule {
    pub fn new() -> Self {
        Self
    }

    fn is_setter_method(func: &StmtFunctionDef) -> bool {
        let name = func.name.as_str();
        name.starts_with("set_") && name.len() > 4
    }

    fn is_property_setter(func: &StmtFunctionDef) -> bool {
        for decorator in &func.decorator_list {
            if let rustpython_ast::Expr::Attribute(attr) = decorator {
                if attr.attr.as_str() == "setter" {
                    return true;
                }
            }
        }
        false
    }
}

impl LintRule for NoSetterMethodsRule {
    fn rule_id(&self) -> &str {
        "DOEFF005"
    }

    fn description(&self) -> &str {
        "Classes should not have setter methods"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Stmt::ClassDef(class_def) = context.stmt {
            for stmt in &class_def.body {
                if let Stmt::FunctionDef(func) = stmt {
                    if Self::is_setter_method(func) || Self::is_property_setter(func) {
                        violations.push(Violation::new(
                            self.rule_id().to_string(),
                            format!(
                                "Setter method '{}' in class '{}' is not allowed. \
                                 Prefer immutable patterns: use constructor parameters, \
                                 dataclasses.replace(), or 'with_*' methods that return new instances.",
                                func.name, class_def.name
                            ),
                            func.range.start().to_usize(),
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
        let rule = NoSetterMethodsRule::new();
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
    fn test_setter_method() {
        let code = r#"
class MyClass:
    def __init__(self):
        self._value = 0

    def set_value(self, value):
        self._value = value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("set_value"));
    }

    #[test]
    fn test_property_setter() {
        let code = r#"
class MyClass:
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_with_method_allowed() {
        let code = r#"
class MyClass:
    def __init__(self, value):
        self.value = value

    def with_value(self, value):
        return MyClass(value)
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }
}




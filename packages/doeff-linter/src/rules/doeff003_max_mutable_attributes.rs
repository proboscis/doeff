//! DOEFF003: Max Mutable Attributes
//!
//! Limit the number of mutable attributes in a class.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::{Expr, ExprAttribute, ExprName, Stmt, StmtClassDef, StmtFunctionDef};
use std::collections::HashSet;

const DEFAULT_MAX_MUTABLE_ATTRIBUTES: usize = 5;

pub struct MaxMutableAttributesRule {
    max_attributes: usize,
}

impl MaxMutableAttributesRule {
    pub fn new() -> Self {
        Self {
            max_attributes: DEFAULT_MAX_MUTABLE_ATTRIBUTES,
        }
    }

    pub fn with_max(max: usize) -> Self {
        Self { max_attributes: max }
    }

    fn extract_self_attribute(expr: &Expr) -> Option<&str> {
        if let Expr::Attribute(ExprAttribute { value, attr, .. }) = expr {
            if let Expr::Name(ExprName { id, .. }) = &**value {
                if id.as_str() == "self" {
                    return Some(attr.as_str());
                }
            }
        }
        None
    }

    fn is_mutable_attribute(name: &str) -> bool {
        if name.starts_with("__") {
            false
        } else if name.starts_with('_') {
            name.starts_with("_mut")
        } else {
            name.starts_with("mut_")
        }
    }

    fn collect_mutable_attributes(class_def: &StmtClassDef) -> HashSet<String> {
        let mut mutable_attrs = HashSet::new();

        for stmt in &class_def.body {
            if let Stmt::FunctionDef(func) = stmt {
                Self::collect_from_function(func, &mut mutable_attrs);
            }
        }

        mutable_attrs
    }

    fn collect_from_function(func: &StmtFunctionDef, attrs: &mut HashSet<String>) {
        for stmt in &func.body {
            Self::collect_from_stmt(stmt, attrs);
        }
    }

    fn collect_from_stmt(stmt: &Stmt, attrs: &mut HashSet<String>) {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Some(attr_name) = Self::extract_self_attribute(target) {
                        if Self::is_mutable_attribute(attr_name) {
                            attrs.insert(attr_name.to_string());
                        }
                    }
                }
            }
            Stmt::AugAssign(aug_assign) => {
                if let Some(attr_name) = Self::extract_self_attribute(&aug_assign.target) {
                    if Self::is_mutable_attribute(attr_name) {
                        attrs.insert(attr_name.to_string());
                    }
                }
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(attr_name) = Self::extract_self_attribute(&ann_assign.target) {
                    if Self::is_mutable_attribute(attr_name) {
                        attrs.insert(attr_name.to_string());
                    }
                }
            }
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    Self::collect_from_stmt(s, attrs);
                }
                for s in &if_stmt.orelse {
                    Self::collect_from_stmt(s, attrs);
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    Self::collect_from_stmt(s, attrs);
                }
            }
            Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    Self::collect_from_stmt(s, attrs);
                }
            }
            _ => {}
        }
    }
}

impl LintRule for MaxMutableAttributesRule {
    fn rule_id(&self) -> &str {
        "DOEFF003"
    }

    fn description(&self) -> &str {
        "Limit the number of mutable attributes in a class"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Stmt::ClassDef(class_def) = context.stmt {
            let mutable_attrs = Self::collect_mutable_attributes(class_def);

            if mutable_attrs.len() > self.max_attributes {
                let attr_list: Vec<_> = mutable_attrs.iter().map(|s| s.as_str()).collect();
                violations.push(Violation::new(
                    self.rule_id().to_string(),
                    format!(
                        "Class '{}' has {} mutable attributes (max: {}): {}. \
                         Consider splitting into smaller classes or using composition.",
                        class_def.name,
                        mutable_attrs.len(),
                        self.max_attributes,
                        attr_list.join(", ")
                    ),
                    class_def.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Warning,
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

    fn check_code_with_max(code: &str, max: usize) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = MaxMutableAttributesRule::with_max(max);
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
    fn test_too_many_mutable_attributes() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.mut_a = 0
        self.mut_b = 0
        self.mut_c = 0
        self.mut_d = 0
"#;
        let violations = check_code_with_max(code, 3);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("4 mutable attributes"));
    }

    #[test]
    fn test_within_limit() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.mut_a = 0
        self.mut_b = 0
"#;
        let violations = check_code_with_max(code, 3);
        assert_eq!(violations.len(), 0);
    }
}


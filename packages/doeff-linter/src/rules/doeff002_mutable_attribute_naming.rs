//! DOEFF002: Mutable Attribute Naming
//!
//! Class attributes that are assigned outside of __init__ or __post_init__
//! must be prefixed with mut_ (public) or _mut (private).

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use crate::utils::has_dataclass_decorator;
use rustpython_ast::{
    Expr, ExprAttribute, ExprName, Stmt, StmtAnnAssign, StmtAssign, StmtAugAssign, StmtClassDef,
    StmtFunctionDef,
};
use std::collections::HashSet;

pub struct MutableAttributeNamingRule;

impl MutableAttributeNamingRule {
    pub fn new() -> Self {
        Self
    }

    fn is_init_method(func_name: &str) -> bool {
        func_name == "__init__" || func_name == "__post_init__"
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

    fn check_mutable_naming(attr_name: &str) -> bool {
        if attr_name.starts_with("__") {
            true // Dunder attributes are special
        } else if attr_name.starts_with('_') {
            attr_name.starts_with("_mut")
        } else {
            attr_name.starts_with("mut_")
        }
    }

    fn collect_class_attributes(class_def: &StmtClassDef) -> Vec<(String, usize, bool)> {
        let mut attributes = Vec::new();
        let is_dataclass = has_dataclass_decorator(class_def);

        let mut dataclass_fields_with_defaults = Vec::new();
        if is_dataclass {
            for stmt in &class_def.body {
                if let Stmt::AnnAssign(ann_assign) = stmt {
                    if ann_assign.value.is_some() {
                        if let Expr::Name(name) = &*ann_assign.target {
                            dataclass_fields_with_defaults.push(name.id.as_str().to_string());
                        }
                    }
                }
            }
        }

        for stmt in &class_def.body {
            if let Stmt::FunctionDef(func) = stmt {
                let is_init = Self::is_init_method(func.name.as_str());
                Self::collect_function_attributes(func, is_init, &mut attributes);
            }
        }

        if is_dataclass {
            let mut assigned_fields = HashSet::new();
            for (attr_name, _, is_init) in &attributes {
                if !is_init && dataclass_fields_with_defaults.contains(attr_name) {
                    assigned_fields.insert(attr_name.clone());
                }
            }

            for field_name in assigned_fields {
                for stmt in &class_def.body {
                    if let Stmt::AnnAssign(ann_assign) = stmt {
                        if let Expr::Name(name) = &*ann_assign.target {
                            if name.id.as_str() == field_name {
                                attributes.push((
                                    field_name.clone(),
                                    ann_assign.range.start().to_usize(),
                                    false,
                                ));
                                break;
                            }
                        }
                    }
                }
            }
        }

        attributes
    }

    fn collect_function_attributes(
        func: &StmtFunctionDef,
        is_init: bool,
        attributes: &mut Vec<(String, usize, bool)>,
    ) {
        for stmt in &func.body {
            Self::collect_stmt_attributes(stmt, is_init, attributes);
        }
    }

    fn collect_stmt_attributes(stmt: &Stmt, is_init: bool, attributes: &mut Vec<(String, usize, bool)>) {
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    if let Some(attr_name) = Self::extract_self_attribute(target) {
                        attributes.push((attr_name.to_string(), assign.range.start().to_usize(), is_init));
                    }
                }
            }
            Stmt::AugAssign(aug_assign) => {
                if let Some(attr_name) = Self::extract_self_attribute(&aug_assign.target) {
                    attributes.push((attr_name.to_string(), aug_assign.range.start().to_usize(), is_init));
                }
            }
            Stmt::AnnAssign(ann_assign) => {
                if let Some(attr_name) = Self::extract_self_attribute(&ann_assign.target) {
                    attributes.push((attr_name.to_string(), ann_assign.range.start().to_usize(), is_init));
                }
            }
            Stmt::If(if_stmt) => {
                for s in &if_stmt.body {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
                for s in &if_stmt.orelse {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
            }
            Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
            }
            Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
            }
            Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
            }
            Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    Self::collect_stmt_attributes(s, is_init, attributes);
                }
                for handler in &try_stmt.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        for s in &h.body {
                            Self::collect_stmt_attributes(s, is_init, attributes);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl LintRule for MutableAttributeNamingRule {
    fn rule_id(&self) -> &str {
        "DOEFF002"
    }

    fn description(&self) -> &str {
        "Mutable attributes must be prefixed with mut_ (public) or _mut (private)"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Stmt::ClassDef(class_def) = context.stmt {
            let attributes = Self::collect_class_attributes(class_def);
            let mut seen = HashSet::new();

            for (attr_name, offset, is_init) in attributes {
                if !is_init && seen.insert(attr_name.clone()) && !Self::check_mutable_naming(&attr_name) {
                    let prefix = if attr_name.starts_with('_') && !attr_name.starts_with("__") {
                        "_mut"
                    } else {
                        "mut_"
                    };

                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "Attribute '{}' in class '{}' is mutated outside __init__/__post_init__. \
                             Mutable attributes must be prefixed with '{}'.",
                            attr_name, class_def.name, prefix
                        ),
                        offset,
                        context.file_path.to_string(),
                        Severity::Error,
                    ));
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
        let rule = MutableAttributeNamingRule::new();
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
    fn test_mutable_attribute_without_prefix() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.value = 0

    def update(self):
        self.value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'value'"));
    }

    #[test]
    fn test_properly_named_mutable_attributes() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.mut_counter = 0

    def update(self):
        self.mut_counter += 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_private_mutable_attribute() {
        let code = r#"
class MyClass:
    def __init__(self):
        self._value = 0

    def update(self):
        self._value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("_mut"));
    }
}



